"""
title.py — Title-fight scheduling for the MMA career sim.

For each (weight_class, tier_key) pair that supports titles (Regional through
Elite per TIER_RULESET — Amateur has no title fights), a title fight is
scheduled once every TITLE_FIGHT_INTERVAL regular fights registered for that
pool.

Challenger selection:
  Vacant belt  → #1 vs #2 in the current rankings for that weight class.
                 Inactivity override: walk down the ranked list if a slot's
                 default pick is relatively inactive.
                 No hype override for vacant fights (no incumbent to upset).

  Occupied belt → #1 ranked eligible contender as the default challenger.
                 Two overrides evaluated in order:
                 1. Inactivity override: walk down the ranked list (up to
                    _INACTIVITY_WALK_LIMIT positions) if the current pick is
                    relatively inactive.  If all candidates are inactive, keep #1.
                 2. Hype override: with probability _HYPE_OVERRIDE_PROB, bump a
                    ranked #2-5 contender who has >= _HYPE_OVERRIDE_MIN_DIFF more
                    hype than the current pick.  Only fires if inactivity did not.

  Thin-rankings fallback: if a weight class has too few ranked entries, falls
  back to the prior placeholder (highest overall among eligible fighters).

Champion status is stored in labels.py's _title_holders registry via
award_title(), which this module calls immediately after every title fight
resolves (before tier transitions, so the belt is registered for the tier
the fight took place at).

Scheduling counter:
  Incremented once per regular fight using fighter A's tier+weight_class.
  Pools that see more regular fights get proportionally more title fights —
  i.e. a large, active tier gets more title events than a sparse one, which
  is realistic.

Known gap — vacant title on demotion:
  If a champion is demoted out of their pool between title fights, the belt
  is implicitly treated as vacant on the next scheduled fight (the pool lookup
  finds no fighter matching the stored champion_id). Formal vacate-title
  logic is deferred to the Part 3 retirement/cut session, as that's where
  fighter exits are modelled.

Round count:
  Title fight round count comes from TIER_RULESET[tier_key].title_rounds,
  which simulate_full_fight already reads correctly.  TitleFightRecord
  stores rounds_completed from the FightResult so callers can spot-check:
    Regional (tier1) title fights   → 3 rounds max
    Mid-major / Top-org / Elite     → 5 rounds max
  A decision fight always ran the maximum rounds for that tier.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from career.fighter import Fighter
from engine.fight import simulate_fight
from career.labels import award_title, get_champion_id, maybe_update_labels
from matchmaking import apply_tier_transitions, AVOID_REMATCH_SCORE_MARGIN, title_pairing_allowed
from career.tiers import TIER_RULESET
from career.cuts import maybe_evaluate_cut
from career.retirement import maybe_evaluate_retirement
from career.rankings import get_rankings, is_eligible_vs_ranked, RankingEntry, RANKINGS_SIZE
from career.org_rankings import get_org_rankings
from career.nonelite_rankings import get_midmajor_org_rankings
from orgs.org_registry import THE_LEAGUE_NAME, MIDMAJOR_ORG_NAMES, decision_mode_for_org
from career.hype import (
    update_hype_after_fight, apply_title_hype,
    title_win_bonus, title_defense_bonus, title_loss_penalty,
)
from sim_calendar import get_sim_day, inactivity_percentile


# ── Tuning ────────────────────────────────────────────────────────────────────

TITLE_FIGHT_INTERVAL: int = 9
"""
Regular fights per (weight_class, tier_key[, org]) before a title fight is
scheduled. Lowered from 15 (2026-07-13, same session as the tier3/tier4
population rescale and the champion-excluded-from-ordinary-matchmaking fix):
those two changes compounded to make title defenses far too infrequent --
a bigger Elite pool means each (weight_class, org) pool's share of the main
loop's random fighter-A draws shrinks, so accumulating 15 pool-fights took
much longer in calendar time than when this was tuned against the old,
5x-smaller population; on top of that, champions no longer get "extra"
ordinary fights between defenses. Measured directly (seed 42, ~9000-day run):
old value of 15 produced an average 501-day (~1.4yr) gap between a champion's
title defenses, with gaps up to 882 days. At 9, the average gap is ~330 days
(~0.9yr), closer to real-world championship cadence.

