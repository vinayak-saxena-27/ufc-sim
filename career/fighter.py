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
    # Which round the fight ended in (finishes only); None for decisions and for
    # pre-existing history recorded before this field existed.
    round_finished: int | None = None
    # Judges' decision sub-type -- "unanimous" | "split" | "majority" -- only set
    # when method == "decision". See engine/judges.py.
    decision_type: str | None = None
    # Submission category -- "choke" | "joint_lock" | "leg_lock" | "other" -- only
    # set when method == "submission". Cosmetic/reporting tag, not mechanically
    # simulated. See engine/finish_check.py SUBMISSION_TYPE_WEIGHTS.
    submission_type: str | None = None


@dataclass
class Fighter:
    name: str
    age: int
    region: str
    template: str
    tier: str = "unknown"           # current competition tier; changes on promotion/demotion
    weight_class: str = "unknown"  # "lightweight" | "welterweight" | "heavyweight"
    academy: str = ""              # assigned training camp; set at generation, stable thereafter

    # Organization affiliation (Org Identity sessions). Meaningful at tier4
    # (one of "Apex FC" / "The League" / "Eastern Grand Prix") and tier2 (one
    # of the eight mid-major orgs, see orgs/org_registry.py::MIDMAJOR_ORG_NAMES)
    # -- "" at tier0/1/3, which have no org concept. Set at generation time for
    # fighters generated directly into tier2/tier4, and at promotion/demotion
    # time for fighters who move into those tiers -- see orgs/org_registry.py::
    # assign_org() / assign_midmajor_org().
    org: str = ""
    # Session B1: remembers which mid-major org a fighter competed for when
    # they leave tier2 upward to tier3 (which stays a generic, org-less pool --
    # see orgs/org_registry.py::capture_midmajor_feed()). Consumed by
    # assign_org() when the fighter later reaches tier4, to route them toward
    # that mid-major's fed top-tier org instead of pure template weighting.
    # "" whenever not mid-promotion from a mid-major org.
    midmajor_feed_org: str = ""
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

    # Opponent-avoidance title-rematch exception (matchmaking.py) -- set once
    # by title.py immediately after a controversial title-fight loss (close
    # decision), naming the opponent this fighter is owed an immediate
    # rematch shot against. Consumed (cleared) the next time matchmaking.py
    # draws this fighter as fighter A, regardless of whether that specific
    # opponent ends up drawn -- a one-attempt permission, not a guarantee.
    # Still bounded by the hard lifetime pairing cap; only bypasses the
    # cooldown/soft-weight penalty.
    pending_rematch_opponent_name: str = ""

    # Sim day this fighter was generated (0 for the initial population;
    # replenishment/inflow fighters get the day they spawned). Diagnostic
    # anchor for "how long has this fighter existed without a real fight" --
    # fight_history alone can't distinguish a never-matched veteran of the
    # initial population from a prospect spawned last quarter.
    created_day: int = 0

    # Cached sim_day of the most recent REAL (stamped) fight, maintained by
    # engine/fight.py at result-append time; -1 = no real fight yet. Purely a
    # cache over real_fight_history's max sim_day so the idle-weighted
    # fighter-A draw (sim.py) doesn't rescan every fighter's history every
    # attempt. Idle anchor = max(created_day, last_real_fight_day).
    last_real_fight_day: int = -1

    # Consecutive main-loop cycles where this fighter was drawn as fighter A
    # but pick_opponent returned None (opponent-avoidance left no eligible
    # candidate). Incremented by the caller on each such skip, reset to 0 on
    # any successful booking. Read by pick_opponent's avoidance layer to
    # progressively relax the COOLDOWN (never the lifetime hard cap) so a
    # fighter can't lose cycle after cycle with no escalating priority.
    avoid_skip_streak: int = 0

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

    @property
    def real_fight_history(self) -> list[FightResult]:
        """fight_history entries stamped with a real sim_day -- excludes
        presim-backfilled entries (career/tiers.py::generate_presim_history)
        and any other unstamped/pre-calendar history, both of which use the
        sim_day=-1 sentinel (see FightResult.sim_day's docstring).

        Use this (not fight_history directly) for any "recent form" signal --
        rolling windows, peak-vs-current comparisons, anything meant to
        detect what a fighter has actually done LATELY in the sim. Confirmed
        the hard way: career/tiers.py's presim backfill caused 38 tier4->
        tier3 demotions in the first 300 fights of a fresh run (vs 0 without
        it) before matchmaking.py's promotion/demotion window was fixed to
        use this same filter -- fighters were being evaluated on fake losses
        that happened to fall in the trailing window, not anything that
        happened in the sim. career/labels.py and career/cuts.py have the
        same "last N fights" pattern and need the same treatment.

        Cumulative, whole-career facts (wins, losses, record_by_tier, ranking
        quality/volume) are fine reading fight_history directly -- crediting
        a backfilled career with its own record is the intended design of
        the presim feature; only RECENCY signals need this filter."""
        return [r for r in self.fight_history if r.sim_day != -1]

    def record_by_tier(self, tier: str) -> tuple[int, int]:
        """Returns (wins, losses) for fights tagged with the given tier."""
        w = sum(1 for r in self.fight_history if r.outcome == "win"  and r.tier == tier)
        l = sum(1 for r in self.fight_history if r.outcome == "loss" and r.tier == tier)
        return w, l
