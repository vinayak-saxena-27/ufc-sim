"""
cuts.py — Label-aware, performance-driven cut evaluation for the MMA career sim.

A "cut" removes a fighter entirely from the active simulation pool.
It is distinct from demotion (which lowers tier but keeps the fighter active).
Cut decisions are driven solely by label + recent form, and are
AGE-INDEPENDENT — age-driven retirement is handled by retirement.py.

Cut risk tiers (by label):
  Washed:               Primary signal. 65% cut probability per evaluation cycle
                        when recent form confirms the decline with majority finishes.
  Gatekeeper/Journeyman: Low risk (25%) only on complete non-competitive collapse —
                        these archetypes are protected but not immune.
  No label:             Moderate safety-net fallback (40%) for sustained non-
                        competitive losing with enough history to rule out variance.
  Prospect/Contender/Champion/Legend: Protected. Skip evaluation entirely.

Competitiveness signal (consistent with the label classifier):
  competitive loss     = method == "decision"   (went the distance)
  non-competitive loss = method != "decision"   (KO/TKO or submission)

Evaluation cadence: every LABEL_UPDATE_INTERVAL fights per fighter — same trigger
as maybe_update_labels. Labels are updated first (in sim.py) so cut evaluation
always reads the freshest label set.

Shared removal machinery (execute_removal):
  Both cuts and retirements (retirement.py) call execute_removal() so pool
  removal, title vacancy, and the stale-reference guard are never duplicated.
  The `reason` field on CutRecord distinguishes the cause:
    "cut"            — performance-driven release (this module)
    "retired"        — age/decline-driven retirement (retirement.py)
    "retired_on_top" — voluntary retirement while still Champion/Legend
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from career.fighter import Fighter
from career.labels import (
    PROSPECT, GATEKEEPER, JOURNEYMAN, WASHED, CONTENDER, CHAMPION, LEGEND,
    LABEL_UPDATE_INTERVAL, get_champion_id, vacate_title,
)


# ── Risk-tier thresholds ──────────────────────────────────────────────────────
# UNCHANGED from the cut-logic session — do not modify these for retirement work.

_PROTECTED_LABELS: frozenset[str] = frozenset({PROSPECT, CONTENDER, CHAMPION, LEGEND})

# Washed — primary cut signal
_WASHED_WINDOW:       int   = 5
_WASHED_MAX_WIN_RATE: float = 0.20   # 0 or 1 win out of 5 recent fights
_WASHED_MIN_NC_RATE:  float = 0.50   # >= 50% of those losses by KO/TKO or sub
_WASHED_CUT_PROB:     float = 0.65   # per-eval-cycle probability if criteria met

# Gatekeeper / Journeyman — low but non-zero; only fires on extreme collapse
_GK_JN_WINDOW:       int   = 5
_GK_JN_MAX_WIN_RATE: float = 0.00   # 0 wins in last 5 (total collapse)
_GK_JN_MIN_NC_RATE:  float = 0.80   # 80%+ of those losses by finish
_GK_JN_CUT_PROB:     float = 0.25

# No label — moderate safety-net fallback for early/unlabelled fighters
_UNLABELLED_MIN_FIGHTS:   int   = 8    # need history to distinguish variance
_UNLABELLED_WINDOW:       int   = 6
_UNLABELLED_MAX_WIN_RATE: float = 0.17  # 0 or 1 win out of 6
_UNLABELLED_MIN_NC_RATE:  float = 0.67  # at least 2/3 of losses by finish
_UNLABELLED_CUT_PROB:     float = 0.40


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class CutRecord:
    fight_num:     int
    fighter_name:  str
    fighter_id:    str
    tier:          str
    weight_class:  str
    record_str:    str
    labels_at_cut: frozenset[str]
    title_vacated: bool
    reason:        str = "cut"   # "cut" | "retired" | "retired_on_top"
    age_at_event:  int = 0       # fighter.age at time of removal


_cut_log:             list[CutRecord] = []
_removed_fighter_ids: set[str]        = set()   # stale-reference guard (cuts + retirements)


def reset_cut_registry() -> None:
    """Clear log and removed-ID set. Call at the start of each simulation."""
    _cut_log.clear()
    _removed_fighter_ids.clear()


def get_cut_log() -> list[CutRecord]:
    """Snapshot of all cuts AND retirements logged this simulation run."""
    return list(_cut_log)


def is_removed(fighter_id: str) -> bool:
    """True if fighter has already been cut or retired (stale-reference guard)."""
    return fighter_id in _removed_fighter_ids


# ── Helpers ───────────────────────────────────────────────────────────────────

def _recent_win_rate(recent: list) -> float:
    if not recent:
        return 1.0
    return sum(1 for r in recent if r.outcome == "win") / len(recent)


def _nc_loss_rate(recent: list) -> float:
    """Fraction of recent losses that are non-competitive (finished, not decision)."""
    losses = [r for r in recent if r.outcome == "loss"]
    if not losses:
        return 0.0
    return sum(1 for r in losses if r.method != "decision") / len(losses)


# ── Core evaluation ───────────────────────────────────────────────────────────

def _should_cut(fighter: Fighter) -> bool:
    """
    Returns True if fighter meets cut criteria for their label tier.
    Probabilistic — same criteria can return True on one cycle and False on another.
    Labels updated before this call (in sim.py / title.py) so reads are always fresh.
    """
    labels  = fighter.labels
    history = fighter.fight_history
    n       = len(history)

    if labels & _PROTECTED_LABELS:
        return False

    if WASHED in labels:
        if n < _WASHED_WINDOW:
            return False
        recent = history[-_WASHED_WINDOW:]
        if (
            _recent_win_rate(recent) <= _WASHED_MAX_WIN_RATE
            and _nc_loss_rate(recent) >= _WASHED_MIN_NC_RATE
        ):
            return random.random() < _WASHED_CUT_PROB
        return False

    if GATEKEEPER in labels or JOURNEYMAN in labels:
        if n < _GK_JN_WINDOW:
            return False
        recent = history[-_GK_JN_WINDOW:]
        if (
            _recent_win_rate(recent) <= _GK_JN_MAX_WIN_RATE
            and _nc_loss_rate(recent) >= _GK_JN_MIN_NC_RATE
        ):
            return random.random() < _GK_JN_CUT_PROB
        return False

    # No active label — moderate safety-net
    if n < _UNLABELLED_MIN_FIGHTS:
        return False
    recent = history[-_UNLABELLED_WINDOW:]
    if (
        _recent_win_rate(recent) <= _UNLABELLED_MAX_WIN_RATE
        and _nc_loss_rate(recent) >= _UNLABELLED_MIN_NC_RATE
    ):
        return random.random() < _UNLABELLED_CUT_PROB
    return False


# ── Shared removal action ─────────────────────────────────────────────────────

def execute_removal(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int = 0,
    reason:    str = "cut",
) -> CutRecord:
    """
    Shared removal function for both cuts (reason="cut") and retirements
    (reason="retired" or "retired_on_top").

    Actions:
      - Remove fighter from pools[wc][tier] via identity comparison.
      - Vacate title if fighter currently holds the belt at their tier+division.
      - Add fighter_id to _removed_fighter_ids (stale-reference guard).
      - Append CutRecord to _cut_log.

    Caller must remove fighter from all_fighters (this handles pools only).
    """
    wc   = fighter.weight_class
    tier = fighter.tier

    pool = pools.get(wc, {}).get(tier, [])
    pool[:] = [f for f in pool if f is not fighter]

    title_vacated = False
    _org = fighter.org if tier in ("tier1", "tier2", "tier4") else ""
    if get_champion_id(wc, tier, _org) == fighter.fighter_id:
        vacate_title(wc, tier, _org)
        title_vacated = True

    _removed_fighter_ids.add(fighter.fighter_id)
    rec = CutRecord(
        fight_num     = fight_num,
        fighter_name  = fighter.name,
        fighter_id    = fighter.fighter_id,
        tier          = tier,
        weight_class  = wc,
        record_str    = fighter.record_str,
        labels_at_cut = frozenset(fighter.labels),
        title_vacated = title_vacated,
        reason        = reason,
        age_at_event  = fighter.age,
    )
    _cut_log.append(rec)
    return rec


# ── Periodic hook (call after maybe_update_labels) ───────────────────────────

def maybe_evaluate_cut(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int = 0,
) -> bool:
    """
    Fire every LABEL_UPDATE_INTERVAL fights (same cadence as maybe_update_labels).
    Returns True if the fighter was cut; False otherwise.

    Called AFTER maybe_evaluate_retirement in the sim loop; a fighter already
    retired will be in _removed_fighter_ids and this call returns False immediately.

    When True: fighter removed from pools and any held title vacated.
    Caller must also remove fighter from all_fighters.
    """
    n = len(fighter.fight_history)
    if n == 0 or n % LABEL_UPDATE_INTERVAL != 0:
        return False
    if is_removed(fighter.fighter_id):
        return False
    if _should_cut(fighter):
        execute_removal(fighter, pools, fight_num, "cut")
        return True
    return False
