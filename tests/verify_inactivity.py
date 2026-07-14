"""
verify_inactivity.py -- Confirm population-relative inactivity primitive.

Runs a short sim then prints the full inactivity_percentile breakdown for the
LW Elite pool. Two things to confirm:

  1. Dates advance across a fighter's career (monotonic, not frozen).
  2. The primitive does NOT flag the majority of the pool as inactive --
     the specific failure mode of the prior absolute-threshold attempt where
     ~100% of a thin pool was flagged because every fighter has long absolute
     gaps as a structural property of pool size.

Run: python verify_inactivity.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from engine.fight import simulate_fight
from career.tiers import generate_all_tiers
from matchmaking import pick_opponent, apply_tier_transitions, reset_gate_stats
from career.labels import reset_title_registry
from title import reset_title_scheduling
from career.cuts import reset_cut_registry
from career.rankings import reset_rankings
from sim_calendar import (
    reset_sim_clock, advance_sim_clock, get_sim_day,
    inactivity_percentile, INACTIVITY_PERCENTILE_THRESHOLD,
    _last_stamped_day,
)

N_FIGHTS = 1200
SEED = 42
WC = "lightweight"

random.seed(SEED)
reset_title_registry()
reset_title_scheduling()
reset_cut_registry()
reset_rankings()
reset_gate_stats()
reset_sim_clock()

pools = generate_all_tiers(scale=1.0)
all_fighters = [f for wc_pools in pools.values() for tp in wc_pools.values() for f in tp]

# Lean sim loop: fights + tier transitions + clock only.
# No labels / retirement / cuts -- verification doesn't need them.
for _ in range(N_FIGHTS):
    if not all_fighters:
        break
    a = random.choice(all_fighters)
    try:
        b = pick_opponent(a, pools)
    except IndexError:
        continue
    if b is None:
        continue
    current_day = get_sim_day()
    winner, loser = simulate_fight(a, b, org="league", sim_day=current_day)
    for f in (winner, loser):
        apply_tier_transitions(f, pools)
    advance_sim_clock()

elite_pool = pools[WC]["tier4"]
final_day = get_sim_day()

print()
print(f"=== Inactivity Percentile -- {WC.title()} Elite ({len(elite_pool)} fighters) ===")
print(f"    Sim: {N_FIGHTS} fights | Day 0 -> Day {final_day} ({final_day / 365.25:.1f} sim-years)")
print(
    f"    Threshold: >= {INACTIVITY_PERCENTILE_THRESHOLD:.0f}th percentile"
    f" = relatively inactive (top {100 - INACTIVITY_PERCENTILE_THRESHOLD:.0f}% longest gaps)"
)
print()
print(f"  {'Fighter':<26}  {'Fights':>6}  {'Last':>5}  {'Gap':>5}  {'Pct':>5}  {'Flag':>5}")
print("  " + "-" * 62)

# Collect results first so we can sort by gap.
rows: list[tuple] = []
for f in elite_pool:
    result = inactivity_percentile(f, elite_pool)
    last = _last_stamped_day(f)
    rows.append((f, result, last))

# Sort by gap descending (most inactive at top) to make the distribution clear.
rows.sort(key=lambda x: (x[1].gap_days if x[1] is not None else -1), reverse=True)

n_flagged = 0
n_with_data = 0

for f, result, last_day in rows:
    n_fights_total = len(f.fight_history)
    if result is None:
        last_s = str(last_day) if last_day is not None else "--"
        print(
            f"  {f.name:<26}  {n_fights_total:>6}  {last_s:>5}  {'--':>5}  {'N/A':>5}  {'(none)':>5}"
        )
        continue

    n_with_data += 1
    if result.is_relatively_inactive:
        n_flagged += 1

    flag_s = "YES" if result.is_relatively_inactive else "no"
    print(
        f"  {f.name:<26}  {n_fights_total:>6}"
        f"  {last_day:>5}  {result.gap_days:>5}"
        f"  {result.percentile:>4.0f}%  {flag_s:>5}"
    )

print()

# ── Summary ───────────────────────────────────────────────────────────────────
no_data = len(elite_pool) - n_with_data
if n_with_data == 0:
    print("  WARNING: no fighters with valid fight history -- run more fights.")
else:
    pct_flagged = 100 * n_flagged / n_with_data
    print(f"  Flagged as relatively inactive: {n_flagged} / {n_with_data}"
          f" fighters with data  ({pct_flagged:.0f}%)")
    print(f"  Design target: ~{100 - INACTIVITY_PERCENTILE_THRESHOLD:.0f}% flagged"
          f" (threshold = {INACTIVITY_PERCENTILE_THRESHOLD:.0f}th percentile)")
    if no_data:
        print(f"  Excluded (no stamped history): {no_data} fighter(s)")
    print()

    if pct_flagged <= 50:
        print("  PASS -- majority of pool is NOT flagged as inactive.")
    else:
        print(
            f"  FAIL -- {pct_flagged:.0f}% flagged; majority of pool is inactive."
            "  The threshold or fight count may need adjustment."
        )

print()
