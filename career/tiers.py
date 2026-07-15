from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean as _mean

from career.fighter import Fighter, FightResult
from career.templates import TEMPLATES, _TEMPLATE_REGIONS
from career.academies import pick_academy, regional_name, reset_name_registry, Academy
from career.development import assign_prospect_tier
from career.weight_cut import generate_cut_severity
from career.style_mixing import generate_style_flexibility
from career.hype import generate_hype_seed
from engine.fight import SCALE

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
    "tier3": 130,   # Top-org btm-15  — org-less feeder pool; raised from 25 (2026-07-13,
                    # same session as tier4 below) by the same ~5.25x multiplier, since it
                    # has the same "no organic academy inflow" structural problem tier4 does.
    "tier4": 105,   # Top-org elite   — raised from 20 (2026-07-13): comparing to real UFC
                    # density (~53 active fighters/weight class in ONE org) showed the combined
                    # 3-org Elite pool was too shallow for RANKINGS_SIZE=15 to mean anything --
                    # nearly the whole roster was ranked regardless of record. New total is the
                    # sum of prestige-ratio-derived per-org targets (orgs/org_registry.py's
                    # Apex FC=10.0 / Eastern GP=7.0 / The League=4.0, anchored at Apex=50 to
                    # match real UFC scale): Apex 50 + Eastern GP 35 + The League 20 = 105.
                    # Actual per-org depth is enforced by career/replenishment.py's
                    # TIER4_ORG_FLOORS, not this total alone -- see that module for why (Apex
                    # poaching is directional, so a combined floor let The League/Eastern GP
                    # collapse even with a healthy total). Earlier note (raised 15->20 because
                    # a 5000-fight sim without run_replenishment collapses to 1-4/division and
                    # starves title-fight challenger selection) still applies at this new scale.
}


# ─── Generator ────────────────────────────────────────────────────────────────

def _template_natural_overall(template_name: str) -> float:
    """Mean of a template's attribute means — its 'neutral' overall at league average."""
    return _mean(m for m, _ in TEMPLATES[template_name].values())


# ── Pre-sim fight-history backfill (experimental, 2026-07-13) ───────────────
# Every fighter previously started with a completely blank fight_history
# regardless of age -- a fighter generated directly at tier3/tier4 at age 35
# looked, on paper, identical to a fresh 23-year-old until the SIM itself
# produced enough fights to tell them apart. That blank slate is a candidate
# root cause behind several things investigated this session (thin-looking
# rankings, "zombie" fighters with near-zero fight history sitting for years,
# fringe/losing-record ranked entries): a realistic starting population
# should already have a career's worth of history behind its older members.
# Experimental -- verify against the same metrics used to investigate those
# issues before deciding this is the right lever to pull.

_PRESIM_DEBUT_AGE: int = 18
"""Assumed age every fighter's backfilled career began, regardless of the
tier/age they were actually generated at -- matches tier0's own age center
(19), i.e. everyone is assumed to have come up through the amateur ranks."""

_PRESIM_FIGHTS_PER_YEAR: float = 2.0
"""Average fights/year over a full backfilled career. Deliberately LOWER than
career.age.FIGHTS_PER_SIM_YEAR=3 -- that rate models an established tier4
pro's active cadence once the sim is running; a full career backfill blends
in the slower amateur/regional years, so a flat career-average rate should
sit below the peak-career rate, not match it."""

_PRESIM_FIGHTS_PER_YEAR_STD: float = 0.6
"""Gaussian jitter on the per-year rate so same-age fighters don't all get
an identical backfilled fight count."""

_PRESIM_METHOD_WEIGHTS: dict[str, float] = {"decision": 0.55, "KO/TKO": 0.30, "submission": 0.15}
"""Cosmetic only (backfilled fights don't trigger hype/development/labels --
those only fire when the sim actually resolves a fight) -- just gives the
fight-history table plausible variety instead of "decision" 100% of the time."""

_PRESIM_TAPER_START_YEARS: float = 10.0
"""Years of (assumed) career before the fight-count ceiling starts tapering
below the flat _PRESIM_FIGHTS_PER_YEAR rate."""

_PRESIM_TAPER_RATE: float = 1.0
"""Fights/year credited toward the ceiling beyond _PRESIM_TAPER_START_YEARS --
lower than _PRESIM_FIGHTS_PER_YEAR because sustaining a high fight rate for
15-20 years running is the rare exception, not the norm."""

_PRESIM_HARD_CAP: int = 40
"""Absolute ceiling on backfilled fight count, any career length. A real
fighter deep into an unusually long, durable career might reach 30-40 total
fights after 15-20 years of activity -- that's the rare tail this caps
against, not something ordinary generation should produce casually."""


