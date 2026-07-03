"""
org_registry.py -- Top-tier organization entities (Org Identity, Session A).

Three named top-tier orgs, all tier4, replacing the single generic Elite pool
with distinct identities/formats/cultures:

  Apex FC              -- standard format, round_by_round(*) scoring, KO/finish
                          culture, North American base. The dominant global org.
  The League           -- season/playoff tournament format, round_by_round(*)
                          scoring, prize money, North American base.
  Eastern Grand Prix    -- standard format, WHOLE-FIGHT scoring, multi-discipline
                          striking-art prestige culture, Asia base.

(*) "round_by_round" here means Apex FC/The League use the NEW round-by-round
win-counting decision path (engine/fight_engine.py's decision_mode="round_count")
built this session -- see that module's docstring for why this is the "new"
path rather than Eastern GP's, which reuses the engine's pre-existing
whole-fight total-score-sum decision logic essentially unchanged.

Mid-major/regional org entities (Contender Series FC, Vanguard MMA, etc.) are
explicitly out of scope this session (Session B) -- primary_feed_from is left
as an empty list on all three orgs, flagged below for that future session to
populate.

## Prestige

Uncapped, zero-centered floats (same rating philosophy as the rest of the sim).
First-pass, ordered per spec: Apex FC highest, Eastern GP second, The League
third. Not currently consumed by any mechanic this session (poaching/departure
logic in orgs/org_movement.py uses org identity directly, not this number) --
reserved for a future cross-org prestige-weighted mechanic.
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


@dataclass(frozen=True)
class Org:
    name:               str
    tier:               str                # all three are "tier4" this session
    format:             str                # "standard" | "tournament" | "whole_fight"
    scoring:            str                # "round_by_round" | "whole_fight"
    hype_culture:       dict[str, float]   # per-method hype modifiers -- career/hype.py Part 5
    prestige:           float              # uncapped, zero-centered; first-pass
    primary_feed_from:  list[str] = field(default_factory=list)  # populated in Session B


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


APEX_FC = Org(
    name=APEX_FC_NAME, tier="tier4", format="standard", scoring="round_by_round",
    hype_culture=APEX_HYPE_CULTURE, prestige=10.0, primary_feed_from=[],
)
THE_LEAGUE = Org(
    name=THE_LEAGUE_NAME, tier="tier4", format="tournament", scoring="round_by_round",
    hype_culture=LEAGUE_HYPE_CULTURE, prestige=4.0, primary_feed_from=[],
)
EASTERN_GP = Org(
    name=EASTERN_GP_NAME, tier="tier4", format="standard", scoring="whole_fight",
    hype_culture=EASTERN_GP_HYPE_CULTURE, prestige=7.0, primary_feed_from=[],
)

ORGS: dict[str, Org] = {
    APEX_FC_NAME:    APEX_FC,
    THE_LEAGUE_NAME: THE_LEAGUE,
    EASTERN_GP_NAME: EASTERN_GP,
}


def decision_mode_for_org(org: str) -> str:
    """Maps an org name to engine/fight_engine.py's decision_mode kwarg.
    Unknown/empty org falls back to "round_count" (matches Apex FC/The
    League's shape, i.e. the ordinary case) -- only Eastern GP is special."""
    return "total_score" if org == EASTERN_GP_NAME else "round_count"


# ── Part 1: per-template org-assignment weighting ────────────────────────────
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


def assign_org(fighter: "Fighter") -> str:
    """Weighted-random org assignment for a fighter entering tier4 (generation
    or promotion). Mutates fighter.org and returns the assigned name. Does NOT
    set org_start_day -- callers set that from their own sim-day context."""
    weights = TEMPLATE_ORG_WEIGHTS.get(fighter.template, _DEFAULT_ORG_WEIGHTS)
    names   = list(weights.keys())
    probs   = list(weights.values())
    org     = random.choices(names, weights=probs, k=1)[0]
    fighter.org = org
    return org
