"""
Fight-night simulator with rich terminal output.

Usage:
  python sim.py                          # 2000 fights, default pyramid roster (~735 fighters)
  python sim.py --fights 5000            # more fights
  python sim.py --fighters 1.5           # bigger roster (~1100 fighters)
  python sim.py --seed 7                 # different random seed
  python sim.py --debug                  # per-fight compact table instead of panels
"""
from __future__ import annotations

import argparse
import random
from types import SimpleNamespace

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from engine.fight import simulate_fight, SCALE
from career.fighter import Fighter
from career.tiers import TIER_LEVELS, WEIGHT_CLASSES, generate_all_tiers
from matchmaking import (
    pick_opponent, pick_scheduled_elite_a, pick_discovery_a, apply_tier_transitions,
    reset_gate_stats, get_gate_stats,
    reset_elite_pairings, get_elite_pairings, ElitePairingRecord,
    ELITE_FIGHT_INTERVAL, ELITE_DISCOVERY_INTERVAL,
)
from career.labels import maybe_update_labels, reset_title_registry, update_labels, get_champion_id, is_champion, CONTENDER
from title import reset_title_scheduling, maybe_run_title_fight, get_title_history, TITLE_FIGHT_INTERVAL
from career.age import advance_all_ages, reset_age_advancement
from career.development import (
    advance_all_development, apply_win_development_boost,
    apply_phase_development_feedback, reset_development_advancement,
)
from career.academy_reputation import (
    update_academy_reputations, reset_academy_reputation,
)
from career.hype import (
    update_hype_after_fight, advance_all_hype_decay, reset_hype_decay,
)
from career.cuts import maybe_evaluate_cut, get_cut_log, reset_cut_registry
from career.retirement import maybe_evaluate_retirement, maybe_retire_inactive, reset_retirement_scanning
from career.weight_movement import maybe_evaluate_weight_move
from career.weight_transfers import (
    maybe_process_weight_transfers, advance_campaigns,
    get_move_log, reset_weight_transfer_log,
)
from career.rankings import (
    update_rankings, get_rankings, reset_rankings,
    RANKINGS_UPDATE_INTERVAL, RANKINGS_SIZE,
    _W_WIN_RATE, _W_QUALITY, _W_HYPE,
)
from career.nonelite_rankings import (
    update_nonelite_rankings, reset_nonelite_rankings,
    get_midmajor_rankings, get_midmajor_org_rankings, get_toporg_rankings,
    maybe_apply_scout_notice, reset_scout_notice_log, get_scout_notice_log,
    MIDMAJOR_LIST_SIZE, TOPORG_LIST_SIZE,
)
from career.org_rankings import (
    update_org_rankings, reset_org_rankings, get_org_rankings,
)
from orgs.org_registry import (
    ORG_NAMES, APEX_FC_NAME, THE_LEAGUE_NAME, EASTERN_GP_NAME, decision_mode_for_org,
    MIDMAJOR_ORG_NAMES, REGIONAL_ORG_NAMES,
)
from orgs.league_season import (
    run_league_season, reset_league_season, reset_league_history, get_league_history,
    LEAGUE_SEASON_LENGTH, LEAGUE_PLAYOFF_SIZE, FINISH_WIN_POINTS, DECISION_WIN_POINTS,
)
from orgs.org_movement import (
    run_org_movement_sweep, reset_org_movement_log, get_org_movement_log,
    MAX_APEX_ROSTER,
)
from sim_calendar import reset_sim_clock, advance_sim_clock, get_sim_day, SIM_DAYS_PER_FIGHT
from career.replenishment import (
    initialize_replenishment, run_replenishment,
    get_replenishment_history, get_backstop_log, get_event_log, get_total_generated,
    get_inflow_counts,
    FLOOR_THRESHOLDS, TIER4_ORG_FLOORS,
)

console = Console()

# ── Sim state container (api.py stepping support) ─────────────────────────────
# Holds everything init_sim()/step_sim() need to survive between calls, since
# run()'s old local `pools`/`all_fighters` would otherwise vanish when run()
# returns. get_sim_state() is the one supported way for api.py to reach in.
#
# fight_index is NOT part of the originally-sketched shape (all_fighters,
# pools, current_day, initialized, params) -- it's a necessary addition: the
# old loop's `i` (fight_num = i+1) drives RANKINGS_UPDATE_INTERVAL,
# ELITE_FIGHT_INTERVAL, and every fight_num=... log/gate throughout this
# module, so it must persist across separate step_sim() calls rather than
# restarting at 0 each time, or those cadences would desync from real
# simulated history.
_sim_state = SimpleNamespace(
    all_fighters=None,
    pools=None,
    current_day=0,
    initialized=False,
    params=None,      # {"scale":..., "seed":..., "debug":...}
    fight_index=0,    # next 0-based fight attempt index (fight_num = fight_index + 1)
)


def get_sim_state() -> SimpleNamespace:
    return _sim_state

_TIER_SHORT: dict[str, str] = {
    "tier0": "Amateur",
    "tier1": "Regional",
    "tier2": "Mid-major",
    "tier3": "Top-org",
    "tier4": "Elite",
}

_WC_SHORT: dict[str, str] = {
    "lightweight":  "LW",
    "welterweight": "WW",
    "heavyweight":  "HW",
}

_TEMPLATE_SHORT: dict[str, str] = {
    "dagestan_sambo":     "Dagestan",
    "american_wrestling": "Amer.Wres",
    "brazilian":          "Brazilian",
    "muay_thai":          "Muay Thai",
    "sea_mixed":          "SEA Mixed",
}


def _true_prob(ovr_a: float, ovr_b: float) -> float:
    """Noiseless logistic probability — structural signal without per-fight jitter."""
    return 1.0 / (1.0 + 10.0 ** (-(ovr_a - ovr_b) / SCALE))


def init_sim(scale: float, seed: int, debug: bool = False) -> None:
    """
    Build a fresh population and reset every stateful subsystem's registry --
    this is exactly what used to be inlined at the top of run(). Populates
    _sim_state so step_sim()/get_sim_state() (and api.py, via those) can pick
    up where this leaves off. Calling it again fully resets state, same as
    starting a fresh run().
    """
    random.seed(seed)
    pools = generate_all_tiers(scale=scale)
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_nonelite_rankings()
    reset_org_rankings()
    reset_scout_notice_log()
    reset_gate_stats()
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()
    reset_academy_reputation()
    reset_hype_decay()
    initialize_replenishment()
    reset_retirement_scanning()
    reset_elite_pairings()
    reset_weight_transfer_log()
    reset_league_season()
    reset_league_history()
    reset_org_movement_log()
    all_fighters = [
        f
        for wc_pools in pools.values()
        for tier_pool in wc_pools.values()
        for f in tier_pool
    ]

    _sim_state.pools = pools
    _sim_state.all_fighters = all_fighters
    _sim_state.current_day = get_sim_day()
    _sim_state.fight_index = 0
    _sim_state.params = {"scale": scale, "seed": seed, "debug": debug}
    _sim_state.initialized = True


def _run_additive_elite_fight(
    fighter_a: Fighter, pools: dict, all_fighters: list, fight_num: int,
) -> None:
    """
    Resolve one additively-injected tier4 fight (fighter_a already selected)
    and run the same post-fight bookkeeping every other fight source in this
    sim runs -- dev boost, phase feedback, hype, tier transitions, labels,
    retirement/cut, title-fight-counter increment, then the annual-cadence
    advances (clock/age/development/academy reputation/hype decay).

    Shared by both additive density mechanisms (ELITE_FIGHT_INTERVAL's
    ranked-density injection and ELITE_DISCOVERY_INTERVAL's under-fought-
    fighter injection) -- they differ only in how fighter_a was selected;
    everything downstream of that is identical, so it lives here once.

    These are fully normal fights -- no special org tag, no exclusion from
    any downstream system (wins, losses, promotion, demotion, labels,
    rankings, hype, development all treat them like any other fight).
    """
    try:
        _be = pick_opponent(fighter_a, pools)
    except IndexError:
        return
    _ewc, _etier, _eday = fighter_a.weight_class, fighter_a.tier, get_sim_day()
    _eorg = fighter_a.org if _etier == "tier4" else ""
    _edecision_mode = decision_mode_for_org(_eorg) if _eorg else "total_score"
    _ew, _el = simulate_fight(fighter_a, _be, org="league", sim_day=_eday,
                               decision_mode=_edecision_mode)
    apply_win_development_boost(_ew)
    apply_phase_development_feedback(_ew)
    apply_phase_development_feedback(_el)
    update_hype_after_fight(_ew, _el)
    update_hype_after_fight(_el, _ew)
    _erm: list = []
    for _ef in (_ew, _el):
        apply_tier_transitions(_ef, pools)
        maybe_update_labels(_ef)
        _er = maybe_evaluate_retirement(_ef, pools, fight_num=fight_num)
        if not _er:
            _er = maybe_evaluate_cut(_ef, pools, fight_num=fight_num)
        if _er:
            _erm.append(_ef)
    for _erf in _erm:
        all_fighters[:] = [f for f in all_fighters if f is not _erf]
    maybe_run_title_fight(_ewc, _etier, pools, org="league",
                         fight_num=fight_num, all_fighters=all_fighters,
                         top_tier_org=_eorg)
    advance_sim_clock()
    advance_all_ages(all_fighters)
    advance_all_development(all_fighters)
    update_academy_reputations(all_fighters, get_sim_day())
    advance_all_hype_decay(all_fighters)


