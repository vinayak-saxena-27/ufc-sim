from __future__ import annotations

import random
from dataclasses import dataclass

from career.fighter import Fighter
from career.tiers import TIER_LEVELS
from career.academies import ACADEMY_PIPELINE
from career.rankings import is_eligible_vs_ranked, get_ranked_ids, get_rankings, RANKINGS_SIZE

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
#
# WIDENED (calibration-fix session): since org="exhibition" was removed,
# scheduled Elite fights (ELITE_FIGHT_INTERVAL) count fully toward this
# window -- and they skew heavily toward ranked/top fighters specifically
# (~2x appearance rate vs unranked, per verify_elite_matchmaking CHECK 5),
# so a 3-fight window now covers roughly half the real-time span it did
# when calibrated. Retuned against verify_title_selection.py /
# verify_elite_matchmaking.py (seed=42, both scripts unmodified): 6 got
# CHECK 3/CHECK 1 to 78.8% within-pool (still short); 7 and 9 both passed
# everything, 8 unexpectedly failed one check (non-monotonic -- small
# integer window changes interact with this seed's specific fight sequence
# in ways that aren't smooth). Settled on 9 over 7 for margin: 86.1%
# within-pool (target ~88%) vs 7's 80.8%, which sat right at the 80% floor.
ELITE_DEMOTE_LOSSES_IN_LAST: int = 2   # lose 2 of last ELITE_DEMOTE_WINDOW -> demoted
ELITE_DEMOTE_WINDOW:         int = 9   # widened from 3 -- see comment above

# HOOK: Replace pick_opponent with hype-driven matchmaking when that system is built.
# Protective matchmaking (prospect protection, gatekeeper roles, ranking position)
# also plugs in here. For now: simple tier-pool sampling with a small cross-tier rate.

# ── Elite-tier sub-pool constants (Layers 2 + 3) ─────────────────────────────

ELITE_CROSS_POOL_RATE: float = 0.12
"""Probability of a cross-pool draw within Elite tier: ranked fighter vs unranked pool,
or unranked fighter vs ranked pool.  Mirrors CROSS_TIER_RATE (tier-to-tier boundary)
one layer finer — same ratio, same intent.  First-pass: 0.12 → 88% within-pool.
Tune independently of CROSS_TIER_RATE."""

ELITE_PROXIMITY_BASE: float = 1.0
"""Rank-proximity weighting base for ranked-vs-ranked Elite fights.
weight(dist) = 1.0 / (ELITE_PROXIMITY_BASE + |rank_a - rank_b|)
At base=1.0: adjacent ranks weight 0.50, dist=5 weight 0.17, dist=12 weight 0.08.
Increase to flatten (more uniform); decrease to sharpen (tighter clustering). First-pass."""

# Stratified A-selection (option a density fix) was tested at rates 0.20 and 0.30
# and rejected: boosting Elite fight frequency and depleting the pool are the same
# multiplier -- no rate separates them. Option (b) scheduled fights are the active fix.

ELITE_FIGHT_INTERVAL: int = 5
"""One scheduled Elite-vs-Elite fight is injected per this many main-loop fights.
Additive: the global fight loop is unchanged, non-Elite fighters keep their natural
fight cadence, and the Elite pool replenishes at the same rate as without this feature.
Only Elite fighters gain additional appearances from the injected fights.

At interval=5: 3000 main fights produce 600 injected Elite fights (20% extra).
Each injected fight picks A via pick_scheduled_elite_a() and routes B through
pick_opponent() / _pick_elite_opponent() so Layers 2+3 remain active.
Lower values = denser schedule; set to 0 to disable."""

# Gate statistics — how often the Elite ranked-opponent gate fires per sim run.
_gate_enforced: int = 0   # gate applied, candidates filtered to unranked only
_gate_fallback: int = 0   # gate would apply but no unranked candidates exist


def reset_gate_stats() -> None:
    global _gate_enforced, _gate_fallback
    _gate_enforced = _gate_fallback = 0


def get_gate_stats() -> tuple[int, int]:
    """Returns (enforced, fallback) gate trigger counts since last reset."""
    return _gate_enforced, _gate_fallback


# ── Elite pairing log ─────────────────────────────────────────────────────────

