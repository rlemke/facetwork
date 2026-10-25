"""NFL — domain dashboard app: players, teams, predictions, the path to the Super Bowl.

Read paths query the ``nfl_*`` collections in ``FW_EXAMPLES_DATABASE``, which
the ``fwh_nfl`` domain's workflows write. Write paths never touch those
collections: they submit an ``nfl.workflows.*`` run, so a refresh is a
first-class run with a step graph, logs and retries.

Every prediction on these pages is shown next to the betting market's view of
the same game and the model's measured track record, because a probability
with no record behind it invites more trust than it has earned.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ...dependencies import get_current_user, get_store

router = APIRouter(prefix="/nfl")

WF_UPDATE = "nfl.workflows.Update"
WF_SIMULATE = "nfl.workflows.Simulate"
WF_BACKTEST = "nfl.workflows.ScoreModel"

ROUND_NAMES = {"WC": "Wild card", "DIV": "Divisional", "CON": "Conference", "SB": "Super Bowl"}
ROUND_ORDER = {"WC": 1, "DIV": 2, "CON": 3, "SB": 4}
POSITION_ORDER = ["QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "SPEC"]

#: Season leaders shown on the players page: (label, stat field, position groups).
LEADER_STATS = [
    ("Passing yards", "passing_yards", None),
    ("Passing TDs", "passing_tds", None),
    ("Rushing yards", "rushing_yards", None),
    ("Receiving yards", "receiving_yards", None),
    ("Receptions", "receptions", None),
    ("Sacks", "def_sacks", None),
    ("Interceptions", "def_interceptions", None),
    ("Tackles (solo)", "def_tackles_solo", None),
]

#: Career/season table columns per position group: (header, field).
STAT_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "QB": [
        ("Cmp", "completions"), ("Att", "attempts"), ("Pass yds", "passing_yards"),
        ("TD", "passing_tds"), ("Int", "passing_interceptions"), ("Sacked", "sacks_suffered"),
        ("Pass EPA", "passing_epa"), ("Rush yds", "rushing_yards"), ("Rush TD", "rushing_tds"),
    ],
    "RB": [
        ("Car", "carries"), ("Rush yds", "rushing_yards"), ("Rush TD", "rushing_tds"),
        ("Rec", "receptions"), ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds"),
        ("Rush EPA", "rushing_epa"), ("Fum lost", "fumbles_lost_total"),
    ],
    "WR": [
        ("Tgt", "targets"), ("Rec", "receptions"), ("Rec yds", "receiving_yards"),
        ("Rec TD", "receiving_tds"), ("Rec EPA", "receiving_epa"), ("Rush yds", "rushing_yards"),
    ],
    "DEF": [
        ("Solo", "def_tackles_solo"), ("Ast", "def_tackle_assists"), ("TFL", "def_tackles_for_loss"),
        ("Sacks", "def_sacks"), ("Int", "def_interceptions"), ("PD", "def_pass_defended"),
        ("FF", "def_fumbles_forced"), ("TD", "def_tds"),
    ],
    "SPEC": [
        ("FG", "fg_made"), ("FGA", "fg_att"), ("XP", "pat_made"), ("XPA", "pat_att"),
        ("PR yds", "punt_return_yards"), ("KR yds", "kickoff_return_yards"),
    ],
}  # fmt: skip
STAT_COLUMNS["TE"] = STAT_COLUMNS["WR"]
for _g in ("DL", "LB", "DB"):
    STAT_COLUMNS[_g] = STAT_COLUMNS["DEF"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _db(store):
    return store._db.client[os.environ.get("FW_EXAMPLES_DATABASE", "facetwork_examples")]


def _ctx(**kw: Any) -> dict:
    return {"active_tab": "nfl", "active_app": "NFL", **kw}


def _teams(db) -> dict[str, dict]:
    return {t["_id"]: t for t in db.nfl_teams.find()}


def _season_now(db) -> int:
    meta = db.nfl_meta.find_one({"_id": "ratings"}) or {}
    if meta.get("season"):
        return int(meta["season"])
    latest = db.nfl_games.find_one(sort=[("season", -1)])
    return int(latest["season"]) if latest else datetime.date.today().year


def _record(games: list[dict], team: str) -> dict:
    """W-L-T for ``team`` over played regular-season games."""
    w = lost = t = 0
    for g in games:
        if g.get("game_type") != "REG" or g.get("home_score") is None:
            continue
        m = g["home_score"] - g["away_score"]
        if g["away"] == team:
            m = -m
        elif g["home"] != team:
            continue
        if m > 0:
            w += 1
        elif m < 0:
            lost += 1
        else:
            t += 1
    return {"w": w, "l": lost, "t": t, "text": f"{w}-{lost}" + (f"-{t}" if t else "")}


def _game_view(g: dict) -> dict:
    """A game with the model's and the market's view, from the home side."""
    m = g.get("model") or {}
    played = g.get("home_score") is not None
    p_home = m.get("p_home")
    out = {
        "id": g["_id"],
        "season": g["season"],
        "week": g["week"],
        "game_type": g["game_type"],
        "round": ROUND_NAMES.get(g["game_type"], f"Week {g['week']}"),
        "gameday": g.get("gameday") or "",
        "gametime": g.get("gametime") or "",
        "home": g["home"],
        "away": g["away"],
        "neutral": g.get("neutral"),
        "played": played,
        "home_score": g.get("home_score"),
        "away_score": g.get("away_score"),
        "p_home": p_home,
        "spread_pred": m.get("spread_pred"),
        "spread_line": g.get("spread_line"),
        "p_home_market": m.get("p_home_market"),
        "home_qb": g.get("home_qb") or "",
        "away_qb": g.get("away_qb") or "",
    }
    if p_home is not None:
        out["pick"] = g["home"] if p_home >= 0.5 else g["away"]
        out["pick_p"] = max(p_home, 1 - p_home)
    if played and p_home is not None:
        margin = g["home_score"] - g["away_score"]
        out["pick_right"] = None if margin == 0 else (margin > 0) == (p_home >= 0.5)
    return out


def _points(series: list[float], width: int = 640, height: int = 160, pad: int = 6) -> dict:
    """Polyline points for a small server-rendered line chart."""
    if len(series) < 2:
        return {"points": "", "min": None, "max": None}
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(series) - 1)
    pts = " ".join(
        f"{pad + i * step:.1f},{pad + (height - 2 * pad) * (1 - (v - lo) / span):.1f}"
        for i, v in enumerate(series)
    )
    return {"points": pts, "min": round(lo), "max": round(hi), "width": width, "height": height}


def _season_history(db, team: str) -> list[dict]:
    """One row per season: record, points, playoff result, final rating."""
    by_season: dict[int, list[dict]] = defaultdict(list)
    for g in db.nfl_games.find(
        {"$or": [{"home": team}, {"away": team}]},
        {
            "season": 1,
            "game_type": 1,
            "home": 1,
            "away": 1,
            "home_score": 1,
            "away_score": 1,
            "home_abbr": 1,
            "away_abbr": 1,
        },
    ):
        by_season[g["season"]].append(g)
    rating = db.nfl_team_ratings.find_one({"_id": team}) or {}
    final_elo: dict[int, float] = {}
    for season, _week, _gt, elo in rating.get("history") or []:
        final_elo[season] = elo
    rows = []
    for season in sorted(by_season, reverse=True):
        games = by_season[season]
        rec = _record(games, team)
        pf = pa = 0
        furthest, won_sb = None, False
        abbr = team
        for g in games:
            if g.get("home_score") is None:
                continue
            mine_home = g["home"] == team
            abbr = g["home_abbr"] if mine_home else g["away_abbr"]
            if g["game_type"] == "REG":
                pf += g["home_score"] if mine_home else g["away_score"]
                pa += g["away_score"] if mine_home else g["home_score"]
            elif g["game_type"] in ROUND_ORDER:
                if furthest is None or ROUND_ORDER[g["game_type"]] > ROUND_ORDER[furthest]:
                    furthest = g["game_type"]
                margin = g["home_score"] - g["away_score"]
                if g["game_type"] == "SB" and ((margin > 0) == mine_home) and margin != 0:
                    won_sb = True
        if won_sb:
            playoff = "Won Super Bowl"
        elif furthest:
            playoff = f"Lost {ROUND_NAMES[furthest].lower()}" + (
                "" if furthest == "SB" else " round"
            )
        else:
            playoff = ""
        rows.append(
            {
                "season": season,
                "abbr": abbr,
                "record": rec["text"],
                "pf": pf,
                "pa": pa,
                "playoff": playoff,
                "champion": won_sb,
                "elo": final_elo.get(season),
            }
        )
    return rows


def _latest_odds(db, season: int) -> dict | None:
    return db.nfl_odds.find_one({"season": season}, sort=[("created_at", -1)])


def _live_record(db, season: int) -> dict:
    """The season's track record from FROZEN pre-game predictions."""
    n = right = 0.0
    mkt_n = mkt_right = 0.0
    brier = mkt_brier = 0.0
    for p in db.nfl_predictions.find({"season": season, "margin": {"$exists": True}}):
        y = 1.0 if p["margin"] > 0 else 0.0 if p["margin"] < 0 else 0.5
        n += 1
        right += 0.5 if y == 0.5 else float((p["p_home"] >= 0.5) == (y == 1.0))
        brier += (p["p_home"] - y) ** 2
        if p.get("p_home_market") is not None:
            mkt_n += 1
            mkt_right += 0.5 if y == 0.5 else float((p["p_home_market"] >= 0.5) == (y == 1.0))
            mkt_brier += (p["p_home_market"] - y) ** 2
    first = db.nfl_predictions.find_one(
        {"season": season}, {"predicted_at": 1}, sort=[("predicted_at", 1)]
    )
    return {
        "since": (first or {}).get("predicted_at"),
        "n": int(n),
        "accuracy": right / n if n else None,
        "brier": brier / n if n else None,
        "market_n": int(mkt_n),
        "market_accuracy": mkt_right / mkt_n if mkt_n else None,
        "market_brier": mkt_brier / mkt_n if mkt_n else None,
    }


