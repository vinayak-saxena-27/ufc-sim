"""
academy_reputation.py — Academy reputation feedback loop (Session 5c).

## Design

ACADEMY_REPUTATION is a LIVE value per academy that drifts based on how its
recent alumni have actually performed, while still gravitating back toward
the academy's base_pipeline_strength (the static ACADEMY_PIPELINE anchor).
A strong academy whose alumni go cold slowly loses its shine; a weak
academy that produces a hot run of finishers slowly earns one. Coaching
changes occasionally nudge the anchor itself, so the gravity point can
permanently shift over a long sim, not just the live value orbiting it.

All EXTERNAL reads of an academy's effective strength for scouting/promotion
purposes go through get_effective_pipeline_strength() — nothing outside this
module should read ACADEMY_REPUTATION or ACADEMY_PIPELINE directly for that
purpose. No other module should import this one directly except sim.py's
periodic evaluation sweep (see update_academy_reputations()).

## Alumni signal

Once per evaluation period (same annual cadence as advance_all_development(),
see update_academy_reputations()), each academy's alumni — fighters currently
in all_fighters with fighter.academy == academy_name — are scanned for fights
with sim_day >= current_day - ALUMNI_WINDOW_DAYS. From that window:

  title_component = min(title_wins / TITLE_WINS_CAP, 1.0)   weight 0.5
  finish_rate      = finishes / wins (0 if no wins)          weight 0.3
  win_rate         = wins / (wins + losses)                  weight 0.2

composite in [0, 1] is re-centered onto pipeline_strength's zero-centered
scale (-8..+9, see academies.py) via (composite - 0.5) * _SIGNAL_SCALE, so a
roughly-average alumni showing (composite ~0.5) yields signal ~0.0 — neither
pulling reputation up nor down.

If an academy has no alumni fights in the window, signal is None and that
academy's reputation is left unchanged this period (no update, not a decay
to zero — see _update_reputation()).

## Reputation update

  rep = rep + ALPHA * (signal - rep) + BETA * (base_pipeline_strength - rep)

Two independent pulls: toward the fresh alumni signal (ALPHA, fast-ish) and
toward the static anchor (BETA, slow) so reputation can't wander off forever
even through a long cold/hot streak.

## Coaching events

Each evaluation period, each academy independently has COACHING_EVENT_PROB
chance of a coaching change. If triggered, base_pipeline_strength (i.e.
ACADEMY_PIPELINE[name] itself — the shared anchor dict other modules import)
shifts by a value drawn uniformly from
[-COACHING_MAGNITUDE_MAX, +COACHING_MAGNITUDE_MAX] of its CURRENT value.

## Deviation from spec: clamp bounds

The spec calls for clamping the shifted base_pipeline_strength to
[MIN_BASE_PIPELINE (0.1), "the academy's template's natural ceiling"]. That
0.1 floor assumes a positive-scale pipeline_strength; this codebase's is
zero-centered (-8..+9) and FIVE of the fifteen academies are deliberately
negative (weak-pipeline identity — see academies.py's docstring). A flat 0.1
floor would snap any of those five up to positive on their very first
coaching event, erasing that identity. Confirmed with the user before
building: instead, _clamp_bounds() derives a natural floor AND ceiling per
academy from the min/max pipeline_strength among the frozen Academy objects
sharing its template (e.g. dagestan_sambo's academies span -4.0..+7.0) —
those Academy objects are never mutated, so this range is a stable per-
template natural range regardless of how much coaching-event drift has
accumulated in ACADEMY_PIPELINE. MIN_BASE_PIPELINE is kept as a spec-mandated
constant but is only used as a defensive fallback if template lookup ever
fails, not as the primary clamp.

## Scope: which pipeline_strength reads switch to get_effective_pipeline_strength()

Only call sites making a SCOUTING or PROMOTION decision were switched:
  - matchmaking.py's direct-promotion nudge (Effect 2)
  - nonelite_rankings.py's scout-notice probability (_p_scout_notice)

development.py's and replenishment.py's pipeline reads drive development
RATE and prospect-generation CADENCE respectively — not scouting/promotion —
so per spec they were left reading the static ACADEMY_PIPELINE anchor
directly. That anchor is still live: coaching events mutate ACADEMY_PIPELINE
in place, so those two systems do feel a permanent coaching swing over time,
just not the faster-moving alumni-driven reputation signal.
"""
from __future__ import annotations

import random

from career.fighter import Fighter
from career.academies import ACADEMIES, ACADEMY_PIPELINE, Academy
from career.age import SIM_DAYS_PER_YEAR

# ── Constants (per spec) ───────────────────────────────────────────────────────

ALPHA: float = 0.08
BETA: float = 0.05
COACHING_EVENT_PROB: float = 0.04
COACHING_MAGNITUDE_MAX: float = 0.15
ALUMNI_WINDOW_DAYS: int = 4 * 365  # 4 sim years
MIN_BASE_PIPELINE: float = 0.1  # defensive fallback only — see module docstring

# ── Alumni-signal composite ────────────────────────────────────────────────────

TITLE_WIN_WEIGHT:   float = 0.5
FINISH_RATE_WEIGHT: float = 0.3
WIN_RATE_WEIGHT:    float = 0.2

TITLE_WINS_CAP: int = 3
"""Title wins in one alumni window are rare; TITLE_WINS_CAP or more maxes out
the title component of the composite instead of letting one prolific champion
dominate the signal unboundedly."""

_SIGNAL_MIDPOINT: float = 0.5
_SIGNAL_SCALE:    float = 16.0
"""Maps the [0, 1] composite onto pipeline_strength's zero-centered scale
(-8..+9): composite 0.5 (average alumni showing) -> signal 0.0."""

