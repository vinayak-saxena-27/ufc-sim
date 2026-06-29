from __future__ import annotations

import random

from fighter import Fighter
from tiers import TIER_LEVELS
from academies import ACADEMY_PIPELINE
from rankings import is_eligible_vs_ranked, get_ranked_ids

# ─── Tuning constants ─────────────────────────────────────────────────────────
# Adjust once you see promotion/demotion rates in sim output.

PROMOTE_WINS_IN_LAST:  int   = 4     # wins needed in the last PROMOTE_WINDOW tier-fights
PROMOTE_WINDOW:        int   = 5     # rolling window size for promotion check
DEMOTE_LOSSES_IN_LAST: int   = 4     # losses needed in the last DEMOTE_WINDOW tier-fights
DEMOTE_WINDOW:         int   = 5     # rolling window size for demotion check (tiers 0-3)
CROSS_TIER_RATE:       float = 0.12  # fraction of fights matched against an adjacent tier

# Effect 2 (pipeline bias) -- direct promotion nudge.
# Fighters one win short of the promotion threshold get a small probabilistic
# second chance, scaled by their academy's pipeline_strength.
#
# Relative magnitude vs Effect 1 (hype modifier):
#   Effect 1 adds pipeline_strength directly to hype (e.g. +9 pts at max).
#   Effect 2 fires at most ~18% of the time (ps=+9, scale=0.02) and only when
#   the fighter is exactly one win short -- expected extra promotion rate ~4-5%.
#   Effect 1 is the PRIMARY mechanism; Effect 2 is a secondary "insider access" nudge.
PROMO_DIRECT_NUDGE_SCALE: float = 0.02

# Elite (tier4) demotion fires on a tighter window so fighters who bomb out don't
# linger at the top. Generated Elite fighters start with no fight history, so the
# standard 5-fight window would require 5 tier4 fights before any demotion fires —
# with a small Elite pool that takes many sim fights. A 3-fight window means a
# fighter who goes 0-3 or 1-2 at Elite is out after their third bout there.
ELITE_DEMOTE_LOSSES_IN_LAST: int = 2   # lose 2 of last ELITE_DEMOTE_WINDOW -> demoted
ELITE_DEMOTE_WINDOW:         int = 3   # shorter window specific to tier4

# HOOK: Replace pick_opponent with hype-driven matchmaking when that system is built.
# Protective matchmaking (prospect protection, gatekeeper roles, ranking position)
# also plugs in here. For now: simple tier-pool sampling with a small cross-tier rate.

# Gate statistics — how often the Elite ranked-opponent gate fires per sim run.
_gate_enforced: int = 0   # gate applied, candidates filtered to unranked only
_gate_fallback: int = 0   # gate would apply but no unranked candidates exist


def reset_gate_stats() -> None:
    global _gate_enforced, _gate_fallback
    _gate_enforced = _gate_fallback = 0


def get_gate_stats() -> tuple[int, int]:
    """Returns (enforced, fallback) gate trigger counts since last reset."""
    return _gate_enforced, _gate_fallback


def pick_opponent(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> Fighter:
    """
    Selects an opponent with tier-constrained, division-partitioned matchmaking.
    ~88% of fights stay within the same tier; ~12% cross one tier up or down.
    Opponents are ALWAYS drawn from the same weight class — no cross-division fights.
    """
    wc = fighter.weight_class
    own_idx = TIER_LEVELS.index(fighter.tier)

    if random.random() < CROSS_TIER_RATE:
        adjacent: list[str] = []
        if own_idx > 0:
            adjacent.append(TIER_LEVELS[own_idx - 1])
        if own_idx < len(TIER_LEVELS) - 1:
            adjacent.append(TIER_LEVELS[own_idx + 1])
        opp_tier = random.choice(adjacent) if adjacent else fighter.tier
    else:
        opp_tier = fighter.tier

    candidates = [f for f in pools[wc][opp_tier] if f is not fighter]
    if not candidates:
        # Fallback: broaden within the same division if the preferred tier pool is empty.
        for tier in TIER_LEVELS:
            candidates = [f for f in pools[wc][tier] if f is not fighter]
            if candidates:
                break

    # ── Elite ranked-opponent gate (Part 1) ────────────────────────────────────
    # A fighter matched into tier4 (Elite) who has not earned the right to face
    # ranked opposition is restricted to unranked candidates only.
    # Four conditions grant eligibility — see rankings.is_eligible_vs_ranked().
    # Condition 4 (tier3-dominance fast-track) covers naturally promoted fighters;
    # generated-at-Elite fighters must prove themselves first (conditions 1–3).
    # If filtering leaves no candidates (very small pools early in the sim),
    # fall back to the full candidate list rather than deadlocking.
    if fighter.tier == "tier4" and opp_tier == "tier4" and not is_eligible_vs_ranked(fighter):
        global _gate_enforced, _gate_fallback
        ranked_ids = get_ranked_ids()
        unranked_candidates = [f for f in candidates if f.fighter_id not in ranked_ids]
        if unranked_candidates:
            candidates = unranked_candidates
            _gate_enforced += 1
        else:
            _gate_fallback += 1
            # Pool too small to enforce gate — allow any candidate (documented fallback)

    return random.choice(candidates)


def _recent_tier_fights(fighter: Fighter, window: int) -> list:
    """Last `window` fight-history entries tagged with the fighter's current tier."""
    tier_fights = [r for r in fighter.fight_history if r.tier == fighter.tier]
    return tier_fights[-window:]


def check_promotion(fighter: Fighter) -> bool:
    recent = _recent_tier_fights(fighter, PROMOTE_WINDOW)
    if len(recent) < PROMOTE_WINDOW:
        return False
    wins = sum(1 for r in recent if r.outcome == "win")
    if wins >= PROMOTE_WINS_IN_LAST:
        return True
    # Effect 2: small direct nudge for well-connected academies.
    # Only fires when fighter is exactly one win short and academy has positive pipeline.
    if wins == PROMOTE_WINS_IN_LAST - 1:
        ps = ACADEMY_PIPELINE.get(fighter.academy, 0.0)
        if ps > 0.0 and random.random() < ps * PROMO_DIRECT_NUDGE_SCALE:
            return True
    return False


def check_demotion(fighter: Fighter) -> bool:
    if fighter.tier == "tier4":
        window, threshold = ELITE_DEMOTE_WINDOW, ELITE_DEMOTE_LOSSES_IN_LAST
    else:
        window, threshold = DEMOTE_WINDOW, DEMOTE_LOSSES_IN_LAST
    recent = _recent_tier_fights(fighter, window)
    if len(recent) < window:
        return False
    return sum(1 for r in recent if r.outcome == "loss") >= threshold


def apply_tier_transitions(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> str | None:
    """
    Checks whether fighter should move up or down one tier within their division.
    Mutates fighter.tier and pools in place. Returns new tier key on transition, else None.

    Promotion/demotion is always within the same weight class — fighters never cross
    divisions via tier transitions. Only the pool membership changes; sub-attributes
    and overall are never modified.
    """
    wc  = fighter.weight_class
    idx = TIER_LEVELS.index(fighter.tier)

    if idx < len(TIER_LEVELS) - 1 and check_promotion(fighter):
        pools[wc][fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx + 1]
        pools[wc][fighter.tier].append(fighter)
        return fighter.tier

    if idx > 0 and check_demotion(fighter):
        pools[wc][fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx - 1]
        pools[wc][fighter.tier].append(fighter)
        return fighter.tier

    return None
