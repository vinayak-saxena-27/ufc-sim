from __future__ import annotations

import random

from fighter import Fighter
from academies import pick_academy, regional_name, reset_name_registry


# ─── Template config ──────────────────────────────────────────────────────────
# Format: {attribute: (mean, std)}
# "strong"  ≈ mean +15 to +25, std ~7–8
# "weak"    ≈ mean -10 to -20, std ~7–8
# "neutral" ≈ mean near 0,    std ~8–10
#
# These are starting-point values — tune them interactively once you see output.
# All constants are here, not scattered through the generator, so adjustments
# require edits in exactly one place.
#
# HOOK: When physical archetypes (height/reach, weight-class tendencies) are added,
# extend each template dict here with those fields.

TEMPLATES: dict[str, dict[str, tuple[float, float]]] = {
    "dagestan_sambo": {
        # Strengths: wrestling, clinch control, cardio, chin
        # Weakness:  boxing
        "wrestling":   (22.0,  8.0),
        "bjj":         ( 5.0,  8.0),
        "clinch":      (20.0,  8.0),
        "boxing":      (-15.0, 7.0),
        "kickboxing":  (-8.0,  8.0),
        "power":       ( 5.0,  8.0),
        "cardio":      (22.0,  7.0),
        "chin":        (16.0,  7.0),
        "athleticism": ( 8.0,  8.0),
        "fight_iq":    ( 8.0,  8.0),
    },
    "american_wrestling": {
        # Strengths: wrestling, athleticism, cardio
        # Weakness:  bjj, kickboxing
        "wrestling":   (22.0,  7.0),
        "bjj":         (-12.0, 8.0),
        "clinch":      ( 5.0,  8.0),
        "boxing":      ( 8.0,  8.0),
        "kickboxing":  (-10.0, 8.0),
        "power":       ( 8.0,  8.0),
        "cardio":      (20.0,  7.0),
        "chin":        ( 5.0,  8.0),
        "athleticism": (20.0,  7.0),
        "fight_iq":    ( 5.0,  8.0),
    },
    "brazilian": {
        # Strengths: bjj, fight_iq
        # Variable:  athleticism (intentionally high spread — Brazilian athletes vary a lot)
        "wrestling":   ( 0.0, 10.0),
        "bjj":         (25.0,  7.0),
        "clinch":      ( 5.0,  8.0),
        "boxing":      ( 8.0,  8.0),
        "kickboxing":  ( 8.0,  8.0),
        "power":       ( 5.0,  8.0),
        "cardio":      ( 5.0,  8.0),
        "chin":        ( 5.0,  8.0),
        "athleticism": (-5.0, 12.0),  # high std is intentional
        "fight_iq":    (22.0,  7.0),
    },
    "muay_thai": {
        # Strengths: kickboxing, clinch, chin
        # Weakness:  wrestling
        "wrestling":   (-18.0, 7.0),
        "bjj":         (-5.0,  8.0),
        "clinch":      (22.0,  7.0),
        "boxing":      (10.0,  8.0),
        "kickboxing":  (25.0,  7.0),
        "power":       (10.0,  8.0),
        "cardio":      (10.0,  8.0),
        "chin":        (18.0,  7.0),
        "athleticism": ( 8.0,  8.0),
        "fight_iq":    ( 5.0,  8.0),
    },
    "sea_mixed": {
        # Strengths: kickboxing, bjj, athleticism
        # Weakness:  power, wrestling
        "wrestling":   (-12.0, 8.0),
        "bjj":         (15.0,  8.0),
        "clinch":      ( 8.0,  8.0),
        "boxing":      ( 5.0,  8.0),
        "kickboxing":  (18.0,  7.0),
        "power":       (-10.0, 8.0),
        "cardio":      ( 5.0,  8.0),
        "chin":        ( 5.0,  8.0),
        "athleticism": (18.0,  7.0),
        "fight_iq":    ( 8.0,  8.0),
    },
}

_TEMPLATE_REGIONS: dict[str, str] = {
    "dagestan_sambo":     "Dagestan/Russia",
    "american_wrestling": "United States",
    "brazilian":          "Brazil",
    "muay_thai":          "Thailand",
    "sea_mixed":          "Southeast Asia",
}


def _sample_hype(power: float, athleticism: float) -> float:
    # Crude proxy: finishing-capable and explosive fighters draw more attention.
    # TODO: Real hype system tracks finishes, upsets, social reach, and media coverage
    #       independently of true skill — gap between hype and skill drives promotion
    #       speed and matchmaking priority in later sessions.
    return 0.4 * power + 0.3 * athleticism + random.gauss(0.0, 10.0)


def generate_fighter(template_name: str) -> Fighter:
    """Samples one fighter from the given template distribution."""
    academy = pick_academy(template_name)
    cfg = TEMPLATES[template_name]
    attrs = {
        attr: random.gauss(mean + academy.get_nudge(attr), std)
        for attr, (mean, std) in cfg.items()
    }
    age = max(18, min(42, int(random.gauss(27.0, 4.0))))
    return Fighter(
        name=regional_name(template_name),
        age=age,
        region=_TEMPLATE_REGIONS[template_name],
        template=template_name,
        academy=academy.name,
        hype=_sample_hype(attrs["power"], attrs["athleticism"]) + academy.pipeline_strength,
        **attrs,
    )


def generate_population(per_template: int = 40) -> list[Fighter]:
    """Generates `per_template` fighters from each of the 5 templates."""
    reset_name_registry()
    fighters: list[Fighter] = []
    for template_name in TEMPLATES:
        for _ in range(per_template):
            fighters.append(generate_fighter(template_name))
    return fighters
