"""
phase_engine.py — Round-level phase/transition simulation (Session 4a Part 1+Fix).

Simulates one 5-minute round as a contested phase timeline. Three phases
(STANDING / CLINCH / GROUND) emerge from sub-attribute contests, not from
random per-round selection. No damage, scoring, or finish logic here — those
come in the next session when this module is wired into simulate_fight().

Fixes applied in session 4a-fix:
  A. Absolute-skill floor on attempt frequency (_p_attempt now multiplies by a
     sigmoid of the attacker's own absolute skill, suppressing attempt rates for
     fighters who are simply bad at the relevant action regardless of opponent).
  B. GROUND phase tracks top/bottom position. Only the BOTTOM fighter can
     initiate SCRAMBLE; the top fighter's attributes serve as defense.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum

from career.fighter import Fighter
from engine.fight import SCALE  # shared logistic steepness constant

# Sub-attribute gaps are comparable in magnitude to overall gaps (template means
# range from -22 to +25), so reusing SCALE=43 keeps contest steepness consistent
# with overall-level calibration. See fight.py for derivation.
SKILL_SCALE: float = SCALE


# ─── Phases ───────────────────────────────────────────────────────────────────

class Phase(Enum):
    STANDING = "STANDING"
    CLINCH   = "CLINCH"
    GROUND   = "GROUND"


class TransitionType(Enum):
    CLINCH_ENTRY     = "clinch_entry"       # STANDING -> CLINCH
    CLINCH_BREAK     = "clinch_break"       # CLINCH   -> STANDING
    CLINCH_TO_GROUND = "clinch_to_ground"   # CLINCH   -> GROUND
    DIRECT_TAKEDOWN  = "direct_takedown"    # STANDING -> GROUND
    SCRAMBLE         = "scramble"           # GROUND   -> STANDING


_TRANSITION_DEST: dict[TransitionType, Phase] = {
    TransitionType.CLINCH_ENTRY:     Phase.CLINCH,
    TransitionType.CLINCH_BREAK:     Phase.STANDING,
    TransitionType.CLINCH_TO_GROUND: Phase.GROUND,
    TransitionType.DIRECT_TAKEDOWN:  Phase.GROUND,
    TransitionType.SCRAMBLE:         Phase.STANDING,
}

# Transitions available from STANDING and CLINCH; either fighter may initiate.
# GROUND is handled separately in simulate_round (asymmetric: bottom-only).
# CLINCH does not track a dominant/non-dominant role this session. With the
# absolute-skill floor in place, a weak-clinch fighter already attempts
# CLINCH_BREAK rarely, suppressing the symmetric over-escape issue. A "clinch
# control" flag (analogous to GROUND top/bottom) would add realism but is
# deferred until tests confirm Issue A is sufficient for CLINCH.
_PHASE_TRANSITIONS: dict[Phase, list[TransitionType]] = {
    Phase.STANDING: [TransitionType.CLINCH_ENTRY, TransitionType.DIRECT_TAKEDOWN],
    Phase.CLINCH:   [TransitionType.CLINCH_TO_GROUND, TransitionType.CLINCH_BREAK],
}


# ─── Round / tick constants ───────────────────────────────────────────────────
ROUND_SECONDS:   int = 300
TICK_SECONDS:    int = 5
TICKS_PER_ROUND: int = ROUND_SECONDS // TICK_SECONDS  # 60 ticks


# ─── Stamina constants ────────────────────────────────────────────────────────
MAX_STAMINA:          float = 100.0
SUCCESS_STAMINA_COST: float = 5.0   # stamina consumed by a successful attempt
FAIL_STAMINA_COST:    float = 9.0   # failed attempts cost ~1.8x (energy wasted on stuffed burst)
# NOTE: fatigue-degrades-defense-more-than-offense asymmetry deferred to next
# session; requires finish-threat tracking that doesn't exist yet.


# ─── Attempt-tendency constants ───────────────────────────────────────────────
# Final p_attempt = BASE_ATTEMPT_PROB * abs_floor * (TENDENCY_SCALE * p_success) * stamina_frac
#
# BASE_ATTEMPT_PROB: per-tick base rate before any modifiers.
# ABS_SKILL_SCALE:   steepness of the absolute-skill sigmoid. At 15:
#     skill = -20 -> abs_floor ~0.056  (almost never initiates)
#     skill =   0 -> abs_floor = 0.50  (base rate at league average)
#     skill = +20 -> abs_floor ~0.944  (near-maximum rate)
#   Chosen so negative-territory fighters barely attempt, while the gap-based
#   modifier still does meaningful work for mid-range matched fighters.
# TENDENCY_SCALE:    multiplier linking relative skill gap to attempt frequency.
#   At equal skills (p_success=0.5): gap_modifier = 1.0 -> no boost.
#   At dominant     (p_success=0.9): gap_modifier = 1.8 -> 80% more attempts.
BASE_ATTEMPT_PROB: float = 0.12
ABS_SKILL_SCALE:   float = 13.0
TENDENCY_SCALE:    float = 2.0


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class TransitionAttempt:
    tick:          int
    phase_from:    Phase
    phase_to:      Phase
    transition:    TransitionType
    attacker_name: str
    success:       bool
    stamina_cost:  float
    attacker_id:   str | None = None   # stable fighter_id; None for legacy/test callers


@dataclass
class RoundTimeline:
    time_in_phase:   dict[str, float]   # phase name -> seconds spent in that phase
    attempts:        list[TransitionAttempt]
    end_stamina_a:   float              # remaining stamina for future cross-round use
    end_stamina_b:   float
    ticks_per_round: int = TICKS_PER_ROUND   # actual tick count this round ran for


# ─── Contest helpers ──────────────────────────────────────────────────────────

def _logistic(gap: float) -> float:
    """P(attacker wins sub-attribute contest); gap = attacker_skill - defender_skill."""
    return 1.0 / (1.0 + 10.0 ** (-gap / SKILL_SCALE))


def _logistic_abs(skill: float) -> float:
    """Sigmoid of attacker's ABSOLUTE skill value; floors attempt frequency."""
    return 1.0 / (1.0 + 10.0 ** (-skill / ABS_SKILL_SCALE))


