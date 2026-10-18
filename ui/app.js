const $ = (id) => document.getElementById(id);
const FIELDS = ["location", "breed", "color", "sex", "size", "max_days", "transport", "q", "sort"];
let cfg = {}, searches = [], dogs = [], lastVisit = null, debounce, refreshTimer;
let breedOptions = [];
let hiddenSources = new Set(), knownSources = [], sourceCounts = {};

// Auth token (phone/remote): pass ?token=... once; it is remembered in localStorage.
const TOKEN = (() => {
  try {
    const t = new URLSearchParams(location.search).get("token");
    if (t) localStorage.setItem("token", t);
    return localStorage.getItem("token") || "";
  } catch { return ""; }
})();
const api = (path, opts = {}) =>
  fetch(path, { ...opts, headers: { ...(opts.headers || {}), ...(TOKEN ? { Authorization: "Bearer " + TOKEN } : {}) } })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return r.json(); });

try { lastVisit = parseFloat(localStorage.getItem("lastVisit")) || null; } catch {}
addEventListener("beforeunload", () => { try { localStorage.setItem("lastVisit", Date.now() / 1000); } catch {} });

function getParams() {
  const p = {};
  FIELDS.forEach((f) => { const v = $(f).value.trim(); if (v) p[f] = v; });
  p.radius = $("radius").value;
  if ($("statewide").checked) p.statewide = "1";
  if ($("max_days").value && $("include_undated").checked) p.include_undated = "1";
  if ($("undated_last").checked && $("sort").value === "newest") p.undated_last = "1";
  const ages = [...document.querySelectorAll("#age input:checked")].map((c) => c.value);
  if (ages.length) p.age = ages.join(",");
  if (hiddenSources.size) p.hide_sources = [...hiddenSources].join("|");
  return p;
}
function setParams(p = {}) {
  FIELDS.forEach((f) => ($(f).value = p[f] ?? (f === "location" ? cfg.default_location : f === "transport" ? "include" : f === "sort" ? "newest" : "")));
  $("radius").value = p.radius ?? cfg.default_radius; $("radius-val").textContent = $("radius").value;
  $("statewide").checked = p.statewide === "1"; $("radius").disabled = $("statewide").checked;
  $("include_undated").checked = p.include_undated === "1"; $("undated-wrap").hidden = !$("max_days").value;
  $("undated_last").checked = p.undated_last === "1"; $("undated-last-wrap").hidden = $("sort").value !== "newest";
  const ages = (p.age || "").split(",");
  document.querySelectorAll("#age input").forEach((c) => (c.checked = ages.includes(c.value)));
  hiddenSources = new Set((p.hide_sources || "").split("|").filter(Boolean));
}

async function load() {
  const qs = new URLSearchParams(getParams()).toString();
  try {
    const res = await api("/api/dogs?" + qs);
    dogs = res.dogs;
    sourceCounts = res.source_counts || {};
    breedOptions = res.breeds || [];
    renderSourceFilter();
    $("summary").textContent = `${res.count} dog${res.count === 1 ? "" : "s"} near ${res.origin}`;
    $("filters-done").textContent = `Show ${res.count} dog${res.count === 1 ? "" : "s"}`;
    render();
  } catch (e) { $("summary").textContent = "⚠ " + e.message; $("grid").innerHTML = ""; }
}

