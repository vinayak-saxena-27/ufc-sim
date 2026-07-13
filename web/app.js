"use strict";
/* UFC Career Sim -- vanilla-JS frontend over the FastAPI /init, /advance, /state
 * endpoints. No build step, no dependencies. Served by api.py's StaticFiles
 * mount at /ui/, so all fetches below use root-relative paths ("/state" etc.)
 * regardless of the page's own /ui/ prefix. */

const TIER_LABELS = {
  tier0: "Amateur",
  tier1: "Regional",
  tier2: "Mid-major",
  tier3: "Top-org btm-15",
  tier4: "Top-org elite",
};

const TIER_ORDER = ["tier4", "tier3", "tier2", "tier1"];

const ATTR_LABELS = {
  wrestling: "Wrestling", bjj: "BJJ", clinch: "Clinch", boxing: "Boxing",
  kickboxing: "Kickboxing", power: "Power", cardio: "Cardio", chin: "Chin",
  athleticism: "Athleticism", fight_iq: "Fight IQ",
};
const ATTR_ORDER = ["wrestling", "bjj", "clinch", "boxing", "kickboxing", "power", "cardio", "chin", "athleticism", "fight_iq"];

let SNAPSHOT = null;
let fightersById = new Map();
let fightersByName = new Map();

// ── Small helpers ────────────────────────────────────────────────────────────

