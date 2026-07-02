"""
development.py — Fighter development modifier (career growth for young fighters).

## Design

Development is age-based (not fight-count-based): a high-volume 18-year-old SEA
fighter develops at the same rate as a lower-volume American wrestler of the same
age — biological maturity and training time are the primary axes, not bouts logged.

## Modifier layer pattern

apply_development_to_fighter(fighter) returns a Fighter COPY with development-
adjusted sub-attributes for fight-resolution purposes. Base attributes on the
Fighter object are NEVER written — same pattern as apply_age_to_fighter() and
apply_fatigue_to_fighter().

## Attribute asymmetry (mirrors age.py decline asymmetry, positive direction)

  Technical (fight_iq, bjj, clinch): develop at 1.3× base rate
  Physical  (power, athleticism):    develop at 0.6× base rate
  All other attributes:              develop at 1.0× base rate

Tactical/strategic skills benefit most from training and coaching; peak physical
attributes are more biologically constrained.

## Development rate formula (annual sweep)

  gain = BASE_DEV_RATE
       × _TIER_MOD[fighter.prospect_tier]   # tier-dependent rate modifier
       × _pipeline_modifier(fighter.academy) # small bonus from pipeline_strength
       × _age_factor(fighter.age)            # linear 1.0 at 18 → 0.0 at PRIME_START

  age_factor:
    age < _PRIME_START (23): (_PRIME_START - age) / (_PRIME_START - 18)
    age >= _PRIME_START:     0.0  (development effectively complete)

## Win boost (fight-triggered, secondary)

apply_win_development_boost(fighter) adds a small gain to development_modifier
after a WIN. Magnitude scales with prospect_tier. No effect on losses.

## Stacking with age and fatigue

Call order at fight resolution (fight.py):
  1. apply_development_to_fighter(fighter)  — career development gain
  2. apply_age_to_fighter(dev_copy)         — age-based decline
  3. Fatigue per round                       — within fight_engine.py

Both development and age modifiers are purely additive constants, so chaining
them (apply age to the dev-copy) is mathematically equivalent to applying each
independently to base and summing:
  final_attr = base + dev_delta + age_delta
The order of steps 1 and 2 does not affect correctness.

## Constants

All constants are first-pass estimates; flag for retuning once career-arc
populations are large enough to observe realistic development curves — consistent
with how every other first-pass constant in this project has been handled.
"""
from __future__ import annotations

import dataclasses
import random

from career.fighter import Fighter
from career.academies import ACADEMY_PIPELINE
# _PRIME_START reuse is intentional: keeps both development and age systems
# anchored to the same prime-start constant so they remain coherent.
from career.age import _PRIME_START, SIM_DAYS_PER_YEAR
from sim_calendar import get_sim_day

# ── Prospect tier assignment ──────────────────────────────────────────────────

_TIER_PROBS: list[tuple[float, str]] = [
    (0.40, "raw"),          # ~40%
    (0.70, "developing"),   # ~30%  (cumulative 0.70)
    (0.90, "high_upside"),  # ~20%  (cumulative 0.90)
    (1.00, "elite"),        # ~10%  (cumulative 1.00)
]


def assign_prospect_tier() -> str:
    """Assign a prospect tier probabilistically at fighter generation time.

    raw ~40% | developing ~30% | high_upside ~20% | elite ~10%
    Tiers influence development RATE only — not ceiling.
    """
    r = random.random()
    for threshold, tier in _TIER_PROBS:
        if r < threshold:
            return tier
    return "elite"  # float rounding safety net


# ── Rate constants ────────────────────────────────────────────────────────────

BASE_DEV_RATE: float = 1.5
"""
Base development gain (in development_modifier points) per simulated year,
before prospect-tier and age modifiers. First-pass estimate.

At age 18, developing-tier fighter from average academy: gains 1.5 pts/yr.
Over the 5-year window (18–23), average age_factor ≈ 0.6, so total ≈ 4.5 pts.
Elite-tier fighter at best pipeline: 1.5 × 2.0 × 1.18 × avg_factor ≈ 10.6 pts.
Raw-tier fighter at worst pipeline:  1.5 × 0.7 × 0.88 × avg_factor ≈ 2.8 pts.
"""

_DEV_AGE_MIN: int = 18  # youngest possible generated fighter (matches tiers.py)

# Prospect tier → rate multiplier on BASE_DEV_RATE.
# Spec table: raw=-0.3x, developing=0x (baseline), high_upside=+0.4x, elite=+1.0x
# Translated: raw=0.7, developing=1.0, high_upside=1.4, elite=2.0
_TIER_MOD: dict[str, float] = {
    "raw":         0.7,    # 30% slower than baseline
    "developing":  1.0,    # baseline
    "high_upside": 1.4,    # 40% faster than baseline
    "elite":       2.0,    # 2× baseline ("+1.0x" = +100% of baseline)
}

# Win boost added to development_modifier per win, by prospect tier.
_WIN_BOOST: dict[str, float] = {
    "raw":         0.15,
    "developing":  0.25,
    "high_upside": 0.40,
    "elite":       0.60,
}

