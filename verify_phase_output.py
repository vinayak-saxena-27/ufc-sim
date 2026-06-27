"""
verify_phase_output.py -- Session 4b Part 2 verification suite for phase_output.py.

Four focused tests that verify the two-track design (round-score vs finish-pressure)
works as intended.  All tests use synthetic RoundTimeline objects constructed directly
so the phase/position/timing is fully controlled -- no reliance on random simulation.

  1. Decoupling:    GROUND-top with zero offensive weapons -> HIGH score, LOW pressure
  2. Active bump:   same position with full offense -> pressure rises, score same/higher
  3. Independence:  striking and submission tracks move without coupling
  4. Recency:       same 20 ticks of GROUND-top, late vs early -- late scores higher
"""
from __future__ import annotations

import sys

from fighter import Fighter
from phase_engine import (
    Phase, TransitionType, TransitionAttempt, RoundTimeline,
)
from phase_output import compute_round_output, RoundOutput

_failures: list[str] = []


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _f(name: str, **attrs) -> Fighter:
    """Fighter with all unspecified sub-attributes at 0 (league average)."""
    defaults = dict(
        wrestling=0.0, bjj=0.0, clinch=0.0, boxing=0.0, kickboxing=0.0,
        power=0.0, cardio=0.0, chin=0.0, athleticism=0.0, fight_iq=0.0,
    )
    defaults.update(attrs)
    return Fighter(name=name, age=28, region="test", template="test", **defaults)


def _att(
    tick: int, phase_from: Phase, phase_to: Phase,
    trans: TransitionType, attacker_name: str,
    *, success: bool = True,
) -> TransitionAttempt:
    return TransitionAttempt(
        tick=tick, phase_from=phase_from, phase_to=phase_to,
        transition=trans, attacker_name=attacker_name,
        success=success, stamina_cost=5.0 if success else 9.0,
    )


def _tl(attempts: list[TransitionAttempt] | None = None) -> RoundTimeline:
    """Synthetic timeline; time_in_phase is zeroed (compute_round_output doesn't use it)."""
    return RoundTimeline(
        time_in_phase={p.value: 0.0 for p in Phase},
        attempts=attempts or [],
        end_stamina_a=100.0,
        end_stamina_b=100.0,
    )


def _show(label: str, out: RoundOutput, fa_name: str, fb_name: str) -> None:
    print(f"  {label}")
    print(f"    {fa_name:<26}  score={out.total_score_a:>8.3f}"
          f"  strike_p={out.total_strike_pressure_a:>6.3f}"
          f"  sub_p={out.total_sub_pressure_a:>6.3f}"
          f"  dom={out.total_dominance_a:>6.2f}")
    print(f"    {fb_name:<26}  score={out.total_score_b:>8.3f}"
          f"  strike_p={out.total_strike_pressure_b:>6.3f}"
          f"  sub_p={out.total_sub_pressure_b:>6.3f}"
          f"  dom={out.total_dominance_b:>6.2f}")


def chk(label: str, actual: float, lo: float, hi: float) -> bool:
    ok     = lo <= actual <= hi
    status = "PASS" if ok else "FAIL"
    hi_str = "inf" if hi > 9000 else f"{hi:.2f}"
    print(f"    [{status}]  {label:<48}  {actual:.4f}  (want {lo:.2f}--{hi_str})")
    if not ok:
        _failures.append(label)
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1 -- Decoupling: passive GROUND-top -> high score, low finish-pressure
# ═══════════════════════════════════════════════════════════════════════════════
#
# A wrestler who holds top position all round but has zero offensive weapons:
# boxing, power, and bjj are all extreme-negative so gnp_rate and sub_rate
# are near-zero.  The passive positional contribution to round-score is still
# large (GROUND_PASSIVE_RATE * logistic(wrestling_gap)).
#
# Expected (pre-calculated):
#   total_score_a   ~= 26.1   (passive dominance: 0.25 * logistic(55) * 60 ticks * rw=1.804)
#   strike_pressure ~= 0.019  (near-zero: terrible boxer / no power)
#   sub_pressure    ~= 0.188  (near-zero: terrible bjj)
# Neutral case (gap=0): score ~= 13.5; threshold confirms active wrestling advantage > 60% higher.
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("TEST 1 -- Decoupling: passive GROUND-top -> high score, low pressure")
print("  passive_wrestler  wrestl=+30  boxing=-30  power=-30  bjj=-30")
print("  victim            wrestl=-25  bjj=-25")
print("  Timeline: 60-tick all-GROUND, wrestler always on top, no transitions")
print("=" * 72)

