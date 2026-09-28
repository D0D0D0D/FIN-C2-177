"""JSON-serialisable state contract for FIN-C2-177."""

from typing import NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state.

    Add only fields specific to this agent below.
    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    mode: str
    tenant_id: str
    org_id: str
    case_ref: str
    vendor_id: str
    assessment_ref: str
    evidence_payload_ref: str
    # Handle to this invocation's sanitised evidence text, held in the transient
    # payload store so Stage 3a can read the body WITHOUT it ever entering State
    # (and therefore never reaching a checkpoint). Never the body itself.
    evidence_text_ref: NotRequired[str]
    control_catalogue_ref: str
    catalogue_version: str
    catalogue_content_hash: str
    perspective_pack_ref: str
    perspective_pack_version: str
    perspective_pack_content_hash: str
    mapping_pack_version: str
    mapping_pack_content_hash: str
    sealed_content: dict
    service_scope_fingerprint: str
    evidence_confidence: float
    vendor_type: str
    source_registry: dict
    docs_present: NotRequired[dict]
    format_check: NotRequired[dict]
    prior_findings: NotRequired[list]
    current_evidence_basis: NotRequired[list]
    changed_since_last: NotRequired[list]
    case_evidence_hits: NotRequired[list]
    evidence_brief: NotRequired[dict]
    evidence_map: NotRequired[list]
    gaps: NotRequired[list]
    contradictions: NotRequired[list]
    requires_human_review: NotRequired[bool]
    degradation_reason: NotRequired[str]
    ingest_result: NotRequired[dict]
    feedback_result: NotRequired[dict]
    review_round: NotRequired[int]
    review_outcome: NotRequired[str]
