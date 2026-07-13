"""
replenishment.py -- Academy prospect replenishment and population floor backstop.

## Part 1: Per-academy staggered prospect generation

Each academy independently tracks its next_prospect_day on the global calendar.
The sim loop calls run_replenishment() each tick; any academy whose day has
passed generates one Amateur-tier prospect and immediately schedules the next
arrival from an exponential distribution.

Generation rate formula:
  mean_days = BASE_INTERVAL / (1 + ps_normalized)
  ps_normalized = (pipeline_strength - PS_MIN) / (PS_MAX - PS_MIN)  -> [0, 1]

  At ps=-6 (worst): mean = BASE_INTERVAL / 1.0
  At ps= 0 (avg):   mean = BASE_INTERVAL / 1.4
  At ps=+9 (best):  mean = BASE_INTERVAL / 2.0

  Inter-arrival: exponential (Poisson process), so different academies rarely
  fire on the same tick even at similar rates.

## Part 2: Population floor backstop

Fires every BACKSTOP_CHECK_INTERVAL sim days. If any weight class + tier drops
below its floor threshold, an emergency batch spawns into that pool. Elite tier
checked first (most sim-breaking when depleted).

## Part 3: Replenishment history log

Per (weight_class, sim_year): counts of normal vs backstop generations, plus
prospect tier distribution. Accessible via get_replenishment_history().

## Part 4: Initialization

initialize_replenishment() staggers each academy's first next_prospect_day
across [0, mean_interval] so the pipeline feels mid-cycle at startup, not like
everything begins on day 1.

## Constants

All constants flagged first-pass; tune after observing long-run population
dynamics -- consistent with every other first-pass constant in this project.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from career.fighter import Fighter
from career.academies import ACADEMIES, ACADEMY_PIPELINE, Academy
from career.tiers import TIER_LEVELS, WEIGHT_CLASSES, generate_tier_fighter
from career.age import SIM_DAYS_PER_YEAR
from sim_calendar import get_sim_day
from career.inflow import generate_crossover, generate_lateral

# ── Rate constants ────────────────────────────────────────────────────────────

BASE_INTERVAL: int = 180
"""
Mean simulated days between prospects from an academy with pipeline_strength=0.
First-pass estimate; tune after observing long-run Elite pool dynamics.

Derived rates:
  ps=-6 (worst): mean=180d -> ~2.0 prospects/year per academy
  ps= 0 (avg):   mean=129d -> ~2.8 prospects/year per academy
  ps=+9 (best):  mean= 90d -> ~4.1 prospects/year per academy
  Average: ~2.8/year, 15 academies -> ~42/year total, ~14/WC (random 1/3 split).
  Net effect at scale=1.0 (~5-6 attrition events/WC/year): comfortable surplus
  at Amateur that the cut system naturally trims. Elite pool filled via promotion.
