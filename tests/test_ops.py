"""Unit tests for ops.py: cost, visitor ids, rate limits, cache, log and stats (no network, no API key)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import ops

HAIKU = {"model": "claude-haiku-5-5", "input": 1_000_000, "output": 1_000_000, "cache_write": 0, "cache_read": 1_000_000}


def test_cost_uses_per_model_prices():
    assert ops.cost_usd([HAIKU]) == pytest.approx(0.10 + 0.50 + 0.01)
    sonnet = {**HAIKU, "model": "claude-sonnet-5-5"}
    assert ops.cost_usd([sonnet]) == pytest.approx(2.00 + 10.00 + 0.20)


def test_unknown_model_priced_as_most_expensive():
    top = max(ops.PRICES.values(), key=lambda p: p["output"])
    assert ops.cost_usd([{**HAIKU, "model": "some-new-model"}]) == pytest.approx(top["input"] + top["output"] + top["cache_read"])
    assert top["output"] >= 10.00


def test_visitor_hash_is_keyed_and_stable():
    a, b = ops.keyed_hash("203.0.113.7"), ops.keyed_hash("203.0.113.7")
    assert a == b and len(a) == 16 and "203" not in a
    import hashlib
    assert a != hashlib.sha256(b"203.0.113.7").hexdigest()[:16]  # not a plain, brute-forceable sha256


@pytest.mark.parametrize("hops, xff, expected", [
    (0, "1.2.3.4", "10.0.0.9"),                # not behind a known proxy: header ignored (spoofable)
    (1, "6.6.6.6, 198.51.100.2", "198.51.100.2"),  # one trusted proxy appended the real client address
    (1, "", "10.0.0.9"),                       # header missing: fall back to the socket address
    (2, "198.51.100.2, 10.1.1.1", "198.51.100.2"),
])
def test_client_ip_only_trusts_forwarded_for_with_known_proxies(hops, xff, expected):
    headers = {"x-forwarded-for": xff} if xff else {}
    assert ops.client_ip(headers, "10.0.0.9", hops=hops) == expected


def test_rate_limiter_blocks_the_16th_request_and_recovers():
    rl = ops.RateLimiter({"chat": 15}, window_seconds=3600)
    assert all(rl.check("chat", "v1", now=1000 + i) == 0 for i in range(15))
    retry = rl.check("chat", "v1", now=1015)
    assert retry > 0                                   # 16th within the hour is limited
    assert rl.check("chat", "v2", now=1015) == 0       # other visitors unaffected
    assert rl.check("chat", "v1", now=1000 + 3601) == 0  # window slides


def test_answer_cache_expires_and_normalises_whitespace():
    cache = ops.AnswerCache(ttl=3600)
    k1, k2 = ops.AnswerCache.key("chat", "Has Nitin used  Kafka?"), ops.AnswerCache.key("chat", "has nitin used kafka?")
    assert k1 == k2
    cache.put(k1, "answer", now=0)
    assert cache.get(k1, now=3599) == "answer"
    assert cache.get(k1, now=3601) is None


def test_log_and_stats(tmp_path):
    log = tmp_path / "requests.jsonl"
    ops.log_request({"endpoint": "chat", "status": "ok", "ip": "a", "latency_ms": 900, "tools": ["get_github_repos"]}, [HAIKU], path=log)
    ops.log_request({"endpoint": "chat", "status": "ok", "ip": "a", "latency_ms": 5, "cache_hit": True}, [], path=log)
    ops.log_request({"endpoint": "fit", "status": "rate_limited", "ip": "b", "latency_ms": 1}, [], path=log)
    ops.log_request({"endpoint": "chat", "status": "ok", "ip": "c", "latency_ms": 700, "refused": True}, [HAIKU], path=log)
    # an 8-day-old line must not count in the 7-day window
    old = {"ts": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), "endpoint": "chat", "status": "ok",
           "ip": "z", "latency_ms": 1, "cost_usd": 99}
    with log.open("a") as f:
        f.write(json.dumps(old) + "\n" + "{not json\n")
    s = ops.stats(path=log)
    today = s["today"]
    assert today["requests"] == 4 and s["last_7_days"]["requests"] == 4
    assert today["by_endpoint"] == {"chat": 3, "fit": 1}
    assert today["by_status"] == {"ok": 3, "rate_limited": 1}
    assert today["cache_hits"] == 1 and today["refused"] == 1 and today["unique_visitors"] == 3
    assert today["cost_usd"] == pytest.approx(2 * 0.61, abs=1e-4)
    assert today["avg_latency_ms"] == 800  # cache hits and non-ok requests excluded
    assert today["tools"] == {"get_github_repos": 1}


def test_log_never_needs_raw_ip_or_jd_text(tmp_path):
    log = tmp_path / "requests.jsonl"
    rec = ops.log_request({"endpoint": "fit", "status": "ok", "ip": ops.keyed_hash("203.0.113.7"), "latency_ms": 1,
                           "jd_len": 1234, "jd_hash": ops.keyed_hash("full job description text")}, [], path=log)
    line = log.read_text()
    assert "203.0.113.7" not in line and "job description text" not in line
    assert rec["jd_len"] == 1234


def test_spend_sees_lines_written_by_another_process(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log)
    monkeypatch.setattr(ops, "_spend", {"day": None, "size": -1, "public": 0.0, "eval": 0.0})
    assert ops.spent_today() == 0.0
    now = datetime.now(timezone.utc).isoformat()
    # another process (e.g. a second worker) appends a public line directly
    log.write_text(json.dumps({"ts": now, "endpoint": "chat", "status": "ok", "ip": "x", "latency_ms": 1, "cost_usd": 0.25}) + "\n")
    assert ops.spent_today() == pytest.approx(0.25)


def test_eval_spend_has_its_own_budget(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log)
    monkeypatch.setattr(ops, "_spend", {"day": None, "size": -1, "public": 0.0, "eval": 0.0})
    monkeypatch.setattr(ops, "DAILY_SPEND_CAP_USD", 0.50)
    monkeypatch.setattr(ops, "EVAL_DAILY_CAP_USD", 1.00)
    ops.log_request({"endpoint": "eval", "eval_of": "judge", "status": "ok", "ip": "local", "latency_ms": 1},
                    [{"model": "claude-sonnet-5-5", "input": 100_000, "output": 0, "cache_write": 0, "cache_read": 0}])  # $0.20
    ops.log_request({"endpoint": "chat", "status": "ok", "ip": "v", "latency_ms": 1},
                    [{"model": "claude-haiku-5-5", "input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}])  # $0.10
    # eval spend doesn't count toward the public cap, and vice versa
    assert ops.spent_today("public") == pytest.approx(0.10)
    assert ops.spent_today("eval") == pytest.approx(0.20)
    for _ in range(5):  # push eval spend to $1.20: eval capped, public still open
        ops.log_request({"endpoint": "eval", "status": "ok", "ip": "local", "latency_ms": 1},
                        [{"model": "claude-sonnet-5-5", "input": 100_000, "output": 0, "cache_write": 0, "cache_read": 0}])
    assert ops.over_eval_cap() and not ops.over_daily_cap()


def test_client_ip_header_mode():
    headers = {"x-client-ip": "198.51.100.9", "x-forwarded-for": "6.6.6.6, 198.51.100.9, 66.241.124.1"}
    assert ops.client_ip(headers, "172.16.0.2", hops=0, ip_header="x-client-ip") == "198.51.100.9"
    assert ops.client_ip({}, "172.16.0.2", hops=0, ip_header="x-client-ip") == "172.16.0.2"  # header missing
    assert ops.client_ip(headers, "172.16.0.2", hops=0, ip_header="") == "172.16.0.2"          # not configured: ignored


def test_client_ip_behind_caddy():
    # Caddy ignores a client-supplied X-Forwarded-For and sends only the peer address; with one trusted
    # hop the app uses the last entry, so even a header that slipped through can't pick the bucket.
    assert ops.client_ip({"x-forwarded-for": "203.0.113.5"}, "172.18.0.3", hops=1, ip_header="") == "203.0.113.5"
    assert ops.client_ip({"x-forwarded-for": "10.0.0.1, 203.0.113.5"}, "172.18.0.3", hops=1, ip_header="") == "203.0.113.5"
