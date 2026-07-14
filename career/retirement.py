"""
retirement.py — Age-driven retirement evaluation for the MMA career sim.

Three retirement paths, evaluated every LABEL_UPDATE_INTERVAL fights (same
cadence as maybe_update_labels and maybe_evaluate_cut — same trigger condition,
checked BEFORE cut evaluation so retirement takes priority for fighters that
qualify for both):

  Path 3: Retire on top (checked FIRST — highest honor)
    Fighter currently holds Legend OR Champion label AND is past prime.
    Small voluntary probability (5% per eval) — rare, flavorful event.
    Reason logged: "retired_on_top".

  Path 1: Washed + past prime (checked second)
    Fighter carries Washed label AND is past prime (_PRIME_END from age.py).
    Moderate probability (35% per eval) — age-appropriate exit framing.
    Distinct from cut's Washed path: cut requires specific recent form
    (0-1 wins, 50%+ finishes), this requires only Washed + age threshold.
    Reason logged: "retired".

  Path 2: Steep decline (checked last — universal safety mechanism)
    Applies to ALL fighters past prime regardless of labels.
    Probability scales linearly with the actual age-curve overall penalty
    (computed via apply_age_to_fighter, so attribute-group multipliers are
    included automatically).  With _RETIRE_DECLINE_SCALE = 30:
      age 35 (penalty ≈  3.8):  prob ≈ 0.127
      age 38 (penalty ≈  9.7):  prob ≈ 0.323
      age 40 (penalty ≈ 15.2):  prob ≈ 0.507
      age 42 (penalty ≈ 21.6):  prob ≈ 0.720
      age 45 (penalty ≈ 33.8):  capped at 0.95
    The COMPOUNDING effect (multiple evaluations as the fighter keeps aging)
    makes survival to extreme old age astronomically unlikely without a hard
    ceiling.  Reason logged: "retired".

Shared removal machinery:
  Actual pool removal and title vacancy are delegated to execute_removal()
  in cuts.py (same identity-based pool slice, same vacate_title() call, same
  _removed_fighter_ids guard).  No removal logic is duplicated here.

Caller contract (same as maybe_evaluate_cut):
  Returns True if the fighter was retired this call; caller must remove fighter
  from all_fighters.  Pool removal and title vacancy are already handled.
"""
from __future__ import annotations

import random

from career.fighter import Fighter
from career.labels import WASHED, LEGEND, CHAMPION, LABEL_UPDATE_INTERVAL, is_champion
from career.age import _PRIME_END, apply_age_to_fighter
from career.cuts import execute_removal, is_removed
from sim_calendar import get_sim_day, days_since, _last_stamped_day


# ── Tuning ────────────────────────────────────────────────────────────────────
# All are first-pass estimates; retune once population age distributions are
# observable.  _RETIRE_DECLINE_SCALE is the key lever: lower = faster forced
# exit; higher = longer active careers at extreme age.

_RETIRE_ON_TOP_PROB:   float = 0.05   # Path 3: per-eval probability for Legend/Champion past prime
_RETIRE_WASHED_PROB:   float = 0.35   # Path 1: per-eval probability for Washed past prime
_RETIRE_DECLINE_SCALE: float = 30.0   # Path 2: penalty / scale → probability (before cap)
_RETIRE_DECLINE_CAP:   float = 0.95   # Path 2: max per-eval probability (never 1.0; no hard ceiling)


# ── Age-penalty helper ────────────────────────────────────────────────────────

def age_penalty(fighter: Fighter) -> float:
    """
    Overall-point penalty from the age DECLINE curve for fighters past prime.
    Returns 0.0 for fighters at or below prime-end age (including developing
    fighters below prime-start, whose youth deficit must NOT be read as decline).

    Uses apply_age_to_fighter() so per-attribute-group multipliers (explosive/
    technical/mixed) are included automatically.
    """
    if fighter.age <= _PRIME_END:   # prime or still-developing: no retirement risk
        return 0.0
    eff = apply_age_to_fighter(fighter)
    if eff is fighter:              # shouldn't happen since age > _PRIME_END
        return 0.0
    return max(0.0, fighter.overall - eff.overall)


def _decline_prob(penalty: float) -> float:
    """Path 2: linear scale from 0 to _RETIRE_DECLINE_CAP as penalty grows."""
    if penalty <= 0.0:
        return 0.0
    return min(penalty / _RETIRE_DECLINE_SCALE, _RETIRE_DECLINE_CAP)


# ── Core evaluation ───────────────────────────────────────────────────────────

def _retirement_reason(fighter: Fighter) -> str | None:
    """
    Evaluate all three retirement paths in priority order.
    Returns the reason string if the fighter retires this cycle, None otherwise.
    Only the FIRST matching path fires per evaluation.
    """
    labels = fighter.labels
    age    = fighter.age

    # Path 3: retire on top — checked first so a Champion/Legend who is also
    # Washed exits with honor rather than being caught by Path 1.
    if (LEGEND in labels or CHAMPION in labels) and age > _PRIME_END:
        if random.random() < _RETIRE_ON_TOP_PROB:
            return "retired_on_top"

    # Path 1: Washed + past prime.  Distinct from cut's Washed path:
    # cut requires specific recent form AND fires only if retirement didn't fire.
    if WASHED in labels and age > _PRIME_END:
        if random.random() < _RETIRE_WASHED_PROB:
            return "retired"

    # Path 2: universal decline-driven — fires for any fighter past prime.
    penalty = age_penalty(fighter)
    prob    = _decline_prob(penalty)
    if prob > 0.0 and random.random() < prob:
        return "retired"

    return None


