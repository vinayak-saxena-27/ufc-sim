# Historical Archive Design (design-only, not implemented)

Status: **design document only**, produced as Section 4 of the
matchmaking-improvement Phase 2 session. No code in this document has been
written; this is a proposal with explicitly flagged open questions, for a
future session to implement (or revise) against.

## 1. Problem statement

The sim currently has no durable, queryable record of a fighter or title
reign once that fighter stops being active. Org-level belt state (current
champion) and champion pinning in rankings already exist
(`career/labels.py`'s title registry, `career/org_rankings.py`'s pinned-#1
champion), but there is nothing that answers "who held this belt before the
current champion" or "what did this retired fighter's career look like."

**Correcting an assumption from prior project memory before designing
around it:** the name-recycling system (`career/academies.py::recycle_names`,
`_retired_names`) does **not** keep a retired fighter's record queryable. It
only holds their **name string** in a cooldown queue
(`NAME_RECYCLE_COOLDOWN_DAYS`) so a new prospect can't reuse it too soon,
checked against a `referenced_names` set built fresh each sweep from
currently-active fighters' own names and their opponents' names. The
`Fighter` object itself -- full `fight_history`, attributes, hype, org
history, everything -- is not retained anywhere once it's dropped from
`all_fighters`/`pools` (every retirement/cut call site does the same
`all_fighters[:] = [f for f in all_fighters if f is not rf]` pattern, and
nothing else holds a reference). **There is no existing durability to
piggyback on.** An archive needs new storage, not a query layer over
something that already persists.

## 2. What "notable" should mean

Three options, with trade-offs:

**(a) Everyone.** Archive every fighter that ever existed, retired or not.
- Pro: no judgment calls, no risk of archiving the "wrong" fighters, trivially
  supports "did fighter X exist" queries for any name ever generated.
- Con: population churn is large (replenishment/backstop/academy spawns are
  continuous; a long run generates thousands of fighters who fight 0-2 times
  and retire/get cut with no notable career). Most of this archive would
  be noise never queried by anything.

**(b) Threshold-based.** Archive any fighter who ever held a title (any
tier/org), OR crossed a career-win/hype threshold (e.g. tier4 with 10+
wins, or peak hype above some bar), OR earned the `Legend` label
(`career/labels.py` already has this exact "sustained Elite excellence"
concept, sticky once earned).
- Pro: keeps the archive to fighters someone would actually look up. The
  `Legend` label is a ready-made "was this fighter notable" signal that
  already exists and is cheap to check at retirement time.
- Con: a threshold is an arbitrary line; a fighter just under it (e.g. a
  tier4 gatekeeper with a long, unremarkable-but-real career) isn't
  archived even though a curious user might look them up after seeing them
  in an old title fight's `TitleFightRecord`/`EventRecord`.

