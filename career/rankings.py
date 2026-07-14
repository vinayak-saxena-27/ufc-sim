"""
rankings.py — Elite-tier ranking system and matchmaking gate.

Part 1: Matchmaking gate (is_eligible_vs_ranked)
  Controls whether a fighter can be matched against a currently ranked Elite opponent.
  Meeting any one of four conditions makes the fighter eligible:

  Condition 1: >= ELITE_GATE_MIN_UNRANKED (3) Elite-tier fights in history (any outcome).
    This is the normal proving-period path: fight unranked Elite opposition first.
    First-pass estimate; tune if the proving period feels too short or long.

  Condition 2: Fighter is currently in the ranked set.
    A fighter already ranked is trivially eligible for ranked opposition.
    Also covers "returning after a gap" — if they were ranked before, they've had enough
    Elite fights to have passed the gate previously (condition 1 would also cover them).

  Condition 3: fighter.hype >= ELITE_GATE_HYPE_THRESHOLD (75.0).
    Exceptional freak-prodigy fast-track. Normal Elite fighters range ~15-60 hype
    (see templates._sample_hype); 75.0 is approximately the top 1% — a genuinely rare
    booking driven entirely by buzz rather than proven Elite record.

  Condition 4: 0 Elite fights AND >= ELITE_GATE_TIER3_WIN_THRESHOLD (4) wins in the last
    ELITE_GATE_TIER3_WINDOW (5) tier3 fights.
    Approximation for "arrived pre-ranked": every fighter that promoted naturally from tier3
    to tier4 passed the promotion check (4/5 wins, matching PROMOTE_WINS_IN_LAST in
    matchmaking.py), so this condition fires for all naturally promoted fighters and does
    NOT fire for fighters generated directly at tier4 with no fight history. This is the
    correct distinction: generated-at-Elite fighters (unknown credentials) must prove
    themselves; promoted fighters (demonstrated tier3 dominance) get fast-tracked.

  Condition 5 (Org Identity session, Part 7): fighter.org_arrived_pre_ranked.
    A GENUINELY NEW condition, not a pre-existing placeholder -- added this session.
    Set once by orgs/org_movement.py when a fighter moves to Apex FC while ranked
    (top-15) at Eastern GP or The League at the moment of the move ("arrived
    pre-ranked from a comparable promotion"). Distinct from Condition 4 (tier3
    dominance) -- this is a cross-org signal, not a cross-tier one.

Part 2: Ranking formula (compute_division_rankings)
  Elite tier only, top RANKINGS_SIZE (15) per weight class.

  Score = W_WIN_RATE * confidence(n) * recency_win_rate(fights)
        + W_QUALITY  * min(1.0, ranked_wins / QUALITY_NORM)
        + W_HYPE     * min(1.0, max(0.0, fighter.hype / HYPE_NORM))

  confidence(n) = n / (n + CONFIDENCE_K)
    Dampens the win-rate component for small samples WITHOUT dampening quality.
    At CONFIDENCE_K=6: n=1→0.14, n=4→0.40, n=10→0.63, n=15→0.71.
    This is the critical fix from the prior attempt: a 4-0 record does NOT receive
    full win-rate credit because the sample is only 4 fights, but a ranked win still
    contributes meaningfully via the un-dampened quality component.

  quality uses opponent's CURRENT ranked status as an approximation for "was ranked at
    time of fight" (FightResult stores opponent_name, not opponent_id; historical ranked
    snapshots are not stored). This is documented and accepted per the spec.

  hype is a small tiebreaker only; max contribution is W_HYPE=0.05 × 1.0 = 0.05 pts.

Calibration anchor:
  A fighter with 3 unranked Elite wins, then 1 ranked win (4 total Elite fights, all wins)
  should land approximately #13-15. The __main__ block verifies this.
"""
from __future__ import annotations

from dataclasses import dataclass

from career.fighter import Fighter, FightResult

