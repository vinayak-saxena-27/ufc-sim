"""
league_season.py -- The League's season/playoff format (Org Identity, Session A,
Part 3).

The League runs a season/playoff tournament structure instead of Apex FC/
Eastern GP's continuous matchmaking. Per the user's explicit choice, this is a
SEPARATE DEDICATED SCHEDULER, fully decoupled from the normal per-fight random
matchmaking cadence: The League's tier4 fighters are excluded from sim.py's
main-loop random draw entirely (see sim.py wiring) and from
matchmaking.pick_scheduled_elite_a's density-fight injection -- every League
fight they have comes from run_league_season() below, called once per
main-loop iteration on its own LEAGUE_FIGHT_INTERVAL cadence.

## Format (first-pass, concrete values)

  Regular season: LEAGUE_SEASON_LENGTH (20) fights per weight class accrue
    points -- FINISH_WIN_POINTS (3) > DECISION_WIN_POINTS (1) > loss (0).
  Playoffs: at season end, the top LEAGUE_PLAYOFF_SIZE (4) point-scorers (or
    fewer if the League's pool in that weight class is thin -- degrades
    gracefully down to a 2-fighter single final) advance to a single-
    elimination bracket: semis (1v4, 2v3) then a final.
  Champion: the final's winner becomes The League's champion for that weight
    class -- awarded via career.labels.award_title() directly (same pattern
    weight_transfers.py already uses for its own campaign-title path,
    bypassing title.py's TITLE_FIGHT_INTERVAL machinery entirely, since
    title.maybe_run_title_fight is a hard no-op for The League -- see that
    module's early-return guard).
  Points reset to 0 for everyone in that weight class at season end; a prior
  champion re-enters the next regular season on equal footing (no bye).

## Fight resolution

Identical phase engine / scoring as any other fight (simulate_fight), just
with decision_mode="round_count" (Apex FC/The League's round-by-round win-
counting -- see engine/fight_engine.py's docstring). The tournament format is
about the competitive STRUCTURE around fights, not how fights themselves
resolve.

## Hype (Part 5)

Every league_season/playoff fight still runs the universal
career.hype.update_hype_after_fight() for both participants (base finish/
decision/adversity/style modifiers -- The League's org multiplier there is a
no-op per hype.py's _org_culture_multiplier: "no method-specific modifier").
On top of that, a playoff-stage WIN (semifinal or final) gets The League's
own Part-5 hype-culture bonus (+15%, LEAGUE_PLAYOFF_HYPE_BONUS) applied to the
realized per-fight hype delta via career.hype.apply_title_hype() -- the same
generic floor-clamped-add helper title.py uses for its title bonuses. The
FINAL additionally gets the universal title_win_bonus()/title_loss_penalty()
(the same belt-level bonus every other org's title fight gets), since that
fight IS the actual championship.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from career.fighter import Fighter
from career.tiers import WEIGHT_CLASSES
from engine.fight import simulate_fight
from matchmaking import apply_tier_transitions
from career.labels import maybe_update_labels, award_title
from career.development import apply_win_development_boost, apply_phase_development_feedback
from career.hype import (
    update_hype_after_fight, apply_title_hype,
    title_win_bonus, title_loss_penalty,
)
from career.retirement import maybe_evaluate_retirement
from career.cuts import maybe_evaluate_cut, is_removed
from orgs.org_registry import THE_LEAGUE_NAME

# ── Tuning constants (first-pass, documented) ────────────────────────────────

LEAGUE_FIGHT_INTERVAL: int = 4
"""One League regular-season fixture is attempted per this many main-loop
iterations -- same shape as matchmaking.ELITE_FIGHT_INTERVAL's density-fight
injection, just routed through this module's own scheduler instead."""

LEAGUE_SEASON_LENGTH: int = 20
"""Regular-season fights per weight class before playoffs trigger. First-pass:
with LEAGUE_FIGHT_INTERVAL=4 and a 2000-fight sim, ~500 League ticks occur,
so a weight class reaching 20 fights (a full season) multiple times over a
run is expected -- enough to observe several playoff cycles."""

LEAGUE_PLAYOFF_SIZE: int = 4
"""Top-N point-scorers advance to the playoff bracket. Degrades to fewer if
the League's pool in that weight class has less than 4 fighters with any
season points recorded (or fewer than 4 in the pool at all)."""

FINISH_WIN_POINTS:   float = 3.0
DECISION_WIN_POINTS: float = 1.0
"""Points scheme: finish win > decision win > loss (0), per spec."""

