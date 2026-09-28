"""Trusted-entry validation and state minimisation."""

from __future__ import annotations

import json
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

_MODES = frozenset({"ingest", "invoke", "feedback"})
_PIN_KEYS = (
    "control_catalogue_ref", "catalogue_version", "catalogue_content_hash", "perspective_pack_ref",
    "perspective_pack_version", "perspective_pack_content_hash",
)


class PreProcessNode(FunctionNode):
    """Accept only server-authenticated scope and version-pinned invoke inputs."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        context = state.get("input_context")
        raw = state.get("user_input")
        if not isinstance(context, dict) or not all(isinstance(context.get(key), str) and context[key] for key in ("tenant_id", "org_id", "entry_mode", "case_ref")):
            raise SecurityViolationError("trusted tenant_id, org_id, entry_mode, and case_ref are required")
        if context["entry_mode"] not in _MODES:
            raise SecurityViolationError("entry_mode is not allowed")
        if not isinstance(raw, str) or not raw or len(raw) > 64_000:
            raise SecurityViolationError("input must be a non-empty JSON object within the size limit")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecurityViolationError("input must be valid JSON") from exc
        if not isinstance(body, dict):
            raise SecurityViolationError("input must be a JSON object")
        if "mode" in body or "evidence_payload_ref" in body:
            raise SecurityViolationError("mode and evidence payload references are server-controlled")
        if context["entry_mode"] == "ingest" and any(key in body for key in _PIN_KEYS):
            raise SecurityViolationError("ingest does not accept control or perspective packs")
        return state

    def execute(self, state: dict) -> dict:
        from shared.utils.audit_logger import emit_trace_event

        emit_trace_event("preprocess_received", {}, state)
        context = state["input_context"]
        body = json.loads(state["user_input"])
        mode = context["entry_mode"]
        validated: dict = {"vendor_type": body.get("vendor_type", ""), "request_kind": mode}
        updates: dict = {
            "tenant_id": context["tenant_id"],
            "org_id": context["org_id"],
            "mode": mode,
            "case_ref": context["case_ref"],
            "validated_input": validated,
            "status": AgentStatus.SUCCESS.value,
        }
        for key in ("vendor_id", "assessment_ref", "evidence_payload_ref", "service_scope_fingerprint"):
            if isinstance(context.get(key), str) and context[key]:
                updates[key] = context[key]
        if mode == "invoke":
            if not isinstance(body.get("vendor_type"), str) or not body["vendor_type"]:
                return {"status": AgentStatus.ERROR.value, "error_log": ["vendor_type is required for invoke"]}
            pins = context.get("pins")
            sealed = context.get("sealed_content")
            if not isinstance(pins, dict) or not isinstance(sealed, dict) or not all(isinstance(pins.get(key), str) and pins[key] for key in _PIN_KEYS):
                return {"status": AgentStatus.ERROR.value, "error_log": ["verified pin bundle is required"]}
            if not updates.get("vendor_id") or not updates.get("evidence_payload_ref"):
                return {"status": AgentStatus.ERROR.value, "error_log": ["server-resolved vendor and evidence references are required"]}
            updates.update({key: pins[key] for key in _PIN_KEYS})
            updates["mapping_pack_version"] = str(pins.get("mapping_pack_version", ""))
            updates["mapping_pack_content_hash"] = str(pins.get("mapping_pack_content_hash", ""))
            updates["vendor_type"] = body["vendor_type"]
            updates["validated_input"] = {"vendor_type": body["vendor_type"], "request_kind": mode}
            updates["sealed_content"] = sealed
        emit_trace_event("preprocess_scope_validated", {"mode": mode}, state)
        return updates
