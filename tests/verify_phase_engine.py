"""
verify_phase_engine.py — Verification tests for phase_engine.py (Session 4a).

Run standalone: python verify_phase_engine.py
Do NOT patch phase_engine.py from here — if a test fails, read the FINDINGS
section and diagnose before touching the engine.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from fighter import Fighter
from phase_engine import (
    Phase, TransitionType,
    simulate_round,
    ROUND_SECONDS, SUCCESS_STAMINA_COST, FAIL_STAMINA_COST,
)

random.seed(99)
N = 400   # rounds per matchup for aggregate statistics


# ─── Fighter factory ──────────────────────────────────────────────────────────

def _f(name: str, *, wrestling=0.0, clinch=0.0, athleticism=0.0,
       bjj=0.0, boxing=0.0, kickboxing=0.0) -> Fighter:
    return Fighter(
        name=name, age=28, region="test", template="test",
        wrestling=wrestling, clinch=clinch, athleticism=athleticism,
        bjj=bjj, boxing=boxing, kickboxing=kickboxing,
    )


# ─── Aggregation helpers ──────────────────────────────────────────────────────

def avg_splits(fa: Fighter, fb: Fighter, n: int) -> dict[str, float]:
    totals: dict[str, float] = {p.value: 0.0 for p in Phase}
    for _ in range(n):
        tl = simulate_round(fa, fb)
        for ph, secs in tl.time_in_phase.items():
            totals[ph] += secs / ROUND_SECONDS
    return {k: v / n for k, v in totals.items()}


def agg_stats(fa: Fighter, fb: Fighter, n: int) -> dict[str, dict]:
    acc: dict[str, list] = {
        fa.name: [0, 0, 0.0],
        fb.name: [0, 0, 0.0],
    }
    for _ in range(n):
        tl = simulate_round(fa, fb)
        for a in tl.attempts:
            bucket = acc[a.attacker_name]
            bucket[0] += 1
            if a.success:
                bucket[1] += 1
            bucket[2] += a.stamina_cost
    return {
        name: {
            "att":  v[0] / n,
            "suc":  v[1] / n,
            "fail": (v[0] - v[1]) / n,
            "stam": v[2] / n,
            "sr":   v[1] / max(v[0], 1),
        }
        for name, v in acc.items()
    }


def fmt_splits(splits: dict[str, float], indent: str = "    ") -> None:
    for ph, pct in splits.items():
        bar = "#" * int(pct * 40)
        print(f"{indent}{ph:<10} {pct:>5.1%}  {bar}")


def chk(label: str, actual: float, lo: float, hi: float = 1.01) -> bool:
    ok = lo <= actual <= hi
    print(f"  [{'PASS' if ok else 'FAIL'}]  {label}:  {actual:.1%}  (want {lo:.0%}-{hi:.0%})")
    return ok


# ─── Archetype fighters ───────────────────────────────────────────────────────

striker_a = _f("Striker-A", wrestling=-20, clinch=-15, athleticism=5,
               boxing=25, kickboxing=22)
striker_b = _f("Striker-B", wrestling=-20, clinch=-15, athleticism=5,
               boxing=25, kickboxing=22)

grappler_a = _f("Grappler-A", wrestling=22, clinch=18, bjj=20, athleticism=10)
grappler_b = _f("Grappler-B", wrestling=22, clinch=18, bjj=20, athleticism=10)

wrestler   = _f("Strong-Wrestler", wrestling=22, clinch=10, athleticism=8)
weak_strk  = _f("Weak-TD-Striker",  wrestling=-18, clinch=-10, athleticism=5)

close_a = _f("Close-A", wrestling=8, clinch=5, athleticism=5)
close_b = _f("Close-B", wrestling=5, clinch=3, athleticism=3)


# ─── TEST 1: Style extremes ───────────────────────────────────────────────────

print("=" * 72)
print(f"TEST 1: STYLE EXTREMES  ({N} rounds each)")
print("=" * 72)

# (a) Striker vs Striker
sp_1a = avg_splits(striker_a, striker_b, N)
print(f"\n(a) {striker_a.name} vs {striker_b.name}")
print(f"    wrestl={striker_a.wrestling:+.0f}  clinch={striker_a.clinch:+.0f}  athl={striker_a.athleticism:+.0f}  (both identical)")
print(f"    Spec target: overwhelmingly STANDING (~90%+, threshold 70%)")
fmt_splits(sp_1a)
p_1a = chk("STANDING >= 70%", sp_1a["STANDING"], 0.70)

# (b) Grappler vs Grappler
sp_1b = avg_splits(grappler_a, grappler_b, N)
print(f"\n(b) {grappler_a.name} vs {grappler_b.name}")
print(f"    wrestl={grappler_a.wrestling:+.0f}  clinch={grappler_a.clinch:+.0f}  athl={grappler_a.athleticism:+.0f}  (both identical)")
print(f"    Spec target: mostly CLINCH/GROUND (CLINCH+GROUND > 60%)")
fmt_splits(sp_1b)
p_1b = chk("CLINCH+GROUND >= 60%", sp_1b["CLINCH"] + sp_1b["GROUND"], 0.60)
print(f"    Contrast with 1a: striker-striker GROUND={sp_1a['GROUND']:.0%}  grappler-grappler GROUND={sp_1b['GROUND']:.0%}")

# (c) Strong wrestler vs Weak-TD-defense striker
sp_1c = avg_splits(wrestler, weak_strk, N)
gap_wrestle_1c = wrestler.wrestling - weak_strk.wrestling
print(f"\n(c) {wrestler.name} vs {weak_strk.name}  (wrestl gap={gap_wrestle_1c:+.0f})")
print(f"    Wrestler:   wrestl={wrestler.wrestling:+.0f}  clinch={wrestler.clinch:+.0f}  athl={wrestler.athleticism:+.0f}")
print(f"    TD-Striker: wrestl={weak_strk.wrestling:+.0f}  clinch={weak_strk.clinch:+.0f}  athl={weak_strk.athleticism:+.0f}")
print(f"    Spec target: heavily GROUND (>= 60%)")
fmt_splits(sp_1c)
p_1c = chk("GROUND >= 60%", sp_1c["GROUND"], 0.60)


# ─── TEST 2: Gradient ─────────────────────────────────────────────────────────

print()
print("=" * 72)
print(f"TEST 2: GRADIENT -- SMALL vs LARGE SKILL GAP  ({N} rounds)")
print("=" * 72)

sp_2 = avg_splits(close_a, close_b, N)
gap_wrestle_2 = close_a.wrestling - close_b.wrestling

print(f"\n  Large-gap (1c): {wrestler.name} vs {weak_strk.name}  (wrestl gap={gap_wrestle_1c:+.0f})")
print(f"  Small-gap  (2): {close_a.name} vs {close_b.name}  (wrestl gap={gap_wrestle_2:+.0f})")
print(f"\n  Small-gap phase split:")
fmt_splits(sp_2)
delta_ground = sp_1c["GROUND"] - sp_2["GROUND"]
print(f"\n  GROUND: large-gap={sp_1c['GROUND']:.1%}  small-gap={sp_2['GROUND']:.1%}  delta={delta_ground:+.1%}")
p_2 = chk("Large-gap GROUND exceeds small-gap by >= 10pp", delta_ground, 0.10)


# ─── TEST 3: Trace + stamina + absolute-floor direct test ────────────────────

print()
print("=" * 72)
print("TEST 3: SINGLE-ROUND TRACE + STAMINA + ABSOLUTE-FLOOR CHECK")
print("=" * 72)

# ── 3a: Fresh single-round trace (confirm wrestler no longer scrambles himself) ──
random.seed(42)
tl = simulate_round(wrestler, weak_strk)

print(f"\nTrace round -- {wrestler.name} vs {weak_strk.name}:")
print("-" * 72)
suc_costs: list[float] = []
fai_costs: list[float] = []
for a in tl.attempts:
    tag = "SUC" if a.success else "FAI"
    print(
        f"  t{a.tick:>02}  [{tag}]  {a.attacker_name:<20}"
        f"  {a.transition.value:<20}"
        f"  {a.phase_from.value}->{a.phase_to.value}"
        f"  cost={a.stamina_cost:.0f}"
    )
    (suc_costs if a.success else fai_costs).append(a.stamina_cost)

print(f"\nPhase split (trace round):")
for ph, secs in tl.time_in_phase.items():
    print(f"  {ph:<10} {secs:.0f}s ({secs/ROUND_SECONDS:.0%})")
print(f"\nEnd stamina:  {wrestler.name} {tl.end_stamina_a:.1f}/100"
      f"  |  {weak_strk.name} {tl.end_stamina_b:.1f}/100")

# Visual check: confirm SCRAMBLE attempts only appear for the BOTTOM fighter.
# After any direct_takedown/clinch_to_ground (SUC), the DEFENDER is BOTTOM.
# All subsequent SCRAMBLE lines should show that defender's name as attacker.
scramble_attempts = [a for a in tl.attempts if a.transition == TransitionType.SCRAMBLE]
takedown_attempts = [a for a in tl.attempts
                     if a.transition in (TransitionType.DIRECT_TAKEDOWN,
                                         TransitionType.CLINCH_TO_GROUND)
                     and a.success]
if scramble_attempts:
    print(f"\nSCRAMBLE attempts in trace ({len(scramble_attempts)}):")
    for a in scramble_attempts:
        print(f"  t{a.tick:>02}  [{('SUC' if a.success else 'FAI')}]"
              f"  attacker={a.attacker_name}")
    if takedown_attempts:
        expected_bottom = takedown_attempts[-1].attacker_name  # last TD winner was TOP; loser was BOTTOM
        # The "expected BOTTOM" is actually the DEFENDER (loser) of the last takedown.
        # TransitionAttempt only stores attacker_name, not defender. We can infer:
        # if wrestler took down striker, striker is bottom; scramble attacker should be striker.
        print(f"  (Last takedown winner/TOP: {takedown_attempts[-1].attacker_name} -- "
              f"so BOTTOM/scrambler should be the other fighter)")
else:
    print("\n  (No SCRAMBLE attempts in this trace round.)")

# ── 3b: Stamina cost verification ─────────────────────────────────────────────
print(f"\nStamina cost constants: success={SUCCESS_STAMINA_COST:.0f}  "
      f"fail={FAIL_STAMINA_COST:.0f}  (ratio {FAIL_STAMINA_COST/SUCCESS_STAMINA_COST:.1f}x)")
if suc_costs or fai_costs:
    print(f"  Trace: {len(suc_costs)} successes @ {SUCCESS_STAMINA_COST:.0f}"
          f"  +  {len(fai_costs)} failures @ {FAIL_STAMINA_COST:.0f}")
cost_ok = (
    all(c == SUCCESS_STAMINA_COST for c in suc_costs) and
    all(c == FAIL_STAMINA_COST    for c in fai_costs)
)
p_3b = cost_ok
print(f"  [{'PASS' if cost_ok else 'FAIL'}]  All stamina costs match constants")

# ── 3c: Absolute-floor direct test (new — validates Issue A fix) ──────────────
# Compare attempt rates for a LOW-wrestling fighter vs a MODERATE-wrestling
# fighter, BOTH facing the SAME opponent. The Issue A fix should make the
# low-skill fighter attempt transitions (especially DIRECT_TAKEDOWN) visibly
# less often, even though the gap-based modifier is the same for both.
N_ABS = N
shared_opp = _f("SharedOpp", wrestling=5, clinch=3, athleticism=3)
low_wres   = _f("LowWrestler",  wrestling=-20, clinch=-5, athleticism=5)
mid_wres   = _f("MidWrestler",  wrestling=0,   clinch=0,  athleticism=5)

st_low = agg_stats(low_wres, shared_opp, N_ABS)
st_mid = agg_stats(mid_wres, shared_opp, N_ABS)

low_att = st_low[low_wres.name]["att"]
mid_att = st_mid[mid_wres.name]["att"]
att_diff = mid_att - low_att

print(f"\nAbsolute-floor direct test ({N_ABS} rounds each vs same opponent"
      f" SharedOpp wrestl={shared_opp.wrestling:+.0f}):")
print(f"  {low_wres.name:<20} (wrestl={low_wres.wrestling:+.0f})"
      f"  {low_att:.1f} att/rnd  sr={st_low[low_wres.name]['sr']:.0%}")
print(f"  {mid_wres.name:<20} (wrestl={mid_wres.wrestling:+.0f})"
      f"  {mid_att:.1f} att/rnd  sr={st_mid[mid_wres.name]['sr']:.0%}")
print(f"  Difference: {att_diff:+.1f} att/rnd  (mid > low, absolute floor effect)")
p_3c = mid_att > low_att
print(f"  [{'PASS' if p_3c else 'FAIL'}]  MidWrestler attempts more than LowWrestler"
      f":  {mid_att:.1f} > {low_att:.1f}")

# ── 3d: Aggregate attempt rates (large-gap vs small-gap, unchanged from last session) ──
N_AGG = N
st_1c = agg_stats(wrestler,  weak_strk, N_AGG)
st_2  = agg_stats(close_a, close_b,   N_AGG)

# NOTE: total attempt count is no longer a useful tendency metric after Issue B.
# The strong wrestler spends ~88% of the round as TOP in GROUND and initiates
# 0 SCRAMBLE attempts there (correct behaviour). Total per-round attempts are
# therefore low. Success rate per attempt is the right proxy: a skill-dominant
# fighter succeeds more often when they DO attempt, regardless of phase lockdown.
wr_sr  = st_1c[wrestler.name]["sr"]
ca_sr  = st_2[close_a.name]["sr"]
p_3d   = wr_sr > ca_sr

print(f"\nTendency check -- success rate: strong wrestler vs close-gap fighter ({N_AGG} rounds each):")
print(f"\n  Large-gap ({wrestler.name} wrestl={wrestler.wrestling:+.0f}"
      f"  vs  {weak_strk.name} wrestl={weak_strk.wrestling:+.0f}):")
for name, s in st_1c.items():
    print(f"    {name:<22}  {s['att']:.1f} att/rnd"
          f"  ({s['suc']:.1f} suc  {s['fail']:.1f} fail)"
          f"  sr={s['sr']:.0%}  stam/rnd={s['stam']:.1f}")
print(f"\n  Small-gap ({close_a.name} wrestl={close_a.wrestling:+.0f}"
      f"  vs  {close_b.name} wrestl={close_b.wrestling:+.0f}):")
for name, s in st_2.items():
    print(f"    {name:<22}  {s['att']:.1f} att/rnd"
          f"  ({s['suc']:.1f} suc  {s['fail']:.1f} fail)"
          f"  sr={s['sr']:.0%}  stam/rnd={s['stam']:.1f}")
print(f"\n  Success rates: {wrestler.name} sr={wr_sr:.0%}  vs  {close_a.name} sr={ca_sr:.0%}")
print(f"  (Wrestler's low total attempts reflect GROUND lockdown as TOP, not low tendency.)")
print(f"  [{'PASS' if p_3d else 'FAIL'}]  Strong wrestler success rate > close-gap fighter"
      f":  {wr_sr:.0%} > {ca_sr:.0%}")


# ─── SUMMARY ─────────────────────────────────────────────────────────────────

print()
print("=" * 72)
print("SUMMARY")
print("=" * 72)

checks = {
    "1a  STANDING >= 70%   (striker vs striker)":                   p_1a,
    "1b  CLINCH+GROUND >= 60%   (grappler vs grappler)":            p_1b,
    "1c  GROUND >= 60%   (strong wrestler vs weak striker)":         p_1c,
    "2   Large-gap GROUND exceeds small-gap by >= 10pp":             p_2,
    "3b  Stamina costs match constants":                             p_3b,
    "3c  Absolute floor: mid-wrestle attempts more than low-wrestle":p_3c,
    "3d  Tendency: strong wrestler success rate > close-gap fighter": p_3d,
}

all_pass = True
for label, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}]  {label}")
    if not ok:
        all_pass = False

import sys as _sys
if all_pass:
    print("\nAll checks pass.")
    _sys.exit(0)
else:
    print()
    print("FINDINGS  (stop here -- diagnose before patching the engine)")
    print("-" * 72)
    if not p_1a:
        print(f"  [1a] Striker-striker STANDING={sp_1a['STANDING']:.0%}. Still below 70%.")
        print(f"       ABS_SKILL_SCALE may need tuning (current=15.0).")
    if not p_1b:
        print(f"  [1b] Grappler-grappler CLINCH+GROUND={sp_1b['CLINCH']+sp_1b['GROUND']:.0%}. Below 60%.")
    if not p_1c:
        print(f"  [1c] Wrestler vs weak-striker GROUND={sp_1c['GROUND']:.0%}. Below 60%.")
        print(f"       Check if SCRAMBLE by BOTTOM is correctly restricted (trace above).")
    if not p_2:
        print(f"  [2]  Gradient delta={delta_ground:.0%}. Below 10pp.")
        print(f"       Likely cascades from 1c -- fix 1c first, recheck.")
    if not p_3c:
        print(f"  [3c] Absolute-floor suppression not working.")
        print(f"       Low ({low_wres.wrestling:+.0f}) attempts: {low_att:.1f}  Mid ({mid_wres.wrestling:+.0f}): {mid_att:.1f}")
    if not p_3d:
        print(f"  [3d] Strong wrestler success rate ({wr_sr:.0%}) not above close fighter ({ca_sr:.0%}).")
        print(f"       This would indicate the gap-based tendency modifier is not working.")
    _sys.exit(1)
