"""
verify_5a.py -- Academy entities and naming system verification (Session 5a Part 2).

Five checks:
  1. Academy distribution -- roughly even spread across each region's academies.
  2. Name collision rate -- per-region registry vs old single global pool.
  3. Nudge visibility -- academy-specific attribute differences within Dagestan
     without washing out the regional template identity.
  4. Tier calibration regression -- overall distributions per tier unchanged.
  5. Readable sample -- 20 fighters, name/region/academy/key attributes eyeballed.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from collections import defaultdict
from statistics import mean, stdev

from tiers import generate_all_tiers, generate_tier_fighter, TIER_LEVELS, TIER_CONFIG
from academies import ACADEMIES, _NAMES, reset_name_registry

# --- Helpers ------------------------------------------------------------------

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    tag = "PASS" if condition else "FAIL"
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    if not condition:
        failures.append(label)


_SEP = "-" * 74
SEED = 42

# --- Shared roster (Checks 1, 2, 5) ------------------------------------------

random.seed(SEED)
pools = generate_all_tiers()
all_fighters = [
    f for wc_pools in pools.values() for tier_pool in wc_pools.values() for f in tier_pool
]

print()
print("=" * 74)
print("SESSION 5A -- Academy entities, naming, nudge effect verification")
print("=" * 74)
print(f"\n  Full default roster: {len(all_fighters)} fighters  (seed={SEED})\n")


# =============================================================================
print(_SEP)
print("CHECK 1 -- Academy distribution within each region")
print(_SEP)
print()

by_region_academy: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for f in all_fighters:
    by_region_academy[f.template][f.academy] += 1

all_dist_ok = True
for tmpl in sorted(by_region_academy):
    counts = by_region_academy[tmpl]
    total  = sum(counts.values())
    n_academies = len(ACADEMIES[tmpl])
    expected    = total / n_academies
    max_ratio   = max(v / expected for v in counts.values())
    ok = max_ratio <= 1.6   # 60% headroom; 3-way random should be much tighter
    if not ok:
        all_dist_ok = False
    print(f"  {tmpl}  (n={total}, expected {expected:.0f} each)")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 2)
        print(f"    {count:>3}  ({count/expected:.2f}x)  {name}")
    print()

check("Even distribution across all 5 regions", all_dist_ok, "max ratio <=1.6x")


# =============================================================================
print(f"\n{_SEP}")
print("CHECK 2 -- Name collision rate: old global pool vs new per-region system")
print(_SEP)
print()

# Reconstruct the old combined pool exactly as it was in templates.py before this session.
_OLD_FIRST = [
    "Ivan", "Dmitri", "Ruslan", "Magomed", "Umar", "Islam", "Akhmat", "Zurab",
    "Shamil", "Zalim", "Hajji", "Khabib",
    "Joao", "Carlos", "Anderson", "Rafael", "Felipe", "Lucas", "Mauricio", "Gabriel",
    "Fabricio", "Rodrigo",
    "Marcus", "Tyrone", "Derek", "Dustin", "Justin", "Tony", "Colby", "Gilbert",
    "Sean", "Cory", "Brandon", "Marlon",
    "Chaiyaphum", "Somrak", "Yodchai", "Lerdsila", "Buakaw", "Sangmanee",
    "Jorge", "Yair", "Diego", "Alejandro", "Eryk", "Stipe", "Jiri", "Alex",
    "Max", "Michael", "Robert", "Israel", "Jan", "Beneil", "Nate", "Nick",
]
_OLD_LAST = [
    "Petrov", "Makhachev", "Guseinov", "Khasbulaev", "Kovalev", "Ankalaev",
    "Ulanbekov", "Chimaev", "Nurmagomedov",
    "Silva", "Santos", "Barboza", "Lopes", "Oliveira", "Nogueira", "Aldo",
    "Johnson", "Williams", "Davis", "Allen", "Brown", "Carter", "Thompson",
    "Cannonier", "Poirier", "Holloway",
    "Rodriguez", "Romero", "Volkanovski", "Topuria", "Prochazka", "Pereira",
    "Adesanya", "Sterling", "Gaethje", "Yan", "Blachowicz",
]

n = len(all_fighters)
old_combos = len(_OLD_FIRST) * len(_OLD_LAST)

TRIALS = 30
total_old_dupes = 0
for _ in range(TRIALS):
    drawn = [f"{random.choice(_OLD_FIRST)} {random.choice(_OLD_LAST)}" for _ in range(n)]
    seen: set[str] = set()
    dupes = 0
    for name in drawn:
        if name in seen:
            dupes += 1
        else:
            seen.add(name)
    total_old_dupes += dupes
avg_old = total_old_dupes / TRIALS

new_names = [f.name for f in all_fighters]
new_dupes = len(new_names) - len(set(new_names))

# Per-region pool statistics
print("  Per-region pool sizes (new system):")
for tmpl, pool in _NAMES.items():
    nf, nl = len(pool["first"]), len(pool["last"])
    combos = nf * nl
    used   = len(by_region_academy.get(tmpl, {}))   # approximate: # academies, not fighters
    fighters_in_region = sum(by_region_academy[tmpl].values()) if tmpl in by_region_academy else 0
    print(f"    {tmpl:<22}  {nf} first x {nl} last = {combos:>5} combos  ({fighters_in_region} fighters used)")

print()
print(f"  Old pool:       {len(_OLD_FIRST)} first x {len(_OLD_LAST)} last = {old_combos:,} combos  (global, no dedup)")
print(f"  Old dupe rate:  {avg_old:.1f} avg duplicates per {n}-fighter roster  ({TRIALS} simulated trials)")
print(f"  New system:     {new_dupes} duplicates in actual {n}-fighter roster  (per-region registry)")
print()

check(
    "New system has 0 name duplicates",
    new_dupes == 0,
    f"{new_dupes} new vs ~{avg_old:.0f} old",
)


# =============================================================================
print(f"\n{_SEP}")
print("CHECK 3 -- Academy nudge visibility (Dagestan region, tier2 sample)")
print(_SEP)
print()

# Use a fresh name registry so this 300-fighter Dagestan sample doesn't collide
# with the 147 Dagestan names already registered from Check 1's roster.
reset_name_registry()

N_DAGESTAN = 300   # ~100 per academy after 3-way split
dagestan: dict[str, list] = defaultdict(list)
for _ in range(N_DAGESTAN):
    f = generate_tier_fighter("dagestan_sambo", "tier2")
    dagestan[f.academy].append(f)

print(f"  Generated {N_DAGESTAN} Dagestan fighters at tier2:")
for name, fs in sorted(dagestan.items()):
    print(f"    {len(fs):>3}  {name}")

# Attributes to compare -- chosen to highlight each academy's nudge
SHOW_ATTRS = ["wrestling", "clinch", "cardio", "chin", "fight_iq", "bjj", "boxing"]

acad_means: dict[str, dict[str, float]] = {
    acad: {attr: mean(getattr(f, attr) for f in fs) for attr in SHOW_ATTRS}
    for acad, fs in dagestan.items()
}

col = 9
print(f"\n  {'Academy':<36}" + "".join(f"{a[:col-1]:>{col}}" for a in SHOW_ATTRS))
print(f"  {'-'*35}" + "-" * (col * len(SHOW_ATTRS)))
for acad in sorted(acad_means):
    row = f"  {acad[:35]:<36}" + "".join(f"{acad_means[acad][a]:>+{col}.1f}" for a in SHOW_ATTRS)
    print(row)

# Sub-check A: template identity preserved
# Dagestan template: wrestling ~+22, boxing ~-15 -> gap ~37 at tier2 center ~0.
# Even with nudges only on 2 attrs, the regional shape should dominate.
print(f"\n  Template identity (wrestling-boxing gap > 20 in every academy):")
identity_ok = True
for acad, m in acad_means.items():
    gap = m["wrestling"] - m["boxing"]
    ok = gap > 20.0
    if not ok:
        identity_ok = False
    print(f"    gap={gap:>+.1f}  {'OK' if ok else 'FAIL'}  {acad}")
check("Dagestan template identity intact across all academies", identity_ok, "wres-box gap >20")

# Sub-check B: primary nudge attr ranks #1 among the 3 academies.
# Nudges (academies.py):
#   Makhachkala: wrestling +4 -> should rank #1 on wrestling
#   Anzhi:       cardio +4   -> should rank #1 on cardio
#   Eagle:       fight_iq +5 -> should rank #1 on fight_iq
PRIMARY = {
    "Makhachkala Combat Sambo Centre": "wrestling",
    "Anzhi MMA Factory":               "cardio",
    "Eagle Athletic Club":             "fight_iq",
}
print(f"\n  Nudge direction (primary nudge attr should rank #1 in its academy):")
nudge_ok = True
for acad, primary_attr in PRIMARY.items():
    if acad not in acad_means:
        continue
    ranked = sorted(acad_means.keys(), key=lambda a: acad_means[a][primary_attr], reverse=True)
    rank = ranked.index(acad) + 1
    ok   = rank == 1
    if not ok:
        nudge_ok = False
    print(f"    {primary_attr:<12}  rank #{rank}  {'OK' if ok else 'FAIL -- wrong order'}  ({acad})")
check("Primary nudge attribute ranks #1 per academy", nudge_ok)


# =============================================================================
print(f"\n{_SEP}")
print("CHECK 4 -- Tier calibration regression (overall distributions unchanged)")
print(_SEP)
print()

# Fresh seed + fresh roster so this is independent of earlier generation.
random.seed(SEED)
pools_cal = generate_all_tiers()

print(f"  {'Tier':<22}  {'Target':>7}  {'Mean':>8}  {'Std':>6}  {'Delta':>7}  Status")
print(f"  {'-'*21}  {'-'*7}  {'-'*8}  {'-'*6}  {'-'*7}  {'-'*12}")

all_tiers_ok = True
for tier_key in TIER_LEVELS:
    cfg    = TIER_CONFIG[tier_key]
    cohort = [f for wc_pools in pools_cal.values() for f in wc_pools[tier_key]]
    overalls = [f.overall for f in cohort]
    m = mean(overalls)
    s = stdev(overalls)
    delta = m - cfg.center
    # Academy nudges add ~+0.75 on average to overall (2 attrs nudged / 10).
    # Tolerance is +/-4: generous for noise, tight enough to catch a real regression.
    ok = abs(delta) <= 4.0
    if not ok:
        all_tiers_ok = False
    status = "OK" if ok else "*** OUT OF RANGE ***"
    print(f"  {cfg.label:<22}  {cfg.center:>+7.0f}  {m:>+8.1f}  {s:>6.1f}  {delta:>+7.1f}  {status}")

print()
check("All tier overall distributions within +/-4 of calibration targets", all_tiers_ok)


# =============================================================================
print(f"\n{_SEP}")
print("CHECK 5 -- Readable sample (20 fighters, eyeball check)")
print(_SEP)
print()

SHOW = 20
sample = sorted(random.sample(all_fighters, SHOW), key=lambda f: f.template)
SAMPLE_ATTRS = ["wrestling", "bjj", "kickboxing", "cardio"]

print(
    f"  {'Name':<26}  {'Region':<16}  {'Academy':<34}  {'Ovr':>5}"
    + "".join(f"  {a[:4]:>5}" for a in SAMPLE_ATTRS)
)
print(
    f"  {'-'*25}  {'-'*15}  {'-'*33}  {'-'*5}"
    + "".join(f"  {'-'*5}" for _ in SAMPLE_ATTRS)
)
for f in sample:
    print(
        f"  {f.name:<26}  {f.region:<16}  {f.academy[:33]:<34}  {f.overall:>+5.1f}"
        + "".join(f"  {getattr(f, a):>+5.1f}" for a in SAMPLE_ATTRS)
    )

print("\n  (eyeball: Dagestan fighters should show high wrestling/cardio and low kickboxing;")
print("   Thai fighters high kickboxing; Brazilians high bjj; no cross-region name leakage)")


# =============================================================================
print(f"\n{'=' * 74}")
print(f"RESULTS  --  {74 - len(failures)} checks passed, {len(failures)} failed")
print("=" * 74)
if failures:
    for label in failures:
        print(f"  [FAIL]  {label}")
else:
    print("  All checks passed.")
print()
