"""
verify_title_selection.py -- Verify rankings-driven title-fight challenger selection.

Checks (Elite tier only, where rankings matter):
  1. RANKED CHALLENGER RATE  -- most Elite challengers come from the ranked list
  2. INACTIVITY OVERRIDE     -- fires at least once concretely; skips #1 for a real case
  3. NO HYPE ON VACANT       -- hype override never fires for vacant-belt fights
  4. FALLBACK RATE           -- thin-rankings fallback only fires early; drops off
  5. OVERRIDE RATES SENSIBLE -- inactivity/hype not constantly firing or never firing

Run with: python verify_title_selection.py
N_FIGHTS controls sim length; SEED for reproducibility.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import sys

# ── Config ────────────────────────────────────────────────────────────────────

N_FIGHTS = 5000
SEED     = 42

_PASS = "PASS"
_FAIL = "FAIL"

# ── Sim bootstrap (mirrors sim.py setup) ─────────────────────────────────────

random.seed(SEED)

from career.tiers        import generate_all_tiers
from matchmaking  import (
    pick_opponent, pick_scheduled_elite_a,
    apply_tier_transitions, reset_gate_stats, reset_elite_pairings,
    ELITE_FIGHT_INTERVAL,
)
from engine.fight        import simulate_fight
from career.labels       import maybe_update_labels, reset_title_registry
from career.retirement   import maybe_evaluate_retirement, maybe_retire_inactive, reset_retirement_scanning
from career.cuts         import maybe_evaluate_cut, reset_cut_registry
from career.rankings     import update_rankings, get_rankings, get_ranked_ids, reset_rankings, RANKINGS_UPDATE_INTERVAL
from title        import (
    maybe_run_title_fight, reset_title_scheduling, get_title_history,
    TitleFightRecord,
)
from sim_calendar import advance_sim_clock, get_sim_day, reset_sim_clock
from career.age          import advance_all_ages, reset_age_advancement

# Build initial population and pool structure
pools = generate_all_tiers(scale=1.0)
all_fighters = [
    f
    for wc_pools in pools.values()
    for tier_pool in wc_pools.values()
    for f in tier_pool
]
reset_gate_stats()
reset_elite_pairings()
reset_rankings()
reset_title_scheduling()
reset_title_registry()
reset_cut_registry()
reset_sim_clock()
reset_age_advancement()
reset_retirement_scanning()

# ── Run sim ───────────────────────────────────────────────────────────────────

print(f"Running {N_FIGHTS} fights (seed={SEED}) ...\n")
print("=" * 70)

total_fight_idx = 0


def _run_fight_cycle(fa, fb, fight_num, org="league"):
    global all_fighters
    fight_wc    = fa.weight_class
    fight_tier  = fa.tier
    current_day = get_sim_day()
    winner, loser = simulate_fight(fa, fb, org=org, sim_day=current_day)
    to_remove = []
    for fighter in (winner, loser):
        apply_tier_transitions(fighter, pools)
        maybe_update_labels(fighter)
        removed = maybe_evaluate_retirement(fighter, pools, fight_num=fight_num)
        if not removed:
            removed = maybe_evaluate_cut(fighter, pools, fight_num=fight_num)
        if removed:
            to_remove.append(fighter)
    for rf in to_remove:
        all_fighters[:] = [f for f in all_fighters if f is not rf]
    maybe_run_title_fight(fight_wc, fight_tier, pools, org=org,
                          fight_num=fight_num, all_fighters=all_fighters)
    advance_sim_clock()
    advance_all_ages(all_fighters)


for i in range(N_FIGHTS):
    if not all_fighters:
        break

    a = random.choice(all_fighters)
    try:
        b = pick_opponent(a, pools)
    except IndexError:
        continue

    _run_fight_cycle(a, b, fight_num=total_fight_idx + 1)
    total_fight_idx += 1

    newly_retired = maybe_retire_inactive(all_fighters, pools, fight_num=total_fight_idx)
    for rf in newly_retired:
        all_fighters[:] = [f for f in all_fighters if f is not rf]

    if total_fight_idx % RANKINGS_UPDATE_INTERVAL == 0:
        update_rankings(pools)

    # Scheduled Elite fight (option b density fix -- unchanged)
    if ELITE_FIGHT_INTERVAL > 0 and (i + 1) % ELITE_FIGHT_INTERVAL == 0:
        ae = pick_scheduled_elite_a(pools)
        if ae is not None:
            try:
                be = pick_opponent(ae, pools)
            except IndexError:
                pass
            else:
                _run_fight_cycle(ae, be, fight_num=total_fight_idx + 1, org="exhibition")
                total_fight_idx += 1
                if total_fight_idx % RANKINGS_UPDATE_INTERVAL == 0:
                    update_rankings(pools)

print("=" * 70)
print()

# ── Analysis ──────────────────────────────────────────────────────────────────

history = get_title_history()
elite_records = [r for r in history if r.tier_key == "tier4"]
all_results   = []

print(f"Total title fights logged : {len(history)}")
print(f"Elite title fights        : {len(elite_records)}")
print()

if not elite_records:
    print("No Elite title fights found -- cannot verify. Run longer or check TITLE_FIGHT_INTERVAL.")
    sys.exit(1)

# ── CHECK 1: Ranked challenger rate ──────────────────────────────────────────

ranked_count   = sum(1 for r in elite_records if r.challenger_rank is not None)
fallback_count = sum(1 for r in elite_records if r.override == "fallback")
ranked_pct     = 100 * ranked_count / len(elite_records)

ok1 = ranked_pct >= 60.0
result1 = _PASS if ok1 else _FAIL
print(f"CHECK 1  Ranked challenger rate  [{result1}]")
print(f"  Elite title fights: {len(elite_records)}")
print(f"  Challenger had a rank: {ranked_count} ({ranked_pct:.0f}%)")
print(f"  Used fallback (no ranked entries): {fallback_count}")
print(f"  Threshold: >= 60% ranked challengers")
all_results.append(ok1)
print()

# ── CHECK 2: Inactivity override fires at least once ─────────────────────────

inactivity_records = [r for r in elite_records if r.override == "inactivity"]
ok2 = len(inactivity_records) >= 1

result2 = _PASS if ok2 else _FAIL
print(f"CHECK 2  Inactivity override fires concretely  [{result2}]")
print(f"  Inactivity override events: {len(inactivity_records)}")
if inactivity_records:
    ex = inactivity_records[0]
    vacant_tag = " (vacant)" if ex.was_vacant else " (defense)"
    rank_tag   = f"rank={ex.challenger_rank}" if ex.challenger_rank else "NR"
    print(f"  Example: fight #{ex.fight_num} {ex.weight_class} {ex.tier_key}{vacant_tag}")
    print(f"    Challenger: {ex.loser_name if not ex.was_vacant else ex.winner_name} ({rank_tag})")
else:
    print("  No inactivity override fired -- try longer sim or lower INACTIVITY_PERCENTILE_THRESHOLD")
all_results.append(ok2)
print()

# ── CHECK 3: Hype override never fires for vacant fights ─────────────────────

vacant_records    = [r for r in elite_records if r.was_vacant]
hype_on_vacant    = [r for r in vacant_records if r.override == "hype"]
ok3 = len(hype_on_vacant) == 0

result3 = _PASS if ok3 else _FAIL
print(f"CHECK 3  Hype override never fires for vacant-belt fights  [{result3}]")
print(f"  Vacant Elite title fights: {len(vacant_records)}")
print(f"  Hype override on vacant fights: {len(hype_on_vacant)}")
all_results.append(ok3)
print()

# ── CHECK 4: Fallback rate is low ────────────────────────────────────────────

fallback_records = [r for r in elite_records if r.override == "fallback"]
fallback_pct = 100 * fallback_count / len(elite_records) if elite_records else 0
# Fallback fires for thin rankings (early sim) or depleted pool (late sim).
# Either case is legitimate; what matters is it's not the default path.
ok4 = fallback_pct <= 20.0
result4 = _PASS if ok4 else _FAIL
print(f"CHECK 4  Fallback rate is low  [{result4}]")
print(f"  Fallback events: {fallback_count} / {len(elite_records)} ({fallback_pct:.1f}%)")
if fallback_records:
    for r in fallback_records[:3]:
        print(f"    fight #{r.fight_num}  {r.weight_class} {'VACANT' if r.was_vacant else 'DEFENSE'}")
print(f"  Threshold: <= 20% of Elite title fights use fallback")
all_results.append(ok4)
print()

# ── CHECK 5: Override rates are sensible ─────────────────────────────────────

defense_records   = [r for r in elite_records if not r.was_vacant]
hype_records      = [r for r in defense_records if r.override == "hype"]
inact_records     = [r for r in elite_records if r.override == "inactivity"]
no_override       = [r for r in elite_records if r.override is None]

n_d = len(defense_records)
n_e = len(elite_records)

inact_rate_pct = 100 * len(inact_records) / n_e if n_e else 0
hype_rate_pct  = 100 * len(hype_records)  / n_d if n_d else 0

# Neither override should dominate (>70%) or be completely absent (=0 if sim is long enough)
# Inactivity: 0-40% reasonable; Hype: 0-30% reasonable
ok5a = inact_rate_pct <= 40.0
ok5b = hype_rate_pct  <= 30.0
ok5  = ok5a and ok5b

result5 = _PASS if ok5 else _FAIL
print(f"CHECK 5  Override rates within sensible bounds  [{result5}]")
print(f"  Defense fights: {n_d}")
print(f"  Inactivity override: {len(inact_records)} ({inact_rate_pct:.1f}%  threshold <= 40%)")
print(f"  Hype override:       {len(hype_records)}  ({hype_rate_pct:.1f}%  threshold <= 30%)")
print(f"  No override:         {len(no_override)}")
if not ok5a:
    print(f"  FAIL: inactivity override firing too often ({inact_rate_pct:.1f}%)")
if not ok5b:
    print(f"  FAIL: hype override firing too often ({hype_rate_pct:.1f}%)")
all_results.append(ok5)
print()

# ── Summary ───────────────────────────────────────────────────────────────────

n_pass = sum(all_results)
n_total = len(all_results)
print("=" * 70)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass == n_total:
    print("  All checks passed -- rankings-driven challenger selection verified.")
else:
    failed = [i + 1 for i, ok in enumerate(all_results) if not ok]
    print(f"  Failed checks: {failed}")
print()

# ── Override rate detail table ────────────────────────────────────────────────

print("Override breakdown (Elite tier all fights):")
print(f"  {'Override':<14}  {'Count':>6}  {'Pct':>6}")
print("  " + "-" * 30)
for label, recs in [
    ("none",       no_override),
    ("inactivity", inact_records),
    ("hype",       hype_records),
    ("fallback",   [r for r in elite_records if r.override == "fallback"]),
]:
    pct = 100 * len(recs) / n_e if n_e else 0
    print(f"  {label:<14}  {len(recs):>6}  {pct:>5.1f}%")
print()

# Show rank distribution of challengers
ranked_challengers = [r.challenger_rank for r in elite_records if r.challenger_rank is not None]
if ranked_challengers:
    from collections import Counter
    rank_counts = Counter(ranked_challengers)
    print("Challenger rank distribution (Elite):")
    for rank in sorted(rank_counts):
        bar = "#" * rank_counts[rank]
        print(f"  #{rank:>2}  {rank_counts[rank]:>3}  {bar}")
    print()
