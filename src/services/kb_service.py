"""Scoped in-memory repository seam used by the domain workflow and tests."""

from __future__ import annotations

from typing import Any


class ScopeError(ValueError):
    pass


class ScopedKnowledgeBase:
    """Safe projections only; every query requires tenant and organisation."""

    def __init__(self) -> None:
        self._case_records: list[dict[str, Any]] = []
        self._prior_records: list[dict[str, Any]] = []
        self._feedback_events: list[dict[str, Any]] = []
        self._perspective_candidates: list[dict[str, Any]] = []

    @staticmethod
    def _scope(tenant_id: str, org_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id or not isinstance(org_id, str) or not org_id:
            raise ScopeError("tenant_id and org_id are required")

    def record_case_evidence(self, *, tenant_id: str, org_id: str, case_ref: str, record: dict[str, Any]) -> None:
        self._scope(tenant_id, org_id)
        if not case_ref:
            raise ScopeError("case_ref is required")
        self._case_records.append({"tenant_id": tenant_id, "org_id": org_id, "case_ref": case_ref, **record})

    def record_assessment(self, *, tenant_id: str, org_id: str, vendor_id: str, record: dict[str, Any]) -> None:
        self._scope(tenant_id, org_id)
        if not vendor_id:
            raise ScopeError("vendor_id is required")
        self._prior_records.append({"tenant_id": tenant_id, "org_id": org_id, "vendor_id": vendor_id, **record})

    def find_case_evidence(self, *, tenant_id: str, org_id: str, case_ref: str, top_k: int) -> list[dict[str, Any]]:
        self._scope(tenant_id, org_id)
        if not case_ref:
            raise ScopeError("case_ref is required")
        return [
            self._safe_projection(record)
            for record in self._case_records
            if (record["tenant_id"], record["org_id"], record["case_ref"]) == (tenant_id, org_id, case_ref)
        ][:top_k]

    def find_prior_findings(self, *, tenant_id: str, org_id: str, vendor_id: str, top_k: int) -> list[dict[str, Any]]:
        self._scope(tenant_id, org_id)
        if not vendor_id:
            raise ScopeError("vendor_id is required")
        records = [
            self._safe_projection(record)
            for record in self._prior_records
            if (record["tenant_id"], record["org_id"], record["vendor_id"]) == (tenant_id, org_id, vendor_id)
        ]
        return sorted(records, key=lambda item: (item.get("assessed_at", ""), item.get("assessment_ref", "")), reverse=True)[:top_k]

    def append_feedback(self, *, tenant_id: str, org_id: str, assessment_ref: str, event: dict[str, Any]) -> None:
        self._scope(tenant_id, org_id)
        if not assessment_ref:
            raise ScopeError("assessment_ref is required")
        self._feedback_events.append({"tenant_id": tenant_id, "org_id": org_id, "assessment_ref": assessment_ref, **event})
        candidate = event.get("perspective_candidate")
        if isinstance(candidate, dict):
            self._perspective_candidates.append({"status": "proposed", **candidate})

    @staticmethod
    def _safe_projection(record: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "source_id", "assessment_ref", "control_id", "support_level", "claim", "verification_questions",
            "evidence_source_ids", "as_of", "assessed_at", "requirement_text_hash", "service_scope_fingerprint",
            "document_versions", "case_ref", "vendor_id", "provenance",
        }
        return {key: value for key, value in record.items() if key in allowed}
