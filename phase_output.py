"""
phase_output.py -- Per-segment scoring and finish-pressure engine (Session 4b Part 1).

Consumes the phase timeline from phase_engine.py and produces, per fighter per
phase-segment, two independent output tracks:

  round_score_effectiveness -- positional + active output; feeds future judges scoring
  finish_pressure           -- active-output-only; split striking vs submission tracks

Key invariant: GROUND-top with low active work produces HIGH round-score (passive
positional dominance accrues regardless) and LOW finish-pressure (no active threat).
These two tracks are computed independently and never derived from each other.

Submission pressure is NOT modulated by fatigue (deliberate asymmetry -- BJJ
technique does not degrade the same way as striking power under fatigue). Fatigue
hook on striking_finish_pressure is wired in Session 4c via effective_chin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from fighter import Fighter
from phase_engine import Phase, RoundTimeline
from fight import SCALE

# Must match phase_engine.py -- kept local for module self-containment.
SKILL_SCALE:     float = SCALE   # 43.0 -- sub-attribute logistic steepness
ABS_SKILL_SCALE: float = 13.0   # absolute-skill sigmoid steepness


# ─── Tuning constants ─────────────────────────────────────────────────────────

# Recency decay for round-score: weight(tick) = exp(RECENCY_DECAY * tick).
# tick=0 -> 1.0x;  tick=59 -> ~3.25x.  Late-round dominance outweighs early control.
RECENCY_DECAY: float = 0.02

# Expected active-output rates per tick (before abs-skill floor and gap modulation).
BASE_STRIKE_RATE: float = 0.30   # STANDING striking exchanges
BASE_CLINCH_RATE: float = 0.20   # CLINCH striking (tighter range)
BASE_GNP_RATE:    float = 0.20   # GROUND G&P rate from top
BASE_SUB_RATE:    float = 0.15   # GROUND submission attempt rate from top

# Passive positional contributions to round score (per tick, before recency).
# GROUND-top earns this regardless of active work -- the "controls without finishing"
# scenario produces high round-score but zero finish-pressure.
GROUND_PASSIVE_RATE:    float = 0.25   # per-tick passive bonus for top; scaled by wrestling logistic
GROUND_RESISTANCE_BASE: float = 0.25   # per-tick base credit for GROUND-bottom fighter;
# scaled by bottom fighter's own bjj+athleticism (absolute skill), not the wrestling gap.
# Replaces the old `0.15 * (1-logistic(gap))` formula which scaled INVERSELY with
# opponent dominance, giving near-zero credit to the bottom fighter in exactly the
# matchups where the gap is largest.
CLINCH_PASSIVE_SCALE:   float = 0.30   # fraction of clinch-gap logistic added as passive

# Dominance level: cumulative scalar for GROUND-top control quality.
# Driven by time held and wrestling skill gap.  Feeds post-round narrative in 4c.
DOMINANCE_PER_TICK: float = 1.0   # base units per tick on top


# ─── Logistic helpers ─────────────────────────────────────────────────────────

def _logistic(gap: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-gap / SKILL_SCALE))


def _logistic_abs(skill: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-skill / ABS_SKILL_SCALE))


def _effective_chin(f: Fighter) -> float:
    """Baseline chin durability [0, 1]; 0.5 = league avg.
    Hook: fatigue will lower this value in Session 4c."""
    return _logistic(f.chin)


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class SegmentOutput:
    """Per-phase-segment output for both fighters."""
    phase:             str
    start_tick:        int
    end_tick:          int          # exclusive; segment covers [start_tick, end_tick)
    ground_top_name:   str | None   # fighter name on top; None outside GROUND
    recency_weight:    float        # exp(RECENCY_DECAY * mid_tick) for round-score

    # Round-score effectiveness (passive position + active output; feeds judging)
    score_a:           float
    score_b:           float

    # Striking finish-pressure (active G&P/striking only; zero from passive hold)
    strike_pressure_a: float
    strike_pressure_b: float

    # Submission finish-pressure (GROUND-top only; NOT fatigue-affected)
    sub_pressure_a:    float
    sub_pressure_b:    float

    # GROUND-top dominance accrual this segment (0 for non-top / non-GROUND)
    dominance_a:       float
    dominance_b:       float


@dataclass
class RoundOutput:
    """Full round output: per-segment breakdown plus weighted totals."""
    segments: list[SegmentOutput]

    # Recency-weighted round-score totals (recent moments count more for judging)
    total_score_a: float
    total_score_b: float

    # Raw cumulative finish-pressure (pressure is cumulative threat, not a narrative
    # signal -- recency weighting does not apply)
    total_strike_pressure_a: float
    total_strike_pressure_b: float
    total_sub_pressure_a:    float
    total_sub_pressure_b:    float

    # Hook for Session 4c fatigue: these values will degrade mid-fight
    effective_chin_a: float
    effective_chin_b: float

    # Cumulative dominance scalar (time held * wrestling gap); drives post-round narrative
    total_dominance_a: float
    total_dominance_b: float


# ─── Segment reconstruction ───────────────────────────────────────────────────

def _reconstruct_segments(
    timeline: RoundTimeline,
    initial_phase: Phase,
    initial_ground_top: str | None,
) -> list[tuple[int, int, Phase, str | None]]:
    """
    Replay the attempt log to recover contiguous phase segments.

    Returns list of (start_tick, end_tick_exclusive, phase, ground_top_name).

    Tick mechanics (from simulate_round):
      time_in_phase[phase] += TICK_SECONDS BEFORE the attempt resolves.
      So tick t is the LAST tick in the old phase; tick t+1 begins the new phase.
      Segment [start, tick+1) is in old phase; new segment starts at tick+1.
    """
    segs: list[tuple[int, int, Phase, str | None]] = []
    cur_phase = initial_phase
    cur_top   = initial_ground_top
    seg_start = 0

    for att in timeline.attempts:
        if not att.success:
            continue
        seg_end = att.tick + 1  # tick att.tick is last tick of old phase
        if seg_end > seg_start:
            segs.append((seg_start, seg_end, cur_phase, cur_top))
        cur_top   = att.attacker_name if att.phase_to == Phase.GROUND else None
        cur_phase = att.phase_to
        seg_start = seg_end

    if seg_start < timeline.ticks_per_round:
        segs.append((seg_start, timeline.ticks_per_round, cur_phase, cur_top))

    return segs


# ─── Per-segment computation ──────────────────────────────────────────────────

def _compute_segment(
    phase: Phase,
    start: int,
    end: int,
    ground_top_name: str | None,
    fa: Fighter,
    fb: Fighter,
    eff_chin_a: float,
    eff_chin_b: float,
) -> SegmentOutput:
    ticks    = end - start
    mid_tick = (start + end - 1) / 2.0
    rw       = math.exp(RECENCY_DECAY * mid_tick)

    score_a = score_b = 0.0
    sp_a    = sp_b    = 0.0   # striking pressure
    sub_a   = sub_b   = 0.0   # submission pressure
    dom_a   = dom_b   = 0.0   # dominance

    if phase == Phase.STANDING:
        comp_a = fa.boxing + fa.kickboxing
        comp_b = fb.boxing + fb.kickboxing
        gap    = comp_a - comp_b

        # Round-score: absolute skill floor governs whether a fighter can strike at
        # all; relative gap governs who wins the exchange.
        rate_a = BASE_STRIKE_RATE * _logistic_abs(comp_a) * _logistic(gap)
        rate_b = BASE_STRIKE_RATE * _logistic_abs(comp_b) * _logistic(-gap)
        score_a = rate_a * ticks
        score_b = rate_b * ticks

        # Striking pressure: landing output (same rate) x defender chin vulnerability.
        # Derived independently from round-score -- same rate, different modifier.
        sp_a = rate_a * (1.0 - eff_chin_b) * ticks
        sp_b = rate_b * (1.0 - eff_chin_a) * ticks

    elif phase == Phase.CLINCH:
        # Clinch striking favors muay thai range; clinch control itself adds passive.
        comp_a     = fa.boxing * 0.4 + fa.kickboxing * 0.5 + fa.clinch * 0.3
        comp_b     = fb.boxing * 0.4 + fb.kickboxing * 0.5 + fb.clinch * 0.3
        clinch_gap = fa.clinch - fb.clinch
        gap        = comp_a - comp_b

        # Passive: who controls the tie (sums to CLINCH_PASSIVE_SCALE; symmetric)
        passive_a = CLINCH_PASSIVE_SCALE * _logistic(clinch_gap)
        passive_b = CLINCH_PASSIVE_SCALE * _logistic(-clinch_gap)

        active_a = BASE_CLINCH_RATE * _logistic_abs(comp_a) * _logistic(gap)
        active_b = BASE_CLINCH_RATE * _logistic_abs(comp_b) * _logistic(-gap)

        score_a = (passive_a + active_a) * ticks
        score_b = (passive_b + active_b) * ticks

        # Pressure is active-output-only -- passive clinch control generates no finish threat
        sp_a = active_a * (1.0 - eff_chin_b) * ticks
        sp_b = active_b * (1.0 - eff_chin_a) * ticks

    elif phase == Phase.GROUND:
        if ground_top_name is None:
            # Defensive fallback -- should not occur in well-formed timeline.
            score_a = score_b = 0.05 * ticks
        else:
            top      = fa if ground_top_name == fa.name else fb
            bot      = fb if top is fa else fa
            is_a_top = (top is fa)
            eff_chin_bot = eff_chin_b if is_a_top else eff_chin_a

            wrestling_gap = top.wrestling - bot.wrestling

            # --- TOP fighter: passive positional dominance ---
            # High even with zero active work -- this is the "controls the round
            # without threatening a finish" scenario.  Round-score is independent of
            # finish-pressure here by design.
            passive_rate_top = GROUND_PASSIVE_RATE * _logistic(wrestling_gap)

            # --- TOP fighter: G&P (active, contributes to both tracks) ---
            gnp_off = top.boxing * 0.4 + top.power * 0.5
            gnp_def = bot.wrestling * 0.5 + bot.athleticism * 0.3 + bot.bjj * 0.2
            gnp_rate = BASE_GNP_RATE * _logistic_abs(gnp_off) * _logistic(gnp_off - gnp_def)

            # --- TOP fighter: submission attempts (active, no fatigue hook) ---
            sub_off = top.bjj * 0.8 + top.wrestling * 0.2
            sub_def = bot.bjj * 0.8 + bot.athleticism * 0.2
            sub_rate = BASE_SUB_RATE * _logistic_abs(sub_off) * _logistic(sub_off - sub_def)

            score_top = (passive_rate_top + gnp_rate + sub_rate) * ticks

            # Dominance: cumulative control quality (time * gap; drives post-round narrative)
            dom_top = DOMINANCE_PER_TICK * _logistic(wrestling_gap) * ticks

            # --- BOTTOM fighter: survival / resistance credit ---
            # Scaled by bottom fighter's OWN bjj+athleticism (ability to be active
            # and defend from bottom), not by the opponent's wrestling dominance.
            bot_grappling = bot.bjj * 0.5 + bot.athleticism * 0.5
            resistance_rate = GROUND_RESISTANCE_BASE * _logistic_abs(bot_grappling)
            score_bot = resistance_rate * ticks

            # Striking pressure: G&P output x bottom's chin vulnerability.
            # Passive positional hold generates ZERO striking pressure.
            sp_top = gnp_rate * (1.0 - eff_chin_bot) * ticks
            sp_bot = 0.0   # floor fighter: no meaningful striking threat from bottom

            # Submission pressure: top-position only; not fatigue-affected.
            sub_top = sub_rate * ticks
            sub_bot = 0.0  # guillotine (bottom guillotine) deferred to future session

            if is_a_top:
                score_a, score_b = score_top, score_bot
                sp_a, sp_b       = sp_top, sp_bot
                sub_a, sub_b     = sub_top, sub_bot
                dom_a, dom_b     = dom_top, 0.0
            else:
                score_a, score_b = score_bot, score_top
                sp_a, sp_b       = sp_bot, sp_top
                sub_a, sub_b     = sub_bot, sub_top
                dom_a, dom_b     = 0.0, dom_top

    return SegmentOutput(
        phase             = phase.value,
        start_tick        = start,
        end_tick          = end,
        ground_top_name   = ground_top_name,
        recency_weight    = rw,
        score_a           = score_a,
        score_b           = score_b,
        strike_pressure_a = sp_a,
        strike_pressure_b = sp_b,
        sub_pressure_a    = sub_a,
        sub_pressure_b    = sub_b,
        dominance_a       = dom_a,
        dominance_b       = dom_b,
    )


# ─── Main API ─────────────────────────────────────────────────────────────────

def compute_round_output(
    timeline: RoundTimeline,
    fa: Fighter,
    fb: Fighter,
    *,
    initial_phase: Phase = Phase.STANDING,
    initial_ground_top_name: str | None = None,
) -> RoundOutput:
    """
    Consume a RoundTimeline and produce per-segment scoring + finish-pressure.

    Fighter A = fa (positional in simulate_round), Fighter B = fb.
    initial_ground_top_name: only needed if initial_phase == Phase.GROUND.
    """
    eff_chin_a = _effective_chin(fa)
    eff_chin_b = _effective_chin(fb)

    # Resolve initial ground-top: if starting in GROUND with no top specified,
    # match phase_engine.py's placeholder (fa is top).
    top0 = initial_ground_top_name
    if initial_phase == Phase.GROUND and top0 is None:
        top0 = fa.name

    raw = _reconstruct_segments(timeline, initial_phase, top0)

    segments = [
        _compute_segment(ph, s, e, gtn, fa, fb, eff_chin_a, eff_chin_b)
        for s, e, ph, gtn in raw
    ]

    # Round-score uses recency weighting; pressure is raw cumulative.
    total_score_a = sum(seg.score_a * seg.recency_weight for seg in segments)
    total_score_b = sum(seg.score_b * seg.recency_weight for seg in segments)
    total_sp_a    = sum(seg.strike_pressure_a for seg in segments)
    total_sp_b    = sum(seg.strike_pressure_b for seg in segments)
    total_sub_a   = sum(seg.sub_pressure_a    for seg in segments)
    total_sub_b   = sum(seg.sub_pressure_b    for seg in segments)
    total_dom_a   = sum(seg.dominance_a        for seg in segments)
    total_dom_b   = sum(seg.dominance_b        for seg in segments)

    return RoundOutput(
        segments                = segments,
        total_score_a           = total_score_a,
        total_score_b           = total_score_b,
        total_strike_pressure_a = total_sp_a,
        total_strike_pressure_b = total_sp_b,
        total_sub_pressure_a    = total_sub_a,
        total_sub_pressure_b    = total_sub_b,
        effective_chin_a        = eff_chin_a,
        effective_chin_b        = eff_chin_b,
        total_dominance_a       = total_dom_a,
        total_dominance_b       = total_dom_b,
    )


# ─── Sample call ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import random
    from templates import generate_fighter
    from phase_engine import simulate_round

    random.seed(7)
    wres = generate_fighter("dagestan_sambo")
    strk = generate_fighter("muay_thai")

    print(f"Sample: {wres.name} (Dagestan) vs {strk.name} (Muay Thai)")
    print(f"  {wres.name[:28]:<28}  wrestl={wres.wrestling:>+.1f}  bjj={wres.bjj:>+.1f}"
          f"  boxing={wres.boxing:>+.1f}  chin={wres.chin:>+.1f}")
    print(f"  {strk.name[:28]:<28}  wrestl={strk.wrestling:>+.1f}  bjj={strk.bjj:>+.1f}"
          f"  boxing={strk.boxing:>+.1f}  chin={strk.chin:>+.1f}")
    print()

    tl = simulate_round(wres, strk)
    out = compute_round_output(tl, wres, strk)

    print(f"Effective chin:  {wres.name[:20]} {out.effective_chin_a:.3f}"
          f"  |  {strk.name[:20]} {out.effective_chin_b:.3f}")
    print()

    # Per-segment breakdown
    COLS = (
        f"{'#':>3}  {'Phase':<9} {'Ticks':>5}  {'Top':<22}"
        f"  {'RW':>5}"
        f"  {'ScoreA':>7} {'ScoreB':>7}"
        f"  {'SpA':>6} {'SpB':>6}"
        f"  {'SubA':>6} {'SubB':>6}"
        f"  {'DomA':>6} {'DomB':>6}"
    )
    print(COLS)
    print("-" * len(COLS))

    for i, seg in enumerate(out.segments, 1):
        top_label = seg.ground_top_name[:22] if seg.ground_top_name else "--"
        print(
            f"{i:>3}  {seg.phase:<9} {seg.end_tick - seg.start_tick:>5}"
            f"  {top_label:<22}"
            f"  {seg.recency_weight:>5.2f}"
            f"  {seg.score_a:>7.3f} {seg.score_b:>7.3f}"
            f"  {seg.strike_pressure_a:>6.3f} {seg.strike_pressure_b:>6.3f}"
            f"  {seg.sub_pressure_a:>6.3f} {seg.sub_pressure_b:>6.3f}"
            f"  {seg.dominance_a:>6.2f} {seg.dominance_b:>6.2f}"
        )

    print()
    wname = wres.name[:16]
    sname = strk.name[:16]

    print(f"{'':40} {wname:<18} {sname:<18}")
    print(f"  Round score (recency-weighted):   {out.total_score_a:>8.3f}           {out.total_score_b:>8.3f}")
    print(f"  Striking pressure (raw):          {out.total_strike_pressure_a:>8.3f}           {out.total_strike_pressure_b:>8.3f}")
    print(f"  Submission pressure (raw):        {out.total_sub_pressure_a:>8.3f}           {out.total_sub_pressure_b:>8.3f}")
    print(f"  Dominance level (cumulative):     {out.total_dominance_a:>8.2f}           {out.total_dominance_b:>8.2f}")
