/* Lintel front end. Plain JS, no build step. Written Oct 27 2026. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (id, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDate = (d) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d || "");
    return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}, ${m[1]}` : esc(d || "");
  };
  const NET_ERR = "Could not reach the records. Try again in a moment.";

  async function api(path, body, signal) {
    const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal } : { signal };
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error(NET_ERR);
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok || (data && data.error)) throw new Error((data && data.error) || NET_ERR);
    return data || {};
  }

  /* ---------------- Address search ---------------- */
  const q = $("#q");
  const list = $("#suggest");
  const clearBtn = $("#clear");
  const msg = $("#search-msg");
  let results = [];
  let active = -1;
  let timer = 0;
  let ctrl = null;

  function setOpen(open) {
    list.hidden = !open;
    q.setAttribute("aria-expanded", String(open));
    if (!open) { active = -1; q.removeAttribute("aria-activedescendant"); }
  }

  function renderSkeletonRows() {
    list.innerHTML = [62, 48, 70].map((w, i) =>
      `<li class="s-skel" role="presentation" id="sk${i}"><span class="sk" style="width:18px;height:18px;border-radius:50%"></span><span class="sk" style="width:${w}%;height:12px"></span></li>`).join("");
    setOpen(true);
  }

  function renderResults() {
    if (!results.length) { list.innerHTML = ""; setOpen(false); return; }
    list.innerHTML = results.map((r, i) =>
      `<li role="option" id="opt${i}" data-i="${i}" aria-selected="${i === active}">${icon("i-pin")}<span class="s-main">${esc(r.label)}</span>${r.borough ? `<span class="s-boro">${esc(r.borough)}</span>` : ""}</li>`).join("");
    setOpen(true);
    if (active >= 0) q.setAttribute("aria-activedescendant", "opt" + active);
  }

  function move(delta) {
    if (!results.length) return;
    active = (active + delta + results.length) % results.length;
    [...list.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    q.setAttribute("aria-activedescendant", "opt" + active);
    list.children[active] && list.children[active].scrollIntoView({ block: "nearest" });
  }

  async function search(text) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    renderSkeletonRows();
    try {
      const data = await api("/api/geocode?q=" + encodeURIComponent(text), null, ctrl.signal);
      results = (data.results || []).filter((r) => r && r.bbl).slice(0, 5);
      active = results.length ? 0 : -1;
      renderResults();
      msg.textContent = results.length ? "" : "No match in New York City. Check the street and borough.";
    } catch (e) {
      if (e.name === "AbortError") return;
      results = [];
      setOpen(false);
      msg.textContent = e.message;
    }
  }

  q.addEventListener("input", () => {
    const text = q.value.trim();
    clearBtn.hidden = !q.value;
    msg.textContent = "";
    clearTimeout(timer);
    if (text.length < 3) { if (ctrl) ctrl.abort(); results = []; setOpen(false); return; }
    timer = setTimeout(() => search(text), 250);
  });

  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden && results.length) renderResults(); else move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (results.length && active >= 0 && !list.hidden) pick(results[active]);
      else if (q.value.trim().length >= 3) { clearTimeout(timer); search(q.value.trim()); }
    } else if (e.key === "Escape") { setOpen(false); }
  });

  q.addEventListener("focus", () => { if (results.length && q.value.trim().length >= 3) renderResults(); });
  q.addEventListener("blur", () => setTimeout(() => setOpen(false), 120));

  list.addEventListener("mousedown", (e) => e.preventDefault());
  list.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li) pick(results[+li.dataset.i]);
  });

  clearBtn.addEventListener("click", () => {
    q.value = ""; clearBtn.hidden = true; results = []; msg.textContent = ""; setOpen(false); q.focus();
  });

  function pick(r) {
    if (!r) return;
    q.value = r.label;
    clearBtn.hidden = false;
    setOpen(false);
    q.blur();
    const url = new URL(location.href);
    url.searchParams.set("bbl", r.bbl);
    history.pushState({ bbl: r.bbl }, "", url);
    loadCard(r.bbl, r.label);
  }

  /* ---------------- Building card ---------------- */
  const result = $("#result");
  let cardSeq = 0;
  let current = null;

  function cardSkeleton(label) {
    return `<article class="card" aria-busy="true">
      <div class="card-head"><div style="flex:1;min-width:0">
        ${label ? `<h2 class="addr">${esc(label)}</h2>` : `<span class="sk" style="width:60%;height:30px"></span>`}
        <span class="sk" style="width:180px;height:12px;margin-top:12px"></span></div></div>
      <div class="facts">${"<div class=fact><span class=sk style='width:50%;height:10px'></span><span class=sk style='width:70%;height:16px;margin-top:8px'></span></div>".repeat(4)}</div>
      <div class="sk" style="height:64px;margin-top:22px;border-radius:12px"></div>
      <div class="sk" style="height:118px;margin-top:22px;border-radius:12px"></div>
      <div class="secs">${"<div class=sec><span class=sk style='width:45%;height:14px'></span><span class=sk style='width:100%;height:12px;margin-top:16px'></span><span class=sk style='width:85%;height:12px;margin-top:12px'></span><span class=sk style='width:92%;height:12px;margin-top:12px'></span></div>".repeat(2)}</div>
    </article>`;
  }

  async function loadCard(bbl, label) {
    const seq = ++cardSeq;
    current = null;
    document.body.classList.add("has-card");
    result.hidden = false;
    result.innerHTML = cardSkeleton(label);
    requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }));
    try {
      const data = await api("/api/core", { bbl });
      if (seq !== cardSeq) return;
      current = data;
      result.innerHTML = renderCard(data);
      if (!q.value && data.address) { q.value = data.address; clearBtn.hidden = false; }
      document.title = `${data.address || "Building"}. Lintel`;
      bindAsk();
    } catch (e) {
      if (seq !== cardSeq) return;
      result.innerHTML = `<article class="card card-err" role="alert"><p>${esc(e.message)}</p></article>`;
    }
  }

  const LEVEL = { good: "Looks fine", mixed: "Some concerns", bad: "Serious concerns" };
  const LINKS = { zola: "Zoning map", hpd: "HPD building page", dob: "DOB building page" };
  const CHIPS = [
    ["i-heat", "Heat this winter?", "Will I have heat this winter?"],
    ["i-pest", "Pests?", "Any pests in this building?"],
    ["i-wave", "Flood risk?", "What is the flood risk here?"],
    ["i-key", "Who owns it?", "Who owns this building?"],
  ];

  function renderCard(d) {
    const lot = d.lot || {};
    const num = (v) => (v == null || v === "" ? "Not listed" : esc(typeof v === "number" ? v.toLocaleString("en-US") : v));
    const v = d.verdict || {};
    const level = LEVEL[v.level] ? v.level : "mixed";
    const links = Object.entries(d.links || {}).map(([k, u]) => safeUrl(u) &&
      `<a class="pill-link" href="${esc(u)}" target="_blank" rel="noopener">${esc(LINKS[k] || k.toUpperCase())}${icon("i-out")}</a>`).filter(Boolean).join("");
    const sub = [d.borough, d.bbl ? `<span class="bbl">BBL ${esc(d.bbl)}</span>` : ""].filter(Boolean);

    return `<article class="card">
      <div class="card-head">
        <div style="min-width:0">
          <h2 class="addr">${esc(d.address || "This building")}</h2>
          <p class="addr-sub">${sub.map((s) => (s.startsWith("<") ? s : esc(s))).join(" &nbsp; ")}</p>
        </div>
        ${links ? `<div class="ext-links">${links}</div>` : ""}
      </div>
      <dl class="facts">
        <div class="fact"><dt>Built</dt><dd>${lot.year_built ? esc(lot.year_built) : "Not listed"}</dd></div>
        <div class="fact"><dt>Floors</dt><dd>${num(lot.floors)}</dd></div>
        <div class="fact"><dt>Homes</dt><dd>${num(lot.homes)}</dd></div>
        <div class="fact"><dt>Class</dt><dd>${num(lot.building_class)}</dd></div>
        ${lot.owner ? `<div class="fact wide"><dt>Owner of record</dt><dd>${esc(lot.owner)}</dd></div>` : ""}
      </dl>
      <div class="verdict" data-level="${level}">
        <span class="v-dot" aria-hidden="true"></span>
        <div><span class="v-label">${LEVEL[level]}</span><p class="v-line">${esc(v.line || "No verdict for this building yet.")}</p></div>
      </div>
      <section class="ask" aria-labelledby="ask-title">
        <h3 class="ask-title" id="ask-title">Ask about this building</h3>
        <form class="ask-form" id="ask-form" autocomplete="off">
          <input id="ask-q" type="text" maxlength="300" enterkeyhint="send" placeholder="Anything about this building" aria-label="Your question">
          <button class="send" id="ask-send" type="submit" aria-label="Ask" disabled>${icon("i-send")}</button>
        </form>
        <ul class="chips" id="ask-chips">${CHIPS.map(([ic, label, full]) =>
          `<li><button class="chip" type="button" data-q="${esc(full)}">${icon(ic)}${esc(label)}</button></li>`).join("")}</ul>
        <div class="answers" id="answers"></div>
      </section>
      ${(d.sections || []).length ? `<div class="secs">${d.sections.map(renderSection).join("")}</div>` : ""}
    </article>`;
  }

  function renderSection(s) {
    const items = s.items || [];
    const open = +s.count_open || 0;
    const total = +s.count_total || 0;
    const hasCounts = s.count_total != null;
    const count = hasCounts ? `<span class="count${open ? " has-open" : ""}"><b>${open}</b> open of ${total}</span>` : "";
    const rows = items.slice(0, 8).map((it) => {
      const u = safeUrl(it.url);
      const inner = `<span class="it-dot${it.open ? " open" : ""}" aria-hidden="true"></span>
        <span class="it-line">${it.class ? `<span class="it-cls">${esc(it.class)}</span>` : ""}${esc(it.line)}${it.open ? '<span class="sr">, open</span>' : ""}</span>
        <span class="it-date">${fmtDate(it.date)}</span>`;
      return `<li>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener" title="${esc(it.source || "Source record")}">${inner}</a>` : `<a>${inner}</a>`}</li>`;
    }).join("");
    return `<section class="sec" aria-label="${esc(s.title)}">
      <div class="sec-head"><h3 class="sec-title">${esc(s.title)}</h3>${count}</div>
      ${s.line ? `<p class="sec-line">${esc(s.line)}</p>` : ""}
      ${rows ? `<ul class="items">${rows}</ul>` : (s.line ? "" : `<p class="empty">Nothing on record in the last three years.</p>`)}
    </section>`;
  }

  /* ---------------- Ask ---------------- */
  let asking = false;
  let whySeq = 0;

  function bindAsk() {
    const form = $("#ask-form");
    const input = $("#ask-q");
    const send = $("#ask-send");
    input.addEventListener("input", () => { send.disabled = asking || !input.value.trim(); });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text || asking) return;
      input.value = ""; send.disabled = true;
      ask(text);
    });
    $("#ask-chips").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-q]");
      if (b && !asking) ask(b.dataset.q);
    });
    $("#answers").addEventListener("click", (e) => {
      const opt = e.target.closest("button[data-intent]");
      if (opt && !asking) { ask(opt.dataset.question, opt.dataset.intent, opt.textContent.trim()); return; }
      const t = e.target.closest(".why-toggle");
      if (t) {
        const body = document.getElementById(t.getAttribute("aria-controls"));
        const open = t.getAttribute("aria-expanded") !== "true";
        t.setAttribute("aria-expanded", String(open));
        body.hidden = !open;
      }
    });
  }

  function setAsking(on) {
    asking = on;
    document.querySelectorAll("#ask-chips button, #answers button[data-intent]").forEach((b) => { b.disabled = on; });
    const input = $("#ask-q"), send = $("#ask-send");
    if (send) send.disabled = on || !(input && input.value.trim());
  }

  async function ask(question, intent, shownAs) {
    if (!current) return;
    const answers = $("#answers");
    const el = document.createElement("article");
    el.className = "answer";
    el.setAttribute("aria-busy", "true");
    el.innerHTML = `<p class="a-q">${esc(shownAs ? `${question} (${shownAs})` : question)}</p>
      <span class="sk" style="width:94%;height:14px"></span><span class="sk" style="width:78%;height:14px;margin-top:10px"></span>
      <span class="sk" style="width:100%;height:40px;margin-top:16px"></span>`;
    answers.prepend(el);
    setAsking(true);
    const body = { bbl: current.bbl, question, workplace: null };
    if (intent) body.intent = intent;
    try {
      const r = await api("/api/ask", body);
      el.innerHTML = renderAnswer(question, r, shownAs);
    } catch (e) {
      el.innerHTML = `<p class="a-q">${esc(question)}</p><p class="a-text refused">${esc(e.message)}</p>`;
    }
    el.removeAttribute("aria-busy");
    setAsking(false);
  }

  function whyPanel(why, data, status) {
    if (!why) return "";
    const id = "why" + ++whySeq;
    const conf = typeof why.confidence === "number" ? `${Math.round(why.confidence * 100)}%` : null;
    const steps = (why.steps || []).map((s) => `<li>${esc(typeof s === "string" ? s : JSON.stringify(s))}</li>`).join("");
    const c = why.contract;
    const sig = c && c.signature ? String(c.signature) : "";
    const sigShort = sig ? (sig.includes(":") ? sig.split(":")[0] + ":" + sig.split(":").slice(1).join(":").slice(0, 12) : sig.slice(0, 16)) + "\u2026" : "";
    const sql = data && data.sql ? String(data.sql).trim() : "";
    const meta = [why.intent_label, conf].filter(Boolean).join(", ");
    return `<div class="why">
      <button class="why-toggle" type="button" aria-expanded="false" aria-controls="${id}">
        ${icon("i-steps", "wt-ic")}<span>${status === "clarify" ? "Why it asked" : status === "refused" ? "Why it declined" : "Why this answer"}</span>${meta ? `<span class="wt-meta">${esc(meta)}</span>` : ""}${icon("i-chev", "wt-chev")}
      </button>
      <div class="why-body" id="${id}" hidden>
        <div class="why-row">
          ${why.intent_label || why.intent ? `<span class="tag" title="${esc(why.intent || "")}">Understood <b>${esc(why.intent_label || why.intent)}</b></span>` : ""}
          ${conf ? `<span class="tag" data-band="${esc(why.band || "")}">Confidence <b>${conf}</b>${why.band ? `, ${esc(why.band)}` : ""}</span>` : ""}
          ${why.risk ? `<span class="tag">Risk <b>${esc(why.risk)}</b></span>` : ""}
          ${data && data.rows != null ? `<span class="tag">Rows read <b>${esc(data.rows)}</b></span>` : ""}
        </div>
        ${steps ? `<p class="why-h">Steps</p><ol class="why-steps">${steps}</ol>` : ""}
        ${sql ? `<p class="why-h">Query</p><pre class="sql"><code>${esc(sql)}</code></pre>` : ""}
        ${c && c.id ? `<p class="why-h">Signed contract</p><div class="contract"><span>${icon("i-seal", "")}</span><span>id <b>${esc(c.id)}</b></span>${sigShort ? `<span>sig <b title="${esc(sig)}">${esc(sigShort)}</b></span>` : ""}</div>`
          : status !== "ok" ? `<p class="why-h">Contract</p><div class="contract"><span>No contract signed, nothing ran.</span></div>` : ""}
      </div>
    </div>`;
  }

  function footLine(r) {
    const parts = [];
    if (r.data && r.data.via) parts.push(r.data.via === "zetaris" ? "Records via Zetaris" : "Records via NYC Open Data");
    const p = r.model && r.model.provider;
    if (p === "meterless") parts.push("model via Meterless Nightshift");
    else if (p === "fallback") parts.push("fallback model");
    return parts.length ? `<p class="a-foot">${esc(parts.join(", "))}</p>` : "";
  }

  function renderAnswer(question, r, shownAs) {
    const head = `<p class="a-q">${esc(shownAs ? `${question} (${shownAs})` : question)}</p>`;
    if (r.status === "clarify") {
      const opts = (r.options || []).filter((o) => o && o.intent).map((o) =>
        `<li><button class="chip plain" type="button" data-intent="${esc(o.intent)}" data-question="${esc(question)}">${esc(o.label || o.intent)}</button></li>`).join("");
      return `${head}<span class="a-mark clarify">${icon("i-steps")}One question first</span>
        <p class="a-text">${esc(r.question || "Which one did you mean?")}</p>
        ${opts ? `<ul class="chips">${opts}</ul>` : ""}${whyPanel(r.why, null, "clarify")}`;
    }
    if (r.status === "refused") {
      return `${head}<span class="a-mark refused">${icon("i-seal")}Not answered</span>
        <p class="a-text refused">${esc(r.message || "I can only answer about this building.")}</p>${whyPanel(r.why, null, "refused")}`;
    }
    const ev = (r.evidence || []).map((e) => {
      const u = safeUrl(e.url);
      const inner = `<span class="ev-label">${esc(e.label)}</span>${e.detail ? `<span class="ev-detail">${esc(e.detail)}</span>` : ""}
        <span class="ev-src">${esc(e.source || "Record")}${e.record ? ` ${esc(e.record)}` : ""}${u ? icon("i-out") : ""}</span>`;
      return `<li>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${inner}</a>` : `<a>${inner}</a>`}</li>`;
    }).join("");
    return `${head}<p class="a-text">${esc(r.answer || "The records show nothing on this.")}</p>
      ${ev ? `<ul class="evidence" aria-label="Evidence">${ev}</ul>` : ""}
      ${whyPanel(r.why, r.data, "ok")}${footLine(r)}`;
  }

  /* ---------------- Boot ---------------- */
  function fromUrl() {
    const bbl = (new URLSearchParams(location.search).get("bbl") || "").replace(/\D/g, "");
    if (bbl.length === 10) { loadCard(bbl); return true; }
    return false;
  }

  window.addEventListener("popstate", () => {
    if (!fromUrl()) {
      cardSeq++; current = null; result.hidden = true; result.innerHTML = "";
      document.body.classList.remove("has-card"); document.title = "Lintel. Know the building before you sign.";
    }
  });

  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
    }), { rootMargin: "0px 0px -60px 0px" });
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
  } else {
    document.querySelectorAll(".reveal").forEach((el) => el.classList.add("in"));
  }

  fromUrl();
})();