passive_wres = _f("passive_wrestler",
                  wrestling=+30.0, boxing=-30.0, power=-30.0, bjj=-30.0)
victim       = _f("victim", wrestling=-25.0, bjj=-25.0)

out1 = compute_round_output(
    _tl(), passive_wres, victim,
    initial_phase=Phase.GROUND,
    initial_ground_top_name=passive_wres.name,
)
print()
_show("passive_wrestler (top) vs victim (bottom)", out1, passive_wres.name, victim.name)
print()
chk("score_a  > 22.0  (high: passive positional dominance)",
    out1.total_score_a,             22.0, 9999.0)
chk("strike_p < 2.0   (low: no boxing/power weapons)",
    out1.total_strike_pressure_a,    0.0, 2.0)
chk("sub_p    < 2.0   (low: no bjj weapons)",
    out1.total_sub_pressure_a,       0.0, 2.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2 -- Active bump: same dominant position + full offensive arsenal
# ═══════════════════════════════════════════════════════════════════════════════
#
# Replace the passive wrestler with one who also has elite boxing, power, and bjj.
# Same victim, same synthetic 60-tick GROUND timeline.
#
# Expected:
#   total_score_a   ~= 95.5   (passive + active; higher than test 1's 62.1)
#   strike_pressure ~= 5.27   (active G&P; meaningfully > test 1's 0.019)
#   sub_pressure    ~= 8.21   (active subs; meaningfully > test 1's 0.188)
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("TEST 2 -- Active bump: same GROUND-top + full offensive arsenal")
print("  active_wrestler   wrestl=+30  boxing=+25  power=+25  bjj=+25")
print("  victim: same as test 1")
print("  Expect: score >= T1 (position + activity > position alone)")
print("          finish-pressure meaningfully higher than test 1")
print("=" * 72)

active_wres = _f("active_wrestler",
                 wrestling=+30.0, boxing=+25.0, power=+25.0, bjj=+25.0)

out2 = compute_round_output(
    _tl(), active_wres, victim,
    initial_phase=Phase.GROUND,
    initial_ground_top_name=active_wres.name,
)
print()
_show("active_wrestler (top) vs victim (bottom)", out2, active_wres.name, victim.name)
print()
print("  Side-by-side vs test 1 (same position, different offensive output):")
hdr = f"  {'':30}  {'Passive (T1)':>12}  {'Active (T2)':>12}  {'Delta':>10}"
print(hdr)
print(f"  {'score':30}  "
      f"{out1.total_score_a:>12.3f}  {out2.total_score_a:>12.3f}  "
      f"{out2.total_score_a - out1.total_score_a:>+10.3f}")
print(f"  {'striking finish-pressure':30}  "
      f"{out1.total_strike_pressure_a:>12.3f}  {out2.total_strike_pressure_a:>12.3f}  "
      f"{out2.total_strike_pressure_a - out1.total_strike_pressure_a:>+10.3f}")
print(f"  {'submission finish-pressure':30}  "
      f"{out1.total_sub_pressure_a:>12.3f}  {out2.total_sub_pressure_a:>12.3f}  "
      f"{out2.total_sub_pressure_a - out1.total_sub_pressure_a:>+10.3f}")
print()
chk("active score >= passive score (activity never hurts position score)",
    out2.total_score_a - out1.total_score_a,                          0.0, 9999.0)
chk("strike_p delta > 2.0 (active G&P created meaningful pressure)",
    out2.total_strike_pressure_a - out1.total_strike_pressure_a,     2.0, 9999.0)
chk("sub_p delta > 2.0 (active sub work created meaningful pressure)",
    out2.total_sub_pressure_a - out1.total_sub_pressure_a,           2.0, 9999.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3 -- Track independence: striking and submission pressure don't couple
# ═══════════════════════════════════════════════════════════════════════════════
#
# Scenario A: elite strikers in STANDING for a full 60-tick round.
#   -> striking pressure HIGH, submission pressure exactly 0 (STANDING has no subs)
#
# Scenario B: elite BJJ specialist on GROUND-top for 60 ticks, terrible boxer.
#   -> submission pressure HIGH, striking pressure near-zero (bad G&P)
#
# Key check: sub_p inflating when strike_p is high (A) or vice versa (B) would
# indicate the tracks are coupled -- they should be independent.
#
# Expected (pre-calculated):
#   ScenA: strike_p ~= 8.49, sub_p = 0.000 (exactly)
#   ScenB: strike_p ~= 0.040, sub_p ~= 7.96
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("TEST 3 -- Track independence: striking vs submission pressure")
print("  Scenario A (STANDING strikers):    high strike_p, zero sub_p")
print("  Scenario B (GROUND-top BJJ, no GnP): near-zero strike_p, high sub_p")
print("=" * 72)

# Scenario A: dominant striker vs inferior striker, full round in STANDING
striker_a = _f("striker_a", boxing=+25.0, kickboxing=+25.0, power=+20.0, bjj=-25.0)
striker_b = _f("striker_b", boxing=+10.0, kickboxing=+10.0, chin=-5.0,   bjj=-20.0)
out_a = compute_round_output(_tl(), striker_a, striker_b)   # default initial_phase=STANDING

# Scenario B: BJJ specialist on GROUND-top -- excellent sub game, terrible G&P
grappler = _f("grappler", wrestling=+25.0, bjj=+25.0, boxing=-25.0, power=-25.0)
grappled = _f("grappled", wrestling=-20.0, bjj=-20.0, athleticism=+5.0)
out_b = compute_round_output(
    _tl(), grappler, grappled,
    initial_phase=Phase.GROUND,
    initial_ground_top_name=grappler.name,
)

print()
_show("Scenario A -- STANDING strikers (60 ticks)", out_a, striker_a.name, striker_b.name)
print()
_show("Scenario B -- GROUND-top BJJ / no G&P (60 ticks)", out_b, grappler.name, grappled.name)
print()
print("  Track separation at a glance:")
print(f"    Scenario A   strike_p={out_a.total_strike_pressure_a:.3f}   sub_p={out_a.total_sub_pressure_a:.3f}"
      f"  <-- strike HIGH, sub = 0")
print(f"    Scenario B   strike_p={out_b.total_strike_pressure_a:.3f}   sub_p={out_b.total_sub_pressure_a:.3f}"
      f"  <-- strike near-0, sub HIGH")
print()
chk("ScenA sub_p == 0 exactly (STANDING cannot generate sub pressure)",
    out_a.total_sub_pressure_a,                                       0.0, 0.001)
chk("ScenB strike_p < 0.5 (terrible boxer on top, near-zero G&P threat)",
    out_b.total_strike_pressure_a,                                    0.0, 0.5)
chk("ScenA strike_p >> ScenB strike_p (delta > 1.0)",
    out_a.total_strike_pressure_a - out_b.total_strike_pressure_a,   1.0, 9999.0)
chk("ScenB sub_p >> ScenA sub_p (delta > 3.0)",
    out_b.total_sub_pressure_a - out_a.total_sub_pressure_a,         3.0, 9999.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4 -- Recency weighting: late GROUND dominance vs early GROUND dominance
# ═══════════════════════════════════════════════════════════════════════════════
#
# Same fighter pair, same 20-tick GROUND-top block for the wrestler -- but in
# Timeline A the block is at ticks 40-60 (late round), and in Timeline B it is
# at ticks 0-20 (early round).
#
# Recency weight = exp(RECENCY_DECAY * mid_tick).  The GROUND segment mid-tick is
# 49.5 in A (rw ~2.69) vs 9.5 in B (rw ~1.21), so A's GROUND block gets ~2.22x
# more round-score weight even though the raw output is identical.
#
# This is a sanity check -- the exact ratio is a tuning choice, not a bug threshold.
# We just confirm the direction (late > early) and report the actual multiplier.
#
# Timeline A: DIRECT_TAKEDOWN at tick 39  (wres initiates, last STANDING tick -> GROUND ticks 40-59)
# Timeline B: starts in GROUND; SCRAMBLE at tick 19 (strk initiates, back to STANDING ticks 20-59)
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("TEST 4 -- Recency weighting: late GROUND vs early GROUND (20 ticks each)")
print("  wres_r: wrestl=+25, bjj=+10, boxing=-15, kickboxing=-15, power=+10")
print("  strk_r: wrestl=-20, bjj=-15, boxing=+15, kickboxing=+20, chin=+10")
print("  Sanity check: late-ground wrestler should score higher (no strict bound)")
print("=" * 72)

wres_r = _f("wres_r", wrestling=+25.0, bjj=+10.0,
            boxing=-15.0, kickboxing=-15.0, power=+10.0)
strk_r = _f("strk_r", wrestling=-20.0, bjj=-15.0,
            boxing=+15.0, kickboxing=+20.0, chin=+10.0)

# Timeline A: takedown at tick 39 -> GROUND segment [40, 60)
tl_A = _tl([
    _att(39, Phase.STANDING, Phase.GROUND, TransitionType.DIRECT_TAKEDOWN, wres_r.name),
])
outA = compute_round_output(tl_A, wres_r, strk_r, initial_phase=Phase.STANDING)

# Timeline B: starts GROUND (wres on top), scramble at tick 19 -> STANDING [20, 60)
tl_B = _tl([
    _att(19, Phase.GROUND, Phase.STANDING, TransitionType.SCRAMBLE, strk_r.name),
])
outB = compute_round_output(
    tl_B, wres_r, strk_r,
    initial_phase=Phase.GROUND,
    initial_ground_top_name=wres_r.name,
)

print()
print("  Per-segment breakdown -- Timeline A (late GROUND):")
for seg in outA.segments:
    top_str = str(seg.ground_top_name)[:18] if seg.ground_top_name else "--"
    print(f"    {seg.phase:<9} [{seg.start_tick:>2},{seg.end_tick:>3})  "
          f"top={top_str:<18}  rw={seg.recency_weight:.3f}"
          f"  score_a(raw)={seg.score_a:.3f}")
print()
print("  Per-segment breakdown -- Timeline B (early GROUND):")
for seg in outB.segments:
    top_str = str(seg.ground_top_name)[:18] if seg.ground_top_name else "--"
    print(f"    {seg.phase:<9} [{seg.start_tick:>2},{seg.end_tick:>3})  "
          f"top={top_str:<18}  rw={seg.recency_weight:.3f}"
          f"  score_a(raw)={seg.score_a:.3f}")
print()
_show("Timeline A totals (late GROUND)", outA, wres_r.name, strk_r.name)
print()
_show("Timeline B totals (early GROUND)", outB, wres_r.name, strk_r.name)
print()

score_A = outA.total_score_a
score_B = outB.total_score_a
ratio   = score_A / score_B if score_B > 0 else float("inf")
print(f"  Wrestler round-score (recency-weighted):")
print(f"    Timeline A  late GROUND  [40,60): {score_A:.3f}")
print(f"    Timeline B  early GROUND  [0,20): {score_B:.3f}")
print(f"    Late / early ratio:               {ratio:.3f}x")
print()
chk("Late GROUND scores higher than early (recency weighting)", ratio, 1.0, 9999.0)

if failures := _failures:
    print()
    print(f"FAILED ({len(failures)} check(s)):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print()
    print("All checks passed.")
