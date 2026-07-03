"""
nonelite_rankings.py — Tier-appropriate rankings below Elite, and the
probability-based "scout noticed them" promotion fast-track.

## Formality ladder

  Amateur (tier0)   -- no rankings, no champion. Amateur has no title format
                       at all (TIER_RULESET["tier0"].title_rounds is None,
                       tiers.py) so there is nothing to surface here; this
                       module adds nothing for tier0.
  Regional (tier1)  -- no formal ranked list, champion only (title.py's
                       existing registry already covers this — it's already
                       displayed in sim.py's Current Champions section).
                       The Regional champion IS the primary scout-notice
                       signal for Mid-major fast-track, despite there being
                       no formal list.
  Mid-major (tier2) -- top-5 "ones to watch" list. Same 3-component formula
                       as Elite (recency-weighted win rate w/ confidence
                       dampening, quality-of-opposition, hype tiebreaker),
                       shorter list, shorter QUALITY_NORM, no matchmaking gate.
  Top-org (tier3)   -- full top-15, SAME formula/weights as Elite. A lighter
                       gate function is defined (is_eligible_for_toporg_ranked)
                       but deliberately NOT wired into matchmaking.pick_opponent
                       this session — that's explicitly out of scope ("no
                       matchmaking sub-pool changes for non-Elite tiers").
  Elite (tier4)     -- unchanged. Owned entirely by rankings.py; not touched.

## Reuse, not reimplementation

Imports rankings.py's tier-agnostic scoring primitives (_confidence,
_recency_weighted_win_rate, the weight/normalization constants, RankingEntry)
rather than reimplementing the formula. rankings.py's own _score_fighter /
compute_division_rankings / update_rankings are hardcoded to tier4 and are
NOT reused directly (would require modifying rankings.py, which this session
must not touch) — _score_fighter_tier below is a tier-generic sibling that
calls the same shared primitives.

## Part 2: Scout-notice promotion

Additive to (never replaces) matchmaking.py's deterministic check_promotion/
check_demotion, which are left completely untouched. Evaluated on the SAME
cadence as this module's own ranking recompute (called from sim.py right
after rankings.update_rankings()) rather than per-fight: the deterministic
path is checked on literally every fight via apply_tier_transitions, but
scout-notice needs a freshly computed rank position as an input, which only
exists right after a periodic rankings recompute.

    p_scout_notice = BASE_SCOUT_PROB
                    * rank_modifier      (champion > rank #1 > ... > rank #N)
                    * pipeline_modifier  (same functional form as
                                          development.py's _pipeline_modifier:
                                          max(0.1, 1.0 + ps*scale))
                    * hype_modifier      (sigmoid, diminishing returns)
                    * recency_modifier   (linear decay with inactivity gap)

All constants below are first-pass estimates, consistent with how every
other constant in this project has been handled — flag for retuning once
long-run promotion dynamics are observed.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from career.fighter import Fighter
from career.tiers import TIER_LEVELS
from career.academies import ACADEMY_PIPELINE
from career.labels import get_champion_id
from career.cuts import is_removed
from career.rankings import (
    RankingEntry,
    _confidence, _recency_weighted_win_rate,
    _W_WIN_RATE, _W_QUALITY, _W_HYPE, _HYPE_NORM,
)
from orgs.org_registry import MIDMAJOR_ORG_NAMES
from sim_calendar import days_since, _last_stamped_day


# ── Part 1: Ranking constants ───────────────────────────────────────────────

MIDMAJOR_LIST_SIZE:   int = 5
TOPORG_LIST_SIZE:     int = 15   # same length as Elite's RANKINGS_SIZE

MIDMAJOR_QUALITY_NORM: float = 2.0
"""Ranked wins needed for quality_component=1.0. Lower than Elite's 5.0 --
a top-5 list is sparse, so 2 wins over other top-5 Mid-major fighters is
already a strong signal (proportionally equivalent to Elite's ~2/15 slots)."""

TOPORG_QUALITY_NORM: float = 5.0
"""Same as Elite's _QUALITY_NORM -- Top-org uses the identical formula/weights."""

TOPORG_GATE_MIN_UNRANKED: int = 2
"""Lighter Top-org matchmaking gate (mirrors rankings.ELITE_GATE_MIN_UNRANKED's
shape, smaller threshold): a Top-org debutant should have this many unranked
Top-org fights before facing ranked Top-org opposition. NOT wired into
matchmaking.pick_opponent this session -- see module docstring. Defined here
so a future matchmaking-integration session can import it directly."""


# ── Module-level ranking cache (mirrors rankings.py's pattern) ─────────────

_midmajor_rankings_by_wc: dict[str, list[RankingEntry]] = {}
_toporg_rankings_by_wc:   dict[str, list[RankingEntry]] = {}
_midmajor_ranked_ids: set[str] = set()
_toporg_ranked_ids:   set[str] = set()

# Per-org mid-major rankings (Org Identity Session B1, Part 4). Kept alongside
# (not replacing) the combined _midmajor_rankings_by_wc above -- same
# "leave the combined computation alive, additive per-org lists on top"
# precedent as Session A's career/org_rankings.py for tier4. The combined
# list is no longer consulted by scout-notice or the sim.py display (both
# moved to per-org), but nothing stops it from still being computed; kept for
# minimal disruption in case anything else reads get_midmajor_rankings().
_midmajor_org_rankings_by_wc_org: dict[tuple[str, str], list[RankingEntry]] = {}
_midmajor_org_ranked_ids: dict[str, set[str]] = {org: set() for org in MIDMAJOR_ORG_NAMES}


def reset_nonelite_rankings() -> None:
    """Clear all cached non-Elite rankings and ranked-id sets. Call at sim start."""
    _midmajor_rankings_by_wc.clear()
    _toporg_rankings_by_wc.clear()
    _midmajor_ranked_ids.clear()
    _toporg_ranked_ids.clear()
    _midmajor_org_rankings_by_wc_org.clear()
    for org in MIDMAJOR_ORG_NAMES:
        _midmajor_org_ranked_ids[org] = set()


def get_midmajor_rankings(weight_class: str) -> list[RankingEntry]:
    """Current cached Mid-major top-5 for a weight class (may be empty)."""
    return _midmajor_rankings_by_wc.get(weight_class, [])


def get_midmajor_org_rankings(weight_class: str, org: str) -> list[RankingEntry]:
    """Current cached Mid-major top-5 for one (weight_class, org) pair (Part 4).
    A fighter appears here only under their CURRENT mid-major org."""
    return _midmajor_org_rankings_by_wc_org.get((weight_class, org), [])


def get_midmajor_org_ranked_ids(org: str) -> set[str]:
    return _midmajor_org_ranked_ids.get(org, set())


def get_toporg_rankings(weight_class: str) -> list[RankingEntry]:
    """Current cached Top-org top-15 for a weight class (may be empty)."""
    return _toporg_rankings_by_wc.get(weight_class, [])


def is_eligible_for_toporg_ranked(fighter: Fighter) -> bool:
    """Lighter Top-org gate (see TOPORG_GATE_MIN_UNRANKED docstring). Pure
    predicate, unused by matchmaking this session -- prepared for later."""
    tier3_fights = [r for r in fighter.fight_history if r.tier == "tier3"]
    return len(tier3_fights) >= TOPORG_GATE_MIN_UNRANKED or fighter.fighter_id in _toporg_ranked_ids


# ── Tier-generic scoring (reuses rankings.py's shared primitives) ──────────

def _score_fighter_tier(
    fighter: Fighter,
    tier_key: str,
    ranked_ids_snapshot: set[str],
    name_to_id: dict[str, str],
    quality_norm: float,
) -> tuple[float, float, float, float, int, int]:
    """Tier-generic sibling of rankings.py's _score_fighter -- identical
    formula, parameterized by tier_key/quality_norm instead of hardcoded
    to tier4. Same weight_class backward-compat handling (empty string =
    pre-existing history, included rather than excluded)."""
    tier_fights = [
        r for r in fighter.fight_history
        if r.tier == tier_key and (r.weight_class == fighter.weight_class or r.weight_class == "")
    ]
    n = len(tier_fights)
    if n == 0:
        return (0.0, 0.0, 0.0, 0.0, 0, 0)

    conf    = _confidence(n)
    rec_wr  = _recency_weighted_win_rate(tier_fights)
    wr_comp = conf * rec_wr

    ranked_wins = sum(
        1 for r in tier_fights
        if r.outcome == "win" and name_to_id.get(r.opponent_name, "") in ranked_ids_snapshot
    )
    qual_comp = min(1.0, ranked_wins / quality_norm)
    hype_comp = min(1.0, max(0.0, fighter.hype / _HYPE_NORM))

    score = _W_WIN_RATE * wr_comp + _W_QUALITY * qual_comp + _W_HYPE * hype_comp
    return (score, wr_comp, qual_comp, hype_comp, n, ranked_wins)


def compute_tier_rankings(
    fighters: list[Fighter],
    tier_key: str,
    ranked_ids_snapshot: set[str],
    list_size: int,
    quality_norm: float,
) -> list[RankingEntry]:
    """Tier-generic sibling of rankings.py's compute_division_rankings."""
    name_to_id = {f.name: f.fighter_id for f in fighters}

    scored: list[tuple[float, float, float, float, int, int, Fighter]] = []
    for f in fighters:
        result = _score_fighter_tier(f, tier_key, ranked_ids_snapshot, name_to_id, quality_norm)
        scored.append((*result, f))

    scored.sort(key=lambda x: x[0], reverse=True)

    ranked: list[RankingEntry] = []
    for score, wr_c, q_c, h_c, n, rw, f in scored:
        if n == 0:
            continue
        if len(ranked) >= list_size:
            break
        ranked.append(RankingEntry(
            rank               = len(ranked) + 1,
            fighter            = f,
            score              = score,
            win_rate_component = wr_c,
            quality_component  = q_c,
            hype_component     = h_c,
            n_elite_fights     = n,
            n_ranked_wins      = rw,
        ))
    return ranked


def update_nonelite_rankings(pools: dict[str, dict[str, list[Fighter]]]) -> None:
    """Recompute Mid-major (top-5, combined AND per-org) and Top-org (top-15)
    rankings for all weight classes. Call on the same cadence as
    rankings.update_rankings() (sim.py's RANKINGS_UPDATE_INTERVAL block) --
    not a separate cadence."""
    global _midmajor_ranked_ids, _toporg_ranked_ids

    new_mm_ids: set[str] = set()
    new_to_ids: set[str] = set()
    new_mm_org_ids: dict[str, set[str]] = {org: set() for org in MIDMAJOR_ORG_NAMES}
    for wc, tier_pools in pools.items():
        midmajor_pool = tier_pools.get("tier2", [])

        mm_entries = compute_tier_rankings(
            midmajor_pool, "tier2", _midmajor_ranked_ids,
            MIDMAJOR_LIST_SIZE, MIDMAJOR_QUALITY_NORM,
        )
        _midmajor_rankings_by_wc[wc] = mm_entries
        new_mm_ids.update(e.fighter.fighter_id for e in mm_entries)

        # Per-org mid-major top-5 (Part 4) -- same tier-generic
        # compute_tier_rankings reused directly on an org-filtered subset,
        # exactly like career/org_rankings.py does for tier4.
        for org in MIDMAJOR_ORG_NAMES:
            org_fighters = [f for f in midmajor_pool if f.org == org]
            org_entries = compute_tier_rankings(
                org_fighters, "tier2", _midmajor_org_ranked_ids[org],
                MIDMAJOR_LIST_SIZE, MIDMAJOR_QUALITY_NORM,
            )
            _midmajor_org_rankings_by_wc_org[(wc, org)] = org_entries
            new_mm_org_ids[org].update(e.fighter.fighter_id for e in org_entries)

        to_entries = compute_tier_rankings(
            tier_pools.get("tier3", []), "tier3", _toporg_ranked_ids,
            TOPORG_LIST_SIZE, TOPORG_QUALITY_NORM,
        )
        _toporg_rankings_by_wc[wc] = to_entries
        new_to_ids.update(e.fighter.fighter_id for e in to_entries)

    _midmajor_ranked_ids = new_mm_ids
    _toporg_ranked_ids   = new_to_ids
    for org in MIDMAJOR_ORG_NAMES:
        _midmajor_org_ranked_ids[org] = new_mm_org_ids[org]


# ── Part 2: Scout-notice promotion ──────────────────────────────────────────

BASE_SCOUT_PROB: float = 0.03
"""Base per-evaluation-cycle probability, before modifiers. Deliberately low
-- most fighters should still earn promotion through the deterministic
threshold path; scout-notice is meant to be a notable event, not routine."""

RANK_MOD_MAX: float = 2.0   # rank #1 (or Regional's sole champion slot)
RANK_MOD_MIN: float = 0.5   # last position in the list (rank #5 or #15)
CHAMPION_BONUS_MULT: float = 1.5
"""Extra multiplier on top of the rank-position modifier when the fighter
also holds their tier's title -- "champion" is a stronger signal than mere
rank position, consistent with Regional champions being the primary
Mid-major fast-track signal despite Regional having no formal list at all.
Applies at every tier EXCEPT tier2 -- see MIDMAJOR_CHAMPION_RETENTION_MULT."""

MIDMAJOR_CHAMPION_RETENTION_MULT: float = 0.4
"""Org Identity Session B1, Part 3 (Apex over-concentration fix): mid-major
(tier2) champions get a REDUCTION instead of CHAMPION_BONUS_MULT's boost --
'king of their own org' has real appeal, unlike Regional/Top-org champions
who are more clearly outgrowing their level. This DELIBERATELY diverges from
the boost every other tier's champions get; confirmed with the user before
building (the two effects directly conflicted for tier2 otherwise)."""

SCOUT_PIPELINE_SCALE: float = 0.03
"""Same functional form as development.py's _pipeline_modifier
(max(0.1, 1.0 + ps*scale)); a dedicated scale constant since a scout's
attention is a different mechanism from development rate."""

HYPE_MOD_MIN:        float = 0.5
HYPE_MOD_MAX:         float = 2.0
HYPE_MOD_MIDPOINT:    float = 15.0   # roughly a solidly-established fighter's hype
HYPE_MOD_STEEPNESS:   float = 10.0
"""Sigmoid on hype: modifier ranges [HYPE_MOD_MIN, HYPE_MOD_MAX], centered at
HYPE_MOD_MIDPOINT, so a viral prospect (hype=60+) gets diminishing extra
benefit beyond a solidly-hyped fighter (hype=25-30) rather than an unbounded
multiplier."""

RECENCY_MOD_FLOOR:      float = 0.1
RECENCY_MOD_DECAY_DAYS: float = 365.0
"""Linear decay from 1.0 (fought today) to RECENCY_MOD_FLOOR over one
simulated year of inactivity -- "a scout isn't calling up someone who
hasn't fought in two years.\""""


def _rank_position_modifier(rank: int | None, list_size: int) -> float:
    if rank is None:
        return RANK_MOD_MAX   # Regional's champion-only case: no numeric rank
    if list_size <= 1:
        return RANK_MOD_MAX
    frac = (rank - 1) / (list_size - 1)
    return RANK_MOD_MAX - (RANK_MOD_MAX - RANK_MOD_MIN) * frac


def _rank_modifier(rank: int | None, list_size: int, is_champion: bool, from_tier: str = "") -> float:
    base = _rank_position_modifier(rank, list_size)
    if not is_champion:
        return base
    if from_tier == "tier2":
        return base * MIDMAJOR_CHAMPION_RETENTION_MULT
    return base * CHAMPION_BONUS_MULT


def _pipeline_modifier(academy_name: str) -> float:
    ps = ACADEMY_PIPELINE.get(academy_name, 0.0)
    return max(0.1, 1.0 + ps * SCOUT_PIPELINE_SCALE)


def _hype_modifier(hype: float) -> float:
    sig = 1.0 / (1.0 + math.exp(-(hype - HYPE_MOD_MIDPOINT) / HYPE_MOD_STEEPNESS))
    return HYPE_MOD_MIN + (HYPE_MOD_MAX - HYPE_MOD_MIN) * sig


def _recency_modifier(fighter: Fighter) -> float:
    last = _last_stamped_day(fighter)
    if last is None:
        return RECENCY_MOD_FLOOR
    gap = days_since(last)
    return max(RECENCY_MOD_FLOOR, 1.0 - gap / RECENCY_MOD_DECAY_DAYS)


def _p_scout_notice(
    fighter: Fighter, rank: int | None, list_size: int, is_champion: bool, from_tier: str = "",
) -> float:
    return (
        BASE_SCOUT_PROB
        * _rank_modifier(rank, list_size, is_champion, from_tier)
        * _pipeline_modifier(fighter.academy)
        * _hype_modifier(fighter.hype)
        * _recency_modifier(fighter)
    )


@dataclass
class ScoutNoticeRecord:
    fight_num:      int
    fighter_name:   str
    fighter_id:     str
    weight_class:   str
    from_tier:      str
    to_tier:        str
    p_scout_notice: float
    was_champion:   bool
    rank:           int | None   # None for the Regional champion-only path


_scout_notice_log: list[ScoutNoticeRecord] = []


def reset_scout_notice_log() -> None:
    """Clear the scout-notice promotion log. Call at sim start."""
    _scout_notice_log.clear()


def get_scout_notice_log() -> list[ScoutNoticeRecord]:
    """Snapshot of all scout-notice fast-track promotions this simulation run."""
    return list(_scout_notice_log)


def _execute_scout_promotion(
    fighter: Fighter, pools: dict[str, dict[str, list[Fighter]]],
    from_tier: str, fight_num: int, rank: int | None, p: float, is_champion: bool,
) -> ScoutNoticeRecord:
    """Same pool-transfer mechanic as matchmaking.apply_tier_transitions'
    promotion branch (remove from current tier pool, bump fighter.tier,
    append to next tier pool) -- reimplemented locally rather than importing
    from matchmaking.py, since that module's promotion code is explicitly
    not to be touched or refactored this session."""
    idx = TIER_LEVELS.index(from_tier)
    to_tier = TIER_LEVELS[idx + 1]
    wc = fighter.weight_class

    pools[wc][from_tier].remove(fighter)
    fighter.tier = to_tier
    pools[wc][to_tier].append(fighter)

    # Org Identity sessions: scout-notice promotion needs the SAME org
    # handling as a deterministic promotion (matchmaking.apply_tier_transitions)
    # -- tier2 entry assigns a mid-major org, tier3 entry captures the feed
    # preference (tier3 stays org-less), tier4 entry assigns a top-tier org
    # (consulting any captured feed preference -- see assign_org()'s
    # midmajor_feed_org handling). Local imports avoid a module-load-order
    # dependency, same pattern used at the other org-assignment call sites.
    from sim_calendar import get_sim_day
    if to_tier == "tier2":
        from orgs.org_registry import assign_midmajor_org
        assign_midmajor_org(fighter)
        fighter.org_start_day = get_sim_day()
    elif to_tier == "tier3":
        from orgs.org_registry import capture_midmajor_feed
        capture_midmajor_feed(fighter)
    elif to_tier == "tier4":
        from orgs.org_registry import assign_org
        assign_org(fighter)
        fighter.org_start_day = get_sim_day()

    rec = ScoutNoticeRecord(
        fight_num=fight_num, fighter_name=fighter.name, fighter_id=fighter.fighter_id,
        weight_class=wc, from_tier=from_tier, to_tier=to_tier,
        p_scout_notice=p, was_champion=is_champion, rank=rank,
    )
    _scout_notice_log.append(rec)

    tag = "CHAMPION" if is_champion else (f"rank=#{rank}" if rank is not None else "")
    print(f"[SCOUT NOTICE] {wc}  {fighter.name}  {from_tier}->{to_tier}"
          f"  (p={p:.3f}  {tag})")
    return rec


def _maybe_promote(
    fighter: Fighter, from_tier: str, pools: dict[str, dict[str, list[Fighter]]],
    fight_num: int, rank: int | None, list_size: int, is_champion: bool,
) -> None:
    if is_removed(fighter.fighter_id):
        return
    p = _p_scout_notice(fighter, rank, list_size, is_champion, from_tier)
    if random.random() < p:
        _execute_scout_promotion(fighter, pools, from_tier, fight_num, rank, p, is_champion)


def maybe_apply_scout_notice(
    pools: dict[str, dict[str, list[Fighter]]], fight_num: int = 0,
) -> None:
    """
    Evaluate the scout-notice fast-track for every eligible fighter across
    all weight classes: Regional champions (-> Mid-major), Mid-major top-5
    PER ORG (-> Top-org, Org Identity Session B1), Top-org top-15 (-> Elite).
    Call right after update_nonelite_rankings() (which itself should follow
    rankings.update_rankings()) so rank data and champion registries are
    fresh. Purely additive to matchmaking.check_promotion/check_demotion,
    which are not called or modified here.
    """
    for wc, tier_pools in pools.items():
        # Regional champion -> Mid-major (no formal list; champion is the signal)
        champ1_id = get_champion_id(wc, "tier1")
        if champ1_id is not None:
            champ1 = next((f for f in tier_pools.get("tier1", []) if f.fighter_id == champ1_id), None)
            if champ1 is not None:
                _maybe_promote(champ1, "tier1", pools, fight_num, rank=None, list_size=1, is_champion=True)

        # Mid-major top-5 PER ORG -> Top-org (Session B1, Part 4: replaces
        # the combined top-5 iteration -- each of the eight mid-major orgs'
        # own top-5 is evaluated separately, using that org's own champion
        # as the champion signal).
        for org in MIDMAJOR_ORG_NAMES:
            champ2_id = get_champion_id(wc, "tier2", org)
            for entry in get_midmajor_org_rankings(wc, org):
                is_champ = entry.fighter.fighter_id == champ2_id
                _maybe_promote(entry.fighter, "tier2", pools, fight_num,
                                rank=entry.rank, list_size=MIDMAJOR_LIST_SIZE, is_champion=is_champ)

        # Top-org top-15 -> Elite
        champ3_id = get_champion_id(wc, "tier3")
        for entry in get_toporg_rankings(wc):
            is_champ = entry.fighter.fighter_id == champ3_id
            _maybe_promote(entry.fighter, "tier3", pools, fight_num,
                            rank=entry.rank, list_size=TOPORG_LIST_SIZE, is_champion=is_champ)
