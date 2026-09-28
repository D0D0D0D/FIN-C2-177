"""TC-01..TC-05, TC-08 and deterministic domain invariants."""

from __future__ import annotations

import json

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import validate_invoke_envelope
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.workflow_nodes import ComparisonNode, EvidenceVerifyNode, RecallNode
from src.schemas.state import State
from src.services import ScopedKnowledgeBase, ScopeError, TransientPayloadStore


def _envelope() -> dict:
    return {
        "evidence_brief": {"control_requirements": [], "evidence_sources": [], "gaps": [], "contradictions": [], "verification_questions": [], "prior_finding": [], "changed_since_last": []},
        "evidence_map": [], "current_evidence_basis": [],
        "applied_pins": {"catalogue_version": "1", "catalogue_content_hash": "h", "perspective_pack_version": "1", "perspective_pack_content_hash": "h2", "mapping_pack_version": "", "mapping_pack_content_hash": ""},
        "assessment_ref": "alphabeticreference", "requires_human_review": False, "degradation_reason": "", "mode": "invoke",
    }


def test_tc01_state_is_typed_dict_and_contains_no_credential_fields() -> None:
    assert issubclass(State, dict)
    assert not {"secret", "token", "credential"} & set(State.__annotations__)


def test_tc02_preprocess_security_violation_blocks_untrusted_scope() -> None:
    with pytest.raises(SecurityViolationError):
        PreProcessNode()._extra_security_gate_input({"user_input": "{}", "input_context": {}})


def test_tc04_scope_comes_from_input_context_not_invocation_context() -> None:
    node = PreProcessNode()
    state = {
        "user_input": json.dumps({"vendor_type": "cloud"}),
        "input_context": {"tenant_id": "tenant", "org_id": "org", "entry_mode": "invoke", "case_ref": "case", "vendor_id": "vendor", "evidence_payload_ref": "letters", "pins": {"control_catalogue_ref": "r", "catalogue_version": "1", "catalogue_content_hash": "h", "perspective_pack_ref": "p", "perspective_pack_version": "1", "perspective_pack_content_hash": "ph"}, "sealed_content": {}},
    }
    result = node.execute(state)
    assert result["tenant_id"] == "tenant"
    assert result["org_id"] == "org"
    assert "tenant_id" not in result["validated_input"]


def test_tc08_verified_external_trust_is_required() -> None:
    result = PreProcessNode()({"caller_trust_level": TrustLevel.ANONYMOUS.value, "correlation_id": "tc08"})
    assert result["status"] == AgentStatus.ERROR.value
    assert "S-1 trust gate denied" in result["error_log"][0]


def test_s3_closed_schema_rejects_forbidden_nested_judgement_key() -> None:
    envelope = _envelope()
    envelope["evidence_brief"]["assessment"] = {"regulatory_compliance_verdict": "yes"}
    with pytest.raises(SecurityViolationError):
        validate_invoke_envelope(envelope)


@pytest.mark.parametrize("bad_level", ["compliant", "approved", ""])
def test_inv4_support_level_is_limited_to_candidate_values(bad_level: str) -> None:
    envelope = _envelope()
    envelope["evidence_brief"]["control_requirements"] = [{"control_id": "C1", "requirement_text": "text", "support_level": bad_level, "claim": "claim", "source_ids": []}]
    with pytest.raises(SecurityViolationError):
        validate_invoke_envelope(envelope)


def test_payload_reference_is_alphabetic_single_use_and_scope_bound() -> None:
    store = TransientPayloadStore(ttl_s=10)
    ref = store.put({"documents": []}, tenant_id="t", org_id="o", case_ref="c")
    assert ref.isalpha()
    with pytest.raises(Exception):
        store.resolve(ref, tenant_id="t", org_id="wrong", case_ref="c")
    assert store.resolve(ref, tenant_id="t", org_id="o", case_ref="c") == {"documents": []}
    with pytest.raises(Exception):
        store.resolve(ref, tenant_id="t", org_id="o", case_ref="c")


def test_inv6_and_inv11_scoped_retrieval_requires_both_boundaries() -> None:
    kb = ScopedKnowledgeBase()
    kb.record_case_evidence(tenant_id="t", org_id="o", case_ref="case-a", record={"source_id": "sourcea"})
    kb.record_case_evidence(tenant_id="t", org_id="other", case_ref="case-a", record={"source_id": "sourceb"})
    assert kb.find_case_evidence(tenant_id="t", org_id="o", case_ref="case-a", top_k=8) == [{"source_id": "sourcea", "case_ref": "case-a"}]
    with pytest.raises(ScopeError):
        kb.find_case_evidence(tenant_id="", org_id="o", case_ref="case-a", top_k=8)


def test_recall_accumulates_an_existing_degradation_reason() -> None:
    result = RecallNode().execute({"degradation_reason": "earlier_degradation"})
    assert result["degradation_reason"] == "earlier_degradation+kb_unavailable"


