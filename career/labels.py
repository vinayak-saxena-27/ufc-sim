"""
labels.py — Fighter label classification system.

Labels represent career archetype and current form. Most are CURRENT-FORM
and recalculated periodically (every LABEL_UPDATE_INTERVAL fights). Legend
is STICKY: once earned it is never removed by ordinary results.

Labels supported:
  Prospect    — active upward trajectory, early/mid career
  Gatekeeper  — stable-tier tenure with competitive losses
  Journeyman  — high fight volume bouncing between tiers, mixed losses
  Washed      — declining form vs demonstrably higher career peak
  Contender   — Elite-tier fighter positioned for title shot
  Champion    — direct readout of current title-holder status
  Legend      — sticky lifetime label for sustained Elite excellence

A fighter may hold multiple labels simultaneously (e.g. Legend + Gatekeeper
late in career; Washed + Journeyman). Champion and Contender are mutually
exclusive (Champion is already the top). Contender and Prospect cannot
coexist (Contender requires Elite tier; Prospect caps out below Elite).

Title tracking
--------------
Minimal registry: dict keyed by (weight_class, tier_key) -> fighter_id | None.
Updated via award_title() when a title fight is simulated.  If no title fights
have run, Champion will simply not appear (correct — no one has earned it yet).

Recalculation cadence
---------------------
LABEL_UPDATE_INTERVAL = 5 fights (matches the PROMOTE_WINDOW in matchmaking.py).
Call maybe_update_labels(fighter) after each fight; it fires a full recompute
only on the 5th, 10th, 15th... fight, keeping the per-fight overhead minimal.
"""
from __future__ import annotations

from career.fighter import Fighter
from career.tiers import TIER_LEVELS

# ── Label name constants ───────────────────────────────────────────────────────

PROSPECT   = "Prospect"
GATEKEEPER = "Gatekeeper"
JOURNEYMAN = "Journeyman"
WASHED     = "Washed"
CONTENDER  = "Contender"
CHAMPION   = "Champion"
LEGEND     = "Legend"

# ── Recalculation cadence ─────────────────────────────────────────────────────

LABEL_UPDATE_INTERVAL: int = 5   # every N fights; matches PROMOTE_WINDOW in matchmaking.py

# ── Tuning constants ──────────────────────────────────────────────────────────
# All thresholds are first-pass estimates. Tune once real career-length
# populations are observed. Tier4 (Elite) careers are short in early sims so
# Legend will be rare — that is expected and correct; do NOT lower the bar to
# force Legends to appear prematurely.

# Prospect
_PROSPECT_MIN_FIGHTS       = 3    # need some history before the label fires
_PROSPECT_MAX_TOTAL_FIGHTS = 25   # label stops applying as career lengthens
_PROSPECT_RECENT_WINS      = 0.60 # win rate in last 5 fights
_PROSPECT_MAX_TIER_LEVEL   = 3    # tier0–tier3 only (not yet Elite)

# Gatekeeper
_GATEKEEPER_MIN_TIER_FIGHTS        = 10   # substantial tenure at current tier
_GATEKEEPER_MIN_TIER_LEVEL         = 1    # tier1 (Regional) or above
_GATEKEEPER_WIN_RATE_LO            = 0.25 # not getting overwhelmed
_GATEKEEPER_WIN_RATE_HI            = 0.60 # not climbing out of the tier
_GATEKEEPER_COMPETITIVE_LOSS_RATE  = 0.50 # >= 50% of tier losses by decision

# Journeyman
_JOURNEYMAN_MIN_FIGHTS      = 15
_JOURNEYMAN_MIN_TIER_COUNT  = 3   # fought at >= 3 distinct tier levels
_JOURNEYMAN_MAX_WIN_RATE    = 0.68 # exclude dominant fighters who merely rose through tiers

# Washed
_WASHED_MIN_FIGHTS              = 10
_WASHED_RECENT_WINDOW           = 6    # "recent" = last 6 fights
_WASHED_RECENT_WIN_RATE         = 0.35 # struggling now
_WASHED_PEAK_WINDOW             = 5    # rolling window for detecting past peak
_WASHED_PEAK_WIN_RATE           = 0.55 # had a genuinely good stretch earlier
_WASHED_MIN_HIGHER_TIER_FIGHTS  = 5    # needs real tenure at the higher tier, not just a visit

