# Ask Nitin

My portfolio site plus **Ask Nitin**, an AI assistant that lets recruiters

1. ask questions about my experience and get short answers with citations, and
2. paste a job description and get a requirement-by-requirement fit report.

Every answer is grounded in the markdown files in `knowledge/`. If something isn't there, the assistant says so.

**Latest eval:** 35/37 graded cases passed (95%, gate 90%) on 2026-11-08; average chat answer 1.8 s, average fit report
5.0 s (`evals/results-2026-11-08.json`). Holdout: 6/6 on 2026-11-08. Average model cost per question on pre-launch test
traffic: $0.0003 (median $0.00020, 354 questions with a model call in `logs/requests.jsonl`).

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

`evals.yaml` (31 chat + 5 fit cases) is checked by `run_evals.py` with rules plus an LLM judge (Claude Sonnet 5.5:
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

## Deploy on AWS Lightsail (one $7/month VM: Docker Compose + Caddy)

Files: `Dockerfile`, `docker-compose.yml` (app + Caddy), `Caddyfile` (automatic HTTPS for nitinpalleti.com,
www → apex), `deploy/setup.sh` (one-time server setup), `deploy/.env.production.example`.
The app port is not published on the host; only Caddy (80/443) is. Logs live on the `app_data` volume, so the
daily spend cap survives restarts. All `aws` commands below use `--profile portfolio --region us-east-2`
(shortened to `$AWS`).

```bash
AWS="--profile portfolio --region us-east-2"
```

### 1. Create the server

```bash
aws lightsail create-instances $AWS --instance-names ask-nitin --availability-zone us-east-2a \
  --blueprint-id ubuntu_24_04 --bundle-id micro_3_0
aws lightsail allocate-static-ip $AWS --static-ip-name ask-nitin-ip
aws lightsail attach-static-ip $AWS --static-ip-name ask-nitin-ip --instance-name ask-nitin
aws lightsail get-static-ip $AWS --static-ip-name ask-nitin-ip --query staticIp.ipAddress --output text
# Firewall: SSH only from your current IP; 80/443 from anywhere (replaces the default rules).
MYIP=$(curl -s https://checkip.amazonaws.com)
aws lightsail put-instance-public-ports $AWS --instance-name ask-nitin --port-infos \
  "fromPort=22,toPort=22,protocol=tcp,cidrs=$MYIP/32" \
  "fromPort=80,toPort=80,protocol=tcp,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" \
  "fromPort=443,toPort=443,protocol=tcp,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" \
  "fromPort=443,toPort=443,protocol=udp,cidrs=0.0.0.0/0,ipv6Cidrs=::/0"
# SSH key (never commit it).
aws lightsail download-default-key-pair $AWS --query privateKeyBase64 --output text > ~/.ssh/lightsail-ask-nitin.pem
chmod 600 ~/.ssh/lightsail-ask-nitin.pem
```

### 2. DNS (Cloudflare)

Add `A @ → <static IP>` and `A www → <static IP>`, both **DNS only** (grey cloud), so Caddy can get the
certificates. Wait until `dig +short nitinpalleti.com` and `dig +short www.nitinpalleti.com` return the IP.

### 3. Deploy

```bash
ssh -i ~/.ssh/lightsail-ask-nitin.pem ubuntu@<static IP>
git clone https://github.com/nitinpalleti-swe/portfolio-site.git && cd portfolio-site
sudo bash deploy/setup.sh && exit          # log back in so the docker group applies
```

```bash
ssh -i ~/.ssh/lightsail-ask-nitin.pem ubuntu@<static IP>
cd portfolio-site && cp deploy/.env.production.example .env && chmod 600 .env
nano .env                                   # ANTHROPIC_API_KEY, fresh STATS_TOKEN and IP_HASH_SALT
docker compose up -d --build
```

### 4. Verify (from your laptop)

```bash
curl -sI https://nitinpalleti.com | head -1                 # 200, valid certificate
curl -sI https://www.nitinpalleti.com | grep -i location    # 301 → https://nitinpalleti.com/
curl -sI http://nitinpalleti.com | grep -i location         # → https
curl -s https://nitinpalleti.com/api/health                 # {"status":"ok",...}
curl -s -o /dev/null -w "%{http_code}\n" https://nitinpalleti.com/api/search?q=x   # 404 (production)
curl -s -o /dev/null -w "%{http_code}\n" https://nitinpalleti.com/api/stats        # 404 without the token
# Spoofed X-Forwarded-For must not dodge the rate limit: the 16th request returns 429.
for i in $(seq 1 16); do curl -s -o /dev/null -w "%{http_code} " -H "X-Forwarded-For: 10.0.0.$i" \
  https://nitinpalleti.com/api/chat -H 'content-type: application/json' -d '{"message":"Where is Nitin based?"}'; done
```

The rate-limit test uses up your own IP's hourly chat allowance (15/hour) for the next hour.

### Update, logs, snapshot, rollback

```bash
cd ~/portfolio-site && git pull && docker compose up -d --build   # update
docker compose logs -f app                                        # app logs (caddy: logs -f caddy)
docker compose exec app tail -n 5 /data/logs/requests.jsonl       # request log on the volume
aws lightsail create-instance-snapshot $AWS --instance-name ask-nitin --instance-snapshot-name ask-nitin-$(date +%F)
git checkout <good-commit> && docker compose up -d --build        # roll back the code
# Roll back the whole server: create-instances-from-snapshot, then move the static IP to the new instance.
```

### Teardown (stops all charges; a stopped instance still bills)

```bash
aws lightsail delete-instance $AWS --instance-name ask-nitin
aws lightsail release-static-ip $AWS --static-ip-name ask-nitin-ip    # unattached static IPs are billed
aws lightsail get-instance-snapshots $AWS --query 'instanceSnapshots[].name' --output text
aws lightsail delete-instance-snapshot $AWS --instance-snapshot-name <name>   # for each snapshot
```

Then delete the two A records in Cloudflare.

### Production checklist

- [ ] `.env` on the server only (chmod 600): `APP_ENV=production`, fresh `STATS_TOKEN` and `IP_HASH_SALT`, **no `EVAL_TOKEN`**
- [ ] Client IP: `TRUSTED_PROXY_HOPS=1`, `CLIENT_IP_HEADER` empty (Caddy ignores client-supplied X-Forwarded-For and sets its own)
- [ ] App port not published; only Caddy on 80/443; SSH open only to your IP
- [ ] One app container, one worker, `--no-proxy-headers`; logs on the `app_data` volume
- [ ] Spoofed-X-Forwarded-For test ends in HTTP 429
- [ ] Run `.venv/bin/python run_evals.py` locally before every deploy
