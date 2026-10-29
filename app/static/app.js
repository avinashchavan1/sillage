"use strict";

/* ================================================================ helpers */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const SENTIMENTS = [
  { v: 2, label: "Love" }, { v: 1, label: "Like" }, { v: 0, label: "Neutral" },
  { v: -1, label: "Dislike" }, { v: -2, label: "Hate" },
];
const SENT_LABEL = Object.fromEntries(SENTIMENTS.map((s) => [s.v, s.label]));
const STATUSES = { owned: "Owned", sample: "Sample", decant: "Decant", wishlist: "Wishlist", tried: "Tried", sold: "Sold" };
const OCCASIONS = ["Work", "Casual", "Date", "Night out", "Special event", "Gym / sport", "Travel", "Home", "Sleep"];
const WEATHER = ["Hot", "Warm", "Mild", "Cool", "Cold", "Rainy", "Humid"];

// Fragrantica-style accord colours (identity colours; every bar also carries its text label)
const ACCORD_COLORS = {
  woody: "#8a5a2b", citrus: "#d9b92e", "fresh spicy": "#7aa83b", "warm spicy": "#c0532b", "soft spicy": "#d58b5b",
  spicy: "#c0532b", aromatic: "#4f9b8f", amber: "#c27c2c", sweet: "#e07aa0", vanilla: "#dcc193", floral: "#e889b8",
  "white floral": "#e6dccb", "yellow floral": "#e9c64a", rose: "#d9466f", fruity: "#e36d3d", powdery: "#cdb4cf",
  musky: "#bcb1a6", aquatic: "#3aa0c8", marine: "#2f86b3", fresh: "#7fc4d6", ozonic: "#94c3df", green: "#3f9a4a",
  earthy: "#6d5a3f", leather: "#5c3a2a", smoky: "#767676", tobacco: "#8c5e34", oud: "#4a2e25", balsamic: "#9b5b3a",
  lavender: "#9a7fd1", iris: "#8f86c9", violet: "#7a5cb8", patchouli: "#6b5a3c", vetiver: "#6f7d3c", mossy: "#5f7a3f",
  honey: "#e0a52b", coffee: "#5b3b25", cacao: "#5e3a2b", chocolate: "#5e3a2b", lactonic: "#e5d9bd", coconut: "#e0d6c0",
  caramel: "#c47f2f", animalic: "#7d5a4a", herbal: "#6e9b5a", salty: "#9bb9c6", tropical: "#f2a541", tuberose: "#e8dccb",
  almond: "#d6b98a", rum: "#9b4f2a", cinnamon: "#b0552f", metallic: "#9aa1a6", mineral: "#929b9d", camphor: "#9fcdbf",
  anis: "#bccb86", nutty: "#b98b5a", beeswax: "#d8a84b", cherry: "#b21f3a", sour: "#b9c852", wine: "#7b2340",
  whiskey: "#a0602a", aldehydic: "#b8c7d3", gourmand: "#b8763a", resinous: "#9b5b3a", conifer: "#2f6b4a",
  terpenic: "#5e8f4e", bitter: "#7e8b4a", savory: "#a0724a", "fresh floral": "#f0a0c0",
};
function accordColor(name) {
  const key = String(name || "").toLowerCase();
  if (ACCORD_COLORS[key]) return ACCORD_COLORS[key];
  let h = 0;
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 52%)`;
}

const todayISO = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
const daysSince = (iso) => (iso ? Math.floor((Date.parse(todayISO()) - Date.parse(iso)) / 86400000) : null);
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
function relDays(iso) {
  const n = daysSince(iso);
  if (n === null) return "never worn";
  if (n <= 0) return "today";
  if (n === 1) return "yesterday";
  if (n < 30) return `${n} days ago`;
  if (n < 365) return `${Math.round(n / 30)} mo ago`;
  return `${Math.round(n / 365)} yr ago`;
}
const fullName = (f) => [f.brand, f.name].filter(Boolean).join(" – ");
const listText = (arr) => (arr || []).join(", ");
const parseList = (s) => String(s || "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
const signed = (n) => (n > 0 ? "+" : "") + Number(n).toFixed(2);

let API_KEY = null;
async function ensureKey() {
  if (API_KEY) return API_KEY;
  API_KEY = store.get("sillage-key");
  if (!API_KEY) {
    const boot = await fetch("/bootstrap").then((r) => r.json()).catch(() => ({}));
    if (boot.api_key) { API_KEY = boot.api_key; store.set("sillage-key", API_KEY); }
  }
  return API_KEY;
}
const authHeaders = () => (API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {});

async function api(path, opts = {}) {
  await ensureKey();
  const init = { ...opts, headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) } };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const res = await fetch(path, init);
  if (res.status === 401) { showLogin(); throw Object.assign(new Error("Enter your Sillage key"), { status: 401 }); }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const det = data.detail;
    let msg = typeof det === "string" ? det : det?.message || res.statusText;
    if (Array.isArray(det)) msg = det.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
    const err = new Error(msg);
    err.status = res.status;
    err.detail = det;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, { error = false, action, onAction, timeout = 5000 } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " err" : "");
  el.innerHTML = `<span>${esc(message)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action;
    b.onclick = () => { onAction?.(); el.remove(); };
    el.append(b);
  }
  $("#toasts").append(el);
  setTimeout(() => el.remove(), timeout);
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

/* ================================================================ state */

const state = {
  status: null,
  fragrances: [],
  filters: { q: "", status: "all", sentiment: "any", sort: store.get("sort") || "recent" },
  lookup: null,       // current preview in Add view
  insights: null,
};

async function loadFragrances() {
  state.fragrances = await api("/api/fragrances");
  return state.fragrances;
}

/* ================================================================ shared bits */