def _run_one_bout(
    pools: dict, all_fighters: list, i: int, n_fights: int, debug: bool, verbose: bool,
) -> bool:
    """
    One attempt of the original run() fight loop's body (`for i in range(n_fights)`,
    one full iteration). Returns True if the roster is exhausted and the caller
    should stop looping entirely (mirrors the original `if not all_fighters: break`);
    False otherwise, whether or not an actual bout happened this attempt (mirrors
    the original `continue` paths, which still consumed one value of `i`).

    verbose gates ALL console output (the debug one-liner and the Panel) so this
    same function serves both run()'s rich CLI output (verbose=True) and
    step_sim()'s headless API-driven steps (verbose=False).
    """
    if not all_fighters:
        return True
    a = random.choice(all_fighters)
    if a.tier == "tier4" and a.org == THE_LEAGUE_NAME:
        # The League's fighters get their fights exclusively through
        # orgs/league_season.py's dedicated scheduler (called below,
        # once per iteration) -- never through normal random matchmaking.
        return False
    if is_champion(a):
        # A reigning champion's only fights are scheduled title defenses
        # (title.maybe_run_title_fight) -- an ordinary matchmaking fight
        # wouldn't count as a defense and shouldn't be able to happen at all.
        return False
    try:
        b = pick_opponent(a, pools)
    except IndexError:
        # Division pool exhausted — skip this iteration rather than crash.
        return False

    # Capture tier+wc(+org) before transitions — title scheduling uses the
    # pool where the fight took place, not where A ends up afterward.
    fight_wc   = a.weight_class
    fight_tier = a.tier
    # tier1 (regional), tier2 (mid-major), and tier4 (top-tier) all carry
    # a real org; other tiers have no org concept. Regular tier1/tier2
    # matchmaking is NOT org-partitioned (only title fights are, per
    # Session B1 Part 5 / B2 Part 4) -- so the title-fight activity
    # counter below tracks fighter A's org regardless of which org B
    # happens to be from, same as tier4's pre-hard-partition convention.
    fight_org  = a.org if fight_tier in ("tier1", "tier2", "tier4") else ""
    current_day = get_sim_day()

    p_a_wins = _true_prob(a.overall, b.overall)
    decision_mode = decision_mode_for_org(fight_org) if fight_org else "total_score"
    winner, loser = simulate_fight(a, b, org="league", sim_day=current_day,
                                    decision_mode=decision_mode)
    result = winner.fight_history[-1]

    # Win-triggered development boost — fires on the BASE fighter object (not the
    # effective copy used inside fight resolution), so the gain is durable.
    apply_win_development_boost(winner)
    # Style-mixing feedback: fires for both winner and loser, gated on
    # non-primary-phase time exposure for this fight (see development.py).
    apply_phase_development_feedback(winner)
    apply_phase_development_feedback(loser)
    # Dynamic hype: base win/loss + style modifiers for both participants.
    update_hype_after_fight(winner, loser)
    update_hype_after_fight(loser, winner)

    # Apply tier transitions, label updates,
    # retirement (checked first), then cut (skips already-retired fighters).
    # Age advancement is now global (advance_all_ages below) -- not per-fight.
    transitions: dict[str, tuple[str, str]] = {}   # name -> (old_tier, new_tier)
    fighters_to_remove: list = []
    for fighter in (winner, loser):
        old_tier = fighter.tier
        new_tier = apply_tier_transitions(fighter, pools)
        if new_tier:
            transitions[fighter.name] = (old_tier, new_tier)
        maybe_update_labels(fighter)
        removed = maybe_evaluate_retirement(fighter, pools, fight_num=i + 1)
        if not removed:
            removed = maybe_evaluate_cut(fighter, pools, fight_num=i + 1)
        if removed:
            fighters_to_remove.append(fighter)
        else:
            maybe_evaluate_weight_move(fighter, pools)
            maybe_process_weight_transfers(fighter, pools, fight_num=i + 1, sim_day=current_day)

    for rf in fighters_to_remove:
        all_fighters[:] = [f for f in all_fighters if f is not rf]

    # Check whether a title fight is due in this pool (top_tier_org is only
    # meaningful at tier4 -- Apex FC / Eastern GP each get their own belt;
    # The League is a no-op here, see title.maybe_run_title_fight's guard).
    maybe_run_title_fight(fight_wc, fight_tier, pools, org="league", fight_num=i + 1,
                          all_fighters=all_fighters, top_tier_org=fight_org)

    # The League's own dedicated season/playoff scheduler (Org Identity
    # session, Part 3) -- gated internally on LEAGUE_FIGHT_INTERVAL, a
    # no-op most iterations.
    run_league_season(pools, fight_num=i + 1, sim_day=current_day, all_fighters=all_fighters)

    # Cross-org free agency sweep (Org Identity session, Part 6) -- gated
    # internally per-fighter on LABEL_UPDATE_INTERVAL, same cadence as labels/cuts.
    run_org_movement_sweep(all_fighters, pools, fight_num=i + 1)

    # Advance the global clock after all activity for this iteration is stamped.
    advance_sim_clock()

    # Age all fighters once per SIM_DAYS_PER_YEAR — inactive fighters age too.
    advance_all_ages(all_fighters)
    # Development sweeps the same cadence; called after age so age_factor reflects
    # the just-incremented age (fighters who turned 23 this year get age_factor=0).
    advance_all_development(all_fighters)
    # Academy reputation feedback loop: same annual cadence, alongside development.
    update_academy_reputations(all_fighters, get_sim_day())
    # Hype decay: same annual cadence -- inactive fighters (including those who
    # went quiet) lose buzz proportionally.
    advance_all_hype_decay(all_fighters)

    # Advance any active win-and-vacate campaigns (weight_transfers.py) by one
    # directly-simulated fight each — self-initiated events, not triggered by
    # a specific fighter's own regular bout landing on the periodic cadence.
    advance_campaigns(all_fighters, pools, fight_num=i + 1, sim_day=get_sim_day())

    # Academy replenishment: generate prospects from academies whose schedule
    # has elapsed, and run the quarterly population floor backstop.
    run_replenishment(pools, all_fighters)

    # Biannual scan: retire fighters inactive for >= RETIRE_INACTIVE_GAP_DAYS.
    newly_retired = maybe_retire_inactive(all_fighters, pools, fight_num=i + 1)
    for rf in newly_retired:
        all_fighters[:] = [f for f in all_fighters if f is not rf]

    # Recompute Elite rankings every RANKINGS_UPDATE_INTERVAL sim fights.
    # Non-Elite (Mid-major/Top-org) rankings share the same cadence -- not
    # a separate trigger. Scout-notice fast-track promotion evaluates right
    # after, since it needs freshly computed rank positions as an input.
    if (i + 1) % RANKINGS_UPDATE_INTERVAL == 0:
        update_rankings(pools)
        update_nonelite_rankings(pools)
        update_org_rankings(pools)
        maybe_apply_scout_notice(pools, fight_num=i + 1)

    # ── Scheduled Elite fight (option b density fix) ─────────────────────
    # Injected additively every ELITE_FIGHT_INTERVAL main fights.  Non-Elite
    # fighters are unaffected; Elite pool replenishment rate is unchanged.
    # These are fully normal fights — no special org tag, no exclusion from
    # any downstream system (wins, losses, promotion, demotion, labels,
    # rankings, hype, development all treat them like any other fight).
    if ELITE_FIGHT_INTERVAL > 0 and (i + 1) % ELITE_FIGHT_INTERVAL == 0:
        _ae = pick_scheduled_elite_a(pools)
        if _ae is not None:
            _run_additive_elite_fight(_ae, pools, all_fighters, i + 1)

    # ── Discovery fight (under-fought tier4 fighters) ────────────────────
    # Sibling of the block above, additively injected on its own (deliberately
    # less frequent) cadence -- see matchmaking.ELITE_DISCOVERY_INTERVAL/
    # pick_discovery_a for the full rationale. Gives fighters who rarely get
    # drawn by ordinary matchmaking a bounded, self-limiting shot at building
    # a real tier4 record, without repeating the rejected option (a) failure
    # mode (uncapped whole-tier rate = uncapped whole-tier churn).
    if ELITE_DISCOVERY_INTERVAL > 0 and (i + 1) % ELITE_DISCOVERY_INTERVAL == 0:
        _da = pick_discovery_a(pools)
        if _da is not None:
            _run_additive_elite_fight(_da, pools, all_fighters, i + 1)

    if debug:
        if verbose:
            a_won = winner is a
            outcome = "A wins" if a_won else "B wins"
            upset = (a_won and p_a_wins < 0.5) or (not a_won and p_a_wins >= 0.5)
            line = Text()
            line.append(f"  {i+1:>2}  ", style="dim")
            line.append(f"{a.name:<24}", style="white")
            line.append(f"{a.overall:>+5.1f}  ", style="cyan")
            line.append(f"{b.name:<24}", style="white")
            line.append(f"{b.overall:>+5.1f}  ", style="cyan")
            line.append(f"{p_a_wins:.2f}  ", style="yellow")
            line.append(outcome, style="green" if a_won else "red")
            if upset:
                line.append(" *", style="bold yellow")
            line.append(f"  d{current_day}", style="dim")
            console.print(line)
        return False

    if verbose:
        is_upset = (winner is b and a.overall - b.overall > 8) or \
                   (winner is a and b.overall - a.overall > 8)

        body = Text()
        body.append(f"  {a.name}", style="bold white")
        body.append(
            f"  ({_WC_SHORT.get(a.weight_class, a.weight_class)} | "
            f"{_TIER_SHORT.get(a.tier, a.tier)}, {a.overall:+.1f})\n",
            style="dim",
        )
        body.append("     vs\n", style="dim")
        body.append(f"  {b.name}", style="bold white")
        body.append(
            f"  ({_WC_SHORT.get(b.weight_class, b.weight_class)} | "
            f"{_TIER_SHORT.get(b.tier, b.tier)}, {b.overall:+.1f})\n\n",
            style="dim",
        )
        body.append(f"  WINNER  {winner.name}", style="bold green")
        body.append(f"  by {result.method}\n", style="green")
        body.append(
            f"  {winner.name} {winner.record_str}   {loser.name} {loser.record_str}",
            style="dim",
        )
        if is_upset:
            body.append("   [UPSET]", style="bold yellow")
        for name, (old_tier, new_tier) in transitions.items():
            tier_label = _TIER_SHORT.get(new_tier, new_tier)
            direction = "PROMOTED to" if TIER_LEVELS.index(new_tier) > TIER_LEVELS.index(old_tier) else "DEMOTED to"
            body.append(f"\n  [{direction} {tier_label}]", style="bold magenta")

        console.print(Panel(body, title=f"[dim]Bout {i + 1} of {n_fights} | Day {current_day}[/dim]", expand=False))

    return False


