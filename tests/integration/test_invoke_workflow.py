"""Integration coverage for the full outer/inner invoke workflow."""

from __future__ import annotations

import json
from base64 import b64encode

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import OutsourcingDueDiligenceBriefAgent
from src.services import ScopedKnowledgeBase, TransientPayloadStore

_PERSPECTIVE_RULE = {
    "rule_id": "BCP-CLOUD-1",
    "applies_to": {
        "catalogue_id": "catalogue-ref",
        "catalogue_version": "1",
        "control_ids": ["BCP-1", "BCP-2"],
    },
    "applicability": {"vendor_types": ["cloud"], "service_scope_fingerprints": []},
    "expected_evidence": [{"type": "recovery exercise record", "required": True}],
    "minimum_sufficiency": "The record identifies the exercise and its outcome.",
    "contradiction_signals": ["failed exercise without remediation"],
    "verification_questions": ["What was the latest exercise outcome?"],
    "approval": {"status": "approved"},
}


def _graph(
    *,
    llm: object | None = None,
    controls: list[dict] | None = None,
    with_kb: bool = True,
) -> tuple[OutsourcingDueDiligenceBriefAgent, TransientPayloadStore]:
    store = TransientPayloadStore()
    controls = controls or [{"control_id": "BCP-1", "requirement_text": "Business continuity evidence", "requirement_text_hash": "req-hash"}]
    config = {
        "max_retry": 3, "memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 8},
        "payload_store": store, "inner_checkpointer": InMemorySaver(),
        "sealed_content": {"catalogue": {"controls": controls}, "perspective": {"rules": [_PERSPECTIVE_RULE]}},
        "evidence_confidence_threshold": 0.8,
    }
    if with_kb:
        config["kb_service"] = ScopedKnowledgeBase()
    if llm is not None:
        config["llm"] = llm
    graph = OutsourcingDueDiligenceBriefAgent(config=config)
    graph.compile(checkpointer=InMemorySaver())
    return graph, store


def _context(store: TransientPayloadStore, body: str, *, control_ids: list[str] | None = None) -> dict:
    payload_ref = store.put({"documents": [{"text": body, "control_ids": control_ids or ["BCP-1"], "document_version": "1", "content_hash": "document-hash"}]}, tenant_id="tenant", org_id="org", case_ref="case")
    return {
        "tenant_id": "tenant", "org_id": "org", "entry_mode": "invoke", "case_ref": "case", "vendor_id": "vendor", "evidence_payload_ref": payload_ref,
        "pins": {"control_catalogue_ref": "catalogue-ref", "catalogue_version": "1", "catalogue_content_hash": "catalogue-hash", "perspective_pack_ref": "perspective-ref", "perspective_pack_version": "1", "perspective_pack_content_hash": "perspective-hash", "mapping_pack_version": "", "mapping_pack_content_hash": ""},
        "sealed_content": {"catalogue_sealed_ref": "catalogue", "perspective_pack_sealed_ref": "perspective"},
        "service_scope_fingerprint": "service-scope",
    }


def _invoke(
    graph: OutsourcingDueDiligenceBriefAgent,
    store: TransientPayloadStore,
    body: str,
    *,
    thread_id: str,
    control_ids: list[str] | None = None,
) -> dict:
    return graph.invoke(
        json.dumps({"vendor_type": "cloud"}),
        ctx=InvocationContext(
            session_id="session", thread_id=thread_id, caller_id="caller",
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, hitl_allowed=False,
        ),
        input_context=_context(store, body, control_ids=control_ids),
    )


def _all_checkpoint_text(graph: OutsourcingDueDiligenceBriefAgent, thread_id: str) -> str:
    compiled_graphs = [graph._compiled]
    inner = graph._nodes["main"]._subgraph
    if inner is not None:
        compiled_graphs.append(inner._compiled)
    values = [
        snapshot.values
        for compiled in compiled_graphs
        if compiled is not None
        for snapshot in compiled.get_state_history({"configurable": {"thread_id": thread_id}})
    ]
    return json.dumps(values, ensure_ascii=False, default=str)