// Generated bottle artwork: every fragrance gets a picture even without a photo. Deterministic per fragrance —
// the brand picks the silhouette and cap, the top accords colour the juice, the label carries the initials.
function hashStr(s) { let h = 2166136261; for (const c of String(s || "")) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
const BOTTLES = [
  { body: "M30 58 Q30 50 38 50 H82 Q90 50 90 58 V140 Q90 148 82 148 H38 Q30 148 30 140 Z", neck: [52, 40, 16, 12], label: [40, 92, 40, 30] },   // classic square
  { body: "M42 54 Q42 48 48 48 H72 Q78 48 78 54 V142 Q78 148 72 148 H48 Q42 148 42 142 Z", neck: [53, 36, 14, 14], label: [46, 96, 28, 34] },   // tall slim
  { body: "M60 52 C88 52 96 78 96 100 C96 128 80 148 60 148 C40 148 24 128 24 100 C24 78 32 52 60 52 Z", neck: [52, 40, 16, 14], label: [42, 92, 36, 28] }, // round flacon
  { body: "M46 50 H74 L92 70 V128 L74 148 H46 L28 128 V70 Z", neck: [52, 38, 16, 14], label: [41, 90, 38, 30] },                                // faceted
  { body: "M34 62 Q34 50 46 50 H74 Q86 50 86 62 L92 136 Q93 148 80 148 H40 Q27 148 28 136 Z", neck: [51, 38, 18, 14], label: [41, 94, 38, 30] }, // tapered
];
const CAPS = [
  (x, w, fill) => `<rect x="${x - 4}" y="14" width="${w + 8}" height="26" rx="3" fill="${fill}"/>`,
  (x, w, fill) => `<circle cx="${x + w / 2}" cy="26" r="15" fill="${fill}"/>`,
  (x, w, fill) => `<rect x="${x - 10}" y="22" width="${w + 20}" height="18" rx="2" fill="${fill}"/>`,
  (x, w, fill) => `<path d="M${x - 2} 40 L${x + w / 2} 8 L${x + w + 2} 40 Z" fill="${fill}"/>`,
];
const CAP_TONES = [["#2b2622", "#4a423b"], ["#c9a24a", "#f1d68c"], ["#d8d6d2", "#ffffff"], ["#1b1b1f", "#3b3b44"], ["#7a4a2a", "#a8724a"]];
function bottleArt(f) {
  const brandH = hashStr(f.brand || f.name), nameH = hashStr(f.name);
  const shape = BOTTLES[brandH % BOTTLES.length], cap = CAPS[(brandH >>> 3) % CAPS.length], tone = CAP_TONES[(brandH >>> 6) % CAP_TONES.length];
  const acc = (f.accords || []).slice(0, 3).map((a) => accordColor(a.name));
  const c1 = acc[0] || accordColor(f.family || f.name), c2 = acc[1] || c1, c3 = acc[2] || c2;
  const fill = 0.55 + ((nameH % 30) / 100); // how full the bottle is
  const top = 50 + (148 - 50) * (1 - fill);
  const initials = (f.name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const [nx, ny, nw, nh] = shape.neck, [lx, ly, lw, lh] = shape.label;
  const id = "b" + nameH.toString(36);
  return `<svg class="bottle-art" viewBox="0 0 120 160" role="img" aria-label="${esc(fullName(f))} bottle artwork">
    <defs>
      <linearGradient id="${id}j" x1="0" y1="0" x2="0.3" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="0.55" stop-color="${c2}"/><stop offset="1" stop-color="${c3}"/></linearGradient>
      <linearGradient id="${id}k" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${tone[1]}"/><stop offset="1" stop-color="${tone[0]}"/></linearGradient>
      <clipPath id="${id}c"><path d="${shape.body}"/></clipPath>
    </defs>
    <ellipse cx="60" cy="152" rx="36" ry="4" fill="rgb(0 0 0 / 14%)"/>
    <rect x="${nx}" y="${ny}" width="${nw}" height="${nh}" rx="2" fill="rgb(255 255 255 / 55%)" stroke="rgb(0 0 0 / 12%)"/>
    ${cap(nx, nw, `url(#${id}k)`)}
    <path d="${shape.body}" fill="rgb(255 255 255 / 38%)" stroke="rgb(0 0 0 / 14%)" stroke-width="1"/>
    <g clip-path="url(#${id}c)">
      <rect x="0" y="${top.toFixed(1)}" width="120" height="160" fill="url(#${id}j)" opacity="0.9"/>
      <rect x="0" y="${top.toFixed(1)}" width="120" height="2" fill="rgb(255 255 255 / 45%)"/>
      <rect x="34" y="44" width="9" height="110" rx="4" fill="rgb(255 255 255 / 30%)" transform="rotate(4 38 100)"/>
    </g>
    <rect x="${lx}" y="${ly}" width="${lw}" height="${lh}" rx="2" fill="rgb(255 255 255 / 88%)" stroke="rgb(0 0 0 / 10%)"/>
    <text x="${lx + lw / 2}" y="${ly + lh / 2 + 5}" text-anchor="middle" font-family="Bodoni Moda, Didot, Georgia, serif" font-size="${initials.length > 1 ? 14 : 16}" font-weight="700" fill="#1d1a16">${esc(initials)}</text>
  </svg>`;
}

function artTile(f) {
  const c1 = accordColor(f.accords?.[0]?.name || f.family || f.name);
  return `<div class="art-tile" style="background:radial-gradient(circle at 50% 38%, color-mix(in srgb, ${c1} 26%, var(--surface)) 0%, var(--surface-2) 78%)">${bottleArt(f)}</div>`;
}
function thumb(f) {
  return f.image_url
    ? `<img src="${esc(f.image_url)}" alt="${esc(fullName(f))}" loading="lazy" data-fallback="1" data-fid="${esc(f.id ?? "")}">`
    : artTile(f);
}
// A photo that can't load (blocked by the image host, offline, deleted) falls back to the bottle artwork
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName === "IMG" && img.dataset.fallback) {
    const f = state.fragrances.find((x) => String(x.id) === img.dataset.fid) || state.lookup?.details || { name: img.alt };
    const wrap = document.createElement("div");
    wrap.style.cssText = "width:100%;height:100%";
    wrap.innerHTML = artTile({ ...f, image_url: null });
    img.replaceWith(wrap);
  }
}, true);

async function photoDataUrl(file) {
  if (!file || !/^image\//.test(file.type)) throw new Error("Choose an image file");
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, 1100 / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale); canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.86);
}

function sentimentPill(s) {
  if (s === null || s === undefined) return `<span class="pill">Unrated</span>`;
  return `<span class="pill s${s}">${s === 2 ? "♥ " : ""}${SENT_LABEL[s]}</span>`;
}
function accordStrip(accords) {
  const top = (accords || []).slice(0, 5);
  if (!top.length) return "";
  return `<div class="accord-strip" aria-hidden="true">${top
    .map((a) => `<span style="flex:${Math.max(0.15, a.strength)};background:${accordColor(a.name)}"></span>`)
    .join("")}</div>`;
}
function accordBars(accords) {
  if (!accords?.length) return `<p class="subtle small">No accords recorded.</p>`;
  return `<div class="accords">${accords
    .map((a) => `<div class="accord"><div class="fill" style="width:${Math.round(a.strength * 100)}%;background:${accordColor(a.name)}"></div>
      <span class="lbl">${esc(a.name)}</span></div>`)
    .join("")}</div>`;
}
function noteChips(notes, affinity) {
  if (!notes?.length) return "";
  return `<div class="chips">${notes
    .map((n) => {
      const a = affinity?.(n);
      const cls = a === undefined || a === null ? "" : a >= 0.35 ? "pos" : a <= -0.35 ? "neg" : "";
      const title = cls ? ` title="Your affinity: ${signed(a)}"` : "";
      return `<span class="chip ${cls}"${title}>${esc(n)}</span>`;
    })
    .join("")}</div>`;
}
function pyramid(d, affinity) {
  const layers = [["Top notes", d.top_notes], ["Heart notes", d.middle_notes], ["Base notes", d.base_notes], ["Notes", d.notes]];
  const html = layers.filter(([, n]) => n?.length).map(([t, n]) => `<div><h4>${t}</h4>${noteChips(n, affinity)}</div>`).join("");
  return html ? `<div class="pyramid">${html}</div>` : `<p class="subtle small">No notes recorded.</p>`;
}
function facts(d) {
  const bits = [
    d.year && ["Launched", d.year], d.concentration && ["Type", d.concentration], d.gender && ["For", d.gender],
    d.family && ["Family", d.family], d.perfumers?.length && ["Nose", d.perfumers.join(", ")],
    d.community_rating && ["Community", `${d.community_rating.toFixed(2)}/5${d.rating_votes ? ` · ${d.rating_votes.toLocaleString()} votes` : ""}`],
    d.longevity && ["Longevity", d.longevity], d.sillage && ["Sillage", d.sillage],
    d.seasons?.length && ["Best for", d.seasons.join(", ")],
  ].filter(Boolean);
  return `<div class="facts">${bits.map(([k, v]) => `<span>${k}: <b>${esc(v)}</b></span>`).join("")}</div>`;
}
function predictionBox(p, title = "Will you like it?") {
  if (!p) return "";
  const reasons = [
    p.pros.length && `<div><span class="small subtle">You tend to like</span> ${noteChips(p.pros.map((x) => x.label), () => 1)}</div>`,
    p.cons.length && `<div><span class="small subtle">You tend to dislike</span> ${noteChips(p.cons.map((x) => x.label), () => -1)}</div>`,
  ].filter(Boolean).join("");
  return `<div class="predict">
    <div class="small subtle">${esc(title)}</div>
    <div class="row"><span class="big">${p.match}%</span><span>${esc(p.verdict)}</span></div>
    ${reasons}
  </div>`;
}
function sentimentRadios(name, value, { allowNone = true } = {}) {
  const opts = SENTIMENTS.map((s) => `<label><input type="radio" name="${name}" value="${s.v}" ${value === s.v ? "checked" : ""}>${s.v === 2 ? "♥ " : ""}${s.label}</label>`);
  if (allowNone) opts.push(`<label><input type="radio" name="${name}" value="" ${value === null || value === undefined ? "checked" : ""}>Not rated</label>`);
  return `<div class="seg" role="radiogroup">${opts.join("")}</div>`;
}
function statusSelect(name, value) {
  return `<select name="${name}">${Object.entries(STATUSES).map(([k, v]) => `<option value="${k}" ${k === value ? "selected" : ""}>${v}</option>`).join("")}</select>`;
}
function readSentiment(form, name) {
  const v = form.querySelector(`input[name="${name}"]:checked`)?.value;
  return v === undefined || v === "" ? null : Number(v);
}

/* ================================================================ router */

const views = { collection: renderCollection, add: renderAdd, wear: renderWear, insights: renderInsights, ai: renderAI, connect: renderConnect };
function route() {
  const name = (location.hash.slice(1).split("?")[0] || "collection");
  const view = views[name] ? name : "collection";
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  views[view]();
}
window.addEventListener("hashchange", route);

/* ================================================================ collection */

function filtered() {
  const { q, status, sentiment, sort } = state.filters;
  const needle = q.trim().toLowerCase();
  let items = state.fragrances.filter((f) => {
    if (status !== "all" && (status === "samples" ? !["sample", "decant"].includes(f.status) : f.status !== status)) return false;
    if (sentiment === "unrated" ? f.sentiment !== null : sentiment !== "any" && f.sentiment !== Number(sentiment)) return false;
    if (!needle) return true;
    const hay = [f.name, f.brand, f.family, ...(f.perfumers || []), ...(f.accords || []).map((a) => a.name),
      ...f.top_notes, ...f.middle_notes, ...f.base_notes, ...f.notes].join(" ").toLowerCase();
    return hay.includes(needle);
  });
  const by = {
    recent: () => 0,
    name: (a, b) => a.name.localeCompare(b.name),
    brand: (a, b) => (a.brand || "").localeCompare(b.brand || "") || a.name.localeCompare(b.name),
    worn: (a, b) => b.wear_count - a.wear_count,
    lastworn: (a, b) => (b.last_worn || "").localeCompare(a.last_worn || ""),
    rating: (a, b) => (b.score ?? -9) - (a.score ?? -9),
    match: (a, b) => (b.prediction?.match ?? -1) - (a.prediction?.match ?? -1),
  }[sort];
  return [...items].sort(by);
}

async function renderCollection() {
  const root = $("#view-collection");
  if (!root.dataset.ready) {
    root.innerHTML = `
      <div class="view-head">
        <div><h1>Your collection</h1><div class="subtle" id="coll-count"></div></div>
        <a class="btn primary" href="#add">+ Add fragrance</a>
      </div>
      <div class="filters">
        <input type="search" id="f-q" placeholder="Search name, brand, note, accord, perfumer…" aria-label="Search">
        <select id="f-status" aria-label="Status">
          <option value="all">All statuses</option><option value="owned">Owned</option><option value="samples">Samples & decants</option>
          <option value="wishlist">Wishlist</option><option value="tried">Tried</option><option value="sold">Sold</option>
        </select>
        <select id="f-sent" aria-label="Rating">
          <option value="any">Any rating</option>${SENTIMENTS.map((s) => `<option value="${s.v}">${s.label}</option>`).join("")}<option value="unrated">Unrated</option>
        </select>
        <select id="f-sort" aria-label="Sort">
          <option value="recent">Recently added</option><option value="name">Name</option><option value="brand">Brand</option>
          <option value="worn">Most worn</option><option value="lastworn">Recently worn</option><option value="rating">Your score</option>
          <option value="match">Predicted match</option>
        </select>
      </div>
      <div id="coll-grid"></div>`;
    root.dataset.ready = "1";
    $("#f-q").addEventListener("input", (e) => { state.filters.q = e.target.value; drawGrid(); });
    $("#f-status").addEventListener("change", (e) => { state.filters.status = e.target.value; drawGrid(); });
    $("#f-sent").addEventListener("change", (e) => { state.filters.sentiment = e.target.value; drawGrid(); });
    $("#f-sort").addEventListener("change", (e) => { state.filters.sort = e.target.value; store.set("sort", e.target.value); drawGrid(); });
    $("#coll-grid").addEventListener("click", onGridClick);
  }
  $("#f-sort").value = state.filters.sort;
  await loadFragrances();
  drawGrid();
}

function drawGrid() {
  const items = filtered();
  const all = state.fragrances;
  const wears = all.reduce((n, f) => n + f.wear_count, 0);
  $("#coll-count").textContent = all.length
    ? `${all.length} fragrance${all.length === 1 ? "" : "s"} · ${wears} wear${wears === 1 ? "" : "s"} logged`
    : "";
  const grid = $("#coll-grid");
  if (!all.length) {
    grid.innerHTML = `<div class="empty card"><h2>Start your collection</h2>
      <p>Type any fragrance name and Sillage fetches its notes and accords from the web.</p>
      <a class="btn primary" href="#add">Add your first fragrance</a></div>`;
    return;
  }
  if (!items.length) { grid.innerHTML = `<div class="empty">Nothing matches these filters.</div>`; return; }
  grid.innerHTML = `<div class="grid">${items.map(card).join("")}</div>`;
}

function card(f) {
  const match = f.prediction ? `<span class="pill match right" title="${esc(f.prediction.verdict)}">${f.prediction.match}% match</span>` : "";
  const meta = [f.year, f.concentration, f.family].filter(Boolean).join(" · ");
  const worn = f.status === "wishlist" ? "" : `<span class="small subtle">${f.wear_count ? `${f.wear_count}× · ${relDays(f.last_worn)}` : "never worn"}</span>`;
  const wearBtn = ["owned", "sample", "decant"].includes(f.status)
    ? `<button class="small" data-act="wear" data-id="${f.id}" title="Log a wear for today">Wore it today</button>` : "";
  return `<article class="frag" data-id="${f.id}" data-name="${esc(f.name)}" tabindex="0" role="button" aria-label="${esc(fullName(f))}">
    <div class="thumb">${thumb(f)}<span class="pill">${esc(STATUSES[f.status] || f.status)}</span>${match}</div>
    <div class="body">
      <div class="subtle small">${esc(f.brand)}</div>
      <div class="name">${esc(f.name)}</div>
      <div class="meta">${esc(meta)}</div>
      ${accordStrip(f.accords)}
      <div class="foot">${sentimentPill(f.sentiment)}${worn}</div>
      ${wearBtn ? `<div>${wearBtn}</div>` : ""}
    </div>
  </article>`;
}

async function onGridClick(e) {
  const btn = e.target.closest("button[data-act]");
  if (btn) {
    e.stopPropagation();
    if (btn.dataset.act === "wear") quickWear(Number(btn.dataset.id));
    return;
  }
  const el = e.target.closest(".frag");
  if (el) openDetail(Number(el.dataset.id));
}
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.classList?.contains("frag")) {
    e.preventDefault();
    openDetail(Number(e.target.dataset.id));
  }
});

async function quickWear(id) {
  const f = state.fragrances.find((x) => x.id === id);
  try {
    const w = await api("/api/wears", { method: "POST", body: { fragrance_id: id, worn_on: todayISO() } });
    toast(`Logged ${f ? f.name : "wear"} for today`, {
      action: "Undo",
      onAction: async () => { await api(`/api/wears/${w.id}`, { method: "DELETE" }); refreshCurrent(); },
    });
    refreshCurrent();
  } catch (err) { toast(err.message, { error: true }); }
}

function refreshCurrent() {
  const name = location.hash.slice(1) || "collection";
  if (name === "collection") renderCollection();
  else if (name === "insights") renderInsights();
  else if (name === "wear") renderWear();
}

/* ================================================================ detail dialog */

async function openDetail(id, { edit = false } = {}) {
  const dlg = $("#detail");
  const body = $("#detail-body");
  body.innerHTML = `<div class="detail"><div class="loading"><span class="spinner"></span>Loading…</div></div>`;
  if (!dlg.open) dlg.showModal();
  try {
    state.insights = null;
    const f = await api(`/api/fragrances/${id}`);
    body.innerHTML = edit ? detailEdit(f) : detailView(f);
    wireDetail(f);
  } catch (err) {
    body.innerHTML = `<div class="detail"><div class="notice err">${esc(err.message)}</div><div class="row end"><button data-close>Close</button></div></div>`;
    $("[data-close]", body).onclick = () => dlg.close();
  }
}
$("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") e.target.close(); });
$("#detail").addEventListener("close", () => { if (location.hash.slice(1) !== "add") refreshCurrent(); });

function detailView(f) {
  const showPredict = f.prediction && (f.status === "wishlist" || f.sentiment === null);
  const src = f.source_url ? `<a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">${f.source === "fragrantica" ? "View on Fragrantica" : "Source"} ↗</a>` : "";
  return `<div class="detail" data-name="${esc(f.name)}">
    <div class="detail-hero">
      <div class="hero-art"><div class="img">${thumb(f)}</div>
        <div class="photo-actions"><label class="btn small" for="d-photo">${f.image_url?.startsWith("/media/") ? "Replace photo" : "Add your photo"}</label><input id="d-photo" type="file" accept="image/*" hidden>${f.image_url?.startsWith("/media/") ? `<button class="small ghost" data-act="rm-photo">Remove</button>` : ""}</div></div>
      <div>
        <div class="subtle">${esc(f.brand)}</div>
        <h1>${esc(f.name)}</h1>
        ${facts(f)}
        <div class="small subtle" style="margin-top:6px">${[src, f.source === "manual" ? "entered manually" : f.source ? `data via ${esc(f.source)}` : ""].filter(Boolean).join(" · ")}</div>
      </div>
      <button class="ghost close-x" data-close aria-label="Close">✕</button>
    </div>

    <form class="card" id="personal" onsubmit="return false">
      <div class="form-grid">
        <label class="field">Status ${statusSelect("status", f.status)}</label>
        <label class="field">Bottle size (ml) <input name="bottle_ml" type="number" min="0" step="any" value="${f.bottle_ml ?? ""}"></label>
        <div class="field wide">How do you feel about it? ${sentimentRadios("sentiment", f.sentiment)}</div>
        <label class="field wide">Your notes <textarea name="personal_notes" placeholder="Performance on your skin, memories, batch code…">${esc(f.personal_notes)}</textarea></label>
      </div>
      <div class="row end small subtle" id="save-state" style="margin-top:6px">Changes save automatically</div>
    </form>

    ${showPredict ? predictionBox(f.prediction) : ""}

    <div class="two-col">
      <div class="card"><h3>Main accords</h3>${accordBars(f.accords)}</div>
      <div class="card"><h3>Notes</h3>${pyramid(f, (n) => f.note_affinity?.[n])}
        <p class="small subtle" style="margin:10px 0 0">Blue/red outlines mark notes you tend to love/avoid.</p></div>
    </div>
    ${f.description ? `<p class="subtle small" style="margin:0">${esc(f.description)}</p>` : ""}
    ${f.similar?.length ? `<div class="card"><h3>Similar in your collection</h3><ul class="list">${f.similar.map((x) => `<li><span class="link" data-open-similar="${x.id}">${esc(x.name)} <span class="subtle small">${esc(x.brand || "")}</span></span><span class="num" title="Shares: ${esc(x.shared.join(", "))}">${Math.round(x.similarity * 100)}% alike</span></li>`).join("")}</ul></div>` : ""}

    <div class="card">
      <div class="card-head"><h3>Wear history</h3><span class="subtle small">${f.wear_count} wear${f.wear_count === 1 ? "" : "s"}</span></div>
      ${wearForm(f.id)}
      <ul class="wear-list" style="margin-top:12px">${(f.wears || []).map((w) => wearItem(w, false)).join("") || `<li class="subtle">No wears yet.</li>`}</ul>
    </div>

    <div class="row" style="justify-content:space-between">
      <div class="row">
        <button data-act="edit">Edit details</button>
        <button data-act="refresh" title="Fetch notes and accords again from the web">Refresh from web</button>
      </div>
      <button class="danger" data-act="delete">Delete</button>
    </div>
  </div>`;
}

function wearItem(w, showName = true) {
  const bits = [w.occasion, w.weather, w.sprays ? `${w.sprays} sprays` : null,
    w.compliments ? `${w.compliments} compliment${w.compliments === 1 ? "" : "s"}` : null,
    w.enjoyment !== null && w.enjoyment !== undefined ? SENT_LABEL[w.enjoyment] : null].filter(Boolean);
  return `<li><span class="when">${fmtDate(w.worn_on)}</span>
    <span class="what">${showName && w.fragrance_name ? `<b>${esc(w.fragrance_name)}</b> ${esc(w.fragrance_brand || "")} · ` : ""}${esc(bits.join(" · "))}${w.notes ? ` — ${esc(w.notes)}` : ""}</span>
    <button class="ghost small" data-del-wear="${w.id}" aria-label="Delete wear">✕</button></li>`;
}

function wearForm(fragranceId) {
  const picker = fragranceId
    ? `<input type="hidden" name="fragrance_id" value="${fragranceId}">`
    : `<label class="field wide">Fragrance
        <input name="fragrance" list="frag-options" placeholder="Start typing…" required autocomplete="off">
        <datalist id="frag-options">${state.fragrances
          .filter((f) => f.status !== "wishlist")
          .map((f) => `<option value="${esc(fullName(f))}"></option>`).join("")}</datalist></label>`;
  return `<form class="wear-form">
    <div class="form-grid">
      ${picker}
      <label class="field">Date <input type="date" name="worn_on" value="${todayISO()}" max="${todayISO()}" required></label>
      <label class="field">Occasion <select name="occasion"><option value="">—</option>${OCCASIONS.map((o) => `<option>${o}</option>`).join("")}</select></label>
      <label class="field">Weather <select name="weather"><option value="">—</option>${WEATHER.map((o) => `<option>${o}</option>`).join("")}</select></label>
      <label class="field">Sprays <input type="number" name="sprays" min="0" max="50" placeholder="—"></label>
      <label class="field">Compliments <input type="number" name="compliments" min="0" value="0"></label>
      <div class="field wide">How did you enjoy it? ${sentimentRadios("enjoyment", null)}</div>
      <label class="field wide">Notes <input name="notes" placeholder="Longevity, reactions, layering…"></label>
    </div>
    <div class="row end" style="margin-top:10px"><button class="primary" type="submit">Log wear</button></div>
  </form>`;
}

async function submitWear(form, fallbackId) {
  const fd = new FormData(form);
  let fid = Number(fd.get("fragrance_id")) || fallbackId;
  if (!fid && fd.get("fragrance")) {
    const typed = String(fd.get("fragrance")).toLowerCase().trim();
    const f = state.fragrances.find((x) => fullName(x).toLowerCase() === typed)
      || state.fragrances.find((x) => fullName(x).toLowerCase().includes(typed));
    fid = f?.id;
  }
  if (!fid) { toast("Pick a fragrance from your collection", { error: true }); return null; }
  const num = (k) => (fd.get(k) === "" || fd.get(k) === null ? null : Number(fd.get(k)));
  return api("/api/wears", {
    method: "POST",
    body: {
      fragrance_id: fid, worn_on: fd.get("worn_on"), occasion: fd.get("occasion") || null,
      weather: fd.get("weather") || null, sprays: num("sprays"), compliments: num("compliments") || 0,
      enjoyment: readSentiment(form, "enjoyment"), notes: fd.get("notes") || null,
    },
  });
}

function wireDetail(f) {
  const body = $("#detail-body");
  const dlg = $("#detail");
  $$("[data-close]", body).forEach((b) => (b.onclick = () => dlg.close()));
  $$("[data-open-similar]", body).forEach((el) => (el.onclick = () => openDetail(Number(el.dataset.openSimilar))));
  const ph = $("#d-photo", body);
  if (ph) ph.onchange = async () => {
    const lab = ph.previousElementSibling; lab.textContent = "Uploading…";
    try { await api(`/api/fragrances/${f.id}/photo`, { method: "POST", body: { data_url: await photoDataUrl(ph.files[0]) } }); toast("Photo saved"); openDetail(f.id); }
    catch (err) { toast(err.message, { error: true }); lab.textContent = "Add your photo"; }
  };
  const rmp = $('[data-act="rm-photo"]', body);
  if (rmp) rmp.onclick = async () => { await api(`/api/fragrances/${f.id}/photo`, { method: "DELETE" }); openDetail(f.id); };

  const personal = $("#personal", body);
  if (personal) {
    let timer;
    const save = async () => {
      const fd = new FormData(personal);
      const patch = {
        status: fd.get("status"), sentiment: readSentiment(personal, "sentiment"),
        bottle_ml: fd.get("bottle_ml") === "" ? null : Number(fd.get("bottle_ml")),
        personal_notes: fd.get("personal_notes") || null,
      };
      try {
        await api(`/api/fragrances/${f.id}`, { method: "PATCH", body: patch });
        $("#save-state", body).textContent = "Saved ✓";
        state.insights = null;
      } catch (err) { $("#save-state", body).textContent = "Couldn't save: " + err.message; }
    };
    personal.addEventListener("change", save);
    personal.addEventListener("input", (e) => {
      if (e.target.tagName === "TEXTAREA") { clearTimeout(timer); timer = setTimeout(save, 700); }
    });
  }

  const wf = $(".wear-form", body);
  if (wf) wf.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { if (await submitWear(wf, f.id)) { toast("Wear logged"); openDetail(f.id); } }
    catch (err) { toast(err.message, { error: true }); }
  });
  $$("[data-del-wear]", body).forEach((b) => (b.onclick = async () => {
    await api(`/api/wears/${b.dataset.delWear}`, { method: "DELETE" });
    openDetail(f.id);
  }));

  const act = (name, fn) => { const b = $(`[data-act="${name}"]`, body); if (b) b.onclick = fn; };
  act("edit", () => { body.innerHTML = detailEdit(f); wireDetail(f); });
  act("refresh", async (e) => {
    e.target.disabled = true;
    e.target.innerHTML = `<span class="spinner"></span> Fetching…`;
    try { await api(`/api/fragrances/${f.id}/refresh`, { method: "POST" }); toast("Updated from the web"); openDetail(f.id); }
    catch (err) { toast(err.message, { error: true }); e.target.disabled = false; e.target.textContent = "Refresh from web"; }
  });
  act("delete", async () => {
    if (!confirm(`Delete ${fullName(f)} and its ${f.wear_count} wear(s)? This can't be undone.`)) return;
    await api(`/api/fragrances/${f.id}`, { method: "DELETE" });
    dlg.close();
    toast("Deleted");
  });
  const ef = $("#edit-form", body);
  if (ef) {
    ef.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/api/fragrances/${f.id}`, { method: "PATCH", body: readDetailsForm(ef) });
        toast("Saved");
        openDetail(f.id);
      } catch (err) { toast(err.message, { error: true }); }
    });
    act("cancel", () => openDetail(f.id));
  }
}

/* --- editable details form (used for edit + manual add) */

function accordText(accords) {
  return (accords || []).map((a) => `${a.name}:${Math.round(a.strength * 100)}`).join(", ");
}
function parseAccords(s) {
  return parseList(s).map((part) => {
    const [name, pct] = part.split(":").map((x) => x.trim());
    const n = Number(pct);
    return { name, strength: Number.isFinite(n) && pct ? Math.max(0, Math.min(1, n / 100)) : 1 };
  }).filter((a) => a.name);
}
function detailsFields(d = {}) {
  const v = (k) => esc(d[k] ?? "");
  return `<div class="form-grid">
    <label class="field">Name <input name="name" value="${v("name")}" required></label>
    <label class="field">Brand <input name="brand" value="${v("brand")}"></label>
    <label class="field">Year <input name="year" type="number" min="1700" max="2100" value="${v("year")}"></label>
    <label class="field">Concentration <input name="concentration" value="${v("concentration")}" placeholder="Eau de Parfum"></label>
    <label class="field">For <input name="gender" value="${v("gender")}" placeholder="Men / Women / Unisex"></label>
    <label class="field">Family <input name="family" value="${v("family")}" placeholder="Amber Woody"></label>
    <label class="field wide">Perfumer(s) <input name="perfumers" value="${esc(listText(d.perfumers))}" placeholder="Comma separated"></label>
    <label class="field wide">Top notes <input name="top_notes" value="${esc(listText(d.top_notes))}" placeholder="Bergamot, Pink Pepper"></label>
    <label class="field wide">Heart notes <input name="middle_notes" value="${esc(listText(d.middle_notes))}"></label>
    <label class="field wide">Base notes <input name="base_notes" value="${esc(listText(d.base_notes))}"></label>
    <label class="field wide">Notes (if no pyramid) <input name="notes" value="${esc(listText(d.notes))}"></label>
    <label class="field wide">Accords <input name="accords" value="${esc(accordText(d.accords))}" placeholder="woody:100, amber:80, vanilla:60"></label>
    <label class="field wide">Image URL <input name="image_url" value="${v("image_url")}"></label>
  </div>`;
}
function readDetailsForm(form) {
  const fd = new FormData(form);
  const s = (k) => (String(fd.get(k) || "").trim() || null);
  return {
    name: s("name") || "", brand: s("brand") || "", year: s("year") ? Number(s("year")) : null,
    concentration: s("concentration"), gender: s("gender"), family: s("family"),
    perfumers: parseList(fd.get("perfumers")), top_notes: parseList(fd.get("top_notes")),
    middle_notes: parseList(fd.get("middle_notes")), base_notes: parseList(fd.get("base_notes")),
    notes: parseList(fd.get("notes")), accords: parseAccords(fd.get("accords")), image_url: s("image_url"),
  };
}
function detailEdit(f) {
  return `<div class="detail"><div class="card-head"><h2>Edit ${esc(f.name)}</h2><button class="ghost" data-close aria-label="Close">✕</button></div>
    <form id="edit-form" class="card">${detailsFields(f)}
      <div class="row end" style="margin-top:12px"><button type="button" data-act="cancel">Cancel</button><button class="primary" type="submit">Save</button></div>
    </form></div>`;
}

/* ================================================================ add */

const LOOKUP_STAGES = [
  [0, "Searching Fragrantica…"],
  [5000, "Reading the notes pyramid…"],
  [12000, "Still digging — researching across the web (this can take up to a minute)…"],
  [40000, "Almost there — cross-checking sources…"],
];

async function renderAdd() {
  const root = $("#view-add");
  if (!state.status) state.status = await api("/api/status").catch(() => ({}));
  const prefill = new URLSearchParams(location.hash.split("?")[1] || "").get("q") || "";
  if (!root.dataset.ready) {
    root.innerHTML = `
      <div class="view-head"><div><h1>Add a fragrance</h1>
        <div class="subtle">Type a name — typos and nicknames are fine — or paste a Fragrantica link. Notes and accords are fetched from the web.</div></div></div>
      <form class="search-hero" id="lookup-form">
        <input id="lookup-q" name="q" placeholder="e.g. Tom Ford Tobacco Vanille" autocomplete="off" aria-label="Fragrance name or Fragrantica URL" required minlength="2">
        <button class="primary" type="submit">Look up</button>
      </form>
      ${state.status?.ai_available ? "" : `<div class="notice" style="margin-top:10px">Smart lookups are off: Sillage will search Fragrantica directly. Set <code>ANTHROPIC_API_KEY</code> to let Claude research fragrances the direct search can't find (typos, rare releases, when Fragrantica blocks scraping).</div>`}
      <div id="lookup-out" style="margin-top:18px"></div>
      <div class="row" style="margin-top:22px;gap:8px">
        <button class="ghost" id="manual-btn" type="button">Enter one manually</button>
        <button class="ghost" id="bulk-btn" type="button">Add many at once</button>
      </div>
      <div id="extra-out" style="margin-top:12px"></div>`;
    root.dataset.ready = "1";
    $("#lookup-form").addEventListener("submit", (e) => { e.preventDefault(); doLookup($("#lookup-q").value); });
    $("#manual-btn").onclick = () => showManual();
    $("#bulk-btn").onclick = () => showBulk();
  }
  if (prefill) {
    $("#lookup-q").value = prefill;
    history.replaceState(null, "", "#add");
    doLookup(prefill);
  } else {
    $("#lookup-q").focus();
  }
}

let lookupSeq = 0;
async function doLookup(query) {
  query = query.trim();
  if (query.length < 2) return;
  const seq = ++lookupSeq;
  const out = $("#lookup-out");
  const started = Date.now();
  out.innerHTML = `<div class="loading"><span class="spinner"></span><span id="lookup-stage">${LOOKUP_STAGES[0][1]}</span></div>`;
  const ticker = setInterval(() => {
    const el = $("#lookup-stage");
    if (!el) return clearInterval(ticker);
    const stage = [...LOOKUP_STAGES].reverse().find(([t]) => Date.now() - started >= t);
    el.textContent = stage[1];
  }, 1000);
  try {
    const res = await api("/api/lookup", { method: "POST", body: { query } });
    if (seq !== lookupSeq) return;
    state.lookup = res;
    out.innerHTML = previewCard(res);
    wirePreview(res);
  } catch (err) {
    if (seq !== lookupSeq) return;
    const errors = err.detail?.errors || [];
    out.innerHTML = `<div class="notice err"><b>${esc(err.message)}</b>
      ${errors.length ? `<ul class="small">${errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      <div class="small">Try the full name with the brand, paste the Fragrantica URL, or enter it manually.</div></div>`;
  } finally { clearInterval(ticker); }
}

function previewCard(res) {
  const d = res.details;
  const dup = res.duplicate_of
    ? `<div class="notice warn">Already in your collection. <a href="#" data-open="${res.duplicate_of}">Open it</a></div>` : "";
  const msg = res.message ? `<div class="notice">${esc(res.message)}</div>` : "";
  const alts = res.alternatives?.length
    ? `<div class="small"><span class="subtle">Not the one?</span> ${res.alternatives
        .map((a) => `<button type="button" class="small ghost" data-alt="${esc(a.url)}">${esc(a.label)}</button>`).join(" ")}</div>` : "";
  const src = d.source_url ? `<a href="${esc(d.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>` : "";
  return `<div class="card" data-name="${esc(d.name)}">
    <div class="detail-hero" style="grid-template-columns:120px 1fr">
      <div class="img" style="width:120px">${thumb(d)}</div>
      <div><div class="subtle">${esc(d.brand)}</div><h2>${esc(d.name)}</h2>${facts(d)}
        <div class="small subtle" style="margin-top:6px">Found via ${esc(d.source)} ${src}</div></div>
    </div>
    <div style="display:grid;gap:10px;margin-top:14px">${msg}${dup}${alts}</div>
    ${res.prediction ? `<div style="margin-top:14px">${predictionBox(res.prediction, "Based on your taste so far")}</div>` : ""}
    <div class="two-col" style="margin-top:14px">
      <div><h3 style="margin-bottom:8px">Main accords</h3>${accordBars(d.accords)}</div>
      <div><h3 style="margin-bottom:8px">Notes</h3>${pyramid(d)}</div>
    </div>
    <form id="save-form" class="card" style="margin-top:16px;box-shadow:none;background:var(--surface-2)">
      <div class="form-grid">
        <label class="field">Status ${statusSelect("status", "owned")}</label>
        <label class="field">Bottle size (ml) <input name="bottle_ml" type="number" min="0" step="any"></label>
        <div class="field wide">How do you feel about it? ${sentimentRadios("sentiment", null)}</div>
        <label class="field wide">Your notes <input name="personal_notes" placeholder="Optional"></label>
      </div>
      <div class="row end" style="margin-top:12px">
        <button type="button" data-act="edit-preview">Fix details first</button>
        <button class="primary" type="submit">${res.duplicate_of ? "Add anyway" : "Add to collection"}</button>
      </div>
    </form>
  </div>`;
}

function wirePreview(res) {
  const out = $("#lookup-out");
  $$("[data-alt]", out).forEach((b) => (b.onclick = () => { $("#lookup-q").value = b.dataset.alt; doLookup(b.dataset.alt); }));
  $$("[data-open]", out).forEach((a) => (a.onclick = (e) => { e.preventDefault(); openDetail(Number(a.dataset.open)); }));
  const form = $("#save-form", out);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = {
      ...state.lookup.details, status: fd.get("status"), sentiment: readSentiment(form, "sentiment"),
      bottle_ml: fd.get("bottle_ml") ? Number(fd.get("bottle_ml")) : null, personal_notes: fd.get("personal_notes") || null,
      allow_duplicate: Boolean(res.duplicate_of),
    };
    await saveNew(body);
  });
  $('[data-act="edit-preview"]', out).onclick = () => showManual(state.lookup.details);
}

