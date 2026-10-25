"""Tests for the NFL dashboard app (reads the nfl_* collections fwh_nfl writes)."""

from __future__ import annotations

import os

import pytest

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


@pytest.fixture
def client():
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_nfl", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app) as tc:
        yield tc, store
    store.drop_database()
    store.close()


def _db(store):
    return store._db.client[os.environ.get("FW_EXAMPLES_DATABASE", "facetwork_examples")]


def _seed(store):
    db = _db(store)
    for abbr, name, div, color in (
        ("KC", "Kansas City Chiefs", "AFC West", "#E31837"),
        ("BUF", "Buffalo Bills", "AFC East", "#00338D"),
    ):
        db.nfl_teams.insert_one(
            {"_id": abbr, "name": name, "division": div, "conference": "AFC", "color": color}
        )
    model = {
        "p_home": 0.62,
        "spread_pred": 3.4,
        "p_home_market": 0.58,
        "elo_home": 1600,
        "elo_away": 1560,
    }
    db.nfl_games.insert_many([
        {"_id": "2026_01_BUF_KC", "season": 2026, "game_type": "REG", "week": 1, "gameday": "2026-09-10",
         "gametime": "20:20", "home": "KC", "away": "BUF", "home_abbr": "KC", "away_abbr": "BUF",
         "home_score": 27, "away_score": 20, "spread_line": 2.5, "model": model},
        {"_id": "2026_02_KC_BUF", "season": 2026, "game_type": "REG", "week": 2, "gameday": "2099-09-17",
         "gametime": "13:00", "home": "BUF", "away": "KC", "home_abbr": "BUF", "away_abbr": "KC",
         "home_score": None, "away_score": None, "spread_line": None, "model": model},
    ])  # fmt: skip
    db.nfl_team_ratings.insert_many([
        {"_id": "KC", "elo": 1610.0, "rank": 1, "conference": "AFC", "history": [[2025, 18, "REG", 1590.0], [2026, 1, "REG", 1610.0]]},
        {"_id": "BUF", "elo": 1550.0, "rank": 2, "conference": "AFC", "history": [[2026, 1, "REG", 1550.0]]},
    ])  # fmt: skip
    db.nfl_meta.insert_one({"_id": "ratings", "season": 2026, "through_game": "2026_01_BUF_KC",
                            "through_date": "2026-09-10", "params": {"hfa": 48.0, "bye_bonus": 25.0, "playoff_mult": 1.2, "regress": 0.4}})  # fmt: skip
    row = {"conference": "AFC", "mean_wins": 11.2, "playoffs": 0.8, "division": 0.6, "bye": 0.3,
           "divisional_round": 0.5, "conference_game": 0.3, "super_bowl": 0.2, "champion": 0.1, "seeds": {"1": 0.3}}  # fmt: skip
    db.nfl_odds.insert_one({
        "_id": "k1", "season": 2026, "sims": 1000, "through_game": "2026_01_BUF_KC", "through_date": "2026-09-10",
        "created_at": "2026-09-11T00:00:00+00:00",
        "odds": [{"team": "KC", **row}, {"team": "BUF", **row, "champion": 0.05}],
        "bracket": {c: [{"seed": i, "team": "KC" if i == 1 else "BUF", "p": 0.3} for i in range(1, 3)] for c in ("AFC", "NFC")},
        "matchups": [{"afc": "KC", "nfc": "BUF", "p": 0.01}],
    })  # fmt: skip
    db.nfl_players.insert_one({"_id": "00-1", "name": "Pat Q", "name_lower": "pat q", "position": "QB",
                               "position_group": "QB", "latest_team": "KC", "status": "ACT"})  # fmt: skip
    db.nfl_player_seasons.insert_one({"player_id": "00-1", "name": "Pat Q", "position": "QB", "position_group": "QB",
                                      "season": 2026, "season_type": "REG", "teams": ["KC"], "games": 1, "passing_yards": 312})  # fmt: skip
    db.nfl_player_weeks.insert_one({"player_id": "00-1", "season": 2026, "week": 1, "season_type": "REG",
                                    "team": "KC", "opponent": "BUF", "passing_yards": 312})  # fmt: skip


def test_empty_state_explains_what_to_run(client):
    tc, _ = client
    r = tc.get("/nfl/")
    assert r.status_code == 200
    assert "No NFL data loaded yet" in r.text


def test_every_page_renders_from_seeded_data(client):
    tc, store = client
    _seed(store)
    r = tc.get("/nfl/")
    assert r.status_code == 200
    assert "Projected playoff bracket" in r.text and "Kansas City Chiefs" in r.text
    assert tc.get("/nfl/?week=2").status_code == 200
    r = tc.get("/nfl/teams/kc")
    assert r.status_code == 200 and "Path to the Super Bowl" in r.text and "1-0" in r.text
    r = tc.get("/nfl/players?q=pat")
    assert r.status_code == 200 and "/nfl/players/00-1" in r.text
    r = tc.get("/nfl/players/00-1")
    assert r.status_code == 200 and "312" in r.text
    assert tc.get("/nfl/model").status_code == 200


def test_unknown_team_and_player_redirect(client):
    tc, store = client
    _seed(store)
    assert tc.get("/nfl/teams/XXX", follow_redirects=False).status_code == 303
    assert tc.get("/nfl/players/nobody", follow_redirects=False).status_code == 303


def test_update_without_seeded_workflow_says_so(client):
    tc, _ = client
    r = tc.post("/nfl/update", follow_redirects=False)
    assert r.status_code == 303 and "not-seeded" in r.headers["location"]