def test_invoke_fallback_emits_closed_envelope_and_never_checkpoints_evidence_body() -> None:
    graph, store = _graph()
    sentinel = "commercially-sensitive-evidence-sentinel"
    result = graph.invoke(json.dumps({"vendor_type": "cloud"}), ctx=InvocationContext(session_id="session", thread_id="thread", caller_id="caller", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, hitl_allowed=False), input_context=_context(store, sentinel))
    assert result["status"] == "success"
    output = result["output"]
    assert output["mode"] == "invoke"
    assert set(output) == {"evidence_brief", "evidence_map", "current_evidence_basis", "applied_pins", "assessment_ref", "requires_human_review", "degradation_reason", "mode"}
    snapshot = graph._compiled.get_state({"configurable": {"thread_id": "thread"}})
    assert sentinel not in json.dumps(snapshot.values, default=str)


def test_invoke_pin_and_scope_errors_fail_closed_before_inner_data_access() -> None:
    graph, store = _graph()
    context = _context(store, "evidence")
    context.pop("org_id")
    result = graph.invoke(json.dumps({"vendor_type": "cloud"}), ctx=InvocationContext(session_id="session", thread_id="thread-two", caller_id="caller", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL), input_context=context)
    assert result["status"] == "error"
    assert result["output"] is None


class _AdversarialDraftLLM:
    """Produce a schema-shaped draft carrying one selected Stage 3a bypass."""

    def __init__(self, bypass: str) -> None:
        self.bypass = bypass

    def complete(self, messages: list) -> dict:
        trusted = json.loads(messages[0]["content"])["approved_assessment_configuration"]
        submission = json.loads(messages[1]["content"])["vendor_submission"]
        body = submission["sources"][0]["text"] if submission.get("sources") else ""
        requirements = [{
            "control_id": control["control_id"],
            "requirement_text": "model-supplied requirement",
            "support_level": "supported",
            "claim": "A recovery exercise is recorded.",
            "source_ids": ["sourcea"],
        } for control in trusted["controls"]]
        evidence_basis = [{
            "control_id": control["control_id"],
            "source_ids": ["sourcea"],
            "spans": [{"start": 0, "end": min(5, len(body))}],
            "observation": "The submission records a recovery exercise.",
        } for control in trusted["controls"]]
        draft = {"control_requirements": requirements, "current_evidence_basis": evidence_basis}

        if self.bypass == "echo":
            requirements[0]["claim"] = body
            evidence_basis[0]["observation"] = body
        elif self.bypass == "alternate_field":
            requirements[0]["requirement_text"] = body
        elif self.bypass == "short_body":
            requirements[0]["claim"] = body
        elif self.bypass == "split_claims":
            split_at = len(body) // 2
            requirements[0]["claim"] = body[:split_at]
            requirements[1]["claim"] = body[split_at:]
        elif self.bypass == "encoded_body":
            requirements[0]["claim"] = b64encode(body.encode("utf-8")).decode("ascii")
        elif self.bypass == "unknown_key":
            requirements[0]["unexpected_model_field"] = body
        elif self.bypass == "unknown_control":
            requirements[0]["control_id"] = "MODEL-NOT-IN-CATALOGUE"
            evidence_basis[0]["control_id"] = "MODEL-NOT-IN-CATALOGUE"
        return {"content": json.dumps(draft)}

    def stream(self, messages: list):
        return iter(())

    def bind_tools(self, tools: list):
        return self


def test_body_echoing_model_cannot_place_evidence_into_the_checkpoint_or_output() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("echo"))
    sentinel = "commercially-sensitive-evidence-sentinel"
    result = _invoke(graph, store, f"{sentinel} recovery exercise completed.", thread_id="echo-thread")
    assert sentinel not in json.dumps(result, default=str)
    assert sentinel not in _all_checkpoint_text(graph, "echo-thread")
    # The draft is refused, not silently truncated — a half sentence would change the claim.
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"


