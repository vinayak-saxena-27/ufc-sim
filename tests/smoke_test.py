"""
Smoke test — tier structure + calibration check (Session 2).

Real-world anchor:
  A fighter went 11-2 in regional/mid-major, then 8-6 at the bottom of a top-15
  org (including a win over the reigning champion). Using that career as ground truth:

  Anchor  (~+29 overall, "bottom-of-top-15"):
    - ~82-85% win rate vs Tier 2 (mid-major) pool      <- matches 11-2 pre-org
    - ~57-63% win rate vs Tier 3 (top-org btm-15) pool <- matches 8-6 in-org

  TopTier (~+47 overall, "elite/champion"):
    - ~75-80% win rate vs the SAME Tier 3 pool         <- capable of long win streaks
      (meaningfully higher than Anchor's ~60%, confirming real internal spread)

SCALE=43 and the tier table (centers: -35/-15/0/+20/+45) were derived together
against this anchor. (Spec originally stated scale=20, but that is too steep for
20-pt tier gaps — produces ~97% instead of 82-85% for Anchor vs Tier 2. Back-
solving simultaneously from both targets gives scale ~= 43.) If any check fails:
try adjusting Anchor/TopTier overalls first, then SCALE in fight.py, then tier
centers in tiers.py — always re-derive against this anchor, not by feel.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from statistics import mean, stdev

from career.fighter import Fighter, ATTR_NAMES
from engine.fight import win_probability, SCALE, NOISE_STD
from career.tiers import TIER_CONFIG, TIER_LEVELS, WEIGHT_CLASSES, generate_all_tiers
from matchmaking import (
    pick_opponent, apply_tier_transitions,
    PROMOTE_WINDOW, PROMOTE_WINS_IN_LAST, DEMOTE_WINDOW, DEMOTE_LOSSES_IN_LAST,
)

random.seed(42)

# Calibration uses a flat 40 per tier so each tier has a stable enough sample for
# win-rate statistics. This overrides the pyramid default — it's deliberate.
# With 3 weight classes the total is 40 * 5 * 3 = 600 fighters, but calibration
# statistics only use one division's pools (skill distribution is identical across
# weight classes — they're the same draws from the same tier distributions).
CALIB_PER_TIER = 40
_calib_counts = {t: CALIB_PER_TIER for t in TIER_LEVELS}
_CALIB_WC = WEIGHT_CLASSES[0]   # "lightweight" — representative, all divisions identical

# ─── 1. Generate populations ──────────────────────────────────────────────────
n_total = CALIB_PER_TIER * len(TIER_LEVELS) * len(WEIGHT_CLASSES)
print(f"Generating populations: {CALIB_PER_TIER} per tier x {len(TIER_LEVELS)} tiers "
      f"x {len(WEIGHT_CLASSES)} divisions = {n_total} total (flat per-tier for calibration)...\n")
pools = generate_all_tiers(per_tier=_calib_counts)

# ─── 2. Per-tier overall summary ──────────────────────────────────────────────
# Uses one division as representative; all divisions draw from the same distributions.
print(f"Tier populations (overall mean +/- std, {_CALIB_WC} division shown):")
for tier_key in TIER_LEVELS:
    cfg   = TIER_CONFIG[tier_key]
    ovrs  = [f.overall for f in pools[_CALIB_WC][tier_key]]
    print(f"  {cfg.label:<20} center={cfg.center:>+5.0f}  "
          f"generated: {mean(ovrs):>+5.1f} +/- {stdev(ovrs):.1f}  "
          f"range [{min(ovrs):>+5.1f}, {max(ovrs):>+5.1f}]")

# ─── 3. Cross-tier template sample (shape preserved across tiers) ─────────────
# Shows that Dagestan/Muay Thai stylistic identity persists at every power level.
print()
print("-" * 95)
print("CROSS-TIER TEMPLATE SAMPLE  (1 fighter per tier x template, Dagestan vs Muay Thai)")
print("-" * 95)
_H = "{:<20} {:<16} {:<22} {:>5} {:>6} {:>6} {:>6} {:>6} {:>6}"
print(_H.format("Tier", "Template", "Name", "Ovr", "Wres", "Box", "KB", "Card", "BJJ"))
print("-" * 95)
for tier_key in TIER_LEVELS:
    label = TIER_CONFIG[tier_key].label
    for tmpl in ("dagestan_sambo", "muay_thai"):
        f = next(x for x in pools[_CALIB_WC][tier_key] if x.template == tmpl)
        print(_H.format(
            label, tmpl.replace("_", " "), f.name,
            f"{f.overall:>+.1f}",
            f"{f.wrestling:>+.1f}", f"{f.boxing:>+.1f}", f"{f.kickboxing:>+.1f}",
            f"{f.cardio:>+.1f}", f"{f.bjj:>+.1f}",
        ))

# ─── 4. Calibration fighters ──────────────────────────────────────────────────
# Hand-set overalls for clean, reproducible calibration — not sampled from a template.
# attr_noise gives each fighter's attribute profile some texture while keeping
# overall close to the target (noise contribution = sqrt(4^2/10) ~= 1.3 overall points).

ANCHOR_OVERALL   = 29.0   # "bottom-of-top-15"
TOPTIER_OVERALL  = 47.0   # "elite/champion"
CALIB_ATTR_NOISE = 4.0

def _make_calib_fighter(name: str, target: float) -> Fighter:
    attrs = {a: target + random.gauss(0.0, CALIB_ATTR_NOISE) for a in ATTR_NAMES}
    return Fighter(
        name=name, age=28,
        region="calibration", template="calibration", tier="calibration",
        hype=target + random.gauss(0.0, 5.0),
        **attrs,
    )

anchor   = _make_calib_fighter("Anchor (btm-top-15)", ANCHOR_OVERALL)
top_tier = _make_calib_fighter("TopTier (elite)",     TOPTIER_OVERALL)

print()
print(f"Calibration fighters:")
print(f"  Anchor  target={ANCHOR_OVERALL:>+.0f}  actual overall={anchor.overall:>+.1f}")
print(f"  TopTier target={TOPTIER_OVERALL:>+.0f}  actual overall={top_tier.overall:>+.1f}")

tier2_pool = pools[_CALIB_WC]["tier2"]
tier3_pool = pools[_CALIB_WC]["tier3"]
print(f"  Tier 2 pool: {len(tier2_pool)} fighters, "
      f"overall {mean(f.overall for f in tier2_pool):>+.1f} "
      f"+/- {stdev(f.overall for f in tier2_pool):.1f}")
print(f"  Tier 3 pool: {len(tier3_pool)} fighters, "
      f"overall {mean(f.overall for f in tier3_pool):>+.1f} "
      f"+/- {stdev(f.overall for f in tier3_pool):.1f}")

# ─── 5. Calibration simulation ────────────────────────────────────────────────
# Uses win_probability directly — no fight_history side effects, clean repeated sampling.

def sim_win_rate(fighter: Fighter, pool: list[Fighter], n: int) -> float:
    return sum(
        1 for _ in range(n)
        if random.random() < win_probability(fighter, random.choice(pool))
    ) / n


N_FIGHTS = 300
print(f"\nRunning calibration ({N_FIGHTS} fights per matchup, SCALE={SCALE}, NOISE_STD={NOISE_STD})...")

rate_anchor_t2   = sim_win_rate(anchor,   tier2_pool, N_FIGHTS)
rate_anchor_t3   = sim_win_rate(anchor,   tier3_pool, N_FIGHTS)
rate_toptier_t3  = sim_win_rate(top_tier, tier3_pool, N_FIGHTS)

# Internal Tier 3 spread: best vs worst of the pool
t3_sorted   = sorted(tier3_pool, key=lambda f: f.overall)
t3_worst, t3_best = t3_sorted[0], t3_sorted[-1]
rate_t3_internal = sim_win_rate(t3_best, [t3_worst], N_FIGHTS)

# Upset sanity: crafted 25-point gap -> theory P(underdog) ~= 1/(1+10^(25/43)) ~= 21%
upset_fav = _make_calib_fighter("UpsetFav",  25.0)
upset_dog = _make_calib_fighter("UpsetDog",   0.0)
rate_upset = sim_win_rate(upset_dog, [upset_fav], N_FIGHTS)

print()
print("=" * 62)
print("CALIBRATION RESULTS")
print("=" * 62)
print(f"  Anchor  ({anchor.overall:>+.1f}) vs Tier 2 pool:  "
      f"{rate_anchor_t2:.1%}   target ~82-85%")
print(f"  Anchor  ({anchor.overall:>+.1f}) vs Tier 3 pool:  "
      f"{rate_anchor_t3:.1%}   target ~57-63%")
print(f"  TopTier ({top_tier.overall:>+.1f}) vs Tier 3 pool: "
      f"{rate_toptier_t3:.1%}   target ~75-80%")
print()
print(f"  Tier 3 internal spread:")
print(f"    best  ovr={t3_best.overall:>+.1f}  worst ovr={t3_worst.overall:>+.1f}"
      f"  gap={t3_best.overall - t3_worst.overall:.1f}")
print(f"    best vs worst: {rate_t3_internal:.1%}   target 80%+")
print()
print(f"  Upset sanity (25-pt gap, theory ~21%):")
print(f"    underdog rate: {rate_upset:.1%}   want 14-28%")
print()


_smoke_failures: list[str] = []

def _check(label: str, actual: float, lo: float, hi: float) -> bool:
    ok     = lo <= actual <= hi
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}]  {label:<40}  {actual:.1%}  (want {lo:.0%}-{hi:.0%})")
    if not ok:
        _smoke_failures.append(label)
    return ok


print("PASS / FAIL SUMMARY")
print("-" * 62)
_check("Anchor vs Tier 2 (mid-major)",   rate_anchor_t2,    0.76, 0.91)
_check("Anchor vs Tier 3 (top-org)",     rate_anchor_t3,    0.50, 0.68)
_check("TopTier vs Tier 3",              rate_toptier_t3,   0.70, 0.86)
_check("Tier 3 internal (best vs worst)",rate_t3_internal,  0.72, 0.98)
_check("Upset rate (25-pt gap)",         rate_upset,        0.14, 0.28)
print()
print("Note: SCALE changed from 20 to 43. See fight.py comment for derivation.")
print("If Anchor-vs-T2 fails: raise ANCHOR_OVERALL +1-2 pts.")
print("If TopTier-vs-T3 fails high: lower TOPTIER_OVERALL or raise SCALE slightly.")

# ─── 6. General sim with tier-constrained matchmaking ─────────────────────────
# Runs fights across the full multi-tier population using pick_opponent +
# apply_tier_transitions. Confirms low-ovr fighters cluster in lower tiers,
# promotions/demotions occur, and record quality correlates with tier + overall.

print()
print("=" * 62)
print("GENERAL SIM (500 fights, tier-constrained matchmaking)")
print("=" * 62)

all_fighters = [
    f
    for wc_pools in pools.values()
    for tier_pool in wc_pools.values()
    for f in tier_pool
]
promotions: list[tuple[str, str, str]] = []  # (name, old_tier, new_tier)
demotions:  list[tuple[str, str, str]] = []

from engine.fight import simulate_fight

random.seed(42)
for _ in range(500):
    a = random.choice(all_fighters)
    b = pick_opponent(a, pools)
    if b is None:
        continue
    simulate_fight(a, b, org="general_sim")

    for fighter in (a, b):
        old_tier = fighter.tier
        new_tier = apply_tier_transitions(fighter, pools)
        if new_tier and new_tier != old_tier:
            level_change = TIER_LEVELS.index(new_tier) - TIER_LEVELS.index(old_tier)
            if level_change > 0:
                promotions.append((fighter.name, old_tier, new_tier))
            else:
                demotions.append((fighter.name, old_tier, new_tier))

print(f"Promotions: {len(promotions)}   Demotions: {len(demotions)}")
if promotions:
    print("  Promoted:", ", ".join(f"{n} ({o}->{t})" for n, o, t in promotions[:5]),
          "..." if len(promotions) > 5 else "")
if demotions:
    print("  Demoted: ", ", ".join(f"{n} ({o}->{t})" for n, o, t in demotions[:5]),
          "..." if len(demotions) > 5 else "")

# Standings: fighters with at least 1 fight, sorted by tier then win rate
from career.tiers import TIER_CONFIG as _TC
active = [f for f in all_fighters if f.wins + f.losses > 0]
active.sort(
    key=lambda f: (
        TIER_LEVELS.index(f.tier),
        f.wins / (f.wins + f.losses) if f.wins + f.losses > 0 else 0,
        f.overall,
    ),
    reverse=True,
)

_WCS = {"lightweight": "LW", "welterweight": "WW", "heavyweight": "HW"}
_SF = "{:<4} {:<24} {:<4} {:<16} {:<14} {:>6} {:>6} {:>6}"
print()
print(_SF.format("Rank", "Fighter", "Div", "Tier", "Style", "Rec", "Ovr", "Hype"))
print("-" * 88)
for rank, f in enumerate(active[:40], 1):
    tier_label = _TC[f.tier].label
    print(_SF.format(
        rank, f.name,
        _WCS.get(f.weight_class, "??"),
        tier_label,
        f.template.replace("_", " ")[:14],
        f.record_str,
        f"{f.overall:>+.1f}",
        f"{f.hype:>+.1f}",
    ))
if len(active) > 40:
    print(f"  ... ({len(active) - 40} more fighters competed)")

import sys as _sys
_sys.exit(1 if _smoke_failures else 0)
