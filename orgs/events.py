"""
orgs/events.py -- Real dated fight-card/event scheduling (matchmaking-improvement
Phase 2, Section 1).

Replaces the previous ad-hoc per-bout scheduling for org-bearing tiers
(tier1/tier2/tier4) with real events: each org runs its own numbered events
on its own cadence, each event a dated card of several fights built up front
via the existing pick_opponent/pick_scheduled_elite_a/pick_discovery_a
pipeline (all eligibility/avoidance logic there is UNCHANGED -- this module
only decides who gets scheduled onto a card and when).

tier0 (Amateur) and tier3 (Top-org, org-less) have no Fighter.org concept and
so can't host a real org's event, but they DO get their own dedicated
pseudo-entity cadence (TIER0_ENTITY/TIER3_ENTITY below) -- same priority
queue as real orgs, same "most overdue wins" selection, same per-entity
card built by _build_event, just with no org filter on the candidate pool
and no title slots (tier0 has no title format; tier3's title-due detection
stays on title.py's original per-bout fight-count gate -- see that module's
tier3 branch -- since tier3 titles were never part of this event system).

(Design note, 2nd pass: an earlier version of this module had tier0/tier3
share a single probabilistic "fallback" draw that only fired when no real
org was due, later patched with a FALLBACK_RESERVE_RATE reserved-attempt-
share hack after that starved tier0 below its own frequency target. Both
are gone now -- dedicated per-entity cadence means tier0/tier3 compete for
attempts on the SAME footing as every real org, which is what makes the
reserve-rate carve-out unnecessary rather than something to keep tuning.)

The League is a schedulable org for cadence purposes (its own event
interval), but its card is built by orgs/league_season.py's season/playoff
logic, not _build_event -- see league_event_due().

## Card model

One Event = one dated card for one org, spanning all of that org's weight
classes at once (a real MMA-style card, not a single-division slate). Built
in full the moment the org becomes due, then resolved one BoutSlot per
subsequent sim.py attempt (preserves the existing invariant that one
_run_one_bout call resolves at most one bout, so every fight-count-keyed
cadence elsewhere in the codebase -- RANKINGS_UPDATE_INTERVAL, LABEL_UPDATE_
INTERVAL, replenishment sweeps, etc. -- needs no change).

## Constants are first-pass

EVENT_INTERVAL_DAYS/CARD_SIZE_RANGE/TITLE_EVENTS_INTERVAL below are a
starting point sized off total-population capacity math (this module's
combined org-card demand should leave real headroom for the tier0/tier3
idle-weighted draw, which serves over half the total population and has no
event system of its own) -- NOT yet validated against actual simulated
frequency distributions. Tune here the same way title.py's TITLE_FIGHT_
INTERVAL was tuned last session: measure mean/p10/p50/p90/zero-in-5y per
tier, adjust, re-measure.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from career.fighter import Fighter
from career.tiers import WEIGHT_CLASSES
from career.labels import is_champion
from orgs.org_registry import ORG_NAMES, MIDMAJOR_ORG_NAMES, REGIONAL_ORG_NAMES, THE_LEAGUE_NAME

# ── Cadence / card-shape constants (first-pass -- see module docstring) ─────

EVENT_INTERVAL_DAYS: dict[str, int] = {
    "tier4": 11,
    "tier2": 400,
    "tier1": 650,
    "tier0": 12,
    "tier3": 18,
}
"""Days between an entity's own numbered events, by the tier it operates at
(tier0/tier3 are pseudo-entities -- see PSEUDO_ENTITIES -- not real orgs,
but get their own entry in this same table).

