"""
age.py — Fighter age advancement and age-performance modifier.

## Age advancement

One simulated year passes every FIGHTS_PER_SIM_YEAR of an individual
fighter's own fights (~3 fights/year, consistent with active MMA careers).
This is the same per-fighter fight-count cadence used by maybe_update_labels
and the promotion/demotion checks — no new time concept introduced.

Call maybe_advance_age(fighter) after every fight for both winner and loser.
It mutates fighter.age in place when the fight count crosses a year boundary.

## Age-performance modifier

apply_age_to_fighter(fighter) returns a Fighter COPY with age-adjusted
sub-attributes for fight-resolution purposes.  Base attributes stored on the
Fighter object are NEVER written — same modifier-layer pattern used by
apply_fatigue_to_fighter() in fatigue.py.

Wire-up: called in fight.py before simulate_full_fight(), so the age-adjusted
copy enters the engine; fatigue then stacks on top within each round.

## Curve shape (first-pass — tune once real aged populations are observable)

    Development  (age < PRIME_START):   linear deficit, -DEVELOPMENT_RATE pts/yr
    Prime        (PRIME_START–PRIME_END): no modifier
    Decline      (age > PRIME_END):      quadratic: -(t² × DECLINE_RATE), t = age − PRIME_END

Quick reference:
    age 18: base modifier ≈ −3.5 pts   (developing, not yet at full potential)
    age 23: modifier =  0.0            (prime starts)
    age 30: modifier =  0.0            (prime ends)
    age 35: modifier ≈ −3.8 pts        (early decline; still competitive)
    age 38: modifier ≈ −9.6 pts        (significant decline)
    age 40: modifier ≈ −15.0 pts       (severe decline)

## Per-attribute-group multipliers (decline phase only)

Explosive attributes (athleticism, power, cardio): decline 30% faster.
Technical attributes (fight_iq, bjj):              decline 40% slower.
All other "mixed" attributes:                      1.0× base rate.

This captures the real-world observation that explosive physical attributes
peak earlier and fade harder than tactical/strategic skills.  Development
phase uses no group multiplier (all attributes develop uniformly).

All constants flagged as first-pass estimates; flag for retuning once
career-length populations are large enough to observe realistic age
distributions.
"""
from __future__ import annotations

from dataclasses import replace

from fighter import Fighter, ATTR_NAMES

# ── Advancement cadence ───────────────────────────────────────────────────────

FIGHTS_PER_SIM_YEAR: int = 3
"""
Per-fighter fight count that represents one simulated year.
Chosen to match real-world active MMA career pace (~3 bouts/year).
Same cadence as LABEL_UPDATE_INTERVAL and PROMOTE_WINDOW to avoid introducing
a separate time concept.
"""

# ── Curve constants ───────────────────────────────────────────────────────────
# All are first-pass estimates. Tune after observing career-end age distributions
# in the simulated population — the goal is that most fighters peak mid-20s to
# early 30s and retire (or get cut) in their mid-to-late 30s.

_PRIME_START: int   = 23     # development phase ends; prime begins
_PRIME_END:   int   = 30     # prime ends; decline begins

_DEVELOPMENT_RATE: float = 0.70
"""
Points of modifier per year below PRIME_START (uniform across all attributes).
At age 18: (18-23) × 0.70 = -3.5 pts.  Small — "don't overdo it" per spec.
"""

_DECLINE_RATE: float = 0.15
"""
Quadratic decline coefficient.  modifier = -(t² × DECLINE_RATE), t = age - PRIME_END.
At t=5 (age 35): -3.75.  At t=8 (age 38): -9.6.  At t=10 (age 40): -15.0.
"""

# ── Per-attribute-group decline multipliers ───────────────────────────────────

_EXPLOSIVE_ATTRS: frozenset[str] = frozenset({"athleticism", "power", "cardio"})
_TECHNICAL_ATTRS: frozenset[str] = frozenset({"fight_iq", "bjj"})

_EXPLOSIVE_DECLINE_MULT: float = 1.30   # decline 30% faster than base
_TECHNICAL_DECLINE_MULT: float = 0.60   # decline 40% slower than base


# ── Curve internals ───────────────────────────────────────────────────────────

def _base_age_modifier(age: int) -> float:
    """
    Base age modifier in sub-attribute point space (negative = penalty).
    Group multipliers are NOT applied here — see _attr_age_modifier.
    """
    if age < _PRIME_START:
        return (age - _PRIME_START) * _DEVELOPMENT_RATE  # negative, linear
    if age <= _PRIME_END:
        return 0.0
    t = age - _PRIME_END
    return -(t * t * _DECLINE_RATE)  # negative, quadratic


def _attr_age_modifier(attr: str, age: int) -> float:
    """Age modifier for one attribute, with group multiplier applied in decline."""
    base = _base_age_modifier(age)
    if base >= 0.0:
        # Development phase or prime: no group differentiation.
        return base
    # Decline phase: scale by attribute group.
    if attr in _EXPLOSIVE_ATTRS:
        return base * _EXPLOSIVE_DECLINE_MULT
    if attr in _TECHNICAL_ATTRS:
        return base * _TECHNICAL_DECLINE_MULT
    return base  # "mixed" attributes — boxing, kickboxing, wrestling, clinch, chin