def step_sim(days: int, verbose: bool = False, n_fights_hint: int | None = None) -> int:
    """
    Advance the simulation by roughly `days` simulated days.

    The sim's atomic unit of progress is one FIGHT ATTEMPT, not a raw day --
    each resolved bout consumes exactly SIM_DAYS_PER_FIGHT calendar days
    (advance_sim_clock()), while a skipped attempt (League tier4 fighter,
    exhausted division pool) consumes none. `days` is converted to an
    attempt count via days // SIM_DAYS_PER_FIGHT (minimum 1) -- the closest
    faithful mapping onto the fight-attempt-indexed cadence every periodic
    system in this module is already keyed on (RANKINGS_UPDATE_INTERVAL,
    ELITE_FIGHT_INTERVAL, LABEL_UPDATE_INTERVAL, etc.), rather than
    reimplementing a separate day-granular scheduler. Actual elapsed days
    (the return value / _sim_state.current_day) may therefore differ
    slightly from the requested `days`.

    verbose/n_fights_hint exist so run() can route its own loop through this
    same function (per the intended init_sim()/step_sim() split) without
    losing its per-bout console output. The /advance API endpoint should
    never pass them -- the defaults produce a silent step.
    """
    if not _sim_state.initialized:
        raise RuntimeError("Simulation not initialized -- call init_sim() first.")
    pools = _sim_state.pools
    all_fighters = _sim_state.all_fighters
    debug = _sim_state.params["debug"]
    n_attempts = max(1, days // SIM_DAYS_PER_FIGHT)
    for _ in range(n_attempts):
        i = _sim_state.fight_index
        stop = _run_one_bout(pools, all_fighters, i, n_fights_hint or (i + 1), debug, verbose)
        _sim_state.fight_index = i + 1
        if stop:
            break
    _sim_state.current_day = get_sim_day()
    return _sim_state.current_day


def run(n_fights: int, scale: float, seed: int, debug: bool = False) -> None:
    init_sim(scale, seed, debug=debug)
    pools = _sim_state.pools
    all_fighters = _sim_state.all_fighters
    total = len(all_fighters)

    console.print()
    console.print(
        f"[bold cyan]MMA Fight Night[/bold cyan]  "
        f"[dim]seed={seed}  |  {total} fighters across {len(WEIGHT_CLASSES)} divisions  |  {n_fights} bouts[/dim]"
    )
    for wc in WEIGHT_CLASSES:
        wc_total = sum(len(pools[wc][t]) for t in TIER_LEVELS)
        console.print(
            f"[dim]  {_WC_SHORT[wc]}:  "
            + "  ".join(f"{_TIER_SHORT[t]}={len(pools[wc][t])}" for t in TIER_LEVELS)
            + f"  ({wc_total})[/dim]"
        )
    console.print()

    if debug:
        console.print("[dim]  #   Fighter A               OvrA   Fighter B               OvrB   P(A)  Result   Day[/dim]")
        console.print("[dim]" + "-" * 90 + "[/dim]")

    # ── Fight loop (routed through step_sim, one bout attempt per call) ───────
    for i in range(n_fights):
        step_sim(SIM_DAYS_PER_FIGHT, verbose=True, n_fights_hint=n_fights)
        if not all_fighters:
            break

    if debug:
        console.print("[dim]  * = underdog won[/dim]\n")

    # ── Force final label recompute and rankings update ───────────────────────
    # maybe_update_labels fires only on multiples of LABEL_UPDATE_INTERVAL;
    # fighters whose last fight didn't land on a multiple would have stale labels.
    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)   # final authoritative snapshot used by all output below
    update_nonelite_rankings(pools)   # same, for the Mid-major/Top-org tables below
    update_org_rankings(pools)   # same, for the per-org Elite tables below

    # ── Calendar summary ──────────────────────────────────────────────────────
    final_day = get_sim_day()
    console.print()
    console.print(Rule(f"[bold]Sim Calendar[/bold]"))
    console.print(
        f"  Day 0 -> Day {final_day}"
        f"  ({final_day / 365.25:.1f} simulated years"
        f"  |  {SIM_DAYS_PER_FIGHT} days/fight)"
    )
    # Sample fight dates from the 3 most-experienced fighters to confirm
    # the calendar advances correctly (each successive fight should show a
    # higher sim_day than the previous one for the same fighter).
    sample_fighters = sorted(
        [f for f in all_fighters if f.fight_history],
        key=lambda f: len(f.fight_history),
        reverse=True,
    )[:3]
    if sample_fighters:
        console.print()
        for sf in sample_fighters:
            stamped = [r for r in sf.fight_history if r.sim_day >= 0]
            if not stamped:
                continue
            sample = stamped[:5]
            dates_str = "  ".join(f"d{r.sim_day}" for r in sample)
            console.print(
                f"  [dim]{sf.name} ({sf.record_str}):[/dim]  {dates_str}"
                + (f"  [dim]... ({len(stamped)} total)[/dim]" if len(stamped) > 5 else "")
            )
    console.print()

    # ── Title fight history ───────────────────────────────────────────────────
    history = get_title_history()
    console.print()
    console.print(Rule(f"[bold]Title Fight History[/bold]  [dim]({len(history)} bouts | 1 per {TITLE_FIGHT_INTERVAL} regular fights per pool)[/dim]"))
    console.print()

    if history:
        th = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        th.add_column("#",       justify="right",  style="dim",          width=5)
        th.add_column("Div",     style="dim",                            width=3)
        th.add_column("Tier",    style="dim",                            width=10)
        th.add_column("Winner",  no_wrap=True,     style="bold green",   min_width=22)
        th.add_column("Loser",   no_wrap=True,     style="dim",          min_width=22)
        th.add_column("Method",  style="cyan",                           width=11)
        th.add_column("Rnds",    justify="right",  style="dim",          width=4)
        th.add_column("Note",    style="yellow",                         width=7)

        for rec in history:
            note = "VACANT" if rec.was_vacant else "DEF."
            th.add_row(
                str(rec.fight_num),
                _WC_SHORT.get(rec.weight_class, rec.weight_class),
                _TIER_SHORT.get(rec.tier_key, rec.tier_key),
                rec.winner_name,
                rec.loser_name,
                rec.method,
                str(rec.rounds_completed),
                note,
            )
        console.print(th)
    else:
        console.print("[dim]  No title fights occurred (increase --fights or lower TITLE_FIGHT_INTERVAL).[/dim]")

    # ── Releases & retirements ────────────────────────────────────────────────
    roster_events = get_cut_log()
    n_cuts     = sum(1 for r in roster_events if r.reason == "cut")
    n_retired  = sum(1 for r in roster_events if r.reason == "retired")
    n_on_top   = sum(1 for r in roster_events if r.reason == "retired_on_top")
    console.print()
    console.print(Rule(
        f"[bold]Releases & Retirements[/bold]  "
        f"[dim]({n_cuts} cut  |  {n_retired} retired  |  {n_on_top} on-top)[/dim]"
    ))
    console.print()

    if roster_events:
        _LABEL_ABBREV = {
            "Champion":   "C",
            "Legend":     "L",
            "Contender":  "cont",
            "Prospect":   "pros",
            "Gatekeeper": "gk",
            "Journeyman": "jrny",
            "Washed":     "wash",
        }
        _LABEL_PRIORITY_CUT = [
            "Champion", "Legend", "Contender", "Prospect",
            "Gatekeeper", "Journeyman", "Washed",
        ]

        def _cut_label_str(ls: frozenset[str]) -> str:
            ordered = [_LABEL_ABBREV[lb] for lb in _LABEL_PRIORITY_CUT if lb in ls]
            return " ".join(ordered) if ordered else "-"

        def _type_str(rec) -> str:
            if rec.reason == "retired_on_top":
                base = "ON TOP"
            elif rec.reason == "retired":
                base = "RETIRED"
            else:
                base = "CUT"
            return base + " (belt)" if rec.title_vacated else base

        ct = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        ct.add_column("Sim#",    justify="right",  style="dim",        width=5)
        ct.add_column("Fighter", no_wrap=True,     style="white",      width=20)
        ct.add_column("Div",                       style="dim",        width=3)
        ct.add_column("Tier",                      style="dim",        width=9)
        ct.add_column("Age",     justify="right",  style="dim",        width=3)
        ct.add_column("Rec",     justify="center", style="bold",       width=6)
        ct.add_column("Labels",                    style="yellow",     width=12)
        ct.add_column("Type",                      style="bold cyan",  width=12)

        for rec in roster_events:
            ct.add_row(
                str(rec.fight_num),
                rec.fighter_name,
                _WC_SHORT.get(rec.weight_class, rec.weight_class),
                _TIER_SHORT.get(rec.tier, rec.tier),
                str(rec.age_at_event),
                rec.record_str,
                _cut_label_str(rec.labels_at_cut),
                _type_str(rec),
            )
        console.print(ct)
    else:
        console.print("[dim]  No roster events during this simulation run.[/dim]")

    # ── Weight-class moves ────────────────────────────────────────────────────
    move_log = get_move_log()
    _executed_reasons = {"title_ambition", "struggling", "cut_damage", "opportunity"}
    n_moves      = sum(1 for r in move_log if r.reason in _executed_reasons)
    n_skips      = sum(1 for r in move_log if r.is_skip)
    n_vacated    = sum(1 for r in move_log if r.title_vacated)
    n_campaigns  = sum(1 for r in move_log if r.reason == "win_and_vacate_start")
    n_dual_champ = sum(1 for r in move_log if r.reason == "win_and_vacate_title_won")
    n_hype       = sum(1 for r in move_log if r.reason == "opportunity_hype_boost")
    console.print()
    console.print(Rule(
        f"[bold]Weight-Class Moves[/bold]  "
        f"[dim]({n_moves} moved  |  {n_skips} class-skips  |  {n_vacated} titles vacated  |  "
        f"{n_campaigns} win-and-vacate campaigns  |  {n_dual_champ} dual-champion wins  |  "
        f"{n_hype} opportunity hype boosts)[/dim]"
    ))
    console.print()

    if move_log:
        mt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        mt.add_column("Sim#",    justify="right",  style="dim",        width=5)
        mt.add_column("Fighter", no_wrap=True,     style="white",      width=20)
        mt.add_column("From",                      style="dim",        width=12)
        mt.add_column("To",                        style="dim",        width=12)
        mt.add_column("Reason",                     style="bold cyan",  width=22)
        mt.add_column("Vacated",                    style="yellow",     width=7)
        mt.add_column("Note",                       style="dim",        min_width=20)

        for rec in move_log:
            mt.add_row(
                str(rec.fight_num),
                rec.fighter_name,
                _WC_SHORT.get(rec.from_wc, rec.from_wc),
                _WC_SHORT.get(rec.to_wc, rec.to_wc),
                rec.reason + (" [SKIP]" if rec.is_skip else ""),
                "YES" if rec.title_vacated else "-",
                rec.note,
            )
        console.print(mt)
    else:
        console.print("[dim]  No weight-class moves during this simulation run.[/dim]")

    # ── Current champions ─────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Current Champions[/bold]"))
    console.print()

    _fighter_index = {f.fighter_id: f for f in all_fighters}
    any_champ = False
    for wc in WEIGHT_CLASSES:
        for tier_key in ("tier3",):   # no titles at tier0; tier1/tier2/tier4 are org-split below
            champ_id = get_champion_id(wc, tier_key)
            if champ_id:
                champ = _fighter_index.get(champ_id)
                name  = champ.name if champ else f"<id {champ_id[:8]}>"
                rec   = champ.record_str if champ else "?"
                console.print(
                    f"  {_WC_SHORT[wc]} {_TIER_SHORT[tier_key]:<12}"
                    f"  [bold yellow]{name}[/bold yellow]"
                    f"  [dim]{rec}[/dim]"
                )
                any_champ = True
        # tier1: one belt per regional org (Session B2).
        for org in REGIONAL_ORG_NAMES:
            champ_id = get_champion_id(wc, "tier1", org)
            if champ_id:
                champ = _fighter_index.get(champ_id)
                name  = champ.name if champ else f"<id {champ_id[:8]}>"
                rec   = champ.record_str if champ else "?"
                console.print(
                    f"  {_WC_SHORT[wc]} {org:<28}"
                    f"  [bold yellow]{name}[/bold yellow]"
                    f"  [dim]{rec}[/dim]"
                )
                any_champ = True
        # tier2: one belt per mid-major org (Session B1).
        for org in MIDMAJOR_ORG_NAMES:
            champ_id = get_champion_id(wc, "tier2", org)
            if champ_id:
                champ = _fighter_index.get(champ_id)
                name  = champ.name if champ else f"<id {champ_id[:8]}>"
                rec   = champ.record_str if champ else "?"
                console.print(
                    f"  {_WC_SHORT[wc]} {org:<28}"
                    f"  [bold yellow]{name}[/bold yellow]"
                    f"  [dim]{rec}[/dim]"
                )
                any_champ = True
        # tier4: one belt per top-tier org.
        for org in ORG_NAMES:
            champ_id = get_champion_id(wc, "tier4", org)
            if champ_id:
                champ = _fighter_index.get(champ_id)
                name  = champ.name if champ else f"<id {champ_id[:8]}>"
                rec   = champ.record_str if champ else "?"
                console.print(
                    f"  {_WC_SHORT[wc]} {org:<12}"
                    f"  [bold yellow]{name}[/bold yellow]"
                    f"  [dim]{rec}[/dim]"
                )
                any_champ = True
    if not any_champ:
        console.print("[dim]  No champions crowned yet.[/dim]")
    console.print()

    # ── Org Distribution (Org Identity session, deliverable a) ───────────────
    # Shows the tier4 population split across the three top-tier orgs, broken
    # down by originating template, so the per-template org-assignment
    # weighting (orgs/org_registry.py::TEMPLATE_ORG_WEIGHTS) is visible.
    console.print()
    console.print(Rule("[bold]Top-Tier Org Distribution (tier4)[/bold]"))
    console.print()

    all_elite = [f for f in all_fighters if f.tier == "tier4"]
    ot = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    ot.add_column("Template", style="white", min_width=16)
    for org in ORG_NAMES:
        ot.add_column(org, justify="right", style="bold blue", width=14)
    ot.add_column("Total", justify="right", style="dim", width=7)

    templates_seen = sorted({f.template for f in all_elite})
    org_totals = {org: 0 for org in ORG_NAMES}
    for tmpl in templates_seen:
        tmpl_fighters = [f for f in all_elite if f.template == tmpl]
        row = [_TEMPLATE_SHORT.get(tmpl, tmpl)]
        for org in ORG_NAMES:
            n = sum(1 for f in tmpl_fighters if f.org == org)
            org_totals[org] += n
            row.append(str(n))
        row.append(str(len(tmpl_fighters)))
        ot.add_row(*row)
    grand_total = sum(org_totals.values())
    ot.add_row("[bold]All[/bold]", *[f"[bold]{org_totals[o]}[/bold]" for o in ORG_NAMES], f"[bold]{grand_total}[/bold]")
    console.print(ot)
    console.print()

    # ── Top-tier org roster vs per-org Elite floor (2026-07-13 rescale) ──────
    _TOPTIER_ORG_SHORT: dict[str, str] = {
        APEX_FC_NAME: "Apex", THE_LEAGUE_NAME: "League", EASTERN_GP_NAME: "EGP",
    }
    console.print("[dim]  Top-tier org roster vs per-org Elite floor (TIER4_ORG_FLOORS):[/dim]")
    for wc in WEIGHT_CLASSES:
        parts = []
        for org in ORG_NAMES:
            n = sum(1 for f in pools[wc].get("tier4", []) if f.org == org)
            floor = TIER4_ORG_FLOORS[org]
            tag = "[red]" if n < floor else "[green]"
            parts.append(f"{_TOPTIER_ORG_SHORT[org]} {tag}{n}[/{tag.strip('[]')}]/{floor}")
        console.print(f"[dim]    {_WC_SHORT[wc]}: [/dim]" + "  ".join(parts))
    console.print()

    # ── Apex FC roster cap check (Session B1, Part 3, deliverable d) ─────────
    console.print(
        f"[dim]  Apex FC roster vs soft poach cap (MAX_APEX_ROSTER={MAX_APEX_ROSTER}):[/dim]"
    )
    for wc in WEIGHT_CLASSES:
        apex_n = sum(1 for f in pools[wc].get("tier4", []) if f.org == APEX_FC_NAME)
        tag = "[yellow]" if apex_n > MAX_APEX_ROSTER else "[green]"
        console.print(f"[dim]    {_WC_SHORT[wc]}: [/dim]{tag}{apex_n}[/{tag.strip('[]')}][dim] / {MAX_APEX_ROSTER}[/dim]")
    console.print()

    # ── Mid-major Org Distribution (Session B1, deliverable a) ───────────────
    console.print()
    console.print(Rule("[bold]Mid-Major Org Distribution (tier2)[/bold]"))
    console.print()

    all_midmajor = [f for f in all_fighters if f.tier == "tier2"]

    _MIDMAJOR_ORG_SHORT: dict[str, str] = {
        "Contender Series FC": "CSF", "Titan Fighting Championship": "TFC",
        "Vanguard MMA": "VAN", "Gladius FC": "GLD",
        "African Warriors Championship": "AWC", "Gulf Combat Series": "GCS",
        "South Asia Combat League": "SACL", "Far East Circuit": "FEC",
    }
    # Eight org columns don't fit an 80-col console at the top-tier table's
    # widths -- narrower fixed widths here (org codes are all <= 4 chars).
    mmot2 = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 0))
    mmot2.add_column("Template", style="white", width=10, no_wrap=True)
    for org in MIDMAJOR_ORG_NAMES:
        mmot2.add_column(_MIDMAJOR_ORG_SHORT[org], justify="right", style="bold blue", width=4)
    mmot2.add_column("Total", justify="right", style="dim", width=5)

    mm_templates_seen = sorted({f.template for f in all_midmajor})
    mm_org_totals = {org: 0 for org in MIDMAJOR_ORG_NAMES}
    for tmpl in mm_templates_seen:
        tmpl_fighters = [f for f in all_midmajor if f.template == tmpl]
        row = [_TEMPLATE_SHORT.get(tmpl, tmpl)]
        for org in MIDMAJOR_ORG_NAMES:
            n = sum(1 for f in tmpl_fighters if f.org == org)
            mm_org_totals[org] += n
            row.append(str(n))
        row.append(str(len(tmpl_fighters)))
        mmot2.add_row(*row)
    mm_grand_total = sum(mm_org_totals.values())
    mmot2.add_row(
        "[bold]All[/bold]",
        *[f"[bold]{mm_org_totals[o]}[/bold]" for o in MIDMAJOR_ORG_NAMES],
        f"[bold]{mm_grand_total}[/bold]",
    )
    console.print(mmot2)
    console.print()

    # ── Regional Org Distribution (Session B2, deliverable a) ────────────────
    # Twelve org columns would overflow an 80-col console the way the mid-major
    # table's 8 did before that fix (see memory) -- transposed here (orgs as
    # ROWS, templates as columns) instead, which also reads more naturally for
    # "which template(s) dominate this specific org."
    console.print()
    console.print(Rule("[bold]Regional Org Distribution (tier1)[/bold]"))
    console.print()

    all_regional = [f for f in all_fighters if f.tier == "tier1"]
    _BASE_TEMPLATES = ["dagestan_sambo", "american_wrestling", "brazilian", "muay_thai", "sea_mixed"]

    rot = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    rot.add_column("Org", style="white", min_width=22, no_wrap=True)
    for tmpl in _BASE_TEMPLATES:
        rot.add_column(_TEMPLATE_SHORT[tmpl], justify="right", style="bold blue", width=6)
    rot.add_column("Other", justify="right", style="dim", width=6)
    rot.add_column("Total", justify="right", style="dim", width=6)

    reg_grand_total = 0
    for org in REGIONAL_ORG_NAMES:
        org_fighters = [f for f in all_regional if f.org == org]
        row = [org]
        base_total = 0
        for tmpl in _BASE_TEMPLATES:
            n = sum(1 for f in org_fighters if f.template == tmpl)
            base_total += n
            row.append(str(n))
        other = len(org_fighters) - base_total
        row.append(str(other))
        row.append(str(len(org_fighters)))
        reg_grand_total += len(org_fighters)
        rot.add_row(*row)
    rot.add_row("[bold]All[/bold]", *[""] * (len(_BASE_TEMPLATES) + 1), f"[bold]{reg_grand_total}[/bold]")
    console.print(rot)
    console.print()

    # ── Standings — one table per weight class ───────────────────────────────
    active = [f for f in all_fighters if f.wins + f.losses > 0]

    # Short codes for the Labels column.
    _LABEL_BADGE: dict[str, str] = {
        "Champion":   "[C]",
        "Legend":     "[L]",
        "Contender":  "cont",
        "Prospect":   "pros",
        "Gatekeeper": "gk",
        "Journeyman": "jrny",
        "Washed":     "wash",
    }
    _LABEL_PRIORITY = ["Champion", "Legend", "Contender", "Prospect", "Gatekeeper", "Journeyman", "Washed"]

    def _label_str(f: Fighter) -> str:
        if not f.labels:
            return ""
        return " ".join(_LABEL_BADGE[lb] for lb in _LABEL_PRIORITY if lb in f.labels)

    for wc in WEIGHT_CLASSES:
        wc_fighters = [f for f in active if f.weight_class == wc]
        wc_fighters.sort(
            key=lambda f: (
                TIER_LEVELS.index(f.tier),
                f.wins / (f.wins + f.losses),
                f.overall,
            ),
            reverse=True,
        )

        console.print()
        console.print(Rule(f"[bold]{wc.title()} — {_WC_SHORT[wc]}[/bold]"))
        console.print()

        table = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        table.add_column("#",       justify="right",  style="dim",     width=3)
        table.add_column("Fighter", no_wrap=True,     style="white",   min_width=20)
        table.add_column("Tier",    style="dim",                       width=10)
        table.add_column("Org",     style="bold blue",                 width=12)
        table.add_column("Style",   style="dim",                       width=10)
        table.add_column("Rec",     justify="center", style="bold",    width=6)
        table.add_column("Ovr",     justify="right",  style="cyan",    width=6)
        table.add_column("Hype",    justify="right",  style="magenta", width=6)
        table.add_column("Labels",  style="yellow",                    min_width=12)

        for rank, f in enumerate(wc_fighters, 1):
            if f.wins > f.losses:
                rec_style = "bold green"
            elif f.losses > f.wins:
                rec_style = "red"
            else:
                rec_style = "yellow"

            table.add_row(
                str(rank),
                f.name,
                _TIER_SHORT.get(f.tier, f.tier),
                f.org if f.org else "-",
                _TEMPLATE_SHORT.get(f.template, f.template),
                f"[{rec_style}]{f.record_str}[/{rec_style}]",
                f"{f.overall:+.1f}",
                f"{f.hype:+.1f}",
                _label_str(f),
            )

        console.print(table)

    # ── Division pyramid cross-tab ─────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Division Pyramid (active fighters)[/bold]"))
    console.print()

    _col_w = 10
    header = f"  {'':10}" + "".join(f"{_TIER_SHORT[t]:>{_col_w}}" for t in TIER_LEVELS) + f"{'Total':>{_col_w}}"
    console.print(f"[dim]{header}[/dim]")

    tier_totals = {t: 0 for t in TIER_LEVELS}
    grand_total = 0
    for wc in WEIGHT_CLASSES:
        wc_active = [f for f in active if f.weight_class == wc]
        counts = {t: sum(1 for f in wc_active if f.tier == t) for t in TIER_LEVELS}
        row_total = sum(counts.values())
        row = f"  {_WC_SHORT[wc]:<10}" + "".join(f"{counts[t]:>{_col_w}}" for t in TIER_LEVELS) + f"{row_total:>{_col_w}}"
        console.print(row)
        for t in TIER_LEVELS:
            tier_totals[t] += counts[t]
        grand_total += row_total

    sep = "  " + "-" * (10 + _col_w * (len(TIER_LEVELS) + 1))
    console.print(f"[dim]{sep}[/dim]")
    total_row = f"  {'All':10}" + "".join(f"{tier_totals[t]:>{_col_w}}" for t in TIER_LEVELS) + f"{grand_total:>{_col_w}}"
    console.print(f"[dim]{total_row}[/dim]")

    # ── Per-division skill staircase ───────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Avg overall per tier per division[/bold]"))
    console.print()

    header2 = f"  {'':10}" + "".join(f"{_TIER_SHORT[t]:>{_col_w}}" for t in TIER_LEVELS)
    console.print(f"[dim]{header2}[/dim]")
    for wc in WEIGHT_CLASSES:
        wc_active = [f for f in active if f.weight_class == wc]
        cells = []
        for t in TIER_LEVELS:
            cohort = [f.overall for f in wc_active if f.tier == t]
            cells.append(f"{sum(cohort)/len(cohort):>+.1f}" if cohort else f"{'--':>10}")
        row = f"  {_WC_SHORT[wc]:<10}" + "".join(f"{c:>{_col_w}}" for c in cells)
        console.print(row)

    not_active = total - len(active)
    if not_active:
        console.print(f"\n[dim]  {not_active} fighters on the roster did not compete.[/dim]")

    # ── Age distribution (retirement verification) ────────────────────────────
    console.print()
    console.print(Rule("[bold]Active Fighter Age Distribution[/bold]"))
    console.print()

    _age_brackets = [(18, 25), (26, 30), (31, 35), (36, 40), (41, 45), (46, 99)]
    _bracket_labels = ["18-25", "26-30", "31-35", "36-40", "41-45", "46+"]
    all_ages = [f.age for f in all_fighters]
    if all_ages:
        for (lo, hi), label in zip(_age_brackets, _bracket_labels):
            count = sum(1 for a in all_ages if lo <= a <= hi)
            bar_len = count * 50 // max(1, len(all_ages))
            bar = "#" * bar_len
            flag = "  ← verify no cluster here" if lo >= 46 and count > 0 else ""
            console.print(f"  {label}:  {count:>4}  {bar}{flag}")
        oldest = max(all_ages)
        mean_age = sum(all_ages) / len(all_ages)
        console.print(f"\n  Mean age: {mean_age:.1f}  |  Oldest active: {oldest}")
        if any(a >= 46 for a in all_ages):
            oldest_fighter = max((f for f in all_fighters), key=lambda f: f.age)
            console.print(
                f"  [yellow]Oldest: {oldest_fighter.name}  age {oldest_fighter.age}"
                f"  {oldest_fighter.record_str}  {' '.join(sorted(oldest_fighter.labels)) or '(no label)'}[/yellow]"
            )
    else:
        console.print("[dim]  No active fighters.[/dim]")
    console.print()

    # ── Elite Matchmaking Sub-Pool Log ───────────────────────────────────────────
    all_pairings = get_elite_pairings()
    if all_pairings:
        console.print()
        console.print(Rule("[bold]Elite Matchmaking Sub-Pool Log[/bold]"))

        # Distribution across all weight classes.
        counts: dict[str, int] = {"RR": 0, "RU": 0, "UR": 0, "UU": 0}
        for p in all_pairings:
            counts[p.pool_type] = counts.get(p.pool_type, 0) + 1
        total_elite = len(all_pairings)

        # Ranked-fighter perspective: how often did a ranked fighter face another ranked fighter?
        ranked_fights = [p for p in all_pairings if p.fighter_rank is not None]
        rr_from_ranked = sum(1 for p in ranked_fights if p.pool_type == "RR")
        ranked_wr_pct = 100 * rr_from_ranked / len(ranked_fights) if ranked_fights else 0.0

        console.print()
        console.print(
            f"  [dim]Total Elite fights: {total_elite}  |  "
            f"Ranked-fighter perspective: {rr_from_ranked}/{len(ranked_fights)} = "
            f"{ranked_wr_pct:.0f}% within ranked pool  (target ~88%)[/dim]"
        )
        console.print()
        console.print(
            f"  [dim]Distribution:  "
            f"R-vs-R {counts['RR']:>4} ({100*counts['RR']//max(1,total_elite):>2}%)  "
            f"R-vs-U {counts['RU']:>4} ({100*counts['RU']//max(1,total_elite):>2}%)  "
            f"U-vs-R {counts['UR']:>4} ({100*counts['UR']//max(1,total_elite):>2}%)  "
            f"U-vs-U {counts['UU']:>4} ({100*counts['UU']//max(1,total_elite):>2}%)[/dim]"
        )
        console.print()

        # Sample: first 35 LW Elite pairings so the proximity pattern is visible.
        lw_pairings = [p for p in all_pairings if p.weight_class == "lightweight"][:35]
        if lw_pairings:
            console.print(f"  [dim]Lightweight sample ({min(35, len(lw_pairings))} of "
                          f"{sum(1 for p in all_pairings if p.weight_class == 'lightweight')} LW Elite fights):[/dim]")
            console.print()
            console.print(f"  [dim]  {'Fighter':<22}  {'Rk':>4}  {'Opponent':<22}  {'Rk':>4}  Type[/dim]")
            console.print(f"  [dim]  {'-'*62}[/dim]")
            for p in lw_pairings:
                frk = f"#{p.fighter_rank}" if p.fighter_rank is not None else "  U"
                ork = f"#{p.opp_rank}"     if p.opp_rank     is not None else "  U"
                console.print(
                    f"  [dim]  {p.fighter_name:<22}  {frk:>4}  {p.opp_name:<22}  {ork:>4}  {p.pool_type}[/dim]"
                )
        console.print()

    # ── Elite Rankings (top-10 per weight class) ──────────────────────────────
    # Shows win-rate component, quality component, and hype component separately
    # so the calibration can be checked component-by-component.
    # Also flags Contenders who are unranked or ranked very low — a drift signal
    # between the label classifier and the ranking formula.
    gate_enforced, gate_fallback = get_gate_stats()
    console.print()
    console.print(Rule("[bold]Elite Rankings (tier4) — Per-Org Breakdown[/bold]"))
    console.print(
        f"[dim]  Updated every {RANKINGS_UPDATE_INTERVAL} fights  |  "
        f"Top {RANKINGS_SIZE} per (division, org)  |  "
        f"Score = {_W_WIN_RATE}*WR + {_W_QUALITY}*Q + {_W_HYPE}*H (rankings.py formula, reused as-is)[/dim]"
    )
    console.print(
        f"[dim]  Gate stats: {gate_enforced} times enforced (unranked-only pool applied)  |  "
        f"{gate_fallback} fallbacks (pool too small to filter)[/dim]"
    )
    console.print(
        "[dim]  Org Identity session: three separate ranked lists per weight class "
        "(one per top-tier org) replace the old single combined list.[/dim]"
    )
    console.print()

    DISPLAY_TOP_N = 10

    for wc in WEIGHT_CLASSES:
        elite_fighters_wc = pools[wc].get("tier4", [])
        n_elite = len(elite_fighters_wc)

        console.print(
            f"[bold]{wc.title()} ({_WC_SHORT[wc]})[/bold]  "
            f"[dim]{n_elite} active in Elite pool[/dim]"
        )

        for org in ORG_NAMES:
            org_fighters_wc = [f for f in elite_fighters_wc if f.org == org]
            rankings = get_org_rankings(wc, org)
            champ_id = get_champion_id(wc, "tier4", org)
            champ = next((f for f in org_fighters_wc if f.fighter_id == champ_id), None) if champ_id else None

            header = f"  [bold]{org}[/bold]  [dim]({len(org_fighters_wc)} fighters)[/dim]"
            if champ:
                header += f"  [dim]Champion:[/dim] [bold yellow]{champ.name}[/bold yellow]"
            console.print(header)

            if not rankings:
                console.print("[dim]    No rankings yet (not enough fights or pool empty).[/dim]")
                continue

            rt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
            rt.add_column("#",     justify="right",  style="dim",         width=3)
            rt.add_column("Fighter",                 style="white",       min_width=16, max_width=18)
            rt.add_column("Rec",   justify="center", style="bold",        width=5)
            rt.add_column("n",     justify="right",  style="dim",         width=3)
            rt.add_column("rw",    justify="right",  style="cyan",        width=3)
            rt.add_column("Score", justify="right",  style="bold yellow", width=5)
            rt.add_column("Label", style="yellow",                        width=6)

            for e in rankings[:DISPLAY_TOP_N]:
                f = e.fighter
                t4_w, t4_l = f.record_by_tier("tier4")
                rt.add_row(
                    str(e.rank), f.name, f"{t4_w}-{t4_l}",
                    str(e.n_elite_fights), str(e.n_ranked_wins),
                    f"{e.score:.3f}", _label_str(f),
                )
            console.print(rt)

            # ── Contender alignment check (org-scoped) ─────────────────────
            for f in org_fighters_wc:
                if CONTENDER not in f.labels:
                    continue
                entry = next((e for e in rankings if e.fighter is f), None)
                if entry is None:
                    console.print(f"[yellow]    DRIFT: {f.name} is Contender but NOT in {org}'s top-15.[/yellow]")
                elif entry.rank > 12:
                    console.print(
                        f"[yellow]    DRIFT: {f.name} is Contender but ranks #{entry.rank} "
                        f"in {org} (score={entry.score:.3f}).[/yellow]"
                    )

        console.print()

    # ── The League — Season Results (Org Identity session, deliverable c) ────
    console.print()
    console.print(Rule("[bold]The League — Season/Playoff Results[/bold]"))
    console.print(
        f"[dim]  Regular season: {LEAGUE_SEASON_LENGTH} fights/division  |  "
        f"Playoffs: top {LEAGUE_PLAYOFF_SIZE} point-scorers  |  "
        f"Finish win={FINISH_WIN_POINTS}pt, decision win={DECISION_WIN_POINTS}pt[/dim]"
    )
    console.print()

    league_history = get_league_history()
    if league_history:
        lt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        lt.add_column("Div",     style="dim",                   width=4)
        lt.add_column("Season",  justify="right", style="dim",  width=6)
        lt.add_column("Top Scorers", style="white",             min_width=28)
        lt.add_column("Final",   style="cyan",                  min_width=22)
        lt.add_column("Champion", style="bold yellow",          min_width=16)
        for rec in league_history:
            scorers_str = ", ".join(f"{n} ({p:.0f}pt)" for n, p in rec.top_scorers[:4]) or "-"
            final_str = (
                f"{rec.final_result[0]} def. {rec.final_result[1]} ({rec.final_result[2]})"
                if rec.final_result else "no final (thin pool)"
            )
            lt.add_row(
                _WC_SHORT.get(rec.weight_class, rec.weight_class),
                str(rec.season_number), scorers_str, final_str,
                rec.champion_name or "-",
            )
        console.print(lt)
    else:
        console.print("[dim]  No League seasons completed yet (increase --fights or lower LEAGUE_SEASON_LENGTH).[/dim]")
    console.print()

    # ── Cross-Org Movement (Org Identity session, Part 6) ────────────────────
    console.print()
    console.print(Rule("[bold]Cross-Org Movement[/bold]"))
    console.print()

    org_move_log = get_org_movement_log()
    n_poached  = sum(1 for r in org_move_log if r.reason == "apex_poach" and not r.was_refusal)
    n_refused  = sum(1 for r in org_move_log if r.was_refusal)
    n_departed = sum(1 for r in org_move_log if r.reason.startswith("apex_departure"))
    n_lateral  = sum(1 for r in org_move_log if r.reason == "within_tier_opportunity")
    console.print(
        f"[dim]  {n_poached} signed with Apex FC  |  {n_refused} declined Apex FC's approach  |  "
        f"{n_departed} left Apex FC  |  {n_lateral} League<->Eastern GP moves[/dim]"
    )
    console.print()

    if org_move_log:
        mvt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        mvt.add_column("Sim#",    justify="right", style="dim",   width=6)
        mvt.add_column("Fighter", no_wrap=True,    style="white", width=20)
        mvt.add_column("Div",                      style="dim",   width=4)
        mvt.add_column("From",                     style="dim",   width=14)
        mvt.add_column("To",                       style="dim",   width=14)
        mvt.add_column("Reason",                   style="bold cyan", width=24)
        mvt.add_column("Note",                     style="dim",   min_width=18)
        for rec in org_move_log:
            reason_str = rec.reason + (" [REFUSED]" if rec.was_refusal else "")
            mvt.add_row(
                str(rec.fight_num), rec.fighter_name,
                _WC_SHORT.get(rec.weight_class, rec.weight_class),
                rec.from_org, rec.to_org, reason_str, rec.note,
            )
        console.print(mvt)
    else:
        console.print("[dim]  No cross-org movement during this simulation run.[/dim]")
    console.print()

    # ── Top-org Rankings (top-15, same formula/weights as Elite) ─────────────
    console.print()
    console.print(Rule("[bold]Top-org Rankings (tier3) — Top 15[/bold]"))
    console.print(
        f"[dim]  Updated every {RANKINGS_UPDATE_INTERVAL} fights (same cadence as Elite)  |  "
        f"Top {TOPORG_LIST_SIZE} per division  |  "
        f"Score = {_W_WIN_RATE}*WR + {_W_QUALITY}*Q + {_W_HYPE}*H  (same weights as Elite)[/dim]"
    )
    console.print()

    for wc in WEIGHT_CLASSES:
        to_rankings = get_toporg_rankings(wc)
        champ_id = get_champion_id(wc, "tier3")
        champ = next((f for f in pools[wc].get("tier3", []) if f.fighter_id == champ_id), None) if champ_id else None

        header = f"[bold]{wc.title()} ({_WC_SHORT[wc]})[/bold]"
        if champ:
            header += f"  [dim]Champion:[/dim] [bold yellow]{champ.name}[/bold yellow]"
        console.print(header)

        if not to_rankings:
            console.print("[dim]  No rankings yet (not enough fights or pool empty).[/dim]")
            console.print()
            continue

        tot = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        tot.add_column("#",     justify="right",  style="dim",         width=3)
        tot.add_column("Fighter",                 style="white",       min_width=16, max_width=18)
        tot.add_column("Rec",   justify="center", style="bold",        width=5)
        tot.add_column("n",     justify="right",  style="dim",         width=3)
        tot.add_column("rw",    justify="right",  style="cyan",        width=3)
        tot.add_column("Score", justify="right",  style="bold yellow", width=5)

        for e in to_rankings:
            f = e.fighter
            w3, l3 = f.record_by_tier("tier3")
            tot.add_row(str(e.rank), f.name, f"{w3}-{l3}", str(e.n_elite_fights),
                        str(e.n_ranked_wins), f"{e.score:.3f}")
        console.print(tot)
        console.print()

    # ── Mid-major "Ones to Watch" (top-5 PER ORG, Session B1) ────────────────
    console.print()
    console.print(Rule("[bold]Mid-major \"Ones to Watch\" (tier2) — Per-Org Top 5[/bold]"))
    console.print(
        f"[dim]  Updated every {RANKINGS_UPDATE_INTERVAL} fights (same cadence as Elite)  |  "
        f"Top {MIDMAJOR_LIST_SIZE} per (division, org)  |  simplified formula, no matchmaking gate[/dim]"
    )
    console.print(
        "[dim]  Org Identity Session B1: eight mid-major orgs, each with their own "
        "top-5 list and champion.[/dim]"
    )
    console.print()

    for wc in WEIGHT_CLASSES:
        midmajor_pool_wc = pools[wc].get("tier2", [])
        console.print(f"[bold]{wc.title()} ({_WC_SHORT[wc]})[/bold]  [dim]{len(midmajor_pool_wc)} in Mid-major pool[/dim]")

        for org in MIDMAJOR_ORG_NAMES:
            org_fighters_wc = [f for f in midmajor_pool_wc if f.org == org]
            mm_rankings = get_midmajor_org_rankings(wc, org)
            champ_id = get_champion_id(wc, "tier2", org)
            champ = next((f for f in org_fighters_wc if f.fighter_id == champ_id), None) if champ_id else None

            header = f"  [bold]{org}[/bold]  [dim]({len(org_fighters_wc)} fighters)[/dim]"
            if champ:
                header += f"  [dim]Champion:[/dim] [bold yellow]{champ.name}[/bold yellow]"
            console.print(header)

            if not mm_rankings:
                console.print("[dim]    No ones-to-watch yet.[/dim]")
                continue

            mmt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
            mmt.add_column("#",     justify="right", style="dim",         width=3)
            mmt.add_column("Fighter",                style="white",       min_width=16, max_width=18)
            mmt.add_column("Rec",   justify="center", style="bold",       width=5)
            mmt.add_column("Score", justify="right",  style="bold yellow", width=5)

            for e in mm_rankings:
                f = e.fighter
                w2, l2 = f.record_by_tier("tier2")
                mmt.add_row(str(e.rank), f.name, f"{w2}-{l2}", f"{e.score:.3f}")
            console.print(mmt)
        console.print()

    # ── Scout-Notice Fast-Track Promotions ────────────────────────────────────
    scout_log = get_scout_notice_log()
    console.print()
    console.print(Rule(
        f"[bold]Scout-Notice Fast-Track Promotions[/bold]  "
        f"[dim]({len(scout_log)} fired — additive to the deterministic threshold path)[/dim]"
    ))
    console.print()
    if scout_log:
        snt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        snt.add_column("Sim#",    justify="right", style="dim",       width=6)
        snt.add_column("Fighter", no_wrap=True,    style="white",     width=20)
        snt.add_column("Div",                      style="dim",       width=3)
        snt.add_column("From",                     style="dim",       width=9)
        snt.add_column("To",                       style="dim",       width=9)
        snt.add_column("p",       justify="right", style="cyan",      width=6)
        snt.add_column("Signal",                   style="bold magenta", width=10)
        for rec in scout_log:
            signal = "CHAMPION" if rec.was_champion else (f"rank #{rec.rank}" if rec.rank is not None else "-")
            snt.add_row(
                str(rec.fight_num),
                rec.fighter_name,
                _WC_SHORT.get(rec.weight_class, rec.weight_class),
                _TIER_SHORT.get(rec.from_tier, rec.from_tier),
                _TIER_SHORT.get(rec.to_tier, rec.to_tier),
                f"{rec.p_scout_notice:.3f}",
                signal,
            )
        console.print(snt)
    else:
        console.print("[dim]  No scout-notice promotions during this simulation run.[/dim]")
    console.print()

    # -- Academy Replenishment Summary ----------------------------------------
    console.print()
    backstop_events = get_backstop_log()
    n_backstop_total = sum(e.count for e in backstop_events)
    console.print(Rule(
        f"[bold]Academy Replenishment[/bold]  "
        f"[dim]({len(backstop_events)} backstop events  |  {n_backstop_total} backstop fighters spawned)[/dim]"
    ))
    console.print()

    # Per-weight-class totals and year-by-year tier distribution
    for wc in WEIGHT_CLASSES:
        counts = get_inflow_counts(wc)
        console.print(
            f"  [bold]{wc.title()}[/bold]  "
            f"[dim]{counts['academy']} academy  |  {counts['backstop']} backstop  |  "
            f"{counts['crossover']} crossover  |  {counts['lateral']} lateral[/dim]"
        )
        history = get_replenishment_history(wc)
        if history:
            console.print(f"  [dim]  {'Year':>4}  {'Norm':>5}  {'BS':>4}  {'XO':>4}  {'Lat':>4}  "
                          f"{'Raw':>5}  {'Dev':>5}  {'HiUp':>5}  {'Elite':>5}[/dim]")
            for rec in history:
                td = rec["tier_dist"]
                console.print(
                    f"  [dim]  {rec['year']:>4}  {rec['normal']:>5}  {rec['backstop']:>4}  "
                    f"{rec.get('crossover',0):>4}  {rec.get('lateral',0):>4}  "
                    f"{td.get('raw',0):>5}  {td.get('developing',0):>5}  "
                    f"{td.get('high_upside',0):>5}  {td.get('elite',0):>5}[/dim]"
                )
        console.print()

    # Population at end of run vs floor thresholds
    console.print(f"  [dim]Final population vs floor thresholds:[/dim]")
    _tier_short = {"tier0":"Amateur","tier1":"Regional","tier2":"Mid-maj",
                   "tier3":"Top-org","tier4":"Elite"}
    header = f"  [dim]  {'':12}" + "".join(f"  {_tier_short[t]:>8}" for t in TIER_LEVELS) + "[/dim]"
    console.print(header)
    for wc in WEIGHT_CLASSES:
        row_parts = []
        for t in TIER_LEVELS:
            n   = len(pools[wc][t])
            fl  = FLOOR_THRESHOLDS[t]
            tag = "[yellow]" if n < fl else ""
            end = "[/yellow]" if n < fl else ""
            row_parts.append(f"  {tag}{n:>8}{end}")
        console.print(f"  [dim]  {_WC_SHORT[wc]:<12}[/dim]" + "".join(row_parts))
    floor_row = "".join(f"  {'(>='+str(FLOOR_THRESHOLDS[t])+')'!s:>9}" for t in TIER_LEVELS)
    console.print(f"  [dim]  {'floor':12}{floor_row}[/dim]")
    console.print()


def main() -> None:
    p = argparse.ArgumentParser(description="MMA fight-night simulator (tier-constrained)")
    p.add_argument("--fights",    type=int, default=2000, help="number of bouts (default 2000)")
    p.add_argument("--fighters",  type=float, default=1.0, metavar="SCALE",
                   help="population scale factor (default 1.0 -> 100/70/35/25/15 per tier = 245 total)")
    p.add_argument("--seed",      type=int, default=42,  help="random seed (default 42)")
    p.add_argument("--debug",     action="store_true",
                   help="print per-fight: overalls, P(A wins), outcome -- skips panels")
    args = p.parse_args()
    run(args.fights, args.fighters, args.seed, debug=args.debug)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