# ── Part 1: Gate constants ────────────────────────────────────────────────────

ELITE_GATE_MIN_UNRANKED: int = 3
"""Condition 1: min Elite fights (any outcome) to pass the gate normally."""

ELITE_GATE_HYPE_THRESHOLD: float = 75.0
"""Condition 3: exceptional hype fast-track. Top ~1% of generated Elite fighters."""

ELITE_GATE_TIER3_WIN_THRESHOLD: int = 4
"""Condition 4: wins-in-last-window for tier3 dominance. Mirrors PROMOTE_WINS_IN_LAST."""

ELITE_GATE_TIER3_WINDOW: int = 5
"""Condition 4: rolling window size. Mirrors PROMOTE_WINDOW in matchmaking.py."""

# ── Part 2: Ranking constants ─────────────────────────────────────────────────

RANKINGS_SIZE: int = 15
"""Top-N fighters included in the ranked list per weight class."""

RANKINGS_UPDATE_INTERVAL: int = 25
"""Recompute all weight-class rankings every N sim fights.
Sits between the label update cadence (every 5 per fighter) and the title fight
interval (every 15 per pool). First-pass estimate."""

_CONFIDENCE_K: float = 6.0
"""confidence(n) = n / (n + K). At K=6 the curve reaches ~0.40 at n=4 and ~0.71
at n=15. Tuned against the calibration anchor; adjust if the anchor drifts."""

_DECAY: float = 0.85
"""Per-fight recency decay. Most recent fight weight=1.0; each older fight is worth
85% of the next more recent one. Differentiates fighters currently in form vs declining."""

_QUALITY_NORM: float = 5.0
"""Ranked wins required to reach quality_score=1.0 (capped there).
1 ranked win → 0.20 (fringe entry), 3 ranked wins → 0.60, 5+ → 1.0."""

_HYPE_NORM: float = 50.0
"""Hype normalizer. Typical Elite hype ~15-60; value >50 gives hype_comp >1.0 before
capping at 1.0. With W_HYPE=0.05 the max tiebreaker contribution is 0.05 pts."""

RANKINGS_MIN_WINS: int = 1
"""Minimum tier4 wins (in the fighter's current weight class) required to appear
in the ranked list at all. Without this, a fighter with n>=1 tier4 fights and
ZERO wins still passed the old `n == 0` check and could occupy a ranked slot
whenever the qualifying pool was smaller than RANKINGS_SIZE (e.g. early in a
run, or at small population scale) -- their win-rate and quality components are
both 0.0, so they contributed nothing but noise. A winless fighter has no
claim to a ranked position regardless of how thin the pool is."""

_W_WIN_RATE: float = 0.55
_W_QUALITY:  float = 0.40
_W_HYPE:     float = 0.05


# ── Module-level ranking cache ────────────────────────────────────────────────

_rankings_by_wc: dict[str, list[RankingEntry]] = {}
_ranked_ids: set[str] = set()


@dataclass
class RankingEntry:
    rank:               int
    fighter:            Fighter
    score:              float
    win_rate_component: float   # confidence(n) * recency_wr, before weight
    quality_component:  float   # min(1, ranked_wins/QUALITY_NORM), before weight
    hype_component:     float   # min(1, hype/HYPE_NORM), before weight
    n_elite_fights:     int
    n_ranked_wins:      int


# ── Internal helpers ──────────────────────────────────────────────────────────

def _confidence(n: int) -> float:
    return n / (n + _CONFIDENCE_K)


def _recency_weighted_win_rate(tier4_fights: list[FightResult]) -> float:
    """
    Weight more recent fights more heavily using exponential decay.
    Most recent fight has weight 1.0; each older fight decays by _DECAY.
    Returns the weighted fraction of wins (0.0 if no fights).
    """
    if not tier4_fights:
        return 0.0
    total_w    = 0.0
    weighted_w = 0.0
    for i, fight in enumerate(reversed(tier4_fights)):
        w = _DECAY ** i
        total_w += w
        if fight.outcome == "win":
            weighted_w += w
    return weighted_w / total_w


