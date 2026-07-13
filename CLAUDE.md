# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A from-scratch MMA career simulator. It doesn't just resolve win/loss — it models per-fight phase-by-phase combat (striking/clinch/ground), then layers a full fighter lifecycle on top: aging, skill development, tier promotion/demotion, rankings, title fights, weight-class moves, retirement/cuts, dynamic hype, and multi-organization career paths (regional → mid-major → top-tier), with new fighters continuously entering the population via academies, crossovers, and lateral transfers. A FastAPI backend + vanilla-JS frontend expose it as a steppable "advance the calendar" web UI.

There is no package installed — everything is run directly from the repo root via `python <script>.py`, relying on each entry point's own `sys.path` shim.

## Commands

**Run the terminal sim** (rich console output, self-contained CLI):
```
python sim.py                    # 2000 fights, default pyramid roster (~735 fighters)
python sim.py --fights 5000      # more fights
python sim.py --fighters 1.5     # bigger roster (scale multiplier, ~1100 fighters)
python sim.py --seed 7           # different random seed
python sim.py --debug            # compact per-fight table instead of rich panels
```

**Run the API + web UI**:
```
pip install -r requirements_api.txt
python -m uvicorn api:app --reload --port 3000    # NOT port 8000 — see note below
```
Then browse `http://127.0.0.1:3000/ui/`. Port 8000 collides with an unrelated app already bound to `localhost:8000` over IPv6 on this machine; use 3000 (or any other free port) and prefer `127.0.0.1` over `localhost`.

**Run tests** — there is no pytest setup (no `pytest.ini`/`conftest.py`/`pyproject.toml`, no `pytest` imports anywhere). Every file under `tests/` is a standalone script run directly:
```
python tests/smoke_test.py
python tests/verify_4c.py
python tests/verify_title_selection.py
# etc. — any tests/verify_*.py
```
Each script does its own repo-root `sys.path.insert` so it works run from anywhere. Diagnostics print as the script runs; several (`verify_4c.py`, `verify_phase_engine.py`, etc.) additionally exit with a nonzero code on failure via `sys.exit(...)`, so `echo $LASTEXITCODE` (PowerShell) / `echo $?` after running one tells you pass/fail without reading output. `tests/test_fixtures.py` is not a test itself — it's shared helpers (`make_zero_baseline`, `make_style_fighter`) imported by the calibration/verify scripts.

There is no lint/typecheck config (no ruff/flake8/mypy config) anywhere in the repo.

## Architecture

### The two top-level entry points share one simulation core

`sim.py` is both the CLI driver *and* the module `api.py` imports for its stepping logic — there's no separate "engine service." State lives in `sim._sim_state` (a `SimpleNamespace`: `all_fighters`, `pools`, `current_day`, `initialized`, `params`, `fight_index`), a single-process/single-sim global.

- `init_sim(scale, seed, debug)` builds a fresh population (`career/tiers.py::generate_all_tiers`) and resets **every** stateful subsystem's registry (rankings caches, title registry, gate stats, replenishment schedule, league season, org movement log, etc.) — must be called before anything else.
- `step_sim(days, verbose=False, n_fights_hint=None)` is the shared stepping primitive. The sim's atomic unit of progress is one **fight attempt**, not a calendar day — `days` is converted to an attempt count via `days // SIM_DAYS_PER_FIGHT` (min 1), and each attempt runs `_run_one_bout()` once. `sim.py`'s own CLI loop (`run()`) calls `step_sim` too (with `verbose=True`), so the CLI and the API never diverge in behavior.
- `get_sim_state()` is the one sanctioned way for `api.py` to reach into `sim.py`'s internals.

`api.py` (FastAPI) is a thin wrapper with no simulation logic of its own: `POST /init` → `init_sim`, `POST /advance` (`period: "week"|"month"` → 7/30 days) → `step_sim`, `GET /state` → builds a full JSON snapshot (fighters, elite_rankings, org_rosters, titles, academy_reputations, organizations). It mounts `web/` as static files at `/ui/`, registered *after* the API routes so the catch-all static mount never shadows them.

