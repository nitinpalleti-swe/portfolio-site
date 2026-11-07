"""Ask Nitin — backend for the portfolio site.

Serves the static site in portfolio/ at "/" and the assistant's API under "/api".
Run locally:  uvicorn app:app --reload --port 8000
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")  # secrets stay server-side; nothing from .env is sent to the browser

app = FastAPI(title="Ask Nitin", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Static site last, so /api/* routes take precedence over files.
app.mount("/", StaticFiles(directory=ROOT / "portfolio", html=True), name="site")
