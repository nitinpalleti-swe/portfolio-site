"""Ask Nitin — evaluation suite runner.

Runs every case in evals.yaml against the local app, checks answers with simple rules plus an
LLM judge ("is every claim supported by the sources?"), runs a browser smoke test of the widget,
prints a pass-rate table and saves evals/results-<date>.json. Exits non-zero below the threshold,
so it can gate deploys.

    .venv/bin/python run_evals.py                          # default models from .env
    .venv/bin/python run_evals.py --model claude-haiku-5-5 --model claude-sonnet-5-5   # side by side
    .venv/bin/python run_evals.py --only kafka --only fit-ios --no-ui

Needs the app running locally with APP_ENV=development and EVAL_TOKEN set in .env. Eval requests
skip rate limits, the answer cache and the daily cap, and are logged as endpoint "eval".
"""

import argparse
import asyncio
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field

from knowledge import CHUNKS, format_sources
from llm import structured_model
from ops import cost_usd, log_request
from tools import contact_nitin

ROOT = Path(__file__).parent
JUDGE_MODEL = "claude-sonnet-5-5"
REFUSAL = "I can only help with questions about Nitin's background, skills and fit for roles."
WORK_AUTH_PREFIX = "looking-for#"


# ── Talking to the app ────────────────────────────────────────────────────────

