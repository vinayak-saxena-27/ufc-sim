from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean as _mean

from fighter import Fighter
from templates import TEMPLATES, _random_name, _TEMPLATE_REGIONS, _sample_hype

# ─── Tuning constants ─────────────────────────────────────────────────────────
# Per-attribute noise added on top of the template shape offset.
# With 10 attributes, its contribution to overall std is sqrt(ATTR_NOISE_STD^2/10) ~= 1.6,
# so it adds texture to attribute profiles without meaningfully shifting the overall.
ATTR_NOISE_STD: float = 5.0


# ─── Tier table ───────────────────────────────────────────────────────────────
# Centers and spreads are calibrated against the real-world career anchor in
# smoke_test.py. Do not adjust these numbers independently of scale in fight.py —
# if one changes, re-derive the other against the anchor before committing.
#
# Tiers DELIBERATELY OVERLAP at their edges (e.g. Tier 2 ceiling ~+24 overlaps
# Tier 3 floor ~+4). A great mid-major and a weak top-org fighter can have
# comparable true skill. Tier = where a fighter currently competes, not a clamp
# on their overall. The generator uses these as SAMPLING parameters for new
# fighters only — never adjust an existing fighter's overall on promotion/demotion.

@dataclass(frozen=True)
class TierConfig:
    key:    str
    label:  str
    level:  int     # 0 (lowest) to 4 (highest)
    center: float   # overall distribution mean
    spread: float   # overall distribution sigma (~2-sigma = practical range)


TIER_CONFIG: dict[str, TierConfig] = {
    "tier0": TierConfig("tier0", "Amateur",         0, -35.0, 12.0),
    "tier1": TierConfig("tier1", "Regional",        1, -15.0, 12.0),
    "tier2": TierConfig("tier2", "Mid-major",       2,   0.0, 12.0),
    "tier3": TierConfig("tier3", "Top-org btm-15",  3, +20.0,  8.0),
    "tier4": TierConfig("tier4", "Top-org elite",   4, +45.0, 10.0),
}

# Ordered lowest to highest — used for indexing in promotion/demotion logic.
TIER_LEVELS: list[str] = ["tier0", "tier1", "tier2", "tier3", "tier4"]

# ─── Default population pyramid ───────────────────────────────────────────────
# Controls how many fighters are seeded per tier at sim start.
# Tune these to adjust roster depth; the scale= arg in generate_all_tiers
# multiplies all values uniformly (e.g. scale=0.5 halves the roster).
TIER_POPULATION: dict[str, int] = {
    "tier0": 100,   # Amateur         — large base pool
    "tier1":  70,   # Regional        — large, somewhat smaller
    "tier2":  35,   # Mid-major       — medium
    "tier3":  25,   # Top-org btm-15  — small
    "tier4":  15,   # Top-org elite   — very small; hard to earn, quick to lose
}


# ─── Generator ────────────────────────────────────────────────────────────────

def _template_natural_overall(template_name: str) -> float:
    """Mean of a template's attribute means — its 'neutral' overall at league average."""
    return _mean(m for m, _ in TEMPLATES[template_name].values())


def generate_tier_fighter(template_name: str, tier_key: str) -> Fighter:
    """
    Creates a fighter whose overall centers on the tier distribution and whose
    attribute profile preserves the template's stylistic identity.

    Composition: tier sets power level, template sets shape within that level.
    A Tier 1 and a Tier 3 Dagestan fighter both show wrestling-strong/boxing-weak
    shape; they differ only in absolute power level.
    """
    tier = TIER_CONFIG[tier_key]
    cfg  = TEMPLATES[template_name]

    natural_ovr    = _template_natural_overall(template_name)
    target_overall = random.gauss(tier.center, tier.spread)

    # relative_offset = how far each attr sits above/below the template's natural overall.
    # Adding it to target_overall re-centers the template shape at the tier's power level.
    attrs = {
        attr: target_overall + (tmpl_mean - natural_ovr) + random.gauss(0.0, ATTR_NOISE_STD)
        for attr, (tmpl_mean, _) in cfg.items()
    }

    age = max(18, min(42, int(random.gauss(27.0, 4.0))))
    return Fighter(
        name=_random_name(),
        age=age,
        region=_TEMPLATE_REGIONS[template_name],
        template=template_name,
        tier=tier_key,
        hype=_sample_hype(attrs["power"], attrs["athleticism"]),
        **attrs,
    )


def generate_all_tiers(
    per_tier: dict[str, int] | None = None,
    scale: float = 1.0,
) -> dict[str, list[Fighter]]:
    """
    Returns {tier_key: [Fighter, ...]} seeded according to the population pyramid.

    per_tier: explicit count per tier — overrides TIER_POPULATION and scale.
    scale:    multiplier on TIER_POPULATION (e.g. 0.5 halves the roster).

    Templates are distributed evenly via cycling so counts need not be multiples of 5.
    """
    if per_tier is None:
        per_tier = {t: max(5, round(TIER_POPULATION[t] * scale)) for t in TIER_LEVELS}

    pools: dict[str, list[Fighter]] = {t: [] for t in TIER_LEVELS}
    templates_list = list(TEMPLATES.keys())
    for tier_key in TIER_LEVELS:
        n = per_tier[tier_key]
        for i in range(n):
            tmpl = templates_list[i % len(templates_list)]
            pools[tier_key].append(generate_tier_fighter(tmpl, tier_key))
    return pools