# ── Periodic hook (call BEFORE maybe_evaluate_cut) ───────────────────────────

def maybe_evaluate_retirement(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int = 0,
) -> bool:
    """
    Fire every LABEL_UPDATE_INTERVAL fights (same cadence as maybe_update_labels
    and maybe_evaluate_cut).

    Must be called BEFORE maybe_evaluate_cut in the sim loop.  If this returns
    True, the fighter is added to _removed_fighter_ids, causing the subsequent
    cut check to short-circuit.

    Returns True if the fighter retired this call; False otherwise.
    Pool removal and title vacancy handled by execute_removal(); caller removes
    from all_fighters.
    """
    n = len(fighter.fight_history)
    if n == 0 or n % LABEL_UPDATE_INTERVAL != 0:
        return False
    if is_removed(fighter.fighter_id):
        return False

    reason = _retirement_reason(fighter)
    if reason:
        execute_removal(fighter, pools, fight_num, reason)
        return True
    return False


# ── Global inactive-fighter retirement scan ───────────────────────────────────
# Complements maybe_evaluate_retirement (which only fires on fight-count multiples).
# Fighters who stop competing never cross those multiples, so this periodic
# calendar-based scan is the only path that can retire them from elapsed time alone.

RETIRE_INACTIVE_SCAN_DAYS: int = 182
"""How often (in sim-days) to scan for fighters who have gone inactive.
~Every 91 fights at 2 days/fight -- roughly twice a simulated year."""

RETIRE_INACTIVE_GAP_DAYS: int = 365
"""A fighter whose most recent stamped fight is this many days in the past is
considered inactive and becomes eligible for retirement evaluation."""

CHAMPION_INACTIVE_GAP_DAYS: int = 1095
"""Reigning champions get a much longer leash than RETIRE_INACTIVE_GAP_DAYS
before the inactive scan will evaluate them -- their gaps are usually just
title-defense scheduling (tier4 defenses average ~310 days apart; small-org
tier1/tier2 belts run slower still). But the old behavior of skipping
champions ENTIRELY made an unlucky champion immortal: excluded from ordinary
matchmaking by design, never defending because their org pool's title
counter crawled, and never retirement-evaluated -- a mid-major champion was
observed frozen for 53 sim-years (matchmaking-audit session). Three years
without a single defense is genuine inactivity, not scheduling; the normal
age-gated evaluation applies (execute_removal vacates the belt if they go)."""

_last_inactive_scan_day: int = 0


def reset_retirement_scanning() -> None:
    """Reset the inactive-scan clock to day 0.  Call at the start of each sim."""
    global _last_inactive_scan_day
    _last_inactive_scan_day = 0


def maybe_retire_inactive(
    all_fighters: list[Fighter],
    pools:        dict[str, dict[str, list[Fighter]]],
    fight_num:    int = 0,
) -> list[Fighter]:
    """Biannual scan for fighters inactive for >= RETIRE_INACTIVE_GAP_DAYS.

    For each eligible fighter, runs the same three-path retirement evaluation
    used by maybe_evaluate_retirement.  Fighters with no stamped fight history
    are skipped (they may simply be waiting for their first match-up).

    Returns the list of fighters retired during this scan so the caller can
    remove them from all_fighters.  Pool removal and title vacancy are handled
    internally by execute_removal() -- callers only need to clean up all_fighters.

    Per-fight maybe_evaluate_retirement is NOT replaced: it continues evaluating
    active fighters on the fight-count cadence.  This function adds the parallel
    path for fighters who have fallen off the active schedule.
    """
    global _last_inactive_scan_day
    current_day = get_sim_day()
    if current_day - _last_inactive_scan_day < RETIRE_INACTIVE_SCAN_DAYS:
        return []

    _last_inactive_scan_day += RETIRE_INACTIVE_SCAN_DAYS

    retired: list[Fighter] = []
    for fighter in all_fighters:
        if is_removed(fighter.fighter_id):
            continue
        gap_threshold = RETIRE_INACTIVE_GAP_DAYS
        if is_champion(fighter):
            # A reigning champion's only fights are scheduled title defenses
            # (matchmaking.py excludes them from ordinary matchmaking), so a
            # MODERATE calendar gap is a scheduling artifact, not genuine
            # inactivity -- but an unconditional skip here made champions
            # immortal (see CHAMPION_INACTIVE_GAP_DAYS). Champions are only
            # evaluated after that much longer threshold; if one retires,
            # execute_removal vacates the belt as usual.
            gap_threshold = CHAMPION_INACTIVE_GAP_DAYS
        last = _last_stamped_day(fighter)
        if last is None:
            # Never had a real (stamped) fight. This USED to be an
            # unconditional skip -- which made never-matched fighters
            # immortal: the fight-count-cadenced maybe_evaluate_retirement
            # never fires at n=0 fights, and this scan was the only other
            # exit path. Measured (7000-attempt seed-42 baseline): 100
            # active fighters had existed 10+ years without a single fight,
            # including day-0 originals still "active" at age 58+ purely
            # because nothing could ever retire them. Anchor on created_day
            # instead: a long-unmatched fighter still faces the same
            # age-gated three-path evaluation as everyone else (all paths
            # are no-ops until past prime, so a young waiting prospect is
            # untouched -- exactly the original comment's concern).
            last = fighter.created_day
        if days_since(last) < gap_threshold:
            continue   # still within the activity window
        reason = _retirement_reason(fighter)
        if reason:
            execute_removal(fighter, pools, fight_num, reason)
            retired.append(fighter)

    return retired
