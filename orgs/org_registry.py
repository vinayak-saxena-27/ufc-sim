"""
org_registry.py -- Organization entities (Org Identity, Sessions A + B1).

## Top-tier orgs (Session A)

Three named top-tier orgs, all tier4, replacing the single generic Elite pool
with distinct identities/formats/cultures:

  Apex FC              -- standard format, round_by_round(*) scoring, KO/finish
                          culture, North American base. The dominant global org.
  The League           -- season/playoff tournament format, round_by_round(*)
                          scoring, prize money, North American base.
  Eastern Grand Prix    -- standard format, WHOLE-FIGHT scoring, multi-discipline
                          striking-art prestige culture, Asia base.

(*) "round_by_round" here means Apex FC/The League use the round-by-round
win-counting decision path (engine/fight_engine.py's decision_mode="round_count")
built in Session A -- see that module's docstring for why this is the "new"
path rather than Eastern GP's, which reuses the engine's pre-existing
whole-fight total-score-sum decision logic essentially unchanged. Mid-major
orgs (below) use this same "round_count" path -- there is no whole-fight
scoring variant below the top tier.

## Mid-major orgs (Session B1)

Eight tier2 orgs, each primarily (and secondarily) feeding ONE of the three
top-tier orgs above. Regional org entities (tier1 and below) are explicitly
out of scope -- Session B2. `primary_feed_from` on the MID-MAJOR orgs is left
empty here (populated once Session B2 builds regional orgs); `primary_feed_from`
on the TOP-TIER orgs is now populated below with the mid-majors that feed them.

Notable asymmetry (not a bug): NO mid-major org has The League as its PRIMARY
feed target -- Contender Series FC/Titan/Gladius/African Warriors feed Apex FC
primary with The League only as their secondary, and Far East Circuit feeds
Eastern GP primary with The League secondary. The League's
`primary_feed_from` is therefore an empty list; it's a common SECONDARY
destination instead. This matches The League's mid-pack prestige (4.0, between
Apex's 10.0 and Eastern GP's 7.0) and Session A's finding that it's the org
fighters get poached AWAY from, not funneled toward.

## Prestige

Uncapped, zero-centered floats. Top-tier: Apex FC 10.0 > Eastern GP 7.0 >
The League 4.0 (Session A). Mid-major: all clearly below top-tier, small
differences between them -- Contender Series FC and Vanguard MMA (established
feeders, per spec) at 2.0, the remaining six at 1.0. Not currently consumed by
any mechanic (same as Session A) -- reserved for a future prestige-weighted
mechanic.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from career.fighter import Fighter

APEX_FC_NAME:    str = "Apex FC"
THE_LEAGUE_NAME: str = "The League"
EASTERN_GP_NAME: str = "Eastern Grand Prix"

ORG_NAMES: list[str] = [APEX_FC_NAME, THE_LEAGUE_NAME, EASTERN_GP_NAME]

# Mid-major org names (Session B1) -- defined before the Org instances below
# so top-tier orgs' primary_feed_from can reference them directly.
CONTENDER_SERIES_FC_NAME:  str = "Contender Series FC"
TITAN_FC_NAME:             str = "Titan Fighting Championship"
VANGUARD_MMA_NAME:         str = "Vanguard MMA"
GLADIUS_FC_NAME:           str = "Gladius FC"
AFRICAN_WARRIORS_NAME:     str = "African Warriors Championship"
GULF_COMBAT_SERIES_NAME:   str = "Gulf Combat Series"
SOUTH_ASIA_COMBAT_NAME:    str = "South Asia Combat League"
FAR_EAST_CIRCUIT_NAME:     str = "Far East Circuit"

MIDMAJOR_ORG_NAMES: list[str] = [
    CONTENDER_SERIES_FC_NAME, TITAN_FC_NAME, VANGUARD_MMA_NAME, GLADIUS_FC_NAME,
    AFRICAN_WARRIORS_NAME, GULF_COMBAT_SERIES_NAME, SOUTH_ASIA_COMBAT_NAME,
    FAR_EAST_CIRCUIT_NAME,
]


@dataclass(frozen=True)
class Org:
    name:               str
    tier:               str                # "tier4" (top-tier) or "tier2" (mid-major)
    format:             str                # "standard" | "tournament" | "whole_fight"
    scoring:            str                # "round_by_round" | "whole_fight"
    hype_culture:       dict[str, float]   # per-method hype modifiers -- career/hype.py Part 5
    prestige:           float              # uncapped, zero-centered; first-pass
    primary_feed_from:  list[str] = field(default_factory=list)
    # Mid-major-only (Session B1): which top-tier org this org primarily/
    # secondarily feeds into via promotion. "" on the three top-tier orgs
    # themselves (they're the destination, not a source).
    primary_feeds_to:   str = ""
    secondary_feeds_to: str = ""


# ── Part 5 hype-culture dicts (consumed by career/hype.py's org modifier) ────
# Keys are documented per-org in career/hype.py alongside the modifier function
# that reads them; kept here as the data half of the "org entity owns its
# culture" design (hype.py owns the interpretation/application logic).

APEX_HYPE_CULTURE: dict[str, float] = {
    "ko_tko_bonus":        0.20,   # +20% on top of existing finish hype gain
    "decision_tax":       -0.05,   # -5% boring-fight tax
}

LEAGUE_HYPE_CULTURE: dict[str, float] = {
    "playoff_win_bonus":   0.15,   # +15% for a win in a playoff/championship fight
}

EASTERN_GP_HYPE_CULTURE: dict[str, float] = {
    "striker_style_bonus": 0.10,   # +10% on ALL hype gains for kickboxing/clinch stylists
    "submission_bonus":    0.10,   # +10% for submission wins
    "wrestling_decision_tax": -0.05,  # -5% for pure-wrestling dominant decisions
}

MIDMAJOR_HYPE_CULTURE: dict[str, float] = {}
"""Neutral -- mid-major org hype culture is explicitly deferred to a future
session (spec: 'can be refined later'). career.hype._org_culture_multiplier
only special-cases Apex FC/Eastern GP by name, so any org not matching those
names (all eight mid-majors) already gets multiplier=1.0 with zero extra code."""


APEX_FC = Org(
    name=APEX_FC_NAME, tier="tier4", format="standard", scoring="round_by_round",
    hype_culture=APEX_HYPE_CULTURE, prestige=10.0,
    primary_feed_from=[
        CONTENDER_SERIES_FC_NAME, TITAN_FC_NAME, VANGUARD_MMA_NAME,
        GLADIUS_FC_NAME, AFRICAN_WARRIORS_NAME,
    ],
)
THE_LEAGUE = Org(
    name=THE_LEAGUE_NAME, tier="tier4", format="tournament", scoring="round_by_round",
    hype_culture=LEAGUE_HYPE_CULTURE, prestige=4.0,
    primary_feed_from=[],   # see module docstring -- no mid-major primarily feeds The League
)
EASTERN_GP = Org(
    name=EASTERN_GP_NAME, tier="tier4", format="standard", scoring="whole_fight",
    hype_culture=EASTERN_GP_HYPE_CULTURE, prestige=7.0,
    primary_feed_from=[GULF_COMBAT_SERIES_NAME, SOUTH_ASIA_COMBAT_NAME, FAR_EAST_CIRCUIT_NAME],
)

ORGS: dict[str, Org] = {
    APEX_FC_NAME:    APEX_FC,
    THE_LEAGUE_NAME: THE_LEAGUE,
    EASTERN_GP_NAME: EASTERN_GP,
}

# ── Mid-major org entities (Session B1) ──────────────────────────────────────
# format/scoring are uniform across all eight ("standard"/"round_by_round" --
# tournament format and whole-fight scoring are top-tier-only this session).
# prestige: Contender Series FC & Vanguard MMA (established feeders) = 2.0;
# the rest = 1.0. All clearly below the top tier's 4.0-10.0 range.

CONTENDER_SERIES_FC = Org(
    name=CONTENDER_SERIES_FC_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=2.0,
    primary_feeds_to=APEX_FC_NAME, secondary_feeds_to=THE_LEAGUE_NAME,
)
TITAN_FC = Org(
    name=TITAN_FC_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=APEX_FC_NAME, secondary_feeds_to=THE_LEAGUE_NAME,
)
VANGUARD_MMA = Org(
    name=VANGUARD_MMA_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=2.0,
    primary_feeds_to=APEX_FC_NAME, secondary_feeds_to=EASTERN_GP_NAME,
)
GLADIUS_FC = Org(
    name=GLADIUS_FC_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=APEX_FC_NAME, secondary_feeds_to=THE_LEAGUE_NAME,
)
AFRICAN_WARRIORS = Org(
    name=AFRICAN_WARRIORS_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=APEX_FC_NAME, secondary_feeds_to=THE_LEAGUE_NAME,
)
GULF_COMBAT_SERIES = Org(
    name=GULF_COMBAT_SERIES_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=EASTERN_GP_NAME, secondary_feeds_to=APEX_FC_NAME,
)
SOUTH_ASIA_COMBAT_LEAGUE = Org(
    name=SOUTH_ASIA_COMBAT_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=EASTERN_GP_NAME, secondary_feeds_to=APEX_FC_NAME,
)
FAR_EAST_CIRCUIT = Org(
    name=FAR_EAST_CIRCUIT_NAME, tier="tier2", format="standard", scoring="round_by_round",
    hype_culture=MIDMAJOR_HYPE_CULTURE, prestige=1.0,
    primary_feeds_to=EASTERN_GP_NAME, secondary_feeds_to=THE_LEAGUE_NAME,
)

MIDMAJOR_ORGS: dict[str, Org] = {
    CONTENDER_SERIES_FC_NAME: CONTENDER_SERIES_FC,
    TITAN_FC_NAME:            TITAN_FC,
    VANGUARD_MMA_NAME:        VANGUARD_MMA,
    GLADIUS_FC_NAME:          GLADIUS_FC,
    AFRICAN_WARRIORS_NAME:    AFRICAN_WARRIORS,
    GULF_COMBAT_SERIES_NAME:  GULF_COMBAT_SERIES,
    SOUTH_ASIA_COMBAT_NAME:   SOUTH_ASIA_COMBAT_LEAGUE,
    FAR_EAST_CIRCUIT_NAME:    FAR_EAST_CIRCUIT,
}


def decision_mode_for_org(org: str) -> str:
    """Maps an org name to engine/fight_engine.py's decision_mode kwarg.
    Unknown/empty org falls back to "round_count" (matches Apex FC/The
    League's shape, i.e. the ordinary case) -- only Eastern GP is special.
    Mid-major org names also fall through to "round_count" here -- correct,
    since mid-majors use the same round-by-round scoring as Apex FC/The League."""
    return "total_score" if org == EASTERN_GP_NAME else "round_count"


# ── Part 1: per-template TOP-TIER org-assignment weighting (Session A) ──────
# First-pass estimates. Dagestan/American Wrestling templates lean Apex FC
# (dominant, North-American-based finish culture fits wrestle-heavy/finish-
# oriented styles); Muay Thai/SEA Mixed lean Eastern GP (striking-art prestige
# culture matches their kickboxing/clinch identity); Brazilian has no strong
# regional pull and is weighted toward Apex FC as the default dominant org
# with a secondary chance elsewhere. Every template retains a nonzero chance
# at every org -- the template only weights probability, it never hard-gates.

TEMPLATE_ORG_WEIGHTS: dict[str, dict[str, float]] = {
    "dagestan_sambo":     {APEX_FC_NAME: 0.65, THE_LEAGUE_NAME: 0.20, EASTERN_GP_NAME: 0.15},
    "american_wrestling": {APEX_FC_NAME: 0.65, THE_LEAGUE_NAME: 0.20, EASTERN_GP_NAME: 0.15},
    "muay_thai":          {APEX_FC_NAME: 0.25, THE_LEAGUE_NAME: 0.15, EASTERN_GP_NAME: 0.60},
    "sea_mixed":          {APEX_FC_NAME: 0.25, THE_LEAGUE_NAME: 0.15, EASTERN_GP_NAME: 0.60},
    "brazilian":          {APEX_FC_NAME: 0.55, THE_LEAGUE_NAME: 0.25, EASTERN_GP_NAME: 0.20},
}

_DEFAULT_ORG_WEIGHTS: dict[str, float] = {
    APEX_FC_NAME: 0.55, THE_LEAGUE_NAME: 0.25, EASTERN_GP_NAME: 0.20,
}
"""Fallback for templates not in TEMPLATE_ORG_WEIGHTS (e.g. crossover/lateral
fighters whose `template` field is a "crossover_{sport}" tag -- see inflow.py).
Apex-dominant, same shape as the Brazilian default."""

# ── Part 6 (Session B1): mid-major feed-routing weights ─────────────────────
# When a fighter arrives at tier4 carrying a midmajor_feed_org (see
# capture_midmajor_feed below), this REPLACES the generic template-based
# weights above with a feed-routing distribution: strong pull toward the
# mid-major's primary_feeds_to, a real but smaller chance at secondary_feeds_to,
# and a small residual chance at the third (unfed) org -- consistent with this
# project's "template/feed only weights probability, never hard-gates" rule.

FEED_PRIMARY_PROB:   float = 0.60
FEED_SECONDARY_PROB: float = 0.25
"""Remaining probability (1 - PRIMARY - SECONDARY = 0.15) goes to the third,
non-fed top-tier org."""


# ── Part 2 (Session B1): per-template MID-MAJOR org-assignment weighting ────
# Contender Series FC/Titan Fighting Championship: American Wrestling-heavy.
# Vanguard MMA/Gladius FC: Brazilian-heavy (Vanguard also Dagestan/Sambo).
# African Warriors Championship: Brazilian (BJJ culture) + American Wrestling
#   (wrestling base) -- "new" org per spec, built from existing templates only.
# Gulf Combat Series: Dagestan/Sambo-heavy.
# South Asia Combat League: Dagestan/Sambo (wrestling culture) + SEA Mixed
#   (striking) -- "new" org per spec, same existing-templates-only approach.
# Far East Circuit: Muay Thai + SEA Mixed-heavy.
# Every template retains a nonzero chance at every mid-major org.

TEMPLATE_MIDMAJOR_ORG_WEIGHTS: dict[str, dict[str, float]] = {
    "dagestan_sambo": {
        CONTENDER_SERIES_FC_NAME: 0.06, TITAN_FC_NAME: 0.06, VANGUARD_MMA_NAME: 0.25,
        GLADIUS_FC_NAME: 0.06, AFRICAN_WARRIORS_NAME: 0.06, GULF_COMBAT_SERIES_NAME: 0.25,
        SOUTH_ASIA_COMBAT_NAME: 0.20, FAR_EAST_CIRCUIT_NAME: 0.06,
    },
    "american_wrestling": {
        CONTENDER_SERIES_FC_NAME: 0.25, TITAN_FC_NAME: 0.25, VANGUARD_MMA_NAME: 0.05,
        GLADIUS_FC_NAME: 0.05, AFRICAN_WARRIORS_NAME: 0.25, GULF_COMBAT_SERIES_NAME: 0.05,
        SOUTH_ASIA_COMBAT_NAME: 0.05, FAR_EAST_CIRCUIT_NAME: 0.05,
    },
    "brazilian": {
        CONTENDER_SERIES_FC_NAME: 0.175, TITAN_FC_NAME: 0.075, VANGUARD_MMA_NAME: 0.175,
        GLADIUS_FC_NAME: 0.175, AFRICAN_WARRIORS_NAME: 0.175, GULF_COMBAT_SERIES_NAME: 0.075,
        SOUTH_ASIA_COMBAT_NAME: 0.075, FAR_EAST_CIRCUIT_NAME: 0.075,
    },
    "muay_thai": {
        CONTENDER_SERIES_FC_NAME: 0.07, TITAN_FC_NAME: 0.07, VANGUARD_MMA_NAME: 0.07,
        GLADIUS_FC_NAME: 0.07, AFRICAN_WARRIORS_NAME: 0.07, GULF_COMBAT_SERIES_NAME: 0.07,
        SOUTH_ASIA_COMBAT_NAME: 0.07, FAR_EAST_CIRCUIT_NAME: 0.51,
    },
    "sea_mixed": {
        CONTENDER_SERIES_FC_NAME: 0.05, TITAN_FC_NAME: 0.05, VANGUARD_MMA_NAME: 0.05,
        GLADIUS_FC_NAME: 0.05, AFRICAN_WARRIORS_NAME: 0.05, GULF_COMBAT_SERIES_NAME: 0.05,
        SOUTH_ASIA_COMBAT_NAME: 0.35, FAR_EAST_CIRCUIT_NAME: 0.35,
    },
}

_DEFAULT_MIDMAJOR_ORG_WEIGHTS: dict[str, float] = {org: 1.0 / len(MIDMAJOR_ORG_NAMES) for org in MIDMAJOR_ORG_NAMES}
"""Uniform fallback for templates not in TEMPLATE_MIDMAJOR_ORG_WEIGHTS
(crossover/lateral fighters) -- same rationale as _DEFAULT_ORG_WEIGHTS, but
uniform rather than Apex-skewed since there's no single 'dominant' mid-major."""


def assign_org(fighter: "Fighter") -> str:
    """Weighted-random TOP-TIER org assignment for a fighter entering tier4
    (generation or promotion). Mutates fighter.org and returns the assigned
    name. Does NOT set org_start_day -- callers set that from their own
    sim-day context.

    Session B1: if the fighter is carrying a midmajor_feed_org (set by
    capture_midmajor_feed when they left mid-major tier for tier3), that
    feed-routing distribution REPLACES the generic template-based weights --
    a fighter fed by Contender Series FC lands at Apex FC/The League per that
    org's primary/secondary_feeds_to, not by generic template weighting. The
    feed marker is consumed (cleared) here whether or not it resolves to a
    real mid-major org.
    """
    feed_name = getattr(fighter, "midmajor_feed_org", "")
    if feed_name:
        fighter.midmajor_feed_org = ""
        feed_org = MIDMAJOR_ORGS.get(feed_name)
        if feed_org is not None:
            remaining = [o for o in ORG_NAMES if o not in (feed_org.primary_feeds_to, feed_org.secondary_feeds_to)]
            residual = max(0.0, 1.0 - FEED_PRIMARY_PROB - FEED_SECONDARY_PROB)
            names = [feed_org.primary_feeds_to, feed_org.secondary_feeds_to] + remaining
            probs = [FEED_PRIMARY_PROB, FEED_SECONDARY_PROB] + [residual / len(remaining)] * len(remaining) if remaining else [FEED_PRIMARY_PROB, FEED_SECONDARY_PROB]
            org = random.choices(names, weights=probs, k=1)[0]
            fighter.org = org
            return org

    weights = TEMPLATE_ORG_WEIGHTS.get(fighter.template, _DEFAULT_ORG_WEIGHTS)
    names   = list(weights.keys())
    probs   = list(weights.values())
    org     = random.choices(names, weights=probs, k=1)[0]
    fighter.org = org
    return org


def assign_midmajor_org(fighter: "Fighter") -> str:
    """Weighted-random MID-MAJOR org assignment for a fighter entering tier2
    (generation, promotion from tier1, or demotion from tier3). Mutates
    fighter.org and returns the assigned name. Does NOT set org_start_day."""
    weights = TEMPLATE_MIDMAJOR_ORG_WEIGHTS.get(fighter.template, _DEFAULT_MIDMAJOR_ORG_WEIGHTS)
    names   = list(weights.keys())
    probs   = list(weights.values())
    org     = random.choices(names, weights=probs, k=1)[0]
    fighter.org = org
    return org


def capture_midmajor_feed(fighter: "Fighter") -> None:
    """Call when a fighter LEAVES mid-major tier (tier2) upward to tier3.
    tier3 ('Top-org btm-15') stays a generic, org-less pool (unchanged from
    Session A) -- but the mid-major org they came from is remembered on
    fighter.midmajor_feed_org so that assign_org() can route them toward that
    org's fed top-tier destination once they eventually reach tier4, instead
    of falling back to pure template-based weighting. fighter.org is cleared
    since tier3 itself has no org concept."""
    fighter.midmajor_feed_org = fighter.org
    fighter.org = ""
