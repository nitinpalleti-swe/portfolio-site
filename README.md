# Ask Nitin

My portfolio site plus "Ask Nitin": a small AI assistant that lets recruiters

1. ask questions about my experience and get answers with citations, and
2. paste a job description and get a fit report.

Every answer is grounded in the markdown files in `knowledge/`. The assistant
doesn't invent facts. If something isn't in those files, it says so.

## Stack

- `portfolio/` holds the static site (HTML, CSS, plain JS, no build step).
- `app.py` is a single FastAPI app. It serves the site at `/` and the API under `/api`.
- Claude via the official `anthropic` SDK. Provider and model names are set in `.env`.

## Run it locally

Requires Python 3.12.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then paste your ANTHROPIC_API_KEY into .env
.venv/bin/uvicorn app:app --reload --port 8000 --no-proxy-headers
```

Open http://localhost:8000. The health check is at http://localhost:8000/api/health.

`.env` is git-ignored, and the API key never leaves the server.

`--no-proxy-headers` matters: without it uvicorn rewrites the client address from
`X-Forwarded-For`, which lets anyone dodge the per-visitor rate limit. The app decides
whether to trust that header itself (`TRUSTED_PROXY_HOPS` in `.env`).

## Evals

`evals.yaml` holds ~30 cases: facts with required citations, things not in his background (must say so),
off-topic and prompt-injection attempts (must refuse / not comply), contact, and job-fit reports with
expected ratings. `run_evals.py` checks each answer with simple rules plus an LLM judge (Claude Sonnet 5.5:
"is every claim supported by the cited source?"), runs a headless-Chrome check that typing + Enter in the
widget sends a message, prints a pass-rate table, saves `evals/results-<date>.json`, and exits non-zero
below 90%.

```bash
.venv/bin/uvicorn app:app --port 8001 --no-proxy-headers   # in one terminal (APP_ENV=development)
.venv/bin/python run_evals.py                               # in another
.venv/bin/python run_evals.py --model claude-haiku-5-5 --model claude-sonnet-5-5   # compare chat models
```

Provider hiccups (connection errors, timeouts, 5xx) are retried once and then reported as ERROR, separate
from FAIL. The 90% gate counts only graded (PASS/FAIL) cases. Exit codes: 0 pass, 1 real failures below 90%,
2 setup problem or an inconclusive run (more than half the cases errored).

`evals-holdout.yaml` is a held-out set of 6 cases that were never used for tuning. Run it rarely
(`--suite evals-holdout.yaml --no-ui`) and don't tune against it.

Eval requests carry `EVAL_TOKEN` from `.env`; they skip rate limits and the answer cache (only in
development, only from localhost), and are logged as endpoint `eval`: excluded from public stats, and
charged to their own budget (`EVAL_DAILY_CAP_USD`, default $1.00) instead of the public
`DAILY_SPEND_CAP_USD`. One full run costs about $0.14.
