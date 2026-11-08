"""Ask Nitin — keeping it safe and cheap to leave public.

Per-visitor rate limits, a daily spend cap computed from logged token usage, a one-hour
answer cache, one JSON log line per request, and the numbers behind /api/stats.
Visitors are identified only by a salted HMAC of their IP; raw IPs are never stored.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections import Counter, OrderedDict, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# LOG_DIR lets the host put the log on a persistent volume (the spend cap is rebuilt from it on restart).
LOG_PATH = Path(os.getenv("LOG_DIR") or ROOT / "logs") / "requests.jsonl"

# USD per million tokens. Cache writes are 1.25x input, cache reads 0.1x input.
# Unknown models are priced like the most expensive entry, so the spend cap errs on the safe side.
PRICES = {
    "claude-haiku-5-5":  {"input": 0.10, "output": 0.50,  "cache_write": 0.125, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50,  "cache_read": 0.20},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00, "cache_write": 2.50,  "cache_read": 0.20},  # fallback model
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_write": 5.00,  "cache_read": 0.20},  # for eval comparisons
}
_MOST_EXPENSIVE = max(PRICES.values(), key=lambda p: p["output"])

DAILY_SPEND_CAP_USD = float(os.getenv("DAILY_SPEND_CAP_USD", "0.50"))      # public widget traffic
EVAL_DAILY_CAP_USD = float(os.getenv("EVAL_DAILY_CAP_USD", "1.00"))        # local eval runs (separate budget)
RATE_LIMITS_PER_HOUR = {
    "chat": int(os.getenv("RATE_LIMIT_CHAT_PER_HOUR", "15")),
    "fit": int(os.getenv("RATE_LIMIT_FIT_PER_HOUR", "5")),
}
CACHE_TTL_SECONDS = 3600
# Number of trusted reverse proxies in front of the app. 0 = use the socket address and ignore
# X-Forwarded-For entirely (anyone can send that header). On a host with one proxy, set 1.
# Run uvicorn with --no-proxy-headers so it doesn't rewrite the client address before we see it.
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
# Alternatively, a header that the host's proxy sets to the client address (Fly.io: Fly-Client-IP).
# Only set this when the app is reachable exclusively through that proxy, so clients can't set it themselves.
CLIENT_IP_HEADER = os.getenv("CLIENT_IP_HEADER", "").strip().lower()

_salt = os.getenv("IP_HASH_SALT", "")
if not _salt:
    _salt = secrets.token_hex(32)
    print("[ops] IP_HASH_SALT is not set: using a random salt, so visitor ids change on every restart")
_SALT = _salt.encode()


# ── Visitor identity ──────────────────────────────────────────────────────────

def keyed_hash(value: str, length: int = 16) -> str:
    """HMAC-SHA256 with a secret salt: can't be reversed by brute-forcing the IPv4 space without the salt."""
    return hmac.new(_SALT, value.encode(), hashlib.sha256).hexdigest()[:length]


def client_ip(headers: dict, socket_host: str | None, hops: int = TRUSTED_PROXY_HOPS,
              ip_header: str = CLIENT_IP_HEADER) -> str:
    """The visitor's IP. Proxy headers are only trusted when configured for the host we run on."""
    if ip_header and (value := headers.get(ip_header, "").strip()):
        return value
    if hops > 0:
        forwarded = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(forwarded) >= hops:
            return forwarded[-hops]  # each trusted proxy appends the address it received from
    return socket_host or "unknown"


# ── Rate limiting (in memory: per process, reset on restart) ──────────────────

class RateLimiter:
    def __init__(self, limits: dict[str, int], window_seconds: int = 3600):
        self.limits, self.window = limits, window_seconds
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, endpoint: str, visitor: str, now: float | None = None) -> int:
        """Records the request. Returns 0 if allowed, else the seconds until the next request is allowed."""
        now = time.time() if now is None else now
        q = self.hits[(endpoint, visitor)]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limits[endpoint]:
            return max(1, int(self.window - (now - q[0])) + 1)
        q.append(now)
        return 0


rate_limiter = RateLimiter(RATE_LIMITS_PER_HOUR)


# ── Answer cache (identical first-turn questions / identical job descriptions) ─

class AnswerCache:
    def __init__(self, ttl: int = CACHE_TTL_SECONDS, max_items: int = 500):
        self.ttl, self.max_items = ttl, max_items
        self.items: OrderedDict[str, tuple[float, object]] = OrderedDict()

    @staticmethod
    def key(kind: str, text: str) -> str:
        return f"{kind}:" + hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()

    def get(self, key: str, now: float | None = None):
        now = time.time() if now is None else now
        hit = self.items.get(key)
        if not hit or now - hit[0] > self.ttl:
            self.items.pop(key, None)
            return None
        return hit[1]

    def put(self, key: str, value, now: float | None = None) -> None:
        self.items[key] = (time.time() if now is None else now, value)
        self.items.move_to_end(key)
        while len(self.items) > self.max_items:
            self.items.popitem(last=False)


answer_cache = AnswerCache()


# ── Cost and the daily spend cap ──────────────────────────────────────────────

