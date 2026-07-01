"""
inflow.py -- Non-academy fighter origin generators.

Two archetypes that enter the sim outside the per-academy pipeline:

  Crossover athletes (~2-3/year total):
    Athletes from other combat sports (boxing, wrestling, Muay Thai, BJJ, Sambo/Judo).
    Entry tier determined by prior-sport caliber; highly asymmetric attribute profiles.
    Always assigned high_upside or elite prospect tier -- existing development system
    then fills their MMA gaps naturally (technical attrs at 1.3x is exactly what they need).

  Lateral transfers (~4-5/year total):
    Established MMA fighters relocating between organizations.
    Standard balanced MMA profile from existing regional templates; entry tier skewed
    toward the higher end of the selected tier range (they've already proven themselves).

All constants are first-pass; tune once long-run inflow dynamics are observed.

Called from replenishment.py only -- not wired directly into sim.py.
"""
from __future__ import annotations

import random
from statistics import mean as _mean

from fighter import Fighter
from templates import TEMPLATES, _TEMPLATE_REGIONS, _sample_hype
from tiers import TIER_CONFIG, ATTR_NOISE_STD
from academies import pick_academy, regional_name
from development import assign_prospect_tier


# ── Crossover sport profiles ──────────────────────────────────────────────────
# Each profile maps attribute -> delta from the fighter's target_overall.
# Constraint: all 10 deltas must sum to 0 so the profile asymmetry is
# preserverd without shifting the fighter's overall away from the tier center.
# "High" attrs are +6 to +28; "Low" attrs are -10 to -26.

_CROSSOVER_NOISE: float = ATTR_NOISE_STD  # per-attribute noise (same as regional templates)

CROSSOVER_PROFILES: dict[str, dict[str, float]] = {
    "boxer": {
        # Striking-heavy, zero ground game. Power and chin from years of sparring.
        # fight_iq poor: boxing ring reads don't transfer cleanly to MMA phase flow.
        "boxing":      +22.0,
        "power":       +15.0,
        "chin":        +10.0,
        "kickboxing":  +6.0,
        "clinch":      +1.0,
        "cardio":       0.0,
        "athleticism":  0.0,
        "fight_iq":   -10.0,
        "wrestling":  -18.0,
        "bjj":        -26.0,
        # sum: 54 + (-54) = 0
    },
    "wrestler": {
        # Dominant takedowns, elite cardio, poor striking. Scrambles well in clinch.
        # BJJ poor: wrestlers resist guard, submissions an afterthought in their sport.
        "wrestling":   +24.0,
        "athleticism": +16.0,
        "cardio":      +12.0,
        "clinch":      +5.0,
        "chin":        +2.0,
        "boxing":     -14.0,
        "kickboxing": -16.0,
        "bjj":        -18.0,
        "power":       -6.0,
        "fight_iq":    -5.0,
        # sum: 59 + (-59) = 0
    },
    "muay_thai": {
        # Elite clinch and striking; Muay Thai clinch (neck wrestling + knees) is world-class.
        # Zero takedown background -- will be sprawl-and-brawl but without the sprawl.
        "kickboxing":  +24.0,
        "clinch":      +18.0,
        "chin":        +10.0,
        "boxing":      +8.0,
        "cardio":      +5.0,
        "wrestling":  -22.0,
        "bjj":        -22.0,
        "fight_iq":   -10.0,
        "power":       -7.0,
        "athleticism": -4.0,
        # sum: 65 + (-65) = 0
    },
    "bjj_champion": {
        # World-class submission game; fight_iq from reading positional transitions.
        # No striking lineage -- physically conditioned differently from grapplers.
        "bjj":         +28.0,
        "fight_iq":    +18.0,
        "wrestling":   +10.0,
        "clinch":      +6.0,
        "athleticism": +3.0,
        "boxing":     -22.0,
        "kickboxing": -20.0,
        "cardio":     -12.0,
        "power":       -8.0,
        "chin":        -3.0,
        # sum: 65 + (-65) = 0
    },
    "sambo_judo": {
        # Explosive throws, grip-based clinch, solid ground control from Sambo base.
        # No striking whatsoever -- Judo/Sambo training never develops punching mechanics.
        "wrestling":   +26.0,
        "clinch":      +18.0,
        "bjj":         +14.0,
        "chin":        +5.0,
        "athleticism": +2.0,
        "boxing":     -22.0,
        "power":      -18.0,
        "kickboxing": -16.0,
        "cardio":      -6.0,
        "fight_iq":    -3.0,
        # sum: 65 + (-65) = 0
    },
}

