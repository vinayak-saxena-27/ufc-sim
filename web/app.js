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

// ── Events / fight cards ─────────────────────────────────────────────────────

// Apex FC only, per user's explicit choice -- other orgs keep flat sequential
// "Org N" numbering. Real MMA convention: numbered flagship events (title
// fights, "Apex FC 47") vs. named Fight Nights for everything else. event.is_major
// / event.major_number come from api.py's _build_events_by_org.
const APEX_FC_NAME = "Apex FC";

function eventTitle(orgName, event) {
  if (orgName === APEX_FC_NAME && !event.is_major) {
    const main = event.bouts[0];
    return `${orgName} Fight Night: ${main.fighter_a_name} vs. ${main.fighter_b_name}`;
  }
  const number = (orgName === APEX_FC_NAME && event.is_major) ? event.major_number : event.number;
  return `${orgName} ${number}`;
}

function eventHref(org, number) { return "#event/" + encodeURIComponent(org) + "/" + number; }

function fighterIdByName(name) {
  const f = fightersByName.get(name);
  return f ? f.fighter_id : null;
}

function recentEventsForOrg(orgName) {
  return (SNAPSHOT.events && SNAPSHOT.events[orgName]) || [];
}

function isLeagueOrg(org) { return org.format === "tournament"; }

function billingLabel(idx) {
  if (idx === 0) return "Main event";
  if (idx === 1) return "Co-main";
  if (idx === 2) return "Featured";
  return "Prelim";
}

function methodText(b) {
  return b.method === "decision"
    ? `Decision · ${b.rounds_completed} Rds`
    : `${humanize(b.method)} · Rd ${b.rounds_completed}`;
}

function initials(name) {
  return (name || "").split(/\s+/).filter(Boolean).map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

// Recent-event teaser strip used by both top-tier and mid-major org cards.
function eventTeaserHtml(orgName) {
  const events = recentEventsForOrg(orgName);
  if (!events.length) return `<p class="empty-note">No numbered events on record yet.</p>`;
  const e = events[0];
  const main = e.bouts[0];
  const mainLine = main
    ? `<div class="et-bout">${fighterLink(fighterIdByName(main.winner_name), main.winner_name)} def. ${escapeHtml(main.loser_name)} <span class="muted">— ${methodText(main)}</span></div>`
    : "";
  return `<div class="event-teaser">
    <div class="et-label">Most recent &middot; ${escapeHtml(eventTitle(orgName, e))}</div>
    ${mainLine}
    <div class="et-more"><a href="${eventHref(orgName, e.number)}">Full card (${e.bouts.length} bouts) &rarr;</a></div>
  </div>`;
}

// The League runs season/playoffs, not numbered event cards -- teaser shows
// the most recently completed season per weight class instead.
function mostRecentLeagueSeasons() {
  const byWc = new Map();
  for (const s of (SNAPSHOT.league_seasons || [])) {
    const cur = byWc.get(s.weight_class);
    if (!cur || cur.season_number < s.season_number) byWc.set(s.weight_class, s);
  }
  return byWc;
}

function leagueTeaserHtml() {
  const byWc = mostRecentLeagueSeasons();
  if (!byWc.size) return `<p class="empty-note">No completed seasons yet.</p>`;
  return weightClasses().filter(wc => byWc.has(wc)).map(wc => {
    const s = byWc.get(wc);
    const line = s.champion_name
      ? `<span class="ls-champ">${escapeHtml(s.champion_name)}</span> crowned Season ${s.season_number} champion`
      : `Season ${s.season_number} ended without a crowned champion`;
    return `<div class="event-teaser"><div class="et-label">${humanize(wc)} &middot; Most recent season</div><div class="et-bout">${line}</div></div>`;
  }).join("\n");
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
    renderOrganizationsSection(),
    renderAcademiesSection(),
    renderFightersSection(),
  ];
  document.getElementById("content").innerHTML = parts.join("\n");
  renderFightersTable();
}