async function saveNew(body) {
  try {
    const saved = await api("/api/fragrances", { method: "POST", body });
    state.insights = null;
    $("#lookup-out").innerHTML = "";
    $("#extra-out").innerHTML = "";
    $("#lookup-q").value = "";
    toast(`Added ${saved.name}`, { action: "View", onAction: () => openDetail(saved.id) });
    $("#lookup-q").focus();
  } catch (err) {
    if (err.status === 409) toast("That one's already in your collection", { error: true, action: "Open", onAction: () => openDetail(err.detail.id) });
    else toast(err.message, { error: true });
  }
}

function showManual(details) {
  const box = $("#extra-out");
  $("#lookup-out").innerHTML = "";
  box.innerHTML = `<form class="card" id="manual-form"><h3>${details ? "Check the details" : "Enter a fragrance manually"}</h3>
    ${detailsFields(details || {})}
    <div class="form-grid" style="margin-top:12px">
      <label class="field">Status ${statusSelect("status", "owned")}</label>
      <div class="field wide">How do you feel about it? ${sentimentRadios("sentiment", null)}</div>
    </div>
    <div class="row end" style="margin-top:12px"><button type="button" data-act="cancel-manual">Cancel</button><button class="primary" type="submit">Add to collection</button></div>
  </form>`;
  const form = $("#manual-form");
  form.querySelector('[data-act="cancel-manual"]').onclick = () => (box.innerHTML = "");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const d = readDetailsForm(form);
    saveNew({
      ...(details || {}), ...d, status: new FormData(form).get("status"), sentiment: readSentiment(form, "sentiment"),
      source: details?.source || "manual", source_url: details?.source_url || null,
    });
  });
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showBulk() {
  const box = $("#extra-out");
  box.innerHTML = `<form class="card" id="bulk-form"><h3>Add many at once</h3>
    <p class="subtle small" style="margin-top:0">One fragrance per line. Each is looked up and saved automatically — check the results afterwards.</p>
    <textarea name="lines" rows="8" placeholder="Dior Sauvage EDT&#10;Creed Aventus&#10;Maison Francis Kurkdjian Baccarat Rouge 540"></textarea>
    <div class="form-grid" style="margin-top:12px">
      <label class="field">Status for all ${statusSelect("status", "owned")}</label>
    </div>
    <div class="row end" style="margin-top:12px"><button class="primary" type="submit">Start</button></div>
    <ul class="bulk-log" id="bulk-log"></ul>
  </form>`;
  const form = $("#bulk-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const lines = [...new Set(String(new FormData(form).get("lines")).split("\n").map((l) => l.trim()).filter((l) => l.length > 1))];
    const status = new FormData(form).get("status");
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const log = $("#bulk-log");
    log.innerHTML = lines.map((l, i) => `<li data-i="${i}"><span class="st">·</span><span>${esc(l)}</span></li>`).join("");
    let ok = 0;
    for (const [i, line] of lines.entries()) {
      const li = log.querySelector(`[data-i="${i}"]`);
      li.querySelector(".st").innerHTML = `<span class="spinner" style="width:14px;height:14px"></span>`;
      try {
        const f = await api("/api/fragrances/quick", { method: "POST", body: { query: line, status } });
        li.innerHTML = `<span class="st" style="color:var(--ok)">✓</span><span>${esc(line)} → <b>${esc(fullName(f))}</b>${f.lookup_message ? ` <span class="subtle">(${esc(f.lookup_message)})</span>` : ""}</span>`;
        ok++;
      } catch (err) {
        const mark = err.status === 409 ? "=" : "✗";
        li.innerHTML = `<span class="st" style="color:${err.status === 409 ? "var(--muted)" : "var(--danger)"}">${mark}</span><span>${esc(line)} — <span class="subtle">${esc(err.message)}</span></span>`;
      }
    }
    btn.disabled = false;
    state.insights = null;
    toast(`Added ${ok} of ${lines.length}`);
  });
}