def post(base_url: str, path: str, body: dict, headers: dict, timeout: float = 90) -> tuple[int, str]:
    req = urllib.request.Request(base_url + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError as e:  # connection refused/reset, client-side timeout
        return 0, f"connection error: {e}"


def error_kind(status: int, body: dict) -> str:
    """refusal / budget = not infrastructure; timeout / error = transient provider or network trouble."""
    if body.get("eval_budget"):
        return "budget"
    if body.get("kind"):
        return body["kind"]
    return "timeout" if status == 504 else "error" if status in (0, 500, 502, 503) else "http"


def is_transient(r: dict) -> bool:
    return bool(r["error"]) and r.get("error_kind") in ("timeout", "error")


def run_chat(base_url: str, headers: dict, case: dict) -> dict:
    t0 = time.perf_counter()
    status, body = post(base_url, "/api/chat", {"message": case["question"], "history": case.get("history", [])}, headers)
    out = {"http": status, "latency_ms": round((time.perf_counter() - t0) * 1000), "answer": "", "sources": {},
           "tools": [], "contact_event": False, "cited": [], "invalid": [], "error": None, "error_kind": None,
           "cost_usd": 0.0, "models": []}
    if status != 200:
        data = json.loads(body) if body.startswith("{") else {}
        out.update(error=f"HTTP {status}: {data.get('error', body[:200])}", error_kind=error_kind(status, data))
        return out
    for raw in body.split("\n\n"):
        if not raw.startswith("data: "):
            continue
        ev = json.loads(raw[6:])
        kind = ev["type"]
        if kind == "sources":
            out["sources"].update({s["id"]: s for s in ev["sources"]})
        elif kind == "delta":
            out["answer"] += ev["text"]
        elif kind == "reset":
            out["answer"] = ev["text"]
        elif kind == "contact":
            out["contact_event"] = True
        elif kind == "error":
            out.update(error=ev["message"], error_kind=ev.get("kind", "error"))
        elif kind == "done":
            out.update(tools=ev["tools"], cited=ev["cited"], invalid=ev["invalid"])
            out.update(cost_usd=ev.get("eval", {}).get("cost_usd", 0.0), models=ev.get("eval", {}).get("models", []))
    return out


def run_fit(base_url: str, headers: dict, case: dict) -> dict:
    t0 = time.perf_counter()
    status, body = post(base_url, "/api/fit", {"job_description": case["jd"]}, headers)
    out = {"http": status, "latency_ms": round((time.perf_counter() - t0) * 1000), "error": None, "error_kind": None,
           "report": None, "cost_usd": 0.0, "models": []}
    data = json.loads(body) if body.startswith("{") else {}
    if status != 200:
        out.update(error=f"HTTP {status}: {data.get('error', body[:200])}", error_kind=error_kind(status, data))
        return out
    out["report"] = data
    out.update(cost_usd=data.get("eval", {}).get("cost_usd", 0.0), models=data.get("eval", {}).get("models", []))
    return out


# ── Rule checks ───────────────────────────────────────────────────────────────

def cited_matches(cited: list[str], wanted: list[str]) -> bool:
    return any(c == w or (w.endswith("#") and c.startswith(w)) for c in cited for w in wanted)


def check_chat(case: dict, r: dict) -> list[str]:
    if r["error"]:
        return [f"error: {r['error']}"]
    a, low, fails = r["answer"], r["answer"].lower(), []
    for s in case.get("contains_all", []):
        if s.lower() not in low:
            fails.append(f"missing '{s}'")
    if case.get("contains_any") and not any(s.lower() in low for s in case["contains_any"]):
        fails.append(f"missing any of {case['contains_any']}")
    for s in case.get("not_contains", []):
        if s.lower() in low:
            fails.append(f"contains forbidden '{s}'")
    for rx in case.get("matches", []):
        if not re.search(rx, a, re.I):
            fails.append(f"no match for /{rx}/")
    for rx in case.get("not_matches", []):
        if m := re.search(rx, a, re.I):
            fails.append(f"forbidden match /{rx}/: '{m.group(0)}'")
    if case.get("cites_any") and not cited_matches(r["cited"], case["cites_any"]):
        fails.append(f"didn't cite any of {case['cites_any']} (cited {r['cited']})")
    if len(r["cited"]) < case.get("min_citations", 0):
        fails.append(f"only {len(r['cited'])} citations")
    if r["invalid"]:
        fails.append(f"invalid citations {r['invalid']}")
    if "refused" in case:
        refused = REFUSAL.lower() in low
        if refused != case["refused"]:
            fails.append("expected the off-topic refusal" if case["refused"] else "refused an on-topic question")
    if case.get("contact_event") and not r["contact_event"]:
        fails.append("no contact buttons event")
    for tool in case.get("tools_include", []):
        if tool not in r["tools"]:
            fails.append(f"tool {tool} not used (used {r['tools']})")
    return fails


def check_fit(case: dict, r: dict) -> list[str]:
    if r["error"]:
        return [f"error: {r['error']}"]
    rep, fails = r["report"], []
    rows = rep["requirements"]
    if rep["overall_fit"] not in case.get("overall_in", [rep["overall_fit"]]):
        fails.append(f"overall_fit {rep['overall_fit']} not in {case['overall_in']}")
    for want in case.get("rows", []):
        hits = [x for x in rows if re.search(want["requirement"], x["requirement"], re.I)]
        if not hits:
            fails.append(f"no row matching /{want['requirement']}/")
        elif not any(x["status"] in want["status_in"] for x in hits):
            fails.append(f"row '{hits[0]['requirement']}' is {hits[0]['status']}, expected {want['status_in']}")
    if case.get("work_auth_gap") and not any(x["status"] == "gap" and x["source_id"].startswith(WORK_AUTH_PREFIX) for x in rows):
        fails.append("no work-authorization gap row")
    if "min_match_share" in case and rows:
        share = sum(x["status"] == "match" for x in rows) / len(rows)
        if share < case["min_match_share"]:
            fails.append(f"match share {share:.0%} < {case['min_match_share']:.0%}")
    return fails


# ── LLM judge ─────────────────────────────────────────────────────────────────

class Verdict(BaseModel):
    problems: list[str] = Field(description="Each unsupported claim, unsupported qualifier/count, wrong citation or "
                                            "case-specific violation, quoted briefly. Empty if none.")
    all_claims_supported: bool


JUDGE_SYSTEM = f"""You grade answers produced by "Nitin's assistant", which must answer only from Nitin Palleti's knowledge base. Be strict and literal, but don't nitpick wording.

A claim is SUPPORTED only if a source states it (paraphrase is fine). Count as a problem:
- facts not stated in the sources (titles, dates, employers, clients, technologies, metrics, team leadership, degrees, salary)
- qualifiers or totals the sources don't state: "only", "never", "just", "all", "both", "every", "first", "N roles", "N years of X" (a number the sources state, like "8+ years", is fine)
- a count that doesn't equal the items the sources support
- a citation id that doesn't support the sentence it is attached to
- breaking the case-specific requirement, if one is given
Not claims: saying something isn't covered by the sources, polite refusals, suggestions to contact Nitin, and the citation markers themselves.

Knowledge base (ground truth):
{format_sources(CHUNKS)}
{contact_nitin().content}
"""


async def judge(kind: str, case: dict, r: dict) -> tuple[Verdict | None, float, str | None]:
    if kind == "chat":
        shown = "\n\n".join(f'<source id="{s["id"]}" title="{s.get("title", "")}">\n{s["text"]}\n</source>'
                              for s in r["sources"].values())
        user = (f"<question>\n{case['question']}\n</question>\n\n<sources_shown_to_assistant>\n{shown or '(none)'}\n"
                f"</sources_shown_to_assistant>\n\n<answer>\n{r['answer']}\n</answer>")
    else:
        rep = r["report"]
        rows = "\n".join(f"- [{x['status']}] {x['requirement']} — {x['evidence']} [{x['source_id'] or 'no source'}]"
                         for x in rep["requirements"])
        user = (f"This is a job-fit report about Nitin for the job description below. Check every claim about Nitin in "
                f"the summary and evidence lines against the knowledge base, and each row's evidence against its own cited "
                f"source. Statements about the job itself (what it requires, offers or where it is) may come from the job "
                f"description, and the summary may summarise the report's own row statuses (e.g. 'most core requirements are "
                f"gaps'); neither is a problem.\n\n<job_description>\n{case['jd']}\n</job_description>\n\n"
                f"<summary>\n{rep['summary']}\n</summary>\n\n<requirement_rows>\n{rows}\n</requirement_rows>")
    if focus := case.get("judge_focus"):
        user += f"\n\n<case_requirement>\n{focus}\n</case_requirement>"
    last_error = None
    for _ in range(2):  # one retry on transient errors
        try:
            t0 = time.perf_counter()
            verdict, usage = await structured_model(JUDGE_MODEL, JUDGE_SYSTEM, user, Verdict)
            # Judge calls spend the same API budget: log them as eval traffic so the daily cap sees them.
            log_request({"endpoint": "eval", "eval_of": "judge", "status": "ok", "ip": "local-eval-runner",
                         "latency_ms": round((time.perf_counter() - t0) * 1000), "case": case["id"]}, [usage])
            return verdict, cost_usd([usage]), None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return None, 0.0, last_error


# ── Browser smoke test (Step 5 regression: typing + Enter in the Ask tab) ─────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def ui_smoke(base_url: str, token: str) -> dict:
    """Drives the installed Chrome over the DevTools protocol (no extra dependency): open the widget,
    type a question into the Ask box, press Enter, and wait for the answer."""
    from websockets.asyncio.client import connect

    chrome = shutil.which("google-chrome") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).exists():
        return {"status": "skipped", "detail": "Google Chrome not found"}
    question, port = "Where did Nitin study?", _free_port()
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as profile:
        proc = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                 "--no-first-run", "--no-default-browser-check", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            targets = None
            for _ in range(50):
                try:
                    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                    break
                except OSError:
                    await asyncio.sleep(0.2)
            page = next(t for t in targets or [] if t["type"] == "page")
            async with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
                next_id = 0

                async def cdp(method: str, **params):
                    nonlocal next_id
                    next_id += 1
                    await ws.send(json.dumps({"id": next_id, "method": method, "params": params}))
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg.get("id") == next_id:
                            return msg.get("result", {})

                async def js(expr: str):
                    res = await cdp("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
                    return res.get("result", {}).get("value")

                await cdp("Network.enable")
                await cdp("Network.setExtraHTTPHeaders", headers={"X-Eval-Token": token})
                await cdp("Page.navigate", url=base_url + "/")
                for _ in range(50):
                    if await js("document.readyState === 'complete' && !!document.querySelector('.chat-fab')"):
                        break
                    await asyncio.sleep(0.2)
                await js("document.querySelector('.chat-fab').click(); document.querySelector('#chatInput').focus(); true")
                await cdp("Input.insertText", text=question)
                for kind in ("keyDown", "keyUp"):  # a real Enter key press, as a visitor would type it
                    await cdp("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter",
                              windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))
                state = {}
                for _ in range(150):  # up to 30 s for the streamed answer
                    state = await js("""(() => {
                        const user = [...document.querySelectorAll('.chat-msg.user')].map(m => m.innerText);
                        const bot = [...document.querySelectorAll('.chat-msg.bot, .chat-msg.error')].pop();
                        return {user, botText: bot ? bot.innerText : '', botClass: bot ? bot.className : '',
                                typing: !!(bot && bot.querySelector('.chat-typing')),
                                chatInput: document.querySelector('#chatInput').value,
                                fitInput: document.querySelector('#fitInput').value};
                    })()""") or {}
                    if state.get("botText") and not state.get("typing"):
                        break
                    await asyncio.sleep(0.2)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    fails = []
    if question not in state.get("user", []):
        fails.append(f"question not sent (user bubbles: {state.get('user')}, Ask box: {state.get('chatInput')!r})")
    if not state.get("botText") or "error" in state.get("botClass", ""):
        fails.append(f"no answer bubble ({state.get('botText', '')[:80]!r})")
    if state.get("fitInput"):
        fails.append("text ended up in the fit textarea")
    return {"status": "pass" if not fails else "fail", "fails": fails, "latency_ms": round((time.perf_counter() - t0) * 1000),
            "answer": state.get("botText", "")[:300]}


