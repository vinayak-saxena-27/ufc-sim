"""
verify_ruleset.py -- Per-tier ruleset verification (Session 4d).

Four checks:
  1. Non-title round counts: all tiers use 3 rounds.
  2. Round length: Amateur (tier0) runs 36 ticks/round (3 min), all pro tiers 60 (5 min).
  3. Title fight round counts: Regional=3, Mid-major/Top-org/Elite=5; Amateur raises.
  4. Finish-rate comparison: same fighters at Amateur (3-min) vs pro (5-min) round length.
"""
from __future__ import annotations

import random
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tiers import TIER_LEVELS, TIER_RULESET, TIER_CONFIG, generate_all_tiers
from fight_engine import simulate_full_fight, TICKS_PER_ROUND, TICK_SECONDS
from test_fixtures import make_style_fighter

PASS = "PASS"
FAIL = "FAIL"
failures: list[str] = []


def check(label: str, condition: bool) -> None:
    tag = PASS if condition else FAIL
    print(f"  [{tag}] {label}")
    if not condition:
        failures.append(label)


# ─── Test 1: Non-title round counts ───────────────────────────────────────────
print("TEST 1: Non-title round counts (all tiers should run 3 rounds)")
print()

for tier_key in TIER_LEVELS:
    ruleset = TIER_RULESET[tier_key]
    label   = TIER_CONFIG[tier_key].label
    check(
        f"{tier_key} ({label}): non_title_rounds = {ruleset.non_title_rounds}",
        ruleset.non_title_rounds == 3,
    )
print()

# Simulate two non-title fights per tier and confirm 3 rounds max.
random.seed(101)
pools = generate_all_tiers(per_tier={t: 20 for t in TIER_LEVELS})

for tier_key in TIER_LEVELS:
    fighters = pools["lightweight"][tier_key]
    fa, fb   = fighters[0], fighters[1]
    outcome  = simulate_full_fight(fa, fb, is_title=False)
    n_rounds = len(outcome.rounds)
    check(
        f"{tier_key} simulated non-title: {n_rounds} round(s) played",
        n_rounds <= 3,
    )
print()

# ─── Test 2: Round length (ticks per round) ────────────────────────────────────
print("TEST 2: Round length (Amateur=36 ticks, pro tiers=60 ticks)")
print()

AMATEUR_TICKS = 180 // TICK_SECONDS   # 36
PRO_TICKS     = 300 // TICK_SECONDS   # 60

for tier_key in TIER_LEVELS:
    ruleset      = TIER_RULESET[tier_key]
    ticks        = ruleset.round_seconds // TICK_SECONDS
    label        = TIER_CONFIG[tier_key].label
    expected     = AMATEUR_TICKS if tier_key == "tier0" else PRO_TICKS
    check(
        f"{tier_key} ({label}): round_seconds={ruleset.round_seconds}  ticks={ticks}  expected={expected}",
        ticks == expected,
    )
print()

# Confirm Amateur timeline really has fewer ticks by checking time_in_phase sums.
random.seed(202)
am_fighters   = pools["lightweight"]["tier0"]
pro_fighters  = pools["lightweight"]["tier2"]
fa_am, fb_am  = am_fighters[0], am_fighters[1]
fa_pro, fb_pro = pro_fighters[0], pro_fighters[1]

outcome_am  = simulate_full_fight(fa_am, fb_am, is_title=False)
outcome_pro = simulate_full_fight(fa_pro, fb_pro, is_title=False)

# Total phase time (seconds) in round 1 reflects round_seconds
am_r1_time  = sum(outcome_am.rounds[0].time_in_phase.values())
pro_r1_time = sum(outcome_pro.rounds[0].time_in_phase.values())

check(
    f"Amateur R1 total time = {am_r1_time:.0f}s (expected 180s)",
    am_r1_time == 180.0,
)
check(
    f"Pro R1 total time = {pro_r1_time:.0f}s (expected 300s)",
    pro_r1_time == 300.0,
)
print()

# ─── Test 3: Title fight round counts ─────────────────────────────────────────
print("TEST 3: Title fight rounds (Regional=3, Mid-major+Top-org+Elite=5, Amateur=ValueError)")
print()

# Amateur: must raise ValueError
try:
    am_pool = pools["lightweight"]["tier0"]
    simulate_full_fight(am_pool[0], am_pool[1], is_title=True)
    check("Amateur title fight raises ValueError", False)
except ValueError as e:
    check(f"Amateur title fight raises ValueError: {e}", True)

