"""
org_movement.py -- Cross-org free agency (Org Identity, Session A, Part 6).

Apex FC is the pinnacle org; movement between the three top-tier orgs is
DIRECTIONAL, not symmetric free agency:

  1. Apex FC poaching (inbound):  Apex scouts top performers at Eastern GP /
     The League. Fighter almost always accepts (refuses only if mid-title-run
     and Apex isn't offering an immediate shot).
  2. Leaving Apex FC (outbound):  rare, two specific triggers only (out-of-
     favor + extraordinary offer; aging legend Apex won't re-sign). Fighters
     never leave a dominant Apex run for money alone.
  3. Within-tier movement (League <-> Eastern GP): uncommon, opportunity-
     driven, lower base probability than Apex poaching.

Amateur/Regional/Mid-major/Top-org (tier0-3) fighters are never subject to any
of this -- everything below only evaluates tier4 fighters.

## Evaluation cadence

run_org_movement_sweep() is a WHOLE-POPULATION SWEEP, same shape as
career.weight_transfers.advance_campaigns() / career.replenishment.
run_replenishment() -- called once per main sim-loop iteration. Internally,
each fighter is only actually evaluated when their own fight count lands on
LABEL_UPDATE_INTERVAL (career.labels), matching "evaluate periodically (same
cadence as labels/cuts)" from the spec without requiring a new per-fight hook
at every one of this sim's many fight-resolution call sites.

All numeric constants below are first-pass estimates, picked and documented
per this project's established convention; flag for retuning once real
movement-event populations are observable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from career.fighter import Fighter
from career.labels import LABEL_UPDATE_INTERVAL, get_champion_id
from career.age import _PRIME_END
from career.org_rankings import get_org_rankings, is_ranked_in_org
from orgs.org_registry import APEX_FC_NAME, THE_LEAGUE_NAME, EASTERN_GP_NAME
from sim_calendar import get_sim_day, days_since
from career.age import SIM_DAYS_PER_YEAR
from title import fights_until_next_title_fight

_NON_APEX_ORGS: list[str] = [THE_LEAGUE_NAME, EASTERN_GP_NAME]

# ── Part 6.1: Apex FC poaching (inbound) ─────────────────────────────────────

MIN_TENURE_BEFORE_POACH_YEARS: float = 1.0
"""Scouts want proven performers, not flash-in-the-pan results -- a fighter
must have been at their current (non-Apex) org for at least this many
simulated years before an approach is even considered."""

POACH_HYPE_THRESHOLD: float = 45.0
"""'Exceptional hype' fast-track eligibility (independent of rank/championship).
Lower than rankings.ELITE_GATE_HYPE_THRESHOLD (75.0) -- this is a looser
'buzzy enough that Apex is watching' bar, not the rarer freak-prodigy gate."""

POACH_TOP_N_RANK: int = 3
"""Ranked #1-3 at current org qualifies for a poach approach (in addition to
being champion, or having exceptional hype)."""

POACH_BASE_PROB_CHAMPION: float = 0.12
"""Base per-evaluation-cycle approach probability for a dominant champion."""

POACH_BASE_PROB_RANKED: float = 0.04
"""Base per-evaluation-cycle approach probability for ranked-but-not-champion
(or exceptional-hype-only) fighters -- clearly lower than the champion rate."""

POACH_NEED_TARGET_DEPTH: int = 8
"""Apex's 'comfortable' ranked-pool depth per weight class. Scouting
aggressiveness scales up as Apex's actual ranked count in that weight class
falls below this."""

POACH_NEED_MIN_MULT: float = 0.5
POACH_NEED_MAX_MULT: float = 2.0
"""Need-scaling multiplier bounds -- thin Apex pool = more aggressive
scouting (up to 2x base), deep Apex pool = less urgency (down to 0.5x)."""

MID_TITLE_RUN_WINDOW: int = 3
"""A champion with a title defense due within this many pool-fights is
considered 'mid-title-run' for the refusal check (see
title.fights_until_next_title_fight)."""

REFUSAL_PROB_MID_TITLE_RUN: float = 0.65
"""Probability a mid-title-run champion refuses an Apex approach (Apex isn't
offering an immediate title shot). The ONLY realistic refusal case -- every
other approached fighter accepts."""


def _apex_need_multiplier(weight_class: str) -> float:
    apex_ranked_count = len(get_org_rankings(weight_class, APEX_FC_NAME))
    deficit = POACH_NEED_TARGET_DEPTH - apex_ranked_count
    mult = 1.0 + deficit * 0.15
    return max(POACH_NEED_MIN_MULT, min(POACH_NEED_MAX_MULT, mult))


@dataclass
class OrgMoveRecord:
    fight_num:     int
    sim_day:       int
    fighter_name:  str
    fighter_id:    str
    weight_class:  str
    from_org:      str
    to_org:        str
    reason:        str     # "apex_poach" / "apex_departure_out_of_favor" /
                            # "apex_departure_aging_legend" / "within_tier_opportunity"
    was_refusal:   bool = False
    note:          str  = ""


_move_log: list[OrgMoveRecord] = []


def reset_org_movement_log() -> None:
    _move_log.clear()


def get_org_movement_log() -> list[OrgMoveRecord]:
    return list(_move_log)


def _log(rec: OrgMoveRecord) -> None:
    _move_log.append(rec)
    if rec.was_refusal:
        print(f"[ORG] {rec.fighter_name} declined Apex FC's approach "
              f"(mid-title-run, no immediate shot offered).")
    elif rec.reason == "apex_poach":
        print(f"[ORG] {rec.fighter_name} signed with Apex FC from {rec.from_org}.")
    elif rec.reason.startswith("apex_departure"):
        tag = "extraordinary offer" if "out_of_favor" in rec.reason else "aging veteran extending career"
        print(f"[ORG] {rec.fighter_name} released by Apex FC, signed with {rec.to_org} ({tag}).")
    else:
        print(f"[ORG] {rec.fighter_name} moved {rec.from_org} -> {rec.to_org} ({rec.note}).")


def _maybe_poach_to_apex(fighter: Fighter, fight_num: int) -> None:
    wc  = fighter.weight_class
    org = fighter.org

    is_champion = get_champion_id(wc, "tier4", org) == fighter.fighter_id
    is_top3     = any(e.fighter.fighter_id == fighter.fighter_id and e.rank <= POACH_TOP_N_RANK
                       for e in get_org_rankings(wc, org))
    exceptional_hype = fighter.hype >= POACH_HYPE_THRESHOLD

    if not (is_champion or is_top3 or exceptional_hype):
        return

    if fighter.org_start_day < 0:
        return
    tenure_days = days_since(fighter.org_start_day)
    if tenure_days < MIN_TENURE_BEFORE_POACH_YEARS * SIM_DAYS_PER_YEAR:
        return

    base_p = POACH_BASE_PROB_CHAMPION if is_champion else POACH_BASE_PROB_RANKED
    p = base_p * _apex_need_multiplier(wc)
    if random.random() >= p:
        return

    # Approach made. Refusal only possible for a mid-title-run champion.
    if is_champion and fights_until_next_title_fight(wc, "tier4", org) <= MID_TITLE_RUN_WINDOW:
        if random.random() < REFUSAL_PROB_MID_TITLE_RUN:
            _log(OrgMoveRecord(
                fight_num=fight_num, sim_day=get_sim_day(), fighter_name=fighter.name,
                fighter_id=fighter.fighter_id, weight_class=wc, from_org=org, to_org=org,
                reason="apex_poach", was_refusal=True,
            ))
            return

    was_ranked_at_old_org = is_ranked_in_org(fighter)
    old_org = org
    fighter.org = APEX_FC_NAME
    fighter.org_start_day = get_sim_day()
    if was_ranked_at_old_org:
        # Part 7 matchmaking-gate exception -- arrived pre-ranked from a
        # comparable top-tier promotion.
        fighter.org_arrived_pre_ranked = True

    _log(OrgMoveRecord(
        fight_num=fight_num, sim_day=get_sim_day(), fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, weight_class=wc, from_org=old_org, to_org=APEX_FC_NAME,
        reason="apex_poach",
        note="arrived pre-ranked" if was_ranked_at_old_org else "",
    ))


# ── Part 6.2: Leaving Apex FC (outbound, rare and specific) ─────────────────

OUT_OF_FAVOR_RANK_THRESHOLD: int = 10
"""Below this Apex rank (or unranked) counts toward 'fallen out of favor'."""

OUT_OF_FAVOR_RECENT_LOSSES: int = 3
OUT_OF_FAVOR_RECENT_WINDOW: int = 5
"""'Recent losing record': at least this many losses in the last WINDOW tier4 fights."""

OUT_OF_FAVOR_HYPE_THRESHOLD: float = 10.0
"""'Declining hype' proxy -- current hype below this level."""

EXTRAORDINARY_OFFER_BASE_PROB: float = 0.0015
"""VERY LOW base probability -- fires maybe once per several hundred sim
fights across the whole Apex roster, per spec. Amplified by
_out_of_favor_amplifier below."""

AGING_LEGEND_HYPE_THRESHOLD: float = 15.0
"""Apex scout-interest proxy (there's no separate scouting-interest field --
reusing hype, same pattern as career/nonelite_rankings.py's scout-notice
system reusing hype as an attention signal). Below this, Apex isn't
interested in a new contract for an aging fighter."""

AGING_LEGEND_BASE_PROB: float = 0.05
"""Moderate per-evaluation-cycle probability once ALL of aging-legend's three
conditions are met (past prime, low Apex interest, an outside offer exists --
modeled as always 'available' once the first two conditions hold)."""


def _out_of_favor_amplifier(recent_losses: int) -> float:
    return 1.0 + max(0, recent_losses - OUT_OF_FAVOR_RECENT_LOSSES) * 1.5


def _maybe_leave_apex(fighter: Fighter, fight_num: int) -> None:
    wc = fighter.weight_class
    tier4_fights = [r for r in fighter.fight_history if r.tier == "tier4"]
    recent = tier4_fights[-OUT_OF_FAVOR_RECENT_WINDOW:]
    recent_losses = sum(1 for r in recent if r.outcome == "loss")

    apex_rank = next(
        (e.rank for e in get_org_rankings(wc, APEX_FC_NAME) if e.fighter.fighter_id == fighter.fighter_id),
        None,
    )
    out_of_favor = (
        (apex_rank is None or apex_rank > OUT_OF_FAVOR_RANK_THRESHOLD)
        and len(recent) >= OUT_OF_FAVOR_RECENT_WINDOW
        and recent_losses >= OUT_OF_FAVOR_RECENT_LOSSES
        and fighter.hype < OUT_OF_FAVOR_HYPE_THRESHOLD
    )

    if out_of_favor:
        p = EXTRAORDINARY_OFFER_BASE_PROB * _out_of_favor_amplifier(recent_losses)
        if random.random() < p:
            _execute_departure(fighter, fight_num, reason="apex_departure_out_of_favor")
            return

    past_prime = fighter.age > _PRIME_END
    if past_prime and fighter.hype < AGING_LEGEND_HYPE_THRESHOLD:
        if random.random() < AGING_LEGEND_BASE_PROB:
            _execute_departure(fighter, fight_num, reason="apex_departure_aging_legend")


def _execute_departure(fighter: Fighter, fight_num: int, *, reason: str) -> None:
    dest_org = random.choice(_NON_APEX_ORGS)
    old_org = fighter.org
    fighter.org = dest_org
    fighter.org_start_day = get_sim_day()
    fighter.org_arrived_pre_ranked = False   # leaves unranked -- no gate boost

    _log(OrgMoveRecord(
        fight_num=fight_num, sim_day=get_sim_day(), fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, weight_class=fighter.weight_class,
        from_org=old_org, to_org=dest_org, reason=reason,
    ))


# ── Part 6.3: Within-tier movement (League <-> Eastern GP) ──────────────────

WITHIN_TIER_MOVE_BASE_PROB: float = 0.015
"""Lower than Apex poaching's base rates -- fighters not getting Apex
attention tend to stay put. Fires only on a genuine opportunity signal
(thinner ranked pool at the other org)."""

WITHIN_TIER_RANK_ELIGIBLE: int = 10
"""Must be ranked top-10 (or champion) at current org to be a plausible
target for the other org's interest."""