_ACADEMY_BY_NAME: dict[str, Academy] = {
    acad.name: acad
    for region_list in ACADEMIES.values()
    for acad in region_list
}

# Snapshot of each academy's ORIGINAL pipeline_strength at import time, before
# any coaching event has had a chance to mutate ACADEMY_PIPELINE. Used to
# restore state on reset_academy_reputation() across repeated sim runs.
_ORIGINAL_BASE_PIPELINE: dict[str, float] = dict(ACADEMY_PIPELINE)

# ── Reputation state ───────────────────────────────────────────────────────────

ACADEMY_REPUTATION: dict[str, float] = dict(ACADEMY_PIPELINE)
"""Live working value read by scouting/promotion via
get_effective_pipeline_strength(). Initialized to each academy's
base_pipeline_strength."""

_coaching_event_log: list[dict] = []
_last_reputation_update_day: int = 0


def reset_academy_reputation() -> None:
    """Reset reputation state, the mutable pipeline anchor, and the tracking
    clock back to each academy's original base_pipeline_strength. Call at the
    start of each fresh simulation, alongside the other reset_* calls."""
    global _last_reputation_update_day
    for name, ps in _ORIGINAL_BASE_PIPELINE.items():
        ACADEMY_PIPELINE[name] = ps
        ACADEMY_REPUTATION[name] = ps
    _coaching_event_log.clear()
    _last_reputation_update_day = 0


def get_effective_pipeline_strength(academy_name: str) -> float:
    """The live, reputation-adjusted pipeline strength for scouting/promotion
    reads. Falls back to 0.0 for an unrecognized academy name, matching the
    existing ACADEMY_PIPELINE.get(name, 0.0) convention at call sites."""
    return ACADEMY_REPUTATION.get(academy_name, 0.0)


def get_coaching_event_log() -> list[dict]:
    return list(_coaching_event_log)


# ── Alumni signal ──────────────────────────────────────────────────────────────

def _compute_alumni_signal(
    academy_name: str, all_fighters: list[Fighter], current_day: int,
) -> float | None:
    """Weighted composite of academy_name's alumni performance within the
    ALUMNI_WINDOW_DAYS lookback, re-centered onto pipeline_strength's scale.
    Returns None if no alumni fought in the window (caller must then leave
    reputation unchanged rather than update it)."""
    window_start = current_day - ALUMNI_WINDOW_DAYS
    wins = losses = 0
    finishes = 0
    title_wins = 0
    for f in all_fighters:
        if f.academy != academy_name:
            continue
        for r in f.fight_history:
            if r.sim_day < 0 or r.sim_day < window_start:
                continue
            if r.outcome == "win":
                wins += 1
                if r.method != "decision":
                    finishes += 1
                if r.is_title:
                    title_wins += 1
            else:
                losses += 1

    total = wins + losses
    if total == 0:
        return None

    win_rate = wins / total
    finish_rate = (finishes / wins) if wins > 0 else 0.0
    title_component = min(title_wins / TITLE_WINS_CAP, 1.0)

    composite = (
        TITLE_WIN_WEIGHT * title_component
        + FINISH_RATE_WEIGHT * finish_rate
        + WIN_RATE_WEIGHT * win_rate
    )
    return (composite - _SIGNAL_MIDPOINT) * _SIGNAL_SCALE


def _update_reputation(academy_name: str, signal: float | None) -> None:
    if signal is None:
        return  # no alumni activity this window -- reputation holds steady
    rep = ACADEMY_REPUTATION[academy_name]
    base = ACADEMY_PIPELINE[academy_name]
    ACADEMY_REPUTATION[academy_name] = (
        rep + ALPHA * (signal - rep) + BETA * (base - rep)
    )


# ── Coaching events ─────────────────────────────────────────────────────────────

def _clamp_bounds(academy_name: str) -> tuple[float, float]:
    """Natural floor/ceiling for base_pipeline_strength: the min/max
    pipeline_strength among the frozen Academy objects sharing this academy's
    template (see module docstring for why this replaces the spec's flat 0.1
    floor)."""
    academy = _ACADEMY_BY_NAME.get(academy_name)
    if academy is None:
        return MIN_BASE_PIPELINE, MIN_BASE_PIPELINE
    peers = [a.pipeline_strength for a in ACADEMIES[academy.template]]
    return min(peers), max(peers)


def _maybe_coaching_event(academy_name: str) -> None:
    if random.random() >= COACHING_EVENT_PROB:
        return
    base = ACADEMY_PIPELINE[academy_name]
    shift = base * random.uniform(-COACHING_MAGNITUDE_MAX, COACHING_MAGNITUDE_MAX)
    lo, hi = _clamp_bounds(academy_name)
    new_base = max(lo, min(base + shift, hi))
    ACADEMY_PIPELINE[academy_name] = new_base

    if new_base > base:
        direction = "up"
    elif new_base < base:
        direction = "down"
    else:
        direction = "flat"
    _coaching_event_log.append({
        "academy":   academy_name,
        "direction": direction,
        "magnitude": new_base - base,
    })


# ── Public API ──────────────────────────────────────────────────────────────────

def update_academy_reputations(all_fighters: list[Fighter], current_day: int) -> None:
    """Advance every academy's reputation and roll for coaching events. Fires
    once per SIM_DAYS_PER_YEAR elapsed — the same annual cadence as
    advance_all_development(). Call this alongside that function in sim.py's
    periodic evaluation sweep.
    """
    global _last_reputation_update_day
    while current_day - _last_reputation_update_day >= SIM_DAYS_PER_YEAR:
        for name in ACADEMY_PIPELINE:
            signal = _compute_alumni_signal(name, all_fighters, current_day)
            _update_reputation(name, signal)
            _maybe_coaching_event(name)
        _last_reputation_update_day += SIM_DAYS_PER_YEAR
