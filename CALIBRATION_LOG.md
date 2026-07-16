# Calibration Log

Auditable history of tuning passes against the real-world UFC benchmarks in
`tests/run_calibration.py`. Parallel in spirit to how `CLAUDE.md` documents
`SCALE=43.0`'s provenance -- every constant change below is re-derivable, not
picked by feel.

**Non-negotiable constraint honored throughout**: nothing here touches
`engine/fight.py::win_probability()`/`SCALE=43.0`/`NOISE_STD`, or the
`total_score`/`round_count` winner-determination logic in
`engine/fight_engine.py::simulate_full_fight()`. `tests/smoke_test.py`'s
win-rate calibration anchor passed unchanged after every pass below (these
thresholds govern *how* a fight ends, never *who* wins).

## Session: 2026-07-15 -- initial calibration run + threshold overhaul

**New mechanics added** (see plan for full design): `round_finished`/
`decision_type`/`submission_type` fields on `FightResult`; weight-class KO/TKO
power scaling (`WEIGHT_CLASS_STRIKE_MULTIPLIER` in `engine/finish_check.py`,
submission pressure deliberately untouched); a full independent 3-judge
decision model (`engine/judges.py`); cosmetic submission-method tagging
(`SUBMISSION_TYPE_WEIGHTS`).

**Finding (before any threshold change)**: overall finish rate came back at
6.8% against a 44.5-52.6% real-world target -- confirmed via isolated
same-tier fight sampling (400 tier4-vs-tier4 lightweight fights, 0 KO/TKOs)
that this predates this session entirely: `KO_TKO_THRESHOLD=10.0`/
`SUBMISSION_THRESHOLD=23.0` were miscalibrated from the start. Flagged to the
user (this was originally out of the approved plan's scope, which had fenced
off touching these thresholds alongside `SCALE`) -- user approved expanding
scope after confirming these are independent axes.

**Threshold retune, pass 1**: swept `KO_TKO_THRESHOLD`/`SUBMISSION_THRESHOLD`
against same-tier welterweight fights (the multiplier=1.0 reference class).
Landed on `KO_TKO_THRESHOLD=2.8`, `SUBMISSION_THRESHOLD=15.0`,
`WEIGHT_CLASS_STRIKE_MULTIPLIER={heavyweight: 1.45, welterweight: 1.00,
lightweight: 0.95}`. Result: overall finish rate 40.1%, KO/TKO rate 32.5% (on
target), submission rate 7.6% (too low), heavyweight KO 36.7% (target
45-48%, multiplier spread too weak -- logistic threshold crossing compresses
proportional pressure boosts, a 1.45x pressure multiplier produced only a
~1.14x actual KO-rate ratio over welterweight).

**Pass 2**: `SUBMISSION_THRESHOLD` 15.0 -> 10.0, heavyweight multiplier 1.45 ->
2.30 (needed a much larger jump than the naive proportional guess, per the
logistic-compression note above), `DRAW_EPSILON` (judges) 0.5 -> 0.15 (0.5 was
producing 16.6% majority decisions against a 2-3% target). Result: overall
finish rate 51.0% (within tolerance), heavyweight KO 44.7% (within tolerance),
welterweight finish 50.6% (within tolerance) -- best all-around pass of the
threshold-only sweeps.

