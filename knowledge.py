"""Ask Nitin — knowledge base and BM25 retrieval.

Each knowledge/*.md file is one topic; each "## " section is one citable chunk.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

ROOT = Path(__file__).parent


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

# Words that carry no meaning for search. "nitin"/"palleti" are here because every
# chunk is about him; "used"/"work" because nearly every question contains them.
STOP_WORDS = set(
    "a an the and or of to in on for with at by from as is are was were be been "
    "has have had do does did what which who whom how when where why can could would "
    "should will about his he him her she they them their it its this that these those "
    "any me my i you your tell much many there used use using work worked working "
    "nitin palleti".split()
)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())  # "Master's" -> ["master", "s"]
    words = [w for w in words if len(w) > 1 and w not in STOP_WORDS]
    # Crude stemming so related forms meet: "masters" -> "master", "monitors"/"monitoring" -> "monitor".
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]
    return [w[:-3] if len(w) > 6 and w.endswith("ing") else w for w in words]


CHUNKS = load_chunks()
CHUNKS_BY_ID = {c.id: c for c in CHUNKS}
BM25 = BM25Okapi([tokenize(f"{c.title} {c.text}") for c in CHUNKS])


def retrieve(query: str, k: int = 5) -> list[tuple[Chunk, float]]:
    """Return the k best-matching chunks for the query, best first (zero-score chunks dropped)."""
    scores = BM25.get_scores(tokenize(query))
    ranked = sorted(zip(CHUNKS, scores), key=lambda pair: pair[1], reverse=True)
    return [(chunk, float(score)) for chunk, score in ranked[:k] if score > 0]


def format_sources(chunks: list[Chunk]) -> str:
    if not chunks:
        return "<sources>\n(no matching excerpts)\n</sources>"
    body = "\n\n".join(f'<source id="{c.id}" title="{c.title}">\n{c.text}\n</source>' for c in chunks)
    return f"<sources>\n{body}\n</sources>"
