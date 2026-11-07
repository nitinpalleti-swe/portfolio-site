"""Ask Nitin — backend for the portfolio site.

Serves the static site in portfolio/ at "/" and the assistant's API under "/api".
Run locally:  uvicorn app:app --reload --port 8000
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")  # secrets stay server-side; nothing from .env is sent to the browser

DEV = os.getenv("APP_ENV", "production") == "development"

app = FastAPI(title="Ask Nitin", docs_url=None, redoc_url=None, openapi_url=None)


# ── Knowledge base ────────────────────────────────────────────────────────────
# Each knowledge/*.md file is one topic. Every "## " section becomes one chunk,
# with a stable id "<file>#<n>" (e.g. "experience-copart#2") used for citations.

KNOWLEDGE_DIR = ROOT / "knowledge"
PHONE_LIKE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


@dataclass(frozen=True)
class Chunk:
    id: str     # "experience-copart#2"
    title: str  # "Experience — Copart, … — Event-driven backend services"
    text: str


def load_chunks(folder: Path = KNOWLEDGE_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(folder.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if PHONE_LIKE.search(raw):  # guardrail: phone numbers must never enter the knowledge base
            raise RuntimeError(f"{path.name} contains something that looks like a phone number")
        doc_title = raw.splitlines()[0].lstrip("# ").strip()
        sections = re.split(r"^## ", raw, flags=re.MULTILINE)[1:]  # drop the preamble above the first "## "
        for n, section in enumerate(sections, start=1):
            heading, _, body = section.partition("\n")
            chunks.append(Chunk(f"{path.stem}#{n}", f"{doc_title} — {heading.strip()}", body.strip()))
    return chunks


# ── Retrieval: BM25 keyword ranking ───────────────────────────────────────────
# BM25 scores a chunk higher when it contains the query's words, especially rare
# words (IDF), with diminishing returns for repeats and a penalty for long chunks.

def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


CHUNKS = load_chunks()
BM25 = BM25Okapi([tokenize(f"{c.title} {c.text}") for c in CHUNKS])


def retrieve(query: str, k: int = 5) -> list[tuple[Chunk, float]]:
    """Return the k best-matching chunks for the query, best first (zero-score chunks dropped)."""
    scores = BM25.get_scores(tokenize(query))
    ranked = sorted(zip(CHUNKS, scores), key=lambda pair: pair[1], reverse=True)
    return [(chunk, float(score)) for chunk, score in ranked[:k] if score > 0]


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "chunks": len(CHUNKS)}


if DEV:  # dev-only: inspect retrieval. Not registered unless APP_ENV=development.
    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=200), k: int = Query(5, ge=1, le=20)) -> dict:
        return {
            "query": q,
            "results": [
                {"id": c.id, "score": round(s, 3), "title": c.title, "text": c.text}
                for c, s in retrieve(q, k)
            ],
        }


# Static site last, so /api/* routes take precedence over files.
app.mount("/", StaticFiles(directory=ROOT / "portfolio", html=True), name="site")