# Regional: title = 3 rounds, must complete <= 3 rounds
reg_ruleset = TIER_RULESET["tier1"]
check(
    f"Regional title_rounds = {reg_ruleset.title_rounds} (expected 3)",
    reg_ruleset.title_rounds == 3,
)
random.seed(303)
reg_fighters = pools["lightweight"]["tier1"]
reg_outcome  = simulate_full_fight(reg_fighters[0], reg_fighters[1], is_title=True)
check(
    f"Regional simulated title fight: {len(reg_outcome.rounds)} round(s) played (<= 3)",
    len(reg_outcome.rounds) <= 3,
)

# Mid-major / Top-org / Elite: title = 5 rounds
for tier_key in ["tier2", "tier3", "tier4"]:
    rs    = TIER_RULESET[tier_key]
    label = TIER_CONFIG[tier_key].label
    check(
        f"{tier_key} ({label}): title_rounds = {rs.title_rounds} (expected 5)",
        rs.title_rounds == 5,
    )

# Simulate a title fight at tier2 and confirm it can go past round 3.
# Use evenly matched fighters to maximise chance of going the distance.
random.seed(404)
fa_title = make_style_fighter("TitleA", target=0.0, style="flat")
fa_title.tier = "tier2"
fb_title = make_style_fighter("TitleB", target=0.0, style="flat")
fb_title.tier = "tier2"
title_outcomes = [simulate_full_fight(fa_title, fb_title, is_title=True) for _ in range(20)]
max_rounds = max(len(o.rounds) for o in title_outcomes)
check(
    f"tier2 title fight (20 runs): max rounds played = {max_rounds} (expected >3)",
    max_rounds > 3,
)
print()

# ─── Test 4: Finish-rate comparison Amateur vs Pro ─────────────────────────────
print("TEST 4: Finish-rate comparison — Amateur (3-min) vs Pro (5-min) at same skill")
print()
print("  Hypothesis: shorter rounds -> less accumulated pressure per round -> lower")
print("  finish rate. Same fighter pairs; only the tier ruleset (tick count) differs.")
print("  Uses pro-tier fighters (tier2-tier4) whose overalls produce actual finishes.")
print()

# Build a pool of fighters that actually produce finishes (tier2/3/4 overalls).
N = 400
random.seed(555)
pro_pool_data = generate_all_tiers(per_tier={t: 60 for t in TIER_LEVELS})
finish_pool   = (pro_pool_data["lightweight"]["tier2"] +
                 pro_pool_data["lightweight"]["tier3"] +
                 pro_pool_data["lightweight"]["tier4"])

# Fix N pairs so both conditions see exactly the same matchups.
pairs = [(random.choice(finish_pool), random.choice(finish_pool)) for _ in range(N)]

def _run_paired_batch(tier_key: str) -> dict[str, float]:
    counts: dict[str, int] = {"KO/TKO": 0, "submission": 0, "decision": 0}
    random.seed(7777)   # same internal fight randomness for both legs
    for fa, fb in pairs:
        outcome = simulate_full_fight(fa, fb, tier=tier_key)
        counts[outcome.method] += 1
    return {k: v / N for k, v in counts.items()}

am_rates  = _run_paired_batch("tier0")   # 36-tick / 3-min rounds
pro_rates = _run_paired_batch("tier2")   # 60-tick / 5-min rounds

print(f"  Amateur (tier0 / 3-min / 36-tick):  "
      f"KO={am_rates['KO/TKO']:.1%}  sub={am_rates['submission']:.1%}"
      f"  dec={am_rates['decision']:.1%}")
print(f"  Pro     (tier2 / 5-min / 60-tick):  "
      f"KO={pro_rates['KO/TKO']:.1%}  sub={pro_rates['submission']:.1%}"
      f"  dec={pro_rates['decision']:.1%}")
print()

am_finish  = am_rates["KO/TKO"] + am_rates["submission"]
pro_finish = pro_rates["KO/TKO"] + pro_rates["submission"]

check(
    f"Amateur total finish rate ({am_finish:.1%}) < Pro finish rate ({pro_finish:.1%})",
    am_finish < pro_finish,
)
print()

# ─── Summary ─────────────────────────────────────────────────────────────────
print("=" * 60)
if failures:
    print(f"FAILED ({len(failures)} check(s)):")
    for f in failures:
        print(f"  - {f}")
    print("=" * 60)
    _sys.exit(1)
else:
    print("ALL CHECKS PASSED")
    print("=" * 60)
