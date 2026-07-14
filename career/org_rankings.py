"""
org_rankings.py -- Per-org Elite (tier4) rankings (Org Identity, Session A, Part 2).

Splits the existing combined tier4 rankings into three separate ranked lists,
one per top-tier org (Apex FC / The League / Eastern Grand Prix), per weight
class. A fighter appears only in their current org's list.

## Reuse, not reimplementation

rankings.py's scoring formula (_score_fighter / compute_division_rankings) is
tier4-hardcoded but does NOT hardcode a single combined pool -- it just takes
whatever list of Fighter objects it's given. So unlike career/nonelite_rankings.py
(which needed a tier-generic SIBLING because it operates on tiers 2/3, not 4),
this module can call rankings.compute_division_rankings() DIRECTLY on an
org-filtered subset of the tier4 pool -- no new scoring code at all. This is
the most literal form of "reuse the existing Elite ranking formula" the spec
asks for.

rankings.py's own combined-tier4 cache (update_rankings/_rankings_by_wc/
_ranked_ids) is left completely untouched and keeps running -- it still backs
career/hype.py's is_ranked() (opponent-quality bonus) and the base matchmaking
gate's Condition 2, both of which are fine treating "ranked at any top-tier
org" as a single global signal. Only the DISPLAY and per-org matchmaking sub-
pool logic (matchmaking.py) move to this module's per-org lists.
"""
from __future__ import annotations

from career.fighter import Fighter
from career.rankings import RankingEntry, compute_division_rankings, RANKINGS_SIZE
from career.labels import get_champion_id
from orgs.org_registry import ORG_NAMES

# ── Module-level per-(weight_class, org) ranking cache ──────────────────────

_org_rankings_by_wc_org: dict[tuple[str, str], list[RankingEntry]] = {}
_org_ranked_ids: dict[str, set[str]] = {org: set() for org in ORG_NAMES}


def reset_org_rankings() -> None:
    """Clear all cached per-org rankings and ranked-id sets. Call at sim start."""
    _org_rankings_by_wc_org.clear()
    for org in ORG_NAMES:
        _org_ranked_ids[org] = set()


def _pin_champion(
    weight_class: str, org: str, org_fighters: list[Fighter], entries: list[RankingEntry],
) -> list[RankingEntry]:
    """
    The reigning champion is #1 by definition, regardless of where the score
    formula would otherwise place them -- a title holder is never "unranked"
    or ranked below a contender. Without this, a champion who e.g. just won
    a vacant belt, or moved weight class, or is on a rare cold stretch could
    show up ranked #2+ or missing from the list entirely, which reads as a
    modeling bug even though the underlying score math is working as designed
    (it was never told the champion is special).

    Moves the champion's existing entry to rank 1 if they're already present;
    synthesizes a zero-valued placeholder entry if they have no qualifying
    score (e.g. no wins yet in their CURRENT weight class -- compute_division_
    rankings only counts current-division tier4 fights, so a fresh weight-
    class mover's belt can outrun their scored history). Everyone else shifts
    down a slot; the list is re-truncated to RANKINGS_SIZE.
    """
    champ_id = get_champion_id(weight_class, "tier4", org)
    if champ_id is None:
        return entries

    champ_fighter = next((f for f in org_fighters if f.fighter_id == champ_id), None)
    if champ_fighter is None:
        return entries   # champion no longer in this org's pool (stale registry) -- leave as-is

    champ_entry = next((e for e in entries if e.fighter.fighter_id == champ_id), None)
    if champ_entry is None:
        champ_entry = RankingEntry(
            rank=1, fighter=champ_fighter, score=0.0,
            win_rate_component=0.0, quality_component=0.0, hype_component=0.0,
            n_elite_fights=0, n_ranked_wins=0,
        )

    remaining = [e for e in entries if e.fighter.fighter_id != champ_id]
    pinned = [champ_entry] + remaining[: RANKINGS_SIZE - 1]
    for i, e in enumerate(pinned):
        e.rank = i + 1
    return pinned


def update_org_rankings(pools: dict[str, dict[str, list[Fighter]]]) -> None:
    """Recompute top-RANKINGS_SIZE rankings for each (weight_class, org) pair,
    tier4 only. Same cadence as rankings.update_rankings() -- call alongside it
    (sim.py's RANKINGS_UPDATE_INTERVAL block), not on a separate schedule.

    Uses each org's OWN previous ranked-id snapshot for quality scoring --
    identical bootstrapping behavior to rankings.update_rankings(), just one
    independent snapshot per org instead of one global one.
    """
    new_ranked_ids: dict[str, set[str]] = {org: set() for org in ORG_NAMES}

    for wc, tier_pools in pools.items():
        elite = tier_pools.get("tier4", [])
        for org in ORG_NAMES:
            org_fighters = [f for f in elite if f.org == org]
            entries = compute_division_rankings(org_fighters, _org_ranked_ids[org])
            entries = _pin_champion(wc, org, org_fighters, entries)
            _org_rankings_by_wc_org[(wc, org)] = entries
            for e in entries:
                new_ranked_ids[org].add(e.fighter.fighter_id)

    for org in ORG_NAMES:
        _org_ranked_ids[org] = new_ranked_ids[org]


def get_org_rankings(weight_class: str, org: str) -> list[RankingEntry]:
    """Current cached top-RANKINGS_SIZE list for one (weight_class, org) pair."""
    return _org_rankings_by_wc_org.get((weight_class, org), [])


def drop_from_org_rankings_cache(fighter_id: str, weight_class: str, org: str) -> None:
    """
    Evict a fighter from one (weight_class, org) pair's CACHED ranking list --
    mirrors rankings.drop_from_rankings_cache, same rationale: RankingEntry
    holds a live Fighter reference, and this cache only refreshes every
    RANKINGS_UPDATE_INTERVAL fights, so a fighter who is demoted out of tier4
    or fully removed (cut/retired) between recomputes would otherwise keep
    appearing in their old org's roster until the next scheduled update.
    No-op if `org` is empty/unrecognized or the fighter wasn't cached there.
    """
    key = (weight_class, org)
    if key in _org_rankings_by_wc_org:
        remaining = [e for e in _org_rankings_by_wc_org[key] if e.fighter.fighter_id != fighter_id]
        for i, e in enumerate(remaining):
            e.rank = i + 1
        _org_rankings_by_wc_org[key] = remaining
    if org in _org_ranked_ids:
        _org_ranked_ids[org].discard(fighter_id)


def get_org_ranked_ids(org: str) -> set[str]:
    """Fighter_ids currently ranked within `org` (any weight class)."""
    return _org_ranked_ids.get(org, set())


def is_ranked_in_org(fighter: Fighter) -> bool:
    """True if fighter is in their OWN org's current top-RANKINGS_SIZE list.
    Org-scoped sibling of rankings.is_ranked() (which is global-tier4-scoped)."""
    if not fighter.org:
        return False
    return fighter.fighter_id in _org_ranked_ids.get(fighter.org, set())