# ── Public API ────────────────────────────────────────────────────────────────

def apply_age_to_fighter(fighter: Fighter) -> Fighter:
    """
    Return a COPY of fighter with age-based modifiers applied to all ten
    sub-attributes.  The original fighter's base attributes are never mutated.

    Returns the original fighter unchanged when age is in the prime window
    [PRIME_START, PRIME_END] — avoids unnecessary object creation.

    Integration: call this in fight.py before simulate_full_fight().
    The age-adjusted copy enters the engine; fatigue then layers on top
    within each round (inside fight_engine.py), preserving the
    age → fatigue stacking order.
    """
    if _PRIME_START <= fighter.age <= _PRIME_END:
        return fighter  # prime window: no modifier, skip copy

    return replace(
        fighter,
        wrestling   = fighter.wrestling   + _attr_age_modifier("wrestling",   fighter.age),
        bjj         = fighter.bjj         + _attr_age_modifier("bjj",         fighter.age),
        clinch      = fighter.clinch      + _attr_age_modifier("clinch",       fighter.age),
        boxing      = fighter.boxing      + _attr_age_modifier("boxing",       fighter.age),
        kickboxing  = fighter.kickboxing  + _attr_age_modifier("kickboxing",   fighter.age),
        power       = fighter.power       + _attr_age_modifier("power",        fighter.age),
        cardio      = fighter.cardio      + _attr_age_modifier("cardio",       fighter.age),
        chin        = fighter.chin        + _attr_age_modifier("chin",         fighter.age),
        athleticism = fighter.athleticism + _attr_age_modifier("athleticism",  fighter.age),
        fight_iq    = fighter.fight_iq    + _attr_age_modifier("fight_iq",     fighter.age),
    )


def maybe_advance_age(fighter: Fighter) -> None:
    """
    Advance fighter.age by 1 year when their fight count crosses a FIGHTS_PER_SIM_YEAR
    boundary.  Mutates fighter.age in place.

    Call after every fight for both winner and loser (same call site as
    maybe_update_labels in sim.py and title.py).
    """
    n = len(fighter.fight_history)
    if n > 0 and n % FIGHTS_PER_SIM_YEAR == 0:
        fighter.age += 1


# ── Curve demo (run as __main__) ─────────────────────────────────────────────

if __name__ == "__main__":
    """
    Show the age curve by printing effective overall at each age for a fighter
    with fixed base sub-attributes.  Illustrates development, prime, and decline
    phases, plus the per-attribute-group differential in the decline phase.
    """
    base = dict(
        wrestling=20.0, bjj=15.0, clinch=18.0,
        boxing=12.0, kickboxing=10.0, power=14.0,
        cardio=16.0, chin=12.0, athleticism=20.0, fight_iq=18.0,
    )
    # base overall = mean of the ten values = 15.5
    sample_ages = [18, 20, 21, 23, 25, 27, 30, 32, 35, 37, 38, 40]

    print(f"\nAge-performance curve  "
          f"(base overall = {sum(base.values())/len(base):+.1f} for all fighters below)")
    print(f"  Curve: dev<{_PRIME_START}  prime {_PRIME_START}-{_PRIME_END}  "
          f"decline>{_PRIME_END}  (decline_rate={_DECLINE_RATE}, dev_rate={_DEVELOPMENT_RATE})")
    print(f"  Group multipliers: explosive*{_EXPLOSIVE_DECLINE_MULT}  "
          f"technical*{_TECHNICAL_DECLINE_MULT}  mixed*1.0\n")

    print(f"  {'Age':>4}  {'BaseOvr':>7}  {'EffOvr':>7}  {'Mod':>6}  "
          f"{'Athletcs':>9}  {'FightIQ':>8}  {'Boxing':>7}  {'Phase'}")
    print("  " + "-" * 73)

    for age in sample_ages:
        f = Fighter(name="X", age=age, region="", template="", **base)
        eff = apply_age_to_fighter(f)
        base_ovr = f.overall
        eff_ovr  = eff.overall
        mod      = eff_ovr - base_ovr

        phase = (
            "developing" if age < _PRIME_START else
            "prime"      if age <= _PRIME_END  else
            "declining"
        )
        print(
            f"  {age:>4}  {base_ovr:>7.1f}  {eff_ovr:>7.1f}  {mod:>+6.1f}  "
            f"{eff.athleticism:>9.1f}  {eff.fight_iq:>8.1f}  {eff.boxing:>7.1f}  "
            f"{phase}"
        )

    print()
    print(f"  Explosive attr (athleticism, power, cardio): {_EXPLOSIVE_DECLINE_MULT:.0%} of base decline rate.")
    print(f"  Technical attr (fight_iq, bjj): {_TECHNICAL_DECLINE_MULT:.0%} of base decline rate.")
    print(f"  Mixed attr decline at base rate.")
    print(f"\n  Note: base attributes on Fighter objects are NEVER written;")
    print(f"  age modifier applies only to the effective copy used in fight resolution.")
    print()