**(c) Reachability-based (recommended).** Archive any fighter who is still
**referenced** by something worth keeping: every former champion (title
reign data needs *some* fighter object to point at), every fighter who
appears in an archived title reign as challenger/opponent, and (optionally)
every fighter who ever appeared in `EventRecord`/`TitleFightRecord` history
(both already retained indefinitely, per `title._title_history`/
`orgs/events._event_history` — see §4). This is threshold-based in effect
(§2b's title/Legend fighters are a subset) but the actual trigger is "does
an already-durable record point at this fighter," not a separately-chosen
number.
- Pro: never orphans a reference (no title-lineage view with a name and a
  dead link); scales with what's *already* being kept, not a second
  independent policy to keep in sync.
- Con: still leaves out fighters who were interesting but never touched a
  title (a long-tenured gatekeeper with a great won-loss record but no
  title shot) — same gap as (b), just via a different mechanism.

**Recommendation: (c), with the `Legend` label folded in as an additional
trigger** (a fighter who earned `Legend` gets archived even if they never
held a title — the label already exists specifically to flag sustained
excellence, so it's a natural second trigger with no new criteria to invent).
Everyone else (the large majority of generated fighters, per §1) is not
archived — consistent with `career/replenishment.py`'s framing of tier0 as a
"large base pool," most of which was never meant to be individually notable.

## 3. What data persists per archived fighter/title reign

Per archived **fighter**:
- Full `real_fight_history` (already exists on the live `Fighter` object;
  archiving means copying it out before the object is dropped, not adding a
  new tracking mechanism).
- Final `record_str`/`wins`/`losses` at time of retirement/removal.
- Org affiliation history — **does not currently exist as a structured
  list**. `Fighter` only has `org`/`org_start_day` (current org, current
  start day) — no log of *prior* orgs a fighter moved through (org movement
  changes `fighter.org` in place, per `orgs/org_movement.py`/
  `career/weight_transfers.py`'s move executors). This would need a new
  `org_history: list[OrgStint]` field added to `Fighter` (or tracked
  externally, keyed by `fighter_id`, populated at each of the ~4 org-move
  call sites already in the codebase — Apex poach, Apex departure,
  within-tier move, weight-class-driven org reassignment) to have anything
  to archive here. **Open question, not decided by this doc**: is per-org
  tenure worth the added bookkeeping at every move site, or is "current org
  + retirement org" (derivable from the last fight in `real_fight_history`)
  good enough for a first version?
- Retirement/removal reason and date — `career/retirement.py` and
  `career/cuts.py` already compute *why* a fighter left (inactivity,
  age-driven decline, cut severity) at the moment it happens; this reason
  is currently only used to decide the removal, not logged anywhere.
  Capturing it into the archive record is a one-line addition at each of the
  two removal call sites, not a new inference.
- Peak hype / peak tier reached (both cheap: max-tracking over the
  fighter's lifetime, or derivable from `real_fight_history`'s tier field
  plus a running hype-max if that's not already tracked — it isn't).

Per archived **title reign**:
- `weight_class`, `tier_key`, `org` (reg_org), champion `fighter_id`, reign
  start day, reign end day (or "still reigning" if current).
- List of successful defenses (opponent, date, method) — this is exactly
  what `title._title_history`/`TitleFightRecord` already contains **per
  fight**, just not yet grouped into reigns. A reign is a derived view:
  walk `TitleFightRecord`s for a given `(weight_class, tier_key, org)` in
  order, start a new reign each time `winner_name != previous champion`.
  This is a query/transform over already-durable data (§4), not new
  storage, **provided `TitleFightRecord` itself is kept indefinitely**
  (currently it is — no cap/eviction on `_title_history`, confirmed by
  reading `title.py`).
- The League's championship (season/playoff, not `TitleFightRecord`-based)
  needs the same treatment over `LeagueSeasonRecord`/(new, Phase 2)
  `EventRecord` history instead — see §4, these are also currently
  uncapped/retained for the life of a sim run.

## 4. Where this should live relative to existing data

**Nothing today survives a fresh `init_sim()` call or process restart** —
`_title_history`, `_event_history` (new this phase, `orgs/events.py`),
`_season_history` (`orgs/league_season.py`), `_move_log`
(`orgs/org_movement.py`), and every other `list`-based history registry in
this codebase are plain in-memory Python lists, cleared by their respective
`reset_*()` calls at sim start and never written to disk. `api.py` has no
persistence layer at all (no database, no file writes) — `GET /state`
rebuilds its JSON snapshot from live in-memory state on every call. So
**"archive" here means two related but separable things**:

1. **Within a single running sim**, title/event/season history is already
   durable for the life of that process (uncapped growth, per §3) — a
   title-lineage query feature could be built *today*, entirely in-memory,
   by deriving reign groupings from `title.get_title_history()` +
   `orgs/league_season.get_league_history()`, with no new storage. This is
   the "piggyback on what already exists" case, and it's real, just
   narrower than prior project memory assumed (§1) — it covers title
   reigns, not fighter career records, since fighter objects themselves are
   not retained.
2. **Across sim restarts / for fighter-level archival** (§2c/§3), there is
   no existing mechanism to build on. This needs: (a) a hook at each
   retirement/cut/removal call site (there are ~6-8 across
   `career/retirement.py`, `career/cuts.py`, `orgs/league_season.py`'s
   inline removal, etc.) that, if the fighter meets the §2 notability
   bar, copies the needed fields (§3) into a new archive structure before
   the `Fighter` object is dropped; and (b) a decision on whether that
   archive is (i) an in-memory list alongside the other history registries
   (simplest, matches existing patterns, but still lost on restart — fine
   if this project has no persistence story at all yet, which it currently
   doesn't) or (ii) written to disk (JSON lines / sqlite) for real
   cross-session durability. **Open question, not decided by this doc**:
   `api.py`/`sim.py` have zero existing disk-persistence precedent to
   extend — introducing one is a bigger architectural decision than this
   phase's scope, and should probably be scoped together with *any* other
   future persistence need (saved sim state, resumable runs), not
   introduced solely for fighter archival.

## 5. Future UI/query shape (sketch, not build)

The obvious first use case, per the task prompt: **"who has held the Apex FC
lightweight belt, in order, with reign lengths."**

- Backend: a function `get_title_lineage(weight_class, tier_key, org) ->
  list[Reign]` that derives reigns from `title.get_title_history()` (§3) —
  pure computation over already-retained data, no new storage needed for
  this specific query, and could genuinely ship as a small standalone
  addition independent of the fighter-archival piece.
- API: a new `GET /titles/{weight_class}/{tier_key}/{org}/lineage` endpoint
  (or folded into the existing title data `GET /state` already returns),
  returning champion name, reign start/end (calendar-day and, if useful,
  converted to a display date), defense count, and how the reign ended
  (dethroned by whom / vacated / still active).
- Frontend (`web/app.js`'s existing Wikipedia-style modal navigation
  pattern is a natural fit): a "Title History" tab on each org's page,
  listing reigns newest-first, each champion name a clickable link into the
  existing fighter-detail modal IF that fighter is still active — for a
  **retired** former champion, the modal would need archived data (§3/§4)
  to render anything at all, which is the concrete point where the
  lineage-view feature and the fighter-archival feature become coupled:
  lineage-by-itself works with zero new storage; lineage-with-clickable-
  retired-champions requires the fighter archive to exist first.

## 6. Open questions (explicitly unresolved by this doc)

1. Does `org_history` (§3) justify adding write-hooks at every org-move call
   site, or is "last known org" sufficient for a first version?
2. Does fighter-level archival need real disk persistence (§4.2), or is an
   in-memory-for-the-life-of-the-process archive acceptable given the rest
   of the sim has no persistence story either?
3. Should the `Legend`-label trigger (§2) be sufficient on its own, or
   should there also be a raw numeric floor (e.g. "any fighter with 15+
   tier4 wins") to catch fighters who had a long notable career but never
   quite earned the sticky label?
4. Should archived records be prunable/capped (like `_event_log`'s
   `_EVENT_LOG_CAP=5000` pattern already used elsewhere) or genuinely
   unbounded? An unbounded per-fighter archive over a very long run could
   grow large; this doc does not size that risk.
