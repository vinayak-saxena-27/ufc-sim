"""
sim_calendar.py — Global simulated calendar and population-relative inactivity
primitive for the MMA career sim.

Part 1: Global clock
  Granularity: simulated days (int).
  Epoch: day 0 = the first fight of the simulation.
  Advance rate: SIM_DAYS_PER_FIGHT days per fight processed.

  Real-world mapping rationale:
    A real MMA card runs ~10 bouts on a ~2-week cadence.
    One sim fight occupies one slot on such a card:
      14 days / 10 fights ~= 1.4 days -> rounded to 2.
    First-pass estimate; tune SIM_DAYS_PER_FIGHT if inactivity
    thresholds feel too tight or too loose after wiring in part 2.

    At 2 000 fights x 2 days/fight ~= 11 simulated years total.
    With ~735 fighters the average fighter sees ~5 fights over
    that span -- roughly once every 2 years, consistent with
    lower-tier pacing. Elite fighters participate more often
    due to pool-selection bias in the sim loop.

Part 2: Population-relative inactivity
  inactivity_percentile(fighter, pool) -- the primitive that prevents the
  100%-flagging failure mode of absolute thresholds. Instead of asking
  "has this fighter been out for more than N days?" it asks "is this
  fighter's gap longer than most of their CURRENT PEERS' gaps?"
  With a thin Elite pool (15 per WC), every fighter has long absolute gaps
  as a structural property of pool size; only a relative measure
  distinguishes outliers from the baseline.

Usage:
  reset_sim_clock()                    -- call at sim start
  advance_sim_clock()                  -- call once per fight processed (AFTER the fight)
  get_sim_day()                        -- current day integer
  days_since(past_day)                 -- elapsed days from a stored sim_day to now
  inactivity_percentile(fighter, pool) -- returns InactivityResult or None
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fighter import Fighter

# ── Part 1: Global clock ──────────────────────────────────────────────────────

SIM_DAYS_PER_FIGHT: int = 2
"""Simulated days that elapse per fight processed. First-pass estimate."""

_current_day: int = 0


def reset_sim_clock() -> None:
    """Reset the global clock to day 0. Call at the start of each simulation run."""
    global _current_day
    _current_day = 0


def advance_sim_clock() -> None:
    """Advance the clock by SIM_DAYS_PER_FIGHT. Call once per fight processed."""
    global _current_day
    _current_day += SIM_DAYS_PER_FIGHT


def get_sim_day() -> int:
    """Return the current simulated day."""
    return _current_day


def days_since(past_day: int) -> int:
    """Return how many simulated days have elapsed since past_day.

    past_day should be a sim_day value stored on a FightResult (>= 0).
    Returns 0 when past_day equals the current day.
    """
    return _current_day - past_day


# ── Part 2: Population-relative inactivity ────────────────────────────────────

INACTIVITY_PERCENTILE_THRESHOLD: float = 85.0
"""A fighter whose gap-since-last-fight is longer than this percentage of their
valid peers' gaps is considered 'relatively inactive'.

At 85.0 with a 15-fighter Elite pool:
  rank-15 (longest gap): 14/15 * 100 = 93.3% -> flagged
  rank-14:               13/15 * 100 = 86.7% -> flagged
  rank-13:               12/15 * 100 = 80.0% -> NOT flagged
  => 2 of 15 flagged (13.3%), within the intended top-15% band.

First-pass estimate -- tune when wiring into title-fight challenger selection.
"""

_MIN_COMPARISON_POOL: int = 2
"""Minimum number of fighters with valid (stamped) fight history needed for a
meaningful percentile. Below this, inactivity_percentile returns None."""


@dataclass(frozen=True)
class InactivityResult:
    """Return value of inactivity_percentile().

    gap_days:              days since this fighter's most recent stamped fight
    percentile:            0-100; fighter's gap is longer than this fraction
                           of valid pool members' gaps
    is_relatively_inactive: True if percentile >= INACTIVITY_PERCENTILE_THRESHOLD
    n_valid:               count of pool members included in the comparison
                           (fighters with at least one stamped fight)
    """
    gap_days:               int
    percentile:             float
    is_relatively_inactive: bool
    n_valid:                int


def _last_stamped_day(fighter: Fighter) -> int | None:
    """Return the sim_day of the fighter's most recent stamped fight, or None.

    Fights with sim_day == -1 are pre-calendar sentinels and are excluded.
    A fighter with only pre-calendar fights is treated as having no usable
    history — their gap would be undefined and misleading.
    """
    days = [r.sim_day for r in fighter.fight_history if r.sim_day >= 0]
    return max(days) if days else None


def inactivity_percentile(
    fighter: Fighter,
    pool: list[Fighter],
) -> InactivityResult | None:
    """Measure a fighter's inactivity relative to their peers in pool.

    For every fighter in pool, computes days_since(last stamped fight).
    Fighters with no fight history or only sim_day==-1 (pre-calendar) are
    EXCLUDED from the comparison distribution -- their gap is undefined and
    including them would distort the percentile for the real population.

    The target fighter is expected to be a member of pool (the typical case:
    "how inactive is this Elite fighter relative to the rest of the Elite
    pool?"). Cross-pool calls (target not in pool) also work correctly --
    the distribution is simply built from pool alone.

    Percentile formula:
      100 * (# valid pool members with gap STRICTLY LESS than target's gap)
            / (# valid pool members)

    Ties (equal gaps) do NOT count as "shorter" -- this keeps the percentile
    at its lower bound for ties. In the degenerate case where all fighters
    have the same gap, everyone gets percentile=0 (no one is more inactive
    than anyone else) and nothing is flagged. This is the correct behavior.

    Recomputes fresh on every call -- no caching. The pool's gap distribution
    shifts as the simulation progresses, so a snapshot would go stale.

    Returns None if:
      - target fighter has no valid (stamped) fight history
      - fewer than _MIN_COMPARISON_POOL fighters in pool have valid history
    """
    target_last = _last_stamped_day(fighter)
    if target_last is None:
        return None

    target_gap = days_since(target_last)

    valid_gaps: list[int] = []
    for f in pool:
        last = _last_stamped_day(f)
        if last is None:
            continue
        valid_gaps.append(days_since(last))

    if len(valid_gaps) < _MIN_COMPARISON_POOL:
        return None

    n_shorter = sum(1 for g in valid_gaps if g < target_gap)
    percentile = 100.0 * n_shorter / len(valid_gaps)

    return InactivityResult(
        gap_days=target_gap,
        percentile=percentile,
        is_relatively_inactive=percentile >= INACTIVITY_PERCENTILE_THRESHOLD,
        n_valid=len(valid_gaps),
    )