# Which regional template provides names for each sport background
_SPORT_NAME_TEMPLATE: dict[str, str] = {
    "boxer":        "american_wrestling",   # boxing most common in Americas
    "wrestler":     "american_wrestling",
    "muay_thai":    "muay_thai",
    "bjj_champion": "brazilian",
    "sambo_judo":   "dagestan_sambo",
}

_SPORT_REGION: dict[str, str] = {
    "boxer":        "United States",
    "wrestler":     "United States",
    "muay_thai":    "Thailand",
    "bjj_champion": "Brazil",
    "sambo_judo":   "Dagestan/Russia",
}

_SPORT_LABELS: dict[str, str] = {
    "boxer":        "Boxing",
    "wrestler":     "Wrestling",
    "muay_thai":    "Muay Thai",
    "bjj_champion": "BJJ",
    "sambo_judo":   "Sambo/Judo",
}


# ── Crossover caliber tiers ───────────────────────────────────────────────────
# Four caliber levels for the athlete's prior-sport career, mapped to MMA entry tier.

_CALIBER_CUMULATIVE_PROBS: list[tuple[float, str]] = [
    (0.40, "recreational"),
    (0.75, "national"),
    (0.95, "elite_decorated"),
    (1.00, "transcendent"),
]

# (tier_key, tier_center, tier_spread) for each caliber.
# Transcendent uses None -- resolved probabilistically (70% tier3, 30% tier4).
_CALIBER_TIER_PARAMS: dict[str, tuple[str, float, float] | None] = {
    "recreational":    ("tier1", -15.0, 12.0),
    "national":        ("tier2",   0.0, 12.0),
    "elite_decorated": ("tier3", +20.0,  8.0),
    "transcendent":    None,
}

_CALIBER_HYPE_BOOST: dict[str, float] = {
    "recreational":    +5.0,
    "national":        +15.0,
    "elite_decorated": +30.0,
    "transcendent":    +60.0,
}

# Prospect tier: crossovers always high_upside or elite (never raw/developing).
# P(high_upside) by caliber; P(elite) = 1 - P(high_upside).
_CALIBER_PROSPECT_TIER_P_HIGH_UPSIDE: dict[str, float] = {
    "recreational":    0.70,
    "national":        0.60,
    "elite_decorated": 0.40,
    "transcendent":    0.20,
}

# Starting age distribution (mean, std, min, max) by caliber.
_CALIBER_AGE_PARAMS: dict[str, tuple[float, float, int, int]] = {
    "recreational":    (24.0, 2.0, 20, 30),
    "national":        (27.0, 2.5, 22, 32),
    "elite_decorated": (29.0, 2.5, 24, 35),
    "transcendent":    (31.0, 3.0, 25, 38),
}


def _pick_caliber() -> str:
    r = random.random()
    for cumprob, caliber in _CALIBER_CUMULATIVE_PROBS:
        if r < cumprob:
            return caliber
    return "transcendent"


def _caliber_tier_params(caliber: str) -> tuple[str, float, float]:
    params = _CALIBER_TIER_PARAMS[caliber]
    if params is None:
        # Transcendent: 30% direct Elite, 70% Top-org
        if random.random() < 0.30:
            return ("tier4", +45.0, 10.0)
        return ("tier3", +20.0, 8.0)
    return params


def _pick_crossover_prospect_tier(caliber: str) -> str:
    p_high_upside = _CALIBER_PROSPECT_TIER_P_HIGH_UPSIDE[caliber]
    return "high_upside" if random.random() < p_high_upside else "elite"


def generate_crossover(weight_class: str) -> tuple[Fighter, str, str]:
    """
    Generate one crossover athlete entering MMA from another combat sport.

    Returns (fighter, sport_background, caliber) for inline logging.
    fighter.academy encodes the crossover identity: "[Crossover: Boxing]" etc.
    fighter.template encodes the sport origin: "crossover_boxer" etc.
    """
    sport = random.choice(list(CROSSOVER_PROFILES.keys()))
    caliber = _pick_caliber()
    tier_key, tier_center, tier_spread = _caliber_tier_params(caliber)

    target_overall = random.gauss(tier_center, tier_spread)

    profile = CROSSOVER_PROFILES[sport]
    attrs: dict[str, float] = {
        attr: target_overall + delta + random.gauss(0.0, _CROSSOVER_NOISE)
        for attr, delta in profile.items()
    }

    age_params = _CALIBER_AGE_PARAMS[caliber]
    age = max(age_params[2], min(age_params[3], int(random.gauss(age_params[0], age_params[1]))))

    hype = _sample_hype(attrs["power"], attrs["athleticism"]) + _CALIBER_HYPE_BOOST[caliber]
    prospect_tier = _pick_crossover_prospect_tier(caliber)

    name_template = _SPORT_NAME_TEMPLATE[sport]
    name = regional_name(name_template)

    fighter = Fighter(
        name=name,
        age=age,
        region=_SPORT_REGION[sport],
        template=f"crossover_{sport}",
        tier=tier_key,
        weight_class=weight_class,
        academy=f"[Crossover: {_SPORT_LABELS[sport]}]",
        prospect_tier=prospect_tier,
        hype=hype,
        **attrs,
    )
    return fighter, sport, caliber


