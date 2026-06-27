from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean as _mean

from fighter import Fighter
from templates import TEMPLATES, _TEMPLATE_REGIONS, _sample_hype
from academies import pick_academy, regional_name, reset_name_registry

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


# ─── Per-tier ruleset ─────────────────────────────────────────────────────────
# Governs round count and round length.  title_rounds=None means the tier has
# no championship format (Amateur).  Regional's title stays at 3 rounds — this
# is a deliberate deviation from every other pro tier, not a bug.
# round_seconds feeds into TICKS_PER_ROUND (= round_seconds // TICK_SECONDS)
# in fight_engine so it actually affects how long each simulated round runs.

@dataclass(frozen=True)
class RulesetConfig:
    non_title_rounds: int
    title_rounds: int | None   # None = no title fights at this tier
    round_seconds: int


TIER_RULESET: dict[str, RulesetConfig] = {
    "tier0": RulesetConfig(3, None, 180),   # Amateur: 3-min rounds, no title
    "tier1": RulesetConfig(3, 3,    300),   # Regional: title stays 3 rounds (deliberate)
    "tier2": RulesetConfig(3, 5,    300),   # Mid-major
    "tier3": RulesetConfig(3, 5,    300),   # Top-org btm-15
    "tier4": RulesetConfig(3, 5,    300),   # Top-org elite
}

# Weight classes in scope for Session 3. Three divisions (light/mid/heavy) were chosen to
# span the range without building all 8 yet. Add remaining five later.
# HOOK: per-template weight-class affinity (e.g. American Wrestling skewing Heavyweight)
# is explicitly deferred — that correlation belongs in a future session once partitioning
# is validated with random assignment.
# HOOK: cross-weight-class movement (weight cuts/gains + skill-transfer rules) is future
# work that deserves its own session — a weight change is NOT a 1:1 skill transfer.
WEIGHT_CLASSES: list[str] = ["lightweight", "welterweight", "heavyweight"]

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


def generate_tier_fighter(template_name: str, tier_key: str, weight_class: str = "unknown") -> Fighter:
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
    # Academy nudge shifts individual attribute centers without changing tier power target.
    academy = pick_academy(template_name)
    attrs = {
        attr: target_overall + (tmpl_mean - natural_ovr) + academy.get_nudge(attr) + random.gauss(0.0, ATTR_NOISE_STD)
        for attr, (tmpl_mean, _) in cfg.items()
    }

    age = max(18, min(42, int(random.gauss(27.0, 4.0))))
    return Fighter(
        name=regional_name(template_name),
        age=age,
        region=_TEMPLATE_REGIONS[template_name],
        template=template_name,
        tier=tier_key,
        weight_class=weight_class,
        academy=academy.name,
        hype=_sample_hype(attrs["power"], attrs["athleticism"]) + academy.pipeline_strength,
        **attrs,
    )


def generate_all_tiers(
    per_tier: dict[str, int] | None = None,
    scale: float = 1.0,
    weight_classes: list[str] | None = None,
) -> dict[str, dict[str, list[Fighter]]]:
    """
    Returns {weight_class: {tier_key: [Fighter, ...]}} seeded per-division.

    Each weight class gets its own independent pyramid — TIER_POPULATION counts
    apply per division, not in aggregate. Total fighters = sum(per_tier) * len(weight_classes).

    per_tier:       explicit per-tier count (overrides TIER_POPULATION + scale).
    scale:          multiplier on TIER_POPULATION defaults.
    weight_classes: defaults to WEIGHT_CLASSES (lightweight/welterweight/heavyweight).
    """
    reset_name_registry()
    if weight_classes is None:
        weight_classes = WEIGHT_CLASSES
    if per_tier is None:
        per_tier = {t: max(5, round(TIER_POPULATION[t] * scale)) for t in TIER_LEVELS}

    pools: dict[str, dict[str, list[Fighter]]] = {
        wc: {t: [] for t in TIER_LEVELS}
        for wc in weight_classes
    }
    templates_list = list(TEMPLATES.keys())
    for wc in weight_classes:
        for tier_key in TIER_LEVELS:
            n = per_tier[tier_key]
            for i in range(n):
                tmpl = templates_list[i % len(templates_list)]
                pools[wc][tier_key].append(generate_tier_fighter(tmpl, tier_key, wc))
    return pools
