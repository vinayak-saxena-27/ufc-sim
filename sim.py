"""
Fight-night simulator with rich terminal output (Session 2: tier-constrained matchmaking).

Usage:
  python sim.py                          # 300 fights, 4 fighters per template per tier
  python sim.py --fights 500             # more fights
  python sim.py --fighters 6             # larger roster (6 * 5 * 5 = 150 fighters)
  python sim.py --seed 7                 # different random seed
  python sim.py --debug                  # per-fight probability diagnostic, skip panels
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
from tiers import TIER_CONFIG, TIER_LEVELS, WEIGHT_CLASSES, generate_all_tiers
from matchmaking import pick_opponent, apply_tier_transitions

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
        console.print("[dim]  #   Fighter A               OvrA   Fighter B               OvrB   P(A)  Result[/dim]")
        console.print("[dim]" + "-" * 82 + "[/dim]")

    # ── Fight loop ────────────────────────────────────────────────────────────
    for i in range(n_fights):
        a = random.choice(all_fighters)
        b = pick_opponent(a, pools)

        p_a_wins = _true_prob(a.overall, b.overall)
        winner, loser = simulate_fight(a, b, org="league")
        result = winner.fight_history[-1]

        # Apply tier transitions for both fighters
        transitions: dict[str, str] = {}
        for fighter in (winner, loser):
            new_tier = apply_tier_transitions(fighter, pools)
            if new_tier:
                transitions[fighter.name] = new_tier

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

        console.print(Panel(body, title=f"[dim]Bout {i + 1} of {n_fights}[/dim]", expand=False))

    if debug:
        console.print("[dim]  * = underdog won[/dim]\n")

    # ── Standings ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Standings - fighters who competed[/bold]"))
    console.print()

    active = [f for f in all_fighters if f.wins + f.losses > 0]
    active.sort(
        key=lambda f: (
            TIER_LEVELS.index(f.tier),
            f.wins / (f.wins + f.losses),
            f.overall,
        ),
        reverse=True,
    )

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    table.add_column("#",       justify="right",  style="dim",     width=3)
    table.add_column("Fighter", no_wrap=True,     style="white",   min_width=20)
    table.add_column("Div",     style="cyan",      width=4)
    table.add_column("Tier",    style="dim",       width=10)
    table.add_column("Style",   style="dim",       width=10)
    table.add_column("Rec",     justify="center", style="bold",    width=6)
    table.add_column("Ovr",     justify="right",  style="cyan",    width=6)
    table.add_column("Hype",    justify="right",  style="magenta", width=6)

    for rank, f in enumerate(active, 1):
        if f.wins > f.losses:
            rec_style = "bold green"
        elif f.losses > f.wins:
            rec_style = "red"
        else:
            rec_style = "yellow"

        table.add_row(
            str(rank),
            f.name,
            _WC_SHORT.get(f.weight_class, f.weight_class),
            _TIER_SHORT.get(f.tier, f.tier),
            _TEMPLATE_SHORT.get(f.template, f.template),
            f"[{rec_style}]{f.record_str}[/{rec_style}]",
            f"{f.overall:+.1f}",
            f"{f.hype:+.1f}",
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
    console.print()


def main() -> None:
    p = argparse.ArgumentParser(description="MMA fight-night simulator (tier-constrained)")
    p.add_argument("--fights",    type=int, default=300, help="number of bouts (default 300)")
    p.add_argument("--fighters",  type=float, default=1.0, metavar="SCALE",
                   help="population scale factor (default 1.0 -> 100/70/35/25/15 per tier = 245 total)")
    p.add_argument("--seed",      type=int, default=42,  help="random seed (default 42)")
    p.add_argument("--debug",     action="store_true",
                   help="print per-fight: overalls, P(A wins), outcome -- skips panels")
    args = p.parse_args()
    run(args.fights, args.fighters, args.seed, debug=args.debug)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
