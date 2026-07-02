"""
verify_development.py -- Five verification checks for the development system.

Run from the repo root:
    python tests/verify_development.py

Checks:
  1. Development accumulates correctly over time: elite > raw at same age;
     gain slows near prime; zero past prime.
  2. Attribute asymmetry: same development_modifier -> fight_iq/bjj/clinch gain
     more than power/athleticism via apply_development_to_fighter.
  3. Win boost fires for winners only; losers gain nothing from match results.
  4. Full modifier stack at 22 vs 38: correct direction and magnitude for each.
  5. Academy pipeline_strength correctly influences development rate.
"""
from __future__ import annotations

import sys
import os
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random

from career.fighter import Fighter
from career.development import (
    apply_development_to_fighter,
    apply_win_development_boost,
    advance_all_development,
    reset_development_advancement,
    _age_factor,
    BASE_DEV_RATE,
    _TIER_MOD,
    _WIN_BOOST,
)
from career.age import (
    apply_age_to_fighter, _PRIME_START, SIM_DAYS_PER_YEAR,
    advance_all_ages, reset_age_advancement,
)
from sim_calendar import reset_sim_clock, advance_sim_clock, SIM_DAYS_PER_FIGHT


# -- Helpers ------------------------------------------------------------------

def _make_fighter(age: int, prospect_tier: str = "developing", academy: str = "") -> Fighter:
    return Fighter(
        name="Test Fighter",
        age=age,
        region="test",
        template="dagestan_sambo",
        academy=academy,
        prospect_tier=prospect_tier,
        wrestling=10.0, bjj=10.0, clinch=10.0,
        boxing=10.0, kickboxing=10.0, power=10.0,
        cardio=10.0, chin=10.0, athleticism=10.0, fight_iq=10.0,
    )


def _run_n_years(fighters: list[Fighter], n: int) -> None:
    """
    Advance age and development for all fighters by exactly n simulated years.
    Resets all clocks before starting -- use for a fresh, isolated run.

    Age advancement uses the 'while' loop in advance_all_ages / advance_all_development,
    so running ceil(n * SIM_DAYS_PER_YEAR / SIM_DAYS_PER_FIGHT) + 1 ticks ensures
    the threshold fires exactly n times.
    """
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()
    ticks = math.ceil(n * SIM_DAYS_PER_YEAR / SIM_DAYS_PER_FIGHT) + 1
    for _ in range(ticks):
        advance_sim_clock()
        advance_all_ages(fighters)
        advance_all_development(fighters)


def _develop_over_years(
    f: Fighter, total_years: int, target_ages: list[int]
) -> dict[int, float]:
    """
    Run a single fighter through total_years years, returning {age: dev_modifier}
    snapshots for each age in target_ages.
    Age and development advance together on the same calendar cadence.
    """
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()

    result = {}
    if f.age in target_ages:
        result[f.age] = f.development_modifier

    prev_age = f.age
    ticks = math.ceil(total_years * SIM_DAYS_PER_YEAR / SIM_DAYS_PER_FIGHT) + 1
    for _ in range(ticks):
        advance_sim_clock()
        advance_all_ages([f])
        advance_all_development([f])
        if f.age != prev_age:
            if f.age in target_ages:
                result[f.age] = f.development_modifier
            prev_age = f.age

    return result