LEAGUE_PLAYOFF_HYPE_BONUS: float = 0.15
"""Part 5: +15% on the realized per-fight hype delta for a playoff-stage WIN
(semifinal or final) -- 'tournament drama generates its own buzz.'"""


# ── State ─────────────────────────────────────────────────────────────────────

_season_fight_counters: dict[str, int] = {}
_season_points:         dict[str, dict[str, float]] = {}
_season_number:         dict[str, int] = {}


def reset_league_season() -> None:
    """Clear all League season state. Call at the start of each simulation."""
    _season_fight_counters.clear()
    _season_points.clear()
    _season_number.clear()
    for wc in WEIGHT_CLASSES:
        _season_fight_counters[wc] = 0
        _season_points[wc] = {}
        _season_number[wc] = 1


@dataclass
class LeagueSeasonRecord:
    weight_class:   str
    season_number:  int
    top_scorers:    list[tuple[str, float]]   # [(fighter_name, points), ...] entering playoffs
    semifinal_results: list[tuple[str, str, str]]   # (winner_name, loser_name, method)
    final_result:      tuple[str, str, str] | None  # (winner_name, loser_name, method), None if no final ran
    champion_name:     str | None


_season_history: list[LeagueSeasonRecord] = []


def reset_league_history() -> None:
    _season_history.clear()


def get_league_history() -> list[LeagueSeasonRecord]:
    return list(_season_history)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _league_pool(pools: dict[str, dict[str, list[Fighter]]], wc: str) -> list[Fighter]:
    return [f for f in pools[wc].get("tier4", []) if f.org == THE_LEAGUE_NAME]


def _award_points(fighter_id: str, wc: str, won: bool, method: str) -> None:
    if not won:
        return
    pts = FINISH_WIN_POINTS if method != "decision" else DECISION_WIN_POINTS
    _season_points[wc][fighter_id] = _season_points[wc].get(fighter_id, 0.0) + pts


def _run_one_fight(
    fa: Fighter, fb: Fighter, pools: dict[str, dict[str, list[Fighter]]],
    fight_num: int, sim_day: int, all_fighters: list[Fighter] | None,
    *, is_final: bool, playoff_stage: bool,
) -> tuple[Fighter, Fighter]:
    """Simulate one League fight (regular season OR playoff) and run the same
    post-fight bookkeeping every other fight source in this sim runs (dev
    boost, phase feedback, hype, tier transitions, labels, retirement/cut)."""
    hype_before_a, hype_before_b = fa.hype, fb.hype
    winner, loser = simulate_fight(
        fa, fb, org="league_season", sim_day=sim_day,
        is_title=is_final, decision_mode="round_count",
    )
    apply_win_development_boost(winner)
    apply_phase_development_feedback(winner)
    apply_phase_development_feedback(loser)
    update_hype_after_fight(winner, loser)
    update_hype_after_fight(loser, winner)

    if playoff_stage:
        hype_before = hype_before_a if winner is fa else hype_before_b
        realized_delta = winner.hype - hype_before
        apply_title_hype(winner, realized_delta * LEAGUE_PLAYOFF_HYPE_BONUS)

    if is_final:
        award_title(winner)
        apply_title_hype(winner, title_win_bonus())
        apply_title_hype(loser, title_loss_penalty())

    removed_ids: list[str] = []
    for fighter in (winner, loser):
        apply_tier_transitions(fighter, pools)
        maybe_update_labels(fighter)
        removed = maybe_evaluate_retirement(fighter, pools, fight_num=fight_num)
        if not removed:
            removed = maybe_evaluate_cut(fighter, pools, fight_num=fight_num)
        if removed:
            removed_ids.append(fighter.fighter_id)
    if removed_ids and all_fighters is not None:
        all_fighters[:] = [f for f in all_fighters if f.fighter_id not in removed_ids]

    return winner, loser