def _transition_skills(
    attacker: Fighter, defender: Fighter, t: TransitionType,
) -> tuple[float, float]:
    """
    Return (attacker_skill, defender_skill) for the relevant sub-attribute pairing.

    Attribute -> transition mapping:
      CLINCH_ENTRY / CLINCH_BREAK  : clinch vs clinch
        (CLINCH_BREAK: breaker's clinch control vs holder's grip)
      DIRECT_TAKEDOWN / CLINCH_TO_GROUND : wrestling + 0.3*athleticism vs same
        (athleticism contributes timing and explosion on shots and sprawls)
      SCRAMBLE : athleticism + 0.5*wrestling vs same
        (hip-escape speed weighs more than raw wrestling for stand-ups)
    """
    if t in (TransitionType.CLINCH_ENTRY, TransitionType.CLINCH_BREAK):
        return attacker.clinch, defender.clinch

    if t in (TransitionType.DIRECT_TAKEDOWN, TransitionType.CLINCH_TO_GROUND):
        a = attacker.wrestling + 0.3 * attacker.athleticism
        d = defender.wrestling + 0.3 * defender.athleticism
        return a, d

    # SCRAMBLE
    a = attacker.athleticism + 0.5 * attacker.wrestling
    d = defender.athleticism + 0.5 * defender.wrestling
    return a, d


def _p_attempt(p_succ: float, stamina: float, attacker_abs_skill: float) -> float:
    """
    Per-tick probability that this fighter initiates this transition.

    Three multiplicative factors:
      1. abs_floor  -- sigmoid of attacker's own absolute skill in the relevant
                       composite attribute; a fighter bad at wrestling barely
                       shoots regardless of who they face.
      2. gap_modifier -- TENDENCY_SCALE * p_success; the dominant attacker
                       in the matchup attempts more and succeeds more.
      3. stamina_fraction -- depletes as the round progresses.
    """
    abs_floor = _logistic_abs(attacker_abs_skill)
    return BASE_ATTEMPT_PROB * abs_floor * (TENDENCY_SCALE * p_succ) * (stamina / MAX_STAMINA)


