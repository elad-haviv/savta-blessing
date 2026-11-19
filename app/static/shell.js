// Side menu shared by every page: navigation + production history (stored on the server, so it shows on every device).
(function () {
  const PAGES = [
    { href: "/", label: "ברכת סבתא", sub: "סרטון ישן ← קול משוכפל ← ברכה" },
    { href: "/talk", label: "תמונה מדברת", sub: "תמונה + אודיו + כתוביות" },
    { href: "/frames", label: "סרטון מתמונות", sub: "תמונת התחלה וסיום + תיאור" },
    { href: "/reference", label: "סרטון מרפרנסים", sub: "תמונות רפרנס + תיאור" },
  ];
  const KIND = { blessing: "ברכה", talk: "תמונה מדברת", frames: "מתמונות", refs: "מרפרנסים" };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const url = (job, name) => `/api/jobs/${job}/files/${encodeURIComponent(name)}`;
  const when = t => new Date(t * 1000).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" });

  const css = `
  .sh-btn { position:fixed; top:12px; right:12px; z-index:40; background:var(--card,#fff); color:var(--ink,#2b2420); border:1px solid var(--line,#e8dfd3); border-radius:12px; width:44px; height:44px; padding:0; font-size:21px; line-height:1; cursor:pointer; box-shadow:0 2px 10px rgba(0,0,0,.08); }
  main > h1 { padding-right:54px; }
  main > h1 + .sub { padding-right:54px; }
  .sh-ov { position:fixed; inset:0; background:rgba(30,20,10,.35); z-index:50; opacity:0; pointer-events:none; transition:opacity .2s; }
  .sh-ov.open { opacity:1; pointer-events:auto; }
  .sh-dr { position:fixed; top:0; bottom:0; right:0; width:min(360px,88vw); background:var(--bg,#faf6f0); z-index:51; transform:translateX(105%); transition:transform .22s ease; display:flex; flex-direction:column; box-shadow:-6px 0 24px rgba(0,0,0,.12); }
  .sh-dr.open { transform:none; }
  .sh-head { display:flex; align-items:center; justify-content:space-between; padding:14px 16px 6px; }
  .sh-head b { font-size:18px; }
  .sh-x { background:transparent; color:var(--muted,#8a7f76); border:0; font-size:22px; padding:4px 8px; cursor:pointer; }
  .sh-nav { padding:4px 10px 10px; display:flex; flex-direction:column; gap:4px; border-bottom:1px solid var(--line,#e8dfd3); }
  .sh-nav a { display:block; padding:9px 12px; border-radius:10px; color:var(--ink,#2b2420); text-decoration:none; }
  .sh-nav a small { display:block; color:var(--muted,#8a7f76); font-size:12px; }
  .sh-nav a.cur { background:#f1e3d6; }
  .sh-nav a:hover { background:#f4ece1; }
  .sh-hh { display:flex; align-items:center; justify-content:space-between; padding:12px 16px 4px; }
  .sh-hh b { font-size:15px; }
  .sh-hh button { background:transparent; color:var(--accent,#b4572d); border:0; font-size:13px; cursor:pointer; padding:4px; }
  .sh-list { overflow:auto; padding:4px 10px 24px; flex:1; }
  .sh-it { display:flex; gap:10px; align-items:center; padding:8px; border-radius:10px; cursor:pointer; }
  .sh-it:hover { background:#f4ece1; }
  .sh-it img, .sh-it .sh-ph { width:64px; height:64px; object-fit:cover; border-radius:8px; background:#e9dfd3; flex:0 0 auto; }
  .sh-it div { min-width:0; }
  .sh-it .t { font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sh-it .m { font-size:12px; color:var(--muted,#8a7f76); }
  .sh-tag { unicode-bidi:isolate; font-size:11px; background:#f1e3d6; border-radius:6px; padding:1px 6px; color:#7a4a2e; }
  .sh-empty { color:var(--muted,#8a7f76); font-size:14px; padding:10px 8px; }
  .sh-vw { position:fixed; inset:0; z-index:60; background:rgba(20,14,8,.6); display:flex; align-items:flex-start; justify-content:center; overflow:auto; padding:20px 10px; }
  .sh-card { background:var(--card,#fff); border-radius:14px; width:min(760px,100%); padding:16px 18px 20px; }
  .sh-card video { width:100%; border-radius:10px; background:#000; max-height:70vh; }
  .sh-card h3 { margin:0 0 6px; font-size:17px; display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
  .sh-meta { font-size:13px; color:var(--muted,#8a7f76); margin:6px 0; }
  .sh-prompt { font-size:13px; background:#fcf8f2; border-radius:8px; padding:8px 10px; white-space:pre-wrap; margin:8px 0; }
  .sh-ins { display:flex; flex-wrap:wrap; gap:10px; margin-top:8px; align-items:flex-start; }
  .sh-ins img { width:120px; border-radius:8px; }
  .sh-ins audio { width:260px; max-width:100%; }
  .sh-ins a { color:var(--accent,#b4572d); font-size:14px; }
  .sh-card h3 span, .sh-prompt, .sh-it .t { unicode-bidi:plaintext; text-align:start; }
  .sh-tr { margin:14px 6px 0; }
  .sh-strip { position:relative; height:56px; border-radius:8px; background:#2b2420 center/100% 100% no-repeat; touch-action:none; user-select:none; -webkit-user-select:none; }
  .sh-dim { position:absolute; top:0; bottom:0; background:rgba(0,0,0,.6); pointer-events:none; }
  .sh-win { position:absolute; top:-4px; bottom:-4px; border-top:4px solid #f5c542; border-bottom:4px solid #f5c542; cursor:grab; }
  .sh-win .h { position:absolute; top:-4px; bottom:-4px; width:18px; background:#f5c542; display:flex; align-items:center; justify-content:center; cursor:ew-resize; }
  .sh-win .h::before { content:""; position:absolute; inset:0 -12px; }  /* bigger touch target */
  .sh-win .h i { width:3px; height:20px; border-radius:2px; background:#5a4300; }
  .sh-win .h[data-h="s"] { left:-18px; border-radius:6px 0 0 6px; }
  .sh-win .h[data-h="e"] { right:-18px; border-radius:0 6px 6px 0; }
  .sh-ph { position:absolute; top:-6px; bottom:-6px; width:2px; margin-left:-1px; background:#fff; box-shadow:0 0 3px rgba(0,0,0,.6); pointer-events:none; }
  .sh-trinfo { display:flex; flex-wrap:wrap; gap:8px; align-items:center; font-size:14px; margin-top:12px; }
  .sh-trinfo b { font-variant-numeric:tabular-nums; }
  .sh-trinfo button { background:var(--accent,#b4572d); color:#fff; border:0; border-radius:8px; padding:7px 14px; font-size:14px; cursor:pointer; font-family:inherit; }
  .sh-trinfo button:disabled { opacity:.5; }
  .sh-fine { font-size:13px; margin-top:8px; color:var(--muted,#8a7f76); }
  .sh-fine summary { cursor:pointer; }
  .sh-fine input { width:72px; padding:5px 7px; border:1px solid var(--line,#e8dfd3); border-radius:8px; font:inherit; margin:6px 4px 0; }
  .sh-acts { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; align-items:center; }
  .sh-acts a { color:var(--accent,#b4572d); }
  .sh-acts button { background:transparent; color:var(--err,#b3261e); border:1px solid var(--err,#b3261e); border-radius:8px; padding:4px 10px; font-size:13px; cursor:pointer; font-family:inherit; }
  `;
  document.head.insertAdjacentHTML("beforeend", `<style>${css}</style>`);

  const here = location.pathname.replace(/\/$/, "") || "/";
  document.body.insertAdjacentHTML("beforeend", `
    <div class="sh-ov" id="sh-ov"></div>
    <aside class="sh-dr" id="sh-dr" aria-label="תפריט">
      <div class="sh-head"><b>תפריט</b><button class="sh-x" id="sh-x" aria-label="סגור">✕</button></div>
      <nav class="sh-nav">${PAGES.map(p => `<a href="${p.href}" class="${p.href === here ? "cur" : ""}">${esc(p.label)}<small>${esc(p.sub)}</small></a>`).join("")}</nav>
      <div class="sh-hh"><b>היסטוריית הפקות</b><button id="sh-ref" type="button">רענן</button></div>
      <div class="sh-list" id="sh-list"><div class="sh-empty">טוען...</div></div>
    </aside>`);
  const btn = document.createElement("button");
  btn.className = "sh-btn"; btn.type = "button"; btn.setAttribute("aria-label", "תפריט"); btn.textContent = "☰";
  document.body.appendChild(btn);  // floats in the corner, so it's reachable from anywhere on a long page

  const dr = document.getElementById("sh-dr"), ov = document.getElementById("sh-ov"), list = document.getElementById("sh-list");
  let items = [], ttl = 14;
  const open = () => { dr.classList.add("open"); ov.classList.add("open"); load(); };
  const close = () => { dr.classList.remove("open"); ov.classList.remove("open"); };
  btn.onclick = open; ov.onclick = close; document.getElementById("sh-x").onclick = close;
  document.getElementById("sh-ref").onclick = load;
  document.addEventListener("keydown", e => { if (e.key === "Escape") { const v = document.querySelector(".sh-vw"); v ? v.remove() : close(); } });

  async function load() {
    try {
      const r = await fetch("/api/history"); if (!r.ok) throw new Error(r.status);
      const j = await r.json(); items = j.items || []; ttl = j.ttl_days || ttl; render();
    } catch (e) { list.innerHTML = `<div class="sh-empty">טעינת ההיסטוריה נכשלה</div>`; }
  }
  function render() {
    if (!items.length) { list.innerHTML = `<div class="sh-empty">עוד אין הפקות. כל סרטון שתפיק יופיע כאן, בכל המכשירים.</div>`; return; }
    list.innerHTML = items.map((o, i) => `<div class="sh-it" data-i="${i}">
      ${o.thumb ? `<img src="${url(o.job, o.thumb)}" alt="" loading="lazy">` : `<div class="sh-ph"></div>`}
      <div><div class="t">${esc(o.title || o.prompt || "סרטון")}</div>
        <div class="m"><span class="sh-tag">${esc(KIND[o.kind] || o.kind)}</span> ${esc(when(o.created))}</div>
        <div class="m">${esc((o.model || "").split("/").pop())}${o.cost != null ? ` · $${(+o.cost).toFixed(2)}` : ""}</div></div>
    </div>`).join("") + `<div class="sh-empty" style="font-size:12px">הקבצים נשמרים בשרת כ-${ttl} ימים.</div>`;
  }
  list.onclick = e => { const it = e.target.closest(".sh-it"); if (it) view(items[+it.dataset.i]); };

  function inputHtml(o, n) {
    const u = url(o.job, n);
    if (/\.(jpe?g|png|webp)$/i.test(n)) return `<a href="${u}" target="_blank" rel="noopener"><img src="${u}" alt=""></a>`;
    if (/\.(mp3|wav|m4a|ogg)$/i.test(n)) return `<audio controls preload="none" src="${u}"></audio>`;
    if (/^subs\./.test(n)) return `<a href="${u}" target="_blank" rel="noopener">קובץ הכתוביות</a>`;
    return `<a href="${u}" target="_blank" rel="noopener">${esc(n)}</a>`;
  }
  function view(o) {
    const v = document.createElement("div");
    v.className = "sh-vw";
    v.innerHTML = `<div class="sh-card">
      <h3><span>${esc(o.title || "סרטון")}</span><button class="sh-x" aria-label="סגור">✕</button></h3>
      <video controls playsinline src="${url(o.job, o.name)}"${o.thumb ? ` poster="${url(o.job, o.thumb)}"` : ""}></video>
      <div class="sh-tr" dir="ltr"><div class="sh-strip" data-strip>
        <div class="sh-dim" data-dl></div><div class="sh-dim" data-dr></div>
        <div class="sh-win" data-win><span class="h" data-h="s"><i></i></span><span class="h" data-h="e"><i></i></span></div>
        <div class="sh-ph" data-ph></div></div></div>
      <div class="sh-trinfo"><span>חיתוך: <b data-tinfo>…</b></span><button type="button" data-trim>שמור את הקטע</button><span data-tmsg></span></div>
      <details class="sh-fine"><summary>כוונון מדויק (חינם, נוצר קובץ חדש והמקור נשאר)</summary>
        מ-<input type="number" data-ts min="0" step="0.1" inputmode="decimal"> עד <input type="number" data-te min="0.2" step="0.1" inputmode="decimal"> שניות</details>
      <div class="sh-meta"><span class="sh-tag">${esc(KIND[o.kind] || o.kind)}</span> ${esc(when(o.created))} · ${esc(o.model || "")}${o.label ? " · " + esc(o.label) : ""}${o.cost != null ? ` · עלות $${(+o.cost).toFixed(2)}` : ""}</div>
      ${o.prompt ? `<div class="sh-prompt">${esc(o.prompt)}</div>` : ""}
      ${(o.inputs || []).length ? `<div class="sh-meta">קבצי המקור:</div><div class="sh-ins">${o.inputs.map(n => inputHtml(o, n)).join("")}</div>` : ""}
      <div class="sh-acts"><a href="${url(o.job, o.name)}" download="${esc(o.kind)}-${o.created}.mp4">הורד MP4</a>
        <button type="button" data-del>מחק מההיסטוריה</button></div>
    </div>`;
    // ---- WhatsApp-style trimmer: drag either handle, or the window itself; playback loops inside the selection
    const vid = v.querySelector("video"), q = s => v.querySelector(s);
    const stripEl = q("[data-strip]"), win = q("[data-win]"), dl = q("[data-dl]"), dr = q("[data-dr]"), ph = q("[data-ph]");
    const ts = q("[data-ts]"), te = q("[data-te]"), info = q("[data-tinfo]");
    const T = { s: 0, e: 0, d: 0 }, MIN = 0.2;
    const clamp = (x, a, b) => Math.min(Math.max(x, a), b), snap = x => Math.round(x * 10) / 10;
    function draw() {
      if (!T.d) return;
      const p = x => (x / T.d * 100) + "%";
      dl.style.left = "0"; dl.style.width = p(T.s); dr.style.left = p(T.e); dr.style.right = "0";
      win.style.left = p(T.s); win.style.width = p(T.e - T.s);
      info.textContent = `${T.s.toFixed(1)} – ${T.e.toFixed(1)}  ·  ${(T.e - T.s).toFixed(1)} שנ׳`;
      if (document.activeElement !== ts) ts.value = T.s.toFixed(1);
      if (document.activeElement !== te) te.value = T.e.toFixed(1);
    }
    vid.addEventListener("loadedmetadata", async () => {
      T.d = vid.duration; T.s = 0; T.e = T.d; te.max = T.d.toFixed(2); ts.max = (T.d - MIN).toFixed(2); draw();
      try {
        const r = await fetch(`/api/jobs/${o.job}/strip?name=${encodeURIComponent(o.name)}`);
        if (r.ok) stripEl.style.backgroundImage = `url("${url(o.job, (await r.json()).name)}")`;
      } catch (x) {}
    });
    vid.addEventListener("timeupdate", () => {
      if (!T.d) return;
      if (!vid.paused && vid.currentTime >= T.e - 0.03) vid.currentTime = T.s;
      ph.style.left = (vid.currentTime / T.d * 100) + "%";
    });
    vid.addEventListener("play", () => { if (vid.currentTime < T.s || vid.currentTime >= T.e - 0.05) vid.currentTime = T.s; });
    let drag = null;
    const timeAt = e => { const r = stripEl.getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width * T.d, 0, T.d); };
    stripEl.addEventListener("pointerdown", e => {
      if (!T.d) return;
      const t = timeAt(e), h = e.target.closest("[data-h]");
      if (h) drag = { k: h.dataset.h };
      else if (e.target.closest("[data-win]")) drag = { k: "move", off: t - T.s, len: T.e - T.s };
      else { vid.currentTime = clamp(t, T.s, T.e); return; }  // a tap outside the window just seeks
      stripEl.setPointerCapture(e.pointerId); vid.pause(); e.preventDefault();
    });
    stripEl.addEventListener("pointermove", e => {
      if (!drag) return;
      const t = timeAt(e);
      if (drag.k === "s") { T.s = clamp(snap(t), 0, T.e - MIN); vid.currentTime = T.s; }
      else if (drag.k === "e") { T.e = Math.min(clamp(snap(t), T.s + MIN, T.d), T.d); if (T.d - T.e < 0.05) T.e = T.d; vid.currentTime = Math.max(T.s, T.e - 0.05); }
      else { T.s = clamp(snap(t - drag.off), 0, Math.max(0, T.d - drag.len)); T.e = Math.min(T.s + drag.len, T.d); vid.currentTime = T.s; }
      draw();
    });
    const endDrag = () => { if (drag) { drag = null; vid.currentTime = T.s; } };
    stripEl.addEventListener("pointerup", endDrag); stripEl.addEventListener("pointercancel", endDrag);
    ts.addEventListener("change", () => { T.s = clamp(+ts.value || 0, 0, T.e - MIN); vid.currentTime = T.s; draw(); });
    te.addEventListener("change", () => { T.e = clamp(+te.value || T.d, T.s + MIN, T.d); vid.currentTime = Math.max(T.s, T.e - 0.05); draw(); });
    v.onclick = async e => {
      if (e.target === v || e.target.closest(".sh-x")) { v.remove(); return; }
      if (e.target.closest("[data-trim]")) {
        const msg = v.querySelector("[data-tmsg]"), start = snap(T.s), end = T.e >= T.d - 0.05 ? T.d : snap(T.e);
        if (!T.d) return;
        if (start <= 0.01 && end >= T.d - 0.01) { msg.textContent = "גרור את הקצוות כדי לבחור קטע"; return; }
        msg.textContent = "שומר את הקטע..."; e.target.disabled = true;
        try {
          const r = await postJSON(`/api/jobs/${o.job}/trim`, { name: o.name, start, end });
          const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.status);
          await Shell.record(o.job, { name: j.name, kind: o.kind, title: o.title, model: o.model, prompt: o.prompt, cost: 0,
                                      label: `${o.label ? o.label + " · " : ""}חיתוך ${start}–${(+j.end).toFixed(1)} שנ׳`, inputs: o.inputs || [] });
          v.remove(); await load();
          const n = items.find(x => x.name === j.name); if (n) view(n);
        } catch (x) { msg.textContent = "החיתוך נכשל: " + x.message; e.target.disabled = false; }
        return;
      }
      if (e.target.closest("[data-del]")) {
        if (!confirm("למחוק את הסרטון הזה מהשרת?")) return;
        try { await fetch(`/api/jobs/${o.job}/outputs/${encodeURIComponent(o.name)}`, { method: "DELETE" }); } catch (x) {}
        v.remove(); load();
      }
    };
    document.body.appendChild(v);
  }

  const postJSON = (u, body) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  window.Shell = {
    // after a video is finished and saved on the server
    async record(job, entry) {
      try {
        const r = await postJSON(`/api/jobs/${job}/record`, entry);
        if (r.ok && dr.classList.contains("open")) load();
      } catch (e) { /* history is best-effort; the video itself is already saved */ }
    },
    // right after a paid generation is submitted: if the page dies before the video is saved, it can be collected later
    async trackGen(job, g) { try { await postJSON(`/api/jobs/${job}/gens`, g); } catch (e) {} },
    async genDone(job, genId, part = "", status = "done", error = "") {
      try { await postJSON(`/api/jobs/${job}/gens/${encodeURIComponent(genId)}`, { status, part, error }); } catch (e) {}
    },
  };

  // ---------------------------------------------------------------- collect paid generations that were cut off
  document.head.insertAdjacentHTML("beforeend", `<style>
    .sh-rs { position:fixed; left:12px; right:12px; bottom:12px; z-index:45; display:flex; flex-direction:column; gap:8px; pointer-events:none; }
    .sh-rs div { pointer-events:auto; background:#2b2420; color:#fff; border-radius:12px; padding:10px 14px; font-size:14px; box-shadow:0 6px 20px rgba(0,0,0,.2); max-width:560px; margin:0 auto; width:100%; }
    .sh-rs a { color:#ffcf9e; cursor:pointer; text-decoration:underline; }
  </style>`);
  const rs = document.createElement("div"); rs.className = "sh-rs"; document.body.appendChild(rs);
  function note(id, html, sticky) {
    let el = document.getElementById("sh-n-" + id);
    if (!el) { el = document.createElement("div"); el.id = "sh-n-" + id; rs.appendChild(el); }
    el.innerHTML = html;
    el.querySelector("[data-open]")?.addEventListener("click", () => { el.remove(); open(); });
    if (!sticky) setTimeout(() => el.remove(), 15000);
  }
  const getKey = () => { try { return localStorage.getItem("savta_or_key") || sessionStorage.getItem("savta_or_key") || ""; } catch (e) { return ""; } };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const label = { blessing: "ברכה", talk: "תמונה מדברת", frames: "סרטון מתמונות", refs: "סרטון מרפרנסים" };

  async function collect(g, onStatus) {
    const key = getKey(), id = g.gen_id.slice(-8), what = `${label[g.page]} (${when(g.created)})`;
    const say = t => { onStatus?.(t); note(id, esc(t), true); };
    const auth = { Authorization: `Bearer ${key}` };
    const poll = g.polling_url ? (g.polling_url.startsWith("http") ? g.polling_url : "https://openrouter.ai" + g.polling_url) : `https://openrouter.ai/api/v1/videos/${g.gen_id}`;
    say(`אוסף את ההפקה ${what} מ-OpenRouter, בלי חיוב נוסף...`);
    try {
      let cur, deadline = Date.now() + 30 * 60 * 1000;
      for (;;) {
        const r = await fetch(poll, { headers: auth });
        if (r.status === 404) throw new Error("OpenRouter כבר לא מחזיק את ההפקה הזו");
        if (!r.ok) throw new Error(`OpenRouter ${r.status}`);
        cur = await r.json();
        if (cur.status === "completed") break;
        if (["failed", "cancelled", "expired"].includes(cur.status)) throw new Error(`ההפקה ${cur.status === "expired" ? "פגה" : "נכשלה"} ב-OpenRouter`);
        if (Date.now() > deadline) throw new Error("ההפקה עדיין לא הסתיימה. רענן מאוחר יותר");
        say(`ההפקה עדיין בעבודה ב-OpenRouter (${cur.status || "pending"})...`);
        await sleep(5000);
      }
      const pf = new FormData(); pf.append("seconds", g.seconds || 99999);
      if (cur.urls?.length) { say("השרת מוריד את הסרטון..."); pf.append("url", cur.urls[0]); }
      else if (cur.unsigned_urls?.length) {
        const v = await fetch(cur.unsigned_urls[0], { headers: auth });
        if (!v.ok) throw new Error(`הורדת הסרטון מ-OpenRouter נכשלה (${v.status})`);
        const total = +v.headers.get("content-length") || 0, rd = v.body.getReader(), bufs = []; let got = 0;
        for (;;) {
          const { done, value } = await rd.read(); if (done) break;
          bufs.push(value); got += value.length;
          say(`מוריד את הסרטון: ${(got / 1e6).toFixed(1)}${total ? " מתוך " + (total / 1e6).toFixed(1) : ""} MB`);
        }
        pf.append("file", new Blob(bufs, { type: "video/mp4" }), "video.mp4");
        say(`מעלה לשרת (${(got / 1e6).toFixed(1)} MB)...`);
      } else throw new Error("OpenRouter לא החזיר קישור לסרטון");
      let r = await fetch(`/api/jobs/${g.job}/part`, { method: "POST", body: pf });
      if (!r.ok) { let m = ""; try { m = (await r.json()).detail; } catch (x) {} throw new Error("שמירת הסרטון בשרת נכשלה " + (m || r.status)); }
      const part = await r.json();
      let name = part.name, lbl = g.label;
      if (g.page === "talk" && g.chunks === 1 && g.audio) {  // finish the job the page would have done: join + subtitles
        say(g.stitch?.subs ? "צורב כתוביות..." : "מחבר את הסרטון עם האודיו...");
        r = await postJSON(`/api/jobs/${g.job}/stitch`, { parts: [{ name: part.name, seconds: g.seconds }], audio: g.audio, ...(g.stitch || {}) });
        if (r.ok) name = (await r.json()).name; else lbl += " · בלי כתוביות";
      } else if (g.chunks > 1) lbl = `${lbl} · קטע ${g.chunk + 1} מתוך ${g.chunks}`;
      await Shell.record(g.job, { name, kind: g.page, title: g.title || "הפקה ששוחזרה", model: g.model, label: (lbl ? lbl + " · " : "") + "שוחזר",
                                  prompt: g.prompt, cost: cur.usage?.cost ?? null, inputs: g.inputs || [] });
      await Shell.genDone(g.job, g.gen_id, name);
      onStatus?.("נשמר! הסרטון בהיסטוריה (תפריט ☰).", `/api/jobs/${g.job}/files/${name}`);
      note(id, `ההפקה ${esc(what)} נשמרה. <a data-open>פתח בהיסטוריה</a>`, true);
      return name;
    } catch (e) {
      if (!/עדיין לא הסתיימה/.test(e.message)) await Shell.genDone(g.job, g.gen_id, "", "failed", e.message);
      note(id, `לא הצלחתי לשחזר את ${esc(what)}: ${esc(e.message)}`, true);
      throw e;
    }
  }
  Shell.collect = collect;
  (async function resumePending() {
    try {
      const r = await fetch("/api/gens/pending"); if (!r.ok) return;
      const items = (await r.json()).items || [];
      if (!items.length) return;
      if (!getKey()) { note("key", `יש ${items.length} הפקות ששולמו ולא נשמרו. הזן מפתח OpenRouter ורענן כדי לאסוף אותן.`, true); return; }
      for (const g of items) { try { await collect(g); } catch (e) {} }
    } catch (e) {}
  })();
})();