# Academy pipeline_strength → development rate multiplier.
# pipeline_strength range: approx -6 to +9 (from academies.py).
# At max (+9): 1.0 + 9 × 0.02 = 1.18.  At min (-6): 1.0 + (-6) × 0.02 = 0.88.
_PIPELINE_DEV_SCALE: float = 0.02

# ── Attribute group multipliers ───────────────────────────────────────────────
# Applied when computing effective attribute delta from development_modifier.

_TECHNICAL_DEV_ATTRS: frozenset[str] = frozenset({"fight_iq", "bjj", "clinch"})
_PHYSICAL_DEV_ATTRS:  frozenset[str] = frozenset({"power", "athleticism"})

_TECHNICAL_DEV_MULT: float = 1.3   # technical skills develop faster
_PHYSICAL_DEV_MULT:  float = 0.6   # physical attributes develop slower


# ── Internal helpers ──────────────────────────────────────────────────────────

def _age_factor(age: int) -> float:
    """Linear development factor: 1.0 at age _DEV_AGE_MIN, 0.0 at _PRIME_START."""
    if age >= _PRIME_START:
        return 0.0
    if age <= _DEV_AGE_MIN:
        return 1.0
    return (_PRIME_START - age) / (_PRIME_START - _DEV_AGE_MIN)


def _pipeline_modifier(academy_name: str) -> float:
    """Convert academy pipeline_strength to a development rate multiplier.
    Clamped to 0.1 minimum to prevent degenerate zero/negative values.
    """
    ps = ACADEMY_PIPELINE.get(academy_name, 0.0)
    return max(0.1, 1.0 + ps * _PIPELINE_DEV_SCALE)


def _attr_dev_multiplier(attr: str) -> float:
    """Attribute-specific multiplier for applying development_modifier."""
    if attr in _TECHNICAL_DEV_ATTRS:
        return _TECHNICAL_DEV_MULT
    if attr in _PHYSICAL_DEV_ATTRS:
        return _PHYSICAL_DEV_MULT
    return 1.0


# ── Public API ────────────────────────────────────────────────────────────────

def apply_development_to_fighter(fighter: Fighter) -> Fighter:
    """
    Return a COPY of fighter with development modifiers applied to all ten
    sub-attributes. The original fighter's base attributes are never mutated.

    Returns the original fighter unchanged when development_modifier == 0.0
    (all fighters at sim start; avoids unnecessary object creation).

    Integration: call this in fight.py BEFORE apply_age_to_fighter().
    """
    if fighter.development_modifier == 0.0:
        return fighter

    dm = fighter.development_modifier
    return dataclasses.replace(
        fighter,
        wrestling   = fighter.wrestling   + dm * _attr_dev_multiplier("wrestling"),
        bjj         = fighter.bjj         + dm * _attr_dev_multiplier("bjj"),
        clinch      = fighter.clinch      + dm * _attr_dev_multiplier("clinch"),
        boxing      = fighter.boxing      + dm * _attr_dev_multiplier("boxing"),
        kickboxing  = fighter.kickboxing  + dm * _attr_dev_multiplier("kickboxing"),
        power       = fighter.power       + dm * _attr_dev_multiplier("power"),
        cardio      = fighter.cardio      + dm * _attr_dev_multiplier("cardio"),
        chin        = fighter.chin        + dm * _attr_dev_multiplier("chin"),
        athleticism = fighter.athleticism + dm * _attr_dev_multiplier("athleticism"),
        fight_iq    = fighter.fight_iq    + dm * _attr_dev_multiplier("fight_iq"),
    )


def apply_win_development_boost(fighter: Fighter) -> None:
    """
    Add a small win-triggered development boost to fighter.development_modifier.
    Mutates in place — same pattern as f.age += 1 in advance_all_ages().
    Only call this after a WIN. Losses have no direct development effect.
    """
    boost = _WIN_BOOST.get(fighter.prospect_tier, _WIN_BOOST["developing"])
    fighter.development_modifier += boost


# ── Time-based global development advancement ─────────────────────────────────

_last_dev_advance_day: int = 0


def reset_development_advancement() -> None:
    """Reset the development-advancement clock to day 0. Call at start of each sim."""
    global _last_dev_advance_day
    _last_dev_advance_day = 0


def advance_all_development(all_fighters: list[Fighter]) -> None:
    """
    Advance development_modifier for every fighter younger than _PRIME_START.
    Fires once per SIM_DAYS_PER_YEAR elapsed — same cadence as advance_all_ages().

    Call immediately after advance_all_ages() in the sim loop so fighters' ages
    have already been updated before the age_factor is evaluated.

    Win boosts (per fight) are handled separately by apply_win_development_boost().
    """
    global _last_dev_advance_day
    current_day = get_sim_day()
    while current_day - _last_dev_advance_day >= SIM_DAYS_PER_YEAR:
        for f in all_fighters:
            af = _age_factor(f.age)
            if af <= 0.0:
                continue  # prime or older — no camp-based development
            tier_mod     = _TIER_MOD.get(f.prospect_tier, _TIER_MOD["developing"])
            pipeline_mod = _pipeline_modifier(f.academy)
            f.development_modifier += BASE_DEV_RATE * tier_mod * pipeline_mod * af
        _last_dev_advance_day += SIM_DAYS_PER_YEAR