"""

_PS_MIN: float = -6.0   # minimum pipeline_strength across all academies in academies.py
_PS_MAX: float = +9.0   # maximum pipeline_strength

# ── Floor thresholds (backstop) ───────────────────────────────────────────────
# Per weight class. Elite has highest priority -- a thin Elite pool is sim-breaking.
# First-pass estimates; tune if backstop fires too often or never fires in practice.

FLOOR_THRESHOLDS: dict[str, int] = {
    "tier0": 30,   # Amateur:      large base pool needed for promotion pipeline
    "tier1": 25,   # Regional
    "tier2": 20,   # Mid-major
    "tier3": 15,   # Top-org
    "tier4": 18,   # Elite:        minimum for title fights + ranked matchmaking
                   # raised from 12 (2026-07-13): Elite attrition (retirement/cuts)
                   # chronically outpaces tier3->tier4 promotion, so the backstop
                   # fires very often and the floor becomes the de facto steady-state
                   # population, not just a rare safety net -- with TIER_POPULATION
                   # tier4=20, a floor of 12 meant top-tier org rosters equilibrated
                   # at ~12-14/weight class instead of near the intended 20. 18
                   # (90% of target) keeps a long 5000-fight sim (seed 42) sitting at
                   # 17-19/weight class while still passing verify_title_selection,
                   # verify_elite_matchmaking, verify_migration, verify_replenishment,
                   # and smoke_test.
}

BACKSTOP_CHECK_INTERVAL: int = 90  # sim days between floor scans (~quarterly)

# Tier order for backstop processing: Elite first (most critical).
_BACKSTOP_TIER_ORDER: list[str] = ["tier4", "tier3", "tier2", "tier1", "tier0"]

# ── Crossover / lateral rate constants ───────────────────────────────────────
# Both use a single global Poisson process (one next-day across the entire sim).
# First-pass rates; tune once long-run inflow is observed.

# Mean 150 days -> ~2.4 crossovers/year total across all weight classes.
_CROSSOVER_INTERVAL: int = 150
# Mean 80 days -> ~4.6 laterals/year total across all weight classes.
_LATERAL_INTERVAL: int = 80

# ── Event log cap ─────────────────────────────────────────────────────────────
_EVENT_LOG_CAP: int = 5000  # stop appending after this many events (memory safety)

# ── Academy flat list ─────────────────────────────────────────────────────────
# Built once at import time from ACADEMIES; never mutated.
_ACADEMY_TEMPLATES: list[tuple[str, Academy]] = [
    (template, academy)
    for template, academy_list in ACADEMIES.items()
    for academy in academy_list
]  # 15 (template_name, Academy) pairs


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReplenishmentEvent:
    """One prospect generation event, logged for diagnostics and verification."""
    sim_day:       int
    academy_name:  str
    template_name: str
    weight_class:  str
    tier_key:      str        # "tier0" for academy; target tier for backstop
    prospect_tier: str
    fighter_name:  str
    overall:       float
    source:        str        # "academy" | "backstop"


@dataclass(frozen=True)
class BackstopEntry:
    """One backstop event: a weight class + tier fell below its floor."""
    sim_day:          int
    weight_class:     str
    tier_key:         str
    count:            int    # fighters spawned
    population_before: int


# ── Module-level state ────────────────────────────────────────────────────────

_next_prospect_day: dict[str, int] = {}      # academy_name -> next sim_day to generate
_last_backstop_day: int = 0
_next_crossover_day: int = 0
_next_lateral_day: int = 0

_event_log:   list[ReplenishmentEvent] = []
_backstop_log: list[BackstopEntry]    = []

# {weight_class: {sim_year: {"normal": int, "backstop": int, "crossover": int, "lateral": int, "tier_dist": {...}}}}
_yearly_history: dict[str, dict[int, dict]] = {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalize_ps(ps: float) -> float:
    """Map pipeline_strength to [0, 1] (clamped against known extremes)."""
    return max(0.0, min(1.0, (ps - _PS_MIN) / (_PS_MAX - _PS_MIN)))


def _mean_interval(academy_name: str) -> float:
    """Mean days between prospects for this academy."""
    ps   = ACADEMY_PIPELINE.get(academy_name, 0.0)
    norm = _normalize_ps(ps)
    return BASE_INTERVAL / (1.0 + norm)


def _sample_interval(academy_name: str) -> int:
    """Sample next inter-arrival time from an exponential distribution (>= 1 day)."""
    mean = _mean_interval(academy_name)
    return max(1, round(random.expovariate(1.0 / mean)))


def _sim_year(sim_day: int) -> int:
    return sim_day // SIM_DAYS_PER_YEAR


_SOURCE_HISTORY_KEY: dict[str, str] = {
    "academy":   "normal",
    "backstop":  "backstop",
    "crossover": "crossover",
    "lateral":   "lateral",
}


def _record_history(wc: str, sim_day: int, prospect_tier: str, source: str) -> None:
    yr = _sim_year(sim_day)
    if wc not in _yearly_history:
        _yearly_history[wc] = {}
    if yr not in _yearly_history[wc]:
        _yearly_history[wc][yr] = {
            "normal": 0, "backstop": 0, "crossover": 0, "lateral": 0,
            "tier_dist": {"raw": 0, "developing": 0, "high_upside": 0, "elite": 0},
        }
    rec = _yearly_history[wc][yr]
    rec[_SOURCE_HISTORY_KEY.get(source, "normal")] += 1
    td = rec["tier_dist"]
    td[prospect_tier] = td.get(prospect_tier, 0) + 1


def _spawn(
    template_name: str,
    academy: Academy,
    weight_class: str,
    tier_key: str,
    pools: dict,
    all_fighters: list[Fighter],
    sim_day: int,
    source: str,
) -> None:
    """Generate one fighter and add them to pools and all_fighters."""
    f = generate_tier_fighter(template_name, tier_key, weight_class, academy=academy)
    pools[weight_class][tier_key].append(f)
    all_fighters.append(f)

    if len(_event_log) < _EVENT_LOG_CAP:
        _event_log.append(ReplenishmentEvent(
            sim_day       = sim_day,
            academy_name  = academy.name,
            template_name = template_name,
            weight_class  = weight_class,
            tier_key      = tier_key,
            prospect_tier = f.prospect_tier,
            fighter_name  = f.name,
            overall       = f.overall,
            source        = source,
        ))

    _record_history(weight_class, sim_day, f.prospect_tier, source)


# ── Part 4: Initialization ────────────────────────────────────────────────────

def initialize_replenishment() -> None:
    """
    Reset all replenishment state and schedule each academy's first prospect day.

    First-day values are staggered across [0, mean_interval] per academy so that
    the pipeline feels mid-cycle at startup rather than all 15 academies firing
    simultaneously on day 1. Crossover and lateral first-days are also staggered.
    """
    global _next_prospect_day, _last_backstop_day, _next_crossover_day, _next_lateral_day

    _next_prospect_day.clear()
    _event_log.clear()
    _backstop_log.clear()
    _yearly_history.clear()
    _last_backstop_day = 0
    _next_crossover_day = round(random.uniform(0.0, _CROSSOVER_INTERVAL))
    _next_lateral_day   = round(random.uniform(0.0, _LATERAL_INTERVAL))

    for _template, academy in _ACADEMY_TEMPLATES:
        mean = _mean_interval(academy.name)
        # Uniform in [0, mean] gives a spread equivalent to "picking a random
        # point in the first inter-arrival window" for each academy independently.
        _next_prospect_day[academy.name] = round(random.uniform(0.0, mean))


# ── Part 1: Academy prospect generation ──────────────────────────────────────

def _check_academy_generation(
    pools: dict,
    all_fighters: list[Fighter],
) -> None:
    """Generate a prospect from any academy whose scheduled day has arrived."""
    current_day = get_sim_day()

    for template_name, academy in _ACADEMY_TEMPLATES:
        if current_day >= _next_prospect_day.get(academy.name, 0):
            wc = random.choice(WEIGHT_CLASSES)
            _spawn(template_name, academy, wc, "tier0", pools, all_fighters,
                   current_day, "academy")
            _next_prospect_day[academy.name] = current_day + _sample_interval(academy.name)


# ── Part 2: Population floor backstop ─────────────────────────────────────────

def _check_backstop(
    pools: dict,
    all_fighters: list[Fighter],
) -> None:
    """
    Quarterly floor scan. Spawns emergency fighters into any weight class + tier
    below its threshold. Processes Elite first (highest priority).
    Prints a diagnostic line for each event so operator can distinguish natural
    lean periods from structural problems.
    """
    global _last_backstop_day
    current_day = get_sim_day()

    if current_day - _last_backstop_day < BACKSTOP_CHECK_INTERVAL:
        return
    _last_backstop_day = current_day

    templates = list(ACADEMIES.keys())

    for tier_key in _BACKSTOP_TIER_ORDER:
        floor = FLOOR_THRESHOLDS[tier_key]
        for wc in WEIGHT_CLASSES:
            count = len(pools[wc][tier_key])
            if count >= floor:
                continue

            needed = floor - count
            print(
                f"  [BACKSTOP] d{current_day}  {wc} {tier_key}: "
                f"population {count} < floor {floor} -- spawning {needed}"
            )
            _backstop_log.append(BackstopEntry(
                sim_day=current_day,
                weight_class=wc,
                tier_key=tier_key,
                count=needed,
                population_before=count,
            ))

            for idx in range(needed):
                template_name   = templates[idx % len(templates)]
                backstop_academy = random.choice(ACADEMIES[template_name])
                _spawn(template_name, backstop_academy, wc, tier_key,
                       pools, all_fighters, current_day, "backstop")


# ── Crossover and lateral transfer generation ─────────────────────────────────

def _check_crossover_generation(
    pools: dict,
    all_fighters: list[Fighter],
) -> None:
    """Generate a crossover athlete if the scheduled day has arrived."""
    global _next_crossover_day
    current_day = get_sim_day()
    if current_day < _next_crossover_day:
        return

    wc = random.choice(WEIGHT_CLASSES)
    fighter, sport, caliber = generate_crossover(wc)

    pools[wc][fighter.tier].append(fighter)
    all_fighters.append(fighter)

    print(
        f"  [CROSSOVER] d{current_day}  {wc} {fighter.tier}: "
        f"{sport} ({caliber}) -- {fighter.name} "
        f"ovr={fighter.overall:+.1f}  hype={fighter.hype:+.1f}  pt={fighter.prospect_tier}"
    )

    if len(_event_log) < _EVENT_LOG_CAP:
        _event_log.append(ReplenishmentEvent(
            sim_day       = current_day,
            academy_name  = fighter.academy,
            template_name = fighter.template,
            weight_class  = wc,
            tier_key      = fighter.tier,
            prospect_tier = fighter.prospect_tier,
            fighter_name  = fighter.name,
            overall       = fighter.overall,
            source        = "crossover",
        ))

    _record_history(wc, current_day, fighter.prospect_tier, "crossover")
    _next_crossover_day = current_day + max(1, round(random.expovariate(1.0 / _CROSSOVER_INTERVAL)))


def _check_lateral_generation(
    pools: dict,
    all_fighters: list[Fighter],
) -> None:
    """Generate a lateral-transfer fighter if the scheduled day has arrived."""
    global _next_lateral_day
    current_day = get_sim_day()
    if current_day < _next_lateral_day:
        return

    wc = random.choice(WEIGHT_CLASSES)
    fighter, tier_key = generate_lateral(wc)

    pools[wc][tier_key].append(fighter)
    all_fighters.append(fighter)

    print(
        f"  [LATERAL]   d{current_day}  {wc} {tier_key}: "
        f"{fighter.template} -- {fighter.name} "
        f"ovr={fighter.overall:+.1f}  hype={fighter.hype:+.1f}  pt={fighter.prospect_tier}"
    )

    if len(_event_log) < _EVENT_LOG_CAP:
        _event_log.append(ReplenishmentEvent(
            sim_day       = current_day,
            academy_name  = fighter.academy,
            template_name = fighter.template,
            weight_class  = wc,
            tier_key      = tier_key,
            prospect_tier = fighter.prospect_tier,
            fighter_name  = fighter.name,
            overall       = fighter.overall,
            source        = "lateral",
        ))

    _record_history(wc, current_day, fighter.prospect_tier, "lateral")
    _next_lateral_day = current_day + max(1, round(random.expovariate(1.0 / _LATERAL_INTERVAL)))


# ── Combined tick function (called from sim.py) ───────────────────────────────

def run_replenishment(pools: dict, all_fighters: list[Fighter]) -> None:
    """
    Called once per sim tick (after advance_sim_clock).
    Runs academy generation, crossover/lateral inflow checks, and the backstop floor scan.
    """
    _check_academy_generation(pools, all_fighters)
    _check_crossover_generation(pools, all_fighters)
    _check_lateral_generation(pools, all_fighters)
    _check_backstop(pools, all_fighters)


# ── Part 3: History accessors ─────────────────────────────────────────────────

def get_replenishment_history(
    weight_class: str,
    n_years: int | None = None,
) -> list[dict]:
    """
    Return per-year replenishment summary for the given weight class,
    sorted oldest-first. Each entry: {year, normal, backstop, tier_dist}.
    Pass n_years to get only the most recent N years.
    """
    history = _yearly_history.get(weight_class, {})
    years   = sorted(history.keys())
    if n_years is not None:
        years = years[-n_years:]
    return [{"year": yr, **history[yr]} for yr in years]


def get_event_log() -> list[ReplenishmentEvent]:
    """Return all logged generation events (capped at _EVENT_LOG_CAP)."""
    return list(_event_log)


def get_backstop_log() -> list[BackstopEntry]:
    """Return all backstop events."""
    return list(_backstop_log)


def get_total_generated(weight_class: str) -> tuple[int, int]:
    """Return (normal_total, backstop_total) for a weight class across all years."""
    normal = backstop = 0
    for rec in _yearly_history.get(weight_class, {}).values():
        normal   += rec.get("normal", 0)
        backstop += rec.get("backstop", 0)
    return normal, backstop


def get_inflow_counts(weight_class: str) -> dict[str, int]:
    """
    Return per-source fighter count for a weight class across all sim years.
    Keys: "academy", "backstop", "crossover", "lateral".
    """
    counts: dict[str, int] = {"academy": 0, "backstop": 0, "crossover": 0, "lateral": 0}
    for rec in _yearly_history.get(weight_class, {}).values():
        counts["academy"]   += rec.get("normal", 0)
        counts["backstop"]  += rec.get("backstop", 0)
        counts["crossover"] += rec.get("crossover", 0)
        counts["lateral"]   += rec.get("lateral", 0)
    return counts
