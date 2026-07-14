from __future__ import annotations

import random
from dataclasses import dataclass

from career.fighter import Fighter
from career.tiers import TIER_LEVELS
from career.academy_reputation import get_effective_pipeline_strength
from career.rankings import is_eligible_vs_ranked, get_ranked_ids, get_rankings, RANKINGS_SIZE, drop_from_rankings_cache
from career.org_rankings import get_org_ranked_ids, get_org_rankings, drop_from_org_rankings_cache
from career.labels import is_champion
from orgs.org_registry import assign_org, assign_midmajor_org, assign_regional_org, capture_midmajor_feed
from sim_calendar import get_sim_day

# ─── Tuning constants ─────────────────────────────────────────────────────────
# Adjust once you see promotion/demotion rates in sim output.

PROMOTE_WINS_IN_LAST:  int   = 4     # wins needed in the last PROMOTE_WINDOW tier-fights
PROMOTE_WINDOW:        int   = 5     # rolling window size for promotion check
DEMOTE_LOSSES_IN_LAST: int   = 4     # losses needed in the last DEMOTE_WINDOW tier-fights
DEMOTE_WINDOW:         int   = 5     # rolling window size for demotion check (tiers 0-3)
CROSS_TIER_RATE:       float = 0.12  # fraction of fights matched against an adjacent tier

# ── Opponent-avoidance (repeat-matchup prevention) ───────────────────────────
# Three stacked layers, real-world-matchmaking-shaped: a hard lifetime cap
# (trilogies happen, tetralogies don't), a cooldown (not immediately), and a
# soft penalty that tapers off (matchmaking doesn't casually reach for a
# repeat, but doesn't forbid one once real time has passed). An explicit
# title-rematch exception bypasses the cooldown/soft-weight (not the hard
# cap) for one specific next booking after a controversial title loss.
#
# All pairing history is derived directly from Fighter.real_fight_history
# (matched by opponent_name -- FightResult has no opponent_id field, and
# academies.py's name registry guarantees names are unique for the life of a
# sim run, so name-matching is safe here, same as several other systems
# already rely on). No separate pairing-history registry is tracked --
# deriving directly from real_fight_history means this is immune to the
# presim-contamination bug class by construction, not by a parallel filter
# that could drift out of sync with it.

AVOID_HARD_CAP: int = 3
"""Max lifetime meetings between any two fighters, ever -- no exceptions,
including the title-rematch exception below (which governs the 2nd/3rd
meeting, not an unlimited chain). Applies uniformly at every tier."""

AVOID_COOLDOWN_DAYS:   int = 180
AVOID_COOLDOWN_FIGHTS: int = 8
"""A rebooking is blocked until BOTH clear: at least this many calendar days
AND at least this many of the fighter's OWN real fights have passed since
their last meeting with this specific opponent. Gating on both prevents a
long-inactive fighter from getting an artificially short cooldown just
because few fights happened, and prevents a very active fighter from getting
an artificially short cooldown just because days passed quickly."""

AVOID_MEMORY_WINDOW_DAYS: int = AVOID_COOLDOWN_DAYS * 2
"""Soft-penalty taper window, counted from the SAME last-meeting day the
cooldown uses. The penalty is steepest right as the cooldown lifts and
relaxes linearly back to no penalty by the end of this window."""

AVOID_SOFT_MIN: float = 0.3
"""Rejection-sampling accept probability for a candidate right as their
cooldown lifts (see _repeat_penalty) -- i.e. matchmaking still reaches for
them ~30% as often as a fresh opponent at that instant, rising to 100% (no
penalty) by AVOID_MEMORY_WINDOW_DAYS."""

AVOID_MAX_REROLLS: int = 3
"""Max rejection-sampling attempts to avoid a soft-penalized pick before just
accepting whichever candidate was last drawn. Keeps the penalty probabilistic
rather than a second hard filter -- a thin pool can still produce a "recent"
opponent, just less often than an unpenalized draw would."""

