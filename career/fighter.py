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
    # Phase-distribution summary for this fight (seconds), shared by both fighters —
    # phase is a single shared timeline, not independent per fighter. Populated by
    # engine/fight.py from FightOutcome.rounds; consumed by the style-mixing
    # development feedback hook (career/development.py).
    time_standing:    float = 0.0
    time_clinch:      float = 0.0
    time_ground:      float = 0.0
    # True if the WINNER survived meaningful finish-danger (received pressure
    # crossed a threshold fraction at some point) and still won. Winner's record
    # only -- a loser's FightResult never sets this. See engine/fight.py and
    # career/hype.py (style-to-hype overhaul session).
    adversity_comeback: bool = False
    # Weight class the fight took place in. Empty string = pre-existing fight
    # history recorded before this field existed (whatever division the fighter
    # was in at the time -- see rankings.py's backward-compat handling).
    weight_class: str = ""


@dataclass
class Fighter:
    name: str
    age: int
    region: str
    template: str
    tier: str = "unknown"           # current competition tier; changes on promotion/demotion
    weight_class: str = "unknown"  # "lightweight" | "welterweight" | "heavyweight"
    academy: str = ""              # assigned training camp; set at generation, stable thereafter

    # Top-tier organization affiliation (Org Identity session). Only meaningful at
    # tier4 -- "" for every other tier. One of "Apex FC" / "The League" /
    # "Eastern Grand Prix" once assigned. Set at generation time for fighters
    # generated directly into tier4, and at promotion time for fighters who rise
    # into tier4 -- see orgs/org_registry.py::assign_org().
    org: str = ""
    # Sim day the fighter joined their CURRENT org (generation day, or the day of
    # a promotion/move/poach). Drives the org-movement tenure gate
    # (MIN_TENURE_BEFORE_POACH in orgs/org_movement.py).
    org_start_day: int = -1
    # True if this fighter arrived at their current org already ranked (top-15)
    # at a comparable top-tier promotion (Eastern GP or The League) at the moment
    # of the move -- Part 7's matchmaking-gate exception. Set once by
    # orgs/org_movement.py on an Apex FC signing; never cleared afterward (it
    # remains a true historical fact even once the fighter naturally qualifies
    # via the other gate conditions).
    org_arrived_pre_ranked: bool = False
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

    # Voluntary style-mixing personality trait: willingness to operate outside the
    # dominant phase even when able to pursue it. Uncapped, zero-centered — see
    # career/style_mixing.py for the generation formula. High positive = diverse
    # stylist; near zero = situational; low/negative = deliberate specialist.
    style_flexibility: float = 0.0

    # Accumulated development gain from training over career so far.
    # Modified by advance_all_development() (annual sweep) and apply_win_development_boost()
    # (per win). Applied at fight-resolution time via apply_development_to_fighter() —
    # base sub-attributes above are NEVER written by the development system.
    development_modifier: float = 0.0

    # Relative walk-around-weight attribute: how hard this fighter's cut to their
    # current weight_class is. Zero-centered, uncapped — see weight_cut.py for the
    # generation formula and the fight-night performance modifier it drives.
    # Set at generation time by generate_tier_fighter(); recalibrated on an
    # executed weight-class move by weight_transfers.py (Flex Session C).
    cut_severity: float = 0.0

    # Pending weight-class movement flags — set periodically by
    # weight_movement.evaluate_weight_class_move(), fully recomputed (not sticky)
    # each evaluation cycle. Consumed (and cleared) by weight_transfers.py
    # (Flex Session C), which executes the actual pool transfer these flags request.
    weight_class_move_candidate: bool = False
    weight_class_move_direction: str | None = None   # "up" | "down" | None
    weight_class_move_reason:    str | None = None   # "title_ambition" | "struggling" | "cut_damage" | "opportunity" | None
    weight_class_move_target:    str | None = None   # resolved target weight_class, or None

    # Most-recently-EXECUTED move tracking (weight_transfers.py, Flex Session C) —
    # distinct from the pending flags above, which get cleared once a move fires.
    # Drives Part 3's opportunity hype-boost window.
    last_move_reason:               str | None = None
    last_move_fight_count:          int  = 0       # len(fight_history) at the time of the move
    opportunity_hype_boost_applied: bool = False   # guards the one-time Part 3 boost

    # Win-and-vacate campaign state (weight_transfers.py Part 2) — set only while
    # an elite, entrenched champion is actively campaigning for a second belt in
    # the adjacent division without leaving their home division.
    campaign_active:           bool = False
    campaign_weight_class:     str | None = None   # the ADJACENT division being campaigned
    campaign_fights_remaining: int  = 0
    campaign_wins:              int  = 0

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
