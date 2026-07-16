"""
run_calibration.py -- 10+ sim-year world-calibration run vs real-world UFC
benchmarks (calibration session, 2026-07-15).

Standalone script (repo convention, see CLAUDE.md): own sys.path shim, prints
diagnostics, exits nonzero on a hard failure to run (NOT on benchmark misses --
those are reported as findings, not assertion failures, since this is a
calibration report rather than a pass/fail test).

Runs the FULL sim (career/tiers.py's population + every lifecycle subsystem --
replenishment, retirement, org movement, hype, rankings, titles) via sim.py's
documented init_sim()/step_sim()/get_sim_state() API, exactly like sim.py's own
CLI driver, then aggregates every fight into a comparison table against the
real-world benchmark ranges from the user's calibration brief.

Data collection note: sim.py drops retired/cut fighters from _sim_state.
all_fighters IN PLACE once they leave the active population, so a fighter's
only reachable reference disappears after removal. We snapshot after every
batch (not just at the end) into a script-local fighter_id -> Fighter dict --
since retirement/cuts only ever happen inside a step_sim() call, snapshotting
between every call guarantees every fighter that ever existed gets captured at
least once before any later removal.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import init_sim, step_sim, get_sim_state
from sim_calendar import SIM_DAYS_PER_FIGHT
from career.tiers import WEIGHT_CLASSES, TIER_RULESET
from career.fighter import Fighter, FightResult
from orgs.org_registry import APEX_FC_NAME, ORGS, MIDMAJOR_ORGS, REGIONAL_ORGS

SCRATCHPAD_DIR = os.environ.get(
    "CALIBRATION_OUTPUT_DIR",
    r"C:\Users\vinay\AppData\Local\Temp\claude\c--Users-vinay-ufc-sim\7dc947a1-8b65-4533-b811-c5dd306fa28c\scratchpad",
)

BATCH_FIGHTS = 50  # fight-attempts per step_sim() call between snapshots


# ─── Org-tree resolution (programmatic, not hardcoded names) ──────────────────

def _all_orgs_by_name() -> dict:
    merged = {}
    merged.update(ORGS)
    merged.update(MIDMAJOR_ORGS)
    merged.update(REGIONAL_ORGS)
    return merged


def is_apex_affiliated(org_name: str, _orgs: dict = None) -> bool:
    """Walk primary_feeds_to up from org_name; True if it terminates at Apex FC
    (or org_name IS Apex FC). Programmatic so it stays correct if the org graph
    changes, per the plan's non-hardcoding requirement."""
    if org_name == APEX_FC_NAME:
        return True
    orgs = _orgs if _orgs is not None else _all_orgs_by_name()
    seen = set()
    current = org_name
    while current and current not in seen:
        seen.add(current)
        org = orgs.get(current)
        if org is None:
            return False
        if org.primary_feeds_to == APEX_FC_NAME:
            return True
        current = org.primary_feeds_to
    return False


# ─── Data collection ───────────────────────────────────────────────────────────

def collect(n_fights: int, scale: float, seed: int) -> dict[str, Fighter]:
    init_sim(scale=scale, seed=seed)
    seen: dict[str, Fighter] = {}
    remaining = n_fights
    while remaining > 0:
        batch = min(BATCH_FIGHTS, remaining)
        step_sim(days=batch * SIM_DAYS_PER_FIGHT)
        remaining -= batch
        for f in get_sim_state().all_fighters:
            seen[f.fighter_id] = f
    return seen


def extract_fight_log(fighters: dict[str, Fighter]) -> list[dict]:
    """One row per actual fight (the WINNER's FightResult only, to avoid
    double-counting the mirrored win+loss pair each fight produces).

    NOTE: FightResult.org is NOT the fighter's real organization -- sim.py
    passes it a hardcoded generic "league" literal at its simulate_fight()
    call site, unrelated to org identity. The real affiliation lives on
    Fighter.org (career/fighter.py), so we read the WINNER's current .org
    here instead. This is an approximation for fighters who changed orgs
    mid-career (lateral transfers, Apex poaching) -- it reflects each
    fighter's org at extraction time, not necessarily at the time of every
    individual historical fight -- but FightResult has no per-fight org
    snapshot to fall back on, and most fighters change orgs rarely."""
    rows = []
    for f in fighters.values():
        for r in f.real_fight_history:
            if r.outcome != "win":
                continue
            rows.append({
                "winner": f.name, "opponent": r.opponent_name,
                "method": r.method, "weight_class": r.weight_class,
                "org": f.org, "tier": r.tier, "is_title": r.is_title,
                "round_finished": r.round_finished, "rounds_completed": r.rounds_completed,
                "decision_type": r.decision_type, "submission_type": r.submission_type,
                "sim_day": r.sim_day,
            })
    return rows


