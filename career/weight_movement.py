"""
weight_movement.py — Weight-class movement DECISION logic (Weight Class Flex,
Session B).

This module evaluates fighters periodically and sets PENDING FLAGS
(weight_class_move_candidate / _direction / _reason / _target on Fighter) when
one of four drivers below is met and a probabilistic roll fires. It never
executes a move — no pool transfer, no fighter.weight_class write. A later
session consumes these flags to do that.

## Evaluation cadence

evaluate_weight_class_move(fighter, pools) fully recomputes (not sticky) a
fighter's flags from scratch — same non-sticky pattern as labels.update_labels().
maybe_evaluate_weight_move(fighter, pools) gates that on the same per-fighter
LABEL_UPDATE_INTERVAL fight-count trigger used by maybe_update_labels() /
maybe_evaluate_cut() / maybe_evaluate_retirement() ("reuse the existing
trigger"). Wired into sim.py's main fight loop only this session (not the
title-fight-resolution branch in title.py or the scheduled-Elite branch in
sim.py) to keep this session's footprint minimal — later wiring is additive
and can extend to those call sites without touching this module.

## Known data limitation — Driver 3's "late-round degradation" signal

FightResult (fighter.py) stores only an aggregate score_margin, method, and
rounds_completed per fight — there is no round-by-round score breakdown
anywhere in fight_history. Driver 3 therefore uses a proxy available from
existing stored fields: among a fighter's recent LOSSES, the fraction that
were finishes (method != "decision") occurring in the fight's FINAL scheduled
round (rounds_completed >= that tier's max round count). A late finish is
read as "faded and got finished," a plausible fatigue/cut signal; an early
finish (round 1-2) reads as a pure skill/power mismatch instead. This is a
first-pass proxy, not a substitute for real round-by-round scoring.

## Four drivers (checked in order 1→4; first driver whose gate conditions are
## met wins for this evaluation cycle — same "first match wins" structure as
## retirement.py's three-path evaluation)

  1. title_ambition (always UP):    Champion / recent title pedigree, enough
     defenses, physically plausible up a class, not about to get their own
     title shot.
  2. struggling (always DOWN):      losing consistently, not Washed (that's a
     cut/retire case), not already at the floor division, cut isn't the
     problem (cut_severity LOW).
  3. cut_damage (always UP):        cut_severity HIGH, late-finish-loss proxy
     signals fading specifically (not just losing), old enough (or cut severe
     enough regardless of age) for it to plausibly be the cut.
  4. opportunity (UP or DOWN):      Contender-caliber fighter, home division
     congested, an adjacent division has a genuine opening. Down-direction
     additionally requires cut_severity LOW (same reasoning as Driver 2).

Meeting a driver's gate conditions does not guarantee a flag: each driver has
a probability-of-firing function that scales with how strongly conditions are
met (see _move_probability). Most fighters who qualify will still roll false
most cycles — same "not everyone who could leave does" texture as cuts.py /
retirement.py's probabilistic firing.

## Class-skip logic

Normally moves target the ADJACENT division only. A skip (two divisions in
one move, e.g. LW->HW skipping WW) is possible ONLY for the title_ambition
and opportunity drivers (per spec, struggling/cut_damage physically can't
skip), and ONLY in the "up" direction (the "rare physical freak who can
compete two classes up" framing is inherently an upward phenomenon — skipping
DOWN two divisions isn't a comparable feat and is explicitly out of scope).
Skip additionally requires
top-percentile overall within the current tier pool AND very high hype, and
even then only fires _CLASS_SKIP_PROB of the time — rare by construction.

## Congestion / opportunity heuristic (Driver 4)

Rankings (rankings.py) only exist for tier4 (Elite). For tier4 fighters,
congestion = ranked-pool depth + the current champion's defense count
(entrenched champion = more blocked). For every other tier, there is no
rankings signal to read, so congestion falls back to raw pool size at that
tier — a rougher but data-available proxy. This is a natural consequence of
which systems this session is allowed to read, not a separate design choice
requiring confirmation: Driver 4 (and Driver 1's "next challenger" check)
simply degrade gracefully to a coarser signal outside tier4.

All thresholds/probabilities below are first-pass estimates, picked and
documented per this project's established convention; flag for retuning once
real flagged-candidate populations are observable (see __main__ demo).
"""
from __future__ import annotations