# Contender
_CONTENDER_TIER       = "tier4"   # Elite only
_CONTENDER_MIN_FIGHTS = 3         # enough tier4 fights to establish themselves
_CONTENDER_WIN_RATE   = 0.60      # strong recent form against elite opposition

# Legend (sticky — documented as first-pass thresholds needing population tuning)
_LEGEND_MIN_TOTAL_FIGHTS    = 30   # career longevity gate
_LEGEND_MIN_ELITE_FIGHTS    = 10   # sustained Elite-tier tenure (tier4 fights)
_LEGEND_MIN_CAREER_WIN_RATE = 0.55 # career-long excellence, not just a peak
_LEGEND_MIN_TITLE_WINS      = 2    # title-fight wins (from is_title=True wins)
                                   # if title fights haven't been simulated yet,
                                   # this gate will prevent all Legends — acceptable
                                   # for early sim runs; lower to 0 if needed for testing


# ── Title holder registry ─────────────────────────────────────────────────────
# Keys: (weight_class, tier_key).  Value: fighter_id of current holder, or None.

_title_holders: dict[tuple[str, str], str | None] = {}

# Consecutive successful-defense counter per (weight_class, tier_key). Added for
# Weight Class Flex Session C's win-and-vacate path (8+ defenses gate — see
# weight_transfers.py). Maintained entirely inside award_title()/vacate_title();
# no other title-tracking behavior changes.
_title_defenses: dict[tuple[str, str], int] = {}


def reset_title_registry() -> None:
    """Clear all title holders and defense counts. Call at the start of a fresh simulation."""
    _title_holders.clear()
    _title_defenses.clear()


def award_title(winner: Fighter) -> None:
    """
    Record winner as the current champion at their tier+division.

    Also updates the defense counter: if winner already held this exact belt,
    this is a successful defense (increment); otherwise it's a new reign
    (different winner, or the belt was vacant) and the counter resets to 0.
    """
    key = (winner.weight_class, winner.tier)
    if _title_holders.get(key) == winner.fighter_id:
        _title_defenses[key] = _title_defenses.get(key, 0) + 1
    else:
        _title_defenses[key] = 0
    _title_holders[key] = winner.fighter_id


def vacate_title(weight_class: str, tier_key: str) -> None:
    """Vacate a title (injury, retirement, etc.). Also resets the defense counter."""
    _title_holders[(weight_class, tier_key)] = None
    _title_defenses[(weight_class, tier_key)] = 0


def get_champion_id(weight_class: str, tier_key: str) -> str | None:
    """Return the fighter_id of the current champion, or None if vacant/unset."""
    return _title_holders.get((weight_class, tier_key))


def get_title_defenses(weight_class: str, tier_key: str) -> int:
    """Return the current champion's consecutive successful-defense count
    (0 if vacant, a freshly-won reign, or unset)."""
    return _title_defenses.get((weight_class, tier_key), 0)


# ── Internal helpers ──────────────────────────────────────────────────────────

_TIER_LEVEL: dict[str, int] = {t: i for i, t in enumerate(TIER_LEVELS)}


def _recent_win_rate(fighter: Fighter, window: int) -> float:
    recent = fighter.fight_history[-window:]
    if not recent:
        return 0.0
    return sum(1 for r in recent if r.outcome == "win") / len(recent)


def _tier_fights(fighter: Fighter, tier_key: str) -> list:
    return [r for r in fighter.fight_history if r.tier == tier_key]


def _peak_win_rate(fighter: Fighter, window: int) -> float:
    """Best win rate over any consecutive `window`-fight stretch in career history."""
    history = fighter.fight_history
    if len(history) < window:
        return 0.0
    best = 0.0
    for i in range(len(history) - window + 1):
        chunk = history[i : i + window]
        wr = sum(1 for r in chunk if r.outcome == "win") / window
        if wr > best:
            best = wr
    return best


def _competitive_loss_rate(losses: list) -> float:
    """Fraction of losses that went to decision (full distance = competitive)."""
    if not losses:
        return 0.0
    return sum(1 for r in losses if r.method == "decision") / len(losses)


# ── Core label computation ────────────────────────────────────────────────────

