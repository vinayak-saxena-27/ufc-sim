"""
title.py — Minimal title-fight scheduling for the MMA career sim.

For each (weight_class, tier_key) pair that supports titles (Regional through
Elite per TIER_RULESET — Amateur has no title fights), a title fight is
scheduled once every TITLE_FIGHT_INTERVAL regular fights registered for that
pool.

Challenger selection (deliberately simple — not a full rankings system):
  Vacant belt  → top-2 overall fighters in the pool fight for the title.
  Occupied belt → current champion defends vs the highest-overall eligible
                  non-champion in the same pool.

Champion status is stored in labels.py's _title_holders registry via
award_title(), which this module calls immediately after every title fight
resolves (before tier transitions, so the belt is registered for the tier
the fight took place at).

Scheduling counter:
  Incremented once per regular fight using fighter A's tier+weight_class.
  Pools that see more regular fights get proportionally more title fights —
  i.e. a large, active tier gets more title events than a sparse one, which
  is realistic.

Known gap — vacant title on demotion:
  If a champion is demoted out of their pool between title fights, the belt
  is implicitly treated as vacant on the next scheduled fight (the pool lookup
  finds no fighter matching the stored champion_id). Formal vacate-title
  logic is deferred to the Part 3 retirement/cut session, as that's where
  fighter exits are modelled.

Round count:
  Title fight round count comes from TIER_RULESET[tier_key].title_rounds,
  which simulate_full_fight already reads correctly.  TitleFightRecord
  stores rounds_completed from the FightResult so callers can spot-check:
    Regional (tier1) title fights   → 3 rounds max
    Mid-major / Top-org / Elite     → 5 rounds max
  A decision fight always ran the maximum rounds for that tier.
"""
from __future__ import annotations

from dataclasses import dataclass

from fighter import Fighter
from fight import simulate_fight
from labels import award_title, get_champion_id, maybe_update_labels
from matchmaking import apply_tier_transitions
from tiers import TIER_RULESET
from age import maybe_advance_age
from cuts import maybe_evaluate_cut
from retirement import maybe_evaluate_retirement


# ── Tuning ────────────────────────────────────────────────────────────────────

TITLE_FIGHT_INTERVAL: int = 15
"""
Regular fights per (weight_class, tier_key) before a title fight is scheduled.
Rationale: with ~2 000 total regular fights across 15 pools (3 WC × 5 tiers),
each pool averages ~133 fights → ~8–9 title bouts per pool per full sim run.
That leaves room for meaningful champion tenures and occasional belt changes
without title fights overwhelming the calendar.

Flag as a first-pass estimate.  If champion turnover feels too fast or slow,
adjust here; no other file needs to change.
"""

_MIN_POOL_SIZE: int = 2   # need at least 2 fighters to hold a title fight


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class TitleFightRecord:
    fight_num:        int    # which regular-fight iteration triggered this bout
    weight_class:     str
    tier_key:         str
    winner_name:      str
    loser_name:       str
    method:           str    # "decision", "KO/TKO", or "submission"
    rounds_completed: int    # actual rounds run; equals title_rounds on a decision
    was_vacant:       bool   # True → both fighters competed for a vacant belt


_fight_counters: dict[tuple[str, str], int]  = {}
_title_history:  list[TitleFightRecord]       = []


def reset_title_scheduling() -> None:
    """Clear fight counters and title history.  Call at the start of each sim."""
    _fight_counters.clear()
    _title_history.clear()


def get_title_history() -> list[TitleFightRecord]:
    """Return a snapshot of all title fights run this simulation."""
    return list(_title_history)


# ── Challenger selection ──────────────────────────────────────────────────────

def _find_champion(pool: list[Fighter], champion_id: str) -> Fighter | None:
    return next((f for f in pool if f.fighter_id == champion_id), None)


def _pick_challenger(pool: list[Fighter], champion_id: str) -> Fighter | None:
    """Highest-overall fighter in the pool who is not the current champion."""
    eligible = [f for f in pool if f.fighter_id != champion_id]
    return max(eligible, key=lambda f: f.overall) if eligible else None


def _pick_vacant_pair(pool: list[Fighter]) -> tuple[Fighter, Fighter] | None:
    """Top-2 overall for a vacant-belt fight.  Returns None if pool is too small."""
    if len(pool) < _MIN_POOL_SIZE:
        return None
    top2 = sorted(pool, key=lambda f: f.overall, reverse=True)[:2]
    return top2[0], top2[1]


# ── Main scheduling hook ──────────────────────────────────────────────────────

def maybe_run_title_fight(
    weight_class: str,
    tier_key:     str,
    pools:        dict[str, dict[str, list[Fighter]]],
    org:          str = "league",
    fight_num:    int = 0,
    all_fighters: list[Fighter] | None = None,
) -> bool:
    """
    Register one regular fight's worth of activity for this (weight_class, tier_key).
    If the pool's counter reaches TITLE_FIGHT_INTERVAL, run a title fight and reset.

    Call once per regular fight using fighter A's weight_class and tier (captured
    before tier transitions, so the counter reflects where the fight actually
    took place).

    Returns True if a title fight was run during this call, False otherwise.
    Amateur (tier0) is silently skipped — no title format exists there.
    """
    ruleset = TIER_RULESET.get(tier_key)
    if ruleset is None or ruleset.title_rounds is None:
        return False   # tier0 or unknown tier — no titles

    key = (weight_class, tier_key)
    _fight_counters[key] = _fight_counters.get(key, 0) + 1
    if _fight_counters[key] < TITLE_FIGHT_INTERVAL:
        return False

    # Title fight is due.
    _fight_counters[key] = 0
    pool = pools[weight_class][tier_key]

    if len(pool) < _MIN_POOL_SIZE:
        return False

    champion_id = get_champion_id(weight_class, tier_key)
    was_vacant  = False

    if champion_id is None:
        # Belt never awarded or explicitly vacant.
        pair = _pick_vacant_pair(pool)
        if pair is None:
            return False
        fa, fb     = pair
        was_vacant = True
    else:
        champ = _find_champion(pool, champion_id)
        if champ is None:
            # Champion was demoted out of this pool — treat as vacant.
            pair = _pick_vacant_pair(pool)
            if pair is None:
                return False
            fa, fb     = pair
            was_vacant = True
        else:
            challenger = _pick_challenger(pool, champion_id)
            if challenger is None:
                return False
            fa, fb = champ, challenger

    winner, loser = simulate_fight(fa, fb, org=org, is_title=True)

    # award_title BEFORE tier transitions so the registry key matches the tier
    # the fight was contested at, not the tier the winner may be promoted into.
    award_title(winner)

    for fighter in (winner, loser):
        apply_tier_transitions(fighter, pools)
        maybe_advance_age(fighter)
        maybe_update_labels(fighter)
        removed = maybe_evaluate_retirement(fighter, pools, fight_num)
        if not removed:
            removed = maybe_evaluate_cut(fighter, pools, fight_num)
        if removed and all_fighters is not None:
            all_fighters[:] = [f for f in all_fighters if f is not fighter]

    _title_history.append(TitleFightRecord(
        fight_num        = fight_num,
        weight_class     = weight_class,
        tier_key         = tier_key,
        winner_name      = winner.name,
        loser_name       = loser.name,
        method           = winner.fight_history[-1].method,
        rounds_completed = winner.fight_history[-1].rounds_completed,
        was_vacant       = was_vacant,
    ))
    return True
