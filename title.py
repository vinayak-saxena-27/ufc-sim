"""
title.py — Title-fight scheduling for the MMA career sim.

For each (weight_class, tier_key) pair that supports titles (Regional through
Elite per TIER_RULESET — Amateur has no title fights), a title fight is
scheduled once every TITLE_FIGHT_INTERVAL regular fights registered for that
pool.

Challenger selection:
  Vacant belt  → #1 vs #2 in the current rankings for that weight class.
                 Inactivity override: walk down the ranked list if a slot's
                 default pick is relatively inactive.
                 No hype override for vacant fights (no incumbent to upset).

  Occupied belt → #1 ranked eligible contender as the default challenger.
                 Two overrides evaluated in order:
                 1. Inactivity override: walk down the ranked list (up to
                    _INACTIVITY_WALK_LIMIT positions) if the current pick is
                    relatively inactive.  If all candidates are inactive, keep #1.
                 2. Hype override: with probability _HYPE_OVERRIDE_PROB, bump a
                    ranked #2-5 contender who has >= _HYPE_OVERRIDE_MIN_DIFF more
                    hype than the current pick.  Only fires if inactivity did not.

  Thin-rankings fallback: if a weight class has too few ranked entries, falls
  back to the prior placeholder (highest overall among eligible fighters).

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

import random
from dataclasses import dataclass

from career.fighter import Fighter
from engine.fight import simulate_fight
from career.labels import award_title, get_champion_id, maybe_update_labels
from matchmaking import apply_tier_transitions
from career.tiers import TIER_RULESET
from career.cuts import maybe_evaluate_cut
from career.retirement import maybe_evaluate_retirement
from career.rankings import get_rankings, is_eligible_vs_ranked, RankingEntry, RANKINGS_SIZE
from sim_calendar import get_sim_day, inactivity_percentile


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

_INACTIVITY_WALK_LIMIT: int = 7
"""Walk at most this many ranked positions when applying the inactivity override.
If all positions are inactive, fall back to the first (default) pick."""

_HYPE_OVERRIDE_PROB: float = 0.20
"""Probability the hype override is even considered each title defense.
At 20%: roughly 1-2 hype bumps per pool per full sim run at the default interval.
Set to 0.0 to disable."""

_HYPE_OVERRIDE_MIN_DIFF: float = 12.0
"""A #2-5 ranked contender must have at least this many more hype points than
the current default pick to qualify for the hype override.  With Elite hype
ranging ~15-60, a 12-point gap is meaningful without being extreme."""

_HYPE_CANDIDATE_RANKS: int = 5
"""Search ranked positions #2 through this (inclusive) for hype-override candidates."""


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
    override:         str | None = None   # None / "inactivity" / "hype" / "fallback"
    challenger_rank:  int | None = None   # current rank of the challenger (slot-A if vacant)


_fight_counters: dict[tuple[str, str], int]  = {}
_title_history:  list[TitleFightRecord]       = []


def reset_title_scheduling() -> None:
    """Clear fight counters and title history.  Call at the start of each sim."""
    _fight_counters.clear()
    _title_history.clear()


def get_title_history() -> list[TitleFightRecord]:
    """Return a snapshot of all title fights run this simulation."""
    return list(_title_history)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_champion(pool: list[Fighter], champion_id: str) -> Fighter | None:
    return next((f for f in pool if f.fighter_id == champion_id), None)


def _get_fighter_rank(fighter: Fighter, weight_class: str) -> int | None:
    """Return fighter's current rank in their weight class, or None if unranked."""
    for entry in get_rankings(weight_class):
        if entry.fighter.fighter_id == fighter.fighter_id:
            return entry.rank
    return None


def _walk_inactivity(
    candidates: list[RankingEntry],
    pool: list[Fighter],
) -> tuple[RankingEntry, bool]:
    """Walk candidates until a non-relatively-inactive fighter is found.

    Returns (selected_entry, override_fired) where override_fired is True
    if we skipped the first candidate (i.e. not the default #1 pick).
    If all candidates are inactive, returns the first entry with override_fired=False
    — we log this as no override (the default still applies, just unavoidably inactive).
    """
    first = candidates[0]
    for entry in candidates[:_INACTIVITY_WALK_LIMIT]:
        result = inactivity_percentile(entry.fighter, pool)
        if result is None or not result.is_relatively_inactive:
            fired = entry is not first
            return entry, fired
    return first, False  # all inactive: keep default