def compute_labels(fighter: Fighter, existing_labels: set[str]) -> set[str]:
    """
    Compute the full current label set for a fighter.

    existing_labels must be passed in so Legend stickiness can be applied.
    Returns a new set — does not mutate existing_labels.
    """
    labels: set[str] = set()
    history = fighter.fight_history
    n = len(history)

    if n == 0:
        return labels

    wins   = fighter.wins
    losses = fighter.losses
    total  = wins + losses
    career_win_rate = wins / total if total > 0 else 0.0

    current_level = _TIER_LEVEL.get(fighter.tier, 0)

    # ── Champion (direct readout) ─────────────────────────────────────────────
    if get_champion_id(fighter.weight_class, fighter.tier) == fighter.fighter_id:
        labels.add(CHAMPION)

    # ── Legend (sticky lifetime label) ────────────────────────────────────────
    if LEGEND in existing_labels:
        labels.add(LEGEND)
    else:
        elite_fights = _tier_fights(fighter, "tier4")
        title_wins   = sum(1 for r in history if r.is_title and r.outcome == "win")
        if (
            total                >= _LEGEND_MIN_TOTAL_FIGHTS
            and len(elite_fights) >= _LEGEND_MIN_ELITE_FIGHTS
            and career_win_rate  >= _LEGEND_MIN_CAREER_WIN_RATE
            and title_wins       >= _LEGEND_MIN_TITLE_WINS
        ):
            labels.add(LEGEND)

    # ── Prospect ──────────────────────────────────────────────────────────────
    # Upward-trajectory fighter: recent wins, early-career, not yet Elite level.
    if (
        n >= _PROSPECT_MIN_FIGHTS
        and total <= _PROSPECT_MAX_TOTAL_FIGHTS
        and current_level <= _PROSPECT_MAX_TIER_LEVEL
        and _recent_win_rate(fighter, 5) >= _PROSPECT_RECENT_WINS
    ):
        labels.add(PROSPECT)

    # ── Gatekeeper ────────────────────────────────────────────────────────────
    # Long stable-tier tenure, competitively losing (decisions) more than half the time.
    # Explicitly excluded from Prospect (they are not climbing).
    if PROSPECT not in labels and current_level >= _GATEKEEPER_MIN_TIER_LEVEL:
        tier_history = _tier_fights(fighter, fighter.tier)
        tier_n  = len(tier_history)
        if tier_n >= _GATEKEEPER_MIN_TIER_FIGHTS:
            tier_wins   = sum(1 for r in tier_history if r.outcome == "win")
            tier_wr     = tier_wins / tier_n
            tier_losses = [r for r in tier_history if r.outcome == "loss"]
            comp_rate   = _competitive_loss_rate(tier_losses)
            if (
                _GATEKEEPER_WIN_RATE_LO <= tier_wr <= _GATEKEEPER_WIN_RATE_HI
                and comp_rate >= _GATEKEEPER_COMPETITIVE_LOSS_RATE
            ):
                labels.add(GATEKEEPER)

    # ── Journeyman ────────────────────────────────────────────────────────────
    # High fight count, spread across many tiers, no stable Gatekeeper role.
    # Can coexist with Washed but not with Gatekeeper (Gatekeeper requires
    # the stable single-tier tenure that Journeyman explicitly lacks).
    if GATEKEEPER not in labels and total >= _JOURNEYMAN_MIN_FIGHTS:
        tiers_fought = {r.tier for r in history}
        tier_levels_fought = {_TIER_LEVEL.get(t, 0) for t in tiers_fought}
        if (
            len(tier_levels_fought) >= _JOURNEYMAN_MIN_TIER_COUNT
            and career_win_rate < _JOURNEYMAN_MAX_WIN_RATE
        ):
            labels.add(JOURNEYMAN)

    # ── Washed ────────────────────────────────────────────────────────────────
    # Declining from a demonstrably higher earlier career level.
    # PRIMARY retirement/cut signal for Part 2 — detection logic is intentionally
    # conservative (requires real prior tenure at a higher tier, not just a brief visit).
    if total >= _WASHED_MIN_FIGHTS:
        # Did this fighter have substantial tenure above their current tier?
        higher_tier_fights = [
            r for r in history
            if _TIER_LEVEL.get(r.tier, 0) > current_level
        ]
        if len(higher_tier_fights) >= _WASHED_MIN_HIGHER_TIER_FIGHTS:
            recent_wr = _recent_win_rate(fighter, _WASHED_RECENT_WINDOW)
            peak_wr   = _peak_win_rate(fighter, _WASHED_PEAK_WINDOW)
            if (
                recent_wr <= _WASHED_RECENT_WIN_RATE
                and peak_wr >= _WASHED_PEAK_WIN_RATE
            ):
                labels.add(WASHED)

    # ── Contender ─────────────────────────────────────────────────────────────
    # Elite-tier fighter with strong recent form, not yet champion.
    # Relatively exclusive: only a handful per division should qualify at once.
    # The 60% win rate over the last 5 Elite fights keeps the pool small.
    if CHAMPION not in labels and fighter.tier == _CONTENDER_TIER:
        elite_fights_all = _tier_fights(fighter, "tier4")
        if len(elite_fights_all) >= _CONTENDER_MIN_FIGHTS:
            recent_elite = elite_fights_all[-5:]
            elite_recent_wr = (
                sum(1 for r in recent_elite if r.outcome == "win") / len(recent_elite)
                if recent_elite else 0.0
            )
            if elite_recent_wr >= _CONTENDER_WIN_RATE:
                labels.add(CONTENDER)

    return labels