# ─── Aggregate stat helpers ────────────────────────────────────────────────────

def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else float("nan")


def method_breakdown(rows: list[dict]) -> dict[str, float]:
    n = len(rows)
    ko = sum(1 for r in rows if r["method"] == "KO/TKO")
    sub = sum(1 for r in rows if r["method"] == "submission")
    dec = sum(1 for r in rows if r["method"] == "decision")
    return {
        "finish_rate": _pct(ko + sub, n), "ko_rate": _pct(ko, n),
        "sub_rate": _pct(sub, n), "decision_rate": _pct(dec, n), "n": n,
    }


def decision_type_breakdown(rows: list[dict]) -> dict[str, float]:
    decisions = [r for r in rows if r["method"] == "decision"]
    n = len(decisions)
    unan = sum(1 for r in decisions if r["decision_type"] == "unanimous")
    split = sum(1 for r in decisions if r["decision_type"] == "split")
    maj = sum(1 for r in decisions if r["decision_type"] == "majority")
    return {
        "unanimous_pct": _pct(unan, n), "split_pct": _pct(split, n),
        "majority_pct": _pct(maj, n), "dissent_pct": _pct(split + maj, n), "n": n,
    }


def submission_type_breakdown(rows: list[dict]) -> dict[str, float]:
    subs = [r for r in rows if r["method"] == "submission"]
    n = len(subs)
    out = {"n": n}
    for cat in ("choke", "joint_lock", "leg_lock", "other"):
        out[cat + "_pct"] = _pct(sum(1 for r in subs if r["submission_type"] == cat), n)
    return out