def _run_playoffs(
    wc: str, pools: dict[str, dict[str, list[Fighter]]],
    fight_num: int, sim_day: int, all_fighters: list[Fighter] | None,
) -> None:
    pool = _league_pool(pools, wc)
    points = _season_points.get(wc, {})
    by_id = {f.fighter_id: f for f in pool}

    seeded_ids = sorted(
        (fid for fid in points if fid in by_id),
        key=lambda fid: points[fid], reverse=True,
    )[:LEAGUE_PLAYOFF_SIZE]
    seeds = [by_id[fid] for fid in seeded_ids]

    season_num = _season_number.get(wc, 1)
    top_scorers = [(f.name, points[f.fighter_id]) for f in seeds]
    semifinal_results: list[tuple[str, str, str]] = []
    final_result: tuple[str, str, str] | None = None
    champion_name: str | None = None

    if len(seeds) >= 4:
        w1, l1 = _run_one_fight(seeds[0], seeds[3], pools, fight_num, sim_day, all_fighters,
                                 is_final=False, playoff_stage=True)
        semifinal_results.append((w1.name, l1.name, w1.fight_history[-1].method))
        w2, l2 = _run_one_fight(seeds[1], seeds[2], pools, fight_num, sim_day, all_fighters,
                                 is_final=False, playoff_stage=True)
        semifinal_results.append((w2.name, l2.name, w2.fight_history[-1].method))
        # A semifinal winner can retire/get cut in _run_one_fight's own post-fight
        # bookkeeping (apply_tier_transitions/maybe_evaluate_retirement/
        # maybe_evaluate_cut) before the final runs -- execute_removal() has
        # already pulled them out of pools[wc][their_tier] at that point, so
        # re-running apply_tier_transitions on them in the final would crash
        # with "list.remove(x): x not in list". Check first and skip the final
        # gracefully instead (no champion crowned this season) if either
        # finalist is gone.
        if is_removed(w1.fighter_id) or is_removed(w2.fighter_id):
            print(f"[LEAGUE] {wc} Season {season_num} -- a semifinal winner "
                  f"retired/was cut before the final could run; season ends "
                  f"without a crowned champion.")
        else:
            champ, runner_up = _run_one_fight(w1, w2, pools, fight_num, sim_day, all_fighters,
                                               is_final=True, playoff_stage=True)
            final_result = (champ.name, runner_up.name, champ.fight_history[-1].method)
            champion_name = champ.name
            print(f"[LEAGUE] {wc} Season {season_num} CHAMPION -- {champ.name}!")
    elif len(seeds) >= 2:
        # Thin pool: skip straight to a final between the top two scorers.
        champ, runner_up = _run_one_fight(seeds[0], seeds[1], pools, fight_num, sim_day, all_fighters,
                                           is_final=True, playoff_stage=True)
        final_result = (champ.name, runner_up.name, champ.fight_history[-1].method)
        champion_name = champ.name
        print(f"[LEAGUE] {wc} Season {season_num} CHAMPION (thin-pool final) -- {champ.name}!")
    else:
        print(f"[LEAGUE] {wc} Season {season_num} -- pool too thin for playoffs, season skipped.")

    _season_history.append(LeagueSeasonRecord(
        weight_class=wc, season_number=season_num,
        top_scorers=top_scorers, semifinal_results=semifinal_results,
        final_result=final_result, champion_name=champion_name,
    ))

    # Reset for next season -- points wipe for everyone; prior champion re-enters
    # the regular season on equal footing (no special seeding/bye).
    _season_points[wc] = {}
    _season_number[wc] = season_num + 1


# ── Main scheduling hook ──────────────────────────────────────────────────────

def run_league_season(
    pools: dict[str, dict[str, list[Fighter]]],
    fight_num: int,
    sim_day: int,
    all_fighters: list[Fighter] | None = None,
) -> None:
    """
    Call once per main sim-loop iteration. Gated on LEAGUE_FIGHT_INTERVAL --
    a no-op most calls. When it fires, picks one weight class at random and
    runs one regular-season fixture between two random League tier4 fighters
    in that division; if that fixture brings the weight class's season fight
    count to LEAGUE_SEASON_LENGTH, immediately runs the playoff bracket and
    starts a new season.
    """
    if fight_num % LEAGUE_FIGHT_INTERVAL != 0:
        return

    wc = random.choice(WEIGHT_CLASSES)
    pool = _league_pool(pools, wc)
    if len(pool) < 2:
        return   # thin pool this cycle -- try again next tick

    fa, fb = random.sample(pool, 2)
    winner, loser = _run_one_fight(fa, fb, pools, fight_num, sim_day, all_fighters,
                                    is_final=False, playoff_stage=False)
    _award_points(winner.fighter_id, wc, won=True, method=winner.fight_history[-1].method)
    _award_points(loser.fighter_id, wc, won=False, method=loser.fight_history[-1].method)

    _season_fight_counters[wc] = _season_fight_counters.get(wc, 0) + 1
    if _season_fight_counters[wc] >= LEAGUE_SEASON_LENGTH:
        _season_fight_counters[wc] = 0
        _run_playoffs(wc, pools, fight_num, sim_day, all_fighters)
