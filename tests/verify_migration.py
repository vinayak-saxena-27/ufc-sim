"""
verify_migration.py -- Verify the Part 3 global-time migration.

CHECK 1 (age parity):
  Active and inactive fighters starting at the same age end up at the same age
  after the same elapsed sim time.  Proves advance_all_ages() sweeps the whole
  roster regardless of fight-count.

CHECK 2 (inactive retirement eligibility):
  A past-prime fighter who stops competing becomes retirement-eligible from
  elapsed time alone -- no new fights needed.  Proves maybe_retire_inactive()
  finds and evaluates them.

CHECK 3 (spot-check):
  Run a 600-fight sim through the full post-migration pipeline and confirm:
  some fighters have labels, some were retired/cut, Elite rankings are populated,
  no fighter older than 55 remains active (retirement keeps the age ceiling).

Run: python verify_migration.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from engine.fight import simulate_fight
from career.fighter import FightResult
from career.tiers import generate_all_tiers
from matchmaking import pick_opponent, apply_tier_transitions, reset_gate_stats
from career.labels import maybe_update_labels, reset_title_registry, update_labels
from title import reset_title_scheduling, maybe_run_title_fight
from career.age import (
    reset_age_advancement, advance_all_ages,
    SIM_DAYS_PER_YEAR, _PRIME_END,
)
from career.cuts import maybe_evaluate_cut, get_cut_log, reset_cut_registry
from career.retirement import (
    maybe_evaluate_retirement, maybe_retire_inactive, reset_retirement_scanning,
    RETIRE_INACTIVE_GAP_DAYS, RETIRE_INACTIVE_SCAN_DAYS,
)
from career.rankings import update_rankings, get_rankings, reset_rankings, RANKINGS_UPDATE_INTERVAL
from sim_calendar import (
    reset_sim_clock, advance_sim_clock, get_sim_day,
    SIM_DAYS_PER_FIGHT, days_since,
)

_PASS = "PASS"
_FAIL = "FAIL"


def _reset_all() -> None:
    reset_sim_clock()
    reset_age_advancement()
    reset_retirement_scanning()
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_gate_stats()


# ── CHECK 1: Age parity ───────────────────────────────────────────────────────

def check_age_parity() -> bool:
    """Active and inactive fighters age at the same rate under global time."""
    print("CHECK 1: Age parity (active vs inactive fighter)")

    random.seed(1)
    _reset_all()

    pools = generate_all_tiers(scale=0.5)
    elite_lw = pools["lightweight"]["tier4"]
    if len(elite_lw) < 2:
        print("  SKIP: Elite LW pool has fewer than 2 fighters")
        return True

    f_active   = elite_lw[0]
    f_inactive = elite_lw[1]

    # Force identical starting ages so the comparison is unambiguous.
    f_active.age   = 25
    f_inactive.age = 25
    start_age = 25

    all_fighters = [f_active, f_inactive]

    # Advance until we've crossed the SIM_DAYS_PER_YEAR threshold.
    # +1 because 365 // 2 = 182 steps gives day 364 (one step shy of 365).
    # We need at least 183 steps (day 366) to trigger the first age advance.
    steps = SIM_DAYS_PER_YEAR // SIM_DAYS_PER_FIGHT + 1
    fight_stamped = False
    midpoint = steps // 2

    for step in range(steps):
        current_day = get_sim_day()
        if not fight_stamped and step == midpoint:
            # Stamp a real fight on f_active so it has valid fight history.
            # The result doesn't matter for this test -- only the age.
            simulate_fight(f_active, f_inactive, org="test", sim_day=current_day)
            fight_stamped = True
        advance_sim_clock()
        advance_all_ages(all_fighters)

    expected = start_age + 1
    ok = (f_active.age == expected and f_inactive.age == expected)

    print(f"  Elapsed:     {get_sim_day()} sim-days  ({get_sim_day() / 365.25:.2f} sim-years)")
    print(f"  f_active   age={f_active.age}   fights={len(f_active.fight_history)}  (expected {expected})")
    print(f"  f_inactive age={f_inactive.age}  fights={len(f_inactive.fight_history)}  (expected {expected})")

    if ok:
        print(f"  {_PASS}  Both aged from {start_age} to {expected} regardless of activity.")
    else:
        print(f"  {_FAIL}  Mismatch: active={f_active.age}, inactive={f_inactive.age}, expected={expected}")
    return ok


# ── CHECK 2: Inactive fighter retirement eligibility ─────────────────────────

def check_inactive_retirement() -> bool:
    """A fighter who stops competing can retire from elapsed time alone."""
    print("\nCHECK 2: Inactive retirement eligibility from elapsed time alone")

    # Use multiple seeds; retirement is probabilistic (Path 2 at age 42 ~= 72%).
    # We try up to MAX_SCAN_ATTEMPTS scan windows; at 72% prob the expected
    # number of scans before first retirement is ~1.4, so 20 attempts is ample.
    MAX_SCAN_ATTEMPTS = 20

    for seed in (42, 7, 13, 99, 123):
        random.seed(seed)
        _reset_all()

        pools = generate_all_tiers(scale=0.5)
        elite_lw = pools["lightweight"]["tier4"]
        if not elite_lw:
            continue

        fighter = elite_lw[0]
        fighter.age = 42   # deep decline: Path 2 prob ~0.72
        all_fighters = list(elite_lw)

        # Give the fighter exactly one fight at day 0, then no more.
        # _last_stamped_day will return 0, so after RETIRE_INACTIVE_GAP_DAYS
        # have passed the fighter is eligible.
        dummy = elite_lw[1] if len(elite_lw) > 1 else fighter
        simulate_fight(fighter, dummy, org="test", sim_day=0)

        # Advance past the inactivity gap so the scan can trigger.
        target_day = RETIRE_INACTIVE_GAP_DAYS + RETIRE_INACTIVE_SCAN_DAYS
        steps = target_day // SIM_DAYS_PER_FIGHT + 1
        for _ in range(steps):
            advance_sim_clock()
            advance_all_ages(all_fighters)

        retired: list = []
        attempt = 0
        for attempt in range(1, MAX_SCAN_ATTEMPTS + 1):
            retired = maybe_retire_inactive(all_fighters, pools, fight_num=attempt)
            if retired:
                break
            # Open the next scan window.
            scan_steps = RETIRE_INACTIVE_SCAN_DAYS // SIM_DAYS_PER_FIGHT + 1
            for _ in range(scan_steps):
                advance_sim_clock()
                advance_all_ages(all_fighters)

        if retired:
            gap = days_since(0)
            print(f"  Fighter: {fighter.name}  age={fighter.age}  gap_at_retirement={gap} days")
            print(f"  {_PASS}  Retired from inactivity alone on attempt {attempt} (seed={seed}).")
            return True

    print(f"  {_FAIL}  No retirement fired across all seeds after {MAX_SCAN_ATTEMPTS} attempts each.")
    print("  (Check RETIRE_INACTIVE_GAP_DAYS, RETIRE_INACTIVE_SCAN_DAYS, and the")
    print("   retirement probability at age 42.)")
    return False


# ── CHECK 3: Spot-check labels / cuts / rankings ──────────────────────────────

def check_spot() -> bool:
    """600-fight mini-sim: confirm labels, cuts/retirements, and rankings survive migration."""
    print("\nCHECK 3: Spot-check — 600-fight mini-sim through full pipeline")

    random.seed(42)
    _reset_all()

    N_FIGHTS = 600
    pools = generate_all_tiers(scale=1.0)
    all_fighters = [
        f for wc_pools in pools.values() for tp in wc_pools.values() for f in tp
    ]

    for i in range(N_FIGHTS):
        if not all_fighters:
            break
        a = random.choice(all_fighters)
        try:
            b = pick_opponent(a, pools)
        except IndexError:
            continue

        fight_wc   = a.weight_class
        fight_tier = a.tier
        current_day = get_sim_day()

        winner, loser = simulate_fight(a, b, org="league", sim_day=current_day)

        fighters_to_remove: list = []
        for fighter in (winner, loser):
            apply_tier_transitions(fighter, pools)
            maybe_update_labels(fighter)
            removed = maybe_evaluate_retirement(fighter, pools, fight_num=i + 1)
            if not removed:
                removed = maybe_evaluate_cut(fighter, pools, fight_num=i + 1)
            if removed:
                fighters_to_remove.append(fighter)

        for rf in fighters_to_remove:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        maybe_run_title_fight(fight_wc, fight_tier, pools, org="league",
                              fight_num=i + 1, all_fighters=all_fighters)

        advance_sim_clock()
        advance_all_ages(all_fighters)

        newly_retired = maybe_retire_inactive(all_fighters, pools, fight_num=i + 1)
        for rf in newly_retired:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        if (i + 1) % RANKINGS_UPDATE_INTERVAL == 0:
            update_rankings(pools)

    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)

    cut_log   = get_cut_log()
    labeled   = sum(1 for f in all_fighters if f.labels)
    n_cuts    = sum(1 for r in cut_log if r.reason == "cut")
    n_retired = sum(1 for r in cut_log if r.reason in {"retired", "retired_on_top"})
    ages      = [f.age for f in all_fighters]
    oldest    = max(ages) if ages else 0
    rk_lw     = get_rankings("lightweight")

    print(f"  Sim day reached:        {get_sim_day()}  ({get_sim_day() / 365.25:.1f} sim-years)")
    print(f"  Fighters remaining:     {len(all_fighters)}")
    print(f"  Fighters with labels:   {labeled}")
    print(f"  Cuts:                   {n_cuts}")
    print(f"  Retirements:            {n_retired}")
    print(f"  Oldest active fighter:  {oldest}")
    print(f"  LW Elite rankings:      {'yes (' + str(len(rk_lw)) + ' entries)' if rk_lw else 'EMPTY'}")

    age_breakdown = {}
    for bracket, lo, hi in [("18-25", 18, 25), ("26-30", 26, 30), ("31-35", 31, 35),
                             ("36-40", 36, 40), ("41-45", 41, 45), ("46+", 46, 999)]:
        age_breakdown[bracket] = sum(1 for a in ages if lo <= a <= hi)
    print(f"  Age distribution:       " + "  ".join(f"{k}={v}" for k, v in age_breakdown.items()))

    ok = (
        labeled >= 5
        and (n_cuts + n_retired) >= 1
        and rk_lw
        and oldest <= 55
    )

    if ok:
        print(f"  {_PASS}  Labels present, cuts/retirements present, rankings populated, oldest <= 55.")
    else:
        reasons = []
        if labeled < 5:                  reasons.append(f"too few labeled fighters ({labeled} < 5)")
        if (n_cuts + n_retired) < 1:     reasons.append("no cuts or retirements")
        if not rk_lw:                    reasons.append("LW Elite rankings empty")
        if oldest > 55:                  reasons.append(f"oldest active = {oldest} (> 55 ceiling)")
        print(f"  {_FAIL}: " + "; ".join(reasons))

    return ok


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=== Part 3 Global-Time Migration Verification ===")
    print()

    results = [
        check_age_parity(),
        check_inactive_retirement(),
        check_spot(),
    ]

    n_pass = sum(results)
    n_fail = len(results) - n_pass
    print()
    print(f"{'=' * 49}")
    print(f"Results: {n_pass}/{len(results)} PASS")
    if n_fail == 0:
        print("All checks passed. Migration verified.")
    else:
        print(f"{n_fail} check(s) FAILED -- see output above.")
    print()
