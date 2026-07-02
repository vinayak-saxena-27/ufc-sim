"""
weight_cut.py — Walk-around weight (cut_severity) generation and its fight-night
performance modifier (Weight Class Flex, Session A).

## Design

The sim has no real weight units (155lbs, 185lbs, etc). `cut_severity` is a
RELATIVE, zero-centered attribute on Fighter representing how hard a fighter's
cut to their current weight_class is:

    near 0     — easy cut, walks close to competition weight
    positive   — harder cut; larger positive = more severe (+10 to +20 is
                 moderate-to-severe)
    negative   — walks BELOW competition weight; no cut, slight fight-night
                 benefit (handled naturally by the linear modifier below —
                 no special case needed)

## Generation (Part 1)

cut_severity correlates loosely with power/athleticism (bigger, more powerful
fighters tend to walk around heavier) plus meaningful Gaussian noise so the
correlation isn't perfect. HEAVYWEIGHT is a special case: there's no weight
limit above HW to cut for, so HW fighters are generated from an independent,
near-zero/negative distribution instead of the power/athleticism formula.

Formula (first-pass; weights chosen so tier4-caliber power/athleticism lands
in the spec's example +10 to +20 "moderate-to-severe" range, and tier0-caliber
lands negative — see weight_cut.py __main__ demo):

    non-HW: cut_severity = CUT_POWER_WEIGHT * power
                          + CUT_ATHLETICISM_WEIGHT * athleticism
                          + gauss(0, CUT_NOISE_STD)
    HW:     cut_severity = gauss(HW_CUT_MEAN, HW_CUT_STD)

Called from generate_tier_fighter() (tiers.py) — the only generator with a
weight_class parameter. templates.py's generate_fighter() (demo/test-only,
weight-class-agnostic) is untouched; its fighters keep the Fighter default
cut_severity=0.0.

## Fight-night modifier (Part 2)

apply_cut_to_fighter(fighter) returns a Fighter COPY with cut-affected
sub-attributes adjusted, following the exact modifier-layer pattern used by
apply_age_to_fighter() / apply_development_to_fighter() / apply_fatigue_to_fighter().
Base attributes on the Fighter object are NEVER written.

Affected attributes, most to least (all NEGATIVE for positive cut_severity —
i.e. a hard cut hurts these; a negative cut_severity, walking under weight,
produces a small positive benefit via the same linear formula):

    cardio      — most affected (energy systems hit hardest by dehydration/rehydration)
    chin        — second (recovery from damage worse dehydrated; more finishable)
    athleticism — third, smallest (explosiveness dulled on fight night)

All other attributes (boxing, wrestling, etc.) are untouched — technique
doesn't change from cutting weight.

Age interaction: modifier scales with BOTH cut_severity and fighter.age, using
age.py's _PRIME_START/_PRIME_END to define young/prime/old — older fighters
feel cuts harder. Mirrors age.py's own prime-window + past-prime-quadratic
shape for consistency:

    age < _PRIME_START:        dampened   (YOUNG_CUT_DAMPEN, e.g. 0.85x)
    _PRIME_START..._PRIME_END: baseline   (1.0x, no age effect)
    age > _PRIME_END:          amplified  (quadratic in years past prime end)

Quick reference (cut_severity=+15.0, a moderate-severe cut):
    age 24 (prime):  scale=1.00 -> cardio -5.25, chin -3.00, athleticism -1.50
    age 36 (t=6):     scale=1.72 -> cardio -9.03, chin -5.16, athleticism -2.58

All constants flagged first-pass; tune once real generated populations and
aged fight outcomes are observable — same convention as age.py/development.py.

## Integration into fight resolution stack (Part 3)

Order in fight.py: development -> cut -> age -> (fatigue, per round, unchanged).
Cut is fight-night state that happens before the bout, so it slots in before
age; age then further amplifies the cut's effect per the scaling above. The
cut modifier reads from the DEVELOPMENT-adjusted fighter (so career growth
feeds the base cardio/chin/athleticism the cut discounts), and age reads from
the CUT-adjusted fighter (so age amplifies the already-cut-discounted values).
fighter.age itself is a raw Fighter field untouched by any modifier layer, so
apply_cut_to_fighter can read it directly regardless of stack position.
"""
from __future__ import annotations

import random
from dataclasses import replace

from career.fighter import Fighter
from career.age import _PRIME_START, _PRIME_END

# ── Generation constants (Part 1) ───────────────────────────────────────────

CUT_POWER_WEIGHT:       float = 0.25
CUT_ATHLETICISM_WEIGHT: float = 0.15
CUT_NOISE_STD:          float = 8.0
"""
Non-HW cut_severity = CUT_POWER_WEIGHT*power + CUT_ATHLETICISM_WEIGHT*athleticism
                     + gauss(0, CUT_NOISE_STD).
At tier4-caliber power/athleticism (~+45 each): ~+18 (moderate-severe, matches spec example).
At tier0-caliber power/athleticism (~-35 each): ~-14 (negative, no cut).
Noise std of 8.0 keeps the correlation loose, not deterministic.
"""