def _score_fighter(
    fighter: Fighter,
    ranked_ids_snapshot: set[str],
    name_to_id: dict[str, str],
) -> tuple[float, float, float, float, int, int, int]:
    """
    Compute ranking score for one fighter.
    Returns (total_score, wr_component, quality_component, hype_component,
             n_elite_fights, n_ranked_wins, n_wins).
    """
    # Only count tier4 fights from the fighter's CURRENT weight class -- a fighter
    # who moved divisions shouldn't have their old-division history count toward
    # their new-division ranking. weight_class == "" is pre-existing fight history
    # recorded before this field existed; included rather than excluded so long-
    # career fighters aren't unfairly penalized for fights that predate the field.
    #
    # Uses real_fight_history (not fight_history) -- excludes presim-backfilled
    # entries (career/tiers.py::generate_presim_history). This score has TWO
    # components that both silently break under presim contamination if left
    # unfiltered: (1) confidence(n) is inflated by fake volume, defeating the
    # exact small-sample dampening it exists for (a fighter with 0 real fights
    # would get HIGH confidence in a recency-weighted average that's ~100% fake);
    # (2) _recency_weighted_win_rate's exponential decay doesn't fall off fast
    # enough to make a large presim tail negligible -- computed directly for a
    # realistic case (2 real fights behind a 30-fight presim tail, DECAY=0.85):
    # presim still accounts for ~72% of the decay-weighted mass. Real fights
    # only cross 50% of the weight at ~5 real fights, roughly independent of
    # how large the presim tail is (geometric decay converges well before 30
    # terms). Same bug class as matchmaking.py/labels.py/cuts.py/
    # org_movement.py/weight_movement.py -- see Fighter.real_fight_history.
    tier4_fights = [
        r for r in fighter.real_fight_history
        if r.tier == "tier4" and (r.weight_class == fighter.weight_class or r.weight_class == "")
    ]
    n = len(tier4_fights)

    if n == 0:
        return (0.0, 0.0, 0.0, 0.0, 0, 0, 0)

    conf     = _confidence(n)
    rec_wr   = _recency_weighted_win_rate(tier4_fights)
    wr_comp  = conf * rec_wr

    n_wins = sum(1 for r in tier4_fights if r.outcome == "win")

    ranked_wins = sum(
        1 for r in tier4_fights
        if r.outcome == "win"
        and name_to_id.get(r.opponent_name, "") in ranked_ids_snapshot
    )
    qual_comp = min(1.0, ranked_wins / _QUALITY_NORM)

    hype_comp = min(1.0, max(0.0, fighter.hype / _HYPE_NORM))

    score = _W_WIN_RATE * wr_comp + _W_QUALITY * qual_comp + _W_HYPE * hype_comp
    return (score, wr_comp, qual_comp, hype_comp, n, ranked_wins, n_wins)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_division_rankings(
    elite_fighters: list[Fighter],
    ranked_ids_snapshot: set[str],
) -> list[RankingEntry]:
    """
    Compute top-RANKINGS_SIZE rankings for one weight class (Elite tier only).

    ranked_ids_snapshot: fighter_ids currently considered ranked, used for quality scoring.
    Quality scoring uses CURRENT ranked status as an approximation for "was ranked at
    time of fight" — a documented limitation since FightResult does not store opponent IDs.
    """
    name_to_id = {f.name: f.fighter_id for f in elite_fighters}

    scored: list[tuple[float, float, float, float, int, int, int, Fighter]] = []
    for f in elite_fighters:
        result = _score_fighter(f, ranked_ids_snapshot, name_to_id)
        scored.append((*result, f))

    scored.sort(key=lambda x: x[0], reverse=True)

    ranked: list[RankingEntry] = []
    for score, wr_c, q_c, h_c, n, rw, wins, f in scored:
        if n == 0 or wins < RANKINGS_MIN_WINS:
            continue
        # No losing career records in a ranked list (matchmaking-audit
        # session). Decided from data, not assumption: across a 50-sim-year
        # baseline run, every losing-record ranked entry (3, all in thin
        # per-org mid-major lists -- e.g. a 7-10 fighter at #1) owed its
        # losses overwhelmingly to DEPARTED opponents (cut/retired), not to
        # ranked/winning opposition: 0-1 quality losses out of 6-10. That's
        # thin-pool padding, not defensible recent form, so exclusion beats
        # loss-quality weighting. Reigning champions are unaffected -- the
        # champion pin (org_rankings/nonelite_rankings) synthesizes their
        # entry from the belt registry, not from this score path.
        if f.wins < f.losses:
            continue
        if len(ranked) >= RANKINGS_SIZE:
            break
        ranked.append(RankingEntry(
            rank               = len(ranked) + 1,
            fighter            = f,
            score              = score,
            win_rate_component = wr_c,
            quality_component  = q_c,
            hype_component     = h_c,
            n_elite_fights     = n,
            n_ranked_wins      = rw,
        ))

    return ranked