import random

from career.fighter import Fighter
from career.tiers import WEIGHT_CLASSES, TIER_RULESET
from career.labels import CHAMPION, CONTENDER, WASHED, LABEL_UPDATE_INTERVAL, get_champion_id
from career.rankings import get_rankings, RANKINGS_SIZE
from career.age import _PRIME_END
from career.weight_cut import _age_cut_scale

# ── Driver 1: title_ambition ──────────────────────────────────────────────────

_TITLE_AMBITION_MIN_DEFENSES: int = 3
"""Min title defenses (wins after the belt-winning fight) before ambition kicks in."""

_TITLE_AMBITION_MIN_OVERALL: float = 35.0
"""'Skill compensates for the cut' bar — comfortably above tier3 center (+20),
below tier4 center (+45); an elite-caliber overall regardless of cut severity."""

_TITLE_AMBITION_BASE_PROB:     float = 0.10
_TITLE_AMBITION_STRENGTH_SCALE: float = 0.05
_TITLE_AMBITION_PROB_CAP:      float = 0.60

# ── Driver 2: struggling ───────────────────────────────────────────────────────

_STRUGGLE_WINDOW:        int   = 5     # matches LABEL_UPDATE_INTERVAL cadence
_STRUGGLE_MAX_WIN_RATE:  float = 0.30  # <=1 win in 5 = "losing consistently"

_STRUGGLE_BASE_PROB:     float = 0.15
_STRUGGLE_STRENGTH_SCALE: float = 0.80
_STRUGGLE_PROB_CAP:      float = 0.55

# ── Driver 3: cut_damage ───────────────────────────────────────────────────────

_CUT_DAMAGE_WINDOW:            int   = 6
_CUT_DAMAGE_LATE_FINISH_RATE:  float = 0.50  # majority of recent finish-losses must be "late"

_CUT_DAMAGE_BASE_PROB:      float = 0.10
_CUT_DAMAGE_STRENGTH_SCALE: float = 0.05
_CUT_DAMAGE_PROB_CAP:       float = 0.65

# ── Shared cut_severity thresholds (LOW / HIGH / VERY HIGH) ───────────────────
# Anchored to weight_cut.py's own scale, which documents +10 to +20 as
# "moderate-to-severe" — HIGH sits at that band's floor.

_CUT_LOW_THRESHOLD:       float = 3.0    # near-zero / easy cut
_CUT_HIGH_THRESHOLD:      float = 12.0   # moderate-to-severe floor (matches weight_cut.py's own language)
_CUT_VERY_HIGH_THRESHOLD: float = 22.0   # severe even for a young fighter

# ── Driver 4: opportunity ──────────────────────────────────────────────────────

_NEAR_CONTENDER_RANK_MAX: int = 8
"""tier4 rank <= this counts as 'near-Contender' even without the label itself."""

_CONGESTION_CHAMP_DEFENSE_WEIGHT: float = 1.0
"""Congestion units added per champion title defense (tier4 only) — an
entrenched champion makes a division look more blocked."""

_CONGESTION_POOL_SIZE_WEIGHT: float = 0.15
"""Congestion units per pool member for tiers with no rankings data (scales
typical 15-100 pool sizes down to the same rough 0-15 range as tier4 depth)."""

_OPPORTUNITY_MIN_SCORE: float = 3.0
"""Minimum (home_congestion - adjacent_congestion) to call the adjacent
division a genuine opening."""

_OPPORTUNITY_BASE_PROB:      float = 0.10
_OPPORTUNITY_STRENGTH_SCALE: float = 0.03
_OPPORTUNITY_PROB_CAP:       float = 0.50

# ── Class-skip constants ───────────────────────────────────────────────────────

_CLASS_SKIP_PERCENTILE:    float = 0.95  # top-5% overall within current tier pool
_CLASS_SKIP_HYPE_THRESHOLD: float = 75.0  # "exceptional" bar — same magnitude as
                                           # rankings.ELITE_GATE_HYPE_THRESHOLD (top ~1%)
_CLASS_SKIP_MIN_POOL:      int   = 5     # need a real pool to compute a percentile from
_CLASS_SKIP_PROB:          float = 0.10  # even when eligible, rare by construction


