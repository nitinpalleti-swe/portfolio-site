# Ask Nitin

My portfolio site plus **Ask Nitin**, an AI assistant that lets recruiters

1. ask questions about my experience and get short answers with citations, and
2. paste a job description and get a requirement-by-requirement fit report.

Every answer is grounded in the markdown files in `knowledge/`. If something isn't there, the assistant says so.

**Latest eval:** 30/33 graded cases passed (91%, gate 90%) on 2026-11-08; average chat answer 1.6 s, average fit report
5.1 s (`evals/results-2026-11-08.json`). Holdout: 6/6 on 2026-11-08. Average model cost per question on pre-launch test
traffic: $0.0003 (median $0.00018, 261 questions with a model call in `logs/requests.jsonl`).

## Architecture

```text
browser ── portfolio/ (static site + chat.js widget)
   │  POST /api/chat (SSE)   POST /api/fit (JSON)
   ▼
FastAPI app.py ── ops.py: rate limits · answer cache · spend caps · request log (logs/requests.jsonl)
   │
   ├─ knowledge.py: knowledge/*.md → sections with ids → BM25 retrieval
   ├─ tools.py: GitHub repos (live) · résumé section · contact buttons
   └─ llm.py: the only module that calls a model
          Claude Haiku 5.5 (chat, streamed, tool loop ≤ 3 rounds)
          Claude Sonnet 5.5 (fit report, Pydantic structured output → deterministic checks)
```

## Key design decisions

- **Grounding + citations.** The model only sees retrieved sections and must cite their ids; citations that weren't provided are flagged, and the widget shows each one with its source text.
- **BM25, not embeddings.** About 60 short, factual sections: keyword search is explainable, free, deterministic and easy to test (`tests/test_retrieval.py`).
- **Code checks the model.** Fit reports go through rules: unknown sources become gaps, Python/ML can't be a full match without a supported alternative, years need a source, no-sponsorship jobs get a work-authorization gap, overall fit is capped by the rows.
- **Guardrails.** Fixed off-topic refusal, prompt-injection resistance, job descriptions treated as data, contact intent always shows contact buttons, no phone number anywhere.
- **Cost caps.** Per-visitor rate limits (15 chat / 5 fit per hour), one-hour answer cache, daily public spend cap ($0.50) and a separate eval budget ($1.00), computed from the log so they survive restarts.
- **Hold out the holdout.** `evals-holdout.yaml` is never used for tuning.

## Run it locally (one command)

```bash
./run.sh                # http://localhost:8000  (PORT=8001 ./run.sh, RELOAD=1 ./run.sh)
```

It creates `.venv` with Python 3.12, installs requirements, and creates `.env` from `.env.example` on first run
(add your `ANTHROPIC_API_KEY`). It starts uvicorn with `--no-proxy-headers`: without it uvicorn rewrites the
client address from `X-Forwarded-For`, which would let anyone dodge the rate limit.

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q tests/`

## Evals

`evals.yaml` (27 chat + 5 fit cases) is checked by `run_evals.py` with rules plus an LLM judge (Claude Sonnet 5.5:
"is every claim supported by the cited source?") and a headless-Chrome check that typing + Enter sends a message.

```bash
PORT=8001 ./run.sh                     # terminal 1 (APP_ENV=development)
.venv/bin/python run_evals.py          # terminal 2: table + evals/results-<date>.json
.venv/bin/python run_evals.py --model claude-haiku-5-5 --model claude-sonnet-5-5   # compare models
.venv/bin/python run_evals.py --suite evals-holdout.yaml --no-ui                   # rarely; never tune on it
```

Provider hiccups are retried once, then reported as ERROR (not graded). Exit codes: 0 pass, 1 real failures below 90%,
2 setup problem or inconclusive. Eval requests use `EVAL_TOKEN` (development + localhost only), skip rate limits and
cache, and are charged to `EVAL_DAILY_CAP_USD`. One full run costs about $0.14–0.21.

## Deploy (Fly.io: one always-on machine + 1 GB volume, about $3.84/month)

Files: `Dockerfile`, `.dockerignore`, `fly.toml`. Steps (needs a Fly account and `flyctl`):

```bash
fly launch --no-deploy --copy-config --name <your-app-name>     # keeps fly.toml; region dfw
fly volumes create ask_nitin_data --size 1 --region dfw
fly secrets set ANTHROPIC_API_KEY=... STATS_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))") IP_HASH_SALT=$(python3 -c "import secrets;print(secrets.token_hex(32))")
fly deploy --ha=false && fly scale count 1
```

### Production checklist

- [ ] `APP_ENV=production` (in fly.toml): disables `/api/search` and the eval bypass
- [ ] Fresh `STATS_TOKEN` and `IP_HASH_SALT` as host secrets; **no `EVAL_TOKEN`** in production
- [ ] `ANTHROPIC_API_KEY` only as a host secret; optional `GITHUB_TOKEN` with zero scopes
- [ ] Client IP: `CLIENT_IP_HEADER=Fly-Client-IP`, `TRUSTED_PROXY_HOPS=0` (Fly's rightmost X-Forwarded-For entry is its own address)
- [ ] Exactly 1 machine, 1 worker, `--no-proxy-headers`; health check `/api/health`; `LOG_DIR=/data/logs` on the volume
- [ ] Spoofing test after deploy: 16 requests with fake headers must end in HTTP 429:
      `for i in $(seq 1 16); do curl -s -o /dev/null -w "%{http_code} " -H "X-Forwarded-For: 10.0.0.$i" -H "Fly-Client-IP: 10.0.0.$i" https://<app>.fly.dev/api/chat -H 'content-type: application/json' -d '{"message":"Where is Nitin based?"}'; done`
- [ ] Run `.venv/bin/python run_evals.py` locally before every deploy