/* ================================================================ wear log */

async function renderWear() {
  const root = $("#view-wear");
  await loadFragrances();
  const wears = await api("/api/wears?limit=300");
  const wearable = state.fragrances.filter((f) => ["owned", "sample", "decant"].includes(f.status));
  root.innerHTML = `
    <div class="view-head"><div><h1>Wear log</h1><div class="subtle">${wears.length ? `${wears.length} recent wears` : "Track what you wear to learn what you actually reach for."}</div></div>
      <button id="suggest-btn" ${wearable.length ? "" : "disabled"}>Suggest what to wear</button></div>
    <div id="suggest-out"></div>
    ${state.fragrances.length ? `<div class="card"><h3>Log a wear</h3>${wearForm(null)}</div>` : `<div class="empty card">Add fragrances to your collection first. <a href="#add">Add one</a></div>`}
    <div class="card"><h3>History</h3>
      <ul class="wear-list">${wears.map((w) => wearItem(w)).join("") || `<li class="subtle">Nothing logged yet.</li>`}</ul></div>`;
  const form = $(".wear-form", root);
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { if (await submitWear(form)) { toast("Wear logged"); renderWear(); } }
    catch (err) { toast(err.message, { error: true }); }
  });
  $$("[data-del-wear]", root).forEach((b) => (b.onclick = async () => {
    await api(`/api/wears/${b.dataset.delWear}`, { method: "DELETE" });
    renderWear();
  }));
  $("#suggest-btn").onclick = () => suggest(wearable);
}