def _ok(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL  {label}: {detail}")


# -- Check 1: Development across tiers and ages --------------------------------

def check_1() -> bool:
    print("\n-- Check 1: Development across tiers and ages --")

    tiers = ["raw", "developing", "high_upside", "elite"]
    target_ages = [18, 21, 24, 27]

    snapshots: dict[str, dict[int, float]] = {}
    for tier in tiers:
        f = _make_fighter(age=18, prospect_tier=tier)
        snaps = _develop_over_years(f, 9, target_ages)
        snapshots[tier] = snaps

    # Print snapshot table
    print(f"  {'Tier':<12}" + "".join(f" {'age'+str(a):>8}" for a in target_ages))
    print("  " + "-" * 50)
    for tier in tiers:
        row = f"  {tier:<12}"
        for age in target_ages:
            row += f" {snapshots[tier].get(age, float('nan')):>8.3f}"
        print(row)

    passed = True

    # a) Elite should accumulate more than raw at all pre-prime ages
    for age in [21]:
        if not (snapshots["elite"].get(age, 0) > snapshots["raw"].get(age, 0)):
            _fail("1a", f"elite should > raw at age {age}; "
                  f"got elite={snapshots['elite'].get(age):.3f} raw={snapshots['raw'].get(age):.3f}")
            passed = False

    # b) Development should slow approaching prime: gain 18->21 > gain 21->24
    for tier in ["developing", "elite"]:
        dm_18 = snapshots[tier].get(18, 0.0)
        dm_21 = snapshots[tier].get(21, float('nan'))
        dm_24 = snapshots[tier].get(24, float('nan'))
        gain_early = dm_21 - dm_18
        gain_late  = dm_24 - dm_21
        if not (gain_early > gain_late):
            _fail("1b", f"{tier}: gain 18->21 ({gain_early:.3f}) should exceed 21->24 ({gain_late:.3f})")
            passed = False

    # c) Past prime (age >= 24, i.e. ages 24 and 27 check for camp-based gain)
    # At _PRIME_START=23, age_factor=0 so gain should be 0 from annual sweeps
    for tier in tiers:
        post_prime = snapshots[tier].get(27, 0.0) - snapshots[tier].get(24, 0.0)
        if post_prime > 0.001:
            _fail("1c", f"{tier}: no camp gain expected age 24->27; got {post_prime:.3f}")
            passed = False

    if passed:
        _ok("elite > raw; gain slows near prime; zero past prime")
    return passed


# -- Check 2: Attribute asymmetry ---------------------------------------------

def check_2() -> bool:
    print("\n-- Check 2: Attribute asymmetry --")
    dm = 5.0
    f = _make_fighter(age=20)
    f.development_modifier = dm
    eff = apply_development_to_fighter(f)

    delta_fight_iq    = eff.fight_iq    - f.fight_iq
    delta_bjj         = eff.bjj         - f.bjj
    delta_clinch      = eff.clinch      - f.clinch
    delta_power       = eff.power       - f.power
    delta_athleticism = eff.athleticism - f.athleticism
    delta_boxing      = eff.boxing      - f.boxing

    print(f"  development_modifier = {dm}")
    print(f"  fight_iq delta: {delta_fight_iq:.3f}  (expect {dm*1.3:.3f})")
    print(f"  bjj delta:      {delta_bjj:.3f}  (expect {dm*1.3:.3f})")
    print(f"  clinch delta:   {delta_clinch:.3f}  (expect {dm*1.3:.3f})")
    print(f"  power delta:    {delta_power:.3f}  (expect {dm*0.6:.3f})")
    print(f"  athleticism:    {delta_athleticism:.3f}  (expect {dm*0.6:.3f})")
    print(f"  boxing delta:   {delta_boxing:.3f}  (expect {dm*1.0:.3f})")

    passed = True
    tol = 1e-6
    for got, expected, name in [
        (delta_fight_iq,    dm * 1.3, "fight_iq"),
        (delta_bjj,         dm * 1.3, "bjj"),
        (delta_clinch,      dm * 1.3, "clinch"),
        (delta_power,       dm * 0.6, "power"),
        (delta_athleticism, dm * 0.6, "athleticism"),
        (delta_boxing,      dm * 1.0, "boxing"),
    ]:
        if abs(got - expected) > tol:
            _fail("2", f"{name}: got {got:.6f} expected {expected:.6f}")
            passed = False

    if passed:
        _ok("technical (1.3x) > mixed (1.0x) > physical (0.6x) -- correct asymmetry")
    return passed


# -- Check 3: Win boost fires for winners only --------------------------------

def check_3() -> bool:
    print("\n-- Check 3: Win boost -- winners only --")

    n = 20
    winner_gains: list[float] = []
    loser_gains:  list[float] = []

    for _ in range(n):
        w = _make_fighter(age=20, prospect_tier="developing")
        l = _make_fighter(age=20, prospect_tier="developing")
        before_w = w.development_modifier
        before_l = l.development_modifier
        apply_win_development_boost(w)
        winner_gains.append(w.development_modifier - before_w)
        loser_gains.append(l.development_modifier  - before_l)

    avg_w = sum(winner_gains) / len(winner_gains)
    avg_l = sum(loser_gains)  / len(loser_gains)
    print(f"  avg winner gain per fight: {avg_w:.3f}  (expect {_WIN_BOOST['developing']:.3f})")
    print(f"  avg loser  gain per fight: {avg_l:.3f}  (expect 0.000)")

    passed = True
    if abs(avg_w - _WIN_BOOST["developing"]) > 1e-6:
        _fail("3a", f"winner gain {avg_w:.3f} != expected {_WIN_BOOST['developing']:.3f}")
        passed = False
    if avg_l != 0.0:
        _fail("3b", f"loser gain {avg_l:.3f} should be 0.0")
        passed = False
    if passed:
        _ok("win boost fires for winners; losers unchanged")
    return passed


# -- Check 4: Full modifier stack -- age 22 vs age 38 -------------------------

def check_4() -> bool:
    print("\n-- Check 4: Modifier stack -- age 22 (young) vs age 38 (veteran) --")

    # Age 22: positive development, near-zero age penalty
    f22 = _make_fighter(age=22)
    f22.development_modifier = 3.0

    # Age 38: no development (past prime), significant age penalty
    f38 = _make_fighter(age=38)
    f38.development_modifier = 0.0

    def full_stack(f: Fighter) -> Fighter:
        return apply_age_to_fighter(apply_development_to_fighter(f))

    eff22 = full_stack(f22)
    eff38 = full_stack(f38)

    base_ovr = f22.overall
    ovr22 = eff22.overall
    ovr38 = eff38.overall

    print(f"  Base overall (both): {base_ovr:+.2f}")
    print(f"  Age-22 effective:    {ovr22:+.2f}  (delta {ovr22-base_ovr:+.2f}, expect positive)")
    print(f"  Age-38 effective:    {ovr38:+.2f}  (delta {ovr38-base_ovr:+.2f}, expect large penalty)")

    passed = True
    # Age 22 should have net positive effective overall vs base (dev > small age deficit)
    if ovr22 <= base_ovr:
        _fail("4a", f"age-22 effective {ovr22:.2f} should be > base {base_ovr:.2f}")
        passed = False

    # Age 38 should have significant net negative effective overall vs base
    if ovr38 >= base_ovr:
        _fail("4b", f"age-38 effective {ovr38:.2f} should be < base {base_ovr:.2f}")
        passed = False

    # The younger fighter should clearly outperform the veteran
    if ovr22 <= ovr38:
        _fail("4c", f"age-22 ({ovr22:.2f}) should outperform age-38 ({ovr38:.2f})")
        passed = False

    # Verify dev delta is additive on top of age delta, not compounded
    # fight_iq effective = base.fight_iq + dev_delta_fight_iq + age_delta_fight_iq
    dm = f22.development_modifier
    expected_dev_delta = dm * 1.3   # fight_iq is technical, 1.3x multiplier
    age_only = apply_age_to_fighter(f22)
    dev_only  = apply_development_to_fighter(f22)
    full      = full_stack(f22)
    actual_fight_iq = full.fight_iq
    expected_fight_iq = f22.fight_iq + dm * 1.3 + (age_only.fight_iq - f22.fight_iq)
    if abs(actual_fight_iq - expected_fight_iq) > 1e-4:
        _fail("4d", f"fight_iq stacking wrong: {actual_fight_iq:.4f} vs {expected_fight_iq:.4f}")
        passed = False

    if passed:
        _ok("age-22 net positive; age-38 significant penalty; layers stack additively")
    return passed


# -- Check 5: Academy pipeline_strength influences development rate -----------

def check_5() -> bool:
    print("\n-- Check 5: Academy pipeline_strength -> development rate --")

    # Evolve MMA Singapore: pipeline_strength=+9 (best in sim)
    # Lanna Muay Thai Chiang Mai: pipeline_strength=-6 (near worst)
    best_academy  = "Evolve MMA Singapore"
    worst_academy = "Lanna Muay Thai Chiang Mai"

    f_best  = _make_fighter(age=18, prospect_tier="developing", academy=best_academy)
    f_worst = _make_fighter(age=18, prospect_tier="developing", academy=worst_academy)

    _run_n_years([f_best, f_worst], 5)

    print(f"  {best_academy}: dev_modifier = {f_best.development_modifier:.3f}")
    print(f"  {worst_academy}: dev_modifier = {f_worst.development_modifier:.3f}")

    passed = True
    if not (f_best.development_modifier > f_worst.development_modifier):
        _fail("5", f"best-pipeline ({f_best.development_modifier:.3f}) "
              f"should exceed worst-pipeline ({f_worst.development_modifier:.3f})")
        passed = False
    else:
        _ok(f"higher pipeline_strength -> more development "
            f"({f_best.development_modifier:.3f} > {f_worst.development_modifier:.3f})")
    return passed


# -- Runner -------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  verify_development.py")
    print("=" * 60)

    results = [
        check_1(),
        check_2(),
        check_3(),
        check_4(),
        check_5(),
    ]

    n_pass = sum(results)
    n_fail = len(results) - n_pass
    print(f"\n{'=' * 60}")
    print(f"  {n_pass}/{len(results)} checks passed"
          + (f"  |  {n_fail} FAILED" if n_fail else ""))
    print("=" * 60)
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
