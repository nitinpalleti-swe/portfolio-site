"""Ask Nitin — the tools the chat assistant can call.

Every tool returns data plus a citation id ([github], [contact], or knowledge chunk ids),
so tool output follows the same grounding rules as retrieved sources.
"""

import asyncio
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field

from knowledge import CHUNKS, KNOWLEDGE_DIR, format_sources


# ── Tools the assistant can call ──────────────────────────────────────────────
# Every tool returns data plus a citation id ([github], [contact], or knowledge
# chunk ids), so tool output follows the same grounding rules as <sources>.

GITHUB_USER = "nitinpalleti-swe"
GITHUB_TTL_SECONDS = 600
_github_cache: dict = {"at": 0.0, "repos": None}

# Contact details. Each value must also appear in knowledge/profile.md (checked at startup),
# so the contact tool can never say anything the knowledge base doesn't.
CONTACT = {
    "email": "npalleti96@gmail.com",
    "linkedin": "https://www.linkedin.com/in/nitinpalleti/",
    "github": f"https://github.com/{GITHUB_USER}",
    "resume": "/resume.pdf",
}
_profile = (KNOWLEDGE_DIR / "profile.md").read_text(encoding="utf-8")
for _needle in ("npalleti96@gmail.com", "linkedin.com/in/nitinpalleti", f"github.com/{GITHUB_USER}", "/resume.pdf"):
    if _needle not in _profile:
        raise RuntimeError(f"contact detail {_needle!r} is not in knowledge/profile.md")

SECTIONS = sorted({c.id.split("#")[0] for c in CHUNKS})  # knowledge file names, e.g. "experience-copart"

TOOLS = [  # fixed order and content, so the cached prompt prefix stays identical between requests
    {
        "name": "get_github_repos",
        "description": "Live list of Nitin's public GitHub repositories (name, description, main language, "
                       "last updated date, URL), fetched from the GitHub API. Use for questions about his GitHub, "
                       "public code or what he is currently building. Cite the result as [github].",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "get_resume_section",
        "description": "Returns one whole topic file from Nitin's knowledge base, split into sections with ids. "
                       "Use when the <sources> excerpts are not enough and the full topic would help. "
                       "Cite the section ids it returns, e.g. [experience-copart#2].",
        "input_schema": {
            "type": "object",
            "properties": {"section": {"type": "string", "enum": SECTIONS, "description": "Topic file name."}},
            "required": ["section"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "contact_nitin",
        "description": "Returns Nitin's public contact details (email, LinkedIn, GitHub, résumé link) as "
                       "structured data; the website shows them as buttons under your answer. It does NOT send "
                       "any message. Use when someone asks how to reach, contact or hire him. Cite it as [contact].",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
]


@dataclass
class ToolOutput:
    content: str                                  # what the model sees
    sources: list[dict] = field(default_factory=list)  # citable sources for the widget popovers
    event: dict | None = None                     # extra widget event (contact buttons)
    is_error: bool = False


def _fetch_github_repos() -> list[dict]:
    url = f"https://api.github.com/users/{GITHUB_USER}/repos?type=owner&sort=updated&per_page=30"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ask-nitin-portfolio"}
    if token := os.getenv("GITHUB_TOKEN"):  # optional, zero-permission token: only raises the rate limit
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:  # no token: only public data is reachable
        data = json.load(resp)
    # Allowlist: only these public metadata fields ever leave this function (the API returns ~80).
    return [
        {"name": r["name"], "description": r.get("description") or "", "language": r.get("language") or "",
         "updated": r["updated_at"][:10], "url": r["html_url"]}
        for r in data if not r.get("private") and not r.get("fork")
    ]


async def get_github_repos() -> ToolOutput:
    now = time.time()
    if _github_cache["repos"] is None or now - _github_cache["at"] > GITHUB_TTL_SECONDS:
        try:
            _github_cache["repos"] = await asyncio.to_thread(_fetch_github_repos)
            _github_cache["at"] = now
        except Exception as exc:
            print(f"[tool] github error: {type(exc).__name__}: {exc}")
            return ToolOutput("GitHub is unavailable right now; say so and do not guess.", is_error=True)
    repos = _github_cache["repos"]
    fetched = time.strftime("%Y-%m-%d", time.gmtime(_github_cache["at"]))
    lines = [f"- {r['name']} ({r['language'] or 'no language listed'}, updated {r['updated']}): "
             f"{r['description'] or 'no description'} {r['url']}" for r in repos] or ["- (no public repositories)"]
    completeness = (f"This is the complete list of his public, non-fork repositories ({len(repos)} in total)."
                    if len(repos) < 30 else "This list may be incomplete (first 30 repositories shown).")
    text = (f"Public GitHub repositories of {GITHUB_USER}, live from the GitHub API on {fetched}. {completeness}\n"
            + "\n".join(lines))
    return ToolOutput(f'<source id="github">\n{text}\n</source>',
                      sources=[{"id": "github", "title": "GitHub — public repositories (live)", "text": text}])


def get_resume_section(section: str) -> ToolOutput:
    chunks = [c for c in CHUNKS if c.id.split("#")[0] == section]
    if not chunks:  # validate even though the schema is strict
        return ToolOutput(f"Unknown section. Valid sections: {', '.join(SECTIONS)}", is_error=True)
    return ToolOutput(format_sources(chunks), sources=[{"id": c.id, "title": c.title, "text": c.text} for c in chunks])


def contact_nitin() -> ToolOutput:
    text = (f"Email: {CONTACT['email']}\nLinkedIn: {CONTACT['linkedin']}\nGitHub: {CONTACT['github']}\n"
            f"Résumé: {CONTACT['resume']} on this site")
    return ToolOutput(f'<source id="contact">\n{text}\n</source>',
                      sources=[{"id": "contact", "title": "Contact details", "text": text}],
                      event={"type": "contact", "contact": CONTACT})


async def run_tool(name: str, args: dict) -> ToolOutput:
    args = args if isinstance(args, dict) else {}
    if name == "get_github_repos":
        return await get_github_repos()
    if name == "get_resume_section":
        return get_resume_section(str(args.get("section", "")))
    if name == "contact_nitin":
        return contact_nitin()
    return ToolOutput(f"Unknown tool {name!r}.", is_error=True)