# ── Lateral transfer ──────────────────────────────────────────────────────────
# Already-MMA fighters moving between orgs. Balanced profiles from existing regional
# templates. Entry tier skewed toward higher end -- they've proven themselves elsewhere.

# Template selection weights for which regions produce lateral transfers
_LATERAL_TEMPLATE_WEIGHTS: list[tuple[str, float]] = [
    ("dagestan_sambo",     0.20),
    ("american_wrestling", 0.20),
    ("brazilian",          0.30),
    ("muay_thai",          0.10),
    ("sea_mixed",          0.20),
]

_LATERAL_ENTRY_TIER_PROBS: list[tuple[float, str]] = [
    (0.40, "tier1"),
    (0.75, "tier2"),   # cumulative: 0.40+0.35=0.75
    (0.95, "tier3"),   # cumulative: 0.75+0.20=0.95
    (1.00, "tier4"),   # cumulative: 0.95+0.05=1.00
]

# Shift mean toward higher end of tier range for laterals (proven fighters)
_LATERAL_OVERALL_BOOST: float = 5.0

_LATERAL_HYPE_BY_TIER: dict[str, float] = {
    "tier1":  0.0,
    "tier2":  5.0,
    "tier3": 12.0,
    "tier4": 20.0,
}


def _pick_lateral_template() -> str:
    r = random.random()
    cumulative = 0.0
    for template, weight in _LATERAL_TEMPLATE_WEIGHTS:
        cumulative += weight
        if r < cumulative:
            return template
    return _LATERAL_TEMPLATE_WEIGHTS[-1][0]


def _pick_lateral_tier() -> str:
    r = random.random()
    for cumprob, tier_key in _LATERAL_ENTRY_TIER_PROBS:
        if r < cumprob:
            return tier_key
    return "tier4"


def generate_lateral(weight_class: str) -> tuple[Fighter, str]:
    """
    Generate one established MMA fighter transferring from another org.

    Returns (fighter, tier_key) for inline logging.
    Uses the same attribute formula as generate_tier_fighter but with
    a boosted target_overall (skewed toward the higher end of the tier range).
    """
    template_name = _pick_lateral_template()
    tier_key = _pick_lateral_tier()
    tier = TIER_CONFIG[tier_key]

    target_overall = random.gauss(tier.center + _LATERAL_OVERALL_BOOST, tier.spread)

    cfg = TEMPLATES[template_name]
    natural_ovr = _mean(m for m, _ in cfg.values())

    academy = pick_academy(template_name)
    attrs: dict[str, float] = {
        attr: target_overall + (tmpl_mean - natural_ovr) + academy.get_nudge(attr) + random.gauss(0.0, ATTR_NOISE_STD)
        for attr, (tmpl_mean, _) in cfg.items()
    }

    # Lateral age: peers of regular fighters at this tier, slightly skewed older
    # (they've built a career elsewhere before transferring).
    _age_params: dict[str, tuple[float, float, int, int]] = {
        "tier1": (24.0, 2.0, 20, 29),
        "tier2": (27.0, 2.5, 22, 33),
        "tier3": (29.0, 2.5, 24, 36),
        "tier4": (31.0, 3.0, 25, 38),
    }
    ap = _age_params[tier_key]
    age = max(ap[2], min(ap[3], int(random.gauss(ap[0], ap[1]))))

    hype_boost = _LATERAL_HYPE_BY_TIER[tier_key]
    hype = _sample_hype(attrs["power"], attrs["athleticism"]) + academy.pipeline_strength + hype_boost
    prospect_tier = assign_prospect_tier()

    fighter = Fighter(
        name=regional_name(template_name),
        age=age,
        region=_TEMPLATE_REGIONS[template_name],
        template=template_name,
        tier=tier_key,
        weight_class=weight_class,
        academy=academy.name,
        prospect_tier=prospect_tier,
        hype=hype,
        **attrs,
    )
    return fighter, tier_key