# ── Public update API ─────────────────────────────────────────────────────────

def update_labels(fighter: Fighter) -> None:
    """Recompute all labels for fighter, preserving Legend stickiness."""
    fighter.labels = compute_labels(fighter, fighter.labels)


def maybe_update_labels(fighter: Fighter) -> None:
    """
    Update labels if the fighter has just completed a multiple of
    LABEL_UPDATE_INTERVAL total fights.  Cheap no-op otherwise.
    Call after every fight for both winner and loser.
    """
    n = len(fighter.fight_history)
    if n > 0 and n % LABEL_UPDATE_INTERVAL == 0:
        update_labels(fighter)


# ── Demo ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import random
    from career.tiers import generate_all_tiers, WEIGHT_CLASSES
    from engine.fight import simulate_fight
    from matchmaking import pick_opponent, apply_tier_transitions

    random.seed(77)
    pools = generate_all_tiers(scale=0.3)
    all_fighters = [
        f
        for wc_pools in pools.values()
        for tier_pool in wc_pools.values()
        for f in tier_pool
    ]

    for _ in range(1500):
        a = random.choice(all_fighters)
        b = pick_opponent(a, pools)
        winner, loser = simulate_fight(a, b, org="league")
        for f in (winner, loser):
            apply_tier_transitions(f, pools)
            maybe_update_labels(f)

    # Force a full label recompute on all fighters (catches those whose last
    # fight count wasn't a multiple of the interval).
    for f in all_fighters:
        update_labels(f)

    # Print a varied sample: prefer fighters with non-empty labels, across tiers.
    labelled = [f for f in all_fighters if f.labels and f.fight_history]
    labelled.sort(key=lambda f: (len(f.labels), f.wins + f.losses), reverse=True)

    print(f"\n{'Fighter':<28} {'Tier':<12} {'Rec':>6}  {'Labels'}")
    print("-" * 75)
    shown = 0
    seen_labels: set[str] = set()
    # Show the most diverse set of label combinations first, then fill to 15.
    for f in labelled:
        if shown >= 15:
            break
        label_key = frozenset(f.labels)
        if label_key not in seen_labels or shown < 8:
            seen_labels.add(label_key)
            label_str = ", ".join(sorted(f.labels))
            print(
                f"{f.name:<28} {f.tier:<12} {f.record_str:>6}  {label_str}"
            )
            shown += 1

    # Also show fighters with zero labels (unlabelled mid-career fighter).
    unlabelled = [f for f in all_fighters if not f.labels and f.fight_history]
    if unlabelled:
        sample = unlabelled[:3]
        print("\n--- Unlabelled (mid-career, no threshold met yet) ---")
        for f in sample:
            print(f"  {f.name:<28} {f.tier:<12} {f.record_str:>6}")

    # Summary counts.
    from collections import Counter
    label_counts: Counter = Counter()
    for f in all_fighters:
        for lb in f.labels:
            label_counts[lb] += 1
    print(f"\nLabel frequency across {len(all_fighters)} fighters ({len(labelled)} have at least one):")
    for lb, cnt in sorted(label_counts.items()):
        print(f"  {lb:<12}: {cnt}")
    print()
