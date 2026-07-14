"""
weight_transfers.py — Weight-class MOVE EXECUTION (Weight Class Flex, Session C).

Consumes the candidate flags session B's weight_movement.py sets
(weight_class_move_candidate / _direction / _reason / _target) and executes the
actual state changes: pool transfer, title vacancy, rankings-cache eviction,
cut_severity recalibration, plus the win-and-vacate campaign path (Part 2) and
the opportunity hype boost (Part 3). This module never decides WHETHER to move
a fighter — that's entirely session B's job; this module only acts once a flag
is already set.

## Wiring / cadence

maybe_process_weight_transfers(fighter, pools, fight_num) is the per-fighter
hook — call it immediately AFTER weight_movement.maybe_evaluate_weight_move()
in the same cycle, so a flag set this cycle is consumed this same cycle ("same
periodic cadence... set and then consumed in the same cycle"). Gated on the
identical LABEL_UPDATE_INTERVAL fight-count trigger.

advance_campaigns(all_fighters, pools, fight_num, sim_day) is a whole-population
sweep — call it once per main sim-loop iteration (same shape as
advance_all_ages()/run_replenishment()), since win-and-vacate campaign fights
are self-initiated events, not triggered by a specific fighter's own regular
bout landing on the fight-count cadence.

## Three decisions made explicit with the user before writing this module

1. Title defense count: NOT previously tracked anywhere. Added a small,
   surgical counter (_title_defenses) to labels.py's title registry, maintained
   entirely inside award_title()/vacate_title() — no other title-tracking
   behavior changed. get_title_defenses() is the read side.

2. "Enters UNRANKED" on move: rankings.py scores fighters from fight_history
   filtered by tier=="tier4" only — FightResult has no weight_class field, so
   a mover's OLD-division elite record still counts toward their score
   wherever they currently are. Added ONE narrow, purely-mechanical cache
   eviction function to rankings.py (drop_from_rankings_cache) — no scoring
   logic touched. This is a best-effort reset: the mover is immediately gone
   from get_rankings() right after the move, but may re-enter on the NEXT
   scheduled update_rankings() recompute if their carried-over record still
   qualifies. Documented limitation, not fully closeable without weight-class-
   aware scoring (out of scope).

3. Win-and-vacate campaign fights: Fighter has a single weight_class field, and
   matchmaking.pick_opponent() keys off it for both tier and division — so a
   campaigning champion (weight_class left at HOME the whole time, per spec)
   would never actually get matched into the new division through normal
   random matchmaking. Campaign fights are therefore DIRECTLY simulated here
   (same pattern title.py already uses for its own scheduled title fights),
   bypassing pick_opponent()/matchmaking.py entirely. The champion IS added to
   the new division's pool (so they're a valid opponent for others fighting
   there, per spec), which means sim.py's population-pyramid pool-size counts
   will double-count this fighter for the campaign's short duration — accepted
   as a documented first-pass quirk, not a bug.

## Win-and-vacate campaign resolution (Part 2) — first-pass abstraction

title.py's own title-fight scheduling (per-pool TITLE_FIGHT_INTERVAL counter)
is explicitly off-limits, so "winning the title at the new weight" can't wait
for title.py to naturally schedule and select the campaigner as challenger.
Instead: the campaign is a fight-by-fight gauntlet, directly simulated against
opponents drawn from the new division's SAME-tier pool. ANY loss ends the
campaign immediately (spec: "if they lose during the campaign, return home") —
reaching _CAMPAIGN_WINS_TO_TITLE consecutive wins within _CAMPAIGN_MAX_FIGHTS
total counts as "winning the title," at which point award_title() is called
directly (exactly as the spec instructs), then vacate_title() immediately, per
the win-and-vacate contract.

Home-title priority: title.py's own scheduling remains completely untouched
and still runs autonomously against this fighter's home pool (they're never
removed from it). Each campaign-advance tick just checks whether they're STILL
champion at home; if title.py's autonomous scheduling stripped the belt from
them (they lost a home defense), the campaign is aborted immediately — this is
the "interrupt if needed" mechanic, requiring zero changes to title.py.

All numeric constants below are first-pass estimates, picked and documented
per this project's established convention; flag for retuning once real
flagged-move populations are observable (see __main__ demo).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from career.fighter import Fighter
from career.tiers import WEIGHT_CLASSES
from career.labels import (
    LABEL_UPDATE_INTERVAL, get_champion_id, award_title, vacate_title, get_title_defenses,
    maybe_update_labels,
)
from career.rankings import drop_from_rankings_cache
from engine.fight import simulate_fight
from matchmaking import apply_tier_transitions
from career.development import apply_win_development_boost
from career.retirement import maybe_evaluate_retirement
from career.cuts import maybe_evaluate_cut, is_removed

# ── Part 1: cut_severity recalibration ──────────────────────────────────────

_CUT_ADJUST_ADJACENT: float = 12.0
"""Points subtracted from cut_severity on an adjacent up-move (added on a down-
move). Anchored to weight_cut.py's own scale, where a one-division-worth shift
in 'how far above the limit they naturally sit' is meaningful but not absolute —
e.g. a fighter at cut_severity=+18 (moderate-severe) drops to +6 (comfortable)
moving up one class."""

_CUT_ADJUST_SKIP: float = 20.0
"""Larger adjustment for a two-division class-skip move (Part 4) — a bigger
jump in competition weight means a bigger change in cutting reality."""

# ── Part 2: win-and-vacate ──────────────────────────────────────────────────

_WIN_AND_VACATE_MIN_DEFENSES: int = 8
"""Title defenses (via labels.get_title_defenses) required to be eligible for
the win-and-vacate path instead of a normal full move."""

_CAMPAIGN_MAX_FIGHTS: int = 5
"""Campaign window: up to this many directly-simulated fights in the new
division before the attempt is declared expired (failure)."""

_CAMPAIGN_WINS_TO_TITLE: int = 3
"""Consecutive campaign wins (any loss resets the whole campaign — see module
docstring) needed to count as 'winning the title' at the new weight."""

# ── Part 3: opportunity hype boost ──────────────────────────────────────────

_OPPORTUNITY_HYPE_WINDOW: int = 8
"""Fights since an opportunity-reason move within which winning the new
division's title still counts as a hype-worthy 'division-hopper succeeds'
moment."""

_OPPORTUNITY_HYPE_BASE: float = 8.0
"""Minimum hype boost for winning the title anywhere inside the window."""

_OPPORTUNITY_HYPE_SPEED_BONUS: float = 12.0
"""Additional hype, scaled by how much of the window was UNUSED — winning in
fight 1 of the window nets the full bonus (+20 total); winning at the window's
edge nets close to the base amount only (+8ish)."""


# ── Move log ─────────────────────────────────────────────────────────────────

@dataclass
class MoveRecord:
    fight_num:     int
    sim_day:       int
    fighter_name:  str
    fighter_id:    str
    from_wc:       str
    to_wc:         str
    reason:        str    # driver reason, or "win_and_vacate_start" / "_title_won" /
                           # "_failed", or "opportunity_hype_boost"
    is_skip:       bool = False
    title_vacated: bool = False
    note:          str  = ""


_move_log: list[MoveRecord] = []


def reset_weight_transfer_log() -> None:
    """Clear the move log. Call at the start of each simulation."""
    _move_log.clear()


def get_move_log() -> list[MoveRecord]:
    """Snapshot of all executed moves (and campaign/hype events) this run."""
    return list(_move_log)


def _log(rec: MoveRecord) -> None:
    _move_log.append(rec)
    tag = "[MOVE-SKIP]" if rec.is_skip else "[MOVE]"
    vac = "  [title vacated]" if rec.title_vacated else ""
    extra = f"  ({rec.note})" if rec.note else ""
    print(f"{tag} {rec.fighter_name}: {rec.from_wc} -> {rec.to_wc}  [{rec.reason}]{vac}{extra}")


# ── Internal helpers ─────────────────────────────────────────────────────────

def _clear_candidate_flags(fighter: Fighter) -> None:
    fighter.weight_class_move_candidate = False
    fighter.weight_class_move_direction = None
    fighter.weight_class_move_reason    = None
    fighter.weight_class_move_target    = None


def _adjacent_up_wc(fighter: Fighter) -> str | None:
    idx = WEIGHT_CLASSES.index(fighter.weight_class)
    if idx >= len(WEIGHT_CLASSES) - 1:
        return None
    return WEIGHT_CLASSES[idx + 1]


def _is_win_and_vacate_eligible(fighter: Fighter) -> bool:
    org = fighter.org if fighter.tier in ("tier1", "tier2", "tier4") else ""
    if get_champion_id(fighter.weight_class, fighter.tier, org) != fighter.fighter_id:
        return False
    if get_title_defenses(fighter.weight_class, fighter.tier, org) < _WIN_AND_VACATE_MIN_DEFENSES:
        return False
    return _adjacent_up_wc(fighter) is not None


# ── Part 1: normal move execution ───────────────────────────────────────────

def _execute_move(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    target_wc: str,
    reason:    str,
    fight_num: int,
    sim_day:   int,
) -> MoveRecord:
    old_wc = fighter.weight_class
    tier   = fighter.tier
    is_skip = abs(WEIGHT_CLASSES.index(target_wc) - WEIGHT_CLASSES.index(old_wc)) == 2

    # 1 & 2: pool transfer (same tier, new division).
    pools[old_wc][tier].remove(fighter)
    pools[target_wc][tier].append(fighter)

    # Title vacancy check.
    title_vacated = False
    _org = fighter.org if tier in ("tier1", "tier2", "tier4") else ""
    if get_champion_id(old_wc, tier, _org) == fighter.fighter_id:
        vacate_title(old_wc, tier, _org)
        title_vacated = True

    # 3: rankings reset (best-effort cache eviction — see module docstring point 2).
    drop_from_rankings_cache(fighter.fighter_id, old_wc)

    # cut_severity recalibration.
    moving_up = WEIGHT_CLASSES.index(target_wc) > WEIGHT_CLASSES.index(old_wc)
    adjust = _CUT_ADJUST_SKIP if is_skip else _CUT_ADJUST_ADJACENT
    fighter.cut_severity += (-adjust if moving_up else adjust)

    # 5: update weight_class.
    fighter.weight_class = target_wc

    # Bookkeeping for Part 3's hype-boost window.
    fighter.last_move_reason               = reason
    fighter.last_move_fight_count          = len(fighter.fight_history)
    fighter.opportunity_hype_boost_applied = False

    # 4: clear session B's pending candidate flags.
    _clear_candidate_flags(fighter)

    rec = MoveRecord(
        fight_num=fight_num, sim_day=sim_day, fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, from_wc=old_wc, to_wc=target_wc,
        reason=reason, is_skip=is_skip, title_vacated=title_vacated,
    )
    _log(rec)
    return rec


# ── Part 2: win-and-vacate campaign ─────────────────────────────────────────

def _start_campaign(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int,
    sim_day:   int,
) -> None:
    target_wc = _adjacent_up_wc(fighter)
    fighter.campaign_active           = True
    fighter.campaign_weight_class     = target_wc
    fighter.campaign_fights_remaining = _CAMPAIGN_MAX_FIGHTS
    fighter.campaign_wins             = 0
    pools[target_wc][fighter.tier].append(fighter)

    _log(MoveRecord(
        fight_num=fight_num, sim_day=sim_day, fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, from_wc=fighter.weight_class, to_wc=target_wc,
        reason="win_and_vacate_start",
        note=f"{get_title_defenses(fighter.weight_class, fighter.tier, fighter.org if fighter.tier in ('tier1', 'tier2', 'tier4') else '')} defenses at home; campaigning up",
    ))


def _end_campaign(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int,
    sim_day:   int,
    *,
    success: bool,
    note:    str,
) -> None:
    campaign_wc = fighter.campaign_weight_class
    pool = pools.get(campaign_wc, {}).get(fighter.tier, [])
    pool[:] = [f for f in pool if f is not fighter]

    _log(MoveRecord(
        fight_num=fight_num, sim_day=sim_day, fighter_name=fighter.name,
        fighter_id=fighter.fighter_id, from_wc=fighter.weight_class, to_wc=campaign_wc,
        reason="win_and_vacate_title_won" if success else "win_and_vacate_failed",
        note=note,
    ))

    fighter.campaign_active           = False
    fighter.campaign_weight_class     = None
    fighter.campaign_fights_remaining = 0
    fighter.campaign_wins             = 0


def _advance_one_campaign(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int,
    sim_day:   int,
) -> None:
    home_wc, tier = fighter.weight_class, fighter.tier

    # Home-title priority: title.py's own scheduling runs autonomously against
    # the home pool (fighter was never removed from it). If it stripped the
    # belt from them, the campaign no longer makes sense — abort.
    if get_champion_id(home_wc, tier, fighter.org if tier in ("tier1", "tier2", "tier4") else "") != fighter.fighter_id:
        _end_campaign(fighter, pools, fight_num, sim_day,
                      success=False, note="lost home title mid-campaign")
        return

    campaign_wc   = fighter.campaign_weight_class
    campaign_pool = pools.get(campaign_wc, {}).get(tier, [])
    opponents     = [f for f in campaign_pool if f is not fighter]

    if not opponents:
        fighter.campaign_fights_remaining -= 1
        if fighter.campaign_fights_remaining <= 0:
            _end_campaign(fighter, pools, fight_num, sim_day,
                          success=False, note="campaign window expired (no opponents)")
        return

    opponent = random.choice(opponents)
    winner, loser = simulate_fight(fighter, opponent, org="campaign", sim_day=sim_day)
    apply_win_development_boost(winner)
    # apply_tier_transitions/retirement/cut are intentionally skipped for the
    # CAMPAIGNER here (handled through their normal main-loop appearances) —
    # a tier change would use fighter.weight_class (home) to relocate them in
    # the home pool, silently orphaning their still-tier-keyed campaign-pool
    # membership and breaking the home title-registry lookup above (it's keyed
    # on the exact (weight_class, tier) pair). The opponent is a normal single-
    # pool member of the campaign division, so full bookkeeping is safe for them,
    # matching title.py's own scheduled-fight pattern.
    maybe_update_labels(fighter)
    apply_tier_transitions(opponent, pools)
    maybe_update_labels(opponent)
    if not is_removed(opponent.fighter_id):
        removed = maybe_evaluate_retirement(opponent, pools, fight_num=fight_num)
        if not removed:
            maybe_evaluate_cut(opponent, pools, fight_num=fight_num)

    fighter.campaign_fights_remaining -= 1

    if winner is fighter:
        fighter.campaign_wins += 1
        if fighter.campaign_wins >= _CAMPAIGN_WINS_TO_TITLE:
            # The belt won here is the CAMPAIGN division's, but the campaigner's
            # Fighter.weight_class stays their home division for the whole
            # campaign -- award_title must be told the division explicitly, or
            # it would re-award the HOME belt (spuriously bumping the home
            # defense counter) and leave this vacate_title stripping a belt the
            # campaigner never held.
            award_title(fighter, weight_class=campaign_wc)
            print(f"[MOVE] {fighter.name} becomes DUAL-CHAMPION at {campaign_wc}!")
            vacate_title(campaign_wc, tier, fighter.org if tier in ("tier1", "tier2", "tier4") else "")
            _end_campaign(fighter, pools, fight_num, sim_day,
                          success=True, note="won title, immediately vacated per win-and-vacate")
            return
    else:
        _end_campaign(fighter, pools, fight_num, sim_day,
                      success=False, note=f"lost to {opponent.name} during campaign")
        return

    if fighter.campaign_fights_remaining <= 0:
        _end_campaign(fighter, pools, fight_num, sim_day,
                      success=False, note="campaign window expired")


def advance_campaigns(
    all_fighters: list[Fighter],
    pools:        dict[str, dict[str, list[Fighter]]],
    fight_num:    int = 0,
    sim_day:      int = 0,
) -> None:
    """
    Whole-population sweep: advance every fighter currently on an active
    win-and-vacate campaign by one directly-simulated fight. Call once per
    main sim-loop iteration (same shape as advance_all_ages()).
    """
    for fighter in list(all_fighters):
        if not fighter.campaign_active:
            continue
        if is_removed(fighter.fighter_id):
            # Removed via the normal retirement/cut channel while campaigning
            # (rare — see module docstring). Clean up the stale campaign-pool
            # reference; all_fighters cleanup is the caller's existing job.
            pool = pools.get(fighter.campaign_weight_class, {}).get(fighter.tier, [])
            pool[:] = [f for f in pool if f is not fighter]
            fighter.campaign_active = False
            continue
        _advance_one_campaign(fighter, pools, fight_num, sim_day)


# ── Part 3: opportunity hype boost ──────────────────────────────────────────

def _maybe_apply_opportunity_hype_boost(fighter: Fighter) -> None:
    if fighter.last_move_reason != "opportunity" or fighter.opportunity_hype_boost_applied:
        return

    fights_since = len(fighter.fight_history) - fighter.last_move_fight_count
    if fights_since > _OPPORTUNITY_HYPE_WINDOW:
        fighter.opportunity_hype_boost_applied = True  # window closed; stop checking
        return

    _org = fighter.org if fighter.tier in ("tier1", "tier2", "tier4") else ""
    if get_champion_id(fighter.weight_class, fighter.tier, _org) != fighter.fighter_id:
        return  # not champion yet — keep checking next cycles until the window closes

    speed_frac = 1.0 - (fights_since / _OPPORTUNITY_HYPE_WINDOW)
    boost = _OPPORTUNITY_HYPE_BASE + speed_frac * _OPPORTUNITY_HYPE_SPEED_BONUS
    fighter.hype += boost
    fighter.opportunity_hype_boost_applied = True

    _log(MoveRecord(
        fight_num=0, sim_day=0, fighter_name=fighter.name, fighter_id=fighter.fighter_id,
        from_wc=fighter.weight_class, to_wc=fighter.weight_class,
        reason="opportunity_hype_boost",
        note=f"title won {fights_since} fights after opportunity move (+{boost:.1f} hype)",
    ))


# ── Public per-fighter hook ──────────────────────────────────────────────────

def maybe_process_weight_transfers(
    fighter:   Fighter,
    pools:     dict[str, dict[str, list[Fighter]]],
    fight_num: int = 0,
    sim_day:   int = 0,
) -> MoveRecord | None:
    """
    Fire every LABEL_UPDATE_INTERVAL fights — identical cadence/trigger to
    weight_movement.maybe_evaluate_weight_move(). Call immediately after it in
    the same cycle so a flag it just set is consumed here, same cycle.

    Consumes fighter.weight_class_move_candidate (if set): either starts a
    win-and-vacate campaign (title_ambition + entrenched champion) or executes
    a normal move. Also checks the opportunity hype-boost window regardless of
    whether a new move fires this cycle.
    """
    n = len(fighter.fight_history)
    if n == 0 or n % LABEL_UPDATE_INTERVAL != 0:
        return None

    _maybe_apply_opportunity_hype_boost(fighter)

    if not fighter.weight_class_move_candidate:
        return None

    reason = fighter.weight_class_move_reason
    target = fighter.weight_class_move_target
    if target is None:
        _clear_candidate_flags(fighter)  # defensive — session B should never leave this None
        return None

    if reason == "title_ambition" and _is_win_and_vacate_eligible(fighter):
        _start_campaign(fighter, pools, fight_num, sim_day)
        _clear_candidate_flags(fighter)
        return None

    return _execute_move(fighter, pools, target, reason, fight_num, sim_day)


# ── Demo (run as __main__) ───────────────────────────────────────────────────

if __name__ == "__main__":
    import random as _random

    from career.tiers import generate_all_tiers
    from engine.fight import simulate_fight as _simulate_fight
    from matchmaking import pick_opponent, apply_tier_transitions as _att
    from career.labels import (
        maybe_update_labels as _mul, reset_title_registry, update_labels,
        CHAMPION, get_title_defenses as _gtd,
    )
    from title import reset_title_scheduling, maybe_run_title_fight
    from career.age import advance_all_ages, reset_age_advancement
    from career.development import (
        advance_all_development, apply_win_development_boost as _awdb,
        reset_development_advancement,
    )
    from career.cuts import maybe_evaluate_cut as _mec, reset_cut_registry
    from career.retirement import maybe_evaluate_retirement as _mer, reset_retirement_scanning
    from career.rankings import update_rankings, reset_rankings, RANKINGS_UPDATE_INTERVAL
    from sim_calendar import reset_sim_clock, advance_sim_clock, get_sim_day
    from career.weight_movement import maybe_evaluate_weight_move

    _random.seed(101)
    N_FIGHTS = 6000

    pools = generate_all_tiers(scale=1.0)
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()
    reset_retirement_scanning()
    reset_weight_transfer_log()

    all_fighters = [f for wc in pools.values() for tp in wc.values() for f in tp]

    for i in range(N_FIGHTS):
        if not all_fighters:
            break
        a = _random.choice(all_fighters)
        try:
            b = pick_opponent(a, pools)
        except IndexError:
            continue
        if b is None:
            continue

        fight_wc, fight_tier = a.weight_class, a.tier
        day = get_sim_day()
        winner, loser = _simulate_fight(a, b, org="league", sim_day=day)
        _awdb(winner)

        to_remove = []
        for f in (winner, loser):
            _att(f, pools)
            _mul(f)
            removed = _mer(f, pools, fight_num=i + 1)
            if not removed:
                removed = _mec(f, pools, fight_num=i + 1)
            if removed:
                to_remove.append(f)
            else:
                maybe_evaluate_weight_move(f, pools)
                maybe_process_weight_transfers(f, pools, fight_num=i + 1, sim_day=day)
        for rf in to_remove:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        maybe_run_title_fight(fight_wc, fight_tier, pools, org="league", fight_num=i + 1, all_fighters=all_fighters)

        advance_sim_clock()
        advance_all_ages(all_fighters)
        advance_all_development(all_fighters)
        advance_campaigns(all_fighters, pools, fight_num=i + 1, sim_day=get_sim_day())

        if (i + 1) % RANKINGS_UPDATE_INTERVAL == 0:
            update_rankings(pools)

    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)

    log = get_move_log()
    print(f"\nWeight-class move log after {N_FIGHTS} fights ({len(log)} events)\n")

    if log:
        print(f"  {'#':>5}  {'Day':>5}  {'Fighter':<24} {'From':<12} {'To':<12} {'Reason':<24} {'Vacated'}  Note")
        print("  " + "-" * 130)
        for rec in log:
            print(
                f"  {rec.fight_num:>5}  {rec.sim_day:>5}  {rec.fighter_name:<24} "
                f"{rec.from_wc:<12} {rec.to_wc:<12} {rec.reason:<24} "
                f"{'YES' if rec.title_vacated else '-':<7}  {rec.note}"
            )
    else:
        print("  (no move events this run — constructing a synthetic case instead)\n")

        from career.labels import award_title, CHAMPION as _CH

        f = next((x for x in all_fighters if x.fight_history and x.tier != "tier0"), all_fighters[0])
        target_wc = "welterweight" if f.weight_class != "welterweight" else "heavyweight"
        f.weight_class_move_candidate = True
        f.weight_class_move_direction = "up" if WEIGHT_CLASSES.index(target_wc) > WEIGHT_CLASSES.index(f.weight_class) else "down"
        f.weight_class_move_reason    = "opportunity"
        f.weight_class_move_target    = target_wc
        n_before = len(f.fight_history)
        # Pad fight_history so the LABEL_UPDATE_INTERVAL gate passes.
        while len(f.fight_history) % LABEL_UPDATE_INTERVAL != 0 or len(f.fight_history) == n_before:
            from career.fighter import FightResult
            f.fight_history.append(FightResult(opponent_name="synthetic", outcome="win", method="decision", org="test", tier=f.tier))

        print(f"  Synthetic candidate: {f.name}  {f.weight_class} -> {target_wc}  (forced 'opportunity' flag)\n")
        rec = maybe_process_weight_transfers(f, pools, fight_num=N_FIGHTS + 1, sim_day=get_sim_day())
        print(f"\n  Result: weight_class={f.weight_class}  cut_severity={f.cut_severity:+.1f}  "
              f"candidate_flag_cleared={not f.weight_class_move_candidate}")

    print(f"\n  Total moves executed: {sum(1 for r in log if r.reason in ('title_ambition','struggling','cut_damage','opportunity'))}")
    print(f"  Class skips:          {sum(1 for r in log if r.is_skip)}")
    print(f"  Title vacancies:      {sum(1 for r in log if r.title_vacated)}")
    print(f"  Campaign starts:      {sum(1 for r in log if r.reason == 'win_and_vacate_start')}")
    print(f"  Dual-champion wins:   {sum(1 for r in log if r.reason == 'win_and_vacate_title_won')}")
    print(f"  Campaign failures:    {sum(1 for r in log if r.reason == 'win_and_vacate_failed')}")
    print(f"  Hype boosts:          {sum(1 for r in log if r.reason == 'opportunity_hype_boost')}\n")