function suggest(wearable) {
  // Favour what you like and haven't worn lately, skip things you dislike.
  const ranked = wearable
    .filter((f) => (f.sentiment ?? 0) >= 0)
    .map((f) => {
      const gap = daysSince(f.last_worn) ?? 60;
      return { f, s: (f.score ?? 0.5) + Math.min(gap / 21, 2) + Math.random() * 0.6 };
    })
    .sort((a, b) => b.s - a.s)
    .slice(0, 3);
  const out = $("#suggest-out");
  if (!ranked.length) { out.innerHTML = `<div class="notice">Rate some fragrances first.</div>`; return; }
  out.innerHTML = `<div class="card"><div class="card-head"><h3>Today, maybe…</h3><button class="ghost small" id="reshuffle">Shuffle</button></div>
    <div class="grid">${ranked.map(({ f }) => card(f)).join("")}</div></div>`;
  $("#reshuffle").onclick = () => suggest(wearable);
  out.querySelector(".grid").addEventListener("click", onGridClick);
}

/* ================================================================ insights */

function divergingChart(rows, emptyText) {
  if (!rows.length) return `<p class="subtle small">${esc(emptyText)}</p>`;
  const max = Math.max(...rows.map((r) => Math.abs(r.affinity)), 0.5);
  return `<div class="legend"><span><span class="sw" style="background:var(--neg)"></span>Avoid</span><span><span class="sw" style="background:var(--pos)"></span>Love</span></div>
  <div class="div-chart">${rows.map((r) => {
    const pct = Math.round((Math.abs(r.affinity) / max) * 100);
    const tip = `${r.label}: ${signed(r.affinity)} · in ${r.count} fragrance${r.count === 1 ? "" : "s"}\n` +
      r.fragrances.map((f) => `• ${f.name} (${f.score >= 0 ? "+" : ""}${f.score.toFixed(1)})`).join("\n");
    return `<div class="div-row" data-tip="${esc(tip)}">
      <span class="lbl">${esc(r.label)} <small>×${r.count}</small></span>
      <span class="neg-half">${r.affinity < 0 ? `<span class="bar" style="width:${pct}%"></span>` : ""}</span>
      <span class="pos-half">${r.affinity > 0 ? `<span class="bar" style="width:${pct}%"></span>` : ""}</span>
      <span class="val">${signed(r.affinity)}</span></div>`;
  }).join("")}</div>`;
}