Flag as a first-pass estimate.  If champion turnover feels too fast or slow,
adjust here; no other file needs to change.
"""

_MIN_POOL_SIZE: int = 2   # need at least 2 fighters to hold a title fight

_INACTIVITY_WALK_LIMIT: int = 7
"""Walk at most this many ranked positions when applying the inactivity override.
If all positions are inactive, fall back to the first (default) pick."""

_HYPE_OVERRIDE_PROB: float = 0.20
"""Probability the hype override is even considered each title defense.
At 20%: roughly 1-2 hype bumps per pool per full sim run at the default interval.
Set to 0.0 to disable."""

_HYPE_OVERRIDE_MIN_DIFF: float = 12.0
"""A #2-5 ranked contender must have at least this many more hype points than
the current default pick to qualify for the hype override.  With Elite hype
ranging ~15-60, a 12-point gap is meaningful without being extreme."""

_HYPE_CANDIDATE_RANKS: int = 5
"""Search ranked positions #2 through this (inclusive) for hype-override candidates."""

REMATCH_MIN_DEFENSES: int = 2
"""A live title-rematch exception (see AVOID_REMATCH_SCORE_MARGIN /
pending_rematch_opponent_name) only bypasses cooldown for a title DEFENSE
once the reigning champion has notched at least this many successful
defenses THIS reign (per _defense_counts). An immediate or soon rematch for
the belt only really happens for a champion who has proven themselves
against others first -- a brand-new titleholder doesn't get an instant
do-over. Checked fresh against whoever currently holds the belt each time,
so it composes correctly through title changes (a dethroned ex-champion who
later becomes a new exemption-holder starts this count over at 0 too)."""


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class TitleFightRecord:
    fight_num:        int    # which regular-fight iteration triggered this bout
    weight_class:     str
    tier_key:         str
    winner_name:      str
    loser_name:       str
    method:           str    # "decision", "KO/TKO", or "submission"
    rounds_completed: int    # actual rounds run; equals title_rounds on a decision
    was_vacant:       bool   # True → both fighters competed for a vacant belt
    override:         str | None = None   # None / "inactivity" / "hype" / "fallback"
    challenger_rank:  int | None = None   # current rank of the challenger (slot-A if vacant)


_fight_counters: dict[tuple[str, str], int]  = {}
_title_history:  list[TitleFightRecord]       = []

# fighter_id -> number of successful defenses THIS reign. Reset to 0 whenever
# that fighter wins the belt (vacant or by dethroning); read (then incremented)
# on each successful defense so the hype bonus diminishes reign-to-reign.
_defense_counts: dict[str, int] = {}


def reset_title_scheduling() -> None:
    """Clear fight counters and title history.  Call at the start of each sim."""
    _fight_counters.clear()
    _title_history.clear()
    _defense_counts.clear()


def get_title_history() -> list[TitleFightRecord]:
    """Return a snapshot of all title fights run this simulation."""
    return list(_title_history)


def fights_until_next_title_fight(weight_class: str, tier_key: str, org: str = "") -> int:
    """How many more regular fights in this (weight_class, tier_key[, org]) pool
    before a title fight is due. Used by orgs/org_movement.py (Part 6) to
    operationalize 'has a scheduled title defense within N fights' for the
    Apex-poach mid-title-run refusal case -- there's no separate per-fighter
    schedule anywhere in this codebase, just this pool-level countdown, so
    that's what "imminent title defense" means here."""
    key = (weight_class, tier_key, org if tier_key in ("tier1", "tier2", "tier4") else "")
    return TITLE_FIGHT_INTERVAL - _fight_counters.get(key, 0)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_champion(pool: list[Fighter], champion_id: str) -> Fighter | None:
    return next((f for f in pool if f.fighter_id == champion_id), None)