def _find_workflow(store, qualified_name: str):
    """The newest seeded ``(flow, workflow)`` with this name, by direct query.

    Deliberately NOT the stocks app's helper, which walks every flow and every
    workflow in the store: measured here against the fleet database it took
    over 60 s and pinned a CPU, so a page that called it on every load never
    rendered. Re-seeding can leave several flows with the same workflow, and
    the newest one carries the current parameters.
    """
    db = store._db
    flow_ids = [w["flow_id"] for w in db.workflows.find({"name": qualified_name}, {"flow_id": 1})]
    if not flow_ids:
        return None, None
    newest = db.flows.find_one({"uuid": {"$in": flow_ids}}, {"uuid": 1}, sort=[("created_at", -1)])
    if not newest:
        return None, None
    flow = store.get_flow(newest["uuid"])
    wf = next(
        (w for w in store.get_workflows_by_flow(newest["uuid"]) if w.name == qualified_name), None
    )
    return flow, wf


def _submit(store, current_user, qualified_name: str, inputs: dict) -> str | None:
    from ..execution.flows import create_flow_run

    flow, wf = _find_workflow(store, qualified_name)
    if not flow or not wf:
        return None
    return create_flow_run(flow, wf, json.dumps(inputs), "none", [], store, current_user)


def _workflow_ready(store) -> bool:
    return store._db.workflows.find_one({"name": WF_UPDATE}, {"_id": 1}) is not None


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@router.get("/")
def nfl_index(request: Request, week: int = 0, store=Depends(get_store)):
    db = _db(store)
    teams = _teams(db)
    if not teams:
        return request.app.state.templates.TemplateResponse(
            request, "nfl/index.html", _ctx(empty=True, workflow_ready=_workflow_ready(store))
        )
    season = _season_now(db)
    season_games = list(
        db.nfl_games.find({"season": season}).sort([("gameday", 1), ("gametime", 1)])
    )

    # The week to show: the first with an unplayed game, unless asked for one.
    weeks = sorted({(g["week"], g["game_type"]) for g in season_games}, key=lambda x: x[0])
    upcoming = next((g["week"] for g in season_games if g.get("home_score") is None), None)
    shown_week = week or upcoming or (weeks[-1][0] if weeks else 1)
    week_games = [_game_view(g) for g in season_games if g["week"] == shown_week]

    odds = _latest_odds(db, season)
    ratings = {r["_id"]: r for r in db.nfl_team_ratings.find({}, {"history": 0})}
    standings = []
    if odds:
        for row in odds["odds"]:
            t = row["team"]
            standings.append(
                {
                    **row,
                    "record": _record(season_games, t)["text"],
                    "elo": ratings.get(t, {}).get("elo"),
                    "rank": ratings.get(t, {}).get("rank"),
                    "division_name": (teams.get(t) or {}).get("division", ""),
                }
            )
    by_conf = {
        c: sorted((r for r in standings if r["conference"] == c), key=lambda r: -r["playoffs"])
        for c in ("AFC", "NFC")
    }
    postseason = [_game_view(g) for g in season_games if g["game_type"] in ROUND_ORDER]
    meta = db.nfl_meta.find_one({"_id": "ratings"}) or {}
    backtest = db.nfl_meta.find_one({"_id": "backtest"})
    odds_lookup = {r["team"]: r for r in standings}
    return request.app.state.templates.TemplateResponse(
        request,
        "nfl/index.html",
        _ctx(
            empty=False,
            season=season,
            teams=teams,
            weeks=[w for w, _ in weeks],
            shown_week=shown_week,
            week_games=week_games,
            odds=odds,
            odds_lookup=odds_lookup,
            by_conf=by_conf,
            postseason=postseason,
            meta=meta,
            live=_live_record(db, season),
            backtest=backtest,
            workflow_ready=_workflow_ready(store),
        ),
    )