def update_rankings(pools: dict[str, dict[str, list[Fighter]]]) -> None:
    """
    Recompute rankings for all weight classes and update the module cache.
    Uses the PREVIOUS snapshot for quality scoring so bootstrapping is stable:
    on the first call ranked_ids is empty → pure win-rate ordering → establishes
    the initial ranked set → subsequent calls can credit ranked-opponent wins.
    """
    global _ranked_ids

    new_ranked_ids: set[str] = set()
    for wc, tier_pools in pools.items():
        elite = tier_pools.get("tier4", [])
        entries = compute_division_rankings(elite, _ranked_ids)
        _rankings_by_wc[wc] = entries
        for e in entries:
            new_ranked_ids.add(e.fighter.fighter_id)

    _ranked_ids = new_ranked_ids


def get_rankings(weight_class: str) -> list[RankingEntry]:
    """Return the current cached rankings for a weight class (may be empty if never updated)."""
    return _rankings_by_wc.get(weight_class, [])


def get_ranked_ids() -> set[str]:
    """Return the set of fighter_ids currently in any weight class's top-15."""
    return _ranked_ids


def is_ranked(fighter: Fighter) -> bool:
    """True if fighter is currently in any weight class's top-15. Pure accessor
    over get_ranked_ids() -- does not touch ranking computation. Used by
    career/hype.py to scale win hype by opponent quality."""
    return fighter.fighter_id in _ranked_ids


def reset_rankings() -> None:
    """Clear all cached rankings and the ranked-id set. Call at sim start."""
    _rankings_by_wc.clear()
    _ranked_ids.clear()


def drop_from_rankings_cache(fighter_id: str, weight_class: str) -> None:
    """
    Evict a fighter from one division's CACHED rankings — used when a fighter
    moves OUT of that division mid-sim (weight_transfers.py, Weight Class Flex
    Session C). Pure cache eviction: does not touch compute_division_rankings()
    or _score_fighter() in any way.

    Best-effort only: FightResult has no weight_class field, so a mover's old-
    division tier4 fight history still counts toward their score wherever they
    currently sit. They may re-enter THIS division's rankings on the next
    scheduled update_rankings() recompute if that carried-over record still
    qualifies — closing that gap would require weight-class-aware scoring,
    out of scope here.
    """
    if weight_class in _rankings_by_wc:
        remaining = [e for e in _rankings_by_wc[weight_class] if e.fighter.fighter_id != fighter_id]
        for i, e in enumerate(remaining):
            e.rank = i + 1
        _rankings_by_wc[weight_class] = remaining
    _ranked_ids.discard(fighter_id)


# ── Part 1: Gate check ────────────────────────────────────────────────────────