def _rankings_for(weight_class: str, org: str) -> list[RankingEntry]:
    """org="" (tier0/1/3): rankings.get_rankings() (unchanged -- this is
    actually only ever called for tier2/tier4 in practice since those are the
    only org-bearing tiers, but stays a safe fallback).
    org=<Apex FC|Eastern Grand Prix> (tier4): the org-specific list
    (career/org_rankings.py) — matches "a fighter appears in the rankings for
    their current org only" (Session A, Part 2). The League never reaches
    this path (see maybe_run_title_fight's early return).
    org=<one of the eight mid-major orgs> (tier2, Session B1): the mid-major
    org-specific list (career/nonelite_rankings.py)."""
    if org in MIDMAJOR_ORG_NAMES:
        return get_midmajor_org_rankings(weight_class, org)
    if org:
        return get_org_rankings(weight_class, org)
    return get_rankings(weight_class)


def _get_fighter_rank(fighter: Fighter, weight_class: str, org: str = "") -> int | None:
    """Return fighter's current rank in their weight class(+org), or None if unranked."""
    for entry in _rankings_for(weight_class, org):
        if entry.fighter.fighter_id == fighter.fighter_id:
            return entry.rank
    return None


def _walk_inactivity(
    candidates: list[RankingEntry],
    pool: list[Fighter],
) -> tuple[RankingEntry, bool]:
    """Walk candidates until a non-relatively-inactive fighter is found.

    Returns (selected_entry, override_fired) where override_fired is True
    if we skipped the first candidate (i.e. not the default #1 pick).
    If all candidates are inactive, returns the first entry with override_fired=False
    — we log this as no override (the default still applies, just unavoidably inactive).
    """
    first = candidates[0]
    for entry in candidates[:_INACTIVITY_WALK_LIMIT]:
        result = inactivity_percentile(entry.fighter, pool)
        if result is None or not result.is_relatively_inactive:
            fired = entry is not first
            return entry, fired
    return first, False  # all inactive: keep default


def _prefer_non_losing(fighters: list[Fighter]) -> list[Fighter]:
    """Filters to fighters with a non-losing career record (wins >= losses),
    if any qualify. Used by the overall-based fallback paths below so a
    below-.500 fighter never becomes a title challenger or vacant-fight
    combatant purely by raw skill stat when a non-losing alternative exists
    -- the prior behavior (pure max-overall) could hand a title to a fighter
    with a losing record whenever the ranked pool was too thin to use the
    normal rankings-driven path. Returns the original list unfiltered if
    nobody qualifies (a losing-record fighter getting the shot beats no
    fight happening at all)."""
    non_losing = [f for f in fighters if f.wins >= f.losses]
    return non_losing if non_losing else fighters


def _consume_title_exemption(fa: Fighter, fb: Fighter) -> None:
    """Clears whichever side's pending_rematch_opponent_name was just used to
    seat this pairing (see title_pairing_allowed). A no-op if neither side
    held a live exemption naming the other."""
    if fa.pending_rematch_opponent_name == fb.name:
        fa.pending_rematch_opponent_name = ""
    if fb.pending_rematch_opponent_name == fa.name:
        fb.pending_rematch_opponent_name = ""