Three tuning passes now (matchmaking-improvement Phase 2). Pass 1
(tier1/tier2/tier4 only, tier0/tier3 still on the old shared fallback
draw): initial guesses (25/150/300) undershot tier4 (0.84 fights/yr vs
>=1.0 target) while tier1/tier2 ran well past their own >=0.35 floor
(1.16/1.46) -- tightened tier4, loosened tier1/tier2. Pass 2: added a
FALLBACK_RESERVE_RATE hack for tier0/tier3 that never fully closed tier0's
gap and cost tier4 fights it didn't need to give up. Pass 3 (this one):
replaced the reserve-rate hack with tier0/tier3 as real dedicated-cadence
entities in this same table, alongside two structural fixes this
population-scale rebalance depended on -- see get_next_due_bout_slot's
docstring for the rate-proportional priority fix (raw-days-overdue
priority structurally favored long-interval entities under the ~160%-
oversubscribed total attempt demand this system runs at) and the
force-soonest-when-nothing-due fix (tier0/tier3 becoming due-gated removed
the old fallback's implicit "always available" clock-keeping guarantee,
which caused a hard deadlock without a replacement).

Measured (seed 42 / seed 7, 9000-day runs, tenure-filtered 5-year window):
tier0 0.40/0.38 fights/yr (target >=0.35 -- direct instrumentation showed
tier0 was already winning the SINGLE LARGEST attempt share of all 25
entities at ~30%, so the fix that actually moved its number was raising
CARD_SIZE_RANGE, not shortening its interval further -- its population
(~780) is simply too large for interval-only tuning to reach target).
tier1 0.46-0.50, tier2 0.55-0.56, tier3 0.54-0.60 (all comfortably above
0.35, loosened repeatedly to fund tier0/tier4). tier4 0.95/1.03 mean, 0%
zero-in-5y both seeds (right at/near the >=1.0 mean target -- a narrower
interval recovers the mean fully but pushes tier0 back under 0.35 on at
least one seed; this value was chosen as the best simultaneous fit across
both seeds rather than over-fitting tier4 at tier0's expense again)."""

CARD_SIZE_RANGE: dict[str, tuple[int, int]] = {
    "tier4": (4, 7),
    "tier2": (3, 4),
    "tier1": (3, 3),
    "tier0": (10, 16),
    "tier3": (3, 5),
}
"""(min, max) regular (non-title) card slots per event, inclusive. tier0's
range is much larger than every other tier's -- see EVENT_INTERVAL_DAYS'
docstring: tier0 needed more fighters served PER WIN, not more frequent
wins (it was already winning the plurality of all attempts)."""

TITLE_EVENTS_INTERVAL: dict[str, int] = {
    "tier4": 29,   # ~29 * 11d = 319d gap, matching title.py's prior ~330d tier4 target
    "tier2": 3,
    "tier1": 3,
}
"""Events (not fights) between title defenses for a given (weight_class,
tier_key, org), mirroring title.py's old TITLE_FIGHT_INTERVAL_BY_TIER but
counted in events since due-ness is now decided at card-construction time.
tier4's value is re-derived each time EVENT_INTERVAL_DAYS["tier4"] changes,
to hold the same real-world calendar gap rather than letting title cadence
silently drift with whatever the frequency retune happened to land on."""

EVENT_SLOT_MIX: dict[str, int] = {
    "discovery": 1,
    "ranked_density": 1,
}
"""Fixed reserved slot counts per tier4 card for the two density-injection
mechanisms (matchmaking.pick_discovery_a/pick_scheduled_elite_a), folded
into card construction instead of firing as a separate always-on interval
layer (see Phase 2 plan's density-injector decision). tier1/tier2 have no
density injectors -- those have only ever applied to tier4."""

TIER0_ENTITY: str = "tier0"
TIER3_ENTITY: str = "tier3"
PSEUDO_ENTITIES: frozenset[str] = frozenset({TIER0_ENTITY, TIER3_ENTITY})
"""tier0/tier3 have no Fighter.org concept, so they can't be real
schedulable orgs -- but they get their own dedicated cadence entry in the
SAME priority queue as real orgs (SCHEDULABLE_ENTITIES below), each keyed
by its own tier_key string used directly as the "entity name" (no org name
exists to use instead). _build_event branches on membership in this set to
skip org-filtering the candidate pool (tier0/tier3 fighters have no
meaningful .org) and to skip title-slot construction entirely -- tier0 has
no title format, and tier3's title-due detection intentionally stays on
title.py's original per-bout fight-count gate (tier3 titles were never
part of this event system, before or after this pseudo-entity addition)."""

# org -> tier_key it operates at, built from org_registry's three org-name
# lists. The League is included (tier4) for cadence purposes even though its
# card is built elsewhere -- see league_event_due().
ORG_TIER: dict[str, str] = {
    **{o: "tier4" for o in ORG_NAMES},
    **{o: "tier2" for o in MIDMAJOR_ORG_NAMES},
    **{o: "tier1" for o in REGIONAL_ORG_NAMES},
}

SCHEDULABLE_ENTITIES: list[str] = [o for o in ORG_TIER if o != THE_LEAGUE_NAME] + list(PSEUDO_ENTITIES)
"""Every org (real) and pseudo-entity (tier0/tier3) whose cards are built by
this module, all competing for attempts via the same most-overdue-wins
priority in get_next_due_bout_slot. Excludes The League (its own
season/playoff scheduler owns card construction; only its cadence gate
lives here, via league_event_due)."""


def _entity_tier_key(entity: str) -> str:
    return entity if entity in PSEUDO_ENTITIES else ORG_TIER[entity]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class BoutSlot:
    weight_class: str
    is_title: bool = False
    fighter_a: Fighter | None = None
    """Pre-selected at card-construction time for regular slots. Left None
    for title slots -- challenger/champion selection stays lazy, resolved
    inside title.maybe_run_title_fight at bout-resolution time, so it reads
    freshly-computed rankings the same way it always has."""


@dataclass
class Event:
    org: str
    tier_key: str
    number: int
    scheduled_day: int
    card: list[BoutSlot] = field(default_factory=list)
    next_slot_idx: int = 0


@dataclass(frozen=True)
class EventRecord:
    org: str
    tier_key: str
    number: int
    scheduled_day: int
    card_size: int
    title_slots: int


# ── State (reset per sim, mirrors title.py/league_season.py's own registries) ─

_next_event_due_day: dict[str, int] = {}
_current_event:      dict[str, Event | None] = {}
_event_number:       dict[str, int] = {}
_title_event_counters: dict[tuple[str, str, str], int] = {}
_event_history:      list[EventRecord] = []

_EVENT_LOG_CAP: int = 5000


def reset_events() -> None:
    """Clear all event scheduling state. Call at the start of each sim."""
    _next_event_due_day.clear()
    _current_event.clear()
    _event_number.clear()
    _title_event_counters.clear()
    _event_history.clear()


def get_event_history() -> list[EventRecord]:
    return list(_event_history)


def log_league_event(scheduled_day: int, number: int, card_size: int, title_slots: int) -> None:
    """Additive logging hook for orgs/league_season.py's playoff completion --
    The League's card is built by that module's own bracket logic, not
    _build_event, but its events still belong in the same history feed as
    every other org's cards (future archive/reporting use). Pure recording;
    no behavior change."""
    if len(_event_history) < _EVENT_LOG_CAP:
        _event_history.append(EventRecord(
            org=THE_LEAGUE_NAME, tier_key="tier4", number=number,
            scheduled_day=scheduled_day, card_size=card_size, title_slots=title_slots,
        ))


# ── Title-due countdown (delegated to by title.fights_until_next_title_fight) ─

def get_title_event_countdown(weight_class: str, tier_key: str, org: str) -> int:
    key = (weight_class, tier_key, org)
    interval = TITLE_EVENTS_INTERVAL.get(tier_key, 0)
    return interval - _title_event_counters.get(key, 0)


# ── Card construction ─────────────────────────────────────────────────────────

def _build_event(org: str, tier_key: str, pools: dict, current_day: int) -> Event:
    """Build one entity's next event: title slot(s) first (per weight class,
    if due), then tier4's reserved density-injection slots, then fill the
    rest via idle-weighted draw from that entity's own (weight_class, tier)
    pool. No fighter appears twice on the same card.

    `org` is a real org name for tier1/tier2/tier4, or one of
    PSEUDO_ENTITIES (the literal string "tier0"/"tier3") for those two
    tiers -- see that constant's docstring. Pseudo-entities skip title-slot
    construction entirely and skip org-filtering the candidate pool (tier0/
    tier3 fighters have no meaningful Fighter.org)."""
    from matchmaking import pick_idle_weighted, pick_scheduled_elite_a, pick_discovery_a

    is_pseudo = org in PSEUDO_ENTITIES
    _event_number[org] = _event_number.get(org, 0) + 1
    event = Event(org=org, tier_key=tier_key, number=_event_number[org], scheduled_day=current_day)
    used_ids: set[str] = set()

    if not is_pseudo:
        for wc in WEIGHT_CLASSES:
            key = (wc, tier_key, org)
            _title_event_counters[key] = _title_event_counters.get(key, 0) + 1
            if _title_event_counters[key] >= TITLE_EVENTS_INTERVAL.get(tier_key, 0):
                _title_event_counters[key] = 0
                event.card.append(BoutSlot(weight_class=wc, is_title=True))

    if tier_key == "tier4":
        for _ in range(EVENT_SLOT_MIX.get("discovery", 0)):
            a = pick_discovery_a(pools, target_org=org)
            if a is not None and a.fighter_id not in used_ids:
                used_ids.add(a.fighter_id)
                event.card.append(BoutSlot(weight_class=a.weight_class, fighter_a=a))
        for _ in range(EVENT_SLOT_MIX.get("ranked_density", 0)):
            a = pick_scheduled_elite_a(pools, target_org=org)
            if a is not None and a.fighter_id not in used_ids:
                used_ids.add(a.fighter_id)
                event.card.append(BoutSlot(weight_class=a.weight_class, fighter_a=a))

    lo, hi = CARD_SIZE_RANGE.get(tier_key, (3, 3))
    target_size = random.randint(lo, hi)
    remaining = target_size - len([s for s in event.card if not s.is_title])
    if is_pseudo:
        org_pool = [
            f for wc in WEIGHT_CLASSES for f in pools.get(wc, {}).get(tier_key, [])
            if not is_champion(f)
        ]
    else:
        org_pool = [
            f for wc in WEIGHT_CLASSES for f in pools.get(wc, {}).get(tier_key, [])
            if f.org == org and not is_champion(f)
        ]
    for _ in range(max(0, remaining)):
        a = pick_idle_weighted(org_pool, current_day, exclude=used_ids)
        if a is None:
            break
        used_ids.add(a.fighter_id)
        event.card.append(BoutSlot(weight_class=a.weight_class, fighter_a=a))

    n_title = sum(1 for s in event.card if s.is_title)
    if len(_event_history) < _EVENT_LOG_CAP:
        _event_history.append(EventRecord(
            org=org, tier_key=tier_key, number=event.number, scheduled_day=current_day,
            card_size=len(event.card), title_slots=n_title,
        ))
    return event


def get_next_due_bout_slot(pools: dict, current_day: int) -> tuple[Event, BoutSlot] | None:
    """Most-overdue schedulable entity (real org or tier0/tier3 pseudo-
    entity) gets the next slot resolved. An entity's current card is fully
    drained (one slot per call) before its next event's due-day is
    (re)computed, so a slow entity doesn't get starved by always looking
    "less overdue" than a faster one mid-card.

    If NO entity is currently due, the entity with the soonest upcoming
    due-day is forced through anyway rather than returning None. This
    sounds like it shouldn't matter (surely "nobody due yet" is just a
    short gap?) but it's load-bearing: current_day only ever advances via
    advance_sim_clock(), called once per RESOLVED bout -- there is no
    independent "time passes" mechanism in this sim. If every entity's
    due-day sits even one day in the future, current_day can never reach
    any of them (nothing resolves -> no clock advance -> the gap never
    closes -> permanent deadlock). Before tier0/tier3 became due-day-gated
    entities themselves, their old shared fallback draw had no due-day at
    all -- it was unconditionally available every attempt -- and that
    unconditional availability was the sim's de facto clock-keeper. Giving
    tier0/tier3 their own dedicated cadence (matching how real orgs behave)
    removed that safety valve, so this function has to provide the
    equivalent guarantee explicitly: something is always selected, either
    because it's genuinely due or because forcing the soonest-due entity
    is the only way to keep the clock moving at all. Confirmed via direct
    reproduction: a 9000-day run hard-froze at day 235 without this (every
    entity's due-day was 236+, forever unreachable).

    Priority among due entities is overdue time AS A FRACTION OF THE
    ENTITY'S OWN INTERVAL, not raw days overdue. Total demand across all
    ~25 entities can exceed the 1-attempt-per-day budget (short-interval,
    big-card entities like tier0 want far more slots/year than a single
    day-per-attempt clock can supply to everyone), and under that
    contention a raw-days metric structurally favors long-interval entities
    -- they accumulate a big absolute "overdue" number while they wait,
    while a short-interval entity like tier0 (which needs to win FAR more
    often per year to hit its own target) only ever accumulates a small one
    before its due-day rolls forward again. Normalizing by each entity's
    own interval measures "how far behind ITS OWN schedule" instead, which
    is what actually needs to be fair here. Confirmed via direct
    measurement: raw-days priority left tier0 firing every ~21-23 days
    despite a configured 12-day interval (out-competed by entities that
    simply have larger absolute gaps), even though tier0 is the shortest
    interval of all 25 entities and should be winning close to every time
    it becomes due."""
    due_entities = [e for e in SCHEDULABLE_ENTITIES if current_day >= _next_event_due_day.get(e, 0)]
    if due_entities:
        org = max(
            due_entities,
            key=lambda e: (current_day - _next_event_due_day.get(e, 0)) / EVENT_INTERVAL_DAYS[_entity_tier_key(e)],
        )
    else:
        org = min(SCHEDULABLE_ENTITIES, key=lambda e: _next_event_due_day.get(e, 0))
    tier_key = _entity_tier_key(org)

    event = _current_event.get(org)
    if event is None or event.next_slot_idx >= len(event.card):
        event = _build_event(org, tier_key, pools, current_day)
        _current_event[org] = event
        if not event.card:
            # Pool too thin to field anything this cycle -- push the due day
            # out so we don't retry (and re-log an empty event) every attempt.
            _next_event_due_day[org] = current_day + EVENT_INTERVAL_DAYS[tier_key]
            return None

    slot = event.card[event.next_slot_idx]
    event.next_slot_idx += 1
    if event.next_slot_idx >= len(event.card):
        _next_event_due_day[org] = current_day + EVENT_INTERVAL_DAYS[tier_key]
    return event, slot


# ── The League's cadence gate (card itself built by orgs/league_season.py) ────

def league_event_due(current_day: int) -> bool:
    """True (and advances The League's own due-day) if it's time for the
    next League fixture/playoff check. Shares tier4's EVENT_INTERVAL_DAYS so
    The League's scheduling config lives in the same table as every other
    org's, without touching league_season.py's own bracket/points logic."""
    if current_day < _next_event_due_day.get(THE_LEAGUE_NAME, 0):
        return False
    _next_event_due_day[THE_LEAGUE_NAME] = current_day + EVENT_INTERVAL_DAYS["tier4"]
    return True
