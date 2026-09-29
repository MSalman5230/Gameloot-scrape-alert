"use strict";

// ---------------------------------------------------------------- helpers

const $ = (sel) => document.querySelector(sel);

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Product links come from scraped HTML; esc() doesn't stop a javascript: URL, so allow only http(s).
const safeUrl = (value) => {
  try {
    const url = new URL(value, location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "#";
  } catch {
    return "#";
  }
};

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d) => d.msg).join("; ");
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(message, kind = "error") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4500);
}

const serverOffset = { ms: 0 }; // server clock minus local clock
const now = () => Date.now() + serverOffset.ms;

function fmtSpan(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d`;
}

function fmtRel(iso) {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - now();
  if (Math.abs(diff) < 1500) return "now";
  return diff > 0 ? `in ${fmtSpan(diff)}` : `${fmtSpan(-diff)} ago`;
}

const fmtDuration = (ms) => (ms == null ? "—" : ms < 10000 ? `${(ms / 1000).toFixed(1)}s` : fmtSpan(ms));
const fmtPrice = (n) => (n == null ? "—" : `₹${Number(n).toLocaleString("en-IN")}`);
const fmtAbs = (iso) => (iso ? new Date(iso).toLocaleString() : "");
// Relative times are filled in by tickClocks(), so rendered HTML stays stable between polls.
const rel = (iso) => `<time data-rel="${esc(iso ?? "")}" title="${esc(fmtAbs(iso))}"></time>`;
const badge = (status) => (status ? `<span class="badge ${esc(status)}">${esc(status)}</span>` : `<span class="muted">—</span>`);

function tickClocks(root = document) {
  for (const el of root.querySelectorAll("[data-rel]")) el.textContent = fmtRel(el.dataset.rel || null);
  for (const el of root.querySelectorAll("[data-elapsed]")) el.textContent = fmtSpan(now() - new Date(el.dataset.elapsed));
}

/** Replace innerHTML only when it changed and the user isn't typing inside the element. */
function render(el, html) {
  if (el._html === html) return;
  const active = document.activeElement;
  if (active && el.contains(active) && active.matches("input:not([type=checkbox]), select, textarea")) return;
  el._html = html;
  el.innerHTML = html;
  tickClocks(el);
}

// ---------------------------------------------------------------- state

const state = {
  tab: "overview",
  status: null,
  sites: [],
  products: { page: 1, pageSize: 50 },
};

const categoryById = () => {
  const map = new Map();
  for (const site of state.sites) for (const c of site.categories) map.set(c.id, { ...c, siteName: site.name });
  return map;
};

// ---------------------------------------------------------------- rendering

function renderHeader() {
  const engine = state.status.engine;
  const pill = $("#engine-state");
  pill.className = `pill ${engine.paused ? "paused" : "live"}`;
  pill.textContent = engine.paused ? "Paused" : "Scheduler running";
  const pauseBtn = $("#pause-btn");
  pauseBtn.textContent = engine.paused ? "Resume" : "Pause";
  pauseBtn.classList.toggle("primary", engine.paused);
  const limit = $("#limit");
  if (document.activeElement !== limit) limit.value = engine.max_concurrent_runs;
}

function renderStats() {
  const { products, runs_24h: runs, engine } = state.status;
  const ok = runs.success ?? 0;
  const failed = runs.failed ?? 0;
  const tile = (label, value, sub = "", cls = "") =>
    `<div class="stat ${cls}"><div class="stat-label">${label}</div><div class="stat-value">${value}</div><div class="stat-sub">${sub}</div></div>`;
  render($("#stats"), [
    tile("Active runs", `${engine.running.length}<span class="of">/${engine.max_concurrent_runs}</span>`,
      `${engine.queued.length} queued`),
    tile("In stock", products.in_stock.toLocaleString(), `${products.total.toLocaleString()} tracked`),
    tile("Runs · 24h", ok.toLocaleString(), "succeeded"),
    tile("Failures · 24h", failed.toLocaleString(), failed ? "check run history" : "all good", failed ? "bad" : ""),
  ].join(""));
}

function renderSlots() {
  const { running, max_concurrent_runs: max } = state.status.engine;
  const slots = [];
  for (let i = 0; i < Math.max(max, running.length); i++) {
    const job = running[i];
    slots.push(`<span class="slot ${job ? "on" : ""} ${i >= max ? "over" : ""}" title="${job ? esc(job.name) : "free"}"></span>`);
  }
  render($("#slots"), slots.join(""));
}

function renderActivity() {
  const { running, queued } = state.status.engine;
  const cats = categoryById();
  const label = (j) => `<strong>${esc(cats.get(j.category_id)?.siteName ?? j.site)}</strong> · ${esc(j.name)}`;

  const runningHtml = running.length
    ? running.map((j) => `
      <li class="job running">
        <div class="job-main">
          <span class="spinner" aria-hidden="true"></span>
          <div><div>${label(j)}</div>
            <div class="muted small">${esc(j.phase)} · page ${j.pages} · ${j.items} items · ${j.trigger}</div></div>
        </div>
        <div class="job-side">
          <time class="mono" data-elapsed="${esc(j.started_at)}"></time>
          <button class="btn small ghost" data-action="cancel" data-run="${esc(j.run_id)}"
            ${j.phase !== "scraping" ? "disabled title='Saving results'" : ""}>Cancel</button>
        </div>
      </li>`).join("")
    : `<li class="empty">Nothing running</li>`;

  const queuedHtml = queued.length
    ? queued.map((j) => `
      <li class="job">
        <div class="job-main"><span class="dot" aria-hidden="true"></span>
          <div><div>${label(j)}</div>
            <div class="muted small">${j.trigger} · queued ${rel(j.queued_at)}</div></div></div>
        <div class="job-side">
          <span class="badge waiting">${esc(j.waiting)}</span>
          <button class="btn small ghost" data-action="cancel" data-run="${esc(j.run_id)}">Remove</button>
        </div>
      </li>`).join("")
    : `<li class="empty">Queue is empty</li>`;

  render($("#activity"), `
    <div><h3>Running</h3><ul class="jobs">${runningHtml}</ul></div>
    <div><h3>Queued</h3><ul class="jobs">${queuedHtml}</ul></div>`);
}

function renderSites() {
  const busy = new Set(state.status.engine.busy_sites);
  const html = state.sites.map((site) => {
    const rows = site.categories.map((c) => {
      const status = c.active ?? c.last_status;
      const err = !c.active && c.last_error ? `<div class="err small" title="${esc(c.last_error)}">${esc(c.last_error)}</div>` : "";
      const action = c.active
        ? `<button class="btn small ghost" data-action="cancel" data-run="${esc(c.active_run_id)}">Cancel</button>`
        : `<button class="btn small" data-action="run" data-id="${esc(c.id)}">Run now</button>`;
      return `
        <tr class="${c.enabled ? "" : "disabled"}">
          <td data-label="Category"><div class="strong">${esc(c.name)}</div>
            <a class="muted small" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.key)} ↗</a></td>
          <td data-label="Enabled"><label class="switch"><input type="checkbox" data-action="toggle-cat" data-id="${esc(c.id)}"
            ${c.enabled ? "checked" : ""} aria-label="Enable ${esc(c.name)}"><span></span></label></td>
          <td data-label="Every"><span class="interval"><input type="number" min="1" max="1440" value="${c.interval_minutes}"
            data-action="interval" data-id="${esc(c.id)}" aria-label="Interval in minutes"><span class="muted">min</span></span></td>
          <td data-label="Status">${badge(status)}${err}</td>
          <td data-label="Last run">${rel(c.last_run_at)}<div class="muted small">${fmtDuration(c.last_duration_ms)}</div></td>
          <td data-label="Items" class="num">${c.last_items ?? "—"}</td>
          <td data-label="Next run">${c.enabled ? (c.active ? `<span class="muted">${c.active}</span>` : rel(c.next_run_at)) : `<span class="muted">disabled</span>`}</td>
          <td class="actions">${action}</td>
        </tr>`;
    }).join("");
    return `
      <div class="site">
        <div class="site-head">
          <h3>${esc(site.name)}</h3>
          <a class="muted small" href="${esc(site.base_url)}" target="_blank" rel="noopener">${esc(site.base_url.replace(/^https?:\/\//, ""))}</a>
          <span class="badge ${busy.has(site.key) ? "running" : "idle"}">${busy.has(site.key) ? "busy" : "idle"}</span>
        </div>
        <table class="table stack">
          <thead><tr><th>Category</th><th>Enabled</th><th>Every</th><th>Status</th><th>Last run</th>
            <th class="num">Items</th><th>Next run</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }).join("");
  render($("#sites"), html || `<p class="empty">No sites registered.</p>`);
}

