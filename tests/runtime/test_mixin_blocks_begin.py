"""Tests for ``MixinBlocksBeginHandler`` creating real mixin sub-step
rows in CREATED state with sig-args pre-bound as params.

Scope B: each aliased mixin gets a sub-step that walks the full
STEP_TRANSITIONS lifecycle.  Sub-step params are seeded from
``parent.attributes.params[alias]`` (the nested dict
``FacetInitializationBeginHandler`` produced from the mixin's
already-evaluated sig-args).

Un-aliased mixins are not created here — they remain pure config via
the v0.21.0 flat-merge contract on parent.params.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from facetwork.runtime.changers.base import StateChangeResult
from facetwork.runtime.handlers.blocks import MixinBlocksBeginHandler
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import AttributeValue, FacetAttributes, ObjectType


def _make_step(
    facet_name: str = "F2",
    params: dict | None = None,
) -> StepDefinition:
    step = StepDefinition.create(
        workflow_id="wf-test",
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        facet_name=facet_name,
    )
    if params:
        step.attributes = FacetAttributes(
            params={k: AttributeValue(k, v) for k, v in params.items()}
        )
    return step


def _make_context(facet_def: dict | None) -> MagicMock:
    context = MagicMock()
    context.get_facet_definition.return_value = facet_def
    context.persistence.get_steps_by_container.return_value = []
    context.changes.created_steps = []
    context.changes.add_created_step.side_effect = lambda s: context.changes.created_steps.append(s)
    return context


class TestMixinBlocksBeginCreatesExecutableSubSteps:
    def test_creates_one_sub_step_per_aliased_mixin_in_created_state(self):
        step = _make_step("F2")
        facet_def = {
            "type": "FacetDecl",
            "name": "F2",
            "mixins": [
                {"target": "M1", "alias": "m1"},
                {"target": "M2", "alias": "m2"},
            ],
        }
        context = _make_context(facet_def)
        handler = MixinBlocksBeginHandler(step, context)

        result = handler.process_state()

        assert isinstance(result, StateChangeResult)
        created = context.changes.created_steps
        assert len(created) == 2

        by_alias = {s.statement_name: s for s in created}
        m1 = by_alias["m1"]
        assert m1.facet_name == "M1"
        assert m1.container_id == step.id
        # Scope B: sub-steps walk the full lifecycle, so they start in CREATED
        # rather than being persisted directly at STATEMENT_COMPLETE.
        assert m1.state == StepState.CREATED
        assert by_alias["m2"].facet_name == "M2"

    def test_seeds_sub_step_params_from_parent_alias_bag(self):
        """The nested dict ``FacetInitializationBeginHandler`` placed on
        ``parent.params[alias]`` becomes the sub-step's bound params,
        one ``AttributeValue`` per key."""
        step = _make_step(
            "F2",
            params={
                "input": "outer-value",
                "m1": {"input": "inner-value", "max": 3},
            },
        )
        facet_def = {
            "type": "FacetDecl",
            "name": "F2",
            "mixins": [{"target": "M1", "alias": "m1"}],
        }
        context = _make_context(facet_def)
        MixinBlocksBeginHandler(step, context).process_state()

        sub = context.changes.created_steps[0]
        assert sub.attributes.params["input"].value == "inner-value"
        assert sub.attributes.params["max"].value == 3
        # The outer "input" must not leak into the mixin's own params.
        assert set(sub.attributes.params) == {"input", "max"}

    def test_no_alias_bag_yields_empty_params(self):
        """If the parent didn't bind any sig-args for this alias,
        the sub-step is created with no pre-bound params (facet defaults
        applied later by FacetInitializationBeginHandler)."""
        step = _make_step("F2")  # no params at all
        facet_def = {
            "type": "FacetDecl",
            "name": "F2",
            "mixins": [{"target": "M1", "alias": "m1"}],
        }
        context = _make_context(facet_def)
        MixinBlocksBeginHandler(step, context).process_state()

        sub = context.changes.created_steps[0]
        assert sub.attributes.params == {}

    def test_unaliased_mixins_are_not_persisted(self):
        """Mixins without an `as` alias remain pure config — handled by
        the v0.21.0 flat-merge contract on parent.params."""
        step = _make_step("F1")
        facet_def = {
            "type": "FacetDecl",
            "name": "F1",
            "mixins": [
                {"target": "M1"},  # no alias
                {"target": "M2"},
            ],
        }
        context = _make_context(facet_def)
        MixinBlocksBeginHandler(step, context).process_state()
        assert context.changes.created_steps == []

    def test_idempotent_when_sub_steps_already_persisted(self):
        """Re-running the handler must not duplicate sub-step rows."""
        step = _make_step("F2")
        facet_def = {
            "type": "FacetDecl",
            "name": "F2",
            "mixins": [{"target": "M1", "alias": "m1"}],
        }
        context = _make_context(facet_def)
        # First run
        MixinBlocksBeginHandler(step, context).process_state()
        first_count = len(context.changes.created_steps)
        # Simulate the sub-step having been persisted between runs.
        existing = context.changes.created_steps[0]
        context.persistence.get_steps_by_container.return_value = [existing]
        context.changes.created_steps = []  # fresh tick
        MixinBlocksBeginHandler(step, context).process_state()

        assert first_count == 1
        assert context.changes.created_steps == []

    def test_no_facet_def_is_noop(self):
        step = _make_step("Unknown")
        context = _make_context(None)
        result = MixinBlocksBeginHandler(step, context).process_state()
        assert isinstance(result, StateChangeResult)
        assert context.changes.created_steps == []