def _pick_challenger(
    pool: list[Fighter],
    champion_id: str,
    tier_key: str,
    weight_class: str,
    org: str = "",
) -> tuple[Fighter | None, str | None]:
    """Rankings-driven challenger selection for a title defense.

    Returns (challenger, override) where override is one of:
      None         -- #1 ranked, no override fired
      "inactivity" -- inactivity override walked past #1
      "hype"       -- hype override bumped a #2-5 contender
      "fallback"   -- thin rankings, used highest-overall placeholder
    """
    eligible = [f for f in pool if f.fighter_id != champion_id]
    if not eligible:
        return None, None

    champion = next((f for f in pool if f.fighter_id == champion_id), None)
    current_day = get_sim_day()

    if tier_key == "tier4":
        gate_eligible = [f for f in eligible if is_eligible_vs_ranked(f)]
        if gate_eligible:
            eligible = gate_eligible

    eligible_ids = {f.fighter_id for f in eligible}
    rankings = _rankings_for(weight_class, org)
    ranked_eligible = [e for e in rankings if e.fighter.fighter_id in eligible_ids]

    # Prefer non-losing-record challengers even on the NORMAL rankings-driven
    # path, not just the thin-pool fallback below -- the ranking formula
    # (career/rankings.py) rewards recency-weighted form and opponent quality
    # with no floor on cumulative win/loss record, so a losing-record fighter
    # can legitimately reach the top of the ranked list. Without this, that
    # fighter would still become the #1 challenger (and could win the title)
    # via this path, since the fallback-only guard below is rarely reached
    # once rankings are populated (which is most of the time now).
    non_losing_ranked = [e for e in ranked_eligible if e.fighter.wins >= e.fighter.losses]
    if non_losing_ranked:
        ranked_eligible = non_losing_ranked

    # Opponent-avoidance (hard cap + cooldown, see matchmaking.title_pairing_
    # allowed) -- title.py never calls matchmaking.pick_opponent, so this is
    # the only place a title defense's challenger gets checked against the
    # same-pairing history the rest of the sim already enforces. Prefer the
    # avoidance-filtered list; fall through to the unfiltered one if it would
    # leave nothing ranked at all -- title fights are rare/high-stakes enough
    # that an occasional repeat beats silently skipping the defense (unlike
    # ordinary matchmaking's hard skip-the-cycle fallback in pick_opponent).
    #
    # The rematch exception only bypasses cooldown once the champion has
    # REMATCH_MIN_DEFENSES defenses this reign -- an immediate/soon rematch
    # only really happens for an established champion (see REMATCH_MIN_
    # DEFENSES), not one who just won the belt.
    if champion is not None:
        exemption_ready = _defense_counts.get(champion.fighter_id, 0) >= REMATCH_MIN_DEFENSES
        avoid_ranked = [
            e for e in ranked_eligible
            if title_pairing_allowed(champion, e.fighter, current_day, exemption_ready)
        ]
        if avoid_ranked:
            ranked_eligible = avoid_ranked

    if not ranked_eligible:
        fallback_candidates = _prefer_non_losing(eligible)
        if champion is not None:
            avoid_fallback = [
                f for f in fallback_candidates
                if title_pairing_allowed(champion, f, current_day, exemption_ready)
            ]
            if avoid_fallback:
                fallback_candidates = avoid_fallback
        challenger = max(fallback_candidates, key=lambda f: f.overall)
        if champion is not None:
            _consume_title_exemption(champion, challenger)
        return challenger, "fallback"

    # 1. Inactivity override: walk from #1 down the ranked list
    pick_entry, inactivity_fired = _walk_inactivity(ranked_eligible, pool)
    override = "inactivity" if inactivity_fired else None
    challenger = pick_entry.fighter

    # 2. Hype override: only if inactivity didn't fire; small probability bump
    if override is None and random.random() < _HYPE_OVERRIDE_PROB:
        hype_candidates = [
            e.fighter
            for e in ranked_eligible[1:_HYPE_CANDIDATE_RANKS]
            if e.fighter.hype - challenger.hype >= _HYPE_OVERRIDE_MIN_DIFF
        ]
        if hype_candidates:
            challenger = max(hype_candidates, key=lambda f: f.hype)
            override = "hype"

    if champion is not None:
        _consume_title_exemption(champion, challenger)

    return challenger, override