function columnChart(months) {
  const max = Math.max(...months.map((m) => m.count), 1);
  const peak = months.reduce((a, b) => (b.count > a.count ? b : a), months[0]);
  const name = (k) => new Date(k + "-01T00:00:00").toLocaleDateString(undefined, { month: "short" });
  return `<div class="col-chart">${months.map((m) => {
    const h = (m.count / max) * 100;
    const label = m === peak && m.count ? `<span class="top" style="bottom:calc(${h}% + 2px)">${m.count}</span>` : "";
    return `<div class="col" data-tip="${esc(`${name(m.month)} ${m.month.slice(0, 4)}: ${m.count} wear${m.count === 1 ? "" : "s"}`)}">${label}<div class="bar" style="height:${h}%"></div></div>`;
  }).join("")}</div><div class="col-axis">${months.map((m) => `<span>${name(m.month)}</span>`).join("")}</div>`;
}

function fragLinkList(rows, right) {
  if (!rows.length) return `<p class="subtle small">Nothing yet.</p>`;
  return `<ul class="list">${rows.map((r) => `<li><span class="link" data-open="${r.id}">${esc(r.name)} <span class="subtle small">${esc(r.brand || "")}</span></span><span class="num">${right(r)}</span></li>`).join("")}</ul>`;
}

function scoreCell(v) {
  if (v === null || v === undefined) return `<span class="subtle">—</span>`;
  return `<span style="color:${v >= 0.35 ? "var(--pos)" : v <= -0.35 ? "var(--neg)" : "var(--text-2)"}">${signed(v)}</span>`;
}

async function renderInsights() {
  const root = $("#view-insights");
  root.innerHTML = `<div class="loading"><span class="spinner"></span>Analysing your collection…</div>`;
  const [r, ai, status] = await Promise.all([
    api("/api/insights"), api("/api/insights/ai").catch(() => null), state.status || api("/api/status").catch(() => ({})),
  ]);
  state.insights = r;
  state.status = status;
  const t = r.totals;
  const noteRows = [...r.liked_notes.slice(0, 10), ...r.disliked_notes.slice(0, 8).reverse()];
  const accRows = [...r.liked_accords.slice(0, 8), ...r.disliked_accords.slice(0, 6).reverse()];

  root.innerHTML = `
    <div class="view-head"><div><h1>Your scent profile</h1><div class="subtle">Learned from ${r.scored_count} rated or worn fragrance${r.scored_count === 1 ? "" : "s"}</div></div></div>
    <div class="card summary-card">${r.summary.map((l) => `<p>${esc(l)}</p>`).join("")}</div>
    <div class="kpis">
      <div class="kpi"><div class="v">${t.fragrances}</div><div class="k">fragrances (${t.by_status.owned || 0} owned)</div></div>
      <div class="kpi"><div class="v">${t.wears}</div><div class="k">wears logged</div></div>
      <div class="kpi"><div class="v">${t.wears_last_30_days}</div><div class="k">wears in last 30 days</div></div>
      <div class="kpi"><div class="v">${t.unique_worn_last_90_days}</div><div class="k">different scents worn (90 days)</div></div>
    </div>

    <div class="card" id="ai-card">${aiCard(ai, status, r)}</div>

    <div class="insight-grid" style="margin-top:16px">
      <div class="card"><div class="card-head"><h3>Notes: love vs avoid</h3><span class="small subtle">hover for fragrances</span></div>
        ${divergingChart(noteRows, "Rate a few fragrances to see which notes you gravitate to.")}</div>
      <div class="card"><div class="card-head"><h3>Accords: love vs avoid</h3></div>
        ${divergingChart(accRows, "No accord data yet.")}
        ${r.polarizing_notes.length ? `<h3 style="margin-top:18px">Polarizing notes</h3>
          <p class="small subtle" style="margin-top:0">In both fragrances you love and ones you dislike — the rest of the composition decides.</p>
          ${noteChips(r.polarizing_notes.map((n) => n.label))}` : ""}
      </div>
    </div>

    <div class="insight-grid" style="margin-top:16px">
      <div class="card"><h3>Wears per month</h3>${columnChart(r.monthly_wears)}</div>
      <div class="card"><h3>What you wear by season</h3>${r.seasons.length ? `<div class="season-grid">${r.seasons.map((s) => `
        <div class="season"><b>${esc(s.season)}</b> <span class="small subtle">${s.wears} wears</span>
        <div class="chips" style="margin-top:6px">${s.accords.map((a) => `<span class="chip"><span class="dot" style="background:${accordColor(a.label)}"></span>${esc(a.label)}</span>`).join("")}</div></div>`).join("")}</div>`
        : `<p class="subtle small">Log wears to see your seasonal habits.</p>`}</div>
    </div>

    <div class="insight-grid" style="margin-top:16px">
      <div class="card"><h3>Most worn</h3>${fragLinkList(r.most_worn, (x) => `${x.count}×`)}</div>
      <div class="card"><h3>Neglected bottles</h3><p class="small subtle" style="margin-top:0">Owned but not worn in 90+ days</p>
        ${fragLinkList(r.neglected, (x) => (x.last_worn ? relDays(x.last_worn) : "never"))}</div>
      <div class="card"><h3>Compliment getters</h3>${fragLinkList(r.most_complimented, (x) => `${x.compliments} 💬`)}</div>
      <div class="card"><h3>Wishlist: predicted match</h3>${fragLinkList(r.wishlist_predictions, (x) => `<span class="pill match">${x.match}%</span>`)}</div>
    </div>

    <div class="insight-grid" style="margin-top:16px">
      ${groupTable("Families", r.families)}
      ${groupTable("Perfumers (2+ fragrances)", r.perfumers)}
      ${groupTable("Brands", r.brands)}
      ${r.occasions.length ? `<div class="card"><h3>Go-tos by occasion</h3><ul class="list">${r.occasions.map((o) => `<li><span><b>${esc(o.occasion)}</b></span>
        <span class="num wrap">${o.top.map((x) => `<span class="link" data-open="${x.id}">${esc(x.name)}</span>`).join(", ")}</span></li>`).join("")}</ul></div>` : ""}
    </div>`;

  $$("[data-open]", root).forEach((el) => (el.onclick = () => openDetail(Number(el.dataset.open))));
  wireAi(root);
}

function groupTable(title, rows) {
  if (!rows.length) return "";
  return `<div class="card"><h3>${esc(title)}</h3><table class="tbl"><thead><tr><th>Name</th><th class="num">Count</th><th class="num">Avg score</th></tr></thead>
    <tbody>${rows.map((g) => `<tr><td>${esc(g.label)}</td><td class="num">${g.count}</td><td class="num">${scoreCell(g.avg_score)}</td></tr>`).join("")}</tbody></table></div>`;
}

function aiCard(ai, status, report) {
  const btn = status?.ai_available
    ? `<button class="primary" id="ai-btn" ${report.ready ? "" : "disabled"}>${ai ? "Regenerate" : "Generate"} AI profile</button>`
    : `<span class="small subtle">Set ANTHROPIC_API_KEY to enable</span>`;
  const head = `<div class="card-head"><h3>AI scent profile & recommendations</h3>${btn}</div>`;
  if (!ai) {
    return head + `<p class="subtle" style="margin:0">Claude reads your statistics and collection, explains your taste in plain words, and suggests what to try next (and what to skip).</p>`;
  }
  return head + `
    <p class="small subtle" style="margin-top:-6px">Generated ${esc(fmtDate(ai.created_at.slice(0, 10)))}</p>
    <h2 style="margin-bottom:8px">${esc(ai.headline)}</h2>
    ${ai.narrative.split(/\n+/).map((p) => `<p>${esc(p)}</p>`).join("")}
    <div class="chips" style="margin:10px 0 16px">${ai.signature_traits.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
    <h3>Try next</h3>
    <div class="recs">${ai.recommendations.map((x) => `<div class="rec"><div class="subtle small">${esc(x.brand)}</div><div class="name">${esc(x.name)}</div>
      <div class="small">${esc(x.why)}</div>${noteChips(x.key_notes)}
      <div><a class="btn small" href="#add?q=${encodeURIComponent(`${x.brand} ${x.name}`)}">Look up & add</a></div></div>`).join("")}</div>
    <div class="two-col" style="margin-top:16px">
      <div><h3>Probably skip</h3><ul class="list">${ai.avoid.map((x) => `<li><span><b>${esc(x.brand)} ${esc(x.name)}</b><br><span class="small subtle">${esc(x.why)}</span></span></li>`).join("")}</ul></div>
      <div><h3>Gaps in your collection</h3><ul class="list">${ai.collection_gaps.map((g) => `<li><span>${esc(g)}</span></li>`).join("")}</ul></div>
    </div>`;
}

