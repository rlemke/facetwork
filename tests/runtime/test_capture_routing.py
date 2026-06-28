"""Tests for ``StatementCaptureBeginHandler``'s yield routing.

The handler decides, for each completed yield collected from a step's
andThen blocks, whether the yield's params belong on the parent step's
returns or on one of the parent's aliased mixin sub-steps.  The rules:

1. Yield target == parent facet name → merge into parent.returns.
2. Yield target == declared alias on parent → mixin sub-step for that
   alias.
3. Yield target == mixin facet name AND that facet has exactly one
   alias on the parent → that single sub-step (back-compat with the
   ``yield F(...) with M(...)`` form when there's no ambiguity).
4. Otherwise → ignored at this scope.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from facetwork.runtime.handlers.capture import StatementCaptureBeginHandler
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import AttributeValue, FacetAttributes, ObjectType


def _step(
    *,
    id: str,
    facet_name: str = "",
    statement_name: str = "",
    container_id: str | None = None,
    object_type: str = ObjectType.VARIABLE_ASSIGNMENT,
    params: dict | None = None,
    returns: dict | None = None,
) -> StepDefinition:
    attrs = FacetAttributes(
        params={k: AttributeValue(k, v) for k, v in (params or {}).items()},
        returns={k: AttributeValue(k, v) for k, v in (returns or {}).items()},
    )
    return StepDefinition(
        id=id,
        object_type=object_type,
        workflow_id="wf-1",
        facet_name=facet_name,
        statement_name=statement_name,
        container_id=container_id,
        attributes=attrs,
    )


def _facet_def(mixins: list[dict]) -> dict:
    return {"name": "F2", "mixins": mixins}


def _setup_handler(parent: StepDefinition, facet_def: dict | None, *children: StepDefinition):
    """Wire a handler against a context with the given children persisted."""
    handler = StatementCaptureBeginHandler.__new__(StatementCaptureBeginHandler)
    handler.step = parent

    context = MagicMock()
    context.get_facet_definition.return_value = facet_def
    context.persistence.get_steps_by_container.return_value = list(children)
    context.changes.created_steps = []
    context.changes.updated_steps = []
    updated: list[StepDefinition] = []
    context.changes.add_updated_step.side_effect = lambda s: updated.append(s)
    handler.context = context

    # Mirror what process_state() seeds before any routing call.
    handler._seen_yield_ids = set()
    handler._mixin_substep_working = {}
    handler._mixin_aliases = {}
    handler._target_to_aliases = {}
    if facet_def:
        for mixin in facet_def.get("mixins", []) or []:
            target = mixin.get("target") or ""
            alias = mixin.get("alias")
            if alias:
                handler._mixin_aliases[alias] = target
            if target:
                handler._target_to_aliases.setdefault(target, []).append(alias or "")
    return handler, updated


class TestRouteYieldToParent:
    def test_target_matches_parent_facet(self):
        parent = _step(id="p", facet_name="F2")
        handler, updated = _setup_handler(parent, _facet_def([]))

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="F2",
            params={"output": "primary-value"},
        )
        handler._route_yield(yld)

        assert parent.attributes.returns["output"].value == "primary-value"
        assert updated == []  # nothing to update — parent is the live step

    def test_qualified_target_matches_parent_short_name(self):
        parent = _step(id="p", facet_name="alias_demo.F2")
        handler, _ = _setup_handler(parent, _facet_def([]))

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="F2",
            params={"output": "qualified-match"},
        )
        handler._route_yield(yld)
        assert parent.attributes.returns["output"].value == "qualified-match"


class TestRouteYieldByAlias:
    def test_alias_form_routes_to_alias_substep(self):
        parent = _step(id="p", facet_name="F2")
        sub = _step(
            id="sub",
            facet_name="M",
            statement_name="primary",
            container_id="p",
        )
        handler, updated = _setup_handler(
            parent,
            _facet_def(
                [
                    {"target": "M", "alias": "primary"},
                    {"target": "M", "alias": "fallback"},
                ]
            ),
            sub,
        )

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="primary",  # alias name, not facet name
            params={"output": "first-substep-value"},
        )
        handler._route_yield(yld)

        assert len(updated) == 1
        assert updated[0].statement_name == "primary"
        assert updated[0].attributes.returns["output"].value == "first-substep-value"
        # Parent untouched.
        assert "output" not in parent.attributes.returns

    def test_two_aliases_route_independently(self):
        parent = _step(id="p", facet_name="F2")
        primary_sub = _step(
            id="sub-primary",
            facet_name="M",
            statement_name="primary",
            container_id="p",
        )
        fallback_sub = _step(
            id="sub-fallback",
            facet_name="M",
            statement_name="fallback",
            container_id="p",
        )
        handler, updated = _setup_handler(
            parent,
            _facet_def(
                [
                    {"target": "M", "alias": "primary"},
                    {"target": "M", "alias": "fallback"},
                ]
            ),
            primary_sub,
            fallback_sub,
        )

        y_primary = _step(
            id="yp",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="primary",
            params={"output": "p-val"},
        )
        y_fallback = _step(
            id="yf",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="fallback",
            params={"output": "f-val"},
        )
        handler._route_yield(y_primary)
        handler._route_yield(y_fallback)

        by_name = {s.statement_name: s for s in updated}
        assert by_name["primary"].attributes.returns["output"].value == "p-val"
        assert by_name["fallback"].attributes.returns["output"].value == "f-val"

    def test_multi_yield_to_same_alias_aggregates_collections(self):
        parent = _step(id="p", facet_name="F2")
        sub = _step(
            id="sub",
            facet_name="M",
            statement_name="m1",
            container_id="p",
        )
        handler, updated = _setup_handler(
            parent,
            _facet_def([{"target": "M", "alias": "m1"}]),
            sub,
        )

        for value in (["a"], ["b"], ["c"]):
            handler._route_yield(
                _step(
                    id=f"y-{value[0]}",
                    object_type=ObjectType.YIELD_ASSIGNMENT,
                    facet_name="m1",
                    params={"items": value},
                )
            )

        # All three updates share the working copy, so the last update
        # observed by add_updated_step holds the aggregate.
        assert updated[-1].attributes.returns["items"].value == ["a", "b", "c"]


class TestRouteYieldByUniqueTargetFacet:
    def test_bare_target_routes_when_target_has_one_alias(self):
        """Back-compat: ``yield F(...) with M(out=...)`` still works when
        M is aliased exactly once on F's signature."""
        parent = _step(id="p", facet_name="F2")
        sub = _step(
            id="sub",
            facet_name="M",
            statement_name="only",
            container_id="p",
        )
        handler, updated = _setup_handler(
            parent,
            _facet_def([{"target": "M", "alias": "only"}]),
            sub,
        )

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="M",  # facet name, not alias
            params={"output": "back-compat"},
        )
        handler._route_yield(yld)

        assert len(updated) == 1
        assert updated[0].attributes.returns["output"].value == "back-compat"

    def test_bare_target_dropped_when_target_has_no_aliased_use(self):
        """Un-aliased mixin: yield to bare target has no sub-step to land on."""
        parent = _step(id="p", facet_name="F2")
        # Note: no sub-step under the parent — un-aliased mixins don't get
        # placeholders, and the bare target form has no addressable home.
        handler, updated = _setup_handler(
            parent,
            _facet_def([{"target": "M"}]),  # un-aliased
        )

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="M",
            params={"output": "ignored"},
        )
        handler._route_yield(yld)

        assert updated == []
        assert "output" not in parent.attributes.returns


class TestRouteYieldDropsUnknownTarget:
    def test_inner_facet_body_yield_is_dropped(self):
        """A yield inside a nested step's own body whose target is neither
        the parent nor any parent mixin is silently dropped at this scope."""
        parent = _step(id="p", facet_name="F2")
        handler, updated = _setup_handler(parent, _facet_def([]))

        yld = _step(
            id="y",
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name="UnrelatedInner",
            params={"output": "stays-on-inner"},
        )
        handler._route_yield(yld)

        assert updated == []
        assert "output" not in parent.attributes.returns
