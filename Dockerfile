# Ask Nitin — production image (one process, one worker: rate limits, cache and spend cap live in memory).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the running app needs: no .env, tests, evals or local logs (see .dockerignore).
COPY app.py knowledge.py llm.py ops.py tools.py ./
COPY knowledge ./knowledge
COPY portfolio ./portfolio

EXPOSE 8000
# --no-proxy-headers: uvicorn must not rewrite the client address from X-Forwarded-For; the app
# decides which proxy header to trust (CLIENT_IP_HEADER / TRUSTED_PROXY_HOPS).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
