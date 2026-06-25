from __future__ import annotations

import random

from fighter import Fighter
from tiers import TIER_LEVELS

# ─── Tuning constants ─────────────────────────────────────────────────────────
# Adjust once you see promotion/demotion rates in sim output.

PROMOTE_WINS_IN_LAST:  int   = 4     # wins needed in the last PROMOTE_WINDOW tier-fights
PROMOTE_WINDOW:        int   = 5     # rolling window size for promotion check
DEMOTE_LOSSES_IN_LAST: int   = 4     # losses needed in the last DEMOTE_WINDOW tier-fights
DEMOTE_WINDOW:         int   = 5     # rolling window size for demotion check
CROSS_TIER_RATE:       float = 0.12  # fraction of fights matched against an adjacent tier

# HOOK: Replace pick_opponent with hype-driven matchmaking when that system is built.
# Protective matchmaking (prospect protection, gatekeeper roles, ranking position)
# also plugs in here. For now: simple tier-pool sampling with a small cross-tier rate.


def pick_opponent(fighter: Fighter, pools: dict[str, list[Fighter]]) -> Fighter:
    """
    Selects an opponent with tier-constrained matchmaking.
    ~88% of fights stay within the same tier; ~12% cross one tier up or down.
    """
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

    candidates = [f for f in pools[opp_tier] if f is not fighter]
    if not candidates:
        # Fallback: broaden search if the preferred pool has no valid opponent.
        for tier in TIER_LEVELS:
            candidates = [f for f in pools[tier] if f is not fighter]
            if candidates:
                break

    return random.choice(candidates)


def _recent_tier_fights(fighter: Fighter, window: int) -> list:
    """Last `window` fight-history entries tagged with the fighter's current tier."""
    tier_fights = [r for r in fighter.fight_history if r.tier == fighter.tier]
    return tier_fights[-window:]


def check_promotion(fighter: Fighter) -> bool:
    recent = _recent_tier_fights(fighter, PROMOTE_WINDOW)
    if len(recent) < PROMOTE_WINDOW:
        return False
    return sum(1 for r in recent if r.outcome == "win") >= PROMOTE_WINS_IN_LAST


def check_demotion(fighter: Fighter) -> bool:
    recent = _recent_tier_fights(fighter, DEMOTE_WINDOW)
    if len(recent) < DEMOTE_WINDOW:
        return False
    return sum(1 for r in recent if r.outcome == "loss") >= DEMOTE_LOSSES_IN_LAST


def apply_tier_transitions(
    fighter: Fighter,
    pools: dict[str, list[Fighter]],
) -> str | None:
    """
    Checks whether fighter should move up or down one tier.
    Mutates fighter.tier and pools in place. Returns new tier key on transition,
    else None.

    Only the opponent pool changes — fighter's sub-attributes and overall are
    never modified. True skill is fixed at generation time.
    """
    idx = TIER_LEVELS.index(fighter.tier)

    if idx < len(TIER_LEVELS) - 1 and check_promotion(fighter):
        pools[fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx + 1]
        pools[fighter.tier].append(fighter)
        return fighter.tier

    if idx > 0 and check_demotion(fighter):
        pools[fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx - 1]
        pools[fighter.tier].append(fighter)
        return fighter.tier

    return None