# ── Running a suite ───────────────────────────────────────────────────────────

async def run_suite(args, suite: dict, token: str, model: str | None) -> dict:
    headers = {"X-Eval-Token": token}
    if model:
        headers["X-Eval-Model"] = model
    if args.fit_model:
        headers["X-Eval-Fit-Model"] = args.fit_model
    results = []
    cases = [("chat", c) for c in suite.get("chat", [])] + [("fit", c) for c in suite.get("fit", [])]
    if args.only:
        cases = [(k, c) for k, c in cases if c["id"] in args.only]
    for kind, case in cases:
        call = run_chat if kind == "chat" else run_fit
        r, retried = call(args.base_url, headers, case), False
        if is_transient(r):  # provider/network hiccup: one retry before calling it an ERROR
            await asyncio.sleep(2)
            r, retried = call(args.base_url, headers, case), True
        fails, errors, verdict, judge_cost = [], [], None, 0.0
        if r["error"] and (is_transient(r) or r["error_kind"] == "budget"):
            errors.append(r["error"])  # infrastructure, not answer quality
        else:
            fails = check_chat(case, r) if kind == "chat" else check_fit(case, r)
            if not fails and case.get("judge", True) and not args.no_judge:
                verdict, judge_cost, judge_error = await judge(kind, case, r)
                if judge_error:
                    errors.append(f"judge error: {judge_error}")
                elif not verdict.all_claims_supported:
                    fails.append("judge: " + "; ".join(verdict.problems or ["claims not supported"]))
        status = "FAIL" if fails else "ERROR" if errors else "PASS"
        row = {"id": case["id"], "kind": kind, "status": status, "passed": status == "PASS", "fails": fails,
               "errors": errors, "retried": retried, "latency_ms": r["latency_ms"], "app_cost_usd": r["cost_usd"],
               "judge_cost_usd": round(judge_cost, 6), "models": r["models"], "judged": verdict is not None}
        if kind == "chat":
            row.update(question=case["question"], answer=r["answer"], cited=r["cited"], tools=r["tools"])
        elif r["report"]:
            rep = r["report"]
            row.update(overall_fit=rep["overall_fit"], summary=rep["summary"], checks=rep.get("checks", []),
                       rows=[{k: x[k] for k in ("requirement", "status", "source_id")} for x in rep["requirements"]])
        results.append(row)
        note = (fails or errors or [""])[0][:110]
        print(f"  {status:<5} {case['id']:<22} {kind:<4} {r['latency_ms']:>6} ms  ${r['cost_usd']:.4f}"
              + (" (retried)" if retried else "") + (f"  ← {note}" if note else ""), flush=True)
    if not args.no_ui and not args.only:
        try:
            ui = await ui_smoke(args.base_url, token)
        except Exception as exc:  # Chrome failed to start, DevTools connection dropped, ...
            ui = {"status": "error", "fails": [], "errors": [f"{type(exc).__name__}: {exc}"]}
        status = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skipped": "SKIP"}[ui["status"]]
        results.append({"id": "ui-ask-enter", "kind": "ui", "status": status, "passed": status == "PASS",
                        "fails": ui.get("fails", []), "errors": ui.get("errors", []), "latency_ms": ui.get("latency_ms", 0),
                        "app_cost_usd": 0.0, "judge_cost_usd": 0.0, "detail": ui.get("detail") or ui.get("answer", "")})
        note = (ui.get("fails") or ui.get("errors") or [""])[0]
        print(f"  {status:<5} ui-ask-enter           ui   {ui.get('latency_ms', 0):>6} ms" + (f"  ← {note}" if note else ""), flush=True)
    count = lambda s: sum(r["status"] == s for r in results)
    passed, failed, errored = count("PASS"), count("FAIL"), count("ERROR")
    graded = passed + failed
    lat = sorted(r["latency_ms"] for r in results if r["kind"] in ("chat", "fit") and r["status"] != "ERROR")
    by_kind = lambda k: [r["latency_ms"] for r in results if r["kind"] == k and r["status"] != "ERROR"]
    return {
        "model": model or "default (.env)", "fit_model": args.fit_model or "default (.env)", "suite": args.suite,
        "passed": passed, "failed": failed, "errors": errored, "graded": graded,
        "pass_rate": round(passed / graded, 4) if graded else 0.0,  # ERRORs (infrastructure) are not graded
        # More than half the cases erroring means the run can't tell us anything about quality.
        "inconclusive": graded == 0 or errored > (graded + errored) / 2,
        "avg_latency_ms_chat": round(sum(by_kind("chat")) / len(by_kind("chat"))) if by_kind("chat") else None,
        "avg_latency_ms_fit": round(sum(by_kind("fit")) / len(by_kind("fit"))) if by_kind("fit") else None,
        "p95_latency_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
        "app_cost_usd": round(sum(r["app_cost_usd"] for r in results), 4),
        "judge_cost_usd": round(sum(r["judge_cost_usd"] for r in results), 4),
        "cases": results,
    }


