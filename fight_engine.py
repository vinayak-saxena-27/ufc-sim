"""
fight_engine.py -- Round-by-round fight simulation (Session 4c Part 2).

Sequences all prior modules into a complete fight:
  1. apply_fatigue_to_fighter()  -> fatigued Fighter copies for this round
  2. simulate_round()            -> phase/transition timeline (4a)
  3. compute_round_output()      -> per-segment scoring + finish-pressure (4b)
  4. check_finish() incremental  -> stoppage detection after each segment
  5. update_fatigue()            -> carry fatigue to next round
  6. Sum round scores            -> decision if no stoppage across all rounds

Fatigue feeds into 4a (lower initial stamina -> fewer transition attempts)
and 4b (lower chin attribute -> weaker striking-pressure defense) simultaneously,
starting round 2.  The within-round effect comes from 4a's stamina depletion
being continuous across ticks (later ticks already run with less stamina).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from fighter import Fighter
from phase_engine import Phase, simulate_round, TICKS_PER_ROUND, TICK_SECONDS
from tiers import TIER_RULESET
from phase_output import compute_round_output
from finish_check import FinishEvent, check_finish
from fatigue import FatigueState, fresh_fatigue, apply_fatigue_to_fighter, update_fatigue


# ─── Round count constants ─────────────────────────────────────────────────────
ROUNDS_STANDARD: int = 3   # non-title fights
ROUNDS_TITLE:    int = 5   # title / main-event fights (caller passes is_title=True)

# Fraction of a round's recency-weighted sub pressure that carries into the next round.
# Models the partial reset a fighter gets in the corner: submission danger accumulated
# in prior rounds persists, but doesn't carry at full value.
# 0.0 = full reset each round (old behaviour); 1.0 = full carry-over (no recovery).
SUB_PRESSURE_ROUND_DECAY: float = 0.5


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class RoundResult:
    """Summary of one simulated round."""
    round_num:       int
    score_a:         float          # recency-weighted round score (accumulated segs only)
    score_b:         float
    end_stamina_a:   float          # from 4a timeline (used for cross-round fatigue)
    end_stamina_b:   float
    fatigue_a:       float          # cumulative fatigue ENTERING this round (pre-round)
    fatigue_b:       float
    time_in_phase:   dict[str, float]
    n_segments:      int            # how many segments ran before finish or time
    finish_event:    FinishEvent | None   # None if the round went to time


@dataclass
class FightOutcome:
    """Complete result of a simulated fight."""
    winner_name:    str
    loser_name:     str
    method:         str             # "KO/TKO", "submission", or "decision"
    round_finished: int | None      # None for decisions; round number otherwise
    total_score_a:  float           # cumulative score across all completed/partial rounds
    total_score_b:  float
    rounds:         list[RoundResult]


# ─── Round runner ─────────────────────────────────────────────────────────────

def _run_round(
    fa:       Fighter,
    fb:       Fighter,
    fat_a:    FatigueState,
    fat_b:    FatigueState,
    round_num: int,
    *,
    prior_sub_a: float = 0.0,
    prior_sub_b: float = 0.0,
    ticks_per_round: int = TICKS_PER_ROUND,
) -> tuple[RoundResult, FinishEvent | None, float, float]:
    """
    Run one round through the full 4a -> 4b -> finish-check pipeline.

    Returns (RoundResult, FinishEvent | None, end_wsub_a, end_wsub_b).
    end_wsub_a/b is the total recency-weighted sub pressure at end of this round
    (prior carry-over + this round), for the caller to decay and pass into the
    next round as prior_sub_a/b.

    Score uses only accumulated segments (up to and including the segment where
    the finish occurred), so post-finish ticks are never scored.
    """
    fa_eff = apply_fatigue_to_fighter(fa, fat_a)
    fb_eff = apply_fatigue_to_fighter(fb, fat_b)

    # 4a: phase/transition timeline.  Cross-round stamina carryover via initial_stamina.
    timeline = simulate_round(
        fa_eff, fb_eff,
        initial_phase     = Phase.STANDING,
        initial_stamina_a = fat_a.stamina_start,
        initial_stamina_b = fat_b.stamina_start,
        ticks_per_round   = ticks_per_round,
    )

    # 4b: per-segment scoring + finish-pressure (uses fatigued chin attribute).
    round_out = compute_round_output(timeline, fa_eff, fb_eff)

    # Incremental finish check: break at the first segment that crosses a threshold.
    # Sub pressure carry-over from prior rounds is included via prior_sub_a/b.
    accumulated: list = []
    finish: FinishEvent | None = None
    for seg in round_out.segments:
        accumulated.append(seg)
        finish = check_finish(accumulated, fa.name, fb.name,
                              prior_sub_a=prior_sub_a, prior_sub_b=prior_sub_b)
        if finish:
            break

    # Score only the segments that actually resolved (pre-finish or all of them).
    score_a = sum(s.score_a * s.recency_weight for s in accumulated)
    score_b = sum(s.score_b * s.recency_weight for s in accumulated)

    # Total sub pressure at end of this round (prior + this round's contribution).
    end_wsub_a = prior_sub_a + sum(s.sub_pressure_a * s.recency_weight for s in accumulated)
    end_wsub_b = prior_sub_b + sum(s.sub_pressure_b * s.recency_weight for s in accumulated)

    result = RoundResult(
        round_num     = round_num,
        score_a       = score_a,
        score_b       = score_b,
        end_stamina_a = timeline.end_stamina_a,
        end_stamina_b = timeline.end_stamina_b,
        fatigue_a     = fat_a.cumulative_fatigue,
        fatigue_b     = fat_b.cumulative_fatigue,
        time_in_phase = timeline.time_in_phase,
        n_segments    = len(accumulated),
        finish_event  = finish,
    )
    return result, finish, end_wsub_a, end_wsub_b


# ─── Fight loop ───────────────────────────────────────────────────────────────

def simulate_full_fight(
    fa: Fighter,
    fb: Fighter,
    *,
    is_title: bool = False,
    tier: str | None = None,
) -> FightOutcome:
    """
    Simulate a complete fight between two fighters.

    Derives round count and round length from TIER_RULESET[tier].  If tier is
    None, falls back to fa.tier.  Unknown tier keys (e.g. test fixtures) fall
    back to ROUNDS_STANDARD / TICKS_PER_ROUND.

    A finish anywhere in any round exits early.  If all rounds complete, the
    decision goes to cumulative round scores.  Exact score ties are broken by
    coin flip (floating-point ties are astronomically rare with this engine).

    Raises ValueError if is_title=True for a tier whose title_rounds is None.

    The fighters are NOT mutated; fatigue and fight_history recording is the
    caller's responsibility (simulate_fight() in fight.py handles recording).
    """
    tier_key = tier if tier is not None else fa.tier
    ruleset  = TIER_RULESET.get(tier_key)

    if ruleset is None:
        n_rounds        = ROUNDS_TITLE if is_title else ROUNDS_STANDARD
        ticks_per_round = TICKS_PER_ROUND
    elif is_title:
        if ruleset.title_rounds is None:
            raise ValueError(
                f"Tier '{tier_key}' has no title fight format (title_rounds is None)"
            )
        n_rounds        = ruleset.title_rounds
        ticks_per_round = ruleset.round_seconds // TICK_SECONDS
    else:
        n_rounds        = ruleset.non_title_rounds
        ticks_per_round = ruleset.round_seconds // TICK_SECONDS

    fat_a   = fresh_fatigue()
    fat_b   = fresh_fatigue()
    results: list[RoundResult] = []

    # Sub pressure accumulated from prior rounds, decayed by SUB_PRESSURE_ROUND_DECAY
    # between rounds (models partial corner recovery).  Striking pressure does not
    # carry across rounds.
    carry_sub_a = 0.0
    carry_sub_b = 0.0

    for rnd in range(1, n_rounds + 1):
        result, finish, end_wsub_a, end_wsub_b = _run_round(
            fa, fb, fat_a, fat_b, rnd,
            prior_sub_a=carry_sub_a,
            prior_sub_b=carry_sub_b,
            ticks_per_round=ticks_per_round,
        )
        results.append(result)

        if finish:
            return FightOutcome(
                winner_name    = finish.winner_name,
                loser_name     = finish.loser_name,
                method         = finish.method,
                round_finished = rnd,
                total_score_a  = sum(r.score_a for r in results),
                total_score_b  = sum(r.score_b for r in results),
                rounds         = results,
            )

        # Update fatigue and sub carry-over for the next round.
        fat_a       = update_fatigue(fat_a, result.end_stamina_a)
        fat_b       = update_fatigue(fat_b, result.end_stamina_b)
        carry_sub_a = end_wsub_a * SUB_PRESSURE_ROUND_DECAY
        carry_sub_b = end_wsub_b * SUB_PRESSURE_ROUND_DECAY

    # Decision: sum round scores across all rounds.
    total_a = sum(r.score_a for r in results)
    total_b = sum(r.score_b for r in results)

    if total_a > total_b:
        winner_name, loser_name = fa.name, fb.name
    elif total_b > total_a:
        winner_name, loser_name = fb.name, fa.name
    else:
        # Exact tie (astronomically rare); coin flip.
        if random.random() < 0.5:
            winner_name, loser_name = fa.name, fb.name
        else:
            winner_name, loser_name = fb.name, fa.name

    return FightOutcome(
        winner_name    = winner_name,
        loser_name     = loser_name,
        method         = "decision",
        round_finished = None,
        total_score_a  = total_a,
        total_score_b  = total_b,
        rounds         = results,
    )


# ─── Sample call ─────────────────────────────────────────────────────────────

def _print_fight(fa: Fighter, fb: Fighter, *, is_title: bool = False) -> None:
    """Run and print a formatted round-by-round fight summary."""
    title_tag = " [TITLE FIGHT]" if is_title else ""
    print(f"{'='*65}")
    print(f"  {fa.name} vs {fb.name}{title_tag}")
    print(f"  A: overall={fa.overall:>+.1f}  wrestl={fa.wrestling:>+.1f}"
          f"  bjj={fa.bjj:>+.1f}  box={fa.boxing:>+.1f}"
          f"  pwr={fa.power:>+.1f}  chin={fa.chin:>+.1f}")
    print(f"  B: overall={fb.overall:>+.1f}  wrestl={fb.wrestling:>+.1f}"
          f"  bjj={fb.bjj:>+.1f}  box={fb.boxing:>+.1f}"
          f"  pwr={fb.power:>+.1f}  chin={fb.chin:>+.1f}")
    print()

    outcome = simulate_full_fight(fa, fb, is_title=is_title)

    for rnd in outcome.rounds:
        tp    = rnd.time_in_phase
        stand = tp.get("STANDING", 0)
        clinc = tp.get("CLINCH",   0)
        grnd  = tp.get("GROUND",   0)

        phase_str = (
            f"STD={stand:>3.0f}s"
            + (f"  CLN={clinc:>3.0f}s" if clinc > 0 else "")
            + (f"  GRD={grnd:>3.0f}s"  if grnd  > 0 else "")
        )

        finish_tag = ""
        if rnd.finish_event:
            fe = rnd.finish_event
            finish_tag = f"  *** {fe.method.upper()} -- {fe.winner_name} wins ***"

        print(
            f"  R{rnd.round_num}  {phase_str:<30}"
            f"  segs={rnd.n_segments}"
            f"  score A={rnd.score_a:>6.1f} B={rnd.score_b:>6.1f}"
            f"  stam A={rnd.end_stamina_a:>5.1f} B={rnd.end_stamina_b:>5.1f}"
            f"  fat A={rnd.fatigue_a:.3f} B={rnd.fatigue_b:.3f}"
            + finish_tag
        )

    print()
    if outcome.round_finished:
        print(f"  RESULT: {outcome.winner_name} by {outcome.method}"
              f" (R{outcome.round_finished})")
    else:
        print(f"  RESULT: {outcome.winner_name} by decision"
              f"  (score {outcome.total_score_a:.1f} vs {outcome.total_score_b:.1f})")
    print()


if __name__ == "__main__":
    from templates import generate_fighter

    # Fight 1: Wrestler (Dagestan) vs Striker (Muay Thai) -- likely GROUND control
    random.seed(12)
    wres = generate_fighter("dagestan_sambo")
    strk = generate_fighter("muay_thai")
    _print_fight(wres, strk)

    # Fight 2: Brazilian BJJ vs American wrestler -- grappling war
    random.seed(34)
    bjj_f = generate_fighter("brazilian")
    wres2 = generate_fighter("american_wrestling")
    _print_fight(bjj_f, wres2)

    # Fight 3: 5-round title fight -- Muay Thai vs Dagestan
    random.seed(56)
    fa_title = generate_fighter("muay_thai")
    fb_title = generate_fighter("dagestan_sambo")
    _print_fight(fa_title, fb_title, is_title=True)