`web/` is a vanilla-JS SPA (no build step, no framework, no `package.json`) styled as a Wikipedia-article reading experience. `app.js` fetches `/state`/`/init`/`/advance`, caches the snapshot, and renders five sections (Elite Rankings, Titles, Organizations, Academies, All Fighters) plus a fighter/org detail modal with in-modal navigation between linked fighters (clicking an opponent's name in someone's fight history navigates the modal to that opponent, via a small nav stack rather than a page hash change).

### One fight: `engine/`

`simulate_fight()` (`engine/fight.py`) is the entry point matchmaking and title code call. It layers development → cut-severity → age modifiers onto **copies** of the two `Fighter` objects (base attributes are never mutated mid-fight), then hands off to `engine/fight_engine.py::simulate_full_fight()`, which runs one `_run_round()` per round (round count/length come from `career/tiers.py::TIER_RULESET`, tier- and title-fight-dependent).

Each round is a tick-by-tick (`TICK_SECONDS=5`) phase simulation (`engine/phase_engine.py`) across three phases — STANDING / CLINCH / GROUND — with contested per-tick transitions (clinch entry/break, takedowns, scrambles) resolved via logistic contests on sub-attributes. `SCALE = 43.0` (`engine/fight.py`) is the shared logistic steepness constant used throughout the phase/fatigue system — back-solved against a real-world career anchor, not the original spec value. Fighters with high `style_flexibility` can voluntarily dampen pursuit of their dominant phase to develop their weaker one (the "style-mixing" mechanic, gated by matchup safety and tier — see `phase_engine.py`).

Per-round fatigue (`engine/fatigue.py`) degrades only defensive attributes (`chin`, `wrestling`, `clinch`), never offense. Stoppage is checked incrementally within a round (`engine/finish_check.py`) against recency-weighted `strike_pressure` (resets each round) and `sub_pressure` (persists across rounds, decayed 50%/round).

Two scoring modes: `"total_score"` (whole-fight point sum — Eastern Grand Prix) vs `"round_count"` (rounds-won, MMA-style — everyone else), selected via `orgs/org_registry.py::decision_mode_for_org()`.

### The career layer: `career/`

`career/fighter.py::Fighter` is a plain dataclass identified by a stable `fighter_id` (uuid), not name. `overall` is a simple mean of 10 zero-centered sub-attributes (`ATTR_NAMES`) — explicitly a placeholder, not a calibrated formula. `fight_history: list[FightResult]` is the source of truth most other systems read from (rankings, labels, retirement all recompute off of it rather than tracking separate running state).

**Population structure** (`career/tiers.py`): each weight class (`lightweight`/`welterweight`/`heavyweight`) has its own independent 5-tier pyramid (`tier0` Amateur → `tier4` Elite), Gaussian-sampled around per-tier `center`/`spread` with deliberate overlap so promotion/demotion boundaries aren't sharp cliffs. `generate_all_tiers()` returns `dict[weight_class, dict[tier_key, list[Fighter]]]` — this nested dict (`pools`) is the shape almost every subsystem function takes as an argument.

