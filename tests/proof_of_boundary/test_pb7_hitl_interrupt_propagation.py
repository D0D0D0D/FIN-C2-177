"""PB-7: compiled Cat-2 HITL propagation and the no-deadlock guard."""

from __future__ import annotations

import json
import pathlib
import warnings

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from src.graph.graph import OutsourcingDueDiligenceBriefAgent
from src.services import ScopedKnowledgeBase, TransientPayloadStore

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true.

    An absent or unreadable config.yaml warns instead of skipping silently —
    either case is ambiguous (never shipped / broken
    vs. genuinely non-HITL) and should not look like a clean auto-waiver.
    """
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled. "
            "If this template calls interrupt(), ship config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    try:
        import yaml  # PyYAML is supplied by the test environment.

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped without "
            "verifying hitl.enabled. If this template calls interrupt(), fix config/config.yaml "
            "before release.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped "
            "without verifying hitl.enabled. If this template calls interrupt(), fix "
            "config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)


class _DraftLLM(BaseLLM):
    def complete(self, messages: list) -> dict:
        return {"content": json.dumps({
            "control_requirements": [{
                "control_id": "C1", "requirement_text": "BCP test evidence", "support_level": "supported",
                "claim": "Current BCP test record is available.", "source_ids": ["sourcea"],
            }],
            "current_evidence_basis": [{
                "control_id": "C1", "source_ids": ["sourcea"], "spans": [{"start": 0, "end": 10}],
                "observation": "The current submission records a BCP test.",
            }],
        })}

    def stream(self, messages: list):
        return iter(())

    def bind_tools(self, tools: list):
        return self


def _agent() -> tuple[OutsourcingDueDiligenceBriefAgent, TransientPayloadStore]:
    config = yaml.safe_load(_CONFIG_PATH.read_text())
    payload_store = TransientPayloadStore(config["payload_ttl_s"])
    kb = ScopedKnowledgeBase()
    kb.record_assessment(tenant_id="tenant", org_id="org", vendor_id="vendor", record={
        "assessment_ref": "prior", "control_id": "C1", "support_level": "insufficient", "claim": "Prior evidence was incomplete.",
        "verification_questions": [], "evidence_source_ids": [], "assessed_at": "2026-01-01",
        "as_of": {"catalogue_version": "1", "catalogue_content_hash": "catalogue-hash", "requirement_text_hash": "", "assessed_at": "2026-01-01", "service_scope_fingerprint": "", "document_versions": []},
    })
    config.update({
        "payload_store": payload_store, "kb_service": kb, "llm": _DraftLLM(),
        "sealed_content": {
            "catalogue": {"controls": [{"control_id": "C1", "requirement_text": "BCP test evidence"}]},
            "perspective": {"rules": [{
                "rule_id": "BCP-CLOUD-1",
                "applies_to": {"catalogue_id": "catalogue-ref", "catalogue_version": "1", "control_ids": ["C1"]},
                "applicability": {"vendor_types": ["cloud"], "service_scope_fingerprints": []},
                "expected_evidence": [{"type": "BCP test record", "required": True}],
                "minimum_sufficiency": "The record identifies the exercise and outcome.",
                "contradiction_signals": ["failed exercise without remediation"],
                "verification_questions": ["When was the latest exercise completed?"],
                "approval": {"status": "approved"},
            }]},
        },
        "inner_checkpointer": InMemorySaver(),
    })
    graph = OutsourcingDueDiligenceBriefAgent(config=config)
    graph.compile(checkpointer=InMemorySaver())
    return graph, payload_store


def _invoke(graph: OutsourcingDueDiligenceBriefAgent, store: TransientPayloadStore, *, hitl_allowed: bool, thread_id: str = "hitlthread") -> dict:
    payload_ref = store.put({"documents": [{"text": "BCP test record confirms recovery exercise.", "control_ids": ["C1"], "document_version": "v1", "content_hash": "doc-hash"}]}, tenant_id="tenant", org_id="org", case_ref="case")
    return graph.invoke(json.dumps({"vendor_type": "cloud"}), ctx=InvocationContext(session_id="session", thread_id=thread_id, caller_id="caller", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, hitl_allowed=hitl_allowed), input_context={
        "tenant_id": "tenant", "org_id": "org", "entry_mode": "invoke", "case_ref": "case", "vendor_id": "vendor", "evidence_payload_ref": payload_ref,
        "pins": {"control_catalogue_ref": "catalogue-ref", "catalogue_version": "1", "catalogue_content_hash": "catalogue-hash", "perspective_pack_ref": "perspective-ref", "perspective_pack_version": "1", "perspective_pack_content_hash": "perspective-hash", "mapping_pack_version": "", "mapping_pack_content_hash": ""},
        "sealed_content": {"catalogue_sealed_ref": "catalogue", "perspective_pack_sealed_ref": "perspective"},
    })


def test_pb7_hitl_interrupt_propagates_through_compiled_graph() -> None:
    graph, store = _agent()
    result = _invoke(graph, store, hitl_allowed=True)
    assert result["status"] == "awaiting_human"
    assert result["status"] != "error"
    assert result["thread_id"] == "hitlthread"
    assert result["hitl_metadata"]["reason"] == "human_review_required"


def test_pb7_hitl_allowed_false_skips_interrupt_and_returns_pending_shape() -> None:
    graph, store = _agent()
    result = _invoke(graph, store, hitl_allowed=False)
    assert result["status"] == "success"
    assert result["output"]["mode"] == "draft_pending_review"
    assert result["output"]["requires_human_review"] is True


def test_pb7_resume_reaches_success_without_replaying_preprocess() -> None:
    graph, store = _agent()
    initial = _invoke(graph, store, hitl_allowed=True)
    resumed = graph.resume(initial["thread_id"], {"action": "approve"})
    assert resumed["status"] == "success"
    assert resumed["output"]["mode"] == "invoke"
