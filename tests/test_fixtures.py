"""
test_fixtures.py -- Reusable fighter fixtures for calibration and regression tests.

Provides:
  make_zero_baseline(name)              -- all sub-attributes = 0; style-neutral reference
  make_style_fighter(name, target, style) -- deterministic (no noise) fighter at target overall

Zero-baseline rationale:
  All 10 sub-attributes = 0 → each transition contest and scoring formula sees
  gap = opponent_attr - 0 = opponent_attr exactly. The baseline fighter introduces
  no style preference of its own, making it a clean measuring stick for whether
  a test fighter's overall advantage translates consistently to win rate across
  different style shapes.

Style shapes:
  Each style is defined by per-attribute DEVIATIONS from a neutral baseline (sum = 0),
  applied as: attr = target_overall + deviation. Because deviations sum to 0, the
  fighter's overall (mean of all 10 attrs) equals target_overall exactly.

Available styles:
  flat       -- all deviations 0; perfectly balanced; every attr = target overall
  dagestan   -- wrestling/clinch/cardio/chin high; boxing/kick low
  muay_thai  -- kickboxing/boxing/chin/clinch high; wrestling/bjj/athleticism low
  brazilian  -- bjj/athleticism/fight_iq high; boxing/kick/clinch low
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fighter import Fighter, ATTR_NAMES


# ─── Style deviation templates ─────────────────────────────────────────────────
# Each template's deviations sum to 0 so that overall = target exactly.
# Represents how a style specialist distributes relative to a balanced fighter.

STYLE_DEVIATIONS: dict[str, dict[str, float]] = {

    "flat": {a: 0.0 for a in ATTR_NAMES},

    # High wrestling/clinch control and cardio; poor boxer/kicker
    # Deviations: 13.7-3.3+11.7-23.3-16.3-3.3+13.7+7.7-0.3-0.3 = 0
    "dagestan": {
        "wrestling":   +13.7,
        "bjj":          -3.3,
        "clinch":      +11.7,
        "boxing":      -23.3,
        "kickboxing":  -16.3,
        "power":        -3.3,
        "cardio":      +13.7,
        "chin":         +7.7,
        "athleticism":  -0.3,
        "fight_iq":     -0.3,
    },

    # Elite kickboxer and boxer; poor wrestler/BJJ/athleticism
    # Deviations: -20-15+10+15+25+10+5+10-20-20 = 0
    "muay_thai": {
        "wrestling":   -20.0,
        "bjj":         -15.0,
        "clinch":      +10.0,
        "boxing":      +15.0,
        "kickboxing":  +25.0,
        "power":       +10.0,
        "cardio":       +5.0,
        "chin":        +10.0,
        "athleticism": -20.0,
        "fight_iq":    -20.0,
    },

    # Elite BJJ and scrambles; poor striker and clinch
    # Deviations: -10+30-10-15-20-10+10-5+15+15 = 0
    "brazilian": {
        "wrestling":   -10.0,
        "bjj":         +30.0,
        "clinch":      -10.0,
        "boxing":      -15.0,
        "kickboxing":  -20.0,
        "power":       -10.0,
        "cardio":      +10.0,
        "chin":         -5.0,
        "athleticism": +15.0,
        "fight_iq":    +15.0,
    },
}

# Readable display labels for test output
STYLE_LABELS: dict[str, str] = {
    "flat":      "Flat",
    "dagestan":  "Dagestan",
    "muay_thai": "Muay Thai",
    "brazilian": "Brazilian",
}


def make_zero_baseline(name: str = "ZeroBaseline") -> Fighter:
    """
    Fighter with all 10 sub-attributes = 0.0.

    This is the neutral measuring stick for overall-scaling fidelity tests.
    Placing this fighter as the opponent means every contest resolves entirely
    on the test fighter's own attribute values -- the baseline contributes no
    style-specific advantage or disadvantage of its own. Overall = 0.0 exactly
    (the center of the Mid-major tier in the existing tier table).
    """
    return Fighter(
        name=name, age=28,
        region="test", template="zero_baseline", tier="test",
        wrestling    = 0.0,
        bjj          = 0.0,
        clinch       = 0.0,
        boxing       = 0.0,
        kickboxing   = 0.0,
        power        = 0.0,
        cardio       = 0.0,
        chin         = 0.0,
        athleticism  = 0.0,
        fight_iq     = 0.0,
    )


def make_style_fighter(name: str, target: float, style: str) -> Fighter:
    """
    Create a deterministic (no random noise) fighter at `target` overall with the given style shape.

    Attributes: attr = target + STYLE_DEVIATIONS[style][attr]
    Because deviations sum to 0, overall = mean(all attrs) = target exactly.

    Args:
        name:   Fighter name string.
        target: Target overall rating (e.g. 29.0, 20.0, 45.0).
        style:  One of: 'flat', 'dagestan', 'muay_thai', 'brazilian'
    """
    if style not in STYLE_DEVIATIONS:
        raise ValueError(
            f"Unknown style '{style}'. Available: {sorted(STYLE_DEVIATIONS)}"
        )
    deviations = STYLE_DEVIATIONS[style]
    attrs = {attr: target + deviations[attr] for attr in ATTR_NAMES}
    return Fighter(
        name=name, age=28,
        region="test", template=style, tier="test",
        **attrs,
    )
