from __future__ import annotations

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
    method: str   # "decision", "KO/TKO", "submission" — placeholder until real engine
    org: str      # e.g. "regional_circuit", "ufc", "bellator"
    tier: str     # e.g. "regional", "contender", "elite" — supports tier-split record queries


@dataclass
class Fighter:
    name: str
    age: int
    region: str
    template: str
    tier: str = "unknown"           # current competition tier; changes on promotion/demotion
    weight_class: str = "unknown"  # "lightweight" | "welterweight" | "heavyweight"
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

    fight_history: list[FightResult] = field(default_factory=list)

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
