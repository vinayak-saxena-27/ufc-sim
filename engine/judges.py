"""
judges.py -- Independent 3-judge decision scoring (calibration session, 2026-07-15).

Produces a REPORTED decision sub-type ("unanimous" | "split" | "majority") for
fights that go the distance. This module never determines the fight's actual
winner -- that's still simulate_full_fight()'s existing total_score/round_count
logic (the SCALE=43.0 calibration anchor depends on that being untouched). It
only asks: if 3 independent human judges had scored these rounds, how much
would they have agreed with the outcome the engine already produced?

Each judge scores every round independently: the engine's own round score
differential (RoundResult.score_a - score_b) plus per-judge Gaussian noise
(subjectivity), rounded to a 10-9/10-10 style call. A judge's fight-level pick
is whichever fighter they gave more rounds to (or a draw if tied). Comparing
all 3 judges' picks against the engine's actual winner yields the sub-type.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from engine.fight_engine import RoundResult

JUDGE_COUNT: int = 3

# Per-judge subjectivity noise on a round's score differential. Retuned
# 2026-07-15 against round-score margins captured from REAL matchmaking (not
# naive same-tier random pairs) -- matchmaking's ~88% same-tier + rank-
# proximity weighting produces much closer fights than uniform sampling
# suggested (median round margin ~0.6, vs ~7-10 for naive random pairs), so
# earlier passes tuned against an unrepresentative distribution. 0.7 is the
# best fit found against the real distribution; see CALIBRATION_LOG.md.
JUDGE_NOISE_STD: float = 0.7

# A round scored within this margin (after noise) is a 10-10 draw on that
# judge's card. Retuned 2026-07-15: roughly a quarter of real rounds come out
# as an EXACT 0.0 score differential from the engine itself (a phase_output.py
# scoring characteristic, not a judges.py issue -- out of this session's
# scope), so DRAW_EPSILON has to stay small or it swallows those as majority-
# decision draws far more than real judging panels do. 0.02 was the best fit
# found -- gets majority-decision rate almost exactly on target (2.7% vs a
# 2-3% real-world benchmark) at the cost of split still running somewhat hot
# (see CALIBRATION_LOG.md's "still open" section).
DRAW_EPSILON: float = 0.02

# Cap on resampling attempts to find a judge draw consistent with the engine's
# already-determined winner (see score_decision docstring).
MAX_RESAMPLE_ATTEMPTS: int = 25


@dataclass
class JudgeCard:
    """One judge's per-round picks and resulting fight-level pick."""
    round_picks: list[str]   # "A" | "B" | "draw", one per round
    fight_pick:  str          # "A" | "B" | "draw"


def _score_one_judge(rounds: list[RoundResult]) -> JudgeCard:
    picks: list[str] = []
    wins_a = wins_b = 0
    for r in rounds:
        diff = (r.score_a - r.score_b) + random.gauss(0.0, JUDGE_NOISE_STD)
        if abs(diff) < DRAW_EPSILON:
            picks.append("draw")
        elif diff > 0:
            picks.append("A")
            wins_a += 1
        else:
            picks.append("B")
            wins_b += 1
    if wins_a > wins_b:
        fight_pick = "A"
    elif wins_b > wins_a:
        fight_pick = "B"
    else:
        fight_pick = "draw"
    return JudgeCard(round_picks=picks, fight_pick=fight_pick)


def score_decision(rounds: list[RoundResult], a_is_winner: bool) -> str:
    """
    Return "unanimous" | "split" | "majority" for a fight that went to a
    decision, given the engine's already-determined winner (a_is_winner).

    Resamples fresh per-judge noise (capped at MAX_RESAMPLE_ATTEMPTS) until at
    least 2 of 3 judges land on the engine's actual winner -- that winner is
    ground truth (from simulate_full_fight()'s total_score/round_count logic),
    so a judge panel that disagrees on the winner outright would be internally
    inconsistent with the fight that was actually simulated. Falls back to a
    forced majority (flips the single most weakly-dissenting judge onto the
    winner) if resampling never converges -- astronomically rare given
    JUDGE_NOISE_STD is calibrated well below typical round-score gaps.
    """
    winner_key = "A" if a_is_winner else "B"
    loser_key  = "B" if a_is_winner else "A"

    cards = [_score_one_judge(rounds) for _ in range(JUDGE_COUNT)]
    for _ in range(MAX_RESAMPLE_ATTEMPTS):
        agree = sum(1 for c in cards if c.fight_pick == winner_key)
        if agree >= 2:
            break
        cards = [_score_one_judge(rounds) for _ in range(JUDGE_COUNT)]
    else:
        # Forced fallback: flip the first dissenting judge onto the winner.
        for c in cards:
            if c.fight_pick != winner_key:
                c.fight_pick = winner_key
                break

    picks = [c.fight_pick for c in cards]
    if all(p == winner_key for p in picks):
        return "unanimous"
    if any(p == loser_key for p in picks):
        return "split"
    return "majority"
