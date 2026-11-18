// Side menu shared by every page: navigation + production history (stored on the server, so it shows on every device).
(function () {
  const PAGES = [
    { href: "/", label: "ברכת סבתא", sub: "סרטון ישן ← קול משוכפל ← ברכה" },
    { href: "/talk", label: "תמונה מדברת", sub: "תמונה + אודיו + כתוביות" },
    { href: "/frames", label: "סרטון מתמונות", sub: "תמונת התחלה וסיום + תיאור" },
  ];
  const KIND = { blessing: "ברכה", talk: "תמונה מדברת", frames: "מתמונות" };
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
      <div class="sh-meta"><span class="sh-tag">${esc(KIND[o.kind] || o.kind)}</span> ${esc(when(o.created))} · ${esc(o.model || "")}${o.label ? " · " + esc(o.label) : ""}${o.cost != null ? ` · עלות $${(+o.cost).toFixed(2)}` : ""}</div>
      ${o.prompt ? `<div class="sh-prompt">${esc(o.prompt)}</div>` : ""}
      ${(o.inputs || []).length ? `<div class="sh-meta">קבצי המקור:</div><div class="sh-ins">${o.inputs.map(n => inputHtml(o, n)).join("")}</div>` : ""}
      <div class="sh-acts"><a href="${url(o.job, o.name)}" download="${esc(o.kind)}-${o.created}.mp4">הורד MP4</a>
        <button type="button" data-del>מחק מההיסטוריה</button></div>
    </div>`;
    v.onclick = async e => {
      if (e.target === v || e.target.closest(".sh-x")) { v.remove(); return; }
      if (e.target.closest("[data-del]")) {
        if (!confirm("למחוק את הסרטון הזה מהשרת?")) return;
        try { await fetch(`/api/jobs/${o.job}/outputs/${encodeURIComponent(o.name)}`, { method: "DELETE" }); } catch (x) {}
        v.remove(); load();
      }
    };
    document.body.appendChild(v);
  }

  // pages call this after a video is finished and saved on the server
  window.Shell = {
    async record(job, entry) {
      try {
        const r = await fetch(`/api/jobs/${job}/record`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(entry) });
        if (r.ok && dr.classList.contains("open")) load();
      } catch (e) { /* history is best-effort; the video itself is already saved */ }
    },
  };
})();