function srcName(s) { return SRC[s] || s; }
function renderSourceFilter() {
  const names = [...new Set([...knownSources, ...Object.keys(sourceCounts), ...hiddenSources])]
    .sort((a, b) => (sourceCounts[b] || 0) - (sourceCounts[a] || 0) || srcName(a).localeCompare(srcName(b)));
  $("sources-filter").innerHTML = names.map((n) => {
    const off = hiddenSources.has(n);
    return `<div class="srcitem${off ? " off" : ""}"><label><input type="checkbox" data-src="${esc(n)}" ${off ? "" : "checked"}> ${esc(srcName(n))}</label>
      <button type="button" class="link only" data-only="${esc(n)}" title="Show only ${esc(srcName(n))}">only</button>
      <span class="count">${sourceCounts[n] || 0}</span></div>`;
  }).join("");
  $("sources-all").hidden = hiddenSources.size === 0;
}
function sourcesChanged() { clearTimeout(debounce); debounce = setTimeout(load, 150); }
$("sources-filter").addEventListener("change", (e) => {
  const n = e.target.dataset.src; if (!n) return;
  if (e.target.checked) hiddenSources.delete(n); else hiddenSources.add(n);
  renderSourceFilter(); sourcesChanged();
});
$("sources-filter").addEventListener("click", (e) => {
  const n = e.target.dataset.only; if (!n) return;
  const all = new Set([...knownSources, ...Object.keys(sourceCounts), ...hiddenSources]);
  hiddenSources = new Set([...all].filter((x) => x !== n));
  renderSourceFilter(); sourcesChanged();
});
$("sources-all").onclick = () => { hiddenSources.clear(); renderSourceFilter(); sourcesChanged(); };

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
const SRC = { rescuegroups: "RescueGroups", petfinder: "Petfinder", adoptapet: "Adopt-a-Pet", kingcounty: "King County", shelterluv: "Shelterluv rescues", oregonhumane: "Oregon Humane", petango: "Petango shelters", "24petconnect": "24Pet Connect shelters", demo: "Demo" };

function render() {
  $("grid").innerHTML = dogs.map((d, i) => {
    const isNew = lastVisit && d.first_seen > lastVisit;
    const where = [d.distance != null ? `${d.distance} mi` : null, d.city].filter(Boolean).join(" · ");
    return `<div class="card" data-i="${i}">
      ${isNew ? '<span class="badge new">NEW</span>' : ""}
      ${d.photos[0] ? `<img loading="lazy" src="${esc(d.photos[0])}" alt="${esc(d.name)}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'nophoto',textContent:'🐾'}))">` : '<div class="nophoto">🐾</div>'}
      <div class="body"><h3>${esc(d.name)}</h3>
        <div class="meta">${esc(d.breed)}</div>
        <div class="meta">${esc(d.age_text || d.age_group || "Age unknown")}${d.sex ? " · " + esc(d.sex) : ""}</div>
        <div class="meta">${esc(where)}</div>
        <div class="badges">${d.transport ? '<span class="badge tr">Transport</span>' : ""}
          ${d.sources.map((s) => `<span class="badge">${esc(SRC[s.source] || s.source)}</span>`).join("")}
          ${d.age_unknown ? '<span class="badge" title="This source gives no listing date, and the dog was already there when we first checked">date unknown</span>'
            : d.days_listed != null ? `<span class="badge" title="${d.date_known ? "Date the rescue listed this dog" : "This source gives no listing date; this is when we first saw it"}">${d.date_known ? "listed " : "seen "}${d.days_listed < 1 ? "today" : Math.floor(d.days_listed) + "d ago"}</span>` : ""}</div>
      </div></div>`;
  }).join("");
}

$("grid").addEventListener("click", (e) => {
  const c = e.target.closest(".card"); if (!c) return;
  const d = dogs[+c.dataset.i];
  $("detail").innerHTML = `${d.photos.length ? `<div class="gallery">${d.photos.map((p) => `<img src="${esc(p)}" onerror="this.remove()">`).join("")}</div>` : ""}
    <div class="info"><h2>${esc(d.name)}</h2>
    <div class="meta">${[d.breed, d.age_text, d.sex, d.size, d.color].filter(Boolean).map(esc).join(" · ")}</div>
    <div class="meta">${esc(d.org || "")} — ${esc([d.city, d.state].filter(Boolean).join(", "))}${d.distance != null ? ` (${d.distance} mi)` : ""}</div>
    <p>${esc(d.description || "No description.")}</p>
    <div>${d.sources.map((s) => `<div class="srcrow"><a class="badge" target="_blank" rel="noopener" href="${esc(s.url)}">Open on ${esc(SRC[s.source] || s.source)} ↗</a>
      <span class="meta">${esc(s.org || "")}</span>
      ${d.sources.length > 1 ? `<button class="split" data-lid="${s.listing_id}" title="These are different dogs: split this listing off">✂ not the same dog</button>` : ""}</div>`).join("")}</div></div>`;
  $("modal").hidden = false;
});
$("detail").addEventListener("click", async (e) => {
  const b = e.target.closest(".split"); if (!b) return;
  b.disabled = true;
  try { await api("/api/split", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing_id: +b.dataset.lid }) }); $("modal").hidden = true; toast("Split into separate listings"); await load(); }
  catch (err) { toast(err.message); b.disabled = false; }
});
$("close").onclick = () => ($("modal").hidden = true);

