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

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from fight import simulate_fight, SCALE
from tiers import TIER_LEVELS, WEIGHT_CLASSES, generate_all_tiers
from matchmaking import (
    pick_opponent, pick_scheduled_elite_a, apply_tier_transitions,
    reset_gate_stats, get_gate_stats,
    reset_elite_pairings, get_elite_pairings, ElitePairingRecord,
    ELITE_FIGHT_INTERVAL,
)
from labels import maybe_update_labels, reset_title_registry, update_labels, get_champion_id, CONTENDER
from title import reset_title_scheduling, maybe_run_title_fight, get_title_history, TITLE_FIGHT_INTERVAL
from age import advance_all_ages, reset_age_advancement
from development import advance_all_development, apply_win_development_boost, reset_development_advancement
from cuts import maybe_evaluate_cut, get_cut_log, reset_cut_registry
from retirement import maybe_evaluate_retirement, maybe_retire_inactive, reset_retirement_scanning
from rankings import (
    update_rankings, get_rankings, reset_rankings,
    RANKINGS_UPDATE_INTERVAL, RANKINGS_SIZE,
    _W_WIN_RATE, _W_QUALITY, _W_HYPE,
)
from sim_calendar import reset_sim_clock, advance_sim_clock, get_sim_day, SIM_DAYS_PER_FIGHT
from replenishment import (
    initialize_replenishment, run_replenishment,
    get_replenishment_history, get_backstop_log, get_event_log, get_total_generated,
    get_inflow_counts,
    FLOOR_THRESHOLDS,
)

console = Console()

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


