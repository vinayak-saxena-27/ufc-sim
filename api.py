"""
api.py — FastAPI stepping + state server for the MMA career sim.

Thin wrapper over sim.py's init_sim()/step_sim()/get_sim_state(). This module
owns HTTP concerns and serialization only -- no sim mechanics live here.

Run with:  uvicorn api:app --reload --port 8000

Single-process, single-sim assumption (matches the rest of this codebase's
module-global state pattern -- see sim.py's _sim_state): one simulation at a
time, no per-client isolation, no concurrency locking. A second /init call
just resets everything and starts over, same as re-running `python sim.py`.
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sim
from career.fighter import Fighter, FightResult
from career.tiers import WEIGHT_CLASSES
from career.rankings import RankingEntry, get_rankings
from career.org_rankings import get_org_rankings
from career.labels import get_champion_id
from career.academy_reputation import ACADEMY_REPUTATION
from orgs.org_registry import (
    ORG_NAMES, MIDMAJOR_ORG_NAMES, REGIONAL_ORG_NAMES, THE_LEAGUE_NAME,
    ORGS, MIDMAJOR_ORGS, REGIONAL_ORGS, Org,
)
from orgs.events import get_event_history, get_bout_history, EventRecord, BoutRecord
from orgs.league_season import get_league_history, LeagueSeasonRecord

app = FastAPI(title="UFC Sim API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PERIOD_DAYS: dict[str, int] = {"week": 7, "month": 30}

# Fights are tracked at only these four tiers' title slots -- mirrors sim.py's
# own "Current Champions" section (tier0/Amateur has no title format at all).
# tier3 (Top-org) has no org split; tier1/tier2/tier4 each have one belt per org.
_TITLE_SLOTS: list[tuple[str, str]] = (
    [("tier3", "")]
    + [("tier1", org) for org in REGIONAL_ORG_NAMES]
    + [("tier2", org) for org in MIDMAJOR_ORG_NAMES]
    + [("tier4", org) for org in ORG_NAMES]
)


# ── Request bodies ──────────────────────────────────────────────────────────────

class InitParams(BaseModel):
    scale: float = 1.0
    seed: int = 42
    debug: bool = False


class AdvanceParams(BaseModel):
    period: Literal["week", "month"]


# ── Serialization ────────────────────────────────────────────────────────────────

def _serialize_fight_result(r: FightResult) -> dict:
    return {
        "opponent_name": r.opponent_name,
        "outcome": r.outcome,
        "method": r.method,
        "org": r.org,
        "tier": r.tier,
        "is_title": r.is_title,
        "rounds_completed": r.rounds_completed,
        "sim_day": r.sim_day,
        "weight_class": r.weight_class,
    }


def _serialize_fighter(f: Fighter) -> dict:
    return {
        "fighter_id": f.fighter_id,
        "name": f.name,
        "age": f.age,
        "region": f.region,
        "template": f.template,
        "tier": f.tier,
        "weight_class": f.weight_class,
        "academy": f.academy,
        "org": f.org,
        "prospect_tier": f.prospect_tier,
        "labels": sorted(f.labels),
        "hype": f.hype,
        "style_flexibility": f.style_flexibility,
        "cut_severity": f.cut_severity,
        "overall": f.overall,
        "attributes": {
            "wrestling":   f.wrestling,
            "bjj":         f.bjj,
            "clinch":      f.clinch,
            "boxing":      f.boxing,
            "kickboxing":  f.kickboxing,
            "power":       f.power,
            "cardio":      f.cardio,
            "chin":        f.chin,
            "athleticism": f.athleticism,
            "fight_iq":    f.fight_iq,
        },
        "record": {
            "wins": f.wins,
            "losses": f.losses,
            "str": f.record_str,
        },
        # Last 20 fights only -- a full career log is a lot of payload for a
        # profile view that mainly wants "recent form".
        "fight_history": [_serialize_fight_result(r) for r in f.fight_history[-20:]],
    }


_TIER_GROUP_LABELS: dict[str, str] = {"tier4": "Top-tier", "tier2": "Mid-major", "tier1": "Regional"}


def _serialize_org(org: Org) -> dict:
    return {
        "name": org.name,
        "tier_group": _TIER_GROUP_LABELS.get(org.tier, org.tier),
        "format": org.format,
        "scoring": org.scoring,
        "prestige": org.prestige,
        "primary_feed_from": org.primary_feed_from,
        "primary_feeds_to": org.primary_feeds_to,
        "secondary_feeds_to": org.secondary_feeds_to,
    }


_EVENTS_PER_ORG_CAP = 10
"""Most recent numbered events returned per org -- the full history can run
into the hundreds over a long sim; a fight-card UI only ever wants recent
ones, and this keeps /state's payload bounded."""