@router.get("/teams/{abbr}")
def nfl_team(request: Request, abbr: str, season: int = 0, store=Depends(get_store)):
    db = _db(store)
    teams = _teams(db)
    abbr = abbr.upper()
    if abbr not in teams:
        return RedirectResponse(url="/nfl/", status_code=303)
    season = season or _season_now(db)
    games = list(
        db.nfl_games.find({"season": season, "$or": [{"home": abbr}, {"away": abbr}]}).sort(
            [("gameday", 1), ("gametime", 1)]
        )
    )
    rating = db.nfl_team_ratings.find_one({"_id": abbr}) or {}
    history = rating.get("history") or []
    chart = _points([h[3] for h in history], width=760, height=170)
    season_marks = []
    if history:
        n = len(history)
        prev = None
        for i, h in enumerate(history):
            if h[0] != prev and h[0] % 5 == 0:
                x = 6 + i * (760 - 12) / max(n - 1, 1)
                season_marks.append({"x": round(x, 1), "label": h[0]})
            prev = h[0]

    odds = _latest_odds(db, season)
    team_odds = next((r for r in (odds or {}).get("odds", []) if r["team"] == abbr), None)

    roster_by_pos: dict[str, list[dict]] = defaultdict(list)
    for p in db.nfl_players.find(
        {"latest_team": abbr, "status": "ACT"},
        {
            "name": 1,
            "position": 1,
            "position_group": 1,
            "jersey_number": 1,
            "years_of_experience": 1,
            "college": 1,
            "rookie_season": 1,
        },
    ).sort("name", 1):
        roster_by_pos[p.get("position_group") or "Other"].append(p)
    roster = [(g, roster_by_pos[g]) for g in POSITION_ORDER if roster_by_pos.get(g)]
    roster += [(g, ps) for g, ps in roster_by_pos.items() if g not in POSITION_ORDER]

    # This season's leading contributors, by the stat their position is judged on.
    leaders = []
    for label, field, groups in (
        ("Passing yards", "passing_yards", ["QB"]),
        ("Rushing yards", "rushing_yards", None),
        ("Receiving yards", "receiving_yards", None),
        ("Sacks", "def_sacks", None),
        ("Tackles (solo)", "def_tackles_solo", None),
    ):
        q: dict[str, Any] = {
            "season": season,
            "season_type": "REG",
            "teams": abbr,
            field: {"$gt": 0},
        }
        if groups:
            q["position_group"] = {"$in": groups}
        rows = list(
            db.nfl_player_seasons.find(q, {"player_id": 1, "name": 1, "position": 1, field: 1})
            .sort(field, -1)
            .limit(3)
        )
        if rows:
            leaders.append({"label": label, "field": field, "rows": rows})

    seasons_available = sorted(
        db.nfl_games.distinct("season", {"$or": [{"home": abbr}, {"away": abbr}]}), reverse=True
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "nfl/team.html",
        _ctx(
            team=teams[abbr],
            abbr=abbr,
            teams=teams,
            season=season,
            seasons=seasons_available,
            games=[_game_view(g) for g in games],
            record=_record(games, abbr),
            rating=rating,
            chart=chart,
            season_marks=season_marks,
            team_odds=team_odds,
            odds=odds,
            roster=roster,
            leaders=leaders,
            history=_season_history(db, abbr),
        ),
    )


