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
    <div class="chat-tabs" role="tablist">
      <button type="button" role="tab" data-tab="ask" aria-selected="true">Ask</button>
      <button type="button" role="tab" data-tab="fit" aria-selected="false">Check fit for a role</button>
    </div>
    <div class="chat-fit" hidden>
      <form class="fit-form">
        <label class="fit-label" for="fitInput">Paste a job description. Each requirement is checked against Nitin's sources.</label>
        <textarea id="fitInput" rows="7" minlength="50" maxlength="8000" placeholder="Paste the job description here…" required></textarea>
        <div class="fit-actions"><span class="fit-count mono">0 / 8000</span><button class="chat-send" type="submit">Check fit</button></div>
      </form>
      <div class="fit-result" aria-live="polite"></div>
    </div>
    <div class="chat-log" aria-live="polite"></div>
    <form class="chat-form">
      <label class="sr-only" for="chatInput">Your question</label>
      <textarea id="chatInput" rows="1" maxlength="1000" placeholder="Ask a question…" required></textarea>
      <button class="chat-send" type="submit">Send</button>
    </form>
    <p class="chat-foot mono">AI-generated from cited sources. Verify anything important with Nitin. Questions are logged to improve answers.</p>`);
  const pop = el("div", { class: "pop chat-pop", role: "tooltip", hidden: "" });
  document.body.append(fab, panel, pop);

  const log = panel.querySelector(".chat-log");
  const form = panel.querySelector(".chat-form");
  const input = panel.querySelector("#chatInput"); // explicit: the fit tab has its own textarea and button
  const send = form.querySelector(".chat-send");

  // Intro + suggested questions
  const intro = el("div", { class: "chat-suggest" });
  intro.append(el("p", { class: "chat-intro" }, "Hi! I can answer questions about Nitin's experience, skills and projects. Try one of these:"));
  SUGGESTIONS.forEach((q) => {
    const b = el("button", { type: "button" }, escapeHtml(q));
    b.addEventListener("click", () => ask(q));
    intro.append(b);
  });
  log.append(intro);

  /* ── Tabs: Ask | Check fit for a role ────────── */
  const fitPane = panel.querySelector(".chat-fit");
  const fitForm = panel.querySelector(".fit-form");
  const fitInput = panel.querySelector("#fitInput");
  const fitSend = fitForm.querySelector(".chat-send");
  const fitCount = panel.querySelector(".fit-count");
  const fitResult = panel.querySelector(".fit-result");
  let tab = "ask";
  function showTab(name) {
    tab = name;
    panel.querySelectorAll(".chat-tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    const fitOn = name === "fit";
    fitPane.hidden = !fitOn;
    log.hidden = fitOn;
    form.hidden = fitOn;
    panel.classList.toggle("wide", fitOn);
    pop.hidden = true;
    (fitOn ? fitInput : input).focus();
  }
  panel.querySelectorAll(".chat-tabs [role=tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  /* ── Open / close ────────────────────────────── */
  function open() {
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    (tab === "fit" ? fitInput : input).focus();
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
        if (body.limited || body.capped) {  // hourly limit or daily budget: a friendly notice, not an error
          bubble.textContent = body.error;
          renderExtras(bubble, [], body.contact);
          return;
        }
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

  /* ── Job fit ─────────────────────────────────── */
  const FIT_STATUS = { match: ["✅", "Match"], partial: ["⚠️", "Partial"], gap: ["❌", "Gap"] };
  const FIT_LABEL = { strong: "Strong fit", good: "Good fit", partial: "Partial fit", weak: "Weak fit" };

  fitInput.addEventListener("input", () => (fitCount.textContent = `${fitInput.value.length} / 8000`));
  fitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const jd = fitInput.value.trim();
    if (jd.length < 50) {
      fitResult.innerHTML = `<p class="chat-msg error">Please paste a full job description (at least 50 characters).</p>`;
      return;
    }
    fitSend.disabled = true;
    fitSend.textContent = "Checking…";
    fitResult.innerHTML = `<p class="fit-loading mono" role="status"><span class="chat-typing" aria-hidden="true"><span></span><span></span><span></span></span> <span class="fit-stage">Reading the job description…</span></p>`;
    // The request is one call (usually 4–8 s); the stage text is time-based, to show it's still working.
    const stageEl = fitResult.querySelector(".fit-stage");
    const stages = [[2000, "Checking evidence against his sources…"], [10000, "Still working, almost there…"]];
    const timers = stages.map(([ms, text]) => setTimeout(() => (stageEl.textContent = text), ms));
    try {
      const res = await fetch("/api/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_description: jd }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok && (body.limited || body.capped)) {
        const note = el("div", { class: "chat-msg bot" });
        note.textContent = body.error;
        renderExtras(note, [], body.contact);
        fitResult.innerHTML = "";
        fitResult.append(note);
        return;
      }
      if (!res.ok) throw new Error(typeof body.error === "string" ? body.error : "Something went wrong. Please try again.");
      renderFit(body);
    } catch (err) {
      fitResult.innerHTML = "";
      fitResult.append(el("p", { class: "chat-msg error" }, escapeHtml(err.message || "Something went wrong. Please try again.")));
    } finally {
      timers.forEach(clearTimeout);
      fitSend.disabled = false;
      fitSend.textContent = "Check fit";
    }
  });

  function renderFit(r) {
    const sources = r.sources || {};
    const numbers = {};
    const chip = (id) => {
      if (!id || !sources[id]) return "";
      numbers[id] ??= Object.keys(numbers).length + 1;
      return `<button type="button" class="cite chat-cite" data-src="${escapeHtml(id)}" aria-label="Source ${numbers[id]}: ${escapeHtml(sources[id].title)}">[${numbers[id]}]</button>`;
    };
    const counts = { match: 0, partial: 0, gap: 0 };
    const rows = (r.requirements || []).map((q) => {
      counts[q.status] = (counts[q.status] || 0) + 1;
      const [icon, label] = FIT_STATUS[q.status] || ["•", q.status];
      return `<tr class="fit-${escapeHtml(q.status)}"><td class="fit-icon"><span role="img" aria-label="${label}">${icon}</span></td>
        <td><strong>${escapeHtml(q.requirement)}</strong><span class="fit-evidence">${escapeHtml(q.evidence)} ${chip(q.source_id)}</span></td></tr>`;
    }).join("");
    const level = FIT_LABEL[r.overall_fit] ? r.overall_fit : "weak";
    fitResult.innerHTML = `
      <div class="fit-summary">
        <div class="fit-title"><h3>${escapeHtml(r.role_title || "Role")}</h3><span class="fit-badge fit-${level}">${FIT_LABEL[level]}</span></div>
        <p>${escapeHtml(r.summary || "")}</p>
        <p class="fit-tally mono">✅ ${counts.match} match · ⚠️ ${counts.partial} partial · ❌ ${counts.gap} gap</p>
      </div>
      ${rows ? `<table class="fit-table"><thead><tr><th scope="col"><span class="sr-only">Status</span></th><th scope="col">Requirement and evidence</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
      ${(r.checks || []).length ? `<details class="fit-checks"><summary class="mono">${r.checks.length} adjustment${r.checks.length > 1 ? "s" : ""} by the evidence checker</summary><ul>${r.checks.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul></details>` : ""}`;
    fitResult.querySelectorAll(".chat-cite").forEach((b) => bindCite(b, sources[b.dataset.src]));
    fitPane.scrollTop = fitResult.offsetTop - 8; // scroll inside the panel only, never the page behind it
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
    // A tap fires focus (which opens the popover) and then click: only close on a later, separate click.
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!pop.hidden && popAnchor === btn && Date.now() - popShownAt > 400) pop.hidden = true;
      else show();
    });
  }

  let hideTimer, popAnchor = null, popShownAt = 0;
  function showPop(anchor, src) {
    clearTimeout(hideTimer);
    if (pop.hidden || popAnchor !== anchor) popShownAt = Date.now();
    popAnchor = anchor;
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
  fitPane.addEventListener("scroll", () => (pop.hidden = true));

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