// ---- Petfinder crawl panel (PC only) ----
let pfState = null, pfPaces = null;
// rough time for a 41-page search at the chosen pause (page load and drawing add about 5 s a page)
function pfUpdateEta() {
  if (!pfPaces) return;
  const [lo, hi] = pfPaces[$("pf-pace").value] || [6, 11];
  const pages = (pfState && pfState.pages) || 41, perPage = (lo + hi) / 2 + 5;
  $("pf-eta").textContent = `about ${Math.round((pages * perPage) / 60)} min for ${pages} pages`;
}
const pfOpenNow = () => !$("pf-modal").hidden;
function renderPF(st) {
  const wasRunning = !!(pfState && pfState.running);
  pfState = st;
  const running = !!st.running;
  if (wasRunning && !running && pfOpenNow()) loadPF().catch(() => {});   // refresh "last run" and the per-search counts
  $("pf-progress").hidden = st.status === "idle";
  $("pf-host").textContent = st.host ? `Running on your PC (${st.host}). Nothing runs on the device you started it from.` : "";
  $("pf-update").disabled = $("pf-full").disabled = $("pf-save").disabled = running;
  $("pf-stop").disabled = !running;
  const bar = $("pf-bar");
  if (running && !st.pages) { bar.className = "indeterminate"; bar.style.width = ""; }
  else { bar.className = ""; bar.style.width = (st.pages ? Math.min(100, Math.round((st.page / st.pages) * 100)) : (running ? 0 : 100)) + "%"; }
  const where = running && st.url_count > 1 ? `Search ${st.url_index}/${st.url_count} · ` : "";
  $("pf-status").textContent = where + (st.message || "");
  $("pf-status").className = st.status === "error" ? "err" : st.status === "waiting" ? "wait" : "";
  const logEl = $("pf-log"); const atEnd = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 8;
  logEl.textContent = (st.log || []).join("\n"); if (atEnd) logEl.scrollTop = logEl.scrollHeight;
}
async function loadPF() {
  const info = await api("/api/petfinder/crawl");
  if (document.activeElement !== $("pf-urls")) $("pf-urls").value = info.urls.join("\n");
  $("pf-scrubbed").innerHTML = info.urls.map((u) => {
    const n = info.scrubbed[u] || 0;
    return `<div>${n ? `${n} dogs from the last scrub` : "never scrubbed: the first run does every page"} <span class="meta">· ${esc(u.replace(/^https:\/\/www\.petfinder\.com\/search\/dogs-for-adoption\//, "").slice(0, 70))}</span></div>`;
  }).join("");
  $("pf-pace").value = info.pace; pfPaces = info.paces; pfUpdateEta();
  const l = info.last_run;
  $("pf-last").textContent = l ? `Last run ${new Date(l.at * 1000).toLocaleString()}: ${l.outcome}, ${l.dogs} dogs seen, ${l.new} new.` : "";
  if (!info.chrome) { $("pf-status").textContent = "Google Chrome was not found on this PC."; $("pf-progress").hidden = false; }
  renderPF(info.state);
}
$("pf-open").onclick = async () => { $("pf-modal").hidden = false; try { await loadPF(); } catch (e) { toast(e.message); } };
$("pf-close").onclick = () => ($("pf-modal").hidden = true);
$("pf-modal").addEventListener("click", (e) => { if (e.target === $("pf-modal")) $("pf-modal").hidden = true; });
$("pf-save").onclick = async () => {
  try { await api("/api/petfinder/urls", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls: $("pf-urls").value }) }); toast("Saved"); await loadPF(); }
  catch (e) { toast(e.message); }
};
async function pfRun(mode) {
  try {
    await api("/api/petfinder/urls", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls: $("pf-urls").value }) });
    await api("/api/petfinder/crawl", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, pace: $("pf-pace").value }) });
    renderPF({ ...(pfState || {}), running: true, status: "starting", message: "Starting Chrome on your PC...", log: [], page: 0, pages: 0 });
  } catch (e) { toast(e.message); }
}
$("pf-pace").onchange = pfUpdateEta;
$("pf-update").onclick = () => pfRun("auto");
$("pf-full").onclick = () => pfRun("full");
$("pf-stop").onclick = async () => { try { await api("/api/petfinder/crawl/stop", { method: "POST" }); $("pf-status").textContent = "Stopping after this page..."; } catch (e) { toast(e.message); } };
addEventListener("keydown", (e) => { if (e.key === "Escape" && pfOpenNow()) $("pf-modal").hidden = true; });
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) $("modal").hidden = true; });
addEventListener("keydown", (e) => { if (e.key === "Escape") $("modal").hidden = true; });

