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

"""Workflow, flow, namespace, handler registration, and published source operations mixin."""

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from ..entities import (
    FlowDefinition,
    FlowIdentity,
    HandlerRegistration,
    PublishedSource,
    WorkflowDefinition,
)


class WorkflowMixin:
    """Workflow, flow, handler registration, and published source CRUD."""

    # =========================================================================
    # Workflow Operations (query helpers)
    # =========================================================================

    def get_all_workflows(self, limit: int = 100) -> Sequence[WorkflowDefinition]:
        """Get all workflows, most recently created first."""
        docs = self._db.workflows.find().sort("date", -1).limit(limit)
        return [self._doc_to_workflow(doc) for doc in docs]

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow by ID."""
        doc = self._db.workflows.find_one({"uuid": workflow_id})
        return self._doc_to_workflow(doc) if doc else None

    def get_workflow_by_name(self, name: str) -> WorkflowDefinition | None:
        """Get a workflow by name."""
        doc = self._db.workflows.find_one({"name": name})
        return self._doc_to_workflow(doc) if doc else None

    def get_workflows_by_flow(self, flow_id: str) -> Sequence[WorkflowDefinition]:
        """Get all workflows for a flow."""
        docs = self._db.workflows.find({"flow_id": flow_id})
        return [self._doc_to_workflow(doc) for doc in docs]

    def save_workflow(self, workflow: WorkflowDefinition) -> None:
        """Save a workflow."""
        doc = self._workflow_to_doc(workflow)
        self._db.workflows.replace_one({"uuid": workflow.uuid}, doc, upsert=True)

    # =========================================================================
    # Flow Operations
    # =========================================================================

    def get_flow(self, flow_id: str) -> FlowDefinition | None:
        """Get a flow by ID."""
        doc = self._db.flows.find_one({"uuid": flow_id})
        return self._doc_to_flow(doc) if doc else None

    def get_flow_by_path(self, path: str) -> FlowDefinition | None:
        """Get a flow by path."""
        doc = self._db.flows.find_one({"name.path": path})
        return self._doc_to_flow(doc) if doc else None

    def get_flow_by_name(self, name: str) -> FlowDefinition | None:
        """Get a flow by name."""
        doc = self._db.flows.find_one({"name.name": name})
        return self._doc_to_flow(doc) if doc else None

    def save_flow(self, flow: FlowDefinition) -> None:
        """Save a flow."""
        doc = self._flow_to_doc(flow)
        self._db.flows.replace_one({"uuid": flow.uuid}, doc, upsert=True)

    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        result = self._db.flows.delete_one({"uuid": flow_id})
        return result.deleted_count > 0

    def get_all_flows(self) -> Sequence[FlowDefinition]:
        """Get all flows."""
        docs = self._db.flows.find()
        return [self._doc_to_flow(doc) for doc in docs]

    # =========================================================================
    # Handler Registration Operations
    # =========================================================================

    def save_handler_registration(self, registration: HandlerRegistration) -> None:
        """Upsert a handler registration by facet_name."""
        doc = self._handler_reg_to_doc(registration)
        self._db.handler_registrations.replace_one(
            {"facet_name": registration.facet_name}, doc, upsert=True
        )

    def get_handler_registration(self, facet_name: str) -> HandlerRegistration | None:
        """Get a handler registration by facet name."""
        doc = self._db.handler_registrations.find_one({"facet_name": facet_name})
        return self._doc_to_handler_reg(doc) if doc else None

    def list_handler_registrations(self) -> list[HandlerRegistration]:
        """List all handler registrations."""
        docs = self._db.handler_registrations.find()
        return [self._doc_to_handler_reg(doc) for doc in docs]

    def delete_handler_registration(self, facet_name: str) -> bool:
        """Delete a handler registration by facet name."""
        result = self._db.handler_registrations.delete_one({"facet_name": facet_name})
        return result.deleted_count > 0

    # =========================================================================
    # Published Source Operations
    # =========================================================================

    def save_published_source(self, source: PublishedSource) -> None:
        """Save or update a published source.

        Upserts by (namespace_name, version).
        """
        doc = {
            "uuid": source.uuid,
            "namespace_name": source.namespace_name,
            "source_text": source.source_text,
            "namespaces_defined": source.namespaces_defined,
            "version": source.version,
            "published_at": source.published_at,
            "origin": source.origin,
            "checksum": source.checksum,
        }
        self._db.afl_sources.replace_one(
            {"namespace_name": source.namespace_name, "version": source.version},
            doc,
            upsert=True,
        )

    def get_source_by_namespace(self, name: str, version: str = "latest") -> PublishedSource | None:
        """Get a published source by namespace name and version."""
        doc = self._db.afl_sources.find_one({"namespace_name": name, "version": version})
        return self._doc_to_published_source(doc) if doc else None

    def get_sources_by_namespaces(
        self, names: set[str], version: str = "latest"
    ) -> dict[str, PublishedSource]:
        """Batch-fetch published sources by namespace names.

        Returns a dict mapping namespace_name -> PublishedSource.
        """
        docs = self._db.afl_sources.find(
            {"namespace_name": {"$in": list(names)}, "version": version}
        )
        result: dict[str, PublishedSource] = {}
        for doc in docs:
            ps = self._doc_to_published_source(doc)
            result[ps.namespace_name] = ps
        return result

    def delete_published_source(self, name: str, version: str = "latest") -> bool:
        """Delete a published source by namespace name and version."""
        result = self._db.afl_sources.delete_one({"namespace_name": name, "version": version})
        return result.deleted_count > 0

    def list_published_sources(self) -> list[PublishedSource]:
        """List all published sources."""
        docs = self._db.afl_sources.find()
        return [self._doc_to_published_source(doc) for doc in docs]

    # =========================================================================
    # Serialization Helpers — Workflows, Flows, Handlers, Sources
    # =========================================================================

    def _handler_reg_to_doc(self, reg: HandlerRegistration) -> dict:
        """Convert HandlerRegistration to MongoDB document."""
        return {
            "facet_name": reg.facet_name,
            "module_uri": reg.module_uri,
            "entrypoint": reg.entrypoint,
            "version": reg.version,
            "checksum": reg.checksum,
            "timeout_ms": reg.timeout_ms,
            "requirements": reg.requirements,
            "metadata": reg.metadata,
            "created": reg.created,
            "updated": reg.updated,
        }

    def _doc_to_handler_reg(self, doc: dict) -> HandlerRegistration:
        """Convert MongoDB document to HandlerRegistration."""
        return HandlerRegistration(
            facet_name=doc["facet_name"],
            module_uri=doc["module_uri"],
            entrypoint=doc.get("entrypoint", "handle"),
            version=doc.get("version", "1.0.0"),
            checksum=doc.get("checksum", ""),
            timeout_ms=doc.get("timeout_ms", 30000),
            requirements=doc.get("requirements", []),
            metadata=doc.get("metadata", {}),
            created=doc.get("created", 0),
            updated=doc.get("updated", 0),
        )

    def _workflow_to_doc(self, workflow: WorkflowDefinition) -> dict:
        """Convert WorkflowDefinition to MongoDB document."""
        return {
            "uuid": workflow.uuid,
            "name": workflow.name,
            "namespace_id": workflow.namespace_id,
            "facet_id": workflow.facet_id,
            "flow_id": workflow.flow_id,
            "starting_step": workflow.starting_step,
            "version": workflow.version,
            "metadata": asdict(workflow.metadata) if workflow.metadata else None,
            "documentation": workflow.documentation,
            "date": workflow.date,
        }

    def _doc_to_workflow(self, doc: dict) -> WorkflowDefinition:
        """Convert MongoDB document to WorkflowDefinition."""
        from ..entities import WorkflowMetaData

        metadata = None
        if doc.get("metadata"):
            metadata = WorkflowMetaData(**doc["metadata"])

        return WorkflowDefinition(
            uuid=doc["uuid"],
            name=doc["name"],
            namespace_id=doc["namespace_id"],
            facet_id=doc["facet_id"],
            flow_id=doc["flow_id"],
            starting_step=doc["starting_step"],
            version=doc["version"],
            metadata=metadata,
            documentation=doc.get("documentation"),
            date=doc.get("date", 0),
        )

    def _flow_to_doc(self, flow: FlowDefinition) -> dict:
        """Convert FlowDefinition to MongoDB document."""
        return {
            "uuid": flow.uuid,
            "name": asdict(flow.name),
            "namespaces": [asdict(n) for n in flow.namespaces],
            "facets": [asdict(f) for f in flow.facets],
            "workflows": [self._workflow_to_doc(w) for w in flow.workflows],
            "mixins": [asdict(m) for m in flow.mixins],
            "blocks": [asdict(b) for b in flow.blocks],
            "statements": [asdict(s) for s in flow.statements],
            "arguments": [asdict(a) for a in flow.arguments],
            "references": [asdict(r) for r in flow.references],
            "script_code": [asdict(s) for s in flow.script_code],
            "file_artifacts": [asdict(f) for f in flow.file_artifacts],
            "jar_artifacts": [asdict(j) for j in flow.jar_artifacts],
            "resources": [asdict(r) for r in flow.resources],
            "text_sources": [asdict(t) for t in flow.text_sources],
            "inline": asdict(flow.inline) if flow.inline else None,
            "classification": asdict(flow.classification) if flow.classification else None,
            "publisher": asdict(flow.publisher) if flow.publisher else None,
            "ownership": asdict(flow.ownership) if flow.ownership else None,
            "compiled_sources": [asdict(s) for s in flow.compiled_sources],
            "compiled_ast": flow.compiled_ast,
        }

    def _doc_to_flow(self, doc: dict) -> FlowDefinition:
        """Convert MongoDB document to FlowDefinition."""
        from ..entities import (
            BlockDefinition,
            Classifier,
            FacetDefinition,
            FileArtifact,
            InlineSource,
            JarArtifact,
            MixinDefinition,
            NamespaceDefinition,
            Ownership,
            ResourceSource,
            ScriptCode,
            SourceText,
            StatementArguments,
            StatementDefinition,
            StatementReferences,
            TextSource,
            UserDefinition,
        )

        name = FlowIdentity(**doc["name"])

        inline = None
        if doc.get("inline"):
            inline = InlineSource(**doc["inline"])

        classification = None
        if doc.get("classification"):
            classification = Classifier(**doc["classification"])

        publisher = None
        if doc.get("publisher"):
            publisher = UserDefinition(**doc["publisher"])

        ownership = None
        if doc.get("ownership"):
            ownership = Ownership(**doc["ownership"])

        return FlowDefinition(
            uuid=doc["uuid"],
            name=name,
            namespaces=[NamespaceDefinition(**n) for n in doc.get("namespaces", [])],
            facets=[FacetDefinition(**f) for f in doc.get("facets", [])],
            workflows=[self._doc_to_workflow(w) for w in doc.get("workflows", [])],
            mixins=[MixinDefinition(**m) for m in doc.get("mixins", [])],
            blocks=[BlockDefinition(**b) for b in doc.get("blocks", [])],
            statements=[StatementDefinition(**s) for s in doc.get("statements", [])],
            arguments=[StatementArguments(**a) for a in doc.get("arguments", [])],
            references=[StatementReferences(**r) for r in doc.get("references", [])],
            script_code=[ScriptCode(**s) for s in doc.get("script_code", [])],
            file_artifacts=[FileArtifact(**f) for f in doc.get("file_artifacts", [])],
            jar_artifacts=[JarArtifact(**j) for j in doc.get("jar_artifacts", [])],
            resources=[ResourceSource(**r) for r in doc.get("resources", [])],
            text_sources=[TextSource(**t) for t in doc.get("text_sources", [])],
            inline=inline,
            classification=classification,
            publisher=publisher,
            ownership=ownership,
            compiled_sources=[SourceText(**s) for s in doc.get("compiled_sources", [])],
            compiled_ast=doc.get("compiled_ast"),
        )

    def _doc_to_published_source(self, doc: dict) -> PublishedSource:
        """Convert MongoDB document to PublishedSource."""
        return PublishedSource(
            uuid=doc["uuid"],
            namespace_name=doc["namespace_name"],
            source_text=doc["source_text"],
            namespaces_defined=doc.get("namespaces_defined", []),
            version=doc.get("version", "latest"),
            published_at=doc.get("published_at", 0),
            origin=doc.get("origin", ""),
            checksum=doc.get("checksum", ""),
        )