_LEAGUE_SEASONS_CAP = 30


def _serialize_bout(b: BoutRecord) -> dict:
    loser_name = b.fighter_b_name if b.winner_name == b.fighter_a_name else b.fighter_a_name
    return {
        "weight_class": b.weight_class,
        "is_title": b.is_title,
        "fighter_a_name": b.fighter_a_name,
        "fighter_b_name": b.fighter_b_name,
        "winner_name": b.winner_name,
        "loser_name": loser_name,
        "method": b.method,
        "rounds_completed": b.rounds_completed,
        "sim_day": b.sim_day,
    }


def _build_events_by_org(hype_by_name: dict[str, float]) -> dict[str, list[dict]]:
    """Numbered fight-card events, most recent first, grouped by org --
    excludes The League (its own season/playoff history is exposed
    separately, see _serialize_league_season) and any event with no logged
    bouts (thin-pool empty cards, or events logged before bout-level
    tracking existed).

    Within an event, bouts are billed by drawing power (title bout first,
    then combined current hype of the two participants), not by the order
    they happened to resolve in -- BoutSlot construction order (title slots,
    then density-injection slots, then idle-weighted fill) has no relation
    to which matchup a real card would headline. hype_by_name misses
    fighters who've since retired/been cut (treated as 0, sinking them
    toward the prelims) -- an acceptable approximation for card ordering,
    not used anywhere mechanics-relevant.

    Each event also gets "is_major" (does this card carry a title fight) and
    "major_number" (this org's Nth title-bearing card, or None if not major)
    -- a real-numbering-scheme concept, kept generic here even though only
    the frontend's Apex FC display currently uses it (numbered flagship
    cards vs. named Fight Nights, like real MMA numbered-event/Fight-Night
    conventions) -- other orgs just ignore these fields and keep using
    "number". major_number is computed in chronological order (event
    history is already append-ordered) so it counts only major cards, not
    every card in between, matching how real numbered-event sequences work."""
    bouts_by_key: dict[tuple[str, int], list[dict]] = {}
    for b in get_bout_history():
        bouts_by_key.setdefault((b.org, b.event_number), []).append(_serialize_bout(b))

    def _prominence(bout: dict) -> tuple[bool, float]:
        h = hype_by_name.get(bout["fighter_a_name"], 0.0) + hype_by_name.get(bout["fighter_b_name"], 0.0)
        return (bout["is_title"], h)

    events_by_org: dict[str, list[dict]] = {}
    major_counters: dict[str, int] = {}
    for e in get_event_history():
        if e.org == THE_LEAGUE_NAME:
            continue
        bouts = bouts_by_key.get((e.org, e.number))
        if not bouts:
            continue
        bouts = sorted(bouts, key=_prominence, reverse=True)
        is_major = any(b["is_title"] for b in bouts)
        major_number = None
        if is_major:
            major_counters[e.org] = major_counters.get(e.org, 0) + 1
            major_number = major_counters[e.org]
        events_by_org.setdefault(e.org, []).append({
            "org": e.org,
            "tier_key": e.tier_key,
            "number": e.number,
            "is_major": is_major,
            "major_number": major_number,
            "scheduled_day": e.scheduled_day,
            "bouts": bouts,
        })

    for org, events in events_by_org.items():
        events.sort(key=lambda ev: ev["number"], reverse=True)
        events_by_org[org] = events[:_EVENTS_PER_ORG_CAP]
    return events_by_org


def _serialize_league_season(r: LeagueSeasonRecord) -> dict:
    return {
        "weight_class": r.weight_class,
        "season_number": r.season_number,
        "top_scorers": [{"name": n, "points": p} for n, p in r.top_scorers],
        "semifinal_results": [
            {"winner_name": w, "loser_name": l, "method": m} for w, l, m in r.semifinal_results
        ],
        "final_result": (
            {"winner_name": r.final_result[0], "loser_name": r.final_result[1], "method": r.final_result[2]}
            if r.final_result else None
        ),
        "champion_name": r.champion_name,
    }