function formatSimDay(day) {
  const year = Math.floor(day / 365) + 1;
  const month = Math.floor((day % 365) / 30) + 1;
  return `Y${year} M${month}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function humanize(key) {
  if (!key) return "—";
  return key.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function tierLabel(tierKey) {
  return TIER_LABELS[tierKey] || humanize(tierKey);
}

function weightClasses() {
  return SNAPSHOT ? Object.keys(SNAPSHOT.elite_rankings) : [];
}

function fighterHref(id) { return "#fighter/" + encodeURIComponent(id); }
function orgHref(name) { return "#org/" + encodeURIComponent(name); }

function fighterLink(id, name) {
  if (!id) return escapeHtml(name || "—");
  return `<a href="${fighterHref(id)}">${escapeHtml(name)}</a>`;
}

function orgLink(name) {
  if (!name) return "—";
  return `<a href="${orgHref(name)}">${escapeHtml(name)}</a>`;
}

function labelsText(labels) {
  return labels && labels.length ? `(${labels.join(", ")})` : "—";
}

function attrBarHtml(key, value) {
  const scale = 70;
  const v = Math.max(-scale, Math.min(scale, value));
  const half = (v / scale) * 50; // -50..50
  const cls = v >= 0 ? "positive" : "negative";
  const left = v >= 0 ? 50 : 50 + half;
  const width = Math.abs(half);
  return `<div class="attr-row">
    <div class="attr-name">${ATTR_LABELS[key]}</div>
    <div class="attr-track"><div class="attr-center"></div><div class="attr-fill ${cls}" style="left:${left}%;width:${width}%;"></div></div>
    <div class="attr-value">${value.toFixed(1)}</div>
  </div>`;
}

// ── API calls ────────────────────────────────────────────────────────────────

function setStatus(msg, ok) {
  const el = document.getElementById("status-line");
  el.textContent = msg || "";
  el.className = ok ? "ok" : "";
}

async function fetchState() {
  const res = await fetch("/state");
  if (res.status === 400) {
    SNAPSHOT = null;
    document.getElementById("day-display").textContent = "";
    document.getElementById("content").innerHTML =
      '<p class="empty-note">No simulation loaded yet — click <strong>Initialize simulation</strong> above to begin.</p>';
    return;
  }
  if (!res.ok) throw new Error(`GET /state failed: ${res.status}`);
  SNAPSHOT = await res.json();
  fightersById = new Map(SNAPSHOT.fighters.map(f => [f.fighter_id, f]));
  fightersByName = new Map(SNAPSHOT.fighters.map(f => [f.name, f]));
  render();
}

async function doInit() {
  const scale = parseFloat(document.getElementById("in-scale").value) || 1.0;
  const seed = parseInt(document.getElementById("in-seed").value, 10) || 42;
  setStatus("Initializing simulation…", true);
  try {
    const res = await fetch("/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scale, seed, debug: false }),
    });
    if (!res.ok) throw new Error(`init failed: ${res.status}`);
    await fetchState();
    setStatus("Simulation initialized.", true);
    checkModalFromHash();
  } catch (err) {
    setStatus(String(err.message || err), false);
  }
}

async function doAdvance(period) {
  setStatus(`Advancing 1 ${period}…`, true);
  try {
    const res = await fetch("/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ period }),
    });
    if (res.status === 400) {
      const body = await res.json().catch(() => ({}));
      setStatus(body.detail || "Simulation not initialized -- click Initialize simulation first.", false);
      return;
    }
    if (!res.ok) throw new Error(`advance failed: ${res.status}`);
    await fetchState();
    setStatus("", true);
    checkModalFromHash();
  } catch (err) {
    setStatus(String(err.message || err), false);
  }
}

// ── Rendering ────────────────────────────────────────────────────────────────

function render() {
  document.getElementById("day-display").textContent =
    `${formatSimDay(SNAPSHOT.current_day)} · ${SNAPSHOT.fighters.length} fighters`;
  const parts = [
    renderRankingsSection(),
    renderTitlesSection(),
    renderOrganizationsSection(),
    renderAcademiesSection(),
    renderFightersSection(),
  ];
  document.getElementById("content").innerHTML = parts.join("\n");
  renderFightersTable();
}

function rankingRowsHtml(entries) {
  return entries.map(e => {
    const f = fightersById.get(e.fighter_id);
    const org = f ? orgLink(f.org) : "—";
    const overall = f ? f.overall.toFixed(1) : "—";
    const record = f ? escapeHtml(f.record.str) : "—";
    const labels = f ? labelsText(f.labels) : "—";
    const scoreTitle = `Win-rate: ${e.win_rate_component.toFixed(2)} · Quality: ${e.quality_component.toFixed(2)} · Hype: ${e.hype_component.toFixed(2)}`;
    return `<tr>
      <td class="num">${e.rank}</td>
      <td>${fighterLink(e.fighter_id, e.fighter_name)}</td>
      <td>${org}</td>
      <td class="num">${overall}</td>
      <td class="num">${record}</td>
      <td class="num" title="${escapeHtml(scoreTitle)}">${e.score.toFixed(1)}</td>
      <td class="labels-cell">${labels}</td>
    </tr>`;
  }).join("\n");
}

function renderRankingsSection() {
  const sections = weightClasses().map(wc => {
    const entries = SNAPSHOT.elite_rankings[wc] || [];
    const body = entries.length
      ? `<table class="wikitable">
          <thead><tr><th>Rank</th><th>Fighter</th><th>Org</th><th class="num">Overall</th><th class="num">Record</th><th class="num">Score</th><th>Labels</th></tr></thead>
          <tbody>${rankingRowsHtml(entries)}</tbody>
        </table>`
      : `<p class="empty-note">No ranked fighters yet in this division.</p>`;
    return `<h3>${humanize(wc)}</h3>${body}`;
  }).join("\n");

  return `<section id="rankings" class="section-block">
    <h2>Elite Rankings</h2>
    <p>Top-tier (Tier 4) fighters ranked by a blend of win rate, opponent quality, and hype, per weight class.</p>
    ${sections}
  </section>`;
}

function renderTitlesSection() {
  const byWc = new Map();
  for (const key of Object.keys(SNAPSHOT.titles)) {
    const t = SNAPSHOT.titles[key];
    if (!t.champion_id) continue;
    if (!byWc.has(t.weight_class)) byWc.set(t.weight_class, []);
    byWc.get(t.weight_class).push(t);
  }

  const sections = weightClasses().map(wc => {
    const list = byWc.get(wc) || [];
    if (!list.length) {
      return `<h3>${humanize(wc)}</h3><p class="empty-note">No titles awarded yet in this division.</p>`;
    }
    list.sort((a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier) || (a.org || "").localeCompare(b.org || ""));
    const rows = list.map(t => `<tr>
        <td>${tierLabel(t.tier)}</td>
        <td>${t.org ? orgLink(t.org) : "—"}</td>
        <td>${fighterLink(t.champion_id, t.champion_name)}</td>
      </tr>`).join("\n");
    return `<h3>${humanize(wc)}</h3>
      <table class="wikitable">
        <thead><tr><th>Tier</th><th>Org</th><th>Champion</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }).join("\n");

  return `<section id="titles" class="section-block">
    <h2>Titles</h2>
    <p>Current champions across every title-bearing tier and organization.</p>
    ${sections}
  </section>`;
}

