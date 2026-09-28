"""実 LLM 1 回転検証のための共有 fixture。

FIN-C2-177 は outer / inner の 2 段構成で、LLM は `config["llm"]` へ遅延注入される
（`src/nodes/workflow_nodes.py` の Stage 3 draft ノード）。汎用ペイロードでは
payload_store / pins / sealed_content が揃わず LLM ノードまで到達しないため、
**tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py の `_agent()` /
`_invoke()` と同じ構築前提・同じ入力**をここに再現する（値を推測で作らない）。

このファイルは検証用の足場であって src/ には一切触れていない。
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from src.services import ScopedKnowledgeBase, TransientPayloadStore

_CONFIG_PATH = pathlib.Path(__file__).parents[1] / "config" / "config.yaml"
_LIVE_EVIDENCE_DOCUMENT = (
    "Record class: continuity exercise log.\n"
    "Record ID: BCP-EX-2026-0314.\n"
    "Vendor category: cloud service provider.\n"
    "Service: hosted payment-processing gateway.\n"
    "Exercise date: 2026-03-14.\n"
    "Scenario: primary processing region unavailable.\n"
    "Action: secondary-region procedure activated.\n"
    "Execution state: completed without rollback.\n"
    "Recovery objective: service restored within 30 minutes.\n"
    "Observed restoration: 18 minutes.\n"
    "Failover threshold: traffic switched within 20 minutes.\n"
    "Observed failover: 12 minutes.\n"
    "Threshold comparison: pass.\n"
    "Checks: health probes completed.\n"
    "Checks: synthetic payment authorization completed.\n"
    "Checks: queued transactions reconciled.\n"
    "Checks: operator notification delivered.\n"
    "Exception: one monitoring alert arrived late.\n"
    "Service transition result: successful.\n"
    "Remediation owner: resilience engineering.\n"
    "Remediation due: 2026-03-28.\n"
    "Manager review date: 2026-03-16.\n"
    "Manager review result: accepted."
)


# PB-7 `_agent()` と同じ構築依存。本番 runtime config はハーネスが基底に敷くので、
# ここでは「基底に無い構築時オブジェクト」だけを重ねる。
@pytest.fixture
def agent_config() -> dict:
    config = yaml.safe_load(_CONFIG_PATH.read_text())
    store = TransientPayloadStore(config["payload_ttl_s"])
    kb = ScopedKnowledgeBase()
    kb.record_assessment(
        tenant_id="tenant",
        org_id="org",
        vendor_id="vendor",
        record={
            "assessment_ref": "prior",
            "control_id": "C1",
            "support_level": "insufficient",
            "claim": "Prior evidence was incomplete.",
            "verification_questions": [],
            "evidence_source_ids": [],
            "assessed_at": "2026-01-01",
            "as_of": {
                "catalogue_version": "1",
                "catalogue_content_hash": "catalogue-hash",
                "requirement_text_hash": "",
                "assessed_at": "2026-01-01",
                "service_scope_fingerprint": "",
                "document_versions": [],
            },
        },
    )
    return {
        "payload_store": store,
        "kb_service": kb,
        "sealed_content": {
            "catalogue": {"controls": [{"control_id": "C1", "requirement_text": "BCP test evidence"}]},
            "perspective": {"rules": [{
                "rule_id": "BCP-CLOUD-1",
                "applies_to": {"catalogue_id": "catalogue-ref", "catalogue_version": "1", "control_ids": ["C1"]},
                "applicability": {"vendor_types": ["cloud"], "service_scope_fingerprints": []},
                "expected_evidence": [{"type": "BCP test record", "required": True}],
                "minimum_sufficiency": "The record identifies the exercise date and outcome.",
                "contradiction_signals": ["failed exercise without remediation"],
                "verification_questions": ["When was the latest recovery exercise completed?"],
                "approval": {"status": "approved"},
            }]},
        },
        "inner_checkpointer": InMemorySaver(),
    }


# PB-7 `_invoke()` と同じ user_input。
@pytest.fixture
def case_payload() -> dict:
    return {"vendor_type": "cloud"}


# PB-7 `_invoke()` と同じ input_context。証跡本文は State に載せず payload_store 側へ
# 預け、State には evidence_payload_ref だけを渡す契約なので、`agent_config` が作った
# **同一インスタンス**の store へ put する必要がある。
@pytest.fixture
def live_input_context(agent_config: dict) -> dict:
    payload_ref = agent_config["payload_store"].put(
        {
            "documents": [
                {
                    "text": _LIVE_EVIDENCE_DOCUMENT,
                    "control_ids": ["C1"],
                    "document_version": "v1",
                    "content_hash": "doc-hash",
                }
            ]
        },
        tenant_id="tenant",
        org_id="org",
        case_ref="case",
    )
    return {
        "tenant_id": "tenant",
        "org_id": "org",
        "entry_mode": "invoke",
        "case_ref": "case",
        "vendor_id": "vendor",
        "evidence_payload_ref": payload_ref,
        "pins": {
            "control_catalogue_ref": "catalogue-ref",
            "catalogue_version": "1",
            "catalogue_content_hash": "catalogue-hash",
            "perspective_pack_ref": "perspective-ref",
            "perspective_pack_version": "1",
            "perspective_pack_content_hash": "perspective-hash",
            "mapping_pack_version": "",
            "mapping_pack_content_hash": "",
        },
        "sealed_content": {
            "catalogue_sealed_ref": "catalogue",
            "perspective_pack_sealed_ref": "perspective",
        },
        "service_scope_fingerprint": "service-scope",
    }
