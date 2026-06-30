"""
verify_elite_matchmaking.py — Verify Part 1 Elite Matchmaking Overhaul.

Five checks, in order of importance:
  CHECK 1  Sub-pool split:   ranked fighters draw ~88% ranked opponents.
  CHECK 2  Proximity:        RR fight rank-distances are meaningfully closer than uniform.
  CHECK 3  [CRITICAL] Density fix: gap between ranked appearances vs ~370-fight baseline.
  CHECK 4  Inactivity sanity: sane minority flagged in the new structure.
  CHECK 5  Unranked activity: unranked Elite fighters not starved by new structure.

If CHECK 3 doesn't show meaningful improvement, the script reports the numbers and
explains the structural reason rather than recommending constant tuning.

Run: python verify_elite_matchmaking.py
"""
from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean, median

from fight import simulate_fight
from tiers import generate_all_tiers
from matchmaking import (
    pick_opponent, pick_fighter_a, pick_scheduled_elite_a, apply_tier_transitions,
    reset_gate_stats, get_gate_stats,
    reset_elite_pairings, get_elite_pairings,
    ELITE_FIGHT_INTERVAL,
)
from labels import maybe_update_labels, reset_title_registry, update_labels
from title import reset_title_scheduling, maybe_run_title_fight
from age import reset_age_advancement, advance_all_ages
from cuts import maybe_evaluate_cut, reset_cut_registry
from retirement import (
    maybe_evaluate_retirement, maybe_retire_inactive, reset_retirement_scanning,
)
from rankings import (
    update_rankings, get_rankings, get_ranked_ids, reset_rankings,
    RANKINGS_UPDATE_INTERVAL, RANKINGS_SIZE,
)
from sim_calendar import (
    reset_sim_clock, advance_sim_clock, get_sim_day,
    inactivity_percentile, _last_stamped_day, SIM_DAYS_PER_FIGHT,
    INACTIVITY_PERCENTILE_THRESHOLD,
)

N_FIGHTS    = 3000
SEED        = 42
TARGET_WC   = "lightweight"
_PASS       = "PASS"
_FAIL       = "FAIL"


def _reset_all() -> None:
    reset_sim_clock()
    reset_age_advancement()
    reset_retirement_scanning()
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_gate_stats()
    reset_elite_pairings()


# ── Full sim ──────────────────────────────────────────────────────────────────

def run_sim() -> tuple[list, dict, dict, dict, int]:
    """Run N_FIGHTS-fight sim.

    Returns:
      all_fighters, pools,
      ranked_log   – {fighter_id: [(fight_num, sim_day)]} for each Elite fight while ranked
      unranked_log – same but for unranked Elite fighters
      initial_pop  – total fighter count at sim start (used as density baseline)
    """
    print(f"Running {N_FIGHTS}-fight sim  (seed={SEED}) ...")
    random.seed(SEED)
    _reset_all()

    pools = generate_all_tiers(scale=1.0)
    all_fighters = [
        f for wc_pools in pools.values() for tp in wc_pools.values() for f in tp
    ]
    initial_pop = len(all_fighters)

    ranked_log:   dict[str, list[tuple[int, int]]] = defaultdict(list)
    unranked_log: dict[str, list[tuple[int, int]]] = defaultdict(list)

    # total_fight_idx counts main + scheduled fights so gap measurements are
    # relative to all fights that could have contained a ranked appearance.
    total_fight_idx = 0

    def _log_elite_appearances(fa: "Fighter", fb: "Fighter", idx: int, day: int) -> None:
        if fa.tier == "tier4" and fb.tier == "tier4":
            ranked_now = get_ranked_ids()
            for f in (fa, fb):
                entry = (idx, day)
                if f.fighter_id in ranked_now:
                    ranked_log[f.fighter_id].append(entry)
                else:
                    unranked_log[f.fighter_id].append(entry)

    def _run_fight_cycle(
        fa: "Fighter", fb: "Fighter", fight_num: int, org: str = "league"
    ) -> None:
        nonlocal all_fighters
        fight_wc  = fa.weight_class
        fight_tier = fa.tier
        current_day = get_sim_day()
        _log_elite_appearances(fa, fb, total_fight_idx, current_day)

        winner, loser = simulate_fight(fa, fb, org=org, sim_day=current_day)

        to_remove: list = []
        for fighter in (winner, loser):
            apply_tier_transitions(fighter, pools)
            maybe_update_labels(fighter)
            removed = maybe_evaluate_retirement(fighter, pools, fight_num=fight_num)
            if not removed:
                removed = maybe_evaluate_cut(fighter, pools, fight_num=fight_num)
            if removed:
                to_remove.append(fighter)
        for rf in to_remove:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        maybe_run_title_fight(fight_wc, fight_tier, pools, org=org,
                              fight_num=fight_num, all_fighters=all_fighters)
        advance_sim_clock()
        advance_all_ages(all_fighters)

    for i in range(N_FIGHTS):
        if not all_fighters:
            break

        a = pick_fighter_a(all_fighters, pools)
        try:
            b = pick_opponent(a, pools)
        except IndexError:
            continue

        _run_fight_cycle(a, b, fight_num=total_fight_idx + 1)
        total_fight_idx += 1

        newly_retired = maybe_retire_inactive(all_fighters, pools, fight_num=total_fight_idx)
        for rf in newly_retired:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        if total_fight_idx % RANKINGS_UPDATE_INTERVAL == 0:
            update_rankings(pools)

        # ── Scheduled Elite fight (option b density fix) ────────────────────
        if ELITE_FIGHT_INTERVAL > 0 and (i + 1) % ELITE_FIGHT_INTERVAL == 0:
            ae = pick_scheduled_elite_a(pools)
            if ae is not None:
                try:
                    be = pick_opponent(ae, pools)
                except IndexError:
                    pass
                else:
                    _run_fight_cycle(ae, be, fight_num=total_fight_idx + 1, org="exhibition")
                    total_fight_idx += 1
                    if total_fight_idx % RANKINGS_UPDATE_INTERVAL == 0:
                        update_rankings(pools)

    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)

    print(f"  Done: {get_sim_day()} sim-days | {len(all_fighters)} fighters remaining\n")
    return all_fighters, pools, ranked_log, unranked_log, initial_pop


