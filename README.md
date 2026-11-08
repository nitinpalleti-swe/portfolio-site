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