def cost_usd(usages: list[dict]) -> float:
    total = 0.0
    for u in usages:
        p = PRICES.get(u.get("model", ""), _MOST_EXPENSIVE)
        total += sum(u.get(k, 0) * p[k] for k in ("input", "output", "cache_write", "cache_read")) / 1_000_000
    return total


def _utc_day(ts: str) -> date:
    return datetime.fromisoformat(ts).astimezone(timezone.utc).date()


def read_log(path: Path | None = None) -> list[dict]:
    path = path or LOG_PATH
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a half-written line must not break the cap or the stats
    return records


# Today's spend (UTC day) per budget, computed from the log so the caps survive restarts. It is re-read
# whenever the file changed size since this process last saw it, so spend logged by another process
# (a second worker, or the eval runner's judge calls) also counts. Eval traffic has its own budget.
_spend = {"day": None, "size": -1, "public": 0.0, "eval": 0.0}


def _budget(record: dict) -> str:
    return "eval" if record.get("endpoint") == "eval" else "public"


def _log_size(path: Path | None = None) -> int:
    path = path or LOG_PATH
    return path.stat().st_size if path.exists() else 0


def spent_today(budget: str = "public") -> float:
    today = datetime.now(timezone.utc).date()
    if _spend["day"] != today or _log_size() != _spend["size"]:
        totals = {"public": 0.0, "eval": 0.0}
        for r in read_log():
            if _utc_day(r["ts"]) == today:
                totals[_budget(r)] += r.get("cost_usd", 0)
        _spend.update(day=today, size=_log_size(), **totals)
    return _spend[budget]


def over_daily_cap() -> bool:
    return spent_today("public") >= DAILY_SPEND_CAP_USD


def over_eval_cap() -> bool:
    return spent_today("eval") >= EVAL_DAILY_CAP_USD


# ── Request log ───────────────────────────────────────────────────────────────

def log_request(record: dict, usages: list[dict] | None = None, path: Path | None = None) -> dict:
    """Append one JSON line. Adds token totals and estimated cost from the model usages."""
    usages = usages or []
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **record,
        "models": sorted({u["model"] for u in usages}),
        "input_tokens": sum(u["input"] for u in usages),
        "output_tokens": sum(u["output"] for u in usages),
        "cache_write_tokens": sum(u["cache_write"] for u in usages),
        "cache_read_tokens": sum(u["cache_read"] for u in usages),
        "cost_usd": round(cost_usd(usages), 6),
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    target = path or LOG_PATH
    target.parent.mkdir(exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
    if path is None:  # the real log: keep the running spend total in step
        if _spend["size"] == _log_size() - len(line.encode("utf-8")):
            _spend[_budget(record)] += record["cost_usd"]  # nobody else wrote since our last read: just add our line
            _spend["size"] = _log_size()
        # otherwise the next spent_today() call re-reads the file
    return record


# ── Stats ─────────────────────────────────────────────────────────────────────

def _summary(records: list[dict]) -> dict:
    latencies = sorted(r["latency_ms"] for r in records if r.get("status") == "ok" and not r.get("cache_hit"))
    status = Counter(r.get("status", "?") for r in records)
    return {
        "requests": len(records),
        "by_endpoint": dict(Counter(r["endpoint"] for r in records)),
        "by_status": dict(status),
        "cache_hits": sum(1 for r in records if r.get("cache_hit")),
        "refused": sum(1 for r in records if r.get("refused")),
        "unique_visitors": len({r.get("ip") for r in records}),
        "input_tokens": sum(r.get("input_tokens", 0) for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) for r in records),
        "cache_read_tokens": sum(r.get("cache_read_tokens", 0) for r in records),
        "cost_usd": round(sum(r.get("cost_usd", 0) for r in records), 4),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
        "tools": dict(Counter(t for r in records for t in r.get("tools", []))),
    }


def stats(days: int = 7, path: Path | None = None, now: datetime | None = None) -> dict:
    today = (now or datetime.now(timezone.utc)).date()
    first = today - timedelta(days=days - 1)
    all_records = [r for r in read_log(path) if first <= _utc_day(r["ts"]) <= today]
    records = [r for r in all_records if r["endpoint"] != "eval"]  # public traffic only
    evals = [r for r in all_records if r["endpoint"] == "eval"]
    by_day = defaultdict(list)
    for r in records:
        by_day[_utc_day(r["ts"]).isoformat()].append(r)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": "UTC",
        "daily_cap_usd": DAILY_SPEND_CAP_USD,
        "spent_today_usd": round(spent_today("public"), 4) if path is None else None,
        "today": _summary(by_day.get(today.isoformat(), [])),
        f"last_{days}_days": _summary(records),
        "by_day": {d: {"requests": len(rs), "cost_usd": round(sum(r.get("cost_usd", 0) for r in rs), 4)}
                   for d, rs in sorted(by_day.items())},
        # Eval runs are excluded from public numbers and have their own daily budget.
        "eval": {"requests": len(evals), "cost_usd": round(sum(r.get("cost_usd", 0) for r in evals), 4),
                 "daily_cap_usd": EVAL_DAILY_CAP_USD,
                 "spent_today_usd": round(spent_today("eval"), 4) if path is None else None},
    }