function renderOrganizationsSection() {
  const groups = ["Top-tier", "Mid-major", "Regional"];
  const orgs = Object.values(SNAPSHOT.organizations);
  const sections = groups.map(group => {
    const list = orgs.filter(o => o.tier_group === group).sort((a, b) => b.prestige - a.prestige);
    const rows = list.map(o => `<tr>
        <td>${orgLink(o.name)}</td>
        <td>${humanize(o.format)}</td>
        <td>${humanize(o.scoring)}</td>
        <td class="num">${o.prestige.toFixed(1)}</td>
      </tr>`).join("\n");
    return `<h3>${group}</h3>
      <table class="wikitable">
        <thead><tr><th>Organization</th><th>Format</th><th>Scoring</th><th class="num">Prestige</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }).join("\n");

  return `<section id="organizations" class="section-block">
    <h2>Organizations</h2>
    <p>Every promotion in the ecosystem, grouped by tier. Click a name for its roster and feeder pipeline.</p>
    ${sections}
  </section>`;
}

function renderAcademiesSection() {
  const fighterCounts = new Map();
  for (const f of SNAPSHOT.fighters) {
    fighterCounts.set(f.academy, (fighterCounts.get(f.academy) || 0) + 1);
  }
  const rows = Object.entries(SNAPSHOT.academy_reputations)
    .sort((a, b) => b[1] - a[1])
    .map(([name, rep], i) => `<tr>
        <td class="num">${i + 1}</td>
        <td>${escapeHtml(name)}</td>
        <td class="num">${rep.toFixed(2)}</td>
        <td class="num">${fighterCounts.get(name) || 0}</td>
      </tr>`).join("\n");

  return `<section id="academies" class="section-block">
    <h2>Academies</h2>
    <p>Training academies ranked by live reputation, which drifts with the on-record performance of the fighters they produce.</p>
    <table class="wikitable">
      <thead><tr><th>Rank</th><th>Academy</th><th class="num">Reputation</th><th class="num">Fighters</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </section>`;
}

// ── All Fighters table (sortable / filterable / searchable) ─────────────────

let fightersSortKey = "overall";
let fightersSortDir = "desc";
let fightersSearch = "";
let fightersWcFilter = "";
let fightersTierFilter = "";

const FIGHTERS_MAX_ROWS = 200;

const FIGHTERS_SORT_ACCESSORS = {
  name:    f => f.name.toLowerCase(),
  overall: f => f.overall,
  hype:    f => f.hype,
  wins:    f => f.record.wins,
  age:     f => f.age,
};

function setFightersSort(key) {
  if (fightersSortKey === key) {
    fightersSortDir = fightersSortDir === "desc" ? "asc" : "desc";
  } else {
    fightersSortKey = key;
    fightersSortDir = "desc";
  }
  renderFightersTable();
}

function onFightersSearchInput(value) {
  fightersSearch = value;
  renderFightersTable();
}

function onFightersFilterChange() {
  fightersWcFilter = document.getElementById("fighters-wc-filter").value;
  fightersTierFilter = document.getElementById("fighters-tier-filter").value;
  renderFightersTable();
}

function fightersFilteredSorted() {
  const q = fightersSearch.trim().toLowerCase();
  let list = SNAPSHOT.fighters.filter(f => {
    if (fightersWcFilter && f.weight_class !== fightersWcFilter) return false;
    if (fightersTierFilter && f.tier !== fightersTierFilter) return false;
    if (q && !f.name.toLowerCase().includes(q)) return false;
    return true;
  });
  const keyFn = FIGHTERS_SORT_ACCESSORS[fightersSortKey] || FIGHTERS_SORT_ACCESSORS.overall;
  list = list.slice().sort((a, b) => {
    const av = keyFn(a), bv = keyFn(b);
    const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
    return fightersSortDir === "desc" ? -cmp : cmp;
  });
  return list;
}

function fightersSortIndicator(key) {
  if (fightersSortKey !== key) return "";
  return fightersSortDir === "desc" ? " ↓" : " ↑";
}

function fightersColHeaderHtml(key, label) {
  return `<th class="sortable-col" onclick="setFightersSort('${key}')">${label}${fightersSortIndicator(key)}</th>`;
}

function renderFightersTable() {
  const list = fightersFilteredSorted();
  const total = list.length;
  const rows = list.slice(0, FIGHTERS_MAX_ROWS).map(f => `<tr>
      <td>${fighterLink(f.fighter_id, f.name)}</td>
      <td>${humanize(f.weight_class)}</td>
      <td>${tierLabel(f.tier)}</td>
      <td>${orgLink(f.org)}</td>
      <td class="num">${f.overall.toFixed(1)}</td>
      <td class="num">${f.hype.toFixed(1)}</td>
      <td class="num">${escapeHtml(f.record.str)}</td>
      <td class="num">${f.age}</td>
    </tr>`).join("\n");

  const table = total
    ? `<p class="muted">Showing ${Math.min(total, FIGHTERS_MAX_ROWS)} of ${total} matching fighters.</p>
      <table class="wikitable">
        <thead><tr>
          ${fightersColHeaderHtml("name", "Name")}
          <th>Weight Class</th>
          <th>Tier</th>
          <th>Org</th>
          ${fightersColHeaderHtml("overall", "Overall")}
          ${fightersColHeaderHtml("hype", "Hype")}
          ${fightersColHeaderHtml("wins", "Record")}
          ${fightersColHeaderHtml("age", "Age")}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`
    : `<p class="empty-note">No fighters match the current filters.</p>`;

  document.getElementById("fighters-table-container").innerHTML = table;
}

function renderFightersSection() {
  const wcOptions = ['<option value="">All weight classes</option>']
    .concat(weightClasses().map(wc => `<option value="${wc}">${humanize(wc)}</option>`))
    .join("");
  const tierOptions = ['<option value="">All tiers</option>']
    .concat(Object.keys(TIER_LABELS).map(t => `<option value="${t}">${TIER_LABELS[t]}</option>`))
    .join("");

  return `<section id="fighters" class="section-block">
    <h2>All Fighters</h2>
    <p>The full roster, searchable and sortable — click a column heading to sort by it.</p>
    <p class="fighters-controls">
      <input type="text" id="fighters-search" placeholder="Search by name…" oninput="onFightersSearchInput(this.value)">
      <select id="fighters-wc-filter" onchange="onFightersFilterChange()">${wcOptions}</select>
      <select id="fighters-tier-filter" onchange="onFightersFilterChange()">${tierOptions}</select>
    </p>
    <div id="fighters-table-container"></div>
  </section>`;
}

// ── Fighter modal ────────────────────────────────────────────────────────────

function opponentCellHtml(opponentName) {
  const opp = fightersByName.get(opponentName);
  if (!opp) return escapeHtml(opponentName);
  return `<a href="#" onclick="navigateModalTo('${opp.fighter_id}'); return false;">${escapeHtml(opponentName)}</a>`;
}

function renderFighterModal(f) {
  const attrRows = ATTR_ORDER.map(k => attrBarHtml(k, f.attributes[k])).join("\n");

  const history = [...f.fight_history].reverse();
  const historyRows = history.length
    ? history.map(r => `<tr>
          <td class="num">${formatSimDay(r.sim_day)}</td>
          <td>${opponentCellHtml(r.opponent_name)}</td>
          <td>${humanize(r.outcome)}</td>
          <td>${humanize(r.method)}</td>
          <td>${r.org ? escapeHtml(r.org) : "—"}</td>
          <td>${tierLabel(r.tier)}</td>
          <td>${r.is_title ? "Title" : "—"}</td>
        </tr>`).join("\n")
    : `<tr><td colspan="7" class="empty-note">No fights on record.</td></tr>`;

  const backLink = modalStack.length
    ? `<p class="modal-back"><a href="#" onclick="modalGoBack(); return false;">← Back</a></p>`
    : "";

  return `${backLink}<h1>${escapeHtml(f.name)}</h1>
    <p class="muted">${labelsText(f.labels)}</p>
    <table class="infobox">
      <caption>${escapeHtml(f.name)}</caption>
      <tbody>
        <tr><th>Age</th><td>${f.age}</td></tr>
        <tr><th>Region</th><td>${escapeHtml(f.region)}</td></tr>
        <tr><th>Style</th><td>${humanize(f.template)}</td></tr>
        <tr><th>Weight class</th><td>${humanize(f.weight_class)}</td></tr>
        <tr><th>Tier</th><td>${tierLabel(f.tier)}</td></tr>
        <tr><th>Organization</th><td>${orgLink(f.org)}</td></tr>
        <tr><th>Academy</th><td>${escapeHtml(f.academy)}</td></tr>
        <tr><th>Prospect tier</th><td>${humanize(f.prospect_tier)}</td></tr>
        <tr><th>Overall</th><td>${f.overall.toFixed(1)}</td></tr>
        <tr><th>Hype</th><td>${f.hype.toFixed(1)}</td></tr>
        <tr><th>Record</th><td>${escapeHtml(f.record.str)}</td></tr>
      </tbody>
    </table>

    <h2>Attributes</h2>
    <div>${attrRows}</div>

    <h2>Fight History</h2>
    <table class="wikitable">
      <thead><tr><th class="num">Day</th><th>Opponent</th><th>Result</th><th>Method</th><th>Org</th><th>Tier</th><th>Title</th></tr></thead>
      <tbody>${historyRows}</tbody>
    </table>`;
}

// ── Org modal ────────────────────────────────────────────────────────────────

function orgRosterEntries(orgName, wc) {
  const fromApi = SNAPSHOT.org_rosters[orgName] && SNAPSHOT.org_rosters[orgName][wc];
  if (fromApi && fromApi.length) return fromApi;
  return SNAPSHOT.fighters
    .filter(f => f.org === orgName && f.weight_class === wc)
    .sort((a, b) => b.overall - a.overall)
    .map((f, i) => ({ rank: i + 1, fighter_id: f.fighter_id, fighter_name: f.name }));
}

function renderOrgModal(org) {
  const feedsTo = org.primary_feeds_to
    ? `${orgLink(org.primary_feeds_to)}${org.secondary_feeds_to ? ` / ${orgLink(org.secondary_feeds_to)}` : ""}`
    : "—";
  const fedBy = org.primary_feed_from.length
    ? org.primary_feed_from.map(orgLink).join(", ")
    : "—";

  const rosterSections = weightClasses().map(wc => {
    const entries = orgRosterEntries(org.name, wc);
    if (!entries.length) return `<h3>${humanize(wc)}</h3><p class="empty-note">No fighters currently in this division.</p>`;
    const rows = entries.map(e => {
      const f = fightersById.get(e.fighter_id);
      return `<tr>
        <td class="num">${e.rank}</td>
        <td>${fighterLink(e.fighter_id, e.fighter_name)}</td>
        <td>${f ? tierLabel(f.tier) : "—"}</td>
        <td class="num">${f ? f.overall.toFixed(1) : "—"}</td>
        <td class="num">${f ? escapeHtml(f.record.str) : "—"}</td>
        <td class="labels-cell">${f ? labelsText(f.labels) : "—"}</td>
      </tr>`;
    }).join("\n");
    return `<h3>${humanize(wc)}</h3>
      <table class="wikitable">
        <thead><tr><th>Rank</th><th>Fighter</th><th>Tier</th><th class="num">Overall</th><th class="num">Record</th><th>Labels</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }).join("\n");

  return `<h1>${escapeHtml(org.name)}</h1>
    <table class="infobox">
      <caption>${escapeHtml(org.name)}</caption>
      <tbody>
        <tr><th>Tier</th><td>${org.tier_group}</td></tr>
        <tr><th>Format</th><td>${humanize(org.format)}</td></tr>
        <tr><th>Scoring</th><td>${humanize(org.scoring)}</td></tr>
        <tr><th>Prestige</th><td>${org.prestige.toFixed(1)}</td></tr>
        <tr><th>Feeds to</th><td>${feedsTo}</td></tr>
        <tr><th>Fed by</th><td>${fedBy}</td></tr>
      </tbody>
    </table>

    <h2>Roster</h2>
    ${rosterSections}`;
}