function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4000); }

// ---- filters ----
document.querySelectorAll("#filters input, #filters select").forEach((el) => {
  if (el.id === "saved") return;
  el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" || el.type === "range" ? "change" : "input", () => {
    if (el.id === "radius") $("radius-val").textContent = el.value;
    if (el.id === "statewide") $("radius").disabled = el.checked;
    if (el.id === "max_days") $("undated-wrap").hidden = !el.value;
    if (el.id === "sort") $("undated-last-wrap").hidden = el.value !== "newest";
    clearTimeout(debounce); debounce = setTimeout(load, 350);
  });
});
$("radius").addEventListener("input", () => ($("radius-val").textContent = $("radius").value));
$("reset").onclick = () => { $("saved").value = ""; setParams(); load(); };
// Filters sheet (phones): opens over the current scroll position, closes with Done, a tap outside, Esc, or the back button.
let sheetOpen = false;
function openSheet() {
  if (sheetOpen) return;
  sheetOpen = true;
  $("filters").classList.add("open"); $("filters-backdrop").hidden = false; document.body.classList.add("sheet-open");
  $("filters").scrollTop = 0;
  try { history.pushState({ sheet: 1 }, ""); } catch {}
}
function hideSheet() {
  sheetOpen = false;
  $("filters").classList.remove("open"); $("filters-backdrop").hidden = true; document.body.classList.remove("sheet-open");
}
function closeSheet() {
  if (!sheetOpen) return;
  if (history.state && history.state.sheet) history.back(); else hideSheet();  // popstate does the hiding
}
addEventListener("popstate", () => { if (sheetOpen) hideSheet(); });
$("toggle-filters").onclick = () => (sheetOpen ? closeSheet() : openSheet());
$("filters-done").onclick = closeSheet;
$("filters-backdrop").onclick = closeSheet;
addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
addEventListener("resize", () => { if (sheetOpen && innerWidth > 800) hideSheet(); });

// ---- saved searches ----
async function loadSearches(selectId) {
  searches = await api("/api/searches");
  $("saved").innerHTML = '<option value="">— none selected —</option>' +
    searches.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  if (selectId) $("saved").value = selectId;
}
$("saved").onchange = () => {
  const s = searches.find((x) => x.id == $("saved").value);
  setParams(s ? s.params : {}); load();
};
$("save-new").onclick = async () => {
  const name = prompt("Name this search:"); if (!name) return;
  const r = await api("/api/searches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, params: getParams() }) });
  await loadSearches(r.id); toast("Saved");
};
$("save-update").onclick = async () => {
  const id = $("saved").value; if (!id) return toast("Select a saved search first");
  const s = searches.find((x) => x.id == id);
  await api("/api/searches/" + id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: s.name, params: getParams() }) });
  await loadSearches(id); toast("Updated");
};
$("save-delete").onclick = async () => {
  const id = $("saved").value; if (!id || !confirm("Delete this saved search?")) return;
  await api("/api/searches/" + id, { method: "DELETE" }); await loadSearches(); setParams(); load();
};

