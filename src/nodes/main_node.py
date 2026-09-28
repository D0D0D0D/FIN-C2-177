"""GraphNode bridge between the fixed outer backbone and the inner workflow."""

from __future__ import annotations

import json
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel
from src.graph.domain_workflow_graph import OutsourcingBriefWorkflowGraph


class OutsourcingBriefGraphNode(GraphNode):
    """Cache one inner graph instance so HITL resume reaches the suspend point."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}
        self._subgraph: OutsourcingBriefWorkflowGraph | None = None

    def _extra_security_gate_input(self, state: dict) -> dict:
        if not all(isinstance(state.get(key), str) and state[key] for key in ("tenant_id", "org_id", "mode", "case_ref")):
            raise SecurityViolationError("main graph requires trusted tenant, organisation, mode, and case scope")
        return state

    def get_subgraph(self) -> OutsourcingBriefWorkflowGraph:
        if self._subgraph is None:
            self._subgraph = OutsourcingBriefWorkflowGraph(config=self._config)
            self._subgraph.compile(checkpointer=self._config.get("inner_checkpointer"))
        return self._subgraph

    def extract_input(self, state: dict) -> str:
        from shared.utils.audit_logger import emit_trace_event

        emit_trace_event("domain_workflow_input_extracted", {"mode": state.get("mode", "")}, state)
        pins = {key: state.get(key, "") for key in (
            "control_catalogue_ref", "catalogue_version", "catalogue_content_hash", "perspective_pack_ref",
            "perspective_pack_version", "perspective_pack_content_hash", "mapping_pack_version", "mapping_pack_content_hash",
        )}
        payload = {
            "mode": state["mode"], "tenant_id": state["tenant_id"], "org_id": state["org_id"], "case_ref": state["case_ref"],
            "vendor_id": state.get("vendor_id", ""), "vendor_type": state.get("vendor_type", ""), "service_scope_fingerprint": state.get("service_scope_fingerprint", ""),
            "assessment_ref": state.get("assessment_ref", ""), "evidence_payload_ref": state.get("evidence_payload_ref", ""),
            "validated_input": state.get("validated_input", {}), "pins": pins, "sealed_content": state.get("sealed_content", {}),
        }
        return json.dumps(payload, default=str, separators=(",", ":"))

    def merge_output(self, state: dict, sub_result: dict) -> dict:
        mode = state.get("mode")
        result: dict = {"status": sub_result.get("status", "error")}
        if sub_result.get("error_log"):
            result["error_log"] = sub_result["error_log"]
        if mode == "invoke":
            for key in ("evidence_brief", "evidence_map", "current_evidence_basis", "changed_since_last", "requires_human_review", "degradation_reason", "assessment_ref", "source_registry"):
                if key in sub_result and sub_result[key] is not None:
                    result[key] = sub_result[key]
        elif mode == "ingest" and sub_result.get("ingest_result") is not None:
            result["ingest_result"] = sub_result["ingest_result"]
        elif mode == "feedback" and sub_result.get("feedback_result") is not None:
            result["feedback_result"] = sub_result["feedback_result"]
        return result


MainNode = OutsourcingBriefGraphNode