def test_inv17_comparison_does_not_overwrite_current_assessment() -> None:
    node = ComparisonNode({"sealed_content": {"cat": {"controls": [{"control_id": "C1", "requirement_text_hash": "same"}]}}})
    current = {"control_requirements": [{"control_id": "C1", "requirement_text": "text", "support_level": "supported", "claim": "current", "source_ids": []}], "evidence_sources": [], "gaps": [], "contradictions": [], "verification_questions": [], "prior_finding": [], "changed_since_last": []}
    result = node.execute({"evidence_brief": current, "prior_findings": [{"control_id": "C1", "support_level": "insufficient", "claim": "old", "verification_questions": [], "evidence_source_ids": [], "as_of": {"catalogue_version": "1", "catalogue_content_hash": "h", "requirement_text_hash": "same", "assessed_at": "x", "service_scope_fingerprint": "scope", "document_versions": []}}], "sealed_content": {"catalogue_sealed_ref": "cat"}, "service_scope_fingerprint": "scope"})
    assert result["evidence_brief"]["control_requirements"] == current["control_requirements"]


@pytest.mark.parametrize("basis", [[], [{"control_id": "C1", "source_ids": ["old"], "spans": [{"start": 0, "end": 1}], "observation": "x"}], [{"control_id": "C1", "source_ids": ["sourcea"], "spans": [{"start": 0, "end": 99}], "observation": "x"}]])
def test_inv12_stage4_rejects_missing_foreign_or_out_of_range_basis(basis: list) -> None:
    node = EvidenceVerifyNode()
    state = {"evidence_brief": {"control_requirements": [{"control_id": "C1", "support_level": "supported"}], "prior_finding": [], "changed_since_last": []}, "source_registry": {"sourcea": {"assessment_ref": "current", "eligible": True, "control_ids": ["C1"], "span_end": 10}}, "current_evidence_basis": basis, "assessment_ref": "current", "hitl_allowed": False}
    result = node.execute(state)
    assert result["status"] == AgentStatus.ERROR.value


def _basis(control_id: str = "C1", *, count: int = 1) -> dict:
    return {
        "control_id": control_id,
        "source_ids": ["sourcea"] * count,
        "spans": [{"start": 0, "end": 1}] * count,
        "observation": "x",
    }


@pytest.mark.parametrize(
    ("requirements", "basis"),
    [
        ([{"control_id": "C1", "support_level": "undetermined"}], [_basis()]),
        ([{"control_id": "C1", "support_level": "undetermined"}], [_basis("C2")]),
        ([{"control_id": "C1", "support_level": "supported"}], [_basis(), _basis()]),
        ([{"control_id": "C1", "support_level": "supported"}], [_basis(count=101)]),
        ([{"control_id": "C1", "support_level": "supported"}], [{**_basis(), "spans": [{"start": False, "end": True}]}]),
    ],
    ids=["undetermined", "orphan", "duplicate", "span-cap", "boolean-span"],
)
def test_stage4_rejects_every_disallowed_basis_channel(requirements: list[dict], basis: list[dict]) -> None:
    state = {
        "evidence_brief": {"control_requirements": requirements, "prior_finding": [], "changed_since_last": []},
        "source_registry": {
            "sourcea": {
                "assessment_ref": "current",
                "eligible": True,
                "control_ids": ["C1", "C2"],
                "span_end": 10,
            }
        },
        "current_evidence_basis": basis,
        "assessment_ref": "current",
        "hitl_allowed": False,
    }

    result = EvidenceVerifyNode().execute(state)

    assert result["status"] == AgentStatus.ERROR.value


@pytest.mark.parametrize(
    ("requirements", "basis"),
    [
        ([{"control_id": "C1", "requirement_text": "text", "support_level": "undetermined", "claim": "x", "source_ids": []}], [_basis()]),
        ([], [_basis("C2")]),
        ([{"control_id": "C1", "requirement_text": "text", "support_level": "supported", "claim": "x", "source_ids": ["sourcea"]}], [_basis(), _basis()]),
        ([{"control_id": "C1", "requirement_text": "text", "support_level": "supported", "claim": "x", "source_ids": ["sourcea"]}], [_basis(count=101)]),
        ([{"control_id": "C1", "requirement_text": "text", "support_level": "supported", "claim": "x", "source_ids": ["sourcea"]}], [{**_basis(), "spans": [{"start": False, "end": True}]}]),
    ],
    ids=["undetermined", "orphan", "duplicate", "span-cap", "boolean-span"],
)
def test_s3_rejects_every_disallowed_basis_channel(requirements: list[dict], basis: list[dict]) -> None:
    envelope = _envelope()
    envelope["evidence_brief"]["control_requirements"] = requirements
    envelope["current_evidence_basis"] = basis
    envelope["evidence_map"] = [
        {"control_id": requirement["control_id"], "source_ids": requirement["source_ids"]}
        for requirement in requirements
    ]

    with pytest.raises(SecurityViolationError):
        validate_invoke_envelope(envelope, {"sourcea"})
