"""
hype.py -- Dynamic, career-driven hype system (style-to-hype overhaul).

## Design

Hype used to be a static value set at generation (power/athleticism + noise)
that barely moved afterward. This module replaces that with hype EARNED
through career events:
  - finishes (scaled by round and opponent quality)
  - decision wins (smaller)
  - adversity/comeback wins (extra, independent of method)
  - losses (finish losses hurt more than decision losses, scaled by round)
  - style: an exciting, multi-phase fight nudges hype up; a fight won
    entirely via passive ground control with no finish threat taxes it
  - title wins/defenses/losses (wired separately in title.py)
  - inactivity decay (annual sweep, proportional to current hype)

Generation-time hype (career/style_mixing.py's counterpart for
style_flexibility) now lives in generate_hype_seed() below -- power and
athleticism are no longer direct hype inputs at generation; they influence
hype indirectly through what a fighter does in fights (a high-power fighter
finishes more, which earns hype via update_hype_after_fight()).

## Modifier layer pattern -- deliberately NOT followed here

Unlike age/fatigue/development, hype is NOT a derived-at-fight-time modifier
on top of an immutable base. hype IS the accumulated value, mutated directly
(fighter.hype +=) -- same pattern as fighter.development_modifier or
weight_transfers.py's opportunity hype boost. There's no "effective hype"
computed fresh each fight; rankings/labels/matchmaking read fighter.hype
directly, unchanged by this session.

## Constants

All constants are first-pass estimates, consistent with how every other
constant in this project has been handled -- flag for retuning once
long-run hype trajectories are observed.
"""
from __future__ import annotations

import random

from career.fighter import Fighter, FightResult
from career.rankings import is_ranked
from career.age import SIM_DAYS_PER_YEAR  # reuse the same year-length constant
from sim_calendar import get_sim_day

# ── Part 1: Generation seed ────────────────────────────────────────────────────

HYPE_SEED_SIGMA: float = 4.0
"""Genuine uncertainty about an unproven fighter's buzz potential. First-pass."""


def generate_hype_seed(pipeline_strength: float) -> float:
    """hype = gauss(0, HYPE_SEED_SIGMA) + academy_pipeline_modifier.

    Power/athleticism are deliberately NOT inputs here anymore -- they earn
    hype indirectly through fight events (finishes, adversity) via
    update_hype_after_fight(). pipeline_strength stays from the existing 5b
    system: well-connected academies give a small initial visibility edge.
    """
    return random.gauss(0.0, HYPE_SEED_SIGMA) + pipeline_strength


# ── Part 4: Bounds ──────────────────────────────────────────────────────────────

HYPE_FLOOR: float = -10.0
"""Soft floor -- a fighter can become genuinely irrelevant but not infinitely
negative. Uncapped on the positive side (consistent with the sim's zero-
centered, uncapped rating philosophy -- true superstars can go very high)."""


def _apply_floor(hype: float) -> float:
    return max(HYPE_FLOOR, hype)


# ── Part 3: Win/loss base modifiers ─────────────────────────────────────────────

FINISH_WIN_BASE: dict[str, float] = {"KO/TKO": 6.0, "submission": 5.0}
"""KO/TKO slightly more exciting than submission on average; both clearly positive."""

DECISION_WIN_BASE: float = 2.0
"""Small positive, clearly smaller than any finish win."""

FINISH_LOSS_BASE: dict[str, float] = {"KO/TKO": -5.0, "submission": -4.0}
DECISION_LOSS_BASE: float = -1.0
"""Small negative, clearly smaller magnitude than a finish loss."""

FINISH_ROUND_DECAY: float = 0.8
"""Round-of-finish scaling: R1=1.0x, R2=0.8x, R3=0.64x, ... Applies to both
finish wins (earlier finish = more buzz) and finish losses (earlier loss =
more damaging, per spec: "R1 KO is more damaging than a late submission loss")."""

RANKED_OPPONENT_BONUS: float = 3.0
"""Flat bonus added to a WIN's base modifier when the opponent is currently
ranked (career.rankings.is_ranked). Not applied to losses -- losing to a
ranked opponent isn't extra embarrassing in this first-pass model."""

ADVERSITY_COMEBACK_BONUS: float = 4.0
"""Additional bonus on top of the base win modifier when FightResult.adversity_comeback
is set (winner survived meaningful finish-danger and still won), regardless of method."""


def _round_scale(rounds_completed: int) -> float:
    """R1 -> 1.0, R2 -> FINISH_ROUND_DECAY, R3 -> FINISH_ROUND_DECAY**2, ..."""
    n = max(1, rounds_completed)
    return FINISH_ROUND_DECAY ** (n - 1)


def _win_modifier(result: FightResult, opponent: Fighter) -> float:
    if result.method == "decision":
        base = DECISION_WIN_BASE
    else:
        base = FINISH_WIN_BASE.get(result.method, FINISH_WIN_BASE["KO/TKO"])
        base *= _round_scale(result.rounds_completed)

    if is_ranked(opponent):
        base += RANKED_OPPONENT_BONUS
    if result.adversity_comeback:
        base += ADVERSITY_COMEBACK_BONUS
    return base


def _loss_modifier(result: FightResult) -> float:
    if result.method == "decision":
        return DECISION_LOSS_BASE
    base = FINISH_LOSS_BASE.get(result.method, FINISH_LOSS_BASE["KO/TKO"])
    return base * _round_scale(result.rounds_completed)


# ── Part 3: Style modifiers ─────────────────────────────────────────────────────
# Reuses the phase-distribution fields already on FightResult (style-mixing
# session) -- no new plumbing needed. "Boring" specifically requires a decision:
# a fighter whose own pressure crossed a finish threshold necessarily won by
# finish, so method=="decision" is already the correct "never threatened a
# finish" proxy without needing fight-wide pressure totals.