**Pass 3 (final threshold pass)**: combined `SUBMISSION_THRESHOLD=10.0` (pass
2's value) with `JUDGE_NOISE_STD` 1.5 -> 0.7. Result: lightweight KO landed at
29.4% (target 29%, near-exact), overall finish rate 51.8%. Locked in as final:
`KO_TKO_THRESHOLD=2.8`, `SUBMISSION_THRESHOLD=10.0`,
`WEIGHT_CLASS_STRIKE_MULTIPLIER={heavyweight: 2.30, welterweight: 1.00,
lightweight: 0.95}`.

**Judge-model retune (separate diagnostic track)**: initial `JUDGE_NOISE_STD`/
`DRAW_EPSILON` sweeps were tuned against *naive same-tier random pairs*
(median round-score margin ~7-10) and looked reasonable in isolation, but the
full sim showed ~35-40% judge dissent against a 24-26% target no matter how
noise was adjusted. Root cause, found by monkeypatching `judges.score_decision`
to capture real inputs from an actual `step_sim()` run: **real matchmaking**
(rank-proximity-weighted, ~88% same-tier) produces far closer fights than
naive random sampling -- median real round-score margin is ~0.6, not ~7-10,
and roughly a quarter of real rounds come back as an *exact* 0.0 score
differential (a `phase_output.py` round-scoring characteristic, out of this
session's scope to fix). Retuned against the real captured distribution:
`JUDGE_NOISE_STD=0.7`, `DRAW_EPSILON=0.02` (needs to be small or it swallows
those frequent exact-tie rounds as majority-decision draws far more often than
real judging panels do). Final result: majority-decision rate 2.5% (target
2-3%, near-exact); unanimous/split still off (65.6%/31.8% vs 77%/20%) -- see
"Still open" below.

## Final comparison snapshot (seed=42, 2196 fights, ~11 sim years)

| Metric | Sim | Target | Status |
|---|---|---|---|
| Overall finish rate | 51.4% | 44.5-52.6% | OK |
| KO/TKO rate | 35.0% | 32-33% | close, flagged |
| Submission rate | 16.4% | 19-20% | close, flagged |
| Majority decisions | 2.5% | 2-3% | OK |
| Split decisions | 31.8% | 20% | flagged, see below |
| Welterweight finish (Apex-affiliated) | 51.6% | 50-52% | OK |
| Heavyweight KO rate | 48.9% | 45-48% | close, flagged |
| Lightweight KO rate | 24.9% | 29% | flagged |

Full per-run output (all categories) reproducible via `python
tests/run_calibration.py`; raw per-fight CSV export written to the scratchpad
directory each run.

## Still open (diagnosed, deferred -- out of this session's scope)

- **Split-decision rate runs ~12pp hot** (31.8% vs 20%). Root cause: the
  underlying round-scoring engine (`engine/phase_output.py`) produces exact or
  near-exact 0.0 round-score differentials unusually often for closely-matched
  fights (the ~88%-same-tier matchmaking norm). `DRAW_EPSILON` can't fully
  compensate without either swallowing too many as majority draws (small
  epsilon fixes majority but leaves split hot) or too few (large epsilon fixes
  split but blows out majority) -- the two targets pull in opposite
  directions given the current round-score distribution. A real fix would
  need to look at why so many rounds score exactly even in
  `phase_output.py`'s scoring logic itself, not just at the judges layer.
- **Fight duration** (avg 7.8 min vs ~10.6 min target) and **round-of-finish
  skew toward Round 1** (70.3% vs real UFC's Round-1-heavy-but-less-extreme
  pattern) are downstream of the retuned thresholds favoring earlier
  finishes. Not independently tunable without another finish-rate knob;
  flagged rather than chased further given the finish-rate/frequency
  prioritization the user set upfront.
- **Fight frequency** (1.34 fights/active-fighter-year vs ~1.7-1.8 target) and
  **career length** (~2.0 years / ~3.2 fights vs ~7-13 years / ~20-35 fights
  for veterans) are both substantially off, but governed entirely by
  subsystems untouched this session (`career/retirement.py`, hype/inactivity
  decay, matchmaking cadence) -- a real fix here is a separate body of work,
  not a threshold tweak, and needs its own plan.
- **Heavyweight submission rate runs low** (7-11% vs an 18-22% target) as a
  side effect of the heavyweight strike multiplier: fights there end via
  KO/TKO earlier, on average, leaving less time for submission pressure to
  accumulate. This is a plausible real-world-*ish* coupling (bigger fighters
  do get finished by strikes before submission threats develop, and real
  heavyweight sub rate is the one division sitting at the low end of the
  "flat" band) but the sim's heavyweight sub rate undershoots even that.
