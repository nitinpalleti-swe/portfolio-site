/* Ask Nitin — chat widget (plain JS, no build step).
   Talks to POST /api/chat, which streams Server-Sent Events:
     {type:"sources", sources:[{id,title,text}]}  → excerpts the answer may cite
     {type:"delta", text}                          → answer text, token by token
     {type:"done", cited, invalid}                 → citation check
     {type:"error", message}                       → friendly error
   Citations arrive as [source-id] and are rendered as numbered [n] chips in the
   site's existing citation style; hovering or tapping one shows the source text. */
(() => {
  const SUGGESTIONS = [
    "What is Nitin working on now?",
    "Has Nitin used Kafka?",
    "Tell me about PaymentOps Workbench.",
  ];
  const HISTORY_LIMIT = 12; // 6 user/assistant exchanges
  const CITE_RE = /\[([a-z0-9-]+(?:#\d+)?)\]/g; // [experience-copart#2], [github], [contact]
  const TOOL_LABELS = { get_github_repos: "GitHub (live)", get_resume_section: "Résumé section", contact_nitin: "Contact info" };

  const history = [];
  let busy = false;

  /* ── DOM ─────────────────────────────────────── */
  const fab = el("button", { class: "chat-fab", type: "button", "aria-expanded": "false", "aria-controls": "chatPanel" },
    `<span class="dot" aria-hidden="true"></span>Ask about Nitin`);
  const panel = el("section", { class: "chat-panel", id: "chatPanel", role: "dialog", "aria-label": "Ask about Nitin", hidden: "" }, `
    <header class="chat-head">
      <div>
        <h2>Ask about Nitin</h2>
        <p class="mono">Answers come only from his résumé, portfolio and project README, with sources.</p>
      </div>
      <button class="chat-close" type="button" aria-label="Close chat">×</button>
    </header>
    <div class="chat-log" aria-live="polite"></div>
    <form class="chat-form">
      <label class="sr-only" for="chatInput">Your question</label>
      <textarea id="chatInput" rows="1" maxlength="1000" placeholder="Ask a question…" required></textarea>
      <button class="chat-send" type="submit">Send</button>
    </form>
    <p class="chat-foot mono">AI-generated from cited sources. Verify anything important with Nitin.</p>`);
  const pop = el("div", { class: "pop chat-pop", role: "tooltip", hidden: "" });
  document.body.append(fab, panel, pop);

  const log = panel.querySelector(".chat-log");
  const form = panel.querySelector(".chat-form");
  const input = panel.querySelector("textarea");
  const send = panel.querySelector(".chat-send");

  // Intro + suggested questions
  const intro = el("div", { class: "chat-suggest" });
  intro.append(el("p", { class: "chat-intro" }, "Hi! I can answer questions about Nitin's experience, skills and projects. Try one of these:"));
  SUGGESTIONS.forEach((q) => {
    const b = el("button", { type: "button" }, escapeHtml(q));
    b.addEventListener("click", () => ask(q));
    intro.append(b);
  });
  log.append(intro);

  /* ── Open / close ────────────────────────────── */
  function open() {
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    input.focus();
  }
  function close() {
    panel.hidden = true;
    pop.hidden = true;
    fab.setAttribute("aria-expanded", "false");
    fab.focus();
  }
  fab.addEventListener("click", open);
  panel.querySelector(".chat-close").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) close(); });

  /* ── Input ───────────────────────────────────── */
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) ask(q);
  });
  function autosize() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; }

  /* ── Ask ─────────────────────────────────────── */
  async function ask(question) {
    if (busy) return;
    setBusy(true);
    intro.remove();
    input.value = ""; autosize();
    addMsg("user").textContent = question;
    const bubble = addMsg("bot");
    bubble.innerHTML = `<span class="chat-typing" aria-label="Thinking"><span></span><span></span><span></span></span>`;

    const sources = {};
    const tools = [];
    let contact = null;
    let reply = "";
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history: history.slice(-HISTORY_LIMIT) }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.error === "string" ? body.error : "Something went wrong. Please try again.");
      }
      await readEvents(res, (ev) => {
        if (ev.type === "sources") {
          ev.sources.forEach((s) => (sources[s.id] = s)); // retrieval first, then tool results
        } else if (ev.type === "tool") {
          if (!tools.includes(ev.name)) tools.push(ev.name);
        } else if (ev.type === "contact") {
          contact = ev.contact;
        } else if (ev.type === "reset") {
          reply = ev.text; // server dropped a pre-tool-call preamble
          renderAnswer(bubble, reply, sources);
        } else if (ev.type === "delta") {
          reply += ev.text;
          renderAnswer(bubble, reply, sources);
          scrollDown();
        } else if (ev.type === "error") {
          throw new Error(ev.message);
        }
      });
      if (!reply.trim()) throw new Error("No answer came back. Please try again.");
      renderAnswer(bubble, reply, sources, true);
      renderExtras(bubble, tools, contact);
      history.push({ role: "user", content: question }, { role: "assistant", content: reply });
    } catch (err) {
      bubble.className = "chat-msg error";
      bubble.textContent = err.message || "Something went wrong. Please try again.";
    } finally {
      setBusy(false);
      scrollDown();
      input.focus();
    }
  }

  // Parse a streamed text/event-stream body: events are "data: {json}" separated by blank lines.
  async function readEvents(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }

  /* ── Rendering ───────────────────────────────── */
  // Escape the model's text, then turn [source-id] into numbered chips. Ids that are not in
  // this answer's sources are dropped (fail closed). A half-streamed "[exper…" is hidden.
  function renderAnswer(bubble, text, sources, final = false) {
    const numbers = {};
    let html = escapeHtml(final ? text : text.replace(/\[[a-z0-9#-]*$/, ""));
    html = html.replace(CITE_RE, (_, id) => {
      if (!sources[id]) return "";
      numbers[id] ??= Object.keys(numbers).length + 1;
      return `<button type="button" class="cite chat-cite" data-src="${escapeHtml(id)}" aria-label="Source ${numbers[id]}: ${escapeHtml(sources[id].title)}">[${numbers[id]}]</button>`;
    });
    bubble.innerHTML = html.replace(/ +([.,;:])/g, "$1").replace(/\n/g, "<br>");
    bubble.querySelectorAll(".chat-cite").forEach((b) => bindCite(b, sources[b.dataset.src]));
  }

  // Under the answer: contact buttons (plain links — nothing is sent) and a subtle "used" line.
  function renderExtras(bubble, tools, contact) {
    if (contact) {
      const row = el("div", { class: "chat-contact" });
      const links = [
        ["Email", `mailto:${contact.email}`, false],
        ["LinkedIn ↗", contact.linkedin, true],
        ["GitHub ↗", contact.github, true],
        ["Résumé ↓", contact.resume, true],
      ];
      links.forEach(([label, href, newTab]) => {
        if (!/^(https:\/\/|mailto:|\/)/.test(href)) return; // only safe link schemes
        const a = el("a", { class: "btn", href, ...(newTab ? { target: "_blank", rel: "noopener" } : {}) });
        a.textContent = label;
        row.append(a);
      });
      bubble.append(row);
    }
    if (tools.length) {
      const line = el("p", { class: "chat-tools mono" });
      line.textContent = `Used: ${tools.map((t) => TOOL_LABELS[t] || t).join(" · ")}`;
      bubble.after(line);
    }
  }

  function bindCite(btn, src) {
    const show = () => showPop(btn, src);
    btn.addEventListener("mouseenter", show);
    btn.addEventListener("focus", show);
    btn.addEventListener("mouseleave", hidePopSoon);
    btn.addEventListener("blur", hidePopSoon);
    btn.addEventListener("click", (e) => { e.stopPropagation(); pop.hidden ? show() : (pop.hidden = true); });
  }

  let hideTimer;
  function showPop(anchor, src) {
    clearTimeout(hideTimer);
    pop.innerHTML = `<span class="mono">source · ${escapeHtml(src.id)}</span><strong>${escapeHtml(src.title)}</strong><br>${escapeHtml(truncate(src.text, 320))}`;
    pop.hidden = false;
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth;
    const left = Math.max(12, Math.min(r.left + window.scrollX - w / 2, document.documentElement.clientWidth - w - 12));
    const below = r.bottom + 8 + pop.offsetHeight < window.innerHeight;
    pop.style.left = `${left}px`;
    pop.style.top = `${(below ? r.bottom + 8 : r.top - pop.offsetHeight - 8) + window.scrollY}px`;
  }
  function hidePopSoon() { hideTimer = setTimeout(() => (pop.hidden = true), 250); }
  pop.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  pop.addEventListener("mouseleave", hidePopSoon);
  document.addEventListener("click", (e) => { if (!e.target.closest(".chat-cite, .chat-pop")) pop.hidden = true; });
  log.addEventListener("scroll", () => (pop.hidden = true));

  /* ── Helpers ─────────────────────────────────── */
  function addMsg(kind) {
    const m = el("div", { class: `chat-msg ${kind}` });
    log.append(m);
    scrollDown();
    return m;
  }
  function setBusy(on) { busy = on; send.disabled = on; }
  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function truncate(s, n) { return s.length > n ? `${s.slice(0, n - 1)}…` : s; }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
  function el(tag, attrs = {}, html = "") {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (html) n.innerHTML = html;
    return n;
  }
})();