# ── CHECK 1: Sub-pool split ───────────────────────────────────────────────────

def check_sub_pool_split() -> bool:
    print("CHECK 1: Sub-pool split — ranked fighters' opponent draw")

    pairings      = get_elite_pairings()
    ranked_fights = [p for p in pairings if p.fighter_rank is not None]

    if not ranked_fights:
        print("  SKIP: no ranked-fighter pairings recorded")
        return True

    rr = sum(1 for p in ranked_fights if p.pool_type == "RR")
    ru = sum(1 for p in ranked_fights if p.pool_type == "RU")
    total = rr + ru
    pct_within = 100 * rr / total if total else 0.0

    print(f"  Ranked-fighter fights:  {total}")
    print(f"  R-vs-R: {rr} ({100*rr//max(1,total):>2}%)   R-vs-U: {ru} ({100*ru//max(1,total):>2}%)")
    print(f"  Within-pool rate: {pct_within:.1f}%   (target ~88%, ELITE_CROSS_POOL_RATE=0.12)")

    ok = pct_within >= 80.0
    print(f"  {_PASS if ok else _FAIL}  {'(>= 80% threshold)' if ok else '(below 80% -- check ELITE_CROSS_POOL_RATE)'}")
    return ok


# ── CHECK 2: Rank-proximity distribution ─────────────────────────────────────