# ─── Voluntary style-mixing ────────────────────────────────────────────────────
# Islam-test mechanic: a fighter with high style_flexibility voluntarily spends
# meaningful time OUTSIDE their dominant phase, provided (a) the matchup is safe
# enough to afford experimenting and (b) their absolute skill in the secondary
# phase genuinely clears a floor (reuses _logistic_abs — the same absolute-skill
# sigmoid as the Issue A fix above; one canonical version, not reimplemented).
#
# effective_mixing = style_flexibility * matchup_modifier * tier_modifier
#   matchup_modifier: HIGH when the fighter clearly outclasses the opponent
#     (safe to experiment), LOW when facing a peer/superior (lean into your best).
#     Mapped from the overall gap through the same logistic used for sub-attribute
#     contests (SKILL_SCALE=43) -- gap=0 -> 1.0 (neutral), consistent steepness
#     with the rest of the calibration.
#   tier_modifier: elite fighters are more well-rounded and can afford to mix;
#     lower tiers fighting for their career mostly stick to what they know.
#
# The resulting per-fight scalar is squashed through tanh and clamped to >= 0:
# negative/near-zero style_flexibility fighters (specialists) get a mixing
# fraction of exactly 0 -- their transition tendency is completely unaffected,
# i.e. they pursue their dominant phase exactly as the pre-mixing engine did.
MIXING_TIER_MODIFIER: dict[str, float] = {
    "tier0": 0.5,   # Amateur
    "tier1": 0.7,   # Regional
    "tier2": 0.85,  # Mid-major
    "tier3": 1.0,   # Top-org btm-15
    "tier4": 1.15,  # Top-org elite
}
MIXING_MATCHUP_MOD_MIN: float = 0.4   # matchup_modifier at a large negative overall gap
MIXING_MATCHUP_MOD_MAX: float = 1.6   # matchup_modifier at a large positive overall gap
MIXING_TANH_DIVISOR:    float = 15.0  # squashes effective_mixing into a bounded pull; first-pass
MIXING_MODIFIER_SCALE:  float = 1.15  # max fractional dampen/boost on attempt tendency; first-pass.
# NOTE: dominant-phase pursuit (e.g. a wrestler's DIRECT_TAKEDOWN) is only
# ATTEMPTED probabilistically -- once it succeeds, staying in that phase is
# passive and costs no further attempts. Suppressing attempt tendency alone
# therefore needs a large scale to visibly redistribute time; tuned against
# tests/verify_style_mixing.py's Islam-test target (25-40% standing time).
MIXING_DAMPEN_FLOOR:    float = 0.1   # dominant-phase tendency never drops below this fraction


def _phase_composite_skill(fighter: Fighter, phase: "Phase") -> float:
    """Absolute-skill composite for a given phase -- same pairings used by
    _transition_skills / phase_output.py, so 'is this phase viable at all'
    reads the identical attribute mix as 'who wins a contest in this phase'."""
    if phase == Phase.STANDING:
        return fighter.boxing + fighter.kickboxing
    if phase == Phase.CLINCH:
        return fighter.clinch
    return fighter.wrestling + 0.3 * fighter.athleticism  # Phase.GROUND


def primary_phase(fighter: Fighter) -> "Phase":
    """The phase this fighter's own attribute profile would most aggressively
    pursue absent style-mixing -- i.e. their dominant phase. Used both to
    modulate transition tendency here and (career/development.py) to decide
    which post-fight phase-time counts as 'non-primary' for development feedback.
    """
    affinities = {p: _phase_composite_skill(fighter, p) for p in Phase}
    return max(affinities, key=affinities.get)


def _secondary_phase(fighter: Fighter) -> "Phase":
    """The fighter's second-best phase by absolute composite skill -- the
    concrete 'secondary tool' style-mixing deploys. Deliberately not just
    'any non-primary phase': the absolute-skill gate (Part 3) must clamp on
    THIS specific composite, otherwise dampening pursuit of the dominant phase
    would leak free time into a phase the fighter is genuinely bad at (e.g. a
    wrestler with terrible boxing but neutral clinch would otherwise gain
    ungated standing time as a side effect of reduced takedown attempts)."""
    affinities = {p: _phase_composite_skill(fighter, p) for p in Phase}
    ordered = sorted(affinities, key=affinities.get, reverse=True)
    return ordered[1]


def _matchup_modifier(overall_gap: float) -> float:
    """gap = fighter.overall - opponent.overall. gap=0 -> 1.0 (no boost/penalty)."""
    return MIXING_MATCHUP_MOD_MIN + (MIXING_MATCHUP_MOD_MAX - MIXING_MATCHUP_MOD_MIN) * _logistic(overall_gap)


def effective_mixing(fighter: Fighter, opponent: Fighter) -> float:
    """Per-fight mixing appetite for `fighter` in this specific matchup."""
    gap = fighter.overall - opponent.overall
    matchup_mod = _matchup_modifier(gap)
    tier_mod = MIXING_TIER_MODIFIER.get(fighter.tier, 1.0)
    return fighter.style_flexibility * matchup_mod * tier_mod


def _mixing_fraction(fighter: Fighter, opponent: Fighter) -> float:
    """Bounded [0, ~1) pull toward secondary phases. Clamped at 0 for
    negative/low style_flexibility -- specialists are simply unaffected,
    not pushed further into specialism."""
    return max(0.0, math.tanh(effective_mixing(fighter, opponent) / MIXING_TANH_DIVISOR))