// ---- the removed-dog check ----
const clOpenNow = () => !$("cl-modal").hidden;
function renderCL(st) {
  const running = !!st.running;
  $("cl-run").disabled = running;
  $("cl-progress").hidden = st.status === "idle";
  $("cl-bar").className = running ? "indeterminate" : ""; $("cl-bar").style.width = running ? "" : "100%";
  $("cl-status").textContent = st.message || ""; $("cl-status").className = st.status === "error" ? "err" : "";
  renderCLSources(st.sources || {});
  $("cl-open").textContent = running ? "Checking…" : "Check listings";
}
function renderCLSources(src) {
  const rows = Object.entries(src);
  $("cl-sources").innerHTML = rows.map(([n, i]) => {
    const bad = i.status && i.status !== "ok";
    const what = bad ? i.status : `${i.removed} removed`;
    return `<div class="row2"><span>${esc(SRC[n] || n)}<span class="meta"> · ${i.before} held</span>${i.note ? `<span class="note ${bad ? "warn" : "meta"}">${esc(i.note)}</span>` : ""}</span><span class="${bad ? "warn" : ""}">${esc(what)}</span></div>`;
  }).join("");
}
async function loadCL() {
  const info = await api("/api/cleanup");
  $("cl-next").textContent = `Runs automatically ${info.schedule.charAt(0).toLowerCase() + info.schedule.slice(1)}` + (info.next_run_text ? `. Next: ${info.next_run_text}.` : ".");
  const l = info.last;
  $("cl-last").textContent = l ? `Last check ${new Date(l.at * 1000).toLocaleString()} (${l.trigger === "scheduled" ? "automatic" : "manual"}): ${l.removed} removed.` : "No check has run yet.";
  if (info.state.status === "idle" && l) renderCLSources(l.sources); else renderCL(info.state);
}
$("cl-open").onclick = async () => { $("cl-modal").hidden = false; try { await loadCL(); } catch (e) { toast(e.message); } };
$("cl-close").onclick = () => ($("cl-modal").hidden = true);
$("cl-modal").addEventListener("click", (e) => { if (e.target === $("cl-modal")) $("cl-modal").hidden = true; });
addEventListener("keydown", (e) => { if (e.key === "Escape" && clOpenNow()) $("cl-modal").hidden = true; });
$("cl-run").onclick = async () => {
  try { await api("/api/cleanup", { method: "POST" }); renderCL({ running: true, status: "starting", message: "Starting...", sources: {} }); }
  catch (e) { toast(e.message); }
};

// ---- refresh, status, live events ----
$("refresh").onclick = async () => { setBusy(true); await api("/api/refresh", { method: "POST" }); };
function setBusy(b) {
  $("refresh").disabled = b;
  $("refresh").innerHTML = b ? '<span class="spin">⟳</span> Refreshing…' : "⟳ Refresh";
}
async function loadSources() {
  const s = await api("/api/sources");
  knownSources = s.map((x) => x.source);
  renderSourceFilter();
  $("sources").innerHTML = s.map((x) => `<span title="${esc(x.last_error || "")}"><span class="dot ${x.state}"></span>${esc(SRC[x.source] || x.source)}</span>`).join("");
  const ok = s.map((x) => x.last_ok).filter(Boolean);
  if (ok.length) $("updated").textContent = "Updated " + new Date(Math.max(...ok) * 1000).toLocaleTimeString();
}
function connectEvents() {
  const es = new EventSource("/api/events" + (TOKEN ? "?token=" + encodeURIComponent(TOKEN) : ""));
  es.onmessage = async (m) => {
    const { event, data } = JSON.parse(m.data);
    if (event === "petfinder_progress") { renderPF(data); return; }
    if (event === "cleanup_progress") { renderCL(data); if (!data.running && clOpenNow()) loadCL().catch(() => {}); return; }
    if (event === "poll_started") setBusy(true);
    if (event === "source_done") {  // sources finish at different times; show results as they arrive
      clearTimeout(refreshTimer); refreshTimer = setTimeout(() => { load(); loadSources(); }, 400);
      if (data.new) toast(`${data.new} new from ${SRC[data.source] || data.source}`);
    }
    if (event === "poll_done") {
      setBusy(false); await Promise.all([load(), loadSources()]);
    }
  };
}