def _maybe_move_within_tier(fighter: Fighter, fight_num: int) -> None:
    wc = fighter.weight_class
    org = fighter.org
    other_org = EASTERN_GP_NAME if org == THE_LEAGUE_NAME else THE_LEAGUE_NAME

    is_champion = get_champion_id(wc, "tier4", org) == fighter.fighter_id
    rank = next(
        (e.rank for e in get_org_rankings(wc, org) if e.fighter.fighter_id == fighter.fighter_id),
        None,
    )
    if not (is_champion or (rank is not None and rank <= WITHIN_TIER_RANK_ELIGIBLE)):
        return

    own_depth   = len(get_org_rankings(wc, org))
    other_depth = len(get_org_rankings(wc, other_org))
    if other_depth >= own_depth:
        return   # no opportunity signal -- other org isn't thinner

    if random.random() >= WITHIN_TIER_MOVE_BASE_PROB:
        return

    fighter.org = other_org
    fighter.org_start_day = get_sim_day()
    fighter.org_arrived_pre_ranked = False   # enters unranked, per spec

    _log(OrgMoveRecord(
        fight_num=fight_num, sim_day=get_sim_day(), fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, weight_class=wc, from_org=org, to_org=other_org,
        reason="within_tier_opportunity",
        note=f"{other_org} ranked-pool depth {other_depth} < {org}'s {own_depth}",
    ))


# ── Whole-population sweep ───────────────────────────────────────────────────

def run_org_movement_sweep(
    all_fighters: list[Fighter],
    fight_num: int = 0,
) -> None:
    """Evaluate cross-org movement for every tier4 fighter whose fight count
    lands on LABEL_UPDATE_INTERVAL this cycle (same cadence as labels/cuts).
    Call once per main sim-loop iteration."""
    for fighter in all_fighters:
        if fighter.tier != "tier4" or not fighter.org:
            continue
        n = len(fighter.fight_history)
        if n == 0 or n % LABEL_UPDATE_INTERVAL != 0:
            continue

        if fighter.org == APEX_FC_NAME:
            _maybe_leave_apex(fighter, fight_num)
        else:
            _maybe_poach_to_apex(fighter, fight_num)
            if fighter.org != APEX_FC_NAME:   # didn't just get poached this cycle
                _maybe_move_within_tier(fighter, fight_num)
