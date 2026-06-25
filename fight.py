from __future__ import annotations

import random

from fighter import Fighter, FightResult

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


def _pick_method(win_prob: float) -> str:
    """Crude finish-type assignment. Dominant winners finish more often."""
    if win_prob >= 0.70:
        return random.choices(["KO/TKO", "submission", "decision"], weights=[35, 25, 40])[0]
    elif win_prob >= 0.55:
        return random.choices(["KO/TKO", "submission", "decision"], weights=[20, 15, 65])[0]
    else:
        return random.choices(["KO/TKO", "submission", "decision"], weights=[15, 10, 75])[0]


def simulate_fight(
    fighter_a: Fighter,
    fighter_b: Fighter,
    org: str = "unknown",
) -> tuple[Fighter, Fighter]:
    """
    Simulates one fight, records results in both fighters' fight_history,
    and returns (winner, loser).

    Each fighter's FightResult is tagged with their OWN tier at the time of
    the fight — so tier-split record queries and the promotion/demotion window
    check stay consistent even across cross-tier (reach) fights.

    HOOK: Real fight resolution (phase engine) replaces the win_probability call.
    """
    p = win_probability(fighter_a, fighter_b)
    winner, loser = (fighter_a, fighter_b) if random.random() < p else (fighter_b, fighter_a)
    effective_p = p if winner is fighter_a else 1.0 - p
    method = _pick_method(effective_p)

    winner.fight_history.append(FightResult(
        opponent_name=loser.name,
        outcome="win",
        method=method,
        org=org,
        tier=winner.tier,
    ))
    loser.fight_history.append(FightResult(
        opponent_name=winner.name,
        outcome="loss",
        method=method,
        org=org,
        tier=loser.tier,
    ))
    return winner, loser
