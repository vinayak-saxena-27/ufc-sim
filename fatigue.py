"""
fatigue.py -- Cross-round fatigue depletion (Session 4c Part B).

Extends 4a's within-round stamina tracking to produce a cumulative fatigue state
that persists across rounds.  Fatigue degrades DEFENSIVE attributes only:

  Degraded (additive penalty applied to the attribute value in sub-attribute space):
    chin      -- lowers effective_chin in 4b's striking finish-pressure calculation
    wrestling -- weakens takedown/sprawl defense in 4a's transition contests
    clinch    -- weakens clinch-entry/break defense in 4a's transition contests

  Unchanged (offense must not degrade per spec):
    boxing, kickboxing, power  -- striking finish-pressure GENERATION (4b)
    bjj                        -- submission finish-pressure GENERATION (4b)
    athleticism, cardio, fight_iq

Submission finish-pressure specifically is NOT boosted by fatigue.  Confirmed:
  - 4b's sub_rate formula uses bjj and wrestling composites, NOT effective_chin.
  - effective_chin only enters 4b's STRIKING pressure track.
  - This module does not touch bjj (leaves sub offense intact).

Integration path (no 4a/4b code changes needed):
  1. Call apply_fatigue_to_fighter(fighter, state) before each round.
  2. Pass the returned (modified) Fighter to simulate_round() and compute_round_output().
  3. 4b naturally picks up the lower chin via _effective_chin(fatigued_fighter.chin).
  4. 4a naturally uses the lower wrestling/clinch in _transition_skills().
  5. After each round, call update_fatigue(state, timeline.end_stamina) to carry forward.

Caveat on wrestling: 4a uses wrestling symmetrically (same attribute for takedown
offense AND sprawl defense in _transition_skills).  Lowering it here affects both
roles; isolating purely defensive wrestling is deferred to Part 2 (adding a
defense_scale parameter to simulate_round).  All offensive striking/sub outputs
are unaffected regardless.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from fighter import Fighter
from phase_engine import MAX_STAMINA
from fight import SCALE

SKILL_SCALE: float = SCALE  # must match phase_engine.py


def _logistic(gap: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-gap / SKILL_SCALE))


# ─── Tuning constants (first-pass -- expect retuning after 4c Part 3) ─────────

# Fraction of per-round stamina depletion that becomes permanent cumulative fatigue.
# e.g. losing 40 stamina in a round adds 0.5 * (40/100) = 0.20 cumulative fatigue.
# After 3 heavy rounds (~40 depletion each): cumulative ≈ 0.60.
FATIGUE_ACCUMULATION_RATE: float = 0.5

# Between-round stamina recovery: fraction of depleted stamina refilled before next round.
# At 0.4: ending a round at 60 stamina -> starts next at 60 + 0.4*(100-60) = 76.
# Prevents full fresh-start reset while letting fighters recover some energy.
BETWEEN_ROUND_RECOVERY: float = 0.4

# Chin attribute drop (in sub-attribute point space) per unit of cumulative fatigue.
# At cumulative_fatigue=1.0: chin drops by CHIN_FATIGUE_PENALTY points.
# chin=+15, fatigue=0.5: penalized chin = 15 - 0.5*15 = +7.5
# Chosen so a good-chin fighter (+15 to +20) still has meaningful but reduced resilience
# at high fatigue, while a weak-chin fighter becomes dangerously exposed.
CHIN_FATIGUE_PENALTY: float = 15.0

# Defensive attribute (wrestling, clinch) drop per unit of cumulative fatigue.
# Additive penalty preserves direction: +20 wrestler at 0.5 fatigue -> +14 (still good
# but weaker); -10 wrestler at 0.5 fatigue -> -16 (more exposed).
# Smaller than CHIN_FATIGUE_PENALTY: defensive grappling erodes more slowly than durability.
DEFENSE_FATIGUE_PENALTY: float = 10.0


# ─── State ────────────────────────────────────────────────────────────────────

@dataclass
class FatigueState:
    """
    Per-fighter cumulative fatigue state; updated after each round.

    stamina_start:     stamina at the START of the upcoming round (incorporates
                       recovery; 100.0 for round 1, less thereafter).
    cumulative_fatigue: [0, 1]; 0 = fully fresh, 1 = completely gassed.
                       Never resets within a fight; drives defense/chin degradation.
    """
    stamina_start:      float = MAX_STAMINA  # immutable float default: OK in dataclass
    cumulative_fatigue: float = 0.0


def fresh_fatigue() -> FatigueState:
    """Starting state for a fighter entering round 1 (fully fresh)."""
    return FatigueState(stamina_start=MAX_STAMINA, cumulative_fatigue=0.0)


def update_fatigue(state: FatigueState, round_end_stamina: float) -> FatigueState:
    """
    Produce updated fatigue state after a round completes.

    round_end_stamina: the end_stamina_a or end_stamina_b value from 4a's
                       RoundTimeline -- the depletion signal from the phase engine.

    Two updates:
      cumulative_fatigue += fraction_depleted * FATIGUE_ACCUMULATION_RATE (capped at 1.0)
      stamina_start for next round = partial recovery from end_stamina
    """
    depletion_fraction = max(0.0, (state.stamina_start - round_end_stamina) / MAX_STAMINA)
    new_fatigue        = min(1.0, state.cumulative_fatigue + depletion_fraction * FATIGUE_ACCUMULATION_RATE)

    # Cross-round stamina recovery: partial refill from end-of-round level
    recovered_stamina  = round_end_stamina + BETWEEN_ROUND_RECOVERY * (MAX_STAMINA - round_end_stamina)
    next_start         = min(MAX_STAMINA, recovered_stamina)

    return FatigueState(stamina_start=next_start, cumulative_fatigue=new_fatigue)


# ─── Fatigue modifier functions ────────────────────────────────────────────────

def effective_chin_fatigued(fighter: Fighter, state: FatigueState) -> float:
    """
    Compute effective chin WITH cumulative fatigue penalty applied.

    Returns value in (0, 1); decreases as cumulative_fatigue grows.

    This is the Session 4c wire-up of the hook flagged in 4b's _effective_chin():
      4b uses _logistic(f.chin) as a static baseline.
      4c uses this function when fatigue state is available.

    NOT applied to submission finish-pressure (subs are position/control-driven).
    Only the STRIKING track in 4b uses effective_chin; this function feeds that.
    """
    penalized_chin = fighter.chin - state.cumulative_fatigue * CHIN_FATIGUE_PENALTY
    return _logistic(penalized_chin)


def chin_penalty_points(state: FatigueState) -> float:
    """Return the raw chin attribute penalty for a given fatigue state (for logging)."""
    return state.cumulative_fatigue * CHIN_FATIGUE_PENALTY


def defense_penalty_points(state: FatigueState) -> float:
    """Return the raw defense attribute penalty (wrestling, clinch) for logging."""
    return state.cumulative_fatigue * DEFENSE_FATIGUE_PENALTY


def apply_fatigue_to_fighter(fighter: Fighter, state: FatigueState) -> Fighter:
    """
    Return a COPY of fighter with fatigue penalties applied to defensive attributes.

    Degraded (additive drop in attribute-space -- works for positive AND negative values):
      chin      drops by cumulative_fatigue * CHIN_FATIGUE_PENALTY
      wrestling drops by cumulative_fatigue * DEFENSE_FATIGUE_PENALTY
      clinch    drops by cumulative_fatigue * DEFENSE_FATIGUE_PENALTY

    Unchanged:
      boxing, kickboxing, power  (striking offense -> 4b's finish-pressure generation)
      bjj                        (sub offense -> 4b's sub finish-pressure; NOT fatigue-driven)
      athleticism, cardio, fight_iq

    Returns the original fighter unchanged if cumulative_fatigue == 0 (early exit).

    Integration note: pass the returned Fighter to both simulate_round() and
    compute_round_output().  4a and 4b pick up the changes transparently --
    no code changes required in those modules.
    """
    if state.cumulative_fatigue == 0.0:
        return fighter  # fresh: avoid creating unnecessary copies

    chin_drop    = chin_penalty_points(state)
    defense_drop = defense_penalty_points(state)

    return replace(
        fighter,
        chin      = fighter.chin      - chin_drop,
        wrestling = fighter.wrestling - defense_drop,
        clinch    = fighter.clinch    - defense_drop,
        # boxing, kickboxing, power, bjj, athleticism, cardio, fight_iq: UNCHANGED
    )


def stamina_at_tick(
    fighter_name: str,
    attempts: list,   # list[TransitionAttempt] from phase_engine
    initial_stamina: float = MAX_STAMINA,
) -> list[tuple[int, float]]:
    """
    Reconstruct the named fighter's stamina at each tick where it changed,
    using 4a's attempt log.  Extends 4a's stamina tracking without modifying it.

    Returns list of (tick, stamina) for ticks with a depletion event.
    Only tracks the fighter's stamina as ATTACKER (defenders don't lose stamina
    in 4a's current design; full per-tick tracking is a Part 2 extension).

    Usage: provides intra-round stamina snapshots for logging, or for computing
    segment-level effective_chin in a future extension to compute_round_output().
    """
    events: list[tuple[int, float]] = [(0, initial_stamina)]
    stamina = initial_stamina
    for att in attempts:
        if att.attacker_name == fighter_name:
            stamina = max(0.0, stamina - att.stamina_cost)
            events.append((att.tick, stamina))
    return events


# ─── Sample call ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import random
    from templates import generate_fighter
    from phase_engine import simulate_round

    random.seed(7)
    wres = generate_fighter("dagestan_sambo")
    strk = generate_fighter("muay_thai")

    print(f"Sample fatigue progression across 3 rounds")
    print(f"Fighter: {wres.name} (Dagestan)")
    print(f"  Base attrs:  chin={wres.chin:>+.1f}  wrestling={wres.wrestling:>+.1f}"
          f"  clinch={wres.clinch:>+.1f}")
    print(f"  Base effective_chin = {_logistic(wres.chin):.4f}")
    print(f"  (Unchanged by fatigue: boxing={wres.boxing:>+.1f}"
          f"  bjj={wres.bjj:>+.1f}  power={wres.power:>+.1f})")
    print()
    print(f"  {'Round':<7}  {'stam_start':>10}  {'cum_fat':>9}  "
          f"{'eff_chin':>9}  {'chin_attr':>10}  {'wres_attr':>10}  "
          f"{'clinch_attr':>11}  {'boxing':>8}  {'bjj':>8}")
    print("  " + "-" * 95)

    state = fresh_fatigue()

    # Simulate 3 rounds using the actual phase engine, letting stamina deplete naturally
    for rnd in range(1, 4):
        fatigued = apply_fatigue_to_fighter(wres, state)
        eff_chin = effective_chin_fatigued(wres, state)

        print(f"  {rnd:<7}  {state.stamina_start:>10.1f}  {state.cumulative_fatigue:>9.3f}  "
              f"{eff_chin:>9.4f}  {fatigued.chin:>10.2f}  {fatigued.wrestling:>10.2f}  "
              f"{fatigued.clinch:>11.2f}  {fatigued.boxing:>8.2f}  {fatigued.bjj:>8.2f}")

        # Run a real round through 4a to get natural stamina depletion
        tl = simulate_round(fatigued, strk)
        state = update_fatigue(state, tl.end_stamina_a)

    # Show final state
    eff_chin_final = effective_chin_fatigued(wres, state)
    print()
    print(f"  After round 3 (cumulative_fatigue={state.cumulative_fatigue:.3f}):")
    print(f"    effective_chin  base={_logistic(wres.chin):.4f}"
          f"  -> fatigued={eff_chin_final:.4f}"
          f"  (drop of {_logistic(wres.chin) - eff_chin_final:.4f})")
    print(f"    chin attr:      {wres.chin:>+.2f}"
          f"  -> {wres.chin - chin_penalty_points(state):>+.2f}"
          f"  (penalty={chin_penalty_points(state):.2f})")
    print(f"    wrestling attr: {wres.wrestling:>+.2f}"
          f"  -> {wres.wrestling - defense_penalty_points(state):>+.2f}"
          f"  (penalty={defense_penalty_points(state):.2f})")
    print(f"    boxing attr:    {wres.boxing:>+.2f}  -> {wres.boxing:>+.2f}  (UNCHANGED -- offense)")
    print(f"    bjj attr:       {wres.bjj:>+.2f}  -> {wres.bjj:>+.2f}  (UNCHANGED -- sub offense)")
    print()

    # Show stamina_at_tick extension (intra-round stamina reconstruction from 4a's log)
    random.seed(7)
    fatigued_demo = apply_fatigue_to_fighter(wres, fresh_fatigue())
    tl_demo = simulate_round(fatigued_demo, strk)
    wres_ticks = stamina_at_tick(fatigued_demo.name, tl_demo.attempts)
    print(f"  Intra-round stamina trace for {fatigued_demo.name} (round 1, from 4a's attempt log):")
    print(f"    start_stamina={MAX_STAMINA:.0f}  end_stamina={tl_demo.end_stamina_a:.1f}")
    if len(wres_ticks) > 1:
        for tick, stam in wres_ticks:
            print(f"    tick {tick:>2}: {stam:.1f}")
    else:
        print(f"    (no transition attempts as attacker this round)")
