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
    ORG_NAMES, MIDMAJOR_ORG_NAMES, REGIONAL_ORG_NAMES,
    ORGS, MIDMAJOR_ORGS, REGIONAL_ORGS, Org,
)

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
