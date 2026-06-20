"""Tests for the catalog -> compose env sync (facetwork.domains.compose_gen)."""

from __future__ import annotations

import pytest

from facetwork.domains import compose_gen

_COMPOSE = """\
services:
  runner-foo:
    build:
      context: .
    environment:
      AFL_MONGODB_URL: mongodb://mongodb:27017
      AFL_DOMAIN_NAME: foo
      AFL_DOMAIN_REPO: fwh_foo
      AFL_REGISTRY_RUNNER_ARGS: "--task-list foo"
    volumes:
      - x:/y
  runner-bar:
    environment:
      AFL_DOMAIN_NAME: bar
      AFL_DOMAIN_REPO: fwh_bar
      AFL_REGISTRY_RUNNER_ARGS: "--task-list bar"
"""


@pytest.fixture
def compose(tmp_path, monkeypatch):
    f = tmp_path / "docker-compose.full-stack.yml"
    f.write_text(_COMPOSE, encoding="utf-8")
    monkeypatch.setattr(compose_gen, "COMPOSE", f)
    return f


def _catalog(monkeypatch, mapping):
    monkeypatch.setattr(compose_gen, "compose_domains", lambda: mapping)


def test_in_sync_no_changes(compose, monkeypatch):
    _catalog(monkeypatch, {
        "foo": {"service": "runner-foo", "repo": "fwh_foo", "task_list": "foo"},
        "bar": {"service": "runner-bar", "repo": "fwh_bar", "task_list": "bar"},
    })
    changes, warnings = compose_gen.sync(write=True)
    assert changes == [] and warnings == []
    assert compose.read_text() == _COMPOSE  # byte-identical


def test_task_list_change_is_scoped_to_its_block(compose, monkeypatch):
    _catalog(monkeypatch, {
        "foo": {"service": "runner-foo", "repo": "fwh_foo", "task_list": "foo2"},
        "bar": {"service": "runner-bar", "repo": "fwh_bar", "task_list": "bar"},
    })
    changes, _ = compose_gen.sync(write=True)
    assert len(changes) == 1 and "runner-foo" in changes[0]
    text = compose.read_text()
    assert 'AFL_REGISTRY_RUNNER_ARGS: "--task-list foo2"' in text
    assert 'AFL_REGISTRY_RUNNER_ARGS: "--task-list bar"' in text  # bar untouched
    assert "AFL_MONGODB_URL: mongodb://mongodb:27017" in text     # structure intact


def test_server_group_and_extras_formatting(compose, monkeypatch):
    _catalog(monkeypatch, {
        "foo": {"service": "runner-foo", "repo": "fwh_foo", "task_list": "foo",
                "server_group_arg": True, "compose_extras": "a,b", "extras_var": "FOO_EXTRAS"},
        "bar": {"service": "runner-bar", "repo": "fwh_bar", "task_list": "bar"},
    })
    compose_gen.sync(write=True)
    text = compose.read_text()
    assert 'AFL_REGISTRY_RUNNER_ARGS: "--task-list foo --server-group ${AFL_SERVER_GROUP:-default}"' in text
    # compose_extras key wasn't in the template -> reported as a warning, not inserted
    _changes, warnings = compose_gen.sync(write=False)
    assert any("AFL_DOMAIN_EXTRAS" in w for w in warnings)


def test_missing_service_warns(compose, monkeypatch):
    _catalog(monkeypatch, {"ghost": {"service": "runner-ghost", "repo": "fwh_ghost", "task_list": "g"}})
    changes, warnings = compose_gen.sync(write=True)
    assert changes == [] and any("runner-ghost" in w for w in warnings)