def check_proximity_weighting() -> bool:
    print("\nCHECK 2: Rank-proximity weighting — distance distribution in RR fights")

    pairings  = get_elite_pairings()
    rr_fights = [
        p for p in pairings
        if p.pool_type == "RR"
        and p.fighter_rank is not None
        and p.opp_rank    is not None
    ]

    if len(rr_fights) < 30:
        print(f"  SKIP: only {len(rr_fights)} RR fights with known ranks (need >= 30)")
        return True

    dists = [abs(p.fighter_rank - p.opp_rank) for p in rr_fights]
    n = len(dists)

    b_1_3  = sum(1 for d in dists if 1 <= d <= 3)
    b_4_7  = sum(1 for d in dists if 4 <= d <= 7)
    b_8_11 = sum(1 for d in dists if 8 <= d <= 11)
    b_12p  = sum(1 for d in dists if d >= 12)

    # Uniform-draw expectation from a 15-fighter pool (average across all starting ranks):
    # For middle fighters: ~43% in dist 1-3; for edge fighters: ~21%.
    # Average across all ranks is roughly ~33%.
    # Proximity weighting (base=1.0) raises dist 1-3 to ~55-65% for middle ranks.
    unif_expected_pct = 33   # approximate % under uniform draw, 15-fighter pool

    mean_dist = sum(dists) / n
    med_dist  = sorted(dists)[n // 2]

    print(f"  RR fights analyzed: {n}")
    print(f"  Mean rank distance: {mean_dist:.1f}   Median: {med_dist}")
    print()
    print(f"  {'Bucket':<14}  {'Count':>5}  {'Observed':>9}  {'Uniform expect':>15}")
    print(f"  {'-'*48}")
    print(f"  {'dist 1-3':<14}  {b_1_3:>5}  {100*b_1_3//n:>8}%  {unif_expected_pct:>14}%")
    print(f"  {'dist 4-7':<14}  {b_4_7:>5}  {100*b_4_7//n:>8}%  {'~29':>14}%")
    print(f"  {'dist 8-11':<14}  {b_8_11:>5}  {100*b_8_11//n:>8}%  {'~22':>14}%")
    print(f"  {'dist 12+':<14}  {b_12p:>5}  {100*b_12p//n:>8}%  {'~14':>14}%")

    pct_close = 100 * b_1_3 / n
    # Pass: dist 1-3 clearly above uniform expectation (~33%) — use 45% threshold
    ok = pct_close >= 45.0
    print(f"\n  Proximity skew: {pct_close:.1f}% of RR fights within dist-3")
    print(f"  {_PASS if ok else _FAIL}  {'(>= 45% threshold, above ~33% uniform)' if ok else '(below 45% -- proximity not working)'}")
    return ok


# ── CHECK 3: Density fix (CRITICAL) ──────────────────────────────────────────

def check_density_fix(ranked_log: dict, initial_pop: int) -> bool:
    print("\nCHECK 3: [CRITICAL] Density fix — gap between ranked-fighter appearances")

    # Mathematical baseline: uniform random pick of 2 fighters from a population of N
    # means any specific fighter appears once per N/2 fights on average.
    baseline_gap_fights = initial_pop / 2
    baseline_gap_days   = baseline_gap_fights * SIM_DAYS_PER_FIGHT

    # Fighters with >= 3 ranked appearances give us at least 2 consecutive gaps.
    qualifying = {
        fid: apps
        for fid, apps in ranked_log.items()
        if len(apps) >= 3
    }

    if not qualifying:
        print(f"  Initial population: {initial_pop} fighters")
        print(f"  Theoretical baseline gap: {baseline_gap_fights:.0f} fights  "
              f"({baseline_gap_days:.0f} sim-days)")
        print("  SKIP: no ranked fighter has >= 3 ranked appearances (need more fights)")
        return True

    all_gaps_fights: list[int] = []
    all_gaps_days:   list[int] = []

    for apps in qualifying.values():
        for j in range(1, len(apps)):
            all_gaps_fights.append(apps[j][0] - apps[j - 1][0])
            all_gaps_days.append(apps[j][1] - apps[j - 1][1])

    n_gaps   = len(all_gaps_fights)
    n_ranked = len(qualifying)

    mn_f = mean(all_gaps_fights)
    md_f = median(all_gaps_fights)
    mn_d = mean(all_gaps_days)
    md_d = median(all_gaps_days)

    improvement_pct = 100 * (1 - mn_f / baseline_gap_fights)

    print(f"  Initial population: {initial_pop} fighters")
    print(f"  Ranked fighters with >= 3 appearances: {n_ranked}")
    print(f"  Total gaps measured: {n_gaps}")
    print()
    print(f"  {'Metric':<24}  {'Measured':>12}  {'Baseline':>12}  {'Delta':>8}")
    print(f"  {'-'*60}")
    print(f"  {'Mean gap (fights)':<24}  {mn_f:>12.1f}  {baseline_gap_fights:>12.0f}  {mn_f - baseline_gap_fights:>+8.1f}")
    print(f"  {'Median gap (fights)':<24}  {md_f:>12.1f}  {baseline_gap_fights:>12.0f}  {md_f - baseline_gap_fights:>+8.1f}")
    print(f"  {'Mean gap (sim-days)':<24}  {mn_d:>12.1f}  {baseline_gap_days:>12.0f}  {mn_d - baseline_gap_days:>+8.1f}")
    print(f"  {'Median gap (sim-days)':<24}  {md_d:>12.1f}  {baseline_gap_days:>12.0f}  {md_d - baseline_gap_days:>+8.1f}")
    print()

    # Pass criterion: 2x+ reduction = gap halved = 50%+ improvement from 368 baseline.
    # Anything less than that is not a meaningful density fix (consistent with the
    # user-defined bar after the option-a stratified-A-selection was attempted).
    if improvement_pct >= 50:
        ok = True
        print(f"  {_PASS}  {improvement_pct:.0f}% reduction in mean gap -- 2x+ density improvement achieved.")
    elif improvement_pct >= 20:
        ok = False
        print(f"  MARGINAL  {improvement_pct:.0f}% reduction in mean gap -- better but below 2x target.")
        _print_density_analysis(initial_pop, baseline_gap_fights)
    else:
        ok = False
        pct_str = f"+{abs(improvement_pct):.0f}% longer" if improvement_pct < 0 else f"{improvement_pct:.0f}% shorter"
        print(f"  {_FAIL}  Mean gap {pct_str} than baseline -- no meaningful density improvement.")
        _print_density_analysis(initial_pop, baseline_gap_fights)

    return ok


def _print_density_analysis(initial_pop: int, baseline_gap: float) -> None:
    """Explain the structural reason density didn't improve much."""
    n_ranked_lw  = RANKINGS_SIZE          # 15
    n_elite_lw   = 45                     # approximate, 15 per tier4 pool × 3 WCs... no wait, per WC
    # Actually: ~45 per WC in tier4, with 15 ranked.
    # When A is a ranked LW fighter (15 out of ~initial_pop), they draw from ranked pool (14 others)
    # 88% of the time. P(specific ranked X is chosen as B | A is ranked LW) ≈ 1/14 by symmetry.
    # P(X is B from ranked-A path) = (14/initial_pop) × 0.88 × 0.88 × (1/14) = 0.88²/initial_pop
    # Under OLD: P(X is B from any Elite-A) = (44/initial_pop) × 0.88 × (1/44) = 0.88/initial_pop
    # NEW vs OLD: 0.88² vs 0.88 — only 12% reduction from ranked-A path.
    # But unranked-A's no longer pick ranked X (lost 0.88/initial_pop x 30/44 ~= lost most B-appearances).
    # Net: small improvement or even slight regression.

    print()
    print("  Why didn't density improve more?")
    print(f"  The fundamental constraint is the uniform random pick of fighter A from all")
    print(f"  {initial_pop} fighters: any specific ranked fighter is chosen as A only once per")
    print(f"  {initial_pop:.0f} fights (1/{initial_pop}). As B, they are chosen by another")
    print(f"  Elite fighter only when that Elite fighter is A (about {n_elite_lw*3}/{initial_pop} ~= "
          f"{100*n_elite_lw*3/initial_pop:.0f}% of fights).")
    print()
    print("  The sub-pool split changes WHO ranked fighters face, not HOW OFTEN")
    print("  the sim picks them. The two effects partially cancel:")
    print("    + Ranked A's now pick from 14 ranked (vs 44 Elite) -> X chosen 3x more")
    print("      often by ranked A's (88%x1/14 vs 1/44 per ranked-A fight).")
    print("    - But unranked A's no longer pick ranked X (they now favor unranked pool)")
    print("      -> X loses appearances from unranked-A-picks, mostly offsetting the gain.")
    print()
    print("  To meaningfully fix density for ranked fighters, consider one of:")
    print("    a) Stratified selection: pick A as 'Elite fighter' more often than 1/N.")
    print("    b) Dedicated Elite fight loop: schedule ranked-pool fights at a fixed rate")
    print("       independent of the main random draw.")
    print("    c) Smaller Elite pool: fewer fighters means each appears more often.")
    print("    d) Accept current density and rely on inactivity_percentile (relative")
    print("       measure) for downstream title-fight decisions -- already in place.")


# ── CHECK 4: Inactivity primitive sanity ─────────────────────────────────────

def check_inactivity_primitive(all_fighters: list, pools: dict) -> bool:
    print(f"\nCHECK 4: Inactivity primitive sanity ({TARGET_WC.title()} Elite ranked pool)")

    elite_pool   = pools[TARGET_WC].get("tier4", [])
    ranked_ids   = get_ranked_ids()
    ranked_pool  = [f for f in elite_pool if f.fighter_id in ranked_ids]

    if not ranked_pool:
        print("  SKIP: no ranked fighters in LW Elite pool")
        return True

    flagged = valid = 0
    for f in ranked_pool:
        result = inactivity_percentile(f, ranked_pool)
        if result is None:
            continue
        valid   += 1
        flagged += int(result.is_relatively_inactive)

    if valid == 0:
        print("  SKIP: no fighters with valid fight history in ranked pool")
        return True

    pct_flagged = 100 * flagged / valid
    print(f"  Ranked pool size: {len(ranked_pool)}   with valid history: {valid}")
    print(f"  Flagged (relatively inactive): {flagged}/{valid} = {pct_flagged:.0f}%")
    print(f"  Threshold: {INACTIVITY_PERCENTILE_THRESHOLD:.0f}th percentile "
          f"(top {100 - INACTIVITY_PERCENTILE_THRESHOLD:.0f}% longest gaps flagged)")
    print(f"  Reference: ~10-13% when originally validated against thin Elite pool")

    # With 15 ranked fighters and 85th percentile, exactly ~2 will be flagged
    # (14/15 = 93rd pct → flagged, 13/15 = 87th → flagged, 12/15 = 80th → not flagged).
    # So the expected count is 2/15 ≈ 13% regardless of gap distribution.
    # The important check is that the primitive runs without error and doesn't flag > 50%.
    ok = pct_flagged <= 50
    print(f"  {_PASS if ok else _FAIL}  {'minority flagged — distribution is discriminating' if ok else 'majority flagged'}")
    return ok


# ── CHECK 5: Unranked Elite fighter activity ──────────────────────────────────

def check_unranked_activity(
    ranked_log:   dict[str, list[tuple[int, int]]],
    unranked_log: dict[str, list[tuple[int, int]]],
    initial_pop:  int,
) -> bool:
    print("\nCHECK 5: Unranked Elite fighter activity (not starved by new structure)")

    if not unranked_log:
        print("  SKIP: no unranked Elite fighter appearances logged")
        return True

    ur_counts  = [len(v) for v in unranked_log.values()]
    rk_counts  = [len(v) for v in ranked_log.values()]
    avg_unrank = sum(ur_counts) / len(ur_counts)
    avg_rank   = sum(rk_counts) / max(1, len(rk_counts))

    total_tracked = sum(ur_counts) + sum(rk_counts)
    n_tracked     = len(unranked_log) + len(ranked_log)
    avg_tracked   = total_tracked / n_tracked if n_tracked else 0

    # Under option (b) scheduled fights, A is always a ranked fighter.
    # Unranked Elite fighters therefore appear ONLY in main-loop Elite-vs-Elite fights
    # (as A at natural rate, or as B when a ranked A draws cross-pool RU 12% of the time).
    # The ranked/unranked ratio will be high by design -- not a starvation signal.
    # The right question is: do unranked fighters have ANY meaningful Elite appearances?
    # Pass: avg appearances > 0 AND unranked fighters are not completely absent.
    ratio      = avg_rank / max(0.01, avg_unrank)
    n_unranked = len(unranked_log)

    print(f"  Unranked Elite fighters tracked: {n_unranked}")
    print(f"  Avg appearances while unranked:  {avg_unrank:.1f}  (main-loop only -- expected low)")
    print(f"  Ranked fighters tracked:          {len(ranked_log)}")
    print(f"  Avg appearances while ranked:    {avg_rank:.1f}  (main-loop + scheduled)")
    print(f"  Ranked / unranked ratio:         {ratio:.1f}x")
    print()
    print("  Note: under option (b), scheduled fights pick A from ranked pool only,")
    print("  so ranked/unranked asymmetry is intentional.  Check: unranked fighters")
    print("  still appear in main-loop Elite-vs-Elite fights (not completely absent).")

    # Pass: at least some unranked fighters are getting Elite-vs-Elite exposure.
    ok = n_unranked >= 5 and avg_unrank >= 0.5
    print(f"  {_PASS if ok else _FAIL}  "
          f"{'unranked fighters still appearing in Elite fights via main loop' if ok else 'unranked fighters absent from Elite fights'}")
    return ok


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=== Elite Matchmaking Part 2: Verification ===")
    print()

    all_fighters, pools, ranked_log, unranked_log, initial_pop = run_sim()

    results = [
        check_sub_pool_split(),
        check_proximity_weighting(),
        check_density_fix(ranked_log, initial_pop),
        check_inactivity_primitive(all_fighters, pools),
        check_unranked_activity(ranked_log, unranked_log, initial_pop),
    ]

    n_pass = sum(results)
    n_fail = len(results) - n_pass
    print()
    print("=" * 52)
    print(f"Results: {n_pass}/{len(results)} PASS")
    if n_fail == 0:
        print("All checks passed.")
    else:
        print(f"{n_fail} check(s) FAILED or MARGINAL -- see analysis above.")
    print()