def run(n_fights: int, scale: float, seed: int, debug: bool = False) -> None:
    random.seed(seed)
    pools = generate_all_tiers(scale=scale)
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_gate_stats()
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()
    initialize_replenishment()
    reset_retirement_scanning()
    reset_elite_pairings()
    all_fighters = [
        f
        for wc_pools in pools.values()
        for tier_pool in wc_pools.values()
        for f in tier_pool
    ]
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

    # ── Fight loop ────────────────────────────────────────────────────────────
    for i in range(n_fights):
        if not all_fighters:
            break
        a = random.choice(all_fighters)
        try:
            b = pick_opponent(a, pools)
        except IndexError:
            # Division pool exhausted — skip this iteration rather than crash.
            continue

        # Capture tier+wc before transitions — title scheduling uses the pool
        # where the fight took place, not where A ends up afterward.
        fight_wc   = a.weight_class
        fight_tier = a.tier
        current_day = get_sim_day()

        p_a_wins = _true_prob(a.overall, b.overall)
        winner, loser = simulate_fight(a, b, org="league", sim_day=current_day)
        result = winner.fight_history[-1]

        # Win-triggered development boost — fires on the BASE fighter object (not the
        # effective copy used inside fight resolution), so the gain is durable.
        apply_win_development_boost(winner)

        # Apply tier transitions, label updates,
        # retirement (checked first), then cut (skips already-retired fighters).
        # Age advancement is now global (advance_all_ages below) -- not per-fight.
        transitions: dict[str, str] = {}
        fighters_to_remove: list = []
        for fighter in (winner, loser):
            new_tier = apply_tier_transitions(fighter, pools)
            if new_tier:
                transitions[fighter.name] = new_tier
            maybe_update_labels(fighter)
            removed = maybe_evaluate_retirement(fighter, pools, fight_num=i + 1)
            if not removed:
                removed = maybe_evaluate_cut(fighter, pools, fight_num=i + 1)
            if removed:
                fighters_to_remove.append(fighter)

        for rf in fighters_to_remove:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        # Check whether a title fight is due in this pool.
        maybe_run_title_fight(fight_wc, fight_tier, pools, org="league", fight_num=i + 1, all_fighters=all_fighters)

        # Advance the global clock after all activity for this iteration is stamped.
        advance_sim_clock()

        # Age all fighters once per SIM_DAYS_PER_YEAR — inactive fighters age too.
        advance_all_ages(all_fighters)
        # Development sweeps the same cadence; called after age so age_factor reflects
        # the just-incremented age (fighters who turned 23 this year get age_factor=0).
        advance_all_development(all_fighters)

        # Academy replenishment: generate prospects from academies whose schedule
        # has elapsed, and run the quarterly population floor backstop.
        run_replenishment(pools, all_fighters)

        # Biannual scan: retire fighters inactive for >= RETIRE_INACTIVE_GAP_DAYS.
        newly_retired = maybe_retire_inactive(all_fighters, pools, fight_num=i + 1)
        for rf in newly_retired:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        # Recompute Elite rankings every RANKINGS_UPDATE_INTERVAL sim fights.
        if (i + 1) % RANKINGS_UPDATE_INTERVAL == 0:
            update_rankings(pools)

        # ── Scheduled Elite fight (option b density fix) ─────────────────────
        # Injected additively every ELITE_FIGHT_INTERVAL main fights.  Non-Elite
        # fighters are unaffected; Elite pool replenishment rate is unchanged.
        if ELITE_FIGHT_INTERVAL > 0 and (i + 1) % ELITE_FIGHT_INTERVAL == 0:
            _ae = pick_scheduled_elite_a(pools)
            if _ae is not None:
                try:
                    _be = pick_opponent(_ae, pools)
                except IndexError:
                    pass
                else:
                    _ewc, _etier, _eday = _ae.weight_class, _ae.tier, get_sim_day()
                    _ew, _el = simulate_fight(_ae, _be, org="exhibition", sim_day=_eday)
                    apply_win_development_boost(_ew)
                    _erm: list = []
                    for _ef in (_ew, _el):
                        apply_tier_transitions(_ef, pools)
                        maybe_update_labels(_ef)
                        _er = maybe_evaluate_retirement(_ef, pools, fight_num=i + 1)
                        if not _er:
                            _er = maybe_evaluate_cut(_ef, pools, fight_num=i + 1)
                        if _er:
                            _erm.append(_ef)
                    for _erf in _erm:
                        all_fighters[:] = [f for f in all_fighters if f is not _erf]
                    maybe_run_title_fight(_ewc, _etier, pools, org="exhibition",
                                         fight_num=i + 1, all_fighters=all_fighters)
                    advance_sim_clock()
                    advance_all_ages(all_fighters)
                    advance_all_development(all_fighters)

        if debug:
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
            continue

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
        for name, new_tier in transitions.items():
            tier_label = _TIER_SHORT.get(new_tier, new_tier)
            old_idx = TIER_LEVELS.index(new_tier) - 1
            direction = "PROMOTED to" if TIER_LEVELS.index(new_tier) > old_idx else "DEMOTED to"
            body.append(f"\n  [{direction} {tier_label}]", style="bold magenta")

        console.print(Panel(body, title=f"[dim]Bout {i + 1} of {n_fights} | Day {current_day}[/dim]", expand=False))

    if debug:
        console.print("[dim]  * = underdog won[/dim]\n")

    # ── Force final label recompute and rankings update ───────────────────────
    # maybe_update_labels fires only on multiples of LABEL_UPDATE_INTERVAL;
    # fighters whose last fight didn't land on a multiple would have stale labels.
    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)   # final authoritative snapshot used by all output below

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

    # ── Current champions ─────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Current Champions[/bold]"))
    console.print()

    _fighter_index = {f.fighter_id: f for f in all_fighters}
    any_champ = False
    for wc in WEIGHT_CLASSES:
        for tier_key in TIER_LEVELS[1:]:   # skip tier0 (no titles)
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
    if not any_champ:
        console.print("[dim]  No champions crowned yet.[/dim]")
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
    console.print(Rule("[bold]Elite Rankings (tier4) — Component Breakdown[/bold]"))
    console.print(
        f"[dim]  Updated every {RANKINGS_UPDATE_INTERVAL} fights  |  "
        f"Top {RANKINGS_SIZE} per division  |  "
        f"Score = {_W_WIN_RATE}*WR + {_W_QUALITY}*Q + {_W_HYPE}*H[/dim]"
    )
    console.print(
        f"[dim]  Gate stats: {gate_enforced} times enforced (unranked-only pool applied)  |  "
        f"{gate_fallback} fallbacks (pool too small to filter)[/dim]"
    )
    console.print()

    DISPLAY_TOP_N = 10

    for wc in WEIGHT_CLASSES:
        rankings = get_rankings(wc)
        elite_fighters_wc = pools[wc].get("tier4", [])
        n_elite = len(elite_fighters_wc)

        console.print(
            f"[bold]{wc.title()} ({_WC_SHORT[wc]})[/bold]  "
            f"[dim]{n_elite} active in Elite pool[/dim]"
        )

        if not rankings:
            console.print("[dim]  No rankings yet (not enough fights or pool empty).[/dim]")
            console.print()
            continue

        rt = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        rt.add_column("#",     justify="right",  style="dim",         width=3)
        rt.add_column("Fighter",                 style="white",       min_width=16, max_width=18)
        rt.add_column("Rec",   justify="center", style="bold",        width=5)
        rt.add_column("n",     justify="right",  style="dim",         width=3)
        rt.add_column("rw",    justify="right",  style="cyan",        width=3)
        rt.add_column("Score", justify="right",  style="bold yellow", width=5)
        rt.add_column("WR*W",  justify="right",  style="cyan",        width=5)
        rt.add_column("Q*W",   justify="right",  style="green",       width=5)
        rt.add_column("H*W",   justify="right",  style="dim",         width=5)
        rt.add_column("Label", style="yellow",                        width=6)

        for e in rankings[:DISPLAY_TOP_N]:
            f = e.fighter
            t4_w, t4_l = f.record_by_tier("tier4")
            rec = f"{t4_w}-{t4_l}"
            label_s = _label_str(f)
            rt.add_row(
                str(e.rank),
                f.name,
                rec,
                str(e.n_elite_fights),
                str(e.n_ranked_wins),
                f"{e.score:.3f}",
                f"{_W_WIN_RATE * e.win_rate_component:.3f}",
                f"{_W_QUALITY  * e.quality_component:.3f}",
                f"{_W_HYPE     * e.hype_component:.3f}",
                label_s,
            )
        console.print(rt)

        # ── Contender alignment check ─────────────────────────────────────────
        # Flag any labeled Contender not in the top-15 or ranked very low (>12).
        # Drift is expected occasionally (two independent systems), but should be visible.
        for f in elite_fighters_wc:
            if CONTENDER not in f.labels:
                continue
            entry = next((e for e in rankings if e.fighter is f), None)
            if entry is None:
                console.print(
                    f"[yellow]  DRIFT: {f.name} is Contender but NOT in top-15.[/yellow]"
                )
            elif entry.rank > 12:
                console.print(
                    f"[yellow]  DRIFT: {f.name} is Contender but ranks #{entry.rank} "
                    f"(score={entry.score:.3f}).[/yellow]"
                )

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
