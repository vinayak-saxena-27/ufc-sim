"""
Fight-night simulator with rich terminal output.

Usage:
  python sim.py                        # 10 fights, 10 fighters per template
  python sim.py --fights 20            # more fights
  python sim.py --fighters 15          # larger roster
  python sim.py --seed 7               # different random seed
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

from fight import simulate_fight
from templates import generate_population

console = Console()


def _template_short(template: str) -> str:
    return {
        "dagestan_sambo":     "Dagestan",
        "american_wrestling": "Amer.Wres",
        "brazilian":          "Brazilian",
        "muay_thai":          "Muay Thai",
        "sea_mixed":          "SEA Mixed",
    }.get(template, template)


def run(n_fights: int, per_template: int, seed: int) -> None:
    random.seed(seed)
    fighters = generate_population(per_template)
    total = len(fighters)

    console.print()
    console.print(
        f"[bold cyan]MMA Fight Night[/bold cyan]  "
        f"[dim]seed={seed}  |  {total} fighters  |  {n_fights} bouts[/dim]"
    )
    console.print()

    # ── Fight card ────────────────────────────────────────────────────────────
    for i in range(n_fights):
        a, b = random.sample(fighters, 2)
        winner, loser = simulate_fight(a, b, org="fight_night", tier="regional")
        result = winner.fight_history[-1]

        is_upset = (winner is b and a.overall - b.overall > 8) or \
                   (winner is a and b.overall - a.overall > 8)

        body = Text()
        body.append(f"  {a.name}", style="bold white")
        body.append(f"  ({_template_short(a.template)}, {a.overall:+.1f})\n", style="dim")
        body.append("     vs\n", style="dim")
        body.append(f"  {b.name}", style="bold white")
        body.append(f"  ({_template_short(b.template)}, {b.overall:+.1f})\n\n", style="dim")
        body.append(f"  WINNER  {winner.name}", style="bold green")
        body.append(f"  by {result.method}\n", style="green")
        body.append(
            f"  {winner.name} {winner.record_str}   {loser.name} {loser.record_str}",
            style="dim",
        )
        if is_upset:
            body.append("   [UPSET]", style="bold yellow")

        console.print(Panel(body, title=f"[dim]Bout {i + 1} of {n_fights}[/dim]", expand=False))

    # ── Standings ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Standings - fighters who competed tonight[/bold]"))
    console.print()

    active = [f for f in fighters if f.wins + f.losses > 0]
    active.sort(key=lambda f: (f.wins, -f.losses, f.overall), reverse=True)

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    table.add_column("#",        justify="right",  style="dim",     width=3)
    table.add_column("Fighter",  no_wrap=True,     style="white",   min_width=22)
    table.add_column("Style",    style="dim",      width=10)
    table.add_column("Rec",      justify="center", style="bold",    width=6)
    table.add_column("Ovr",      justify="right",  style="cyan",    width=6)
    table.add_column("Hype",     justify="right",  style="magenta", width=6)

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
            _template_short(f.template),
            f"[{rec_style}]{f.record_str}[/{rec_style}]",
            f"{f.overall:+.1f}",
            f"{f.hype:+.1f}",
        )

    console.print(table)
    console.print(
        f"[dim]  {total - len(active)} fighters on the roster did not compete tonight.[/dim]\n"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="MMA fight-night simulator")
    p.add_argument("--fights",   type=int, default=10,  help="number of bouts (default 10)")
    p.add_argument("--fighters", type=int, default=10,  metavar="N",
                   help="fighters per template (default 10, so 50 total)")
    p.add_argument("--seed",     type=int, default=42,  help="random seed (default 42)")
    args = p.parse_args()
    run(args.fights, args.fighters, args.seed)


if __name__ == "__main__":
    main()
