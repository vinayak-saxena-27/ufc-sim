"""
Smoke test — data layer + calibration check.

Purpose: confirm the overall/scale constants produce realistic win-rate
differentials across tiers before building matchmaking/fight-resolution on top.

Real-world anchor used for calibration:
  A fighter who goes 11-2 in regional/mid-major competition then 8-6 in the
  top org (including a win over the champ) is a legit top-15 elite fighter.
  That same fighter at constant true skill should show:
    ~80–85% win rate against a regional pool
    ~55–60% win rate against an elite-tier pool
  And the top-of-tier elite (Topuria/Jones/Pantoja-caliber) should be ~80%+
  against the bottom-of-tier elite, even though both are nominally "top-15."
"""
from __future__ import annotations

import random
from statistics import mean, stdev

from fighter import Fighter, ATTR_NAMES
from templates import TEMPLATES, generate_population
from fight import win_probability, SCALE, NOISE_STD

random.seed(42)

# ─── 1. Generate regional population ─────────────────────────────────────────
PER_TEMPLATE = 40
print(f"Generating {PER_TEMPLATE} fighters per template "
      f"({PER_TEMPLATE * len(TEMPLATES)} total)...\n")
regional_pool = generate_population(PER_TEMPLATE)


# ─── 2. Sample table — eyeball stat distinctiveness per template ──────────────
_FMT = (
    "{:<22} {:<24} {:>3} {:>5}"
    " {:>5} {:>5} {:>5}"   # wrestling bjj clinch
    " {:>5} {:>5} {:>5}"   # boxing kickboxing power
    " {:>5} {:>5} {:>5} {:>5}"  # cardio chin athleticism fight_iq
    " {:>5}"                # hype
)

HEADER = _FMT.format(
    "Template", "Name", "Age", "Ovr",
    "Wres", "BJJ", "Cli",
    "Box", "KB", "Pow",
    "Card", "Chin", "Ath", "IQ",
    "Hype",
)
SEP = "-" * len(HEADER)
print(SEP)
print("SAMPLE  (2 fighters per template)")
print(SEP)
print(HEADER)
print(SEP)

shown: dict[str, int] = {}
for f in regional_pool:
    n = shown.get(f.template, 0)
    if n >= 2:
        continue
    shown[f.template] = n + 1
    print(_FMT.format(
        f.template, f.name, f.age, f"{f.overall:+.1f}",
        f"{f.wrestling:+.1f}", f"{f.bjj:+.1f}", f"{f.clinch:+.1f}",
        f"{f.boxing:+.1f}", f"{f.kickboxing:+.1f}", f"{f.power:+.1f}",
        f"{f.cardio:+.1f}", f"{f.chin:+.1f}", f"{f.athleticism:+.1f}", f"{f.fight_iq:+.1f}",
        f"{f.hype:+.1f}",
    ))

# ─── 3. Per-template overall + key-attribute averages ────────────────────────
print()
print("Per-template overall (mean +/- std):")
for tmpl in TEMPLATES:
    pool = [f for f in regional_pool if f.template == tmpl]
    ovr = [f.overall for f in pool]
    print(f"  {tmpl:<24}  overall {mean(ovr):+.1f} +/- {stdev(ovr):.1f}")

print()
print("Per-template attribute means (shows template identity):")
SHOW_ATTRS = ["wrestling", "bjj", "clinch", "boxing", "kickboxing", "cardio"]
attr_header = f"  {'Template':<24}" + "".join(f"  {a[:5]:>6}" for a in SHOW_ATTRS)
print(attr_header)
for tmpl in TEMPLATES:
    pool = [f for f in regional_pool if f.template == tmpl]
    row = f"  {tmpl:<24}"
    for attr in SHOW_ATTRS:
        vals = [getattr(f, attr) for f in pool]
        row += f"  {mean(vals):>+6.1f}"
    print(row)


# ─── 4. Calibration fighters ──────────────────────────────────────────────────
# Manually constructed to hit specific overall targets — not sampled from templates.
# These represent archetypal fighters at known tier positions.

def make_calibration_fighter(name: str, target_overall: float, attr_noise: float = 4.0) -> Fighter:
    """
    Creates a fighter where each sub-attribute is sampled near target_overall.
    attr_noise controls within-fighter attribute spread while keeping overall
    close to the target (by the law of large numbers over 10 attributes).
    """
    attrs = {attr: target_overall + random.gauss(0.0, attr_noise) for attr in ATTR_NAMES}
    return Fighter(
        name=name,
        age=28,
        region="calibration",
        template="calibration",
        hype=target_overall + random.gauss(0.0, 5.0),
        **attrs,
    )


# Anchor: elite, bottom-of-top-15 — strong regional record, competitive at elite level
ANCHOR_OVERALL = 22.0
anchor = make_calibration_fighter("Anchor (bottom-top-15)", ANCHOR_OVERALL)

# TopTier: dominant elite — Topuria/Jones/Pantoja-caliber, capable of long undefeated runs
TOP_TIER_OVERALL = 36.0
top_tier = make_calibration_fighter("TopTier (elite-dominant)", TOP_TIER_OVERALL)