**Movement between tiers is deterministic** (`matchmaking.py::apply_tier_transitions`, rolling-window win/loss thresholds, tighter demotion window at tier4) **and separately probabilistic** (`career/nonelite_rankings.py`'s "scout notice" fast-track, small per-cycle probability for top-ranked fighters to jump early). Both paths funnel through the same pool-membership mutation.

**Matchmaking** (`matchmaking.py::pick_opponent`) is same-weight-class, ~88% same-tier, with tier4 additionally hard-partitioned by org and gated by an Elite-ranked-opponent eligibility check (`career/rankings.py::is_eligible_vs_ranked`) before a ranked/unranked sub-pool draw with rank-proximity weighting. A separate additive cadence (`ELITE_FIGHT_INTERVAL`) injects one extra scheduled ranked-Elite-vs-Elite fight every 5 main-loop fights, independent of normal matchmaking odds.

**Three separate rankings modules, not one**, each reusing the same scoring primitives (`_confidence`, recency-weighted win rate) rather than duplicating formula code:
- `career/rankings.py` — Elite (tier4) only, combined across orgs, also owns the matchmaking eligibility gate.
- `career/org_rankings.py` — splits the same tier4 pool into three per-org ranked lists (Apex FC / The League / Eastern GP) by calling into `rankings.py`'s division-ranking function on org-filtered subsets.
- `career/nonelite_rankings.py` — Mid-major (tier2) and Top-org (tier3) rankings, a tier-generic sibling of the tier4 scorer; also owns scout-notice promotion.

**Title fights** (`title.py::maybe_run_title_fight`) run per `(weight_class, tier_key[, org])` pool on a fixed cadence (`TITLE_FIGHT_INTERVAL`), picking challengers off the relevant rankings list (with inactivity and hype-upset override chances). The League is a hard no-op here — its championship comes from `orgs/league_season.py`'s own season/playoff bracket instead, which crowns a champion directly via `career/labels.py::award_title()`.

**Lifecycle modules** (`career/age.py`, `development.py`, `retirement.py`, `cuts.py`, `hype.py`, `weight_movement.py` + `weight_transfers.py`, `academy_reputation.py`, `labels.py`) all run off the same global sim-day clock (`sim_calendar.py`), not per-fighter fight counts, so an inactive fighter still ages, develops, and decays in hype. `weight_movement.py`/`weight_transfers.py` split decision from execution: the former only *flags* a pending move (title ambition, struggling, cut damage, opportunity), the latter is what actually transfers pool membership, vacates titles, and runs win-and-vacate campaigns.

**Population inflow** (`career/replenishment.py`, `inflow.py`, `academies.py`) keeps pools from shrinking to zero over long runs: academies periodically spawn tier0 prospects on their own exponentially-distributed schedule (faster for academies with higher `pipeline_strength`), plus lower-frequency crossover athletes and lateral org-to-org transfers, plus a hard population-floor backstop (`FLOOR_THRESHOLDS` per tier) checked quarterly that emergency-spawns into any pool that dropped too low. `career/academy_reputation.py` feeds back into this — an academy's live reputation (drifting off recent alumni performance) modulates its effective pipeline strength, separate from its static generation-time value.

### Organizations: `orgs/`

Three-tier org hierarchy, each level with its own registry in `orgs/org_registry.py`: 3 top-tier orgs (tier4: Apex FC, The League, Eastern Grand Prix — each with distinct format/scoring/prestige/culture), 8 mid-major orgs (tier2), 12 regional orgs (tier1), wired together via `primary_feeds_to`/`secondary_feeds_to` so org assignment for new fighters is feed-routed rather than uniform-random when a fighter has prior regional/mid-major history.

`orgs/league_season.py` implements The League's alternate structure (season standings → single-elim playoff) entirely separately from the `title.py` cadence-based system used by the other two top-tier orgs.

`orgs/org_movement.py::run_org_movement_sweep()` is a whole-population free-agency sweep (tier4 only, evaluated on the same cadence as label updates) modeling directional org movement: Apex poaching top talent inbound (probability scaled by Apex's ranked-depth need and a soft roster cap), rare outbound departures from Apex, and low-probability lateral moves between The League and Eastern GP.

## Known modeling caveats (worth knowing before "fixing" something that isn't a bug)

- `Fighter.overall` is a flat mean of 10 sub-attributes — deliberately unweighted, called out in the code as a placeholder, not evidence of a missing feature.
- `SCALE = 43.0` in `engine/fight.py` was back-solved against a real-world calibration anchor, not derived from the original design spec (which used 20) — don't "correct" it back without checking `smoke_test.py`'s calibration check first.
- Apex FC has a documented tendency toward talent over-concentration relative to the other two top-tier orgs over very long simulation runs, despite the soft roster cap and poaching-probability throttling in `org_movement.py`.