def _presim_fight_ceiling(years_active: float) -> int:
    """Max plausible backfilled fight count for a career this long.

    Grows linearly at _PRESIM_FIGHTS_PER_YEAR up to _PRESIM_TAPER_START_YEARS,
    then tapers to _PRESIM_TAPER_RATE beyond that (few fighters sustain a high
    cadence for 15+ years), capped absolutely at _PRESIM_HARD_CAP. Without this,
    n_fights = years_active * rate is unbounded -- a tier4 fighter generated at
    the max age clamp (37, years_active=19) with just a 2-3 sigma high `rate`
    draw could reach 60-70+ backfilled fights (and, since a strong fighter's
    win probability stays well above 50% most of the ramp, 60+ wins with it)."""
    if years_active <= _PRESIM_TAPER_START_YEARS:
        raw = years_active * _PRESIM_FIGHTS_PER_YEAR
    else:
        raw = (_PRESIM_TAPER_START_YEARS * _PRESIM_FIGHTS_PER_YEAR
               + (years_active - _PRESIM_TAPER_START_YEARS) * _PRESIM_TAPER_RATE)
    return min(_PRESIM_HARD_CAP, round(raw))


def generate_presim_history(fighter: Fighter) -> None:
    """
    Backfill a plausible pre-sim fight record (wins/losses only, no real
    opponents) based on the fighter's age and skill (.overall), so rankings/
    labels aren't reading a blank slate for a fighter who -- per their age --
    should already have a career behind them.

    Win probability per backfilled fight reuses engine.fight.SCALE's exact
    calibration, graded against an opponent baseline that RAMPS from the
    amateur tier center (their assumed debut competition) up to the
    fighter's own current tier center (their peers today) across the
    backfilled career. The original flat "league-average" (overall=0.0)
    baseline mis-graded both ends of the pyramid (matchmaking-audit
    session, measured on a fresh 1320-fighter generation): a tier0 amateur
    (center -35) won only ~13% of backfilled fights because every fight was
    scored against opposition 35 points better than anyone they'd plausibly
    have faced, so 24% of ALL fighters started with losing records
    (concentrated at low tiers); tier4 elites won ~92% (fair fights vs
    peers are closer to a coin flip). The ramp models the real shape: a
    climber dominates early weak competition, then wins closer to 50% as
    the opposition catches up to their level.

    Every backfilled FightResult uses weight_class="" and the default
    sim_day=-1 -- the SAME convention this codebase already uses for
    "pre-existing history from before a field existed" (see
    FightResult.weight_class's docstring): counted by the rankings formula
    (which explicitly includes weight_class=="" entries) and win/loss
    properties, but never treated as a real activity/inactivity signal
    (calendar-based checks like sim_calendar._last_stamped_day skip sim_day=-1).

    Draws from a LOCAL rng (seeded by a single draw off the shared global
    stream) rather than the global `random` module directly. This function's
    internal draw count varies per fighter (proportional to backfilled fight
    count) -- pulling from the global stream directly means any change to
    that count (e.g. the ceiling below) reshuffles every fighter generated
    afterward in the same run, cascading through the rest of that population
    and any global-seed-dependent test built on top of it. A local rng
    bounds this function's "blast radius" on the shared stream to exactly
    one draw, independent of internal complexity, while staying fully
    deterministic under a fixed global seed.
    """
    local_rng = random.Random(random.random())
    years_active = max(0.0, fighter.age - _PRESIM_DEBUT_AGE)
    rate = max(0.0, local_rng.gauss(_PRESIM_FIGHTS_PER_YEAR, _PRESIM_FIGHTS_PER_YEAR_STD))
    n_fights = min(round(years_active * rate), _presim_fight_ceiling(years_active))
    if n_fights <= 0:
        return

    methods = list(_PRESIM_METHOD_WEIGHTS.keys())
    method_weights = list(_PRESIM_METHOD_WEIGHTS.values())

    start_baseline = TIER_CONFIG["tier0"].center
    end_baseline   = TIER_CONFIG.get(fighter.tier, TIER_CONFIG["tier0"]).center
    for i in range(n_fights):
        frac = i / max(1, n_fights - 1)
        opp_baseline = start_baseline + frac * (end_baseline - start_baseline)
        win_p = 1.0 / (1.0 + 10.0 ** (-(fighter.overall - opp_baseline) / SCALE))
        outcome = "win" if local_rng.random() < win_p else "loss"
        fighter.fight_history.append(FightResult(
            opponent_name="Uncredited Opponent",
            outcome=outcome,
            method=local_rng.choices(methods, weights=method_weights, k=1)[0],
            org="",
            tier=fighter.tier,
            rounds_completed=3,
        ))


