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

import random
from dataclasses import dataclass
from enum import Enum

from fighter import Fighter
from fight import SCALE  # shared logistic steepness constant

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


@dataclass
class RoundTimeline:
    time_in_phase: dict[str, float]   # phase name -> seconds spent in that phase
    attempts:      list[TransitionAttempt]
    end_stamina_a: float              # remaining stamina for future cross-round use
    end_stamina_b: float


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


# ─── Round simulation ─────────────────────────────────────────────────────────

def simulate_round(
    fighter_a: Fighter,
    fighter_b: Fighter,
    *,
    initial_phase: Phase = Phase.STANDING,
) -> RoundTimeline:
    """
    Simulate one 5-minute round's phase timeline for two fighters.

    Phase emerges from contested transition attempts on sub-attribute vectors.
    GROUND phase tracks top/bottom position: only the bottom fighter can
    initiate a SCRAMBLE attempt; the top fighter's attributes serve as defense.
    Returns time in each phase and the full attempt log. No damage or scoring.
    """
    phase     = initial_phase
    stamina_a = MAX_STAMINA
    stamina_b = MAX_STAMINA

    # Top/bottom position in GROUND. Set when a takedown or clinch-to-ground
    # succeeds (attacker = top, defender = bottom). None outside of GROUND.
    # If initial_phase is GROUND with no prior takedown, fighter_a is treated as
    # top as a placeholder — that edge case doesn't arise in normal match flow.
    ground_top:    Fighter | None = fighter_a if initial_phase == Phase.GROUND else None
    ground_bottom: Fighter | None = fighter_b if initial_phase == Phase.GROUND else None

    time_in_phase: dict[str, float] = {p.value: 0.0 for p in Phase}
    attempts: list[TransitionAttempt] = []

    for tick in range(TICKS_PER_ROUND):
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
            a_skill, d_skill = _transition_skills(bottom, top, TransitionType.SCRAMBLE)
            p_succ = _logistic(a_skill - d_skill)
            if random.random() < _p_attempt(p_succ, stam, a_skill):
                candidates.append((bottom, top, TransitionType.SCRAMBLE, p_succ))
        else:
            for t in _PHASE_TRANSITIONS[phase]:
                # Fighter A as attacker
                a_skill_a, d_skill_a = _transition_skills(fighter_a, fighter_b, t)
                p_succ_a = _logistic(a_skill_a - d_skill_a)
                if random.random() < _p_attempt(p_succ_a, stamina_a, a_skill_a):
                    candidates.append((fighter_a, fighter_b, t, p_succ_a))

                # Fighter B as attacker
                a_skill_b, d_skill_b = _transition_skills(fighter_b, fighter_a, t)
                p_succ_b = _logistic(a_skill_b - d_skill_b)
                if random.random() < _p_attempt(p_succ_b, stamina_b, a_skill_b):
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
        time_in_phase = time_in_phase,
        attempts      = attempts,
        end_stamina_a = stamina_a,
        end_stamina_b = stamina_b,
    )


# ─── Sample call ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from templates import generate_fighter

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