function wireAi(root) {
  const btn = $("#ai-btn", root);
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Thinking…`;
    try {
      const ai = await api("/api/insights/ai", { method: "POST" });
      $("#ai-card").innerHTML = aiCard(ai, state.status, state.insights);
      wireAi(root);
    } catch (err) {
      toast(err.message, { error: true });
      btn.disabled = false;
      btn.textContent = "Generate AI profile";
    }
  };
}

/* ================================================================ AI: markdown + charts */

// Small, safe Markdown renderer: everything is escaped first, then a few constructs are re-enabled.
function md(src) {
  const blocks = String(src || "").replace(/\r/g, "").split(/\n{2,}/);
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return blocks.map((b) => {
    if (/^```/.test(b)) return `<pre><code>${esc(b.replace(/^```\w*\n?|```$/g, ""))}</code></pre>`;
    const lines = b.split("\n");
    if (/^#{1,4}\s/.test(b)) {
      const [h, ...rest] = lines;
      const lvl = Math.min(4, h.match(/^#+/)[0].length + 2);
      return `<h${lvl}>${inline(h.replace(/^#+\s*/, ""))}</h${lvl}>${rest.length ? md(rest.join("\n")) : ""}`;
    }
    if (lines.every((l) => /^\s*[-*•]\s+/.test(l) || !l.trim()))
      return `<ul>${lines.filter((l) => l.trim()).map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("")}</ul>`;
    if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l) || !l.trim()))
      return `<ol>${lines.filter((l) => l.trim()).map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`;
    if (lines.length > 1 && lines.every((l) => /^\s*\|/.test(l))) {
      const rows = lines.filter((l) => !/^\s*\|[\s:|-]+\|\s*$/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      const [head, ...body] = rows;
      return `<div class="tbl-wrap"><table class="tbl"><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    return `<p>${lines.map(inline).join("<br>")}</p>`;
  }).join("");
}

function chartView(c) {
  if (!c) return "";
  const title = c.title ? `<div class="small subtle" style="margin-bottom:6px">${esc(c.title)}</div>` : "";
  const unit = c.unit ? (c.unit === "%" ? "%" : ` ${c.unit}`) : "";
  const fmt = (v) => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v * 10) / 10) : v.toFixed(2)) + unit;
  if (c.type === "table") {
    return title + `<div class="tbl-wrap"><table class="tbl"><thead><tr>${c.columns.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${c.rows.map((r) => `<tr>${r.map((v) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  if (c.type === "line") {
    const W = 560, H = 160, P = 24, n = c.values.length;
    const max = Math.max(...c.values), min = Math.min(0, ...c.values), span = max - min || 1;
    const x = (i) => P + (n === 1 ? (W - 2 * P) / 2 : (i * (W - 2 * P)) / (n - 1));
    const y = (v) => H - P - ((v - min) / span) * (H - 2 * P);
    const pts = c.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const peak = c.values.indexOf(max);
    return title + `<svg class="line-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(c.title || "trend")}">
      <line x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}" class="axis-line"/>
      <polygon points="${x(0)},${H - P} ${pts} ${x(n - 1)},${H - P}" class="area"/>
      <polyline points="${pts}" class="trend"/>
      ${c.values.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="${i === peak ? 5 : 3.5}" class="pt" data-tip="${esc(`${c.labels[i]}: ${fmt(v)}`)}"/>`).join("")}
      <text x="${x(peak)}" y="${y(max) - 10}" text-anchor="middle" class="lbl">${esc(fmt(max))}</text>
      <text x="${P}" y="${H - 6}" class="lbl">${esc(c.labels[0])}</text>
      <text x="${W - P}" y="${H - 6}" text-anchor="end" class="lbl">${esc(c.labels[n - 1])}</text></svg>`;
  }
  const max = Math.max(...c.values.map(Math.abs), 1e-9);
  if (c.type === "diverging") {
    return title + `<div class="legend"><span><span class="sw" style="background:var(--neg)"></span>Negative</span><span><span class="sw" style="background:var(--pos)"></span>Positive</span></div>
      <div class="div-chart">${c.labels.map((l, i) => { const v = c.values[i], pct = Math.round((Math.abs(v) / max) * 100);
        return `<div class="div-row" data-tip="${esc(`${l}: ${fmt(v)}`)}"><span class="lbl">${esc(l)}</span><span class="neg-half">${v < 0 ? `<span class="bar" style="width:${pct}%"></span>` : ""}</span><span class="pos-half">${v > 0 ? `<span class="bar" style="width:${pct}%"></span>` : ""}</span><span class="val">${esc(fmt(v))}</span></div>`; }).join("")}</div>`;
  }
  return title + `<div class="hbars">${c.labels.map((l, i) => `<div class="hbar" data-tip="${esc(`${l}: ${fmt(c.values[i])}`)}"><span class="lbl">${esc(l)}</span><span class="track"><span class="bar" style="width:${Math.max(2, Math.round((Math.abs(c.values[i]) / max) * 100))}%"></span></span><span class="val">${esc(fmt(c.values[i]))}</span></div>`).join("")}</div>`;
}

/* ================================================================ AI: chat + feed */

const TOOL_LABELS = {
  collection_overview: () => "Looked over your collection", search_fragrances: (i) => `Searched your collection${i.note ? ` for ${i.note}` : i.text ? ` for “${i.text}”` : ""}`,
  get_fragrance: () => "Opened a fragrance", taste_profile: () => "Read your taste profile", wear_history: () => "Checked your wear log",
  query_database: () => "Ran an analysis query", evaluate_fragrance: (i) => `Looked up ${i.name || "a fragrance"} online`,
  similar_fragrances: () => "Compared scent profiles", suggest_what_to_wear: () => "Picked what to wear", list_insights: () => "Read your insights",
  add_fragrance: (i) => `Added ${i.name || "a fragrance"}`, update_fragrance: () => "Updated a fragrance", log_wear: () => "Logged a wear",
  publish_insight: (i) => `Published “${i.title || "an insight"}” to your feed`,
};
const WRITE_TOOLS = new Set(["add_fragrance", "update_fragrance", "log_wear", "publish_insight"]);
const PROMPTS = [
  "What notes do I love, and which bottles prove it?",
  "Pick something for a cold-weather date tonight",
  "Would I like Parfums de Marly Layton?",
  "Which bottles am I neglecting, and which should I sell?",
  "Chart my wears per month and publish it to my insights",
  "What gaps does my collection have?",
];
const chat = { messages: [], busy: false, ctl: null };
try { chat.messages = JSON.parse(store.get("sillage-chat") || "[]"); } catch { chat.messages = []; }
const saveChat = () => store.set("sillage-chat", JSON.stringify(chat.messages.slice(-30).map(({ role, content }) => ({ role, content }))));

async function renderAI() {
  const root = $("#view-ai");
  if (!state.status) state.status = await api("/api/status").catch(() => ({}));
  if (!root.dataset.ready) {
    root.dataset.ready = "1";
    root.innerHTML = `
      <div class="view-head"><div><h1>Ask Sillage</h1><div class="subtle">An AI that knows your whole collection, your ratings and every wear. It can also save what it finds.</div></div>
        <div class="row"><a class="btn" href="#connect">Connect another AI</a><button class="ghost" id="chat-new">New chat</button></div></div>
      <div class="ai-layout">
        <section class="card chat-card" aria-label="Chat">
          <div id="chat-log" class="chat-log" aria-live="polite"></div>
          <form id="chat-form" class="chat-form">
            <textarea id="chat-input" rows="1" placeholder="Ask about your fragrances…" aria-label="Message"></textarea>
            <button class="primary" type="submit" id="chat-send">Send</button>
          </form>
          <div id="chat-off" class="notice small" hidden>The assistant needs an Anthropic API key on the server: set <code>ANTHROPIC_API_KEY</code> and restart. Other AI apps can still use your data from the <a href="#connect">Connect</a> page.</div>
        </section>
        <section aria-label="AI insights">
          <div class="card-head"><h2>AI insights</h2><span class="small subtle" id="feed-count"></span></div>
          <div id="feed"></div>
        </section>
      </div>`;
    $("#chat-form").onsubmit = (e) => { e.preventDefault(); sendChat($("#chat-input").value); };
    $("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(e.target.value); } });
    $("#chat-input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px"; });
    $("#chat-new").onclick = () => { chat.ctl?.abort(); chat.messages = []; saveChat(); drawChat(); };
    $("#chat-log").addEventListener("click", onChatClick);
    $("#feed").addEventListener("click", onFeedClick);
  }
  const off = !state.status?.ai_available;
  $("#chat-off").hidden = !off;
  $("#chat-send").disabled = off;
  drawChat();
  drawFeed();
}

function drawChat() {
  const log = $("#chat-log");
  if (!log) return;
  if (!chat.messages.length) {
    log.innerHTML = `<div class="chat-empty"><div class="spark" aria-hidden="true">✦</div><h3>What would you like to know?</h3>
      <div class="chips">${PROMPTS.map((p) => `<button type="button" class="chip prompt" data-prompt="${esc(p)}">${esc(p)}</button>`).join("")}</div></div>`;
    return;
  }
  log.innerHTML = chat.messages.map((m, i) => m.role === "user"
    ? `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`
    : `<div class="msg bot">
        ${(m.steps || []).length ? `<div class="steps">${m.steps.map((s) => `<details class="step ${s.done ? (s.ok ? "ok" : "bad") : "run"}"><summary>${s.done ? (s.ok ? "✓" : "!") : '<span class="spinner tiny"></span>'} ${esc(s.label)}</summary><pre>${esc(JSON.stringify(s.input, null, 2))}${s.preview ? "\n→ " + esc(s.preview) : ""}</pre></details>`).join("")}</div>` : ""}
        <div class="bubble md">${m.content ? md(m.content) : m.error ? "" : `<span class="thinking">Thinking<span>.</span><span>.</span><span>.</span></span>`}</div>
        ${m.error ? `<div class="notice err small">${esc(m.error)}</div>` : ""}
        ${m.content && !m.streaming ? `<div class="msg-actions"><button class="small ghost" data-save="${i}">Save to insights</button><button class="small ghost" data-copy="${i}">Copy</button></div>` : ""}
      </div>`).join("");
  log.scrollTop = log.scrollHeight;
}

function onChatClick(e) {
  const p = e.target.closest("[data-prompt]");
  if (p) return sendChat(p.dataset.prompt);
  const s = e.target.closest("[data-save]");
  if (s) return saveAnswer(Number(s.dataset.save), s);
  const c = e.target.closest("[data-copy]");
  if (c) copyText(chat.messages[Number(c.dataset.copy)].content);
}

async function saveAnswer(i, btn) {
  const answer = chat.messages[i], question = chat.messages[i - 1]?.content || "Ask Sillage";
  const plain = answer.content.replace(/[#*_`>|]/g, "").replace(/\s+/g, " ").trim();
  btn.disabled = true;
  try {
    await api("/api/feed", { method: "POST", headers: { "X-Client-Name": "Ask Sillage" },
      body: { title: question.slice(0, 118), summary: plain.slice(0, 280) + (plain.length > 280 ? "…" : ""), body_markdown: answer.content } });
    toast("Saved to your insights");
    drawFeed();
  } catch (err) { toast(err.message, { error: true }); btn.disabled = false; }
}

async function sendChat(text) {
  text = String(text || "").trim();
  if (!text || chat.busy || !state.status?.ai_available) return;
  $("#chat-input").value = ""; $("#chat-input").style.height = "auto";
  chat.messages.push({ role: "user", content: text });
  const bot = { role: "assistant", content: "", steps: [], streaming: true };
  chat.messages.push(bot);
  chat.busy = true; chat.ctl = new AbortController();
  const send = $("#chat-send");
  send.textContent = "Stop"; send.type = "button"; send.onclick = () => chat.ctl.abort();
  drawChat();
  let wrote = false, redraw = 0;
  const history = chat.messages.slice(0, -1).filter((m) => m.content).slice(-20).map(({ role, content }) => ({ role, content }));
  try {
    await ensureKey();
    const res = await fetch("/api/chat", { method: "POST", signal: chat.ctl.signal, headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify({ messages: history }) });
    if (res.status === 401) { showLogin(); throw new Error("Enter your Sillage key"); }
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(typeof d.detail === "string" ? d.detail : res.statusText); }
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx).replace(/^data: /, ""); buf = buf.slice(idx + 2);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "text") bot.content += ev.delta;
        else if (ev.type === "tool") bot.steps.push({ id: ev.id, name: ev.name, input: ev.input, label: (TOOL_LABELS[ev.name] || (() => ev.name))(ev.input || {}) });
        else if (ev.type === "tool_result") { const st = bot.steps.find((x) => x.id === ev.id); if (st) Object.assign(st, { done: true, ok: ev.ok, preview: ev.preview }); if (ev.ok && WRITE_TOOLS.has(ev.name)) wrote = true; }
        else if (ev.type === "error") bot.error = ev.message;
      }
      const now = Date.now();
      if (now - redraw > 60) { drawChat(); redraw = now; }
    }
  } catch (err) {
    if (err.name !== "AbortError") bot.error = err.message || "Something went wrong";
  } finally {
    bot.streaming = false; chat.busy = false;
    send.textContent = "Send"; send.type = "submit"; send.onclick = null;
    if (!bot.content && !bot.error) bot.error = "Stopped.";
    saveChat(); drawChat();
    if (wrote) { state.insights = null; drawFeed(); }
  }
}