def is_eligible_vs_ranked(fighter: Fighter) -> bool:
    """
    Returns True if this fighter may be matched against a currently ranked Elite opponent.

    Condition 1: >= ELITE_GATE_MIN_UNRANKED Elite fights (any outcome).
    Condition 2: Fighter is currently in the ranked set (already established).
    Condition 3: Exceptional hype (>= ELITE_GATE_HYPE_THRESHOLD).
    Condition 4: 0 Elite fights AND >= ELITE_GATE_TIER3_WIN_THRESHOLD wins in last
                 ELITE_GATE_TIER3_WINDOW tier3 fights (natural-promote fast-track).

    Uses real_fight_history throughout -- this is a "proving period" gate
    (has the fighter actually demonstrated anything in the sim yet?) plus an
    explicit trailing-window recency check in Condition 4; both are exactly
    the pattern that breaks under career/tiers.py's presim backfill (a fresh
    fighter could otherwise skip the proving period on fake volume alone).
    Same bug class as _score_fighter above -- see Fighter.real_fight_history.
    """
    tier4_fights = [r for r in fighter.real_fight_history if r.tier == "tier4"]
    n_tier4 = len(tier4_fights)

    # Condition 1
    if n_tier4 >= ELITE_GATE_MIN_UNRANKED:
        return True

    # Condition 2
    if fighter.fighter_id in _ranked_ids:
        return True

    # Condition 3
    if fighter.hype >= ELITE_GATE_HYPE_THRESHOLD:
        return True

    # Condition 4
    if n_tier4 == 0:
        tier3_fights = [r for r in fighter.real_fight_history if r.tier == "tier3"]
        if len(tier3_fights) >= ELITE_GATE_TIER3_WINDOW:
            last_t3 = tier3_fights[-ELITE_GATE_TIER3_WINDOW:]
            wins_t3 = sum(1 for r in last_t3 if r.outcome == "win")
            if wins_t3 >= ELITE_GATE_TIER3_WIN_THRESHOLD:
                return True

    # Condition 5 (Org Identity session)
    if fighter.org_arrived_pre_ranked:
        return True

    return False