def _mixing_tendency_modifier(
    attacker: Fighter, mix_frac: float,
    attacker_primary: "Phase", attacker_secondary: "Phase", dest_phase: "Phase",
) -> float:
    """
    Multiplicative modifier on a transition's attempt probability.

    Both directions are gated by the SAME absolute-skill sigmoid (Part 3 /
    Issue A gate), evaluated on the attacker's SECONDARY phase composite (their
    concrete "second tool", not just any non-primary phase):
      dest_phase == primary   -> DAMPEN (pursue the dominant phase slightly
        less), scaled by the secondary-phase gate -- if the fighter has no
        viable secondary option, dampening barely happens (nowhere to go).
      dest_phase == secondary -> BOOST toward the fighter's actual secondary
        tool, scaled by that same gate.
      dest_phase == neither   -> no modifier (1.0). Mixing deploys the
        fighter's designated secondary tool, not a random third phase.
    A wrestler with genuinely bad boxing (-20) gets gate ~0 when STANDING is
    their identified secondary -- the option isn't available to them regardless
    of style_flexibility.
    """
    if mix_frac <= 0.0:
        return 1.0
    gate = _logistic_abs(_phase_composite_skill(attacker, attacker_secondary))
    if dest_phase == attacker_primary:
        return max(MIXING_DAMPEN_FLOOR, 1.0 - MIXING_MODIFIER_SCALE * mix_frac * gate)
    if dest_phase == attacker_secondary:
        return 1.0 + MIXING_MODIFIER_SCALE * mix_frac * gate
    return 1.0


# ─── Round simulation ─────────────────────────────────────────────────────────

def simulate_round(
    fighter_a: Fighter,
    fighter_b: Fighter,
    *,
    initial_phase: Phase = Phase.STANDING,
    initial_stamina_a: float = MAX_STAMINA,
    initial_stamina_b: float = MAX_STAMINA,
    ticks_per_round: int = TICKS_PER_ROUND,
) -> RoundTimeline:
    """
    Simulate one round's phase timeline for two fighters.

    Phase emerges from contested transition attempts on sub-attribute vectors.
    GROUND phase tracks top/bottom position: only the bottom fighter can
    initiate a SCRAMBLE attempt; the top fighter's attributes serve as defense.
    Returns time in each phase and the full attempt log. No damage or scoring.

    initial_stamina_a/b: starting stamina for each fighter this round.
    Defaults to MAX_STAMINA (fresh fighters); pass lower values from
    fatigue.FatigueState.stamina_start for cross-round carryover.

    ticks_per_round: total simulation ticks (ROUND_SECONDS // TICK_SECONDS by
    default = 60 for 5-min rounds; pass 36 for 3-min Amateur rounds).
    """
    phase     = initial_phase
    stamina_a = initial_stamina_a
    stamina_b = initial_stamina_b

    # Style-mixing: computed once per round (deterministic given both fighters'
    # attributes -- no need to recompute per tick). mix_frac_x <= 0 for
    # specialists short-circuits to a 1.0 (no-op) modifier everywhere below.
    primary_a    = primary_phase(fighter_a)
    primary_b    = primary_phase(fighter_b)
    secondary_a  = _secondary_phase(fighter_a)
    secondary_b  = _secondary_phase(fighter_b)
    mix_frac_a   = _mixing_fraction(fighter_a, fighter_b)
    mix_frac_b   = _mixing_fraction(fighter_b, fighter_a)

    # Top/bottom position in GROUND. Set when a takedown or clinch-to-ground
    # succeeds (attacker = top, defender = bottom). None outside of GROUND.
    # If initial_phase is GROUND with no prior takedown, fighter_a is treated as
    # top as a placeholder — that edge case doesn't arise in normal match flow.
    ground_top:    Fighter | None = fighter_a if initial_phase == Phase.GROUND else None
    ground_bottom: Fighter | None = fighter_b if initial_phase == Phase.GROUND else None

    time_in_phase: dict[str, float] = {p.value: 0.0 for p in Phase}
    attempts: list[TransitionAttempt] = []

    for tick in range(ticks_per_round):
        time_in_phase[phase.value] += TICK_SECONDS

        # Each candidate is (attacker, defender, TransitionType, p_success).
        candidates: list[tuple[Fighter, Fighter, TransitionType, float]] = []

        if phase == Phase.GROUND:
            # Only the BOTTOM fighter initiates SCRAMBLE; TOP fighter is the defender.
            # NOTE: reversal (top/bottom flip without full stand-up) deferred —
            # needs position-advancement concepts not yet in scope.
            bottom = ground_bottom
            top    = ground_top
            stam   = stamina_a if bottom is fighter_a else stamina_b
            if bottom is fighter_a:
                bottom_primary, bottom_secondary, bottom_frac = primary_a, secondary_a, mix_frac_a
            else:
                bottom_primary, bottom_secondary, bottom_frac = primary_b, secondary_b, mix_frac_b
            a_skill, d_skill = _transition_skills(bottom, top, TransitionType.SCRAMBLE)
            p_succ = _logistic(a_skill - d_skill)
            mix_mod = _mixing_tendency_modifier(bottom, bottom_frac, bottom_primary, bottom_secondary, Phase.STANDING)
            if random.random() < _p_attempt(p_succ, stam, a_skill) * mix_mod:
                candidates.append((bottom, top, TransitionType.SCRAMBLE, p_succ))
        else:
            for t in _PHASE_TRANSITIONS[phase]:
                dest = _TRANSITION_DEST[t]

                # Fighter A as attacker
                a_skill_a, d_skill_a = _transition_skills(fighter_a, fighter_b, t)
                p_succ_a = _logistic(a_skill_a - d_skill_a)
                mix_mod_a = _mixing_tendency_modifier(fighter_a, mix_frac_a, primary_a, secondary_a, dest)
                if random.random() < _p_attempt(p_succ_a, stamina_a, a_skill_a) * mix_mod_a:
                    candidates.append((fighter_a, fighter_b, t, p_succ_a))

                # Fighter B as attacker
                a_skill_b, d_skill_b = _transition_skills(fighter_b, fighter_a, t)
                p_succ_b = _logistic(a_skill_b - d_skill_b)
                mix_mod_b = _mixing_tendency_modifier(fighter_b, mix_frac_b, primary_b, secondary_b, dest)
                if random.random() < _p_attempt(p_succ_b, stamina_b, a_skill_b) * mix_mod_b:
                    candidates.append((fighter_b, fighter_a, t, p_succ_b))

        if not candidates:
            continue

        # If multiple fired the same tick, resolve only one at random.
        attacker, defender, trans, p_succ = random.choice(candidates)

        success = random.random() < p_succ
        cost    = SUCCESS_STAMINA_COST if success else FAIL_STAMINA_COST

        if attacker is fighter_a:
            stamina_a = max(0.0, stamina_a - cost)
        else:
            stamina_b = max(0.0, stamina_b - cost)

        attempts.append(TransitionAttempt(
            tick          = tick,
            phase_from    = phase,
            phase_to      = _TRANSITION_DEST[trans],
            transition    = trans,
            attacker_name = attacker.name,
            attacker_id   = attacker.fighter_id,
            success       = success,
            stamina_cost  = cost,
        ))

        if success:
            new_phase = _TRANSITION_DEST[trans]
            if new_phase == Phase.GROUND:
                ground_top    = attacker
                ground_bottom = defender
            else:
                ground_top    = None
                ground_bottom = None
            phase = new_phase

    return RoundTimeline(
        time_in_phase   = time_in_phase,
        attempts        = attempts,
        end_stamina_a   = stamina_a,
        end_stamina_b   = stamina_b,
        ticks_per_round = ticks_per_round,
    )


