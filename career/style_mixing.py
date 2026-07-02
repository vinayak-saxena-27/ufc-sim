"""
style_mixing.py -- Generation-time formula for Fighter.style_flexibility.

Voluntary style-mixing: a fighter's willingness to operate outside their
dominant phase even when they could pursue it (the "Islam test" — a
generational wrestler choosing to strike for meaningful stretches against a
striker because his boxing is decent in absolute terms, not because he can't
grapple). This module only covers GENERATION of the trait; the per-fight
usage (matchup/tier modulation, absolute-skill gating, transition-tendency
integration) lives in engine/phase_engine.py.

## Formula

    style_flexibility = STYLE_FLEX_FIGHT_IQ_COEF * fight_iq
                       + TEMPLATE_STYLE_FLEX_NUDGE[template]
                       + gauss(0, STYLE_FLEX_NOISE_SIGMA)

Smart fighters trend more adaptable, but the correlation is loose by design:
some high-IQ fighters are deliberate specialists by choice, some lower-IQ
fighters are naturally eclectic. The noise term is large enough to keep the
correlation visible-but-not-deterministic — a first-pass estimate.

## Academy / regional-template nudge

Small nudge on top of the fight_iq correlation, based on the fighter's
academy's regional template (not fight_iq or academy-specific tuning):
  - Mixed/diverse training cultures (Brazilian, SEA Mixed): positive nudge
  - Muay Thai: slight positive (clinch/striking blend gives some adaptability)
  - Pure-discipline cultures (Dagestan/Sambo, American Wrestling): negative
Deliberately smaller in magnitude than both the fight_iq term and the noise
term — academy is a mild influence, not a determinant.
"""
from __future__ import annotations

import random

STYLE_FLEX_FIGHT_IQ_COEF: float = 0.4
STYLE_FLEX_NOISE_SIGMA:   float = 12.0   # first-pass: keeps correlation visible, not deterministic

TEMPLATE_STYLE_FLEX_NUDGE: dict[str, float] = {
    "brazilian":         +4.0,   # mixed BJJ/boxing/kickboxing training culture
    "sea_mixed":          +4.0,   # explicitly "mixed" regional template
    "muay_thai":           +2.0,   # clinch/striking blend gives some adaptability
    "dagestan_sambo":     -4.0,   # pure wrestling/sambo discipline
    "american_wrestling": -4.0,   # pure wrestling discipline
}


def generate_style_flexibility(fight_iq: float, template_name: str) -> float:
    """Sample style_flexibility for a newly generated fighter.

    fight_iq: the fighter's already-sampled fight_iq value (correlate with,
    not derive independently — call this AFTER fight_iq is sampled).
    template_name: fighter.template, used to look up the regional nudge.
    """
    nudge = TEMPLATE_STYLE_FLEX_NUDGE.get(template_name, 0.0)
    return (
        STYLE_FLEX_FIGHT_IQ_COEF * fight_iq
        + nudge
        + random.gauss(0.0, STYLE_FLEX_NOISE_SIGMA)
    )
