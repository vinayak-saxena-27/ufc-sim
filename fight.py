from __future__ import annotations

import random

from fighter import Fighter, FightResult
from age import apply_age_to_fighter
from development import apply_development_to_fighter
from weight_cut import apply_cut_to_fighter

# ─── Tuning constants ─────────────────────────────────────────────────────────
# Adjust these after reading smoke_test.py output.
# SCALE:     Checked against the real-world career anchor in smoke_test.py — treat as a
#            real constant, not a placeholder. Retune if playtesting shows it's off, but
#            re-derive against the anchor rather than picking a new number by feel.
# NOISE_STD: per-fighter Gaussian jitter on effective overall before computing the diff.
#            Keeps repeated same-matchup fights from resolving identically every time.
# NOTE: spec stated scale=20, but with tier gaps of 20 pts (T2=0, T3=+20) that
# produces ~97% win rate for the Anchor vs Tier 2 — nowhere near the 82-85% target.
# Back-solving from both targets simultaneously requires scale ~= 43. Verified by
# simulation; flag for user to review if the tier centers change.
SCALE: float = 43.0
NOISE_STD: float = 3.0


def win_probability(fighter_a: Fighter, fighter_b: Fighter) -> float:
    """
    P(fighter_a beats fighter_b), sampled with per-fight noise.

    Uses a logistic function on overall diff:
        P = 1 / (1 + 10^(-(ovr_a - ovr_b) / SCALE))

    NOTE: This entire function is a throwaway placeholder.
    The real fight engine will resolve fights through phases (striking range /
    clinch / ground) driven by the sub-attribute vectors directly — not a single
    collapsed overall. Do not invest in this function's sophistication.

    HOOK: Phase-based fight resolution plugs in here, replacing this function
    with one that accepts Fighter objects and returns a richer FightOutcome.
    """
    eff_a = fighter_a.overall + random.gauss(0.0, NOISE_STD)
    eff_b = fighter_b.overall + random.gauss(0.0, NOISE_STD)
    diff = eff_a - eff_b
    return 1.0 / (1.0 + 10.0 ** (-diff / SCALE))


def simulate_fight(
    fighter_a: Fighter,
    fighter_b: Fighter,
    org: str = "unknown",
    *,
    is_title: bool = False,
    sim_day: int = -1,
) -> tuple[Fighter, Fighter]:
    """
    Simulate one fight using the phase-based engine (4a/4b/4c).

    Records FightResult in both fighters' fight_history and returns (winner, loser).
    Method ("KO/TKO", "submission", "decision") comes from the actual fight resolution
    rather than probability weights.

    win_probability() is preserved above for smoke_test.py calibration runs; it is
    no longer called from here.
    """
    from fight_engine import simulate_full_fight

    # Apply modifier layers before entering the engine (order matters for readability
    # and matches the fight-night timeline, not for correctness — all three deltas are
    # computed from fighter.age, a raw field none of them write, so they're mutually
    # additive/order-independent; see development.py module docstring for the proof).
    #   1. Development: career-accumulated gain (young fighters, ~18-23)
    #   2. Cut:         fight-night walk-around-weight cut severity impact
    #   3. Age:         prime window = no-op; decline = negative penalty
    #                   (also amplifies the cut's effect — see weight_cut.py)
    #   4. Fatigue:     applied per round inside fight_engine.py
    fa_eff = apply_age_to_fighter(apply_cut_to_fighter(apply_development_to_fighter(fighter_a)))
    fb_eff = apply_age_to_fighter(apply_cut_to_fighter(apply_development_to_fighter(fighter_b)))

    outcome = simulate_full_fight(fa_eff, fb_eff, is_title=is_title)
    winner  = fighter_a if outcome.winner_id == fighter_a.fighter_id else fighter_b
    loser   = fighter_b if winner is fighter_a else fighter_a

    # score_margin: from each fighter's perspective (positive = their lead, negative = deficit).
    # For finishes the scores are partial-round, but stored for completeness.
    a_margin       = outcome.total_score_a - outcome.total_score_b
    rounds_n       = len(outcome.rounds)
    winner.fight_history.append(FightResult(
        opponent_name    = loser.name,
        outcome          = "win",
        method           = outcome.method,
        org              = org,
        tier             = winner.tier,
        score_margin     = a_margin if winner is fighter_a else -a_margin,
        is_title         = is_title,
        rounds_completed = rounds_n,
        sim_day          = sim_day,
    ))
    loser.fight_history.append(FightResult(
        opponent_name    = winner.name,
        outcome          = "loss",
        method           = outcome.method,
        org              = org,
        tier             = loser.tier,
        score_margin     = -a_margin if loser is fighter_b else a_margin,
        is_title         = is_title,
        rounds_completed = rounds_n,
        sim_day          = sim_day,
    ))
    return winner, loser
