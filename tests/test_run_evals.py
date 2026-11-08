"""run_evals.py: transient infrastructure errors are retried once and reported as ERROR, not FAIL."""

import asyncio
from types import SimpleNamespace

import pytest

import run_evals


def args(**kw):
    base = dict(base_url="http://x", fit_model=None, only=None, no_judge=True, no_ui=True, suite="evals.yaml")
    return SimpleNamespace(**{**base, **kw})


def chat_result(error=None, kind=None, answer="Nitin has used Kafka [skills#3]."):
    return {"http": 200, "latency_ms": 10, "answer": answer, "sources": {}, "tools": [], "contact_event": False,
            "cited": ["skills#3"], "invalid": [], "error": error, "error_kind": kind, "cost_usd": 0.0, "models": []}


SUITE = {"chat": [{"id": "kafka", "question": "Has Nitin used Kafka?", "contains_all": ["Kafka"], "judge": False}]}


def run(monkeypatch, responses, suite=SUITE, **kw):
    calls = iter(responses)
    monkeypatch.setattr(run_evals, "run_chat", lambda *a: next(calls))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(run_evals.asyncio, "sleep", lambda s: real_sleep(0))  # skip the 2 s retry pause
    return asyncio.run(run_evals.run_suite(args(**kw), suite, "token", None))


def test_transient_then_ok_passes_with_retry(monkeypatch):
    res = run(monkeypatch, [chat_result("Something went wrong.", "error"), chat_result()])
    case = res["cases"][0]
    assert case["status"] == "PASS" and case["retried"]


def test_transient_twice_is_error_not_fail(monkeypatch):
    res = run(monkeypatch, [chat_result("That took too long.", "timeout"), chat_result("That took too long.", "timeout")])
    assert res["cases"][0]["status"] == "ERROR"
    assert (res["passed"], res["failed"], res["errors"], res["graded"]) == (0, 0, 1, 0)
    assert res["inconclusive"]  # nothing graded: can't gate on it either way


def test_model_refusal_is_a_quality_fail(monkeypatch):
    res = run(monkeypatch, [chat_result("I can't help with that one.", "refusal")])
    assert res["cases"][0]["status"] == "FAIL" and not res["cases"][0]["retried"]


def test_eval_budget_is_error(monkeypatch):
    res = run(monkeypatch, [chat_result("HTTP 503: Today's eval budget is used up.", "budget")])
    assert res["cases"][0]["status"] == "ERROR"


def test_wrong_answer_is_fail(monkeypatch):
    res = run(monkeypatch, [chat_result(answer="He has used RabbitMQ.")])
    assert res["cases"][0]["status"] == "FAIL"


def test_judge_error_is_error(monkeypatch):
    async def broken_judge(*a):
        return None, 0.0, "APITimeoutError: timed out"
    monkeypatch.setattr(run_evals, "judge", broken_judge)
    suite = {"chat": [{**SUITE["chat"][0], "judge": True}]}
    res = run(monkeypatch, [chat_result()], suite=suite, no_judge=False)
    assert res["cases"][0]["status"] == "ERROR"


def test_pass_rate_ignores_errors(monkeypatch):
    suite = {"chat": [{**SUITE["chat"][0], "id": f"c{i}"} for i in range(4)]}
    ok, bad = chat_result(), chat_result("Connection error.", "error")
    res = run(monkeypatch, [ok, ok, ok, bad, bad], suite=suite)  # 3 pass, 1 errors after its retry
    assert (res["passed"], res["errors"], res["pass_rate"], res["inconclusive"]) == (3, 1, 1.0, False)


@pytest.mark.parametrize("status, body, expected", [
    (504, {}, "timeout"), (502, {"kind": "error"}, "error"), (0, {}, "error"),
    (503, {"eval_budget": True}, "budget"), (422, {"kind": "refusal"}, "refusal"), (429, {}, "http"),
])
def test_error_kind(status, body, expected):
    assert run_evals.error_kind(status, body) == expected