def finish_by_weight_class(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for wc in WEIGHT_CLASSES:
        wc_rows = [r for r in rows if r["weight_class"] == wc]
        out[wc] = method_breakdown(wc_rows)
    return out


def round_finish_distribution(rows: list[dict]) -> dict[int, int]:
    dist = defaultdict(int)
    for r in rows:
        if r["round_finished"] is not None:
            dist[r["round_finished"]] += 1
    return dict(sorted(dist.items()))


def fight_duration_seconds(rows: list[dict]) -> list[float]:
    durations = []
    for r in rows:
        ruleset = TIER_RULESET.get(r["tier"])
        round_seconds = ruleset.round_seconds if ruleset else 300
        if r["round_finished"] is not None:
            # Approximation: full prior rounds + half the finishing round
            # (exact within-round finish time isn't persisted anywhere).
            durations.append((r["round_finished"] - 1) * round_seconds + round_seconds / 2)
        else:
            durations.append(r["rounds_completed"] * round_seconds)
    return durations


def fights_per_fighter_year(fighters: dict[str, Fighter]) -> list[int]:
    """Bucket each fighter's real fight history by sim_day // 365; return the
    fight count for every (fighter, year) bucket that had >= 1 fight."""
    counts = []
    for f in fighters.values():
        buckets = defaultdict(int)
        for r in f.real_fight_history:
            buckets[r.sim_day // 365] += 1
        counts.extend(buckets.values())
    return counts


def career_lengths(fighters: dict[str, Fighter]) -> tuple[list[float], list[int]]:
    """(years-active list, total-fight-count list) across fighters with >=1 real fight."""
    years, totals = [], []
    for f in fighters.values():
        hist = f.real_fight_history
        if not hist:
            continue
        days = [r.sim_day for r in hist]
        years.append((max(days) - min(days)) / 365.0)
        totals.append(len(hist))
    return years, totals


# ─── Comparison table ──────────────────────────────────────────────────────────

def _check(label: str, actual: float, lo: float, hi: float, tolerance_note: str = "") -> None:
    if actual != actual:  # NaN guard (no data in bucket)
        print(f"  [NO DATA]  {label:<45}  --  (want {lo}-{hi})")
        return
    if lo <= actual <= hi:
        status = "OK  "
    else:
        span = hi - lo
        off_frac = min(abs(actual - lo), abs(actual - hi)) / span if span else 1.0
        status = "FLAG" if off_frac > 0.15 else "OK  "
    note = f"  ({tolerance_note})" if tolerance_note else ""
    print(f"  [{status}]  {label:<45}  {actual:>6.1f}%  (want {lo}-{hi}%){note}")


def print_report(rows: list[dict], fighters: dict[str, Fighter], orgs: dict) -> None:
    print("=" * 78)
    print("CALIBRATION REPORT")
    print("=" * 78)
    print(f"Total fights logged: {len(rows)}")
    print()

    print("-- Overall outcome distribution --")
    overall = method_breakdown(rows)
    _check("Finish rate (KO/TKO + submission)", overall["finish_rate"], 44.5, 52.6, "noise band")
    _check("KO/TKO rate", overall["ko_rate"], 32, 33)
    _check("Submission rate", overall["sub_rate"], 19, 20)
    _check("Decision rate", overall["decision_rate"], 44, 47)
    print()

    print("-- Decision sub-types --")
    dec = decision_type_breakdown(rows)
    _check("Unanimous (of decisions)", dec["unanimous_pct"], 77, 77)
    _check("Split (of decisions)", dec["split_pct"], 20, 20)
    _check("Majority (of decisions)", dec["majority_pct"], 2, 3)
    _check("At-least-one-dissent rate", dec["dissent_pct"], 24, 26)
    print()

    print("-- Submission-method breakdown --")
    sub = submission_type_breakdown(rows)
    _check("Chokes (of submissions)", sub["choke_pct"], 79, 79)
    _check("Joint locks (of submissions)", sub["joint_lock_pct"], 15, 15)
    _check("Leg locks (of submissions)", sub["leg_lock_pct"], 3, 3)
    print()

    print("-- Finish rate by weight class (whole population) --")
    by_wc = finish_by_weight_class(rows)
    wc_targets = {
        "heavyweight":  (66, 66, 45, 48, 18, 22),
        "welterweight": (50, 52, 30, 30, 20, 20),
        "lightweight":  (50, 52, 29, 29, 20, 20),
    }
    for wc in WEIGHT_CLASSES:
        b = by_wc[wc]
        t = wc_targets[wc]
        print(f"  {wc}: n={b['n']}")
        _check(f"    {wc} finish rate", b["finish_rate"], t[0], t[1])
        _check(f"    {wc} KO rate", b["ko_rate"], t[2], t[3])
        _check(f"    {wc} sub rate", b["sub_rate"], t[4], t[5])
    print()

    print("-- Finish rate by weight class (Apex FC + affiliated feeder tree ONLY) --")
    apex_rows = [r for r in rows if is_apex_affiliated(r["org"], orgs)]
    by_wc_apex = finish_by_weight_class(apex_rows)
    for wc in WEIGHT_CLASSES:
        b = by_wc_apex[wc]
        t = wc_targets[wc]
        print(f"  {wc}: n={b['n']}")
        _check(f"    {wc} finish rate", b["finish_rate"], t[0], t[1])
        _check(f"    {wc} KO rate", b["ko_rate"], t[2], t[3])
        _check(f"    {wc} sub rate", b["sub_rate"], t[4], t[5])
    print()

    print("-- Fight duration --")
    durations = fight_duration_seconds(rows)
    if durations:
        avg_s, med_s = mean(durations), median(durations)
        print(f"  Average: {avg_s/60:.1f} min (want ~10.6 min)")
        print(f"  Median:  {med_s/60:.1f} min (want ~13.8 min)")
    print()

    print("-- Round-of-finish distribution --")
    dist = round_finish_distribution(rows)
    total_finishes = sum(dist.values())
    for rnd, n in dist.items():
        print(f"  Round {rnd}: {n}  ({_pct(n, total_finishes):.1f}%)")
    print()

    print("-- Fight frequency (fights per active fighter-year) --")
    freq = fights_per_fighter_year(fighters)
    if freq:
        print(f"  Mean: {mean(freq):.2f}  (want ~1.7-1.8)")
        print(f"  Median: {median(freq):.1f}  (want ~2)")
        one_fight_pct = _pct(sum(1 for c in freq if c == 1), len(freq))
        print(f"  Exactly 1 fight: {one_fight_pct:.1f}%  (want ~50%)")
    print()

    print("-- Career length (approximate, derived from sim_day span) --")
    years, totals = career_lengths(fighters)
    if years:
        print(f"  Mean years active: {mean(years):.1f}  (want ~7-13 for top-tier veterans)")
        print(f"  Mean total fights: {mean(totals):.1f}  (want ~20-35 for veterans)")
    print()


def export_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} fight rows to {path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fights", type=int, default=2000, help="fight-attempt budget (default 2000, ~11 sim years)")
    p.add_argument("--fighters", type=float, default=1.0, metavar="SCALE", help="roster scale multiplier")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    print(f"Running {args.fights} fight-attempts (scale={args.fighters}, seed={args.seed})...")
    fighters = collect(args.fights, args.fighters, args.seed)
    print(f"Collected {len(fighters)} fighters seen across the run.")

    rows = extract_fight_log(fighters)
    orgs = _all_orgs_by_name()
    print_report(rows, fighters, orgs)

    os.makedirs(SCRATCHPAD_DIR, exist_ok=True)
    export_csv(rows, os.path.join(SCRATCHPAD_DIR, f"calibration_fightlog_seed{args.seed}.csv"))


if __name__ == "__main__":
    main()