HW_CUT_MEAN: float = -4.0
HW_CUT_STD:  float = 3.0
"""
Heavyweight special case: no weight limit above HW to cut for, so HW
cut_severity is drawn independently of power/athleticism, centered slightly
negative so most HW fighters land near-zero or negative per spec.
"""


def generate_cut_severity(power: float, athleticism: float, weight_class: str) -> float:
    """
    Sample cut_severity at fighter-generation time.

    weight_class == "heavyweight" uses the independent near-zero/negative HW
    distribution; all other weight classes (including "unknown") use the
    power/athleticism-correlated formula.
    """
    if weight_class == "heavyweight":
        return random.gauss(HW_CUT_MEAN, HW_CUT_STD)
    return (
        CUT_POWER_WEIGHT * power
        + CUT_ATHLETICISM_WEIGHT * athleticism
        + random.gauss(0.0, CUT_NOISE_STD)
    )


# ── Fight-night modifier constants (Part 2) ─────────────────────────────────

CUT_CARDIO_RATE:      float = 0.35   # pts of cardio penalty per unit cut_severity, baseline age
CUT_CHIN_RATE:        float = 0.20   # pts of chin penalty per unit cut_severity, baseline age
CUT_ATHLETICISM_RATE: float = 0.10   # pts of athleticism penalty per unit cut_severity, baseline age

YOUNG_CUT_DAMPEN:      float = 0.85  # age < _PRIME_START: cuts hurt slightly less
OLD_CUT_AMPLIFY_RATE:  float = 0.02  # quadratic amplification coefficient past _PRIME_END


def _age_cut_scale(age: int) -> float:
    """Age-based scaling factor on the cut modifier. Mirrors age.py's prime-window shape."""
    if age < _PRIME_START:
        return YOUNG_CUT_DAMPEN
    if age <= _PRIME_END:
        return 1.0
    t = age - _PRIME_END
    return 1.0 + (t * t * OLD_CUT_AMPLIFY_RATE)


# ── Public API ───────────────────────────────────────────────────────────────

def apply_cut_to_fighter(fighter: Fighter) -> Fighter:
    """
    Return a COPY of fighter with cut_severity-based modifiers applied to
    cardio, chin, and athleticism. The original fighter's base attributes are
    never mutated. Same modifier-layer pattern as apply_age_to_fighter() /
    apply_development_to_fighter() / apply_fatigue_to_fighter().

    Returns the original fighter unchanged when cut_severity == 0.0 (avoids
    unnecessary object creation).

    A negative cut_severity (walks under competition weight) naturally
    produces a small POSITIVE modifier here — the formula is linear in
    cut_severity, no special case needed.

    Integration: call in fight.py AFTER apply_development_to_fighter() and
    BEFORE apply_age_to_fighter() — see module docstring for the ordering
    rationale.
    """
    if fighter.cut_severity == 0.0:
        return fighter

    scale = _age_cut_scale(fighter.age)
    return replace(
        fighter,
        cardio      = fighter.cardio      - fighter.cut_severity * CUT_CARDIO_RATE      * scale,
        chin        = fighter.chin        - fighter.cut_severity * CUT_CHIN_RATE        * scale,
        athleticism = fighter.athleticism - fighter.cut_severity * CUT_ATHLETICISM_RATE * scale,
    )


# ── Demo (run as __main__) ───────────────────────────────────────────────────

if __name__ == "__main__":
    from career.tiers import generate_tier_fighter, TIER_LEVELS

    random.seed(11)

    print("\ncut_severity by weight class (tier2/mid-major fighters, dagestan_sambo template)")
    print(f"  {'WeightClass':<14}  {'power':>7}  {'athleticism':>11}  {'cut_severity':>12}")
    print("  " + "-" * 52)
    for wc in ["lightweight", "welterweight", "heavyweight"]:
        for _ in range(5):
            f = generate_tier_fighter("dagestan_sambo", "tier2", wc)
            print(f"  {wc:<14}  {f.power:>7.1f}  {f.athleticism:>11.1f}  {f.cut_severity:>12.1f}")

    print("\n  (correlation should be visible within a weight class but not perfect;")
    print("   heavyweight should cluster near zero/negative regardless of power/athleticism.)\n")

    # Before/after comparison at different ages for a high-cut_severity fighter.
    base = generate_tier_fighter("american_wrestling", "tier4", "welterweight")
    base = replace(base, cut_severity=15.0)  # force a moderate-severe cut for the demo

    print(f"Age-amplification demo (fixed cut_severity={base.cut_severity:+.1f}):")
    print(f"  {'Age':>4}  {'AgeScale':>8}  {'cardio (before->after)':>24}  "
          f"{'chin (before->after)':>22}  {'athleticism (before->after)':>28}")
    print("  " + "-" * 92)
    for age in (24, 36):
        f = replace(base, age=age)
        eff = apply_cut_to_fighter(f)
        scale = _age_cut_scale(age)
        print(
            f"  {age:>4}  {scale:>8.2f}  "
            f"{f.cardio:>+7.1f} -> {eff.cardio:>+7.1f}      "
            f"{f.chin:>+7.1f} -> {eff.chin:>+7.1f}      "
            f"{f.athleticism:>+7.1f} -> {eff.athleticism:>+7.1f}"
        )
    print()