def test_model_requirement_text_is_replaced_from_the_sealed_catalogue() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("alternate_field"))
    body = "alternate-field-sensitive-evidence-" + ("x" * 64)
    result = _invoke(graph, store, body, thread_id="alternate-field-thread")
    assert result["status"] == "success"
    assert result["output"]["degradation_reason"] == ""
    requirement = result["output"]["evidence_brief"]["control_requirements"][0]
    assert requirement["requirement_text"] == "Business continuity evidence"
    assert body not in json.dumps(result, default=str)
    assert body not in _all_checkpoint_text(graph, "alternate-field-thread")


def test_short_body_echo_is_rejected_before_any_compiled_graph_checkpoint() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("short_body"))
    body = "short-secret-1234"
    result = _invoke(graph, store, body, thread_id="short-body-thread")
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"
    assert body not in json.dumps(result, default=str)
    assert body not in _all_checkpoint_text(graph, "short-body-thread")


def test_body_split_across_claims_is_rejected_before_checkpointing() -> None:
    controls = [
        {"control_id": "BCP-1", "requirement_text": "Business continuity evidence"},
        {"control_id": "BCP-2", "requirement_text": "Recovery exercise evidence"},
    ]
    graph, store = _graph(llm=_AdversarialDraftLLM("split_claims"), controls=controls)
    body = ("A" * 40) + ("B" * 40)
    result = _invoke(
        graph, store, body, thread_id="split-claims-thread", control_ids=["BCP-1", "BCP-2"]
    )
    checkpoints = _all_checkpoint_text(graph, "split-claims-thread")
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"
    assert ("A" * 40) not in json.dumps(result, default=str)
    assert ("B" * 40) not in json.dumps(result, default=str)
    assert ("A" * 40) not in checkpoints
    assert ("B" * 40) not in checkpoints


def test_base64_encoded_body_is_rejected_before_checkpointing() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("encoded_body"))
    body = "encoded-sensitive-evidence-body-with-unique-commercial-terms"
    encoded = b64encode(body.encode("utf-8")).decode("ascii")
    result = _invoke(graph, store, body, thread_id="encoded-body-thread")
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"
    assert encoded not in json.dumps(result, default=str)
    assert encoded not in _all_checkpoint_text(graph, "encoded-body-thread")


def test_unknown_model_key_rejects_the_whole_draft_before_checkpointing() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("unknown_key"))
    marker = "unknown-key-sensitive-body"
    result = _invoke(graph, store, marker, thread_id="unknown-key-thread")
    checkpoints = _all_checkpoint_text(graph, "unknown-key-thread")
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"
    assert "unexpected_model_field" not in json.dumps(result, default=str)
    assert marker not in json.dumps(result, default=str)
    assert "unexpected_model_field" not in checkpoints
    assert marker not in checkpoints


def test_control_id_outside_sealed_catalogue_rejects_the_whole_draft() -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM("unknown_control"))
    result = _invoke(graph, store, "ordinary evidence body", thread_id="unknown-control-thread")
    assert result["output"]["degradation_reason"] == "llm_draft_rejected"
    assert "MODEL-NOT-IN-CATALOGUE" not in json.dumps(result, default=str)
    assert "MODEL-NOT-IN-CATALOGUE" not in _all_checkpoint_text(graph, "unknown-control-thread")


@pytest.mark.parametrize(
    ("bypass", "expected_reason"),
    [
        ("valid", "kb_unavailable"),
        ("unknown_key", "kb_unavailable+llm_draft_rejected"),
    ],
)
def test_kb_degradation_survives_stage3a_and_s3_envelope_validation(
    bypass: str,
    expected_reason: str,
) -> None:
    graph, store = _graph(llm=_AdversarialDraftLLM(bypass), with_kb=False)

    result = _invoke(graph, store, "recovery exercise completed", thread_id=f"kb-degraded-{bypass}")

    assert result["status"] == "success"
    assert result["output"]["degradation_reason"] == expected_reason
