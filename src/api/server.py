"""HTTP adapter: authentication, pin resolution, payload isolation, and resume ownership."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.graph.graph import OutsourcingDueDiligenceBriefAgent
from src.services.kb_service import ScopedKnowledgeBase
from src.services.payload_store import TransientPayloadStore
from src.services.pin_resolver import PinResolver

_ROOT = Path(__file__).resolve().parents[2]


def _load_runtime_config() -> dict:
    path = _ROOT / "config" / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("config/config.yaml must be valid YAML") from exc
    if not isinstance(data, dict):
        raise RuntimeError("config/config.yaml must contain a mapping")
    return data


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: dict
    evidence: dict
    session_id: str = ""


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: dict = Field(default_factory=dict)
    evidence: dict
    session_id: str = ""


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: dict
    session_id: str = ""


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    feedback: dict | str


_runtime = _load_runtime_config()
_sealed_content: dict[str, dict] = {}
_runtime["payload_store"] = TransientPayloadStore(int(_runtime["payload_ttl_s"]))
_runtime["kb_service"] = ScopedKnowledgeBase()
_runtime["sealed_content"] = _sealed_content
_runtime["pin_resolver"] = PinResolver(_sealed_content)
_runtime["inner_checkpointer"] = InMemorySaver()
agent = OutsourcingDueDiligenceBriefAgent(config=_runtime)
agent.compile(checkpointer=InMemorySaver())
agent.provision_secrets(secrets_factory(namespace="fin-c2-177", agent_name="FinancialOutsourcingDueDiligenceEvidenceBriefAgent"))

app = FastAPI(title="Financial Outsourcing Due-Diligence Evidence Brief Agent")


def _middleware_has_identity(request: Request) -> bool:
    return any(hasattr(request.state, attr) for attr in ("caller_id", "tenant_id", "org_id", "trust_level"))


def _token_context(request: Request) -> dict | None:
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    supplied = request.headers.get("authorization", "")
    if not expected or not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
        return None
    digest = hashlib.sha256(expected.encode()).hexdigest()[:12]
    return {
        "caller_id": f"invoke-token:{digest}", "tenant_id": "standalone-invoke", "org_id": "standalone-org",
        "case_ref": "standalone-case", "vendor_id": "standalone-vendor", "trust_level": TrustLevel.VERIFIED_EXTERNAL,
    }


def _authenticated_context(request: Request, entry_mode: str, *, allow_standalone_token: bool) -> dict:
    if _middleware_has_identity(request):
        trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
        if isinstance(trust, str):
            try:
                trust = TrustLevel(trust)
            except ValueError:
                trust = TrustLevel.ANONYMOUS
        values = {key: getattr(request.state, key, "") for key in ("caller_id", "tenant_id", "org_id", "case_ref", "vendor_id")}
        if trust not in (TrustLevel.VERIFIED_EXTERNAL, TrustLevel.INTERNAL) or not all(values[key] for key in ("caller_id", "tenant_id", "org_id", "case_ref")):
            raise HTTPException(status_code=403, detail="Authentication is required.")
        if str(values["caller_id"]).startswith("invoke-token:"):
            raise HTTPException(status_code=403, detail="Authentication is required.")
        return {**values, "trust_level": trust}
    if allow_standalone_token:
        token_context = _token_context(request)
        if token_context is not None:
            return token_context
    raise HTTPException(status_code=403, detail="Authentication is required.")


def _resolved_pins(body: dict) -> tuple[dict, dict]:
    resolver = agent.config["pin_resolver"]
    catalogue, catalogue_sealed_ref = resolver.resolve(body, "control_catalogue")
    perspective, perspective_sealed_ref = resolver.resolve(body, "perspective_pack")
    return {
        "control_catalogue_ref": catalogue["ref"], "catalogue_version": catalogue["version"], "catalogue_content_hash": catalogue["content_hash"],
        "perspective_pack_ref": perspective["ref"], "perspective_pack_version": perspective["version"], "perspective_pack_content_hash": perspective["content_hash"],
        "mapping_pack_version": str(body.get("mapping_pack_version", "")), "mapping_pack_content_hash": str(body.get("mapping_pack_content_hash", "")),
    }, {"catalogue_sealed_ref": catalogue_sealed_ref, "perspective_pack_sealed_ref": perspective_sealed_ref}


def _invoke(entry_mode: str, body: dict, evidence: dict | None, session_id: str, request: Request) -> dict:
    context = _authenticated_context(request, entry_mode, allow_standalone_token=entry_mode == "invoke")
    input_context = {**context, "entry_mode": entry_mode}
    if evidence is not None:
        input_context["evidence_payload_ref"] = agent.config["payload_store"].put(evidence, tenant_id=context["tenant_id"], org_id=context["org_id"], case_ref=context["case_ref"])
    if entry_mode == "invoke":
        pins, sealed_content = _resolved_pins(body)
        input_context.update({"pins": pins, "sealed_content": sealed_content})
    if entry_mode == "feedback":
        assessment_ref = getattr(request.state, "assessment_ref", "")
        if not assessment_ref:
            raise HTTPException(status_code=403, detail="Authentication is required.")
        input_context["assessment_ref"] = assessment_ref
    ctx = InvocationContext(session_id=session_id, caller_id=context["caller_id"], caller_trust_level=context["trust_level"], hitl_allowed=True)
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(json.dumps(body, ensure_ascii=False), session_id=session_id, ctx=ctx, input_context=input_context)


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict:
    return _invoke("invoke", req.input, req.evidence, req.session_id, request)


@app.post("/ingest")
async def ingest(req: IngestRequest, request: Request) -> dict:
    return _invoke("ingest", req.input, req.evidence, req.session_id, request)


@app.post("/feedback")
async def feedback(req: FeedbackRequest, request: Request) -> dict:
    return _invoke("feedback", req.input, None, req.session_id, request)


def _checkpoint_owned(thread_id: str, caller_id: str, tenant_id: str) -> bool:
    snapshot = agent._compiled.get_state({"configurable": {"thread_id": thread_id}}) if agent._compiled else None
    values = getattr(snapshot, "values", None) or {}
    return values.get("caller_id") == caller_id and values.get("tenant_id") == tenant_id


@app.post("/resume")
async def resume(req: ResumeRequest, request: Request) -> dict:
    context = _authenticated_context(request, "resume", allow_standalone_token=False)
    if not _checkpoint_owned(req.thread_id, context["caller_id"], context["tenant_id"]):
        raise HTTPException(status_code=403, detail="Authentication is required.")
    with bound_secrets(agent._secrets_provider):
        return agent.resume(req.thread_id, req.feedback)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "FinancialOutsourcingDueDiligenceEvidenceBriefAgent"}
