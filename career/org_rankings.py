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
from orgs.org_registry import ORG_NAMES

# ── Module-level per-(weight_class, org) ranking cache ──────────────────────

_org_rankings_by_wc_org: dict[tuple[str, str], list[RankingEntry]] = {}
_org_ranked_ids: dict[str, set[str]] = {org: set() for org in ORG_NAMES}


def reset_org_rankings() -> None:
    """Clear all cached per-org rankings and ranked-id sets. Call at sim start."""
    _org_rankings_by_wc_org.clear()
    for org in ORG_NAMES:
        _org_ranked_ids[org] = set()


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
            _org_rankings_by_wc_org[(wc, org)] = entries
            for e in entries:
                new_ranked_ids[org].add(e.fighter.fighter_id)

    for org in ORG_NAMES:
        _org_ranked_ids[org] = new_ranked_ids[org]


def get_org_rankings(weight_class: str, org: str) -> list[RankingEntry]:
    """Current cached top-RANKINGS_SIZE list for one (weight_class, org) pair."""
    return _org_rankings_by_wc_org.get((weight_class, org), [])


def get_org_ranked_ids(org: str) -> set[str]:
    """Fighter_ids currently ranked within `org` (any weight class)."""
    return _org_ranked_ids.get(org, set())


def is_ranked_in_org(fighter: Fighter) -> bool:
    """True if fighter is in their OWN org's current top-RANKINGS_SIZE list.
    Org-scoped sibling of rankings.is_ranked() (which is global-tier4-scoped)."""
    if not fighter.org:
        return False
    return fighter.fighter_id in _org_ranked_ids.get(fighter.org, set())