@router.get("/players")
def nfl_players(request: Request, q: str = "", season: int = 0, store=Depends(get_store)):
    db = _db(store)
    season = season or _season_now(db)
    results = []
    if q.strip():
        pattern = re.escape(q.strip().lower())
        results = list(
            db.nfl_players.find(
                {"name_lower": {"$regex": pattern}},
                {
                    "name": 1,
                    "position": 1,
                    "latest_team": 1,
                    "status": 1,
                    "rookie_season": 1,
                    "last_season": 1,
                },
            )
            .sort([("last_season", -1), ("name", 1)])
            .limit(60)
        )
    leaders = []
    for label, field, _groups in LEADER_STATS:
        rows = list(
            db.nfl_player_seasons.find(
                {"season": season, "season_type": "REG", field: {"$gt": 0}},
                {"player_id": 1, "name": 1, "position": 1, "teams": 1, "games": 1, field: 1},
            )
            .sort(field, -1)
            .limit(10)
        )
        leaders.append({"label": label, "field": field, "rows": rows})
    seasons = sorted(db.nfl_player_seasons.distinct("season"), reverse=True)
    return request.app.state.templates.TemplateResponse(
        request,
        "nfl/players.html",
        _ctx(
            q=q, results=results, leaders=leaders, season=season, seasons=seasons, teams=_teams(db)
        ),
    )