# Elite pool: wide internal spread is intentional.
# Bottom-of-tier vs top-of-tier within the same pool should be ~80%+ lopsided,
# while the pool average sits meaningfully above the regional pool.
ELITE_POOL_MEAN = 18.0
ELITE_POOL_STD  = 6.0
ELITE_POOL_SIZE = 60

elite_pool = [
    make_calibration_fighter(f"Elite_{i}", random.gauss(ELITE_POOL_MEAN, ELITE_POOL_STD))
    for i in range(ELITE_POOL_SIZE)
]
elite_overalls = [f.overall for f in elite_pool]

print()
print(f"Calibration fighters:")
print(f"  Anchor overall:   {anchor.overall:+.1f}  (target {ANCHOR_OVERALL:+.0f})")
print(f"  TopTier overall:  {top_tier.overall:+.1f}  (target {TOP_TIER_OVERALL:+.0f})")
print(f"  Elite pool ({ELITE_POOL_SIZE}):  mean={mean(elite_overalls):+.1f}, "
      f"std={stdev(elite_overalls):.1f}, "
      f"range [{min(elite_overalls):+.1f} to {max(elite_overalls):+.1f}]")


# ─── 5. Calibration simulation ────────────────────────────────────────────────

def sim_win_rate(fighter: Fighter, pool: list[Fighter], n: int) -> float:
    """
    Runs n simulated fights against randomly sampled opponents from pool.
    Uses win_probability directly (no fight_history side effects) for speed.
    The per-fight noise inside win_probability is what makes each fight unique.
    """
    wins = sum(
        1 for _ in range(n)
        if random.random() < win_probability(fighter, random.choice(pool))
    )
    return wins / n


N_FIGHTS = 400
print(f"\nRunning calibration ({N_FIGHTS} fights per matchup, "
      f"SCALE={SCALE}, NOISE_STD={NOISE_STD})...")

rate_anchor_regional = sim_win_rate(anchor,   regional_pool, N_FIGHTS)
rate_anchor_elite    = sim_win_rate(anchor,   elite_pool,    N_FIGHTS)
rate_toptier_elite   = sim_win_rate(top_tier, elite_pool,    N_FIGHTS)

# Internal elite-tier spread: best vs worst in the same pool
elite_sorted = sorted(elite_pool, key=lambda f: f.overall)
worst_elite, best_elite = elite_sorted[0], elite_sorted[-1]
rate_best_vs_worst = sim_win_rate(best_elite, [worst_elite], N_FIGHTS)

# Upset sanity check: a FIXED 15-point overall gap.
# Using crafted fighters (not pool extremes) so the gap is always exactly ~15,
# giving a stable theoretical expectation: P(underdog) = 1/(1+10^(15/25)) ~= 20%.
# The spec target is "roughly 1 in 4-5" — this should land in ~18-25% range.
upset_favorite = make_calibration_fighter("UpsetFavorite", 15.0, attr_noise=2.0)
upset_underdog = make_calibration_fighter("UpsetUnderdog",  0.0, attr_noise=2.0)
rate_upset = sim_win_rate(upset_underdog, [upset_favorite], N_FIGHTS)

print()
print("=" * 60)
print("CALIBRATION RESULTS")
print("=" * 60)
print(f"  Anchor  ({anchor.overall:+.1f}) vs regional pool:  {rate_anchor_regional:.1%}"
      f"   target ~80-85%")
print(f"  Anchor  ({anchor.overall:+.1f}) vs elite pool:     {rate_anchor_elite:.1%}"
      f"   target ~55-60%")
print(f"  TopTier ({top_tier.overall:+.1f}) vs elite pool:   {rate_toptier_elite:.1%}"
      f"   target 80%+")
print()
print(f"  Internal elite spread (best vs worst of sampled pool):")
print(f"    best-of-pool  ovr={best_elite.overall:+.1f}")
print(f"    worst-of-pool ovr={worst_elite.overall:+.1f}  (gap={best_elite.overall - worst_elite.overall:.1f})")
print(f"    best vs worst: {rate_best_vs_worst:.1%}"
      f"   target 80%+ (tier has meaningful internal spread)")
print()
print(f"  Upset sanity (crafted 15-pt gap: ovr {upset_underdog.overall:+.1f} vs {upset_favorite.overall:+.1f}):")
print(f"    underdog win rate: {rate_upset:.1%}"
      f"   target ~15-25% (theory ~20%, upsets real not flukes)")
print()


def _check(label: str, actual: float, lo: float, hi: float) -> None:
    status = "PASS" if lo <= actual <= hi else "FAIL"
    print(f"  [{status}]  {label:<38}  {actual:.1%}  (want {lo:.0%}-{hi:.0%})")


print("PASS / FAIL SUMMARY")
print("-" * 60)
_check("Anchor vs regional",       rate_anchor_regional, 0.75, 0.90)
_check("Anchor vs elite",          rate_anchor_elite,    0.50, 0.65)
_check("TopTier vs elite",         rate_toptier_elite,   0.75, 0.92)
_check("Best vs worst in elite",   rate_best_vs_worst,   0.72, 0.95)
_check("Upset rate (15-pt gap)",   rate_upset,           0.12, 0.30)
print()
print("If any checks FAIL, tune SCALE and/or NOISE_STD in fight.py, or")
print("adjust ANCHOR_OVERALL / ELITE_POOL_MEAN / ELITE_POOL_STD above.")
