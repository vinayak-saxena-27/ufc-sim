from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

# Ordered list of skill sub-attributes — used anywhere code needs to iterate them.
ATTR_NAMES: list[str] = [
    "wrestling", "bjj", "clinch",
    "boxing", "kickboxing", "power",
    "cardio", "chin", "athleticism", "fight_iq",
]


@dataclass
class FightResult:
    opponent_name: str
    outcome: Literal["win", "loss"]
    method: str         # "decision", "KO/TKO", "submission"
    org: str            # e.g. "regional_circuit", "ufc", "bellator"
    tier: str           # e.g. "tier1", "tier4" — supports tier-split record queries
    score_margin:     float = 0.0   # this fighter's total score minus opponent's; positive = winner, negative = loser
    is_title:         bool  = False  # True if this was a title fight
    rounds_completed: int   = 0     # how many rounds actually ran (for round-count verification)
    sim_day:          int   = -1    # global simulated day the fight occurred; -1 = unstamped / pre-calendar


@dataclass
class Fighter:
    name: str
    age: int
    region: str
    template: str
    tier: str = "unknown"           # current competition tier; changes on promotion/demotion
    weight_class: str = "unknown"  # "lightweight" | "welterweight" | "heavyweight"
    academy: str = ""              # assigned training camp; set at generation, stable thereafter
    # prospect_tier: assigned at generation; influences development RATE, not ceiling.
    # Fighters generated before the development session default to "developing" (retrofitted,
    # not properly assigned — their base attributes already reflect career-stage skill).
    prospect_tier: str = "developing"
    fighter_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # HOOK: weight-class affinity per template (e.g. American Wrestling skewing Heavyweight,
    # Muay Thai/SEA skewing Lightweight) is future work — deferred to a later session so the
    # partitioning logic can be validated with random assignment first.

    # All skill ratings: uncapped, zero-centered floats (0 = league average).
    # Positive = above average, negative = below average. No floor or ceiling.
    wrestling:   float = 0.0
    bjj:         float = 0.0
    clinch:      float = 0.0
    boxing:      float = 0.0
    kickboxing:  float = 0.0
    power:       float = 0.0
    cardio:      float = 0.0
    chin:        float = 0.0
    athleticism: float = 0.0
    fight_iq:    float = 0.0

    hype: float = 0.0  # Decoupled from true skill — see templates.py for generation note.

    # Accumulated development gain from training over career so far.
    # Modified by advance_all_development() (annual sweep) and apply_win_development_boost()
    # (per win). Applied at fight-resolution time via apply_development_to_fighter() —
    # base sub-attributes above are NEVER written by the development system.
    development_modifier: float = 0.0

    fight_history: list[FightResult] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)

    @property
    def overall(self) -> float:
        # Throwaway placeholder — real system needs matchup-aware overalls.
        # A striking-heavy fight vs a grappling-heavy fight should weight sub-attributes
        # differently; a single collapsed number can't serve both contexts.
        attrs = [
            self.wrestling, self.bjj, self.clinch,
            self.boxing, self.kickboxing, self.power,
            self.cardio, self.chin, self.athleticism, self.fight_iq,
        ]
        return sum(attrs) / len(attrs)

    @property
    def wins(self) -> int:
        return sum(1 for r in self.fight_history if r.outcome == "win")

    @property
    def losses(self) -> int:
        return sum(1 for r in self.fight_history if r.outcome == "loss")

    @property
    def record_str(self) -> str:
        return f"{self.wins}-{self.losses}"

    def record_by_tier(self, tier: str) -> tuple[int, int]:
        """Returns (wins, losses) for fights tagged with the given tier."""
        w = sum(1 for r in self.fight_history if r.outcome == "win"  and r.tier == tier)
        l = sum(1 for r in self.fight_history if r.outcome == "loss" and r.tier == tier)
        return w, l