AVOID_REMATCH_SCORE_MARGIN: float = 2.0
"""'Close/controversial decision' threshold for the title-rematch exception,
on FightResult.score_margin. MMA judging is 10-9-style per round (see
engine/fight_engine.py) -- a 3-round total sits in the high 20s, a 5-round
total in the 40s-50s, so a 1-2 point margin is genuinely a could-have-gone-
either-way scorecard, not a token threshold."""

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
#
# 2026-07-13: the SAME rejection applies to any mechanism that raises a whole
# tier's fight frequency by an uncapped, indefinite multiplier -- promotion/
# demotion/cut/retirement checks are keyed on a fighter's OWN fight-count
# windows, so more fights always means faster churn, no matter the rate. The
# discovery mechanism below (ELITE_DISCOVERY_INTERVAL/pick_discovery_a) is
# NOT that: it's bounded per-fighter (a hard fight-count ceiling, not a rate
# applied forever), which is what makes it structurally safe where option (a)
# wasn't. See pick_discovery_a's docstring for the full reasoning.

ELITE_FIGHT_INTERVAL: int = 5
"""One scheduled Elite-vs-Elite fight is injected per this many main-loop fights.
Additive: the global fight loop is unchanged, non-Elite fighters keep their natural
fight cadence, and the Elite pool replenishes at the same rate as without this feature.
Only Elite fighters gain additional appearances from the injected fights.

At interval=5: 3000 main fights produce 600 injected Elite fights (20% extra).
Each injected fight picks A via pick_scheduled_elite_a() and routes B through
pick_opponent() / _pick_elite_opponent() so Layers 2+3 remain active.
Lower values = denser schedule; set to 0 to disable."""

ELITE_DISCOVERY_MAX_FIGHTS: int = 5
"""Eligibility ceiling for pick_discovery_a's density injection: a tier4 fighter
with FEWER than this many career tier4 fights is eligible to be pulled into an
extra scheduled fight, on top of whatever they get from ordinary matchmaking.
Once a fighter crosses this many tier4 fights they age out of this specific
mechanism for good -- from then on they rely on ordinary matchmaking, or (if
they won enough to rank) ELITE_FIGHT_INTERVAL's existing ranked-density
injection instead. This hard per-fighter cap is what keeps this mechanism from
repeating the rejected option (a) failure mode noted above."""

