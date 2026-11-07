const REPO = "https://github.com/nitinpalleti-swe/paymentops-workbench";

/* ── Theme toggle ─────────────────────────────── */
const root = document.documentElement;
try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch {}
document.getElementById("themeBtn").addEventListener("click", () => {
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch {}
});

/* ── Citations: every claim points at evidence ── */
const CITES = {
  1: { src: "README.md", text: "Local-first, synthetic-data-only workbench for investigating payment exceptions and evaluating AI-assisted review before promotion.", href: `${REPO}#readme` },
  2: { src: "docs/CONTRACTS.md", text: "FastAPI validates request and response source IDs; malformed output, deadlines and rate limits become explicit review-required states.", href: `${REPO}/blob/main/docs/CONTRACTS.md` },
  3: { src: "evals/README.md", text: "Versioned development (30), held-out (20) and adversarial (10) fixtures, with a release-evaluation provenance block.", href: `${REPO}/tree/main/evals` },
  4: { src: "README.md → Approval workflow", text: "Versioned synthetic review drafts, optimistic concurrency, and one durable approval audit event.", href: `${REPO}#what-is-built` },
  6: { src: "résumé → JPMorganChase, Oct 2024 – present", text: "Production deployments on AWS ECS, Datadog monitoring and incident triage; internal AI skills, MCP tools and Copilot used under review and quality controls.", href: "resume.pdf" },
  5: { src: "README.md → Known limitations", text: "Bedrock, public demo and held-out evaluation remain unverified and are documented as blocked on explicit gates.", href: `${REPO}#known-limitations` },
};
const pop = document.getElementById("pop");
let hideTimer;
function showCite(el) {
  const c = CITES[el.dataset.cite];
  if (!c) return;
  clearTimeout(hideTimer);
  pop.innerHTML = `<span class="mono">source · ${c.src}</span>${c.text}<br><a href="${c.href}" target="_blank" rel="noopener">Open evidence ↗</a>`;
  pop.hidden = false;
  const r = el.getBoundingClientRect();
  const w = pop.offsetWidth;
  const left = Math.min(Math.max(12, r.left + window.scrollX - w / 2), document.documentElement.clientWidth - w - 12);
  pop.style.left = `${left}px`;
  pop.style.top = `${r.bottom + window.scrollY + 8}px`;
}
function hideCite() { hideTimer = setTimeout(() => (pop.hidden = true), 250); }
document.querySelectorAll(".cite").forEach((el) => {
  el.href = CITES[el.dataset.cite]?.href || "#";
  el.target = "_blank"; el.rel = "noopener";
  el.addEventListener("mouseenter", () => showCite(el));
  el.addEventListener("mouseleave", hideCite);
  el.addEventListener("focus", () => showCite(el));
  el.addEventListener("blur", hideCite);
  // touch: first tap shows, second follows link
  el.addEventListener("click", (e) => {
    if (matchMedia("(hover: none)").matches && pop.hidden) { e.preventDefault(); showCite(el); }
  });
});
pop.addEventListener("mouseenter", () => clearTimeout(hideTimer));
pop.addEventListener("mouseleave", hideCite);
document.addEventListener("click", (e) => { if (!e.target.closest(".cite, .pop")) pop.hidden = true; });

/* ── Architecture explorer ───────────────────── */
const ARCH = {
  ui: { t: "React operator UI", d: ["Case browser over 10 synthetic cases", "Ordered event timelines, rule flags, versioned policies", "Never supplies evidence to the AI service"] },
  api: { t: "Spring Boot business API", d: ["Tenant-scoped case and policy APIs", "PostgreSQL transactions, request budgets, structured failures", "Re-reads case, events, flags and eligible policy text before calling AI"] },
  db: { t: "PostgreSQL + pgvector", d: ["Keyword and offline vector-fixture retrieval", "Tenant and effective-date filters", "Policy provenance with source citations"] },
  ai: { t: "FastAPI AI boundary", d: ["Schema-validated requests and responses", "Deterministic mock by default; optional Bedrock Converse adapter", "Unknown source IDs or timeouts → review-required, never fake success"] },
  eval: { t: "Eval Gate", d: ["Versioned dev / held-out / adversarial fixtures", "Reusable offline reports and metrics", "Held-out set fingerprinted, not consumed, until an independent adapter is ready"] },
};
const detail = document.getElementById("archDetail");
function selectNode(key) {
  document.querySelectorAll(".node").forEach((n) => {
    const on = n.dataset.node === key;
    n.classList.toggle("active", on);
    n.setAttribute("aria-selected", on);
  });
  const a = ARCH[key];
  detail.innerHTML = `<span class="mono small muted">component</span><h4>${a.t}</h4><ul>${a.d.map((x) => `<li>${x}</li>`).join("")}</ul>`;
}
document.querySelectorAll(".node").forEach((n) => n.addEventListener("click", () => selectNode(n.dataset.node)));
selectNode("ui");

/* ── Mini eval gate ──────────────────────────── */
const CASES = [
  { label: "Response A", text: "Duplicate provider delivery detected (EVT-7, EVT-8). Policy POL-DUPLICATE@1 applies. Recommend human review.",
    verdict: "pass", why: "PASS — cites EVT-7, EVT-8, POL-DUPLICATE@1; all IDs exist in the case evidence. Routed to human review." },
  { label: "Response B", text: "This is a duplicate charge per policy POL-REFUND@3. Refund issued automatically.",
    verdict: "fail", why: "FAIL — POL-REFUND@3 is not in the retrieved evidence, and the system never moves money. Blocked." },
  { label: "Response C", text: "{ \"summary\": \"Looks like a dup\", \"sources\": ",
    verdict: "review", why: "REVIEW_REQUIRED — malformed JSON / schema violation. Fails closed instead of guessing." },
];
const casesEl = document.getElementById("gateCases");
const resultEl = document.getElementById("gateResult");
casesEl.insertAdjacentHTML("beforebegin", `<p class="gate-q">Case <code>CASE-003</code> · evidence: <code>EVT-7</code> <code>EVT-8</code> <code>POL-DUPLICATE@1</code></p>`);
CASES.forEach((c, i) => {
  const b = document.createElement("button");
  b.className = "case";
  b.innerHTML = `<span class="mono">${c.label}</span>${c.text}`;
  b.addEventListener("click", () => {
    casesEl.querySelectorAll(".case").forEach((x) => x.classList.remove("sel"));
    b.classList.add("sel");
    resultEl.className = `gate-result mono ${c.verdict}`;
    resultEl.textContent = c.why;
  });
  casesEl.appendChild(b);
});

/* ── Active nav + reveal on scroll ───────────── */
const links = [...document.querySelectorAll(".nav a")];
const io = new IntersectionObserver((entries) => {
  entries.forEach((e) => {
    if (e.isIntersecting) {
      e.target.classList.add("in");
      links.forEach((l) => l.classList.toggle("on", l.getAttribute("href") === `#${e.target.id}`));
    }
  });
}, { rootMargin: "-40% 0px -55% 0px" });
document.querySelectorAll(".block").forEach((s) => io.observe(s));

const revealIO = new IntersectionObserver((entries) => {
  entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); revealIO.unobserve(e.target); } });
}, { threshold: 0.08 });
document.querySelectorAll(".block").forEach((s) => { s.classList.add("reveal"); revealIO.observe(s); });