function renderOrganizationsSection() {
  const orgs = Object.values(SNAPSHOT.organizations);

  // Information density scales down with real-world prominence: top-tier
  // orgs get a rich card (recent-event teaser + champion + full ranked
  // roster per weight class), mid-major gets a lighter card (teaser only,
  // full roster is one click away via the org page), regional stays a
  // plain compact directory.
  const topTier = orgs.filter(o => o.tier_group === "Top-tier").sort((a, b) => b.prestige - a.prestige);
  const topTierCards = topTier.map(org => {
    const teaser = isLeagueOrg(org) ? leagueTeaserHtml() : eventTeaserHtml(org.name);
    const wcBlocks = weightClasses().map(wc => {
      const t = titleEntry(wc, "tier4", org.name);
      const champLine = t && t.champion_id
        ? `<p><strong>Champion:</strong> ${fighterLink(t.champion_id, t.champion_name)}</p>`
        : `<p class="empty-note">Title vacant</p>`;
      return `<h4>${humanize(wc)}</h4>${champLine}
        <details class="roster-dropdown">
          <summary>Top 15 ranked</summary>
          ${orgRosterTableHtml(org.name, wc)}
        </details>`;
    }).join("\n");
    return `<div class="org-card org-card--top">
      <div class="org-card-head">
        <h3>${orgLink(org.name)}</h3>
        <span class="org-card-meta">${humanize(org.format)} &middot; ${humanize(org.scoring)} &middot; Prestige ${org.prestige.toFixed(1)}</span>
      </div>
      <div class="org-card-body">
        ${teaser}
        ${wcBlocks}
      </div>
    </div>`;
  }).join("\n");

  const midTier = orgs.filter(o => o.tier_group === "Mid-major").sort((a, b) => b.prestige - a.prestige);
  const midTierCards = midTier.map(org => `<div class="org-card org-card--mid">
      <div class="org-card-head">
        <h3>${orgLink(org.name)}</h3>
        <span class="org-card-meta">Prestige ${org.prestige.toFixed(1)}</span>
      </div>
      <div class="org-card-body">
        ${eventTeaserHtml(org.name)}
      </div>
    </div>`).join("\n");

  const regional = orgs.filter(o => o.tier_group === "Regional").sort((a, b) => b.prestige - a.prestige);
  const regionalRows = regional.map(o => `<tr>
      <td>${orgLink(o.name)}</td>
      <td>${humanize(o.format)}</td>
      <td>${humanize(o.scoring)}</td>
      <td class="num">${o.prestige.toFixed(1)}</td>
    </tr>`).join("\n");

  return `<section id="organizations" class="section-block">
    <h2>Organizations</h2>
    <p>Top-tier orgs shown with a recent-event teaser, current champion, and full ranked roster per weight class. Mid-major orgs get a lighter card with just their most recent card. Regional orgs are a plain directory below — click a name for its roster and feeder pipeline.</p>
    <h3>Top-tier</h3>
    ${topTierCards}
    <h3>Mid-major</h3>
    <div class="org-cards-grid">${midTierCards}</div>
    <h3>Regional</h3>
    <div class="regional-directory">
      <table class="wikitable">
        <thead><tr><th>Organization</th><th>Format</th><th>Scoring</th><th class="num">Prestige</th></tr></thead>
        <tbody>${regionalRows}</tbody>
      </table>
    </div>
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
  // Rehydrate the controls from the persisted filter state -- render() runs
  // on every /advance and used to recreate these controls EMPTY while the
  // persisted fightersSearch/fightersWcFilter/fightersTierFilter state kept
  // filtering the table, so after advancing, the table stayed filtered by a
  // query the controls no longer displayed.
  const sel = (v, cur) => (v === cur ? " selected" : "");
  const wcOptions = [`<option value=""${sel("", fightersWcFilter)}>All weight classes</option>`]
    .concat(weightClasses().map(wc => `<option value="${wc}"${sel(wc, fightersWcFilter)}>${humanize(wc)}</option>`))
    .join("");
  const tierOptions = [`<option value=""${sel("", fightersTierFilter)}>All tiers</option>`]
    .concat(Object.keys(TIER_LABELS).map(t => `<option value="${t}"${sel(t, fightersTierFilter)}>${TIER_LABELS[t]}</option>`))
    .join("");

  return `<section id="fighters" class="section-block">
    <h2>All Fighters</h2>
    <p>The full roster, searchable and sortable — click a column heading to sort by it.</p>
    <p class="fighters-controls">
      <input type="text" id="fighters-search" placeholder="Search by name…" value="${escapeHtml(fightersSearch)}" oninput="onFightersSearchInput(this.value)">
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

// Shared by the org modal's full roster and the main page's inline top-tier
// org blocks (renderOrganizationsSection) -- same table shape, one org+wc at a time.
function orgRosterTableHtml(orgName, wc) {
  const entries = orgRosterEntries(orgName, wc);
  if (!entries.length) return `<p class="empty-note">No fighters currently in this division.</p>`;
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
  return `<table class="wikitable">
    <thead><tr><th>Rank</th><th>Fighter</th><th>Tier</th><th class="num">Overall</th><th class="num">Record</th><th>Labels</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// titles is keyed "{wc}|{tier}|{org-or-'-'}" (api.py::_build_snapshot).
function titleEntry(wc, tier, org) {
  return SNAPSHOT.titles[`${wc}|${tier}|${org || "-"}`];
}

function renderRecentEventsList(orgName) {
  const events = recentEventsForOrg(orgName);
  if (!events.length) return "";
  const items = events.map(e => {
    const main = e.bouts[0];
    const mainTxt = main ? `${escapeHtml(main.winner_name)} def. ${escapeHtml(main.loser_name)}` : "";
    return `<li><a href="${eventHref(orgName, e.number)}">${escapeHtml(eventTitle(orgName, e))}</a> — ${mainTxt} <span class="muted">(day ${e.scheduled_day})</span></li>`;
  }).join("\n");
  return `<h2>Recent Events</h2><ul>${items}</ul>`;
}

function renderLeagueSeasonsBlock() {
  const seasons = SNAPSHOT.league_seasons || [];
  if (!seasons.length) return "";
  const items = seasons.slice(0, 12).map(s => {
    const champ = s.champion_name
      ? `<span class="ls-champ">${escapeHtml(s.champion_name)}</span>`
      : "No champion crowned";
    return `<div class="league-season"><div class="ls-title">${humanize(s.weight_class)} &middot; Season ${s.season_number}</div>${champ}</div>`;
  }).join("\n");
  return `<h2>Recent Seasons</h2>${items}`;
}

function renderOrgModal(org) {
  const feedsTo = org.primary_feeds_to
    ? `${orgLink(org.primary_feeds_to)}${org.secondary_feeds_to ? ` / ${orgLink(org.secondary_feeds_to)}` : ""}`
    : "—";
  const fedBy = org.primary_feed_from.length
    ? org.primary_feed_from.map(orgLink).join(", ")
    : "—";

  const rosterSections = weightClasses().map(wc =>
    `<h3>${humanize(wc)}</h3>${orgRosterTableHtml(org.name, wc)}`
  ).join("\n");

  const historyBlock = isLeagueOrg(org) ? renderLeagueSeasonsBlock() : renderRecentEventsList(org.name);

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

    ${historyBlock}

    <h2>Roster</h2>
    ${rosterSections}`;
}

// ── Event / fight-card modal (broadcast treatment) ──────────────────────────

function eventBoutRowHtml(b, idx) {
  const winnerLink = fighterLink(fighterIdByName(b.winner_name), b.winner_name);
  const billingCls = idx === 0 ? "ec-billing ec-main" : "ec-billing";
  const titleTag = b.is_title ? ` <span class="muted">(Title fight)</span>` : "";
  return `<div class="ec-row">
    <div class="${billingCls}">${billingLabel(idx)}</div>
    <div class="ec-matchup-txt"><span class="ec-winner">${winnerLink}</span> def. ${escapeHtml(b.loser_name)}${titleTag} <span class="ec-wc">&middot; ${humanize(b.weight_class)}</span></div>
    <div class="ec-method-txt">${methodText(b)}</div>
  </div>`;
}

function renderEventModal(orgName, number) {
  const events = recentEventsForOrg(orgName);
  const event = events.find(e => e.number === number);
  if (!event || !event.bouts.length) {
    return `<div class="ec-back"><a href="${orgHref(orgName)}">&larr; ${escapeHtml(orgName)}</a></div>
      <p class="empty-note" style="padding:1.4em;">Event not found — it may have aged out of the recent-events window.</p>`;
  }

  const main = event.bouts[0];
  const aIsWinner = main.winner_name === main.fighter_a_name;
  const aId = fighterIdByName(main.fighter_a_name);
  const bId = fighterIdByName(main.fighter_b_name);

  const rows = event.bouts.map((b, i) => eventBoutRowHtml(b, i)).join("\n");

  return `<div class="ec-back"><a href="${orgHref(orgName)}">&larr; ${escapeHtml(orgName)}</a></div>
    <div class="ec-hero">
      <div class="ec-tag">${humanize(main.weight_class)}${main.is_title ? " Championship" : ""}</div>
      <div class="ec-num">${escapeHtml(eventTitle(orgName, event))}</div>
      <div class="ec-sub">Sim day ${event.scheduled_day} &middot; ${event.bouts.length}-bout card</div>
      <div class="ec-matchup">
        <div class="ec-fighter${aIsWinner && main.is_title ? " ec-champ" : ""}">
          ${main.is_title ? `<div class="ec-belt">${aIsWinner ? "Champion" : "Challenger"}</div>` : ""}
          <div class="ec-silhouette">${initials(main.fighter_a_name)}</div>
          <div class="ec-name">${fighterLink(aId, main.fighter_a_name)}</div>
        </div>
        <div class="ec-vs">VS</div>
        <div class="ec-fighter${!aIsWinner && main.is_title ? " ec-champ" : ""}">
          ${main.is_title ? `<div class="ec-belt">${!aIsWinner ? "Champion" : "Challenger"}</div>` : ""}
          <div class="ec-silhouette">${initials(main.fighter_b_name)}</div>
          <div class="ec-name">${fighterLink(bId, main.fighter_b_name)}</div>
        </div>
      </div>
      <div class="ec-result-strip">
        <span class="ec-win-tag">${escapeHtml(main.winner_name)} wins</span><span class="ec-dot">&middot;</span>
        <span class="ec-method">${methodText(main)}</span>
      </div>
    </div>
    <div class="ec-section">
      <h2>Full card</h2>
      <div class="ec-list">${rows}</div>
    </div>
    <div class="ec-footer">${escapeHtml(eventTitle(orgName, event))} &middot; UFC Career Sim</div>`;
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

function openModal(html, extraClass) {
  document.getElementById("modal-content").innerHTML = html;
  document.getElementById("modal-overlay").classList.remove("hidden");
  const box = document.getElementById("modal-box");
  box.className = extraClass || "";
  box.scrollTop = 0;
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  document.getElementById("modal-box").className = "";
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
  } else if (h.startsWith("#event/")) {
    modalStack = [];
    currentModalFighterId = null;
    const rest = decodeURIComponent(h.slice("#event/".length));
    const cut = rest.lastIndexOf("/");
    const orgName = rest.slice(0, cut);
    const number = parseInt(rest.slice(cut + 1), 10);
    openModal(renderEventModal(orgName, number), "event-modal");
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

// Close the modal WITHOUT `location.hash = ""` -- assigning an empty hash
// makes the browser scroll the document to the top, so returning from a
// fighter/org detail view dumped the user back at the top of the page
// instead of where they left the list. pushState strips the hash without
// scrolling and without firing hashchange (so we invoke the modal check
// ourselves), while keeping a history entry so the back button still
// reopens the modal exactly like the old hash-clearing flow did.
function closeModalPreservingScroll() {
  if (!location.hash) return;
  const x = window.scrollX, y = window.scrollY;
  history.pushState("", document.title, location.pathname + location.search);
  checkModalFromHash();
  window.scrollTo(x, y);
}

document.getElementById("modal-overlay").addEventListener("click", e => {
  if (e.target.id === "modal-overlay") closeModalPreservingScroll();
});
// The x button is an <a href="#"> -- without this handler it navigated to the
// empty fragment, which browsers treat as "scroll to top" (same bug family as
// the old location.hash = "" close path).
document.getElementById("modal-close").addEventListener("click", e => {
  e.preventDefault();
  closeModalPreservingScroll();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeModalPreservingScroll();
});

window.addEventListener("hashchange", checkModalFromHash);

fetchState().then(checkModalFromHash).catch(err => setStatus(String(err.message || err), false));