# ── Calibration test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from career.fighter import Fighter, FightResult

    def _make_fighter(name: str, hype: float = 30.0) -> Fighter:
        f = Fighter(
            name=name, age=28, region="test", template="american_wrestling",
            tier="tier4", weight_class="lightweight",
        )
        f.hype = hype
        return f

    def _add_fights(f: Fighter, wins_vs: list[str], losses_vs: list[str]) -> None:
        """
        Evenly interleave wins and losses in chronological order (Bresenham-style).
        This prevents front-loading bias in recency weights so recency_wr ≈ raw win rate.
        The ranked wins in wins_vs are the final entries, representing the most recent
        notable fight (what a newly gate-passing fighter would experience).
        """
        # Split into unranked wins (early fights) and ranked wins (later fights)
        unranked = wins_vs[:-1] if len(wins_vs) > 1 else []
        ranked   = wins_vs[-1:] if wins_vs else []
        # Interleave the unranked wins with losses first, then append ranked win last.
        nw, nl = len(unranked), len(losses_vs)
        total = nw + nl
        wi = li = 0
        for step in range(total):
            if wi < nw and (li >= nl or wi * total <= step * nw):
                f.fight_history.append(FightResult(
                    opponent_name=unranked[wi], outcome="win",
                    method="decision", org="test", tier="tier4"))
                wi += 1
            else:
                f.fight_history.append(FightResult(
                    opponent_name=losses_vs[li], outcome="loss",
                    method="decision", org="test", tier="tier4"))
                li += 1
        for name in ranked:
            f.fight_history.append(FightResult(
                opponent_name=name, outcome="win",
                method="decision", org="test", tier="tier4"))

    # ── Calibration scenario ────────────────────────────────────────────────────
    #
    # We use PHANTOM fighters as opposition.  Phantoms are included in all_elite
    # so their names resolve in name_to_id, but they have 0 Elite fights so they
    # never appear in the rankings.  Their IDs go into ranked_ids_snapshot to
    # represent the pre-established ranked opposition the pool fighters have faced.
    #
    # This cleanly separates "who is ranked opposition" from "who is in our test
    # division" — avoiding the prior test's flaw (closed-pool fights inflating
    # everyone's quality score to the maximum).
    #
    # Pool composition (20 fighters):
    #   3 veterans     — n=15, 11W-4L, 4 ranked wins  → expected score ≈ 0.64
    #   5 contenders   — n=12,  8W-4L, 3 ranked wins  → expected score ≈ 0.52
    #   4 mid-pack A   — n=10,  7W-3L, 1 ranked win   → expected score ≈ 0.35
    #   ANCHOR         — n= 4,  4W-0L, 1 ranked win   → expected score ≈ 0.33 → target #13
    #   5 mid-pack B   — n= 8,  5W-3L, 0 ranked wins  → expected score ≈ 0.22
    #   2 new faces    — n= 4,  2W-2L, 0 ranked wins  → expected score ≈ 0.14
    #
    # Expected ranking: 3+5+4 = 12 fighters above anchor → anchor at #13. ✓

    PHANTOMS = [_make_fighter(f"RankedOpp{i}", hype=45.0) for i in range(8)]
    ranked_ids_snapshot = {p.fighter_id for p in PHANTOMS}
    pn = [p.name for p in PHANTOMS]   # phantom names, used as ranked opponent names
    UNRANKED = "UnrankedOpp"

    pool: list[Fighter] = []

    # Group 1: Veterans
    for i in range(3):
        f = _make_fighter(f"Veteran_{i}", hype=45.0 + i)
        _add_fights(f, wins_vs=pn[:4] + [UNRANKED]*6 + [pn[0]], losses_vs=[UNRANKED]*4)
        # 4 ranked wins + 7 unranked wins + 4 losses = 15 fights, 73% win rate
        pool.append(f)

    # Group 2: Contenders
    for i in range(5):
        f = _make_fighter(f"Contender_{i}", hype=38.0 + i * 0.5)
        _add_fights(f, wins_vs=pn[:2] + [UNRANKED]*5 + [pn[2]], losses_vs=[UNRANKED]*4)
        # 3 ranked wins + 5 unranked wins + 4 losses = 12 fights, 67% win rate
        pool.append(f)

    # Group 3: Mid-pack A (above anchor due to more fights and good win rate)
    for i in range(4):
        f = _make_fighter(f"MidA_{i}", hype=34.0 + i * 0.5)
        _add_fights(f, wins_vs=[UNRANKED]*6 + [pn[4]], losses_vs=[UNRANKED]*3)
        # 1 ranked win + 6 unranked wins + 3 losses = 10 fights, 70% win rate
        pool.append(f)

    # ANCHOR: 4 fights, 4W-0L, 3 unranked + 1 ranked win (most recent = ranked)
    anchor = _make_fighter("ANCHOR", hype=32.0)
    _add_fights(anchor, wins_vs=[UNRANKED, UNRANKED, UNRANKED, pn[5]], losses_vs=[])
    pool.append(anchor)

    # Group 4: Mid-pack B (below anchor — no ranked wins, moderate record)
    for i in range(5):
        f = _make_fighter(f"MidB_{i}", hype=30.0 + i * 0.3)
        _add_fights(f, wins_vs=[UNRANKED]*5, losses_vs=[UNRANKED]*3)
        # 0 ranked wins, 5W-3L = 8 fights, 62% win rate
        pool.append(f)

    # Group 5: New faces (below anchor — few fights, below-50% record)
    for i in range(2):
        f = _make_fighter(f"NewFace_{i}", hype=28.0)
        _add_fights(f, wins_vs=[UNRANKED]*2, losses_vs=[UNRANKED]*2)
        # 0 ranked wins, 2W-2L, 50% win rate
        pool.append(f)

    # all_elite = pool + phantoms (phantoms need to be present for name→id resolution)
    all_elite = pool + PHANTOMS

    rankings = compute_division_rankings(all_elite, ranked_ids_snapshot)

    print("\n=== Calibration Test: Elite Rankings (Explicit-Record Scenario) ===")
    print()
    print(f"  Pool: {len(pool)} fighters  |  Phantom ranked opponents: {len(PHANTOMS)}")
    print(f"  Anchor: 4 fights, 4W-0L, 1 ranked win (against {pn[5]})")
    print()
    print(f"  {'#':>3}  {'Name':<18}  {'n':>4}  {'rw':>3}  {'score':>6}  "
          f"{'WR*W':>6}  {'Q*W':>6}  {'H*W':>5}")
    print("  " + "-" * 72)

    anchor_entry: RankingEntry | None = None
    for e in rankings:
        marker = " <-- ANCHOR" if e.fighter is anchor else ""
        print(f"  #{e.rank:>2}  {e.fighter.name:<18}  {e.n_elite_fights:>4}  "
              f"{e.n_ranked_wins:>3}  {e.score:>6.3f}  "
              f"{_W_WIN_RATE*e.win_rate_component:>6.3f}  "
              f"{_W_QUALITY*e.quality_component:>6.3f}  "
              f"{_W_HYPE*e.hype_component:>5.3f}{marker}")
        if e.fighter is anchor:
            anchor_entry = e

    print()
    if anchor_entry is None:
        print("  RESULT: ANCHOR not in top-15  →  FAIL (target #13-15)")
        print()
        print("  Anchor raw score components:")
        tier4 = [r for r in anchor.fight_history if r.tier == "tier4"]
        n = len(tier4)
        conf = _confidence(n)
        rwr  = _recency_weighted_win_rate(tier4)
        name_to_id = {f.name: f.fighter_id for f in all_elite}
        rw = sum(1 for r in tier4
                 if r.outcome == "win" and name_to_id.get(r.opponent_name, "") in ranked_ids_snapshot)
        print(f"    n={n}  confidence={conf:.3f}  recency_wr={rwr:.3f}  ranked_wins={rw}")
        print(f"    wr_comp={conf*rwr:.3f}  quality={min(1.0,rw/_QUALITY_NORM):.3f}  "
              f"hype_norm={min(1.0,max(0.0,anchor.hype/_HYPE_NORM)):.3f}")
        total = _W_WIN_RATE*conf*rwr + _W_QUALITY*min(1.0,rw/_QUALITY_NORM) + _W_HYPE*min(1.0,anchor.hype/_HYPE_NORM)
        print(f"    total score = {total:.3f}")
        if rankings:
            print(f"    Score needed to enter top-15: > {rankings[-1].score:.3f}")
    else:
        rank = anchor_entry.rank
        lo, hi = 13, 15
        if lo <= rank <= hi:
            status = "PASS"
        elif rank < lo:
            status = f"FAIL -- anchor at #{rank}, too high (target #{lo}-#{hi})"
        else:
            status = f"FAIL -- anchor at #{rank}, too low (target #{lo}-#{hi})"
        print(f"  RESULT: ANCHOR at #{rank}  [{status}]")

    # ── Inverse extreme: 0-fight fighter must not appear in rankings ────────────
    zero_f = _make_fighter("ZeroFight", hype=80.0)
    rankings_z = compute_division_rankings(all_elite + [zero_f], ranked_ids_snapshot)
    zero_ranked = any(e.fighter is zero_f for e in rankings_z)
    print(f"\n  Inverse extreme (0 Elite fights, hype=80.0): "
          f"{'IN rankings -- FAIL' if zero_ranked else 'not in rankings -- PASS'}")
    print()