def _pick_vacant_pair(
    pool: list[Fighter],
    weight_class: str,
    org: str = "",
) -> tuple[tuple[Fighter, Fighter], str | None] | None:
    """Rankings-driven selection for a vacant-belt fight.

    Returns ((fa, fb), override) where override is one of:
      None         -- #1 vs #2, no override
      "inactivity" -- inactivity override on at least one slot
      "fallback"   -- thin rankings, used highest-overall placeholder
    Returns None if pool is too small to run a title fight.
    """
    if len(pool) < _MIN_POOL_SIZE:
        return None

    eligible_ids = {f.fighter_id for f in pool}
    rankings = _rankings_for(weight_class, org)
    ranked_eligible = [e for e in rankings if e.fighter.fighter_id in eligible_ids]

    # Same non-losing preference as _pick_challenger's normal path (see its
    # comment) -- a vacant-belt fight shouldn't crown a losing-record fighter
    # just because they out-scored everyone on recency/quality/hype.
    non_losing_ranked = [e for e in ranked_eligible if e.fighter.wins >= e.fighter.losses]
    if non_losing_ranked:
        ranked_eligible = non_losing_ranked

    current_day = get_sim_day()

    if len(ranked_eligible) < 2:
        candidates = _prefer_non_losing(pool)
        if len(candidates) < 2:
            candidates = pool
        sorted_candidates = sorted(candidates, key=lambda f: f.overall, reverse=True)
        fa = sorted_candidates[0]
        rest = sorted_candidates[1:]
        # Same avoidance preference as the ranked path below -- see its
        # comment. Only changes the pick when the top-2-by-overall happen to
        # be a repeat-capped/cooling-down pair; otherwise identical to before.
        avoid_rest = [f for f in rest if title_pairing_allowed(fa, f, current_day)]
        fb = avoid_rest[0] if avoid_rest else (rest[0] if rest else None)
        if fb is None:
            return None
        _consume_title_exemption(fa, fb)
        return (fa, fb), "fallback"

    # Slot A (#1): walk for inactivity
    slot_a_entry, override_a = _walk_inactivity(ranked_eligible, pool)

    # Slot B (#2, excluding slot A): walk for inactivity
    remaining = [e for e in ranked_eligible if e.fighter is not slot_a_entry.fighter]

    # Opponent-avoidance (hard cap + cooldown) -- same rationale as
    # _pick_challenger: title.py never calls matchmaking.pick_opponent, so a
    # vacant-belt pairing needs its own check against title_pairing_allowed.
    # Prefer the avoidance-filtered set; fall through to the unfiltered one
    # if it would leave nothing, rather than skip the fight over a thin pool.
    avoid_remaining = [
        e for e in remaining if title_pairing_allowed(slot_a_entry.fighter, e.fighter, current_day)
    ]
    if avoid_remaining:
        remaining = avoid_remaining

    if not remaining:
        others_pool = [f for f in pool if f is not slot_a_entry.fighter]
        avoid_others = [
            f for f in others_pool
            if title_pairing_allowed(slot_a_entry.fighter, f, current_day)
        ]
        if avoid_others:
            others_pool = avoid_others
        others = sorted(
            _prefer_non_losing(others_pool),
            key=lambda f: f.overall,
            reverse=True,
        )
        if not others:
            return None
        override = "inactivity" if override_a else None
        _consume_title_exemption(slot_a_entry.fighter, others[0])
        return (slot_a_entry.fighter, others[0]), override

    slot_b_entry, override_b = _walk_inactivity(remaining, pool)
    override = "inactivity" if (override_a or override_b) else None
    _consume_title_exemption(slot_a_entry.fighter, slot_b_entry.fighter)
    return (slot_a_entry.fighter, slot_b_entry.fighter), override


# ── Main scheduling hook ──────────────────────────────────────────────────────