// ── Modal plumbing (hash-routed, so :visited coloring works natively) ───────
//
// Fighter-modal navigation stack: clicking an opponent link inside the fight
// history pushes the currently-displayed fighter onto modalStack and swaps in
// the opponent, without touching location.hash (so hash-based :visited/back-
// button semantics stay reserved for the original rankings/org-table links).
// A "← Back" link pops the stack. Reset whenever a fresh modal is opened via
// hash navigation or the modal is closed.

let modalStack = [];
let currentModalFighterId = null;

function openModal(html) {
  document.getElementById("modal-content").innerHTML = html;
  document.getElementById("modal-overlay").classList.remove("hidden");
  document.getElementById("modal-box").scrollTop = 0;
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  modalStack = [];
  currentModalFighterId = null;
}

function fighterModalHtmlById(id) {
  currentModalFighterId = id;
  const f = fightersById.get(id);
  return f ? renderFighterModal(f) : `<p class="empty-note">Fighter not found.</p>`;
}

function navigateModalTo(id) {
  if (currentModalFighterId) modalStack.push(currentModalFighterId);
  openModal(fighterModalHtmlById(id));
}

function modalGoBack() {
  if (!modalStack.length) return;
  openModal(fighterModalHtmlById(modalStack.pop()));
}

function checkModalFromHash() {
  const h = location.hash;
  if (!SNAPSHOT) { closeModal(); return; }
  if (h.startsWith("#fighter/")) {
    const id = decodeURIComponent(h.slice("#fighter/".length));
    modalStack = [];
    openModal(fighterModalHtmlById(id));
  } else if (h.startsWith("#org/")) {
    modalStack = [];
    currentModalFighterId = null;
    const name = decodeURIComponent(h.slice("#org/".length));
    const org = SNAPSHOT.organizations[name];
    openModal(org ? renderOrgModal(org) : `<p class="empty-note">Organization not found.</p>`);
  } else {
    closeModal();
  }
}

// ── Wiring ───────────────────────────────────────────────────────────────────

document.getElementById("btn-init").addEventListener("click", e => { e.preventDefault(); doInit(); });
document.getElementById("btn-week").addEventListener("click", e => { e.preventDefault(); doAdvance("week"); });
document.getElementById("btn-month").addEventListener("click", e => { e.preventDefault(); doAdvance("month"); });

document.getElementById("modal-overlay").addEventListener("click", e => {
  if (e.target.id === "modal-overlay") location.hash = "";
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") location.hash = "";
});

window.addEventListener("hashchange", checkModalFromHash);

fetchState().then(checkModalFromHash).catch(err => setStatus(String(err.message || err), false));