def _pick_challenger(
    pool: list[Fighter],
    champion_id: str,
    tier_key: str,
    weight_class: str,
) -> tuple[Fighter | None, str | None]:
    """Rankings-driven challenger selection for a title defense.

    Returns (challenger, override) where override is one of:
      None         -- #1 ranked, no override fired
      "inactivity" -- inactivity override walked past #1
      "hype"       -- hype override bumped a #2-5 contender
      "fallback"   -- thin rankings, used highest-overall placeholder
    """
    eligible = [f for f in pool if f.fighter_id != champion_id]
    if not eligible:
        return None, None

    if tier_key == "tier4":
        gate_eligible = [f for f in eligible if is_eligible_vs_ranked(f)]
        if gate_eligible:
            eligible = gate_eligible

    eligible_ids = {f.fighter_id for f in eligible}
    rankings = get_rankings(weight_class)
    ranked_eligible = [e for e in rankings if e.fighter.fighter_id in eligible_ids]

    if not ranked_eligible:
        return max(eligible, key=lambda f: f.overall), "fallback"

    # 1. Inactivity override: walk from #1 down the ranked list
    pick_entry, inactivity_fired = _walk_inactivity(ranked_eligible, pool)
    override = "inactivity" if inactivity_fired else None
    challenger = pick_entry.fighter

    # 2. Hype override: only if inactivity didn't fire; small probability bump
    if override is None and random.random() < _HYPE_OVERRIDE_PROB:
        hype_candidates = [
            e.fighter
            for e in ranked_eligible[1:_HYPE_CANDIDATE_RANKS]
            if e.fighter.hype - challenger.hype >= _HYPE_OVERRIDE_MIN_DIFF
        ]
        if hype_candidates:
            challenger = max(hype_candidates, key=lambda f: f.hype)
            override = "hype"

    return challenger, override


def _pick_vacant_pair(
    pool: list[Fighter],
    weight_class: str,
) -> tuple[tuple[Fighter, Fighter], str | None] | None:
    """Rankings-driven selection for a vacant-belt fight.

    Returns ((fa, fb), override) where override is one of:
      None         -- #1 vs #2, no override
      "inactivity" -- inactivity override on at least one slot
      "fallback"   -- thin rankings, used highest-overall placeholder
    Returns None if pool is too small to run a title fight.
    """
    if len(pool) < _MIN_POOL_SIZE:
        return None

    eligible_ids = {f.fighter_id for f in pool}
    rankings = get_rankings(weight_class)
    ranked_eligible = [e for e in rankings if e.fighter.fighter_id in eligible_ids]

    if len(ranked_eligible) < 2:
        top2 = sorted(pool, key=lambda f: f.overall, reverse=True)[:2]
        return (top2[0], top2[1]), "fallback"

    # Slot A (#1): walk for inactivity
    slot_a_entry, override_a = _walk_inactivity(ranked_eligible, pool)

    # Slot B (#2, excluding slot A): walk for inactivity
    remaining = [e for e in ranked_eligible if e.fighter is not slot_a_entry.fighter]
    if not remaining:
        others = sorted(
            [f for f in pool if f is not slot_a_entry.fighter],
            key=lambda f: f.overall,
            reverse=True,
        )
        if not others:
            return None
        override = "inactivity" if override_a else None
        return (slot_a_entry.fighter, others[0]), override

    slot_b_entry, override_b = _walk_inactivity(remaining, pool)
    override = "inactivity" if (override_a or override_b) else None
    return (slot_a_entry.fighter, slot_b_entry.fighter), override


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
    override    = None
    challenger_rank: int | None = None

    if champion_id is None:
        result = _pick_vacant_pair(pool, weight_class)
        if result is None:
            return False
        (fa, fb), override = result
        was_vacant = True
        challenger_rank = _get_fighter_rank(fa, weight_class)
        rank_b = _get_fighter_rank(fb, weight_class)
        override_tag = f" [{override}]" if override else ""
        print(f"[TITLE] {weight_class} {tier_key} VACANT -- "
              f"{fa.name} (rank={challenger_rank or 'NR'}) "
              f"vs {fb.name} (rank={rank_b or 'NR'}){override_tag}")
    else:
        champ = _find_champion(pool, champion_id)
        if champ is None:
            # Champion demoted — treat as vacant.
            result = _pick_vacant_pair(pool, weight_class)
            if result is None:
                return False
            (fa, fb), override = result
            was_vacant = True
            challenger_rank = _get_fighter_rank(fa, weight_class)
            rank_b = _get_fighter_rank(fb, weight_class)
            override_tag = f" [{override}]" if override else ""
            print(f"[TITLE] {weight_class} {tier_key} VACANT (champ gone) -- "
                  f"{fa.name} (rank={challenger_rank or 'NR'}) "
                  f"vs {fb.name} (rank={rank_b or 'NR'}){override_tag}")
        else:
            challenger, override = _pick_challenger(pool, champion_id, tier_key, weight_class)
            if challenger is None:
                return False
            fa, fb = champ, challenger
            challenger_rank = _get_fighter_rank(challenger, weight_class)
            champ_rank = _get_fighter_rank(champ, weight_class)
            override_tag = f" [{override}]" if override else ""
            print(f"[TITLE] {weight_class} {tier_key} DEFENSE -- "
                  f"[C] {champ.name} (rank={champ_rank or 'NR'}) "
                  f"vs {challenger.name} (rank={challenger_rank or 'NR'}){override_tag}")

    winner, loser = simulate_fight(fa, fb, org=org, is_title=True, sim_day=get_sim_day())

    # award_title BEFORE tier transitions so the registry key matches the tier
    # the fight was contested at, not the tier the winner may be promoted into.
    award_title(winner)

    for fighter in (winner, loser):
        apply_tier_transitions(fighter, pools)
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
        override         = override,
        challenger_rank  = challenger_rank,
    ))
    return True
