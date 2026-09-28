"""S-3 closed-schema output boundary and public output envelope."""

from __future__ import annotations

from copy import deepcopy
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

_SUPPORT_LEVELS = {"supported", "insufficient", "contradicted", "undetermined"}
_BRIEF_KEYS = {"control_requirements", "evidence_sources", "gaps", "contradictions", "verification_questions", "prior_finding", "changed_since_last"}
_ENVELOPE_KEYS = {"evidence_brief", "evidence_map", "current_evidence_basis", "applied_pins", "assessment_ref", "requires_human_review", "degradation_reason", "mode"}
_MAX_EVIDENCE_SPANS = 100


def _exact(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise SecurityViolationError(f"{label} does not match its closed schema")
    return value


def _list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise SecurityViolationError(f"{label} must be a list")
    return value


def _validate_brief(brief: object, registry: set[str]) -> None:
    data = _exact(brief, _BRIEF_KEYS, "evidence_brief")
    for item in _list(data["control_requirements"], "control_requirements"):
        requirement = _exact(item, {"control_id", "requirement_text", "support_level", "claim", "source_ids"}, "control requirement")
        if not all(isinstance(requirement[key], str) for key in ("control_id", "requirement_text", "support_level", "claim")) or requirement["support_level"] not in _SUPPORT_LEVELS:
            raise SecurityViolationError("control requirement contains an invalid value")
        _validate_source_ids(requirement["source_ids"], registry)
    for item in _list(data["evidence_sources"], "evidence_sources"):
        source = _exact(item, {"source_id", "document_version", "content_hash"}, "evidence source")
        if not all(isinstance(source[key], str) for key in source) or source["source_id"] not in registry:
            raise SecurityViolationError("evidence source is not registered")
    for item in _list(data["gaps"], "gaps") + _list(data["contradictions"], "contradictions"):
        point = _exact(item, {"control_id", "description"}, "gap or contradiction")
        if not all(isinstance(point[key], str) for key in point):
            raise SecurityViolationError("gap or contradiction contains an invalid value")
    for item in _list(data["verification_questions"], "verification_questions"):
        question = _exact(item, {"control_id", "question"}, "verification question")
        if not all(isinstance(question[key], str) for key in question):
            raise SecurityViolationError("verification question contains an invalid value")
    for item in _list(data["prior_finding"], "prior_finding"):
        finding = _exact(item, {"control_id", "support_level", "claim", "verification_questions", "evidence_source_ids", "as_of"}, "prior finding")
        if finding["support_level"] not in _SUPPORT_LEVELS:
            raise SecurityViolationError("prior finding support level is invalid")
        _list(finding["verification_questions"], "prior verification questions")
        _list(finding["evidence_source_ids"], "prior evidence sources")
        _exact(finding["as_of"], {"catalogue_version", "catalogue_content_hash", "requirement_text_hash", "assessed_at", "service_scope_fingerprint", "document_versions"}, "prior finding as_of")
    for item in _list(data["changed_since_last"], "changed_since_last"):
        change = _exact(item, {"control_id", "status", "reason_code"}, "changed_since_last")
        if change["status"] not in {"improved", "regressed", "unchanged", "not_comparable"}:
            raise SecurityViolationError("changed_since_last status is invalid")


def _validate_source_ids(source_ids: object, registry: set[str]) -> None:
    ids = _list(source_ids, "source_ids")
    if not all(isinstance(source_id, str) and source_id in registry for source_id in ids):
        raise SecurityViolationError("claim refers to a source outside source_registry")


def _validate_current_evidence_basis(value: object, registry: set[str], requirements: list[dict]) -> None:
    support_by_control = {item["control_id"]: item["support_level"] for item in requirements}
    seen: set[str] = set()
    span_count = 0
    for item in _list(value, "current_evidence_basis"):
        basis = _exact(item, {"control_id", "source_ids", "spans", "observation"}, "current evidence basis")
        if not isinstance(basis["control_id"], str) or not isinstance(basis["observation"], str):
            raise SecurityViolationError("current evidence basis is invalid")
        if basis["control_id"] in seen:
            raise SecurityViolationError("current evidence basis contains duplicate controls")
        if basis["control_id"] not in support_by_control:
            raise SecurityViolationError("current evidence basis contains an orphan control")
        if support_by_control[basis["control_id"]] == "undetermined":
            raise SecurityViolationError("undetermined requirements must not have evidence basis")
        seen.add(basis["control_id"])
        _validate_source_ids(basis["source_ids"], registry)
        spans = _list(basis["spans"], "basis spans")
        if not basis["source_ids"] or len(basis["source_ids"]) != len(spans):
            raise SecurityViolationError("basis source_ids and spans must be non-empty parallel arrays")
        span_count += len(spans)
        if span_count > _MAX_EVIDENCE_SPANS:
            raise SecurityViolationError(f"current evidence basis exceeds {_MAX_EVIDENCE_SPANS} spans")
        for span in spans:
            item_span = _exact(span, {"start", "end"}, "basis span")
            if not all(isinstance(item_span[key], int) and not isinstance(item_span[key], bool) for key in item_span):
                raise SecurityViolationError("basis span is invalid")
    expected = {control_id for control_id, support in support_by_control.items() if support != "undetermined"}
    if seen != expected:
        raise SecurityViolationError("current evidence basis does not match assessed controls")


def validate_stage3a_draft(draft: object, registry: set[str]) -> None:
    """Apply the downstream closed schemas before a model draft can enter State."""
    data = _exact(draft, {"control_requirements", "current_evidence_basis"}, "Stage 3a draft")
    _validate_brief({
        "control_requirements": data["control_requirements"],
        "evidence_sources": [],
        "gaps": [],
        "contradictions": [],
        "verification_questions": [],
        "prior_finding": [],
        "changed_since_last": [],
    }, registry)
    _validate_current_evidence_basis(data["current_evidence_basis"], registry, data["control_requirements"])


def validate_invoke_envelope(envelope: object, registry: set[str] | None = None) -> None:
    """Validate every nested invoke output object; undeclared keys are rejected."""
    registry = registry or set()
    data = _exact(envelope, _ENVELOPE_KEYS, "formatted_output")
    if data["mode"] != "invoke" or not isinstance(data["assessment_ref"], str) or not isinstance(data["requires_human_review"], bool) or not isinstance(data["degradation_reason"], str):
        raise SecurityViolationError("invoke envelope metadata is invalid")
    _validate_brief(data["evidence_brief"], registry)
    for item in _list(data["evidence_map"], "evidence_map"):
        mapping = _exact(item, {"control_id", "source_ids"}, "evidence map")
        if not isinstance(mapping["control_id"], str):
            raise SecurityViolationError("evidence map control id is invalid")
        _validate_source_ids(mapping["source_ids"], registry)
    _validate_current_evidence_basis(
        data["current_evidence_basis"],
        registry,
        data["evidence_brief"]["control_requirements"],
    )
    pins = _exact(data["applied_pins"], {"catalogue_version", "catalogue_content_hash", "perspective_pack_version", "perspective_pack_content_hash", "mapping_pack_version", "mapping_pack_content_hash"}, "applied pins")
    if not all(isinstance(value, str) for value in pins.values()):
        raise SecurityViolationError("applied pins are invalid")


def build_invoke_envelope(state: dict) -> dict:
    brief = deepcopy(state.get("evidence_brief", {}))
    return {
        "evidence_brief": brief,
        "evidence_map": deepcopy(state.get("evidence_map", [])),
        "current_evidence_basis": deepcopy(state.get("current_evidence_basis", [])),
        "applied_pins": {
            "catalogue_version": str(state.get("catalogue_version", "")), "catalogue_content_hash": str(state.get("catalogue_content_hash", "")),
            "perspective_pack_version": str(state.get("perspective_pack_version", "")), "perspective_pack_content_hash": str(state.get("perspective_pack_content_hash", "")),
            "mapping_pack_version": str(state.get("mapping_pack_version", "")), "mapping_pack_content_hash": str(state.get("mapping_pack_content_hash", "")),
        },
        "assessment_ref": str(state.get("assessment_ref", "")),
        "requires_human_review": bool(state.get("requires_human_review", False)),
        "degradation_reason": str(state.get("degradation_reason", "")),
        "mode": "invoke",
    }


class PostProcessNode(FunctionNode):
    """Expose only schema-closed envelopes; no judgement fields can escape."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        from shared.utils.audit_logger import emit_trace_event

        emit_trace_event("output_envelope_started", {}, state)
        mode = state.get("mode")
        if mode == "invoke":
            if state.get("requires_human_review") and not state.get("hitl_allowed", True):
                pending = {"mode": "draft_pending_review", "requires_human_review": True, "degradation_reason": _pending_reason(state.get("degradation_reason", ""))}
                emit_trace_event("pending_review_envelope_created", {}, state)
                return {"formatted_output": pending, "result": pending, "status": AgentStatus.SUCCESS.value}
            envelope = build_invoke_envelope(state)
            validate_invoke_envelope(envelope, set(state.get("source_registry", {})))
            emit_trace_event("invoke_envelope_created", {"assessment_ref": envelope["assessment_ref"]}, state)
            return {"formatted_output": envelope, "result": envelope, "status": AgentStatus.SUCCESS.value}
        if mode == "ingest":
            envelope = {"mode": "ingest", "ingest_result": deepcopy(state.get("ingest_result", {}))}
        elif mode == "feedback":
            envelope = {"mode": "feedback", "feedback_result": deepcopy(state.get("feedback_result", {}))}
        else:
            return {"status": AgentStatus.ERROR.value, "error_log": ["post_process received an unknown mode"]}
        emit_trace_event("noninvoke_envelope_created", {"mode": mode}, state)
        return {"formatted_output": envelope, "result": envelope, "status": AgentStatus.SUCCESS.value}

    def _extra_security_gate_output(self, result: dict) -> dict:
        formatted = result.get("formatted_output")
        if formatted != result.get("result"):
            raise SecurityViolationError("result must equal formatted_output")
        if not isinstance(formatted, dict):
            raise SecurityViolationError("formatted_output must be an object")
        mode = formatted.get("mode")
        if mode == "invoke":
            # The framework hook receives only ``result`` (not State).  Provenance
            # against the private registry was already checked in execute(); here
            # we still revalidate the complete closed shape and internal links.
            validate_invoke_envelope(formatted, _referenced_source_ids(formatted))
        elif mode == "draft_pending_review":
            _exact(formatted, {"mode", "requires_human_review", "degradation_reason"}, "pending review envelope")
        elif mode == "ingest":
            _exact(formatted, {"mode", "ingest_result"}, "ingest envelope")
        elif mode == "feedback":
            _exact(formatted, {"mode", "feedback_result"}, "feedback envelope")
        else:
            raise SecurityViolationError("formatted_output mode is not allowed")
        return result


def _pending_reason(reason: object) -> str:
    return "+".join(part for part in (str(reason) if reason else "", "human_review_required") if part)


def _referenced_source_ids(envelope: dict) -> set[str]:
    ids: set[str] = set()
    brief = envelope.get("evidence_brief", {})
    for requirement in brief.get("control_requirements", []) if isinstance(brief, dict) else []:
        if isinstance(requirement, dict):
            ids.update(source_id for source_id in requirement.get("source_ids", []) if isinstance(source_id, str))
    for mapping in envelope.get("evidence_map", []):
        if isinstance(mapping, dict):
            ids.update(source_id for source_id in mapping.get("source_ids", []) if isinstance(source_id, str))
    for basis in envelope.get("current_evidence_basis", []):
        if isinstance(basis, dict):
            ids.update(source_id for source_id in basis.get("source_ids", []) if isinstance(source_id, str))
    return ids