# ── Internal helpers ────────────────────────────────────────────────────────────

def _title_reign_wins(fighter: Fighter) -> int:
    """
    Count consecutive is_title WINS at the tail of fight_history, walking
    backward until an is_title LOSS breaks the streak (non-title fights
    interleaved do not break it — champions fight plenty of non-title bouts).

    The first title win in the streak is winning the belt, not a defense;
    callers subtract 1 to get a defense count. Returns 0 if the fighter has
    never won a title fight or last held one further back than any losses.
    """
    count = 0
    for r in reversed(fighter.fight_history):
        if not r.is_title:
            continue
        if r.outcome == "win":
            count += 1
        else:
            break
    return count


def _late_finish_loss_rate(fighter: Fighter, window: int) -> float:
    """
    Fraction of recent LOSSES-by-finish that landed in the fight's final
    scheduled round. See module docstring for why this is the Driver 3 proxy.
    Returns 0.0 if there are no finish-losses in the window (no signal either way).
    """
    recent = fighter.fight_history[-window:]
    finishes = [
        r for r in recent
        if r.outcome == "loss" and r.method != "decision" and r.rounds_completed > 0
    ]
    if not finishes:
        return 0.0

    late = 0
    for r in finishes:
        ruleset = TIER_RULESET.get(r.tier)
        if ruleset is None:
            continue
        max_rounds = ruleset.title_rounds if (r.is_title and ruleset.title_rounds) else ruleset.non_title_rounds
        if r.rounds_completed >= max_rounds:
            late += 1
    return late / len(finishes)


def _is_likely_next_challenger(fighter: Fighter, pool: list[Fighter], weight_class: str) -> bool:
    """
    Approximates 'is this fighter about to get their own division's title shot.'

    tier4 (rankings exist): rank #1 (title.py's default challenger pick).
    Other tiers (no rankings data): highest-overall non-champion in the pool —
    a coarser stand-in for 'would be the default pick.'
    """
    if fighter.tier == "tier4":
        for e in get_rankings(weight_class):
            if e.fighter.fighter_id == fighter.fighter_id:
                return e.rank == 1
        return False

    champ_id = get_champion_id(weight_class, fighter.tier)
    candidates = [f for f in pool if f.fighter_id != champ_id]
    if not candidates:
        return False
    top = max(candidates, key=lambda f: f.overall)
    return top.fighter_id == fighter.fighter_id


def _is_contender_caliber(fighter: Fighter) -> bool:
    """Contender label, or (tier4 only) ranked in the top _NEAR_CONTENDER_RANK_MAX."""
    if CONTENDER in fighter.labels:
        return True
    if fighter.tier != "tier4":
        return False
    for e in get_rankings(fighter.weight_class):
        if e.fighter.fighter_id == fighter.fighter_id:
            return e.rank <= _NEAR_CONTENDER_RANK_MAX
    return False


def _division_congestion(
    weight_class: str,
    tier_key: str,
    pools: dict[str, dict[str, list[Fighter]]],
) -> float:
    """Higher = more congested / blocked path to a title in this division+tier."""
    pool = pools.get(weight_class, {}).get(tier_key, [])
    if tier_key == "tier4":
        depth = len(get_rankings(weight_class))
        champ_id = get_champion_id(weight_class, tier_key)
        champ_defenses = 0
        if champ_id:
            champ = next((f for f in pool if f.fighter_id == champ_id), None)
            if champ is not None:
                champ_defenses = max(0, _title_reign_wins(champ) - 1)
        return depth + champ_defenses * _CONGESTION_CHAMP_DEFENSE_WEIGHT
    return len(pool) * _CONGESTION_POOL_SIZE_WEIGHT


def _opportunity_score(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
    target_idx: int,
) -> float:
    """Positive = the division at target_idx looks more open than fighter's own."""
    home = _division_congestion(fighter.weight_class, fighter.tier, pools)
    adjacent = _division_congestion(WEIGHT_CLASSES[target_idx], fighter.tier, pools)
    return home - adjacent


# ── Driver evaluators ────────────────────────────────────────────────────────
# Each returns (reason, direction, strength) if its gate conditions are met, else None.
# `strength` feeds _move_probability — larger = more strongly the conditions are met.