def maybe_run_title_fight(
    weight_class: str,
    tier_key:     str,
    pools:        dict[str, dict[str, list[Fighter]]],
    org:          str = "league",
    fight_num:    int = 0,
    all_fighters: list[Fighter] | None = None,
    top_tier_org: str = "",
) -> bool:
    """
    Register one regular fight's worth of activity for this (weight_class, tier_key
    [, top_tier_org]). If the pool's counter reaches TITLE_FIGHT_INTERVAL, run a
    title fight and reset.

    Call once per regular fight using fighter A's weight_class and tier (captured
    before tier transitions, so the counter reflects where the fight actually
    took place). For tier1/tier2/tier4, callers must also pass top_tier_org =
    fighter A's Fighter.org (top-tier org name, one of the eight mid-major org
    names, or one of the twelve regional org names -- Sessions B1/B2) -- each
    org now runs its own independent title-fight cadence and belt. Every
    other tier omits it (default "" = the pre-existing behavior, unchanged).

    `org` (unrelated parameter, pre-existing) is just the FightResult.org tag
    ("league"/"campaign"/etc.) recorded on the simulated fight -- NOT the same
    concept as top_tier_org. Kept separate deliberately to avoid conflating the
    two "org" meanings in this codebase.

    The League is a special case: it crowns its champion via season/playoffs
    (orgs/league_season.py) rather than this continuous TITLE_FIGHT_INTERVAL
    mechanism, so this function is a no-op for it (returns False immediately).

    Returns True if a title fight was run during this call, False otherwise.
    Amateur (tier0) is silently skipped — no title format exists there.
    """
    ruleset = TIER_RULESET.get(tier_key)
    if ruleset is None or ruleset.title_rounds is None:
        return False   # tier0 or unknown tier — no titles

    if tier_key == "tier4" and top_tier_org == THE_LEAGUE_NAME:
        return False   # The League's title comes from playoffs, not this path

    reg_org = top_tier_org if tier_key in ("tier1", "tier2", "tier4") else ""
    key = (weight_class, tier_key, reg_org)
    _fight_counters[key] = _fight_counters.get(key, 0) + 1
    if _fight_counters[key] < TITLE_FIGHT_INTERVAL:
        return False

    # Title fight is due.
    _fight_counters[key] = 0
    pool = pools[weight_class][tier_key]
    if reg_org:
        pool = [f for f in pool if f.org == reg_org]

    if len(pool) < _MIN_POOL_SIZE:
        return False

    champion_id = get_champion_id(weight_class, tier_key, reg_org)
    was_vacant  = False
    override    = None
    challenger_rank: int | None = None
    prior_champion: Fighter | None = None   # set below only when an incumbent champ actually fought

    if champion_id is None:
        result = _pick_vacant_pair(pool, weight_class, reg_org)
        if result is None:
            return False
        (fa, fb), override = result
        was_vacant = True
        challenger_rank = _get_fighter_rank(fa, weight_class, reg_org)
        rank_b = _get_fighter_rank(fb, weight_class, reg_org)
        override_tag = f" [{override}]" if override else ""
        tag = f" ({reg_org})" if reg_org else ""
        print(f"[TITLE] {weight_class} {tier_key}{tag} VACANT -- "
              f"{fa.name} (rank={challenger_rank or 'NR'}) "
              f"vs {fb.name} (rank={rank_b or 'NR'}){override_tag}")
    else:
        champ = _find_champion(pool, champion_id)
        if champ is None:
            # Champion demoted — treat as vacant.
            result = _pick_vacant_pair(pool, weight_class, reg_org)
            if result is None:
                return False
            (fa, fb), override = result
            was_vacant = True
            challenger_rank = _get_fighter_rank(fa, weight_class, reg_org)
            rank_b = _get_fighter_rank(fb, weight_class, reg_org)
            override_tag = f" [{override}]" if override else ""
            tag = f" ({reg_org})" if reg_org else ""
            print(f"[TITLE] {weight_class} {tier_key}{tag} VACANT (champ gone) -- "
                  f"{fa.name} (rank={challenger_rank or 'NR'}) "
                  f"vs {fb.name} (rank={rank_b or 'NR'}){override_tag}")
        else:
            challenger, override = _pick_challenger(pool, champion_id, tier_key, weight_class, reg_org)
            if challenger is None:
                return False
            fa, fb = champ, challenger
            prior_champion = champ
            challenger_rank = _get_fighter_rank(challenger, weight_class, reg_org)
            champ_rank = _get_fighter_rank(champ, weight_class, reg_org)
            override_tag = f" [{override}]" if override else ""
            tag = f" ({reg_org})" if reg_org else ""
            print(f"[TITLE] {weight_class} {tier_key}{tag} DEFENSE -- "
                  f"[C] {champ.name} (rank={champ_rank or 'NR'}) "
                  f"vs {challenger.name} (rank={challenger_rank or 'NR'}){override_tag}")

    decision_mode = decision_mode_for_org(reg_org) if reg_org else "total_score"
    winner, loser = simulate_fight(fa, fb, org=org, is_title=True, sim_day=get_sim_day(),
                                    decision_mode=decision_mode)

    # Opponent-avoidance title-rematch exception (matchmaking.py): a close
    # ("controversial") title-fight decision earns the loser one exempted
    # shot at an immediate rematch, bypassing the normal cooldown/soft-weight
    # (never the lifetime hard cap). Checked on the just-completed fight's
    # own FightResult (loser.fight_history[-1] -- always the real fight that
    # just resolved, appended by engine/fight.py above).
    _last = loser.fight_history[-1]
    if _last.method == "decision" and abs(_last.score_margin) <= AVOID_REMATCH_SCORE_MARGIN:
        loser.pending_rematch_opponent_name = winner.name

    # Base per-fight hype update (finish/decision/adversity/style) -- title
    # fights are still fights. Title-specific bonus below is additive on top.
    update_hype_after_fight(winner, loser)
    update_hype_after_fight(loser, winner)

    # Title-specific hype event: win (vacant or dethrone) / defense / loss.
    # Defense bonus diminishes with reign length; win/loss reset the winner's count.
    if was_vacant:
        apply_title_hype(winner, title_win_bonus())
        _defense_counts[winner.fighter_id] = 0
    elif winner.fighter_id == prior_champion.fighter_id:
        n_prior_defenses = _defense_counts.get(winner.fighter_id, 0)
        apply_title_hype(winner, title_defense_bonus(n_prior_defenses))
        _defense_counts[winner.fighter_id] = n_prior_defenses + 1
    else:
        apply_title_hype(winner, title_win_bonus())
        _defense_counts[winner.fighter_id] = 0
        apply_title_hype(loser, title_loss_penalty())

    # award_title BEFORE tier transitions so the registry key matches the tier
    # the fight was contested at, not the tier the winner may be promoted into.
    award_title(winner)

    for fighter in (winner, loser):
        apply_tier_transitions(fighter, pools)
        maybe_update_labels(fighter)
        removed = maybe_evaluate_retirement(fighter, pools, fight_num)
        if not removed:
            removed = maybe_evaluate_cut(fighter, pools, fight_num)
        if removed and all_fighters is not None:
            all_fighters[:] = [f for f in all_fighters if f is not fighter]

    _title_history.append(TitleFightRecord(
        fight_num        = fight_num,
        weight_class     = weight_class,
        tier_key         = tier_key,
        winner_name      = winner.name,
        loser_name       = loser.name,
        method           = winner.fight_history[-1].method,
        rounds_completed = winner.fight_history[-1].rounds_completed,
        was_vacant       = was_vacant,
        override         = override,
        challenger_rank  = challenger_rank,
    ))
    return True