def generate_tier_fighter(
    template_name: str,
    tier_key: str,
    weight_class: str = "unknown",
    *,
    academy: Academy | None = None,
    forced_org: str | None = None,
    include_presim_history: bool = True,
) -> Fighter:
    """
    Creates a fighter whose overall centers on the tier distribution and whose
    attribute profile preserves the template's stylistic identity.

    Composition: tier sets power level, template sets shape within that level.
    A Tier 1 and a Tier 3 Dagestan fighter both show wrestling-strong/boxing-weak
    shape; they differ only in absolute power level.

    academy: if supplied (e.g. by replenishment.py for per-academy generation),
             that specific academy is used instead of a random pick. Existing
             callers omit this and get the original random-selection behavior.
    forced_org: tier4 only. If supplied, assigns this org directly instead of
             the weighted-random assign_org() -- used by replenishment.py's
             per-org backstop to refill a SPECIFIC org's deficit rather than
             letting generic template weighting (which skews Apex-heavy) decide.
    include_presim_history: whether to backfill a fabricated pre-sim career
             (see generate_presim_history). Defaults True for the initial
             day-0 population (generate_all_tiers) and every other existing
             caller. career/replenishment.py passes False for tier0 mid-run
             spawns (academy prospects, tier0 backstop refills) -- true
             debuting rookies should start blank, not with a fake career
             (matchmaking-improvement Phase 2, presim-scope-bug fix).
             tier1-tier4 mid-run spawns (backstop refills into an
             already-established tier, laterals) still get one -- an age-30
             "Elite" fighter with zero fights is exactly as implausible as
             the lateral-transfer case this feature was built to fix.
    """
    tier = TIER_CONFIG[tier_key]
    cfg  = TEMPLATES[template_name]

    natural_ovr    = _template_natural_overall(template_name)
    target_overall = random.gauss(tier.center, tier.spread)

    # relative_offset = how far each attr sits above/below the template's natural overall.
    # Adding it to target_overall re-centers the template shape at the tier's power level.
    # Academy nudge shifts individual attribute centers without changing tier power target.
    if academy is None:
        academy = pick_academy(template_name)
    attrs = {
        attr: target_overall + (tmpl_mean - natural_ovr) + academy.get_nudge(attr) + random.gauss(0.0, ATTR_NOISE_STD)
        for attr, (tmpl_mean, _) in cfg.items()
    }

    # Per-tier starting age: Amateur fighters are brand-new (18-23); higher tiers
    # have earned their way up and thus skew older.  Clamps prevent extreme outliers.
    # All are first-pass estimates; tune once realistic age distributions are observed.
    _age_params: dict[str, tuple[float, float, int, int]] = {
        # tier_key: (gauss_mean, gauss_std, min_clamp, max_clamp)
        "tier0": (19.0, 1.5, 18, 23),   # Amateur:      fresh 18–23 year olds
        "tier1": (22.0, 2.0, 18, 27),   # Regional:     early career, some experience
        "tier2": (25.0, 2.5, 20, 31),   # Mid-major:    mid-career
        "tier3": (28.0, 2.5, 22, 34),   # Top-org btm:  experienced, made the big show
        "tier4": (30.0, 3.0, 23, 37),   # Elite:        peak careers, some veterans
    }
    _ap = _age_params.get(tier_key, (27.0, 4.0, 18, 42))
    age = max(_ap[2], min(_ap[3], int(random.gauss(_ap[0], _ap[1]))))
    # Local import (same pattern as the org assignments below) -- avoids a
    # tiers.py <-> sim_calendar import-order dependency at module load.
    from sim_calendar import get_sim_day
    fighter = Fighter(
        name=regional_name(template_name),
        age=age,
        region=_TEMPLATE_REGIONS[template_name],
        template=template_name,
        tier=tier_key,
        weight_class=weight_class,
        academy=academy.name,
        prospect_tier=assign_prospect_tier(),
        hype=generate_hype_seed(academy.pipeline_strength),
        cut_severity=generate_cut_severity(attrs["power"], attrs["athleticism"], weight_class),
        style_flexibility=generate_style_flexibility(attrs["fight_iq"], template_name),
        created_day=get_sim_day(),
        **attrs,
    )
    if include_presim_history:
        generate_presim_history(fighter)
    # Org Identity sessions: fighters generated directly into tier2 (mid-major)
    # or tier4 (initial population pyramid) need an org immediately, same as
    # fighters who PROMOTE into those tiers mid-sim (see
    # matchmaking.apply_tier_transitions) -- otherwise the initial pyramid's
    # tier2/tier4 fighters would sit org-less until natural promotions/
    # demotions eventually replace them. Local import avoids a tiers.py <->
    # orgs.org_registry import-order dependency at module load.
    if tier_key == "tier1":
        from orgs.org_registry import assign_regional_org
        assign_regional_org(fighter)
        fighter.org_start_day = 0
    elif tier_key == "tier2":
        from orgs.org_registry import assign_midmajor_org
        assign_midmajor_org(fighter)
        fighter.org_start_day = 0
    elif tier_key == "tier4":
        if forced_org is not None:
            fighter.org = forced_org
        else:
            from orgs.org_registry import assign_org
            assign_org(fighter)
        fighter.org_start_day = 0
    return fighter


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