def print_summary(runs: list[dict], threshold: float) -> None:
    print("\n" + "─" * 100)
    print(f"{'model':<26}{'pass':>6}{'fail':>6}{'error':>7}{'rate':>7}{'chat avg':>11}{'fit avg':>10}{'p95':>9}{'app $':>9}{'judge $':>9}")
    for r in runs:
        print(f"{r['model']:<26}{r['passed']:>6}{r['failed']:>6}{r['errors']:>7}{r['pass_rate']:>7.0%}"
              f"{(r['avg_latency_ms_chat'] or 0):>9} ms{(r['avg_latency_ms_fit'] or 0):>7} ms{(r['p95_latency_ms'] or 0):>6} ms"
              f"{r['app_cost_usd']:>9.4f}{r['judge_cost_usd']:>9.4f}")
    for r in runs:
        for label, key in (("Failed", "FAIL"), ("Errored (infrastructure, not graded)", "ERROR")):
            bad = [c for c in r["cases"] if c["status"] == key]
            if bad:
                print(f"\n{label} with {r['model']}:")
                for c in bad:
                    print(f"  - {c['id']}: " + " | ".join(c["fails"] or c["errors"])[:300])
    if any(r["inconclusive"] for r in runs):
        print("\nGate: INCONCLUSIVE (too many infrastructure errors to judge quality)")
    else:
        gate = all(r["pass_rate"] >= threshold for r in runs)
        print(f"\nGate ({threshold:.0%} of graded cases): {'PASS' if gate else 'FAIL'}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8001")
    ap.add_argument("--suite", default="evals.yaml", help="case file (e.g. evals-holdout.yaml)")
    ap.add_argument("--model", action="append", help="chat model to evaluate (repeat to compare models side by side)")
    ap.add_argument("--fit-model", help="override the job-fit model")
    ap.add_argument("--only", action="append", help="run only these case ids")
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM judge (rules only)")
    ap.add_argument("--no-ui", action="store_true", help="skip the browser smoke test")
    ap.add_argument("--threshold", type=float, default=0.9)
    args = ap.parse_args()

    token = dotenv_values(ROOT / ".env").get("EVAL_TOKEN", "")
    if not token:
        print("EVAL_TOKEN is missing from .env")
        return 2
    try:
        health = json.load(urllib.request.urlopen(args.base_url + "/api/health", timeout=5))
    except OSError as exc:
        print(f"App not reachable at {args.base_url}: {exc}")
        return 2
    if not health.get("model_configured"):
        print("App has no model configured (ANTHROPIC_API_KEY)")
        return 2
    suite = yaml.safe_load((ROOT / args.suite).read_text())

    runs = []
    for model in args.model or [None]:
        print(f"\n▶ {args.suite} · {model or 'default models from .env'}")
        runs.append(await run_suite(args, suite, token, model))
    print_summary(runs, args.threshold)

    out_dir = ROOT / "evals"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = Path(args.suite).stem
    suffix = ("" if stem == "evals" else "-" + stem.removeprefix("evals-")) + ("" if not args.model else "-" + "-vs-".join(args.model))
    if args.only:
        suffix += "-partial"  # a subset run must not overwrite the full-suite results
    path = out_dir / f"results-{stamp}{suffix}.json"
    path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "threshold": args.threshold, "judge_model": JUDGE_MODEL, "runs": runs},
                               indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {path.relative_to(ROOT)}")
    # Exit codes: 0 = pass, 1 = real failures below the threshold, 2 = setup problem or inconclusive.
    if any(r["inconclusive"] for r in runs):
        return 2
    return 0 if all(r["pass_rate"] >= args.threshold for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