def _driver1_title_ambition(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> tuple[str, str, float] | None:
    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    if own_idx >= len(WEIGHT_CLASSES) - 1:
        return None  # already the top division — nowhere to go up

    reign_wins = _title_reign_wins(fighter)
    is_champion = CHAMPION in fighter.labels
    if not is_champion and reign_wins == 0:
        return None  # no current or recent title pedigree

    defenses = max(0, reign_wins - 1)
    if defenses < _TITLE_AMBITION_MIN_DEFENSES:
        return None

    physically_ready = (
        fighter.cut_severity <= _CUT_LOW_THRESHOLD
        or fighter.overall >= _TITLE_AMBITION_MIN_OVERALL
    )
    if not physically_ready:
        return None

    if not is_champion:
        pool = pools.get(fighter.weight_class, {}).get(fighter.tier, [])
        if _is_likely_next_challenger(fighter, pool, fighter.weight_class):
            return None  # about to get their own division's title shot — don't leave

    strength = (
        (defenses - _TITLE_AMBITION_MIN_DEFENSES)
        + max(0.0, fighter.overall - _TITLE_AMBITION_MIN_OVERALL) * 0.1
    )
    return ("title_ambition", "up", strength)


def _driver2_struggling(fighter: Fighter) -> tuple[str, str, float] | None:
    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    if own_idx <= 0:
        return None  # already the lightest division — nowhere to go down
    if WASHED in fighter.labels:
        return None  # Washed is a cut/retire case, not a weight-class fix
    if fighter.cut_severity > _CUT_LOW_THRESHOLD:
        return None  # a hard cut would only get worse moving down

    recent = fighter.fight_history[-_STRUGGLE_WINDOW:]
    if len(recent) < _STRUGGLE_WINDOW:
        return None
    win_rate = sum(1 for r in recent if r.outcome == "win") / len(recent)
    if win_rate > _STRUGGLE_MAX_WIN_RATE:
        return None

    strength = _STRUGGLE_MAX_WIN_RATE - win_rate
    return ("struggling", "down", strength)


def _driver3_cut_damage(fighter: Fighter) -> tuple[str, str, float] | None:
    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    if own_idx >= len(WEIGHT_CLASSES) - 1:
        return None  # already the top division

    if fighter.cut_severity < _CUT_HIGH_THRESHOLD:
        return None

    late_rate = _late_finish_loss_rate(fighter, _CUT_DAMAGE_WINDOW)
    if late_rate < _CUT_DAMAGE_LATE_FINISH_RATE:
        return None

    age_qualifies = fighter.age > _PRIME_END or fighter.cut_severity >= _CUT_VERY_HIGH_THRESHOLD
    if not age_qualifies:
        return None

    age_scale = _age_cut_scale(fighter.age)
    strength = (fighter.cut_severity - _CUT_HIGH_THRESHOLD) * age_scale * 0.1 + late_rate
    return ("cut_damage", "up", strength)


def _driver4_opportunity(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> tuple[str, str, float] | None:
    if not _is_contender_caliber(fighter):
        return None

    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    best_direction: str | None = None
    best_score = _OPPORTUNITY_MIN_SCORE

    if own_idx < len(WEIGHT_CLASSES) - 1:
        score = _opportunity_score(fighter, pools, own_idx + 1)
        if score > best_score:
            best_direction, best_score = "up", score

    if own_idx > 0 and fighter.cut_severity <= _CUT_LOW_THRESHOLD:
        score = _opportunity_score(fighter, pools, own_idx - 1)
        if score > best_score:
            best_direction, best_score = "down", score

    if best_direction is None:
        return None
    return ("opportunity", best_direction, best_score)


def _movement_candidate(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> tuple[str, str, float] | None:
    """Check the four drivers in order; the first whose gate conditions are met wins."""
    result = _driver1_title_ambition(fighter, pools)
    if result is not None:
        return result
    result = _driver2_struggling(fighter)
    if result is not None:
        return result
    result = _driver3_cut_damage(fighter)
    if result is not None:
        return result
    return _driver4_opportunity(fighter, pools)


def _move_probability(reason: str, strength: float) -> float:
    """Probability the flagged move actually fires this cycle, scaling with strength."""
    if reason == "title_ambition":
        return min(_TITLE_AMBITION_PROB_CAP, _TITLE_AMBITION_BASE_PROB + strength * _TITLE_AMBITION_STRENGTH_SCALE)
    if reason == "struggling":
        return min(_STRUGGLE_PROB_CAP, _STRUGGLE_BASE_PROB + strength * _STRUGGLE_STRENGTH_SCALE)
    if reason == "cut_damage":
        return min(_CUT_DAMAGE_PROB_CAP, _CUT_DAMAGE_BASE_PROB + strength * _CUT_DAMAGE_STRENGTH_SCALE)
    if reason == "opportunity":
        return min(_OPPORTUNITY_PROB_CAP, _OPPORTUNITY_BASE_PROB + strength * _OPPORTUNITY_STRENGTH_SCALE)
    return 0.0  # pragma: no cover — reason always one of the four above


def _maybe_class_skip(
    fighter: Fighter,
    reason: str,
    direction: str,
    pools: dict[str, dict[str, list[Fighter]]],
) -> bool:
    """
    Roll for a rare two-division skip. Only title_ambition/opportunity drivers,
    only "up" direction — see module docstring for why skip is upward-only.
    """
    if reason not in ("title_ambition", "opportunity") or direction != "up":
        return False

    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    if own_idx >= len(WEIGHT_CLASSES) - 2:
        return False  # no room for a two-division jump

    pool = pools.get(fighter.weight_class, {}).get(fighter.tier, [])
    if len(pool) < _CLASS_SKIP_MIN_POOL:
        return False

    overalls = sorted(f.overall for f in pool)
    cutoff = overalls[min(int(len(overalls) * _CLASS_SKIP_PERCENTILE), len(overalls) - 1)]
    if fighter.overall < cutoff or fighter.hype < _CLASS_SKIP_HYPE_THRESHOLD:
        return False

    return random.random() < _CLASS_SKIP_PROB


def _target_weight_class(fighter: Fighter, direction: str, skip: bool) -> str | None:
    own_idx = WEIGHT_CLASSES.index(fighter.weight_class)
    step = 2 if skip else 1
    target_idx = own_idx + step if direction == "up" else own_idx - step
    if 0 <= target_idx < len(WEIGHT_CLASSES):
        return WEIGHT_CLASSES[target_idx]
    return None


def _clear_flags(fighter: Fighter) -> None:
    fighter.weight_class_move_candidate = False
    fighter.weight_class_move_direction = None
    fighter.weight_class_move_reason    = None
    fighter.weight_class_move_target    = None


# ── Public API ───────────────────────────────────────────────────────────────

def evaluate_weight_class_move(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> bool:
    """
    Fully recompute (not sticky) fighter's weight_class_move_* flags.

    Returns True if the fighter ends this call flagged as a candidate, False
    otherwise (either no driver's gate conditions were met, or the driver's
    probabilistic roll didn't fire). Never executes a move — only sets flags.
    """
    candidate = _movement_candidate(fighter, pools)
    if candidate is None:
        _clear_flags(fighter)
        return False

    reason, direction, strength = candidate
    if random.random() >= _move_probability(reason, strength):
        _clear_flags(fighter)
        return False

    skip = _maybe_class_skip(fighter, reason, direction, pools)
    target = _target_weight_class(fighter, direction, skip)
    if target is None:
        _clear_flags(fighter)  # defensive — driver-level bounds checks should prevent this
        return False

    fighter.weight_class_move_candidate = True
    fighter.weight_class_move_direction = direction
    fighter.weight_class_move_reason    = reason
    fighter.weight_class_move_target    = target
    return True


def maybe_evaluate_weight_move(
    fighter: Fighter,
    pools: dict[str, dict[str, list[Fighter]]],
) -> bool:
    """
    Fire every LABEL_UPDATE_INTERVAL fights — same trigger as maybe_update_labels()
    / maybe_evaluate_cut() / maybe_evaluate_retirement(). Cheap no-op otherwise.
    """
    n = len(fighter.fight_history)
    if n == 0 or n % LABEL_UPDATE_INTERVAL != 0:
        return False
    return evaluate_weight_class_move(fighter, pools)


# ── Demo (run as __main__) ───────────────────────────────────────────────────
# Self-contained headless sim loop (same shape as sim.py's run(), minus rich
# console output) so movement candidates can be observed against a realistic
# population with real champions, rankings, and fight history — without
# depending on / modifying sim.py itself.

if __name__ == "__main__":
    import random as _random

    from career.tiers import generate_all_tiers, TIER_LEVELS
    from engine.fight import simulate_fight
    from matchmaking import pick_opponent, apply_tier_transitions
    from career.labels import maybe_update_labels, reset_title_registry, update_labels
    from title import reset_title_scheduling, maybe_run_title_fight
    from career.age import advance_all_ages, reset_age_advancement
    from career.development import advance_all_development, apply_win_development_boost, reset_development_advancement
    from career.cuts import maybe_evaluate_cut, reset_cut_registry
    from career.retirement import maybe_evaluate_retirement, reset_retirement_scanning
    from career.rankings import update_rankings, reset_rankings, RANKINGS_UPDATE_INTERVAL
    from sim_calendar import reset_sim_clock, advance_sim_clock, get_sim_day
    from title import TITLE_FIGHT_INTERVAL

    _random.seed(23)
    N_FIGHTS = 4000

    pools = generate_all_tiers(scale=1.0)
    reset_title_registry()
    reset_title_scheduling()
    reset_cut_registry()
    reset_rankings()
    reset_sim_clock()
    reset_age_advancement()
    reset_development_advancement()
    reset_retirement_scanning()

    all_fighters = [f for wc in pools.values() for tp in wc.values() for f in tp]

    for i in range(N_FIGHTS):
        if not all_fighters:
            break
        a = _random.choice(all_fighters)
        try:
            b = pick_opponent(a, pools)
        except IndexError:
            continue

        fight_wc, fight_tier = a.weight_class, a.tier
        winner, loser = simulate_fight(a, b, org="league", sim_day=get_sim_day())
        apply_win_development_boost(winner)

        to_remove = []
        for f in (winner, loser):
            apply_tier_transitions(f, pools)
            maybe_update_labels(f)
            removed = maybe_evaluate_retirement(f, pools, fight_num=i + 1)
            if not removed:
                removed = maybe_evaluate_cut(f, pools, fight_num=i + 1)
            if removed:
                to_remove.append(f)
            else:
                maybe_evaluate_weight_move(f, pools)
        for rf in to_remove:
            all_fighters[:] = [f for f in all_fighters if f is not rf]

        maybe_run_title_fight(fight_wc, fight_tier, pools, org="league", fight_num=i + 1, all_fighters=all_fighters)

        advance_sim_clock()
        advance_all_ages(all_fighters)
        advance_all_development(all_fighters)

        if (i + 1) % RANKINGS_UPDATE_INTERVAL == 0:
            update_rankings(pools)

    for f in all_fighters:
        update_labels(f)
    update_rankings(pools)

    candidates = [f for f in all_fighters if f.weight_class_move_candidate]

    print(f"\nWeight-class movement candidates after {N_FIGHTS} fights "
          f"({len(all_fighters)} active fighters, {len(candidates)} flagged)\n")
    print(f"  {'Fighter':<26} {'WC':<12} {'Tier':<7} {'->':<2} {'Target':<12} "
          f"{'Reason':<15} {'Rec':>6}  {'Ovr':>6}  {'CutSev':>7}  {'Labels'}")
    print("  " + "-" * 118)

    for f in sorted(candidates, key=lambda f: f.weight_class_move_reason or ""):
        print(
            f"  {f.name:<26} {f.weight_class:<12} {f.tier:<7} {'->':<2} "
            f"{f.weight_class_move_target or '-':<12} {f.weight_class_move_reason:<15} "
            f"{f.record_str:>6}  {f.overall:>+6.1f}  {f.cut_severity:>+7.1f}  "
            f"{' '.join(sorted(f.labels)) or '-'}"
        )

    if not candidates:
        print("  (none flagged this run — try a larger N_FIGHTS or different seed)")

    from collections import Counter
    reason_counts = Counter(f.weight_class_move_reason for f in candidates)
    print(f"\n  Reason breakdown: {dict(reason_counts)}")
    skip_count = sum(
        1 for f in candidates
        if f.weight_class_move_target is not None
        and abs(WEIGHT_CLASSES.index(f.weight_class_move_target) - WEIGHT_CLASSES.index(f.weight_class)) == 2
    )
    print(f"  Class skips: {skip_count} of {len(candidates)} flagged moves\n")
