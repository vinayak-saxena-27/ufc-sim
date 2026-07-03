"""
verify_style_mixing.py -- Five verification checks for voluntary style-mixing.

Run from the repo root:
    python tests/verify_style_mixing.py

Checks:
  1. Trait generation: loose positive fight_iq correlation, visible academy
     nudges, meaningful spread.
  2. Islam test: high-style_flexibility wrestler spends meaningful time
     STANDING (25-40%) vs a pure striker; a specialist wrestler doesn't.
  3. Absolute-skill gate: bad-boxing wrestler stays on the ground regardless
     of high style_flexibility.
  4. Matchup-modulation: mixing appetite falls off as opponent quality rises.
  5. Development feedback: non-primary-phase exposure accelerates
     development_modifier growth vs a specialist who never leaves home.
"""
from __future__ import annotations

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from career.fighter import Fighter
from career.style_mixing import generate_style_flexibility, TEMPLATE_STYLE_FLEX_NUDGE
from career.templates import generate_fighter, TEMPLATES
from career.development import apply_phase_development_feedback, _WIN_BOOST
from engine.phase_engine import Phase, simulate_round, ROUND_SECONDS, primary_phase, effective_mixing


def _ok(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL  {label}: {detail}")


def _f(name: str, *, tier: str = "tier2", **attrs) -> Fighter:
    return Fighter(name=name, age=28, region="test", template="test", tier=tier, **attrs)


def avg_splits(fa: Fighter, fb: Fighter, n: int) -> dict[str, float]:
    totals: dict[str, float] = {p.value: 0.0 for p in Phase}
    for _ in range(n):
        tl = simulate_round(fa, fb)
        for ph, secs in tl.time_in_phase.items():
            totals[ph] += secs / ROUND_SECONDS
    return {k: v / n for k, v in totals.items()}


# -- Check 1: Trait generation --------------------------------------------------

def check_1() -> bool:
    print("\n-- Check 1: Trait generation --")
    random.seed(11)

    N = 600   # stays under the smallest per-template name-pool capacity (muay_thai: 32*22=704)
    by_template: dict[str, list[Fighter]] = {t: [] for t in TEMPLATES}
    for _ in range(N):
        for t in TEMPLATES:
            by_template[t].append(generate_fighter(t))

    all_fighters = [f for fs in by_template.values() for f in fs]
    fight_iqs = [f.fight_iq for f in all_fighters]
    flexes    = [f.style_flexibility for f in all_fighters]

    # (a) Loose positive correlation with fight_iq.
    mean_iq, mean_fx = statistics.mean(fight_iqs), statistics.mean(flexes)
    cov = sum((iq - mean_iq) * (fx - mean_fx) for iq, fx in zip(fight_iqs, flexes)) / len(all_fighters)
    sd_iq, sd_fx = statistics.pstdev(fight_iqs), statistics.pstdev(flexes)
    corr = cov / (sd_iq * sd_fx)
    print(f"  fight_iq vs style_flexibility correlation: r={corr:.3f}  (want 0.15-0.85: visible, not deterministic)")
    passed = True
    if not (0.15 <= corr <= 0.85):
        _fail("1a", f"correlation r={corr:.3f} out of expected loose-but-visible range")
        passed = False

    # (b) Academy/regional-template nudges visible in the expected direction.
    template_means = {t: statistics.mean(f.style_flexibility for f in fs) for t, fs in by_template.items()}
    print("  Per-template mean style_flexibility:")
    for t, m in template_means.items():
        print(f"    {t:<20} {m:>+6.2f}  (nudge={TEMPLATE_STYLE_FLEX_NUDGE.get(t, 0.0):+.1f})")

    if not (template_means["dagestan_sambo"] < template_means["brazilian"]):
        _fail("1b", "Dagestan mean should be clearly below Brazilian mean")
        passed = False
    if not (template_means["american_wrestling"] < template_means["sea_mixed"]):
        _fail("1b", "American Wrestling mean should be clearly below SEA Mixed mean")
        passed = False

    # (c) Meaningful spread -- full range from specialists to diverse stylists populated.
    lo, hi = min(flexes), max(flexes)
    print(f"  Range: [{lo:.1f}, {hi:.1f}]  (want a wide spread spanning negative and positive)")
    if not (lo < -10.0 and hi > 10.0):
        _fail("1c", f"spread too narrow: [{lo:.1f}, {hi:.1f}]")
        passed = False

    if passed:
        _ok(f"correlation r={corr:.3f}, academy nudges visible, spread=[{lo:.1f},{hi:.1f}]")
    return passed


# -- Check 2: Islam test ---------------------------------------------------------

def check_2() -> bool:
    print("\n-- Check 2: Islam test (diverse-stylist wrestler vs pure striker) --")
    random.seed(22)
    N = 500

    wrestler_mix = _f("Diverse-Wrestler", wrestling=30, boxing=10, style_flexibility=15, tier="tier4")
    wrestler_spec = _f("Specialist-Wrestler", wrestling=30, boxing=10, style_flexibility=-10, tier="tier4")
    striker = _f("Pure-Striker", boxing=25, wrestling=-15, tier="tier4")

    sp_mix  = avg_splits(wrestler_mix, striker, N)
    sp_spec = avg_splits(wrestler_spec, striker, N)

    print(f"  Diverse (style_flexibility=+15): STANDING={sp_mix['STANDING']:.1%}"
          f"  CLINCH={sp_mix['CLINCH']:.1%}  GROUND={sp_mix['GROUND']:.1%}")
    print(f"  Specialist (style_flexibility=-10): STANDING={sp_spec['STANDING']:.1%}"
          f"  CLINCH={sp_spec['CLINCH']:.1%}  GROUND={sp_spec['GROUND']:.1%}")

    passed = True
    if not (0.25 <= sp_mix["STANDING"] <= 0.40):
        _fail("2a", f"diverse-stylist STANDING={sp_mix['STANDING']:.1%}, want 25-40%")
        passed = False
    if not (sp_mix["STANDING"] > sp_spec["STANDING"] + 0.10):
        _fail("2b", f"diverse STANDING ({sp_mix['STANDING']:.1%}) should clearly exceed "
              f"specialist STANDING ({sp_spec['STANDING']:.1%})")
        passed = False

    if passed:
        _ok(f"diverse-stylist STANDING={sp_mix['STANDING']:.1%} in range; "
            f"specialist STANDING={sp_spec['STANDING']:.1%} much lower")
    return passed


# -- Check 3: Absolute-skill gate ------------------------------------------------

def check_3() -> bool:
    print("\n-- Check 3: Absolute-skill gate (bad boxing overrides high style_flexibility) --")
    random.seed(33)
    N = 500

    bad_boxer_wrestler = _f("BadBoxer-Wrestler", wrestling=30, boxing=-20, style_flexibility=20, tier="tier4")
    striker = _f("Pure-Striker", boxing=25, wrestling=-15, tier="tier4")

    sp = avg_splits(bad_boxer_wrestler, striker, N)
    print(f"  Wrestling=+30 boxing=-20 style_flexibility=+20: STANDING={sp['STANDING']:.1%}"
          f"  CLINCH={sp['CLINCH']:.1%}  GROUND={sp['GROUND']:.1%}")

    passed = True
    if sp["STANDING"] >= 0.25:
        _fail("3", f"STANDING={sp['STANDING']:.1%} -- absolute-skill gate should suppress "
              f"standing time despite high style_flexibility")
        passed = False

    if passed:
        _ok(f"STANDING={sp['STANDING']:.1%} stays low -- gate overrides personality trait")
    return passed


# -- Check 4: Matchup modulation -------------------------------------------------

def check_4() -> bool:
    print("\n-- Check 4: Matchup modulation (mixing falls off vs tougher opposition) --")

    diverse = _f("Diverse-Stylist", wrestling=30, boxing=15, clinch=10, style_flexibility=20, tier="tier4")
    inferior = _f("Inferior-Opp", wrestling=-20, boxing=-15, tier="tier4")
    peer     = _f("Peer-Opp", wrestling=25, boxing=15, tier="tier4")
    superior = _f("Superior-Opp", wrestling=35, boxing=30, tier="tier4")

    em_inf  = effective_mixing(diverse, inferior)
    em_peer = effective_mixing(diverse, peer)
    em_sup  = effective_mixing(diverse, superior)

    print(f"  effective_mixing vs inferior opponent: {em_inf:+.2f}")
    print(f"  effective_mixing vs peer opponent:     {em_peer:+.2f}")
    print(f"  effective_mixing vs superior opponent: {em_sup:+.2f}")

    passed = True
    if not (em_inf > em_peer > em_sup):
        _fail("4", f"expected monotonic decrease inferior > peer > superior; "
              f"got {em_inf:.2f}, {em_peer:.2f}, {em_sup:.2f}")
        passed = False

    if passed:
        _ok(f"mixing appetite decreases as opponent quality rises "
            f"({em_inf:+.2f} > {em_peer:+.2f} > {em_sup:+.2f})")
    return passed


# -- Check 5: Development feedback -----------------------------------------------

def check_5() -> bool:
    print("\n-- Check 5: Development feedback (non-primary-phase exposure -> faster development) --")

    def _fight_result(time_standing: float, time_clinch: float, time_ground: float):
        from career.fighter import FightResult
        return FightResult(
            opponent_name="Opp", outcome="win", method="decision", org="test", tier="tier2",
            time_standing=time_standing, time_clinch=time_clinch, time_ground=time_ground,
        )

    # A wrestler (primary=GROUND) who spent 40% of the fight STANDING (mixing).
    mixer = _f("Mixer", wrestling=25, athleticism=5, prospect_tier="developing")
    mixer.fight_history.append(_fight_result(time_standing=600.0, time_clinch=0.0, time_ground=900.0))

    # A wrestler who never left the ground (specialist behavior / lopsided fight).
    specialist = _f("GroundSpecialist", wrestling=25, athleticism=5, prospect_tier="developing")
    specialist.fight_history.append(_fight_result(time_standing=0.0, time_clinch=0.0, time_ground=900.0))

    before_mixer = mixer.development_modifier
    before_spec  = specialist.development_modifier
    apply_phase_development_feedback(mixer)
    apply_phase_development_feedback(specialist)
    gain_mixer = mixer.development_modifier - before_mixer
    gain_spec  = specialist.development_modifier - before_spec

    print(f"  Mixer (40% STANDING, primary=GROUND): development_modifier gain = {gain_mixer:.3f}")
    print(f"  Specialist (0% non-primary):          development_modifier gain = {gain_spec:.3f}")
    print(f"  Win-boost reference for 'developing' tier: {_WIN_BOOST['developing']:.3f}")

    passed = True
    if not (gain_mixer > gain_spec):
        _fail("5a", f"mixer gain ({gain_mixer:.3f}) should exceed specialist gain ({gain_spec:.3f})")
        passed = False
    if gain_spec != 0.0:
        _fail("5b", f"specialist should get zero phase-feedback gain, got {gain_spec:.3f}")
        passed = False
    if not (0.0 < gain_mixer < _WIN_BOOST["developing"]):
        _fail("5c", f"mixer gain ({gain_mixer:.3f}) should be positive but smaller than the win boost "
              f"({_WIN_BOOST['developing']:.3f})")
        passed = False

    if passed:
        _ok(f"mixer gains {gain_mixer:.3f} (< win boost {_WIN_BOOST['developing']:.3f}); "
            f"specialist gains {gain_spec:.3f}")
    return passed


# -- Runner -----------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  verify_style_mixing.py")
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
