# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Session login: password hashing, login/logout flow, auth activation."""

import pytest

try:
    from fastapi.testclient import TestClient

    HAS_ROUTES = True
except ImportError:
    HAS_ROUTES = False

try:
    import mongomock

    HAS_MONGOMOCK = True
except ImportError:
    HAS_MONGOMOCK = False

from facetwork.runtime.entities import User

pytestmark_routes = pytest.mark.skipif(
    not (HAS_ROUTES and HAS_MONGOMOCK), reason="fastapi/mongomock not installed"
)


class TestPasswordHashing:
    def test_set_and_verify(self):
        u = User(email="a@b.c")
        assert not u.has_password
        u.set_password("hunter2hunter2")
        assert u.has_password
        assert u.verify_password("hunter2hunter2")
        assert not u.verify_password("wrong")

    def test_verify_garbage_hash_is_false(self):
        u = User(email="a@b.c", password_hash="not-a-hash")
        assert not u.verify_password("anything")

    def test_salts_differ(self):
        a, b = User(email="a@b.c"), User(email="d@e.f")
        a.set_password("same-password")
        b.set_password("same-password")
        assert a.password_hash != b.password_hash


class TestSessionCookie:
    def test_roundtrip_and_tamper(self):
        from starlette.requests import Request

        from facetwork.dashboard.auth import SESSION_COOKIE, make_session, read_session

        val = make_session("user@test.local")

        def req(cookie):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": b"",
                "headers": [(b"cookie", f"{SESSION_COOKIE}={cookie}".encode())],
            }
            return Request(scope)

        assert read_session(req(val)) == "user@test.local"
        assert read_session(req(val[:-2] + "00")) is None  # tampered sig
        assert read_session(req("garbage")) is None

    def test_email_with_pipe_rejected(self):
        from facetwork.dashboard.auth import make_session

        with pytest.raises(ValueError):
            make_session("evil|admin@test")


@pytestmark_routes
class TestLoginRoutes:
    @pytest.fixture
    def client(self):
        from facetwork.dashboard import dependencies as deps
        from facetwork.dashboard.app import create_app
        from facetwork.runtime.mongo_store import MongoStore

        mock_client = mongomock.MongoClient()
        store = MongoStore(database_name="afl_test_login", client=mock_client)
        store.save_user(User(email="ralph@test.local", rights=["delete_runs"]))

        app = create_app()
        app.dependency_overrides[deps.get_store] = lambda: store
        with TestClient(app) as tc:
            yield tc, store
        store.drop_database()
        store.close()

    def test_first_login_sets_password_and_session(self, client):
        tc, store = client
        # No password yet: submitting just the email enters setup mode.
        r = tc.post("/login", data={"email": "ralph@test.local"})
        assert r.status_code == 200 and "Set your password" in r.text
        # Setting it logs in and stores the hash.
        r = tc.post(
            "/login",
            data={
                "email": "ralph@test.local",
                "new_password": "correct-horse",
                "confirm_password": "correct-horse",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "fw_session" in r.cookies
        assert store.get_user("ralph@test.local").has_password

    def test_login_wrong_password(self, client):
        tc, store = client
        u = store.get_user("ralph@test.local")
        u.set_password("correct-horse")
        store.save_user(u)
        r = tc.post("/login", data={"email": "ralph@test.local", "password": "nope"})
        assert "Wrong password" in r.text
        assert "fw_session" not in r.cookies

    def test_session_identity_beats_acting_as_and_gates_rights(self, client, monkeypatch):
        tc, store = client
        u = store.get_user("ralph@test.local")
        u.set_password("correct-horse")
        store.save_user(u)
        store.save_user(User(email="mallory@test.local"))  # no rights, no password

        # Auth is active (a password exists): acting-as cookie alone is ignored.
        tc.cookies.set("afl_current_user", "ralph@test.local")
        r = tc.post("/api/runners/ghost/delete")
        assert r.status_code == 403  # anonymous — impersonation via cookie is dead

        # Log in: session identity + right => passes the gate (404 = fake id).
        tc.post("/login", data={"email": "ralph@test.local", "password": "correct-horse"})
        r = tc.post("/api/runners/ghost/delete")
        assert r.status_code == 404

    def test_session_satisfies_mutation_token_guard(self, client, monkeypatch):
        tc, store = client
        monkeypatch.setenv("FW_DASHBOARD_TOKEN", "sekrit-token")
        u = store.get_user("ralph@test.local")
        u.set_password("correct-horse")
        store.save_user(u)

        # login POST itself is exempt from the token guard
        r = tc.post(
            "/login",
            data={"email": "ralph@test.local", "password": "correct-horse"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        # a mutating call with session but WITHOUT the bearer token is accepted
        r = tc.post("/api/runners/ghost/delete")
        assert r.status_code == 404  # past both middleware and rights gate

    def test_logout_clears_session(self, client):
        tc, store = client
        u = store.get_user("ralph@test.local")
        u.set_password("correct-horse")
        store.save_user(u)
        tc.post("/login", data={"email": "ralph@test.local", "password": "correct-horse"})
        r = tc.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        r = tc.post("/api/runners/ghost/delete")
        assert r.status_code == 403  # anonymous again