@dataclass
class ElitePairingRecord:
    """One Elite-tier fight pairing, logged for post-sim inspection of sub-pool mix."""
    weight_class: str
    fighter_name: str
    fighter_rank: int | None   # None if unranked at time of fight
    opp_name:     str
    opp_rank:     int | None   # None if unranked at time of fight
    pool_type:    str          # "RR" | "RU" | "UR" | "UU"


_elite_pairings: list[ElitePairingRecord] = []


def reset_elite_pairings() -> None:
    """Clear the Elite pairing log.  Call at sim start."""
    _elite_pairings.clear()


def get_elite_pairings() -> list[ElitePairingRecord]:
    """Return all logged Elite pairings since last reset."""
    return list(_elite_pairings)


def _proximity_pick(
    fighter: Fighter,
    ranked_candidates: list[Fighter],
    rank_map: dict[str, int],
) -> Fighter:
    """Weighted-random draw from ranked_candidates; closer ranks are more likely.

    weight(dist) = 1.0 / (ELITE_PROXIMITY_BASE + |rank_a - rank_b|)

    Falls back to uniform if fighter has no rank in rank_map (rankings not yet
    computed — correct during the bootstrap period before the first update).
    """
    fighter_rank = rank_map.get(fighter.fighter_id)
    if fighter_rank is None:
        return random.choice(ranked_candidates)

    weights: list[float] = []
    for f in ranked_candidates:
        opp_rank = rank_map.get(f.fighter_id)
        dist = abs(opp_rank - fighter_rank) if opp_rank is not None else RANKINGS_SIZE
        weights.append(1.0 / (ELITE_PROXIMITY_BASE + dist))

    return random.choices(ranked_candidates, weights=weights, k=1)[0]


def _pick_elite_opponent(
    fighter: Fighter,
    candidates: list[Fighter],
) -> Fighter:
    """Layer 2 + 3 Elite matchmaking: ranked/unranked sub-pool split with
    rank-proximity weighting within the ranked sub-pool.

    Layer 2 — sub-pool split (ELITE_CROSS_POOL_RATE = 0.12):
      Ranked fighter:   ~88% draws from ranked sub-pool, ~12% from unranked.
      Unranked fighter: ~88% draws from unranked sub-pool, ~12% from ranked.
      Graceful fallback: if the target pool is empty, fight from the available pool.

    Layer 3 — proximity weighting (ranked-vs-ranked only):
      When a ranked fighter draws from the ranked sub-pool, candidates are weighted
      inversely by rank distance so fights cluster near the fighter's ranking position.
      Unranked draws (either direction) use uniform selection.

    Gate note: `candidates` has already been filtered by pick_opponent's ranked-opponent
    gate for ineligible fighters, so those arrive with only unranked candidates.
    This function never bypasses that gate — it only further structures whatever
    candidate set the gate left behind.
    """
    wc = fighter.weight_class
    ranked_ids          = get_ranked_ids()
    ranked_candidates   = [f for f in candidates if f.fighter_id in ranked_ids]
    unranked_candidates = [f for f in candidates if f.fighter_id not in ranked_ids]
    fighter_is_ranked   = fighter.fighter_id in ranked_ids

    rankings_list = get_rankings(wc)
    rank_map      = {e.fighter.fighter_id: e.rank for e in rankings_list}
    fighter_rank  = rank_map.get(fighter.fighter_id)

    opp: Fighter
    pool_type: str

    if fighter_is_ranked:
        if ranked_candidates and random.random() < 1.0 - ELITE_CROSS_POOL_RATE:
            opp       = _proximity_pick(fighter, ranked_candidates, rank_map)
            pool_type = "RR"
        elif unranked_candidates:
            opp       = random.choice(unranked_candidates)
            pool_type = "RU"
        elif ranked_candidates:                                # cross-pool impossible
            opp       = _proximity_pick(fighter, ranked_candidates, rank_map)
            pool_type = "RR"
        else:
            opp       = random.choice(candidates)
            pool_type = "RR"
    else:
        if unranked_candidates and random.random() < 1.0 - ELITE_CROSS_POOL_RATE:
            opp       = random.choice(unranked_candidates)
            pool_type = "UU"
        elif ranked_candidates:
            opp       = random.choice(ranked_candidates)
            pool_type = "UR"
        elif unranked_candidates:                              # cross-pool impossible
            opp       = random.choice(unranked_candidates)
            pool_type = "UU"
        else:
            opp       = random.choice(candidates)
            pool_type = "UU"

    _elite_pairings.append(ElitePairingRecord(
        weight_class = wc,
        fighter_name = fighter.name,
        fighter_rank = fighter_rank,
        opp_name     = opp.name,
        opp_rank     = rank_map.get(opp.fighter_id),
        pool_type    = pool_type,
    ))
    return opp