ELITE_DISCOVERY_INTERVAL: int = 6
"""One scheduled 'discovery' fight (see pick_discovery_a) injected per this many
main-loop fights. Swept 10/6/5 (seed 42, 9000-13000 sim-day runs) against two
metrics: the fraction of a tier4 org+weight-class pool with <=1 career tier4
fights (the clique-diagnosis metric -- 59% before this feature existed), and
the average calendar-day gap between a champion's title defenses (target
~330 days, from TITLE_FIGHT_INTERVAL=9's own tuning).
  10 (half ELITE_FIGHT_INTERVAL=5's frequency, the original first-pass guess):
     53% at <=1 fights -- meaningful but modest improvement.
  6: 44% at <=1 fights, ~403-day average title-defense gap -- best clique
     improvement while keeping title cadence close to target.
  5 (same frequency as ELITE_FIGHT_INTERVAL): 47% at <=1 fights (no better
     than 6, within noise) but compressed the title-defense gap to ~244 days --
     confirms running both mechanisms in lockstep over-compounds
     TITLE_FIGHT_INTERVAL's counter, as flagged when this constant was designed.
Settled on 6. Set to 0 to disable."""

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

    Org Identity session: `candidates` also arrives already org-filtered (see
    pick_opponent's hard-partition block) when fighter.org is set, so "ranked"
    here means ranked WITHIN the fighter's own org (career/org_rankings.py),
    not the old combined tier4 list. Falls back to the combined
    rankings.py lists for the (should-not-happen post-session) case of a
    tier4 fighter with no org assigned.
    """
    wc = fighter.weight_class
    if fighter.org:
        ranked_ids    = get_org_ranked_ids(fighter.org)
        rankings_list = get_org_rankings(wc, fighter.org)
    else:
        ranked_ids    = get_ranked_ids()
        rankings_list = get_rankings(wc)
    ranked_candidates   = [f for f in candidates if f.fighter_id in ranked_ids]
    unranked_candidates = [f for f in candidates if f.fighter_id not in ranked_ids]
    fighter_is_ranked   = fighter.fighter_id in ranked_ids

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

    Weight class is chosen uniformly from (WC, org) pairs that have >= 2 ranked
    tier4 fighters IN THE SAME ORG (Org Identity session: hard-partition means
    "ranked" must mean ranked within one org, not the old combined tier4 list).

    The League is excluded entirely -- its fighters get fights exclusively
    through orgs/league_season.py's own dedicated scheduler, never through this
    density-fight injection.

    Falls back to any same-org Elite pair in the bootstrap period before
    rankings populate.

    Excludes reigning champions -- almost always ranked #1 in their org, so
    without this they'd regularly get pulled into this density-fight
    injection as an ordinary (non-title) fight. A champion's only fights are
    scheduled title defenses (see sim.py's main-loop champion skip and
    title.maybe_run_title_fight).
    """
    from orgs.org_registry import ORG_NAMES, THE_LEAGUE_NAME
    schedulable_orgs = [o for o in ORG_NAMES if o != THE_LEAGUE_NAME]

    # Normal path: pick (wc, org) with >= 2 ranked, non-champion fighters in
    # that org's tier4 pool.
    eligible: list[tuple[str, str]] = []
    for wc, wc_pools in pools.items():
        tier4 = wc_pools.get("tier4", [])
        for org in schedulable_orgs:
            ranked_ids = get_org_ranked_ids(org)
            n_ranked_in_org = sum(
                1 for f in tier4
                if f.org == org and f.fighter_id in ranked_ids and not is_champion(f)
            )
            if n_ranked_in_org >= 2:
                eligible.append((wc, org))

    if eligible:
        wc, org = random.choice(eligible)
        ranked_ids = get_org_ranked_ids(org)
        ranked_in_org = [
            f for f in pools[wc]["tier4"]
            if f.org == org and f.fighter_id in ranked_ids and not is_champion(f)
        ]
        return random.choice(ranked_in_org)

    # Bootstrap fallback (before first rankings update): any (wc, org) with
    # >= 2 non-champion Elite fighters in the same org.
    fallback: list[tuple[str, str]] = []
    for wc, wc_pools in pools.items():
        tier4 = wc_pools.get("tier4", [])
        for org in schedulable_orgs:
            if sum(1 for f in tier4 if f.org == org and not is_champion(f)) >= 2:
                fallback.append((wc, org))
    if not fallback:
        return None
    wc, org = random.choice(fallback)
    same_org = [f for f in pools[wc]["tier4"] if f.org == org and not is_champion(f)]
    return random.choice(same_org)


def pick_discovery_a(
    pools: dict[str, dict[str, list[Fighter]]],
) -> Fighter | None:
    """Pick an UNDER-FOUGHT Elite fighter to be A in a scheduled 'discovery' fight
    -- the sibling injection to pick_scheduled_elite_a (option b), targeting the
    OPPOSITE end of the tier4 population: fighters with fewer than
    ELITE_DISCOVERY_MAX_FIGHTS career tier4 fights, rather than fighters already
    in the ranked top-15.

    Without this, a tier4 fighter's ONLY path to ever being discovered or
    evaluated at all is ordinary uniform matchmaking (sim.py's
    random.choice(all_fighters)) -- diluted across the ENTIRE population
    (every tier, every weight class) -- while pick_scheduled_elite_a's density
    injection exclusively keeps boosting whoever is ALREADY ranked. Measured
    directly: 59% of one org's tier4 pool had 0-1 tier4 fights ever after 25
    simulated years, producing the same handful of "insiders" facing each
    other repeatedly while everyone else never gets a look.

    This is NOT a repeat of the rejected option (a) stratified-A-selection
    experiment (see the comment above ELITE_FIGHT_INTERVAL) -- that applied an
    uncapped rate to the whole Elite tier indefinitely, so already-active
    insiders got proportionally MORE fights too, forever, accelerating the
    whole tier's churn with no natural stop. This mechanism explicitly EXCLUDES
    that population (an established fighter with >= ELITE_DISCOVERY_MAX_FIGHTS
    is ineligible by construction) -- every individual fighter's boost is
    hard-capped at a few extra fights before they age out of eligibility for
    good, either by winning enough to rank (and getting picked up by the
    existing ranked-density mechanism instead) or by accumulating enough of a
    record to be evaluated normally like anyone else.

    Same safety shape as pick_scheduled_elite_a: org-partitioned (The League
    excluded -- its fighters fight exclusively via orgs/league_season.py),
    requires >= 2 eligible fighters in a (weight_class, org) pair, excludes
    reigning champions (who by construction already have a real tier4 track
    record), self-limiting (silently returns None if no eligible pair exists).
    No bootstrap-fallback branch needed -- every tier4 fighter starts at 0
    REAL tier4 fights (see below), so "eligible" is trivially true from
    fight #1.

    Eligibility is counted from real_fight_history, not fight_history --
    without this, career/tiers.py's presim backfill would make this
    mechanism silently useless for exactly the population it most needs to
    help: an older fighter with a 20+ fight presim tail would already be
    "over the limit" on paper despite having zero real sim fights, and would
    never receive a discovery-injected fight at all. Same bug class as
    everywhere else -- see Fighter.real_fight_history.
    """
    from orgs.org_registry import ORG_NAMES, THE_LEAGUE_NAME
    schedulable_orgs = [o for o in ORG_NAMES if o != THE_LEAGUE_NAME]

    def _tier4_fight_count(f: Fighter) -> int:
        return sum(1 for r in f.real_fight_history if r.tier == "tier4")

    eligible: list[tuple[str, str]] = []
    for wc, wc_pools in pools.items():
        tier4 = wc_pools.get("tier4", [])
        for org in schedulable_orgs:
            n_underfought = sum(
                1 for f in tier4
                if f.org == org and not is_champion(f)
                and _tier4_fight_count(f) < ELITE_DISCOVERY_MAX_FIGHTS
            )
            if n_underfought >= 2:
                eligible.append((wc, org))

    if not eligible:
        return None
    wc, org = random.choice(eligible)
    underfought_in_org = [
        f for f in pools[wc]["tier4"]
        if f.org == org and not is_champion(f)
        and _tier4_fight_count(f) < ELITE_DISCOVERY_MAX_FIGHTS
    ]
    return random.choice(underfought_in_org)


# ── Opponent-avoidance helpers ────────────────────────────────────────────────

def _last_meeting_index(fighter: Fighter, opponent: Fighter) -> int | None:
    """Index within fighter.real_fight_history of the most recent real fight
    against opponent (matched by name), or None if they've never met in a
    real (non-presim) fight."""
    last_idx = None
    for i, r in enumerate(fighter.real_fight_history):
        if r.opponent_name == opponent.name:
            last_idx = i
    return last_idx


def _meeting_count(fighter: Fighter, opponent: Fighter) -> int:
    return sum(1 for r in fighter.real_fight_history if r.opponent_name == opponent.name)


def _cooldown_cleared(fighter: Fighter, opponent: Fighter, current_day: int) -> bool:
    """True if both AVOID_COOLDOWN_DAYS and AVOID_COOLDOWN_FIGHTS have
    cleared since fighter's last real meeting with opponent (or they've never
    met). See AVOID_COOLDOWN_DAYS/AVOID_COOLDOWN_FIGHTS for why both gate."""
    real_hist = fighter.real_fight_history
    last_idx = _last_meeting_index(fighter, opponent)
    if last_idx is None:
        return True
    last_meeting = real_hist[last_idx]
    days_since = (current_day - last_meeting.sim_day) if last_meeting.sim_day >= 0 else 10**9
    fights_since = len(real_hist) - 1 - last_idx
    return days_since >= AVOID_COOLDOWN_DAYS and fights_since >= AVOID_COOLDOWN_FIGHTS


def _repeat_penalty(fighter: Fighter, opponent: Fighter, current_day: int) -> float:
    """Soft rejection-sampling accept probability for opponent, given
    fighter's last real meeting with them. 1.0 = no penalty (never met, or
    fully past the memory window). Tapers linearly from AVOID_SOFT_MIN (right
    as cooldown lifts) to 1.0 (end of AVOID_MEMORY_WINDOW_DAYS)."""
    last_idx = _last_meeting_index(fighter, opponent)
    if last_idx is None:
        return 1.0
    last_meeting = fighter.real_fight_history[last_idx]
    if last_meeting.sim_day < 0:
        return 1.0
    days_since = current_day - last_meeting.sim_day
    if days_since >= AVOID_MEMORY_WINDOW_DAYS:
        return 1.0
    if days_since <= AVOID_COOLDOWN_DAYS:
        return AVOID_SOFT_MIN
    frac = (days_since - AVOID_COOLDOWN_DAYS) / (AVOID_MEMORY_WINDOW_DAYS - AVOID_COOLDOWN_DAYS)
    return AVOID_SOFT_MIN + frac * (1.0 - AVOID_SOFT_MIN)


def _avoidance_hard_ok(
    fighter: Fighter, candidate: Fighter, current_day: int, exempt_name: str,
) -> bool:
    """Hard filter: excludes a candidate at the lifetime cap or still inside
    the cooldown window, unless `exempt_name` (a live title-rematch
    exception) names this specific candidate -- which bypasses the cooldown
    but never the hard cap (a 4th meeting never happens regardless)."""
    if _meeting_count(fighter, candidate) >= AVOID_HARD_CAP:
        return False
    if candidate.name == exempt_name:
        return True
    return _cooldown_cleared(fighter, candidate, current_day)


def pick_opponent(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> Fighter | None:
    """
    Selects an opponent with tier-constrained, division-partitioned matchmaking.
    ~88% of fights stay within the same tier; ~12% cross one tier up or down.
    Opponents are ALWAYS drawn from the same weight class — no cross-division fights.

    Returns None if opponent-avoidance (see AVOID_* constants) would leave no
    eligible candidate at all -- callers must treat this the same as the
    existing exhausted-pool IndexError case (skip this fighter's fight for
    this cycle rather than force a repeat matchup).
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

    # ── Reigning-champion exclusion ───────────────────────────────────────────
    # A champion's only fights are scheduled title defenses -- they can't be
    # drawn as an ordinary opponent either. Per-candidate check (not a single
    # shared champion id) since tier1/tier2 pools span multiple orgs, each
    # with their own belt. Falls back to the unfiltered set in thin pools
    # rather than risk an empty candidate list.
    non_champ_candidates = [f for f in candidates if not is_champion(f)]
    if non_champ_candidates:
        candidates = non_champ_candidates

    # ── Org hard-partition (Org Identity session) ────────────────────────────
    # Top-tier orgs are separate promotions, not a shared pool: a tier4 fighter's
    # opponents are drawn ONLY from their own org. Applied before the gate/
    # sub-pool logic below so both operate on an already org-scoped set.
    if fighter.tier == "tier4" and opp_tier == "tier4" and fighter.org:
        org_candidates = [f for f in candidates if f.org == fighter.org]
        if org_candidates:
            candidates = org_candidates
        # else: extremely thin org pool for this weight class (no same-org
        # opponent available) -- fall back to the unfiltered same-tier
        # candidate set rather than raising IndexError. Documented first-pass
        # gap; expected to be rare (tier4's ~15/weight-class population split
        # 3 ways is thin but matchmaking.ELITE_FIGHT_INTERVAL/replenishment/
        # promotions keep replenishing each org's pool).

    # ── Elite ranked-opponent gate (unchanged) ───────────────────────────────────
    # An ineligible Elite fighter (hasn't passed any of the four conditions in
    # rankings.is_eligible_vs_ranked) is restricted to unranked candidates.
    # Filtering happens here BEFORE _pick_elite_opponent, so the sub-pool logic
    # below never bypasses this gate — it only further structures the filtered set.
    # "Ranked" here is ORG-scoped when the candidate pool is org-scoped (the
    # normal case above) -- falls back to the combined tier4 list only for the
    # should-not-happen case of a tier4 fighter with no org.
    if fighter.tier == "tier4" and opp_tier == "tier4" and not is_eligible_vs_ranked(fighter):
        global _gate_enforced, _gate_fallback
        ranked_ids_gate = get_org_ranked_ids(fighter.org) if fighter.org else get_ranked_ids()
        unranked_candidates_gate = [f for f in candidates if f.fighter_id not in ranked_ids_gate]
        if unranked_candidates_gate:
            candidates = unranked_candidates_gate
            _gate_enforced += 1
        else:
            _gate_fallback += 1
            # Pool too small to enforce gate — allow any candidate (documented fallback)

    # ── Opponent-avoidance: hard cap + cooldown (repeat-matchup prevention) ──
    # Excludes any candidate at the lifetime pairing cap or still inside their
    # cooldown window -- see the AVOID_* constants above for the full design.
    # A live title-rematch exception (title.py sets fighter.pending_rematch_
    # opponent_name after a controversial title loss) bypasses the cooldown
    # for that one named candidate, never the hard cap. The exception is
    # consumed here -- once fighter reaches this point with a real (non-empty)
    # avoidance-filtered pool, the exception is spent whether or not the named
    # opponent ended up eligible/drawn this time (a one-attempt permission,
    # not a guarantee). If avoidance would leave NO eligible opponent at all,
    # skip this fighter's fight entirely this cycle rather than force a
    # repeat -- return None (caller must handle this like the existing
    # IndexError-on-exhausted-pool case).
    current_day = get_sim_day()
    exempt_name = fighter.pending_rematch_opponent_name
    avoid_candidates = [
        f for f in candidates if _avoidance_hard_ok(fighter, f, current_day, exempt_name)
    ]
    if not avoid_candidates:
        return None
    candidates = avoid_candidates
    if exempt_name:
        fighter.pending_rematch_opponent_name = ""

    def _select(pool: list[Fighter]) -> Fighter:
        if fighter.tier == "tier4" and opp_tier == "tier4":
            return _pick_elite_opponent(fighter, pool)
        return random.choice(pool)

    # ── Soft weighting (still-tapering recent opponents) via rejection
    # sampling -- wraps whichever selection path ran above without touching
    # either path's own internals (in particular, _pick_elite_opponent's
    # tuned rank-proximity weighting stays untouched). The exempted rematch
    # opponent (if drawn) is never rejected on this pass.
    remaining = list(candidates)
    pick = _select(remaining)
    for _ in range(AVOID_MAX_REROLLS - 1):
        if pick.name == exempt_name:
            break
        penalty = _repeat_penalty(fighter, pick, current_day)
        if penalty >= 1.0 or random.random() < penalty:
            break
        remaining = [f for f in remaining if f is not pick]
        if not remaining:
            break
        pick = _select(remaining)
    return pick


def _recent_tier_fights(fighter: Fighter, window: int) -> list:
    """Last `window` fight-history entries tagged with the fighter's tier.

    Scheduled Elite density fights (matchmaking.ELITE_FIGHT_INTERVAL) count
    here like any other fight — no special treatment. An Elite fighter who
    gets extra scheduled bouts will cycle through this window somewhat
    faster than a fighter in another tier; that's an accepted consequence
    of fighting more often, not something this window filters around.

    Uses fighter.real_fight_history (excludes presim-backfilled/unstamped
    entries, see that property's docstring) -- promotion/demotion should
    reflect recently DEMONSTRATED in-sim performance, not a fighter's
    backfilled career. Confirmed empirically that without this filter, a
    fresh tier4 population produced 38 tier4->tier3 demotions in the first
    300 fights (vs 0 with it) -- fighters were being demoted the moment their
    first REAL fight resolved, purely off presim losses that happened to
    fall in the recent-window tail, never reflecting anything that actually
    happened in the sim.
    """
    tier_fights = [r for r in fighter.real_fight_history if r.tier == fighter.tier]
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
        ps = get_effective_pipeline_strength(fighter.academy)
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

    Org Identity sessions: entering tier1 (regional), tier2 (mid-major), or
    tier4 (top-tier) assigns an org (orgs.org_registry.assign_regional_org /
    assign_midmajor_org / assign_org) and stamps org_start_day. tier1->tier2
    is a direct adjacent promotion (no gap tier) -- assign_midmajor_org reads
    fighter.org (still the regional org name at that moment) directly to
    route via that org's feed preference, no persisted field needed (Session
    B2). Entering tier3 ("Top-org btm-15", which stays a generic org-less
    pool) from tier2 captures a feed preference instead (capture_midmajor_feed)
    so assign_org() can route the fighter toward their mid-major's fed
    top-tier org once they reach tier4. Leaving tier4/tier2/tier1 downward
    clears org entirely (tier0/tier3 have no org concept) so a later
    re-promotion never silently reuses a stale org.
    """
    wc  = fighter.weight_class
    idx = TIER_LEVELS.index(fighter.tier)

    if idx < len(TIER_LEVELS) - 1 and check_promotion(fighter):
        pools[wc][fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx + 1]
        pools[wc][fighter.tier].append(fighter)
        if fighter.tier == "tier1":
            assign_regional_org(fighter)
            fighter.org_start_day = get_sim_day()
        elif fighter.tier == "tier2":
            assign_midmajor_org(fighter)
            fighter.org_start_day = get_sim_day()
        elif fighter.tier == "tier3":
            capture_midmajor_feed(fighter)
        elif fighter.tier == "tier4":
            assign_org(fighter)
            fighter.org_start_day = get_sim_day()
        return fighter.tier

    if idx > 0 and check_demotion(fighter):
        was_tier4 = fighter.tier == "tier4"
        pools[wc][fighter.tier].remove(fighter)
        fighter.tier = TIER_LEVELS[idx - 1]
        pools[wc][fighter.tier].append(fighter)
        if was_tier4:
            # Evict the now-stale Elite / org ranking entries immediately rather
            # than waiting for the next periodic update_rankings() recompute (up
            # to RANKINGS_UPDATE_INTERVAL fights away) -- RankingEntry.fighter is
            # a live reference, so without this a demoted, org-less fighter keeps
            # showing up in /state's elite_rankings (and their old org's roster)
            # at their old rank/score. Must run BEFORE fighter.org is cleared
            # below, since the org-rankings cache is keyed by org name.
            drop_from_rankings_cache(fighter.fighter_id, wc)
            if fighter.org:
                drop_from_org_rankings_cache(fighter.fighter_id, wc, fighter.org)
            fighter.org = ""
            fighter.org_start_day = -1
            fighter.org_arrived_pre_ranked = False
        if fighter.tier == "tier1":
            # Demoted INTO Regional from tier2 -- needs a fresh org
            # assignment, same as promoting up into tier1 from tier0.
            assign_regional_org(fighter)
            fighter.org_start_day = get_sim_day()
        elif fighter.tier == "tier2":
            # Demoted INTO mid-major from tier3 (org-less) -- needs a fresh
            # org assignment, same as promoting up into tier2 from tier1.
            assign_midmajor_org(fighter)
            fighter.org_start_day = get_sim_day()
        elif fighter.tier == "tier0":
            # Demoted OUT of Regional -- tier0 (Amateur) has no org concept.
            fighter.org = ""
            fighter.org_start_day = -1
        return fighter.tier

    return None