def _serialize_ranking_entry(e: RankingEntry) -> dict:
    return {
        "rank": e.rank,
        "fighter_id": e.fighter.fighter_id,
        "fighter_name": e.fighter.name,
        "score": e.score,
        "win_rate_component": e.win_rate_component,
        "quality_component": e.quality_component,
        "hype_component": e.hype_component,
        "n_elite_fights": e.n_elite_fights,
        "n_ranked_wins": e.n_ranked_wins,
    }


def _build_snapshot() -> dict:
    state = sim.get_sim_state()
    all_fighters: list[Fighter] = state.all_fighters
    fighter_index = {f.fighter_id: f for f in all_fighters}

    elite_rankings = {
        wc: [_serialize_ranking_entry(e) for e in get_rankings(wc)]
        for wc in WEIGHT_CLASSES
    }

    org_rosters = {
        org: {
            wc: [_serialize_ranking_entry(e) for e in get_org_rankings(wc, org)]
            for wc in WEIGHT_CLASSES
        }
        for org in ORG_NAMES
    }

    titles: dict[str, dict] = {}
    for wc in WEIGHT_CLASSES:
        for tier, org in _TITLE_SLOTS:
            champ_id = get_champion_id(wc, tier, org)
            champ = fighter_index.get(champ_id) if champ_id else None
            key = f"{wc}|{tier}|{org or '-'}"
            titles[key] = {
                "weight_class": wc,
                "tier": tier,
                "org": org,
                "champion_id": champ_id,
                "champion_name": champ.name if champ else None,
            }

    organizations = {
        org.name: _serialize_org(org)
        for org in list(ORGS.values()) + list(MIDMAJOR_ORGS.values()) + list(REGIONAL_ORGS.values())
    }

    hype_by_name = {f.name: f.hype for f in all_fighters}
    events = _build_events_by_org(hype_by_name)

    league_seasons = sorted(
        (_serialize_league_season(r) for r in get_league_history()),
        key=lambda s: s["season_number"], reverse=True,
    )[:_LEAGUE_SEASONS_CAP]

    return {
        "current_day": state.current_day,
        # Display-cased identifiers, per the frontend contract; dict keys above
        # (elite_rankings/org_rosters/titles) use the RAW lowercase weight_class
        # strings that also appear on every serialized fighter, so a client can
        # index straight from fighter.weight_class without re-casing.
        "weight_classes": [wc.title() for wc in WEIGHT_CLASSES],
        "fighters": [_serialize_fighter(f) for f in all_fighters],
        "elite_rankings": elite_rankings,
        "org_rosters": org_rosters,
        "titles": titles,
        "academy_reputations": dict(ACADEMY_REPUTATION),
        "organizations": organizations,
        "events": events,
        "league_seasons": league_seasons,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────────

@app.post("/init")
def init(params: InitParams) -> dict:
    sim.init_sim(params.scale, params.seed, params.debug)
    return {"status": "ok", "current_day": sim.get_sim_state().current_day}


@app.post("/advance")
def advance(params: AdvanceParams) -> dict:
    state = sim.get_sim_state()
    if not state.initialized:
        raise HTTPException(status_code=400, detail="Simulation not initialized -- call /init first.")
    days = _PERIOD_DAYS[params.period]
    current_day = sim.step_sim(days)
    return {"current_day": current_day}


@app.get("/state")
def get_state() -> dict:
    state = sim.get_sim_state()
    if not state.initialized:
        raise HTTPException(status_code=400, detail="Simulation not initialized -- call /init first.")
    return _build_snapshot()


# ── Static frontend ──────────────────────────────────────────────────────────────
# Mounted last so it never shadows the explicit routes above (Starlette matches
# routes in registration order; a Mount only catches what nothing else claimed).
# Serves web/index.html at /ui/ -- same-origin as the API, so the frontend needs
# no CORS handling despite the permissive CORSMiddleware above.

from pathlib import Path
from fastapi.staticfiles import StaticFiles

app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="ui")