function runsTable(runs, { compact = false } = {}) {
  if (!runs.length) return `<p class="empty">No runs yet.</p>`;
  const cats = categoryById();
  const rows = runs.map((r) => {
    const cat = cats.get(r.category_id);
    const changes = r.status === "success"
      ? (r.baseline
          ? `<span class="tag">baseline</span>`
          : [r.new && `<span class="chg new">+${r.new} new</span>`, r.restocked && `<span class="chg new">↻${r.restocked}</span>`,
             r.sold && `<span class="chg sold">−${r.sold} sold</span>`, r.price_changed && `<span class="chg">Δ${r.price_changed} price</span>`]
             .filter(Boolean).join(" ") || `<span class="muted">no changes</span>`)
      : "";
    const error = r.error || r.notify_error;
    return `
      <tr>
        <td data-label="When">${rel(r.started_at ?? r.queued_at)}</td>
        <td data-label="Category"><span class="strong">${esc(cat?.name ?? r.category)}</span>
          <span class="muted small">${esc(cat?.siteName ?? r.site)}</span></td>
        <td data-label="Status">${badge(r.status)}${r.trigger === "manual" ? ` <span class="tag">manual</span>` : ""}</td>
        <td data-label="Duration" class="num">${fmtDuration(r.duration_ms)}</td>
        ${compact ? "" : `<td data-label="Pages" class="num">${r.pages}</td>`}
        <td data-label="Items" class="num">${r.items}</td>
        <td data-label="Changes">${changes}${error ? `<div class="err small" title="${esc(error)}">${esc(error)}</div>` : ""}</td>
      </tr>`;
  }).join("");
  return `<table class="table stack">
    <thead><tr><th>When</th><th>Category</th><th>Status</th><th class="num">Duration</th>
      ${compact ? "" : `<th class="num">Pages</th>`}<th class="num">Items</th><th>Changes</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function fillCategorySelects() {
  for (const id of ["#runs-category", "#products-category"]) {
    const select = $(id);
    const options = state.sites.flatMap((s) =>
      s.categories.map((c) => `<option value="${esc(c.id)}">${esc(s.name)} · ${esc(c.name)}</option>`));
    const html = `<option value="">All categories</option>${options.join("")}`;
    if (select._html !== html) {
      const value = select.value;
      select.innerHTML = html;
      select._html = html;
      select.value = value;
    }
  }
}

function renderProducts(data) {
  const cats = categoryById();
  if (!data.items.length) {
    render($("#products"), `<p class="empty">No products match.</p>`);
  } else {
    const rows = data.items.map((p) => {
      const cat = cats.get(`${p.site}:${p.category}`);
      const history = p.price_history ?? [];
      const prev = [...history].reverse().find((h) => h.price !== p.price)?.price;
      const trend = prev == null ? "" : prev > p.price
        ? `<span class="chg new" title="was ${fmtPrice(prev)}">▼ ${fmtPrice(prev)}</span>`
        : `<span class="chg sold" title="was ${fmtPrice(prev)}">▲ ${fmtPrice(prev)}</span>`;
      return `
        <tr class="${p.in_stock ? "" : "disabled"}">
          <td data-label="Product"><a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener" class="strong">${esc(p.name)}</a></td>
          <td data-label="Category" class="muted nowrap">${esc(cat ? `${cat.siteName} · ${cat.name}` : `${p.site} · ${p.category}`)}</td>
          <td data-label="Price" class="num"><span class="mono">${fmtPrice(p.price)}</span> ${trend}</td>
          <td data-label="Stock">${p.in_stock ? `<span class="badge success">in stock</span>` : `<span class="badge sold">sold</span>`}</td>
          <td data-label="First seen">${rel(p.first_seen_at)}</td>
          <td data-label="Last seen">${rel(p.in_stock ? p.last_seen_at : p.sold_at ?? p.last_seen_at)}</td>
        </tr>`;
    }).join("");
    render($("#products"), `<table class="table stack">
      <thead><tr><th>Product</th><th>Category</th><th class="num">Price</th><th>Stock</th><th>First seen</th><th>Last seen</th></tr></thead>
      <tbody>${rows}</tbody></table>`);
  }
  const { page, page_size: size, total } = data;
  const from = total ? (page - 1) * size + 1 : 0;
  const to = Math.min(page * size, total);
  render($("#products-pager"), `
    <span class="muted">${from}–${to} of ${total.toLocaleString()}</span>
    <button class="btn small ghost" data-action="page" data-step="-1" ${page <= 1 ? "disabled" : ""}>← Prev</button>
    <button class="btn small ghost" data-action="page" data-step="1" ${to >= total ? "disabled" : ""}>Next →</button>`);
}

// ---------------------------------------------------------------- data loading

// Poll fast only while something is queued or running; the numbers can't change otherwise.
const POLL_ACTIVE_MS = 3000;
const POLL_IDLE_MS = 15000;
let refreshing = false;
let refreshAgain = false;
let pollTimer;

function schedulePoll() {
  clearTimeout(pollTimer);
  const engine = state.status?.engine;
  const busy = engine && (engine.running.length || engine.queued.length);
  pollTimer = setTimeout(() => (document.hidden ? schedulePoll() : refresh()), busy ? POLL_ACTIVE_MS : POLL_IDLE_MS);
}

async function refresh() {
  if (refreshing) {
    // A poll already in flight may predate an action the user just took; refresh again after it.
    refreshAgain = true;
    return;
  }
  refreshing = true;
  try {
    const [status, sites] = await Promise.all([api("/status"), api("/sites")]);
    serverOffset.ms = new Date(status.server_time).getTime() - Date.now();
    state.status = status;
    state.sites = sites;
    document.body.classList.remove("offline");
    renderHeader();
    fillCategorySelects();
    if (state.tab === "overview") {
      renderStats();
      renderSlots();
      renderActivity();
      renderSites();
      render($("#recent-runs"), runsTable(status.recent_runs, { compact: true }));
    } else if (state.tab === "runs") {
      await loadRuns();
    }
  } catch (err) {
    document.body.classList.add("offline");
    console.error(err);
  } finally {
    refreshing = false;
    schedulePoll();
  }
  if (refreshAgain) {
    refreshAgain = false;
    await refresh();
  }
}

async function loadRuns() {
  const params = new URLSearchParams({ limit: "200" });
  if ($("#runs-category").value) params.set("category_id", $("#runs-category").value);
  if ($("#runs-status").value) params.set("status", $("#runs-status").value);
  render($("#runs"), runsTable(await api(`/runs?${params}`)));
}

async function loadProducts() {
  const [site, category] = ($("#products-category").value || ":").split(":");
  const params = new URLSearchParams({
    page: String(state.products.page),
    page_size: String(state.products.pageSize),
    sort: $("#products-sort").value,
  });
  if (site) params.set("site", site);
  if (category) params.set("category", category);
  if ($("#products-stock").value) params.set("in_stock", $("#products-stock").value);
  if ($("#products-q").value.trim()) params.set("q", $("#products-q").value.trim());
  try {
    renderProducts(await api(`/products?${params}`));
  } catch (err) {
    toast(err.message);
  }
}

// ---------------------------------------------------------------- actions

async function act(promise, success) {
  try {
    await promise;
    if (success) toast(success, "ok");
  } catch (err) {
    toast(err.message);
  }
  await refresh();
}

async function setLimit(value) {
  const n = Math.min(50, Math.max(1, Math.round(Number(value) || 1)));
  $("#limit").value = n;
  if (n === state.status?.engine.max_concurrent_runs) return;
  await act(api("/settings", { method: "PATCH", body: { max_concurrent_runs: n } }), `Max active runs set to ${n}`);
}

document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-action], [data-tab]");
  if (!el || el.disabled) return;
  const { action } = el.dataset;
  if (!action && el.dataset.tab) return showTab(el.dataset.tab);
  switch (action) {
    case "goto":
      return showTab(el.dataset.tab);
    case "toggle-pause": {
      const paused = !state.status.engine.paused;
      return act(api("/settings", { method: "PATCH", body: { paused } }), paused ? "Scheduler paused" : "Scheduler resumed");
    }
    case "limit-step":
      return setLimit(Number($("#limit").value) + Number(el.dataset.step));
    case "run":
      el.disabled = true;
      return act(api(`/categories/${encodeURIComponent(el.dataset.id)}/run`, { method: "POST" }), "Run queued");
    case "cancel":
      el.disabled = true;
      return act(api(`/runs/${encodeURIComponent(el.dataset.run)}/cancel`, { method: "POST" }), "Cancelled");
    case "page":
      state.products.page += Number(el.dataset.step);
      return loadProducts();
  }
});

document.addEventListener("change", (event) => {
  const el = event.target;
  const id = el.dataset?.id;
  if (el.dataset.action === "toggle-cat") {
    act(api(`/categories/${encodeURIComponent(id)}`, { method: "PATCH", body: { enabled: el.checked } }));
  } else if (el.dataset.action === "interval") {
    const minutes = Math.round(Number(el.value));
    act(api(`/categories/${encodeURIComponent(id)}`, { method: "PATCH", body: { interval_minutes: minutes } }),
      `Interval set to ${minutes} min`);
    el.blur();
  } else if (el.id === "limit") {
    setLimit(el.value);
  } else if (el.id.startsWith("runs-")) {
    loadRuns();
  } else if (el.id.startsWith("products-")) {
    state.products.page = 1;
    loadProducts();
  }
});

let searchTimer;
$("#products-q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.products.page = 1;
    loadProducts();
  }, 300);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches("input[type=number]")) event.target.blur();
});

function showTab(tab) {
  if (!["overview", "runs", "products"].includes(tab)) tab = "overview";
  state.tab = tab;
  for (const btn of document.querySelectorAll(".tabs [data-tab]")) btn.setAttribute("aria-selected", btn.dataset.tab === tab);
  for (const panel of document.querySelectorAll(".tab-panel")) panel.hidden = panel.id !== `tab-${tab}`;
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
  if (tab === "products") loadProducts();
  refresh();
}

// Live clocks without re-rendering tables.
setInterval(() => tickClocks(), 1000);

document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

window.addEventListener("hashchange", () => {
  if (location.hash.slice(1) !== state.tab) showTab(location.hash.slice(1));
});

showTab(location.hash.slice(1));