$("interval").onchange = async () => {
  try { await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ poll_minutes: $("interval").value }) }); toast("Auto-refresh every " + $("interval").selectedOptions[0].text); }
  catch (e) { toast(e.message); }
};

// Keep the filter panel directly under the header, whatever height the header has (it wraps on narrow windows)
function fitHeader() {
  const h = document.querySelector("header");
  if (h) document.documentElement.style.setProperty("--header-h", h.offsetHeight + 8 + "px");
}
addEventListener("resize", fitHeader);
fitHeader();

(async function init() {
  cfg = await api("/api/config");
  if (![...$("interval").options].some((o) => o.value == cfg.poll_minutes)) $("interval").add(new Option(cfg.poll_minutes + " min", cfg.poll_minutes));
  $("interval").value = cfg.poll_minutes;
  if (!cfg.has_rescuegroups_key && !cfg.demo) {
    $("banner").hidden = false;
    $("banner").textContent = "No RescueGroups API key set. Add RESCUEGROUPS_API_KEY to .env (or set DEMO=1 to try sample data), then restart.";
  }
  setParams(); await loadSearches(); await Promise.all([load(), loadSources()]); connectEvents(); fitHeader();
})();

// ---- breed box: suggestions from the dogs currently listed; a leading "-" excludes ----
(function () {
  const input = $("breed"), list = $("breed-list");
  let shown = [], sel = -1;
  const parts = () => input.value.split(",");
  const tok = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const norm = (s) => tok(s.replace(/^\s*-/, ""));
  function current() {                       // the term being typed: after the last comma
    const ps = parts(), raw = ps[ps.length - 1].trimStart();
    return { neg: raw.startsWith("-"), text: norm(raw) };
  }
  function show() {
    const cur = current();
    const used = new Set(parts().slice(0, -1).map(norm).filter(Boolean));
    shown = breedOptions.filter((b) => !used.has(tok(b.name)) && tok(b.name).includes(cur.text)).slice(0, 40);
    sel = -1;
    list.innerHTML = shown.map((b, i) => `<div class="opt" data-i="${i}"><span>${esc(b.name)} <span class="n">${b.count}</span></span><button type="button" class="ex" data-ex="${i}" title="Exclude ${esc(b.name)}">exclude</button></div>`).join("");
    list.hidden = !shown.length;
  }
  function pick(i, exclude) {
    const ps = parts(), neg = exclude || current().neg;
    ps[ps.length - 1] = (ps.length > 1 ? " " : "") + (neg ? "-" : "") + tok(shown[i].name);
    input.value = ps.join(",") + ", ";
    list.hidden = true;
    clearTimeout(debounce); debounce = setTimeout(load, 150);
    input.focus(); setTimeout(show, 400);
  }
  input.addEventListener("input", show);
  input.addEventListener("focus", show);
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
      [...list.children].forEach((el, i) => el.classList.toggle("sel", i === sel)); list.children[sel].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); pick(sel); }
    else if (e.key === "Escape") { list.hidden = true; e.stopPropagation(); }
  });
  list.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const ex = e.target.closest("[data-ex]"), opt = e.target.closest(".opt");
    if (ex) pick(+ex.dataset.ex, true); else if (opt) pick(+opt.dataset.i);
  });
})();