# ─── Sample call ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from career.templates import generate_fighter

    random.seed(7)
    wres = generate_fighter("dagestan_sambo")
    strk = generate_fighter("muay_thai")

    print(f"Sample round: {wres.name} (Dagestan) vs {strk.name} (Muay Thai)")
    print(f"  {wres.name:<28}  wrestl={wres.wrestling:>+.1f}  clinch={wres.clinch:>+.1f}  athl={wres.athleticism:>+.1f}")
    print(f"  {strk.name:<28}  wrestl={strk.wrestling:>+.1f}  clinch={strk.clinch:>+.1f}  athl={strk.athleticism:>+.1f}")
    print()

    tl = simulate_round(wres, strk)

    print("Phase timeline:")
    for phase_name, secs in tl.time_in_phase.items():
        bar = "#" * int(secs / TICK_SECONDS)
        print(f"  {phase_name:<10} {secs:>5.0f}s ({secs / ROUND_SECONDS:>4.0%})  {bar}")

    print(f"\nAttempts ({len(tl.attempts)} total):")
    for a in tl.attempts:
        tag = "SUC" if a.success else "FAI"
        print(
            f"  t{a.tick:>02}  [{tag}]  {a.attacker_name:<28}"
            f"  {a.transition.value:<20}"
            f"  {a.phase_from.value}->{a.phase_to.value}"
            f"  cost={a.stamina_cost:.0f}"
        )

    print(f"\nEnd stamina:  {wres.name} {tl.end_stamina_a:.1f}/100"
          f"  |  {strk.name} {tl.end_stamina_b:.1f}/100")