@router.get("/players/{player_id}")
def nfl_player(request: Request, player_id: str, season: int = 0, store=Depends(get_store)):
    db = _db(store)
    player = db.nfl_players.find_one({"_id": player_id})
    seasons = list(
        db.nfl_player_seasons.find({"player_id": player_id}, {"_id": 0}).sort(
            [("season", -1), ("season_type", 1)]
        )
    )
    if not player and not seasons:
        return RedirectResponse(url="/nfl/players", status_code=303)
    player = player or {
        "_id": player_id,
        "name": seasons[0]["name"],
        "position": seasons[0]["position"],
    }
    group = player.get("position_group") or (seasons[0]["position_group"] if seasons else "")
    columns = STAT_COLUMNS.get(group or "", STAT_COLUMNS["WR"])
    reg = [s for s in seasons if s["season_type"] == "REG"]
    post = [s for s in seasons if s["season_type"] == "POST"]
    career = {"games": sum(s["games"] for s in reg)}
    for _h, f in columns:
        career[f] = round(sum(s.get(f, 0) for s in reg), 2)
    log_season = season or (seasons[0]["season"] if seasons else 0)
    game_log = list(
        db.nfl_player_weeks.find({"player_id": player_id, "season": log_season}, {"_id": 0}).sort(
            "week", 1
        )
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "nfl/player.html",
        _ctx(
            player=player,
            reg=reg,
            post=post,
            career=career,
            columns=columns,
            log_season=log_season,
            log_seasons=sorted({s["season"] for s in seasons}, reverse=True),
            game_log=game_log,
            teams=_teams(db),
        ),
    )


@router.get("/model")
def nfl_model(request: Request, store=Depends(get_store)):
    db = _db(store)
    season = _season_now(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "nfl/model.html",
        _ctx(
            season=season,
            meta=db.nfl_meta.find_one({"_id": "ratings"}) or {},
            backtest=db.nfl_meta.find_one({"_id": "backtest"}),
            live=_live_record(db, season),
            workflow_ready=_workflow_ready(store),
        ),
    )


# --------------------------------------------------------------------------
# Actions — each submits an FFL workflow run
# --------------------------------------------------------------------------


def _run(store, current_user, workflow: str, inputs: dict, back: str):
    runner_id = _submit(store, current_user, workflow, inputs)
    if not runner_id:
        return RedirectResponse(url=f"{back}?error=not-seeded", status_code=303)
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.post("/update")
def nfl_update(store=Depends(get_store), current_user=Depends(get_current_user)):
    return _run(store, current_user, WF_UPDATE, {}, "/nfl/")


@router.post("/simulate")
def nfl_simulate(store=Depends(get_store), current_user=Depends(get_current_user)):
    return _run(store, current_user, WF_SIMULATE, {}, "/nfl/")


@router.post("/backtest")
def nfl_backtest(store=Depends(get_store), current_user=Depends(get_current_user)):
    return _run(store, current_user, WF_BACKTEST, {}, "/nfl/model")