EXCITING_MAX_DOMINANT_PHASE_FRACTION: float = 0.75
"""A fight where no single phase (STANDING/CLINCH/GROUND) consumed more than
this fraction of total time counts as multi-phase / exciting."""

BORING_GROUND_TIME_FRACTION: float = 0.55
"""A decision win with at least this much of the fight spent in GROUND counts
as passive control without finish threat -- the boring tax."""

EXCITING_FIGHT_BONUS: float = 1.0
BORING_FIGHT_TAX: float = -1.5


def _style_modifier(result: FightResult) -> float:
    total_time = result.time_standing + result.time_clinch + result.time_ground
    if total_time <= 0.0:
        return 0.0

    ground_frac = result.time_ground / total_time
    if result.method == "decision" and ground_frac >= BORING_GROUND_TIME_FRACTION:
        return BORING_FIGHT_TAX

    max_frac = max(result.time_standing, result.time_clinch, result.time_ground) / total_time
    if max_frac < EXCITING_MAX_DOMINANT_PHASE_FRACTION:
        return EXCITING_FIGHT_BONUS

    return 0.0


# ── Part 3: Per-fight update entry point ────────────────────────────────────────

def update_hype_after_fight(fighter: Fighter, opponent: Fighter) -> None:
    """
    Apply the base win/loss + style hype modifiers for fighter's most recent
    fight (fighter.fight_history[-1] -- must be called right after
    simulate_fight() records the result, same convention as
    career.development.apply_phase_development_feedback).

    Call for BOTH participants (winner and loser) after every fight, including
    title fights (title.py applies its own additional title-specific bonus on
    top of this). opponent is passed explicitly (not resolved from pools) so
    is_ranked() can be checked directly -- callers already have both Fighter
    objects in hand from simulate_fight()'s return value.
    """
    if not fighter.fight_history:
        return
    result = fighter.fight_history[-1]

    delta = _win_modifier(result, opponent) if result.outcome == "win" else _loss_modifier(result)
    delta += _style_modifier(result)

    fighter.hype = _apply_floor(fighter.hype + delta)


# ── Part 3: Title events (bonuses only; wiring/defense-counting in title.py) ───

TITLE_WIN_HYPE_BONUS: float = 15.0
"""Winning the belt -- either from vacant or by dethroning the champion."""

TITLE_DEFENSE_BASE_BONUS: float = 8.0
TITLE_DEFENSE_DECAY: float = 0.85
"""Defense bonus = TITLE_DEFENSE_BASE_BONUS * TITLE_DEFENSE_DECAY**n_prior_defenses
-- the first defense is more exciting than the eighth."""

TITLE_LOSS_HYPE_PENALTY: float = -10.0
"""Applied to the fighter who loses the belt (was champion going in)."""


def title_win_bonus() -> float:
    return TITLE_WIN_HYPE_BONUS


def title_defense_bonus(n_prior_defenses: int) -> float:
    return TITLE_DEFENSE_BASE_BONUS * (TITLE_DEFENSE_DECAY ** max(0, n_prior_defenses))


def title_loss_penalty() -> float:
    return TITLE_LOSS_HYPE_PENALTY


def apply_title_hype(fighter: Fighter, delta: float) -> None:
    """Apply a title-event hype delta (win/defense/loss), floor-clamped.
    Called from title.py, separate from and additive with update_hype_after_fight()."""
    fighter.hype = _apply_floor(fighter.hype + delta)


# ── Inactivity decay (calendar-driven annual sweep) ─────────────────────────────
# Same cadence pattern as career.age.advance_all_ages / career.development.
# advance_all_development -- fires once per SIM_DAYS_PER_YEAR elapsed.

HYPE_DECAY_RATE: float = 0.08
"""Proportional annual decay for hype ABOVE HYPE_DECAY_HIGH_THRESHOLD: high-hype
fighters who go quiet fade fastest (more public attention to lose)."""

HYPE_DECAY_RATE_MODERATE: float = 0.03
"""Proportional annual decay for hype between 0 and HYPE_DECAY_HIGH_THRESHOLD."""

HYPE_DECAY_HIGH_THRESHOLD: float = 30.0
"""Above this hype level, decay uses HYPE_DECAY_RATE instead of the moderate rate."""


def _decay_rate_for(hype: float) -> float:
    if hype <= 0.0:
        return 0.0   # near-zero/negative hype: nothing left to lose
    if hype >= HYPE_DECAY_HIGH_THRESHOLD:
        return HYPE_DECAY_RATE
    return HYPE_DECAY_RATE_MODERATE


_last_hype_decay_day: int = 0


def reset_hype_decay() -> None:
    """Reset the decay-advancement clock to day 0. Call at the start of each sim."""
    global _last_hype_decay_day
    _last_hype_decay_day = 0


def advance_all_hype_decay(all_fighters: list[Fighter]) -> None:
    """
    Apply proportional annual hype decay to every fighter. Fires once per
    SIM_DAYS_PER_YEAR elapsed -- same cadence as advance_all_ages() /
    advance_all_development(). Decay rate is proportional (hype * rate), so
    it naturally slows as hype approaches zero, and is near-zero for
    low/negative-hype fighters (they can't lose hype they don't have).
    """
    global _last_hype_decay_day
    current_day = get_sim_day()
    while current_day - _last_hype_decay_day >= SIM_DAYS_PER_YEAR:
        for f in all_fighters:
            rate = _decay_rate_for(f.hype)
            if rate > 0.0:
                f.hype = _apply_floor(f.hype - f.hype * rate)
        _last_hype_decay_day += SIM_DAYS_PER_YEAR