async function drawFeed() {
  const box = $("#feed");
  if (!box) return;
  let items;
  try { items = await api("/api/feed"); } catch { return; }
  $("#feed-count").textContent = items.length ? `${items.length} saved` : "";
  if (!items.length) {
    box.innerHTML = `<div class="card empty small"><p><b>Nothing here yet.</b></p><p class="subtle">Insights appear here when you save an answer, when Ask Sillage publishes one, or when another AI app (Claude, ChatGPT, Cursor…) publishes through your Sillage connection.</p></div>`;
    return;
  }
  const names = new Map(state.fragrances.map((f) => [f.id, f.name]));
  box.innerHTML = items.map((it) => `<article class="card insight ${it.pinned ? "pinned" : ""}">
    <div class="insight-meta"><span class="pill src">${esc(it.source)}</span><span class="small subtle">${esc(fmtDate(it.created_at.slice(0, 10)))}</span>${it.pinned ? `<span class="pill match">Pinned</span>` : ""}
      <span class="insight-actions"><button class="ghost small" data-pin="${it.id}" data-pinned="${it.pinned ? 1 : 0}" title="${it.pinned ? "Unpin" : "Pin to top"}">${it.pinned ? "Unpin" : "Pin"}</button><button class="ghost small" data-del-insight="${it.id}" aria-label="Delete insight">✕</button></span></div>
    <h3>${esc(it.title)}</h3>
    <p class="insight-summary">${esc(it.summary)}</p>
    ${it.chart ? `<div class="insight-chart">${chartView(it.chart)}</div>` : ""}
    ${it.body ? `<details class="insight-body"><summary>Read the full analysis</summary><div class="md">${md(it.body)}</div></details>` : ""}
    ${it.fragrance_ids?.length ? `<div class="chips" style="margin-top:8px">${it.fragrance_ids.filter((id) => names.has(id)).map((id) => `<button class="chip linkchip" data-open-frag="${id}">${esc(names.get(id))}</button>`).join("")}</div>` : ""}
  </article>`).join("");
}

async function onFeedClick(e) {
  const pin = e.target.closest("[data-pin]");
  if (pin) { await api(`/api/feed/${pin.dataset.pin}`, { method: "PATCH", body: { pinned: pin.dataset.pinned !== "1" } }); return drawFeed(); }
  const del = e.target.closest("[data-del-insight]");
  if (del) { if (!confirm("Delete this insight?")) return; await api(`/api/feed/${del.dataset.delInsight}`, { method: "DELETE" }); return drawFeed(); }
  const f = e.target.closest("[data-open-frag]");
  if (f) openDetail(Number(f.dataset.openFrag));
}

/* ================================================================ connect */

function copyText(text) {
  const p = navigator.clipboard?.writeText(text) || Promise.reject(new Error("no clipboard"));
  p.then(() => toast("Copied"), () => toast("Couldn't copy. Select the text and copy it instead.", { error: true }));
}

function copyBlock(label, text, note = "") {
  return `<div class="snippet"><div class="row between"><b class="small">${esc(label)}</b><button class="small" data-copy-text="${esc(text)}">Copy</button></div>
    <pre>${esc(text)}</pre>${note ? `<p class="small subtle">${note}</p>` : ""}</div>`;
}

async function renderConnect() {
  const root = $("#view-connect");
  root.innerHTML = `<div class="loading"><span class="spinner"></span>Loading connection details…</div>`;
  let c;
  try { c = await api("/api/connect"); } catch (err) { root.innerHTML = `<div class="notice err">${esc(err.message)}</div>`; return; }
  const k = c.api_key, masked = k.slice(0, 14) + "•".repeat(12);
  const desktop = JSON.stringify({ mcpServers: { sillage: { command: c.python, args: ["-m", "app.mcp_server"], env: { PYTHONPATH: c.project_dir } } } }, null, 2);
  root.innerHTML = `
    <div class="view-head"><div><h1>Connect your AI</h1><div class="subtle">Let Claude, ChatGPT, Cursor or any agent read your collection, run analyses and publish the results back into Sillage.</div></div></div>
    <div class="connect-grid">
      <div class="card">
        <h3>Your key</h3>
        <p class="small subtle">Every request needs it. Treat it like a password.</p>
        <div class="keybox"><code id="key-text">${esc(masked)}</code><button class="small" id="key-show">Show</button><button class="small" data-copy-text="${esc(k)}">Copy</button></div>
        <dl class="kv"><dt>MCP endpoint</dt><dd><code>${esc(c.mcp_url)}</code></dd><dt>REST API</dt><dd><code>${esc(c.base_url)}/api</code> · <a href="/docs" target="_blank" rel="noopener">interactive docs ↗</a></dd><dt>OpenAPI</dt><dd><code>${esc(c.openapi_url)}</code></dd></dl>
        ${c.public ? `<div class="notice small">Public URL configured, so cloud apps (claude.ai, ChatGPT) can reach this server.</div>` : `<div class="notice warn small">This server is only reachable from this computer. Desktop apps work now. For claude.ai or ChatGPT on the web, deploy it with a public HTTPS URL (see the README) and set <code>SILLAGE_PUBLIC_URL</code>.</div>`}
      </div>
      <div class="card">
        <h3>Tools your AI gets</h3>
        <ul class="list tool-list">${c.tools.map((t) => `<li><span><code>${esc(t.name)}</code><br><span class="small subtle">${esc(t.summary)}</span></span><span class="pill ${t.read_only ? "" : "write"}">${t.read_only ? "read" : "write"}</span></li>`).join("")}</ul>
      </div>
    </div>
    <div class="stack-gap">
      <div class="card"><h3>Claude Code</h3>${copyBlock("Terminal", `claude mcp add --transport http sillage ${c.mcp_url} --header "Authorization: Bearer ${k}"`)}</div>
      <div class="card"><h3>Claude Desktop, Cursor and other desktop apps</h3>${copyBlock("Add to the app's MCP config (claude_desktop_config.json for Claude Desktop)", desktop, "Runs Sillage's MCP server locally over stdio against the same database. No key needed, because it never leaves your computer.")}</div>
      <div class="card"><h3>claude.ai and ChatGPT (web connectors)</h3>${copyBlock("Connector URL", `${c.mcp_url}/?key=${k}`, c.public ? "Add it as a custom connector (claude.ai: Settings → Connectors; ChatGPT: Settings → Connectors). The key is in the URL because these apps can't send custom headers, so keep the URL private." : "Needs a public HTTPS deployment first. Once you set SILLAGE_PUBLIC_URL, this URL becomes reachable.")}</div>
      <div class="card"><h3>Custom GPT (ChatGPT Actions)</h3>${copyBlock("Import schema from URL", c.openapi_url, "In the GPT editor: Actions → Import from URL. Authentication: API Key, Auth Type Bearer, paste your key.")}</div>
      <div class="card"><h3>Any app or script (REST)</h3>
        ${copyBlock("Ask the taste model", `curl -s ${c.base_url}/api/tools/taste_profile -H "Authorization: Bearer ${k}" -H "Content-Type: application/json" -d '{}'`)}
        ${copyBlock("Run your own analysis", `curl -s ${c.base_url}/api/sql -H "Authorization: Bearer ${k}" -H "Content-Type: application/json" -d '{"sql": "SELECT note_key(note) AS note, ROUND(AVG(rating),2) AS avg_rating, COUNT(*) AS n FROM v_notes WHERE rating IS NOT NULL GROUP BY 1 HAVING n >= 2 ORDER BY 2 DESC LIMIT 10"}'`)}
        ${copyBlock("Publish a result into the app", `curl -s ${c.base_url}/api/feed -H "Authorization: Bearer ${k}" -H "X-Client-Name: My script" -H "Content-Type: application/json" -d '{"title": "Top notes", "summary": "Vanilla and tonka lead.", "chart": {"type": "bar", "labels": ["Vanilla", "Tonka"], "values": [1.9, 1.7]}}'`)}
      </div>
    </div>`;
  $("#key-show").onclick = (e) => { const shown = e.target.textContent === "Hide"; $("#key-text").textContent = shown ? masked : k; e.target.textContent = shown ? "Show" : "Hide"; };
  $$("[data-copy-text]", root).forEach((b) => (b.onclick = () => copyText(b.dataset.copyText)));
}

/* ================================================================ login */

function showLogin() {
  const box = $("#login");
  if (!box.hidden) return;
  store.set("sillage-key", "");
  API_KEY = null;
  box.hidden = false;
  $("#login-key").focus();
  $("#login-form").onsubmit = (e) => {
    e.preventDefault();
    API_KEY = $("#login-key").value.trim();
    store.set("sillage-key", API_KEY);
    box.hidden = true;
    route();
  };
}

/* ================================================================ tooltip */

const tip = $("#tooltip");
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest?.("[data-tip]");
  if (!el) { tip.hidden = true; return; }
  tip.textContent = "";
  el.dataset.tip.split("\n").forEach((line, i) => {
    if (i) tip.append(document.createElement("br"));
    tip.append(line);
  });
  tip.hidden = false;
  const pad = 14;
  const { width, height } = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + width > innerWidth - 8) x = e.clientX - width - pad;
  if (y + height > innerHeight - 8) y = e.clientY - height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
});

/* ================================================================ theme + boot */

(function theme() {
  const saved = store.get("theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  };
})();

route();
