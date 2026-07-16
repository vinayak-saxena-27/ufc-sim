"""
finish_check.py -- Stoppage detection from accumulated finish-pressure (Session 4c Part A).

Consumes the per-segment SegmentOutput stream from phase_output.py and checks whether
either fighter's accumulated finish-pressure has crossed a stoppage threshold.

Design:
  - Callable INCREMENTALLY: pass the list of SegmentOutput objects processed so far
    in a round; the fight loop calls this after EACH segment and breaks on stoppage.
  - Uses RECENCY-WEIGHTED pressure sums (same recency_weight already stored on each
    SegmentOutput from 4b) -- late-round sustained damage matters more than early.
  - Two independent pressure tracks (consistent with 4b's design invariant):
      striking_finish_pressure -> KO/TKO if threshold crossed
      sub_finish_pressure      -> submission if threshold crossed
  - Submission pressure is NOT influenced by the defender's fatigue/chin (that
    asymmetry lives in 4b; this module just reads what 4b produced).

What this module does NOT do (Part 2 / Part 3):
  - Loop across segments within a round -- that's the fight loop's job.
  - Aggregate across rounds -- also the fight loop.
  - Wire into simulate_fight() -- Part 2.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from engine.phase_output import SegmentOutput


# ─── Thresholds ───────────────────────────────────────────────────────────────
#
# FIRST-PASS ESTIMATES -- expect retuning after Part 3 calibration.
#
# Reference values (recency-weighted, from 4b verified outputs):
#   Close match, equal strikers, 60-tick STANDING:       ~6.0 / fighter
#   Elite striker (+25 box/kick) vs avg chin, 60-tick:  ~9.5 / fighter
#   Elite striker vs weak chin (-25), 60-tick STANDING: ~27  / fighter
#   Passive wrestler, no BJJ, 60-tick GROUND-top:       ~0.34 sub
#   Elite BJJ (+25), 60-tick GROUND-top vs avg defense: ~14.8 sub
#   Average BJJ vs average, 60-tick GROUND:             ~4.0  sub
#
# At 10.0: close match never finishes in 1 round; clearly one-sided finish
# likely within a single round for large skill+chin gaps; borderline cases
# typically take 2-3 rounds to accumulate.
#
# SUBMISSION_THRESHOLD raised from 10.0 -> 30.0 (Session 4c Fix):
# Diagnostic showed recency-weighted R1 sub pressure averaging 11-12 for a
# +29-overall Dagestan anchor vs Tier 2 pool, crossing 10.0 in 70% of R1s and
# causing near-100% win rates. 30.0 sets the floor well above the observed R1
# max (~15.4), so subs require either extreme skill gaps or multi-round
# accumulation in later rounds. KO_TKO_THRESHOLD left at 10.0 this pass.
# Retuned 2026-07-15 (calibration session): the original 10.0/23.0 values
# produced a ~5-7% overall finish rate against real-world's 44.5-52.6% target
# (confirmed via isolated same-tier fight sampling -- even elite-vs-elite
# matchups almost never crossed either threshold). Independent of SCALE=43.0/
# win_probability() -- these thresholds govern HOW a fight ends, never WHO
# wins, so smoke_test.py's win-rate calibration anchor is unaffected by this
# change (re-verified after retuning). See CALIBRATION_LOG.md for the sweep
# methodology and history.
KO_TKO_THRESHOLD:    float = 2.8
SUBMISSION_THRESHOLD: float = 10.0

# Weight-class KO/TKO power scaling (calibration session, 2026-07-15).
# Real-world finish rate scales strongly with weight (bigger = more knockout
# power) while submission rate stays roughly flat across divisions -- so this
# multiplier ONLY scales striking pressure before the KO_TKO_THRESHOLD check,
# never submission pressure. Keyed by Fighter.weight_class; unknown/missing
# keys (e.g. smoke_test.py's flat test populations) fall back to 1.0, an exact
# no-op that preserves the existing SCALE=43.0 calibration anchor.
# Real-world lightweight/welterweight KO rates are nearly identical (29%/30%)
# with heavyweight far higher (45-48%) -- the multiplier spread mirrors that
# shape rather than a uniform per-step gradient. First-pass estimates -- see
# CALIBRATION_LOG.md for tuning history.
WEIGHT_CLASS_STRIKE_MULTIPLIER: dict[str, float] = {
    "heavyweight":  2.30,
    "welterweight": 1.00,
    "lightweight":  0.95,
}

# Real-world submission-method breakdown (chokes ~79%, joint locks ~15%,
# leg locks ~3%, other ~3%). Cosmetic reporting tag only -- rolled once when a
# submission FinishEvent fires, no mechanical effect on the fight itself.
SUBMISSION_TYPE_WEIGHTS: dict[str, float] = {
    "choke":      0.79,
    "joint_lock": 0.15,
    "leg_lock":   0.03,
    "other":      0.03,
}


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class FinishEvent:
    """Describes a fight stoppage."""
    winner_name:   str
    loser_name:    str
    method:        str        # "KO/TKO" or "submission"
    segment_index: int        # 0-indexed position within the round's segment list
    winner_id:     str | None = None   # stable fighter_id; None for legacy/test callers
    loser_id:      str | None = None
    submission_type: str | None = None  # set only when method == "submission"


def _roll_submission_type() -> str:
    """Draw a cosmetic submission-method tag from SUBMISSION_TYPE_WEIGHTS."""
    return random.choices(
        list(SUBMISSION_TYPE_WEIGHTS.keys()),
        weights=list(SUBMISSION_TYPE_WEIGHTS.values()),
        k=1,
    )[0]


# ─── Core check ───────────────────────────────────────────────────────────────

def check_finish(
    segments: list[SegmentOutput],
    fa_name:  str,
    fb_name:  str,
    *,
    fa_id:       str | None = None,
    fb_id:       str | None = None,
    prior_sub_a: float = 0.0,
    prior_sub_b: float = 0.0,
    strike_multiplier: float = 1.0,
) -> FinishEvent | None:
    """
    Check whether accumulated finish-pressure has crossed a stoppage threshold.

    Call after each segment with ALL segments completed so far in this round.
    Returns None if no stoppage, or a FinishEvent on the first threshold crossing.

    prior_sub_a / prior_sub_b: carry-over sub pressure from previous rounds
    (already decayed by SUB_PRESSURE_ROUND_DECAY in fight_engine). Striking
    pressure does not carry across rounds (corner stoppage resets that clock).

    strike_multiplier: weight-class KO/TKO power scaling (WEIGHT_CLASS_STRIKE_
    MULTIPLIER above), applied only to striking pressure -- submission pressure
    is deliberately untouched so sub rate stays weight-class-flat. Default 1.0
    is an exact no-op for any caller that doesn't pass a real weight class.

    Priority: striking KO/TKO checked before submission. Simultaneous crossings
    resolved in fa's favor -- placeholder tiebreak.

    Recency weighting: segment.recency_weight (exp(RECENCY_DECAY * mid_tick)) is
    pre-computed by 4b and stored on each SegmentOutput, so we just multiply here.
    """
    if not segments:
        return None

    idx = len(segments) - 1

    wsp_a  = strike_multiplier * sum(s.strike_pressure_a * s.recency_weight for s in segments)
    wsp_b  = strike_multiplier * sum(s.strike_pressure_b * s.recency_weight for s in segments)
    wsub_a = prior_sub_a + sum(s.sub_pressure_a * s.recency_weight for s in segments)
    wsub_b = prior_sub_b + sum(s.sub_pressure_b * s.recency_weight for s in segments)

    # Striking finishes (KO/TKO)
    if wsp_a >= KO_TKO_THRESHOLD and wsp_b >= KO_TKO_THRESHOLD:
        # Both crossed in the same segment.  Find which crossed first by interpolating
        # within the last segment: t = (threshold - pressure_before_segment) / segment_delta.
        # Smaller t = earlier crossing = that fighter's striking wins.
        last    = segments[-1]
        delta_a = strike_multiplier * last.strike_pressure_a * last.recency_weight
        delta_b = strike_multiplier * last.strike_pressure_b * last.recency_weight
        t_a = (KO_TKO_THRESHOLD - (wsp_a - delta_a)) / delta_a if delta_a > 0 else 0.0
        t_b = (KO_TKO_THRESHOLD - (wsp_b - delta_b)) / delta_b if delta_b > 0 else 0.0
        if t_a <= t_b:
            return FinishEvent(fa_name, fb_name, "KO/TKO", idx, fa_id, fb_id)
        else:
            return FinishEvent(fb_name, fa_name, "KO/TKO", idx, fb_id, fa_id)
    if wsp_a >= KO_TKO_THRESHOLD:
        return FinishEvent(fa_name, fb_name, "KO/TKO", idx, fa_id, fb_id)
    if wsp_b >= KO_TKO_THRESHOLD:
        return FinishEvent(fb_name, fa_name, "KO/TKO", idx, fb_id, fa_id)

    # Submission finishes
    if wsub_a >= SUBMISSION_THRESHOLD and wsub_b >= SUBMISSION_THRESHOLD:
        last        = segments[-1]
        delta_sub_a = last.sub_pressure_a * last.recency_weight
        delta_sub_b = last.sub_pressure_b * last.recency_weight
        t_a = (SUBMISSION_THRESHOLD - (wsub_a - delta_sub_a)) / delta_sub_a if delta_sub_a > 0 else 0.0
        t_b = (SUBMISSION_THRESHOLD - (wsub_b - delta_sub_b)) / delta_sub_b if delta_sub_b > 0 else 0.0
        if t_a <= t_b:
            return FinishEvent(fa_name, fb_name, "submission", idx, fa_id, fb_id, _roll_submission_type())
        else:
            return FinishEvent(fb_name, fa_name, "submission", idx, fb_id, fa_id, _roll_submission_type())
    if wsub_a >= SUBMISSION_THRESHOLD:
        return FinishEvent(fa_name, fb_name, "submission", idx, fa_id, fb_id, _roll_submission_type())
    if wsub_b >= SUBMISSION_THRESHOLD:
        return FinishEvent(fb_name, fa_name, "submission", idx, fb_id, fa_id, _roll_submission_type())

    return None


def pressure_snapshot(segments: list[SegmentOutput]) -> dict[str, float]:
    """
    Return current recency-weighted finish-pressure for all four tracks.
    Useful for fight-loop logging without duplicating the sum logic.
    """
    return {
        "strike_a": sum(s.strike_pressure_a * s.recency_weight for s in segments),
        "strike_b": sum(s.strike_pressure_b * s.recency_weight for s in segments),
        "sub_a":    sum(s.sub_pressure_a    * s.recency_weight for s in segments),
        "sub_b":    sum(s.sub_pressure_b    * s.recency_weight for s in segments),
    }


# ─── Sample call ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import math
    from engine.phase_output import SegmentOutput

    RECENCY_DECAY = 0.02  # must match phase_output.py

    def _seg(
        start: int, end: int, *,
        sp_a: float = 0.0, sp_b: float = 0.0,
        sub_a: float = 0.0, sub_b: float = 0.0,
        phase: str = "STANDING",
    ) -> SegmentOutput:
        mid = (start + end - 1) / 2.0
        rw  = math.exp(RECENCY_DECAY * mid)
        return SegmentOutput(
            phase=phase, start_tick=start, end_tick=end,
            ground_top_name=None, recency_weight=rw,
            score_a=0.0, score_b=0.0,
            strike_pressure_a=sp_a, strike_pressure_b=sp_b,
            sub_pressure_a=sub_a,   sub_pressure_b=sub_b,
            dominance_a=0.0,        dominance_b=0.0,
        )

    print(f"Thresholds:  KO/TKO = {KO_TKO_THRESHOLD}  Submission = {SUBMISSION_THRESHOLD}")
    print()

    # ── Scenario 1: sustained one-sided striking, detect incrementally ─────────
    # Segments of sp_a=5.0; weighted:
    #   seg 0 [0,20)  mid=9.5   rw=1.209  cumulative=6.04  < 10.0
    #   seg 1 [20,40) mid=29.5  rw=1.804  cumulative=15.1  > 10.0  -> FINISH
    print("Scenario 1 -- Sustained one-sided striking (sp_a=5.0 per segment)")
    segs_s1 = [
        _seg( 0, 20, sp_a=5.0),
        _seg(20, 40, sp_a=5.0),
        _seg(40, 60, sp_a=5.0),
    ]
    accumulated: list[SegmentOutput] = []
    finished = False
    for i, seg in enumerate(segs_s1):
        accumulated.append(seg)
        snap   = pressure_snapshot(accumulated)
        result = check_finish(accumulated, "Puncher", "GlassChin")
        bar    = "<<< FINISH" if result else "..."
        print(f"  after seg {i} [{seg.start_tick:>2},{seg.end_tick:>2})  "
              f"rw={seg.recency_weight:.3f}  "
              f"w_strike_A={snap['strike_a']:>6.3f}  {bar}")
        if result:
            print(f"  RESULT: {result.winner_name} defeats {result.loser_name} by {result.method}")
            finished = True
            break
    if not finished:
        print("  Round completed -- no finish")

    print()

    # ── Scenario 2: balanced exchange, should NOT cross threshold ─────────────
    # Each fighter sp=2.0 per segment; weighted total for 3 segs ≈ 10.2
    # (right at threshold -- let's use sp=1.5 to stay clearly below)
    print("Scenario 2 -- Balanced exchange (sp=1.5 each per segment, 3 segments)")
    segs_s2 = [
        _seg( 0, 20, sp_a=1.5, sp_b=1.5),
        _seg(20, 40, sp_a=1.5, sp_b=1.5),
        _seg(40, 60, sp_a=1.5, sp_b=1.5),
    ]
    snap2   = pressure_snapshot(segs_s2)
    result2 = check_finish(segs_s2, "Fighter A", "Fighter B")
    print(f"  Total w_strike_A={snap2['strike_a']:.3f}  w_strike_B={snap2['strike_b']:.3f}"
          f"  (threshold={KO_TKO_THRESHOLD})")
    print(f"  Result: {'FINISH -- ' + result2.method if result2 else 'No finish -- goes to judges'}")

    print()

    # ── Scenario 3: submission finish from dominant GROUND control ─────────────
    # sub_a=8.0 raw, single 60-tick segment: rw=1.804, weighted=14.4 > 10.0
    print("Scenario 3 -- Submission from GROUND-top control (sub_a=8.0, 60-tick segment)")
    segs_s3 = [_seg(0, 60, sub_a=8.0, phase="GROUND")]
    snap3   = pressure_snapshot(segs_s3)
    result3 = check_finish(segs_s3, "Grappler", "Wrestler")
    print(f"  w_sub_A={snap3['sub_a']:.3f}  w_strike_A={snap3['strike_a']:.3f}"
          f"  (sub threshold={SUBMISSION_THRESHOLD})")
    print(f"  Result: "
          f"{result3.winner_name + ' defeats ' + result3.loser_name + ' by ' + result3.method if result3 else 'No finish'}")

    print()

    # ── Scenario 4: striking pressure but submission pressure stays separate ───
    # Confirm the two tracks are independent even at the threshold level
    # High strike, sub=0
    print("Scenario 4 -- High strike_A (crosses threshold), sub_A=0 (never set)")
    segs_s4 = [_seg(0, 60, sp_a=7.0)]  # weighted: 7.0 * 1.804 = 12.6 > 10.0
    snap4   = pressure_snapshot(segs_s4)
    result4 = check_finish(segs_s4, "Boxer", "Defender")
    print(f"  w_strike_A={snap4['strike_a']:.3f}  w_sub_A={snap4['sub_a']:.3f}")
    print(f"  Result: {result4.method if result4 else 'no finish'}  "
          f"(sub_A={snap4['sub_a']:.3f} never crossed sub threshold -- tracks independent)")