def pick_scheduled_elite_a(
    pools: dict[str, dict[str, list[Fighter]]],
) -> Fighter | None:
    """Pick a RANKED Elite fighter to be A in a scheduled Elite fight (option b).

    Picks from the ranked sub-pool specifically, not all Elite.  This ensures:
      - Every scheduled fight directly contributes a ranked-fighter appearance.
      - A is ranked, so _pick_elite_opponent routes to the RR path (ranked_candidates
        is non-empty by construction since we only schedule when ranked fighters exist).
      - Self-limiting: if all ranked fighters leave a WC's tier4 pool, that WC is
        skipped; when all WCs are empty the scheduled slot is silently skipped.

    Weight class is chosen uniformly from WCs that have >= 1 ranked tier4 fighter
    and >= 1 OTHER tier4 fighter (so opponent selection doesn't immediately fail).

    Falls back to any Elite fighter in the bootstrap period before rankings populate.
    """
    ranked_ids = get_ranked_ids()

    # Normal path: pick A from ranked tier4 fighters.
    # Require >= 2 ranked fighters in the WC's tier4 pool so ranked_candidates is
    # non-empty when _pick_elite_opponent runs — guarantees pool_type "RR" path fires.
    eligible = [
        wc for wc, wc_pools in pools.items()
        if sum(1 for f in wc_pools.get("tier4", [])
               if f.fighter_id in ranked_ids) >= 2
    ]
    if eligible:
        wc = random.choice(eligible)
        ranked_in_wc = [f for f in pools[wc]["tier4"] if f.fighter_id in ranked_ids]
        return random.choice(ranked_in_wc)

    # Bootstrap fallback (before first rankings update): any WC with >= 2 Elite fighters.
    fallback = [
        wc for wc, wc_pools in pools.items()
        if len(wc_pools.get("tier4", [])) >= 2
    ]
    if not fallback:
        return None
    wc = random.choice(fallback)
    return random.choice(pools[wc]["tier4"])


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

    # ── Elite ranked-opponent gate (unchanged) ───────────────────────────────────
    # An ineligible Elite fighter (hasn't passed any of the four conditions in
    # rankings.is_eligible_vs_ranked) is restricted to unranked candidates.
    # Filtering happens here BEFORE _pick_elite_opponent, so the sub-pool logic
    # below never bypasses this gate — it only further structures the filtered set.
    if fighter.tier == "tier4" and opp_tier == "tier4" and not is_eligible_vs_ranked(fighter):
        global _gate_enforced, _gate_fallback
        ranked_ids_gate = get_ranked_ids()
        unranked_candidates_gate = [f for f in candidates if f.fighter_id not in ranked_ids_gate]
        if unranked_candidates_gate:
            candidates = unranked_candidates_gate
            _gate_enforced += 1
        else:
            _gate_fallback += 1
            # Pool too small to enforce gate — allow any candidate (documented fallback)

    # ── Elite sub-pool split + proximity weighting (Layers 2 + 3) ─────────────
    if fighter.tier == "tier4" and opp_tier == "tier4":
        return _pick_elite_opponent(fighter, candidates)

    return random.choice(candidates)


def _recent_tier_fights(fighter: Fighter, window: int) -> list:
    """Last `window` fight-history entries tagged with the fighter's tier.

    Scheduled Elite density fights (matchmaking.ELITE_FIGHT_INTERVAL) count
    here like any other fight — no special treatment. An Elite fighter who
    gets extra scheduled bouts will cycle through this window somewhat
    faster than a fighter in another tier; that's an accepted consequence
    of fighting more often, not something this window filters around.
    """
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
