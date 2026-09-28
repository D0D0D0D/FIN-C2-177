"""Contract: Stage 3a receives the current evidence, and never leaks it into State.

🔴 なぜ要るか（2026-08-19 実 LLM 実測）:
  docs/02_design.md §8 は Stage 3a に「統制表・観点表（版固定）＋**今回証跡のみ**」を渡すと
  定めているが、実装は `source_id` の一覧しか渡していなかった。実 LLM は当てはめる対象が
  無いため形の合わない応答を返し、毎回 `llm_fallback` へ縮退していた
  （ユニット/統合テストの fake は完成形 JSON を返すので緑のまま）。

  ここでは「プロンプトに証跡本文と出力契約が入っていること」「本文が State に載らないこと」
  「証跡を読めなかったときに黙って undetermined を返さないこと」を固定する。
"""

from __future__ import annotations

import json

import pytest

from src.nodes.workflow_nodes import (
    _SUPPORT_LEVELS,
    _VERBATIM_GUARD_CHARS,
    CurrentAssessmentNode,
    DeterministicCheckNode,
)
from src.services import TransientPayloadStore

_SENTINEL = "commercially-sensitive-evidence-sentinel"
_PERSPECTIVE_RULE = {
    "rule_id": "BCP-CLOUD-1",
    "applies_to": {"catalogue_id": "catalogue-ref", "catalogue_version": "1", "control_ids": ["C1"]},
    "applicability": {"vendor_types": ["cloud"], "service_scope_fingerprints": []},
    "expected_evidence": [{"type": "BCP test record", "required": True}],
    "minimum_sufficiency": "The record identifies the exercise date and outcome.",
    "contradiction_signals": ["exercise failed without remediation"],
    "verification_questions": ["When was the latest recovery exercise completed?"],
    "approval": {"status": "approved"},
}
_OTHER_VENDOR_RULE = {
    **_PERSPECTIVE_RULE,
    "rule_id": "BCP-ONPREM-1",
    "applicability": {"vendor_types": ["on-premises"], "service_scope_fingerprints": []},
}
_OTHER_CATALOGUE_RULE = {
    **_PERSPECTIVE_RULE,
    "rule_id": "BCP-OLD-CATALOGUE-1",
    "applies_to": {"catalogue_id": "catalogue-ref", "catalogue_version": "0", "control_ids": ["C1"]},
}


class _RecordingLLM:
    """Captures the prompt and returns a schema-valid draft."""

    def __init__(self) -> None:
        self.messages: list[list[dict]] = []

    def complete(self, messages: list) -> dict:
        self.messages.append(messages)
        return {"content": json.dumps({
            "control_requirements": [{
                "control_id": "C1", "requirement_text": "BCP test evidence", "support_level": "supported",
                "claim": "Recovery exercise completed.", "source_ids": ["sourcea"],
            }],
            "current_evidence_basis": [{
                "control_id": "C1", "source_ids": ["sourcea"], "spans": [{"start": 0, "end": 8}],
                "observation": "Recovery exercise recorded.",
            }],
        })}

    def stream(self, messages: list):
        return iter(())

    def bind_tools(self, tools: list):
        return self


class _BodyEchoingRecordingLLM(_RecordingLLM):
    """Records the actual prompt and copies its evidence body into model output."""

    def complete(self, messages: list) -> dict:
        self.messages.append(messages)
        submission = json.loads(messages[1]["content"])["vendor_submission"]
        body = submission["sources"][0]["text"] if submission.get("sources") else ""
        return {"content": json.dumps({
            "control_requirements": [{
                "control_id": "C1", "requirement_text": "model-owned text", "support_level": "supported",
                "claim": body, "source_ids": ["sourcea"],
            }],
            "current_evidence_basis": [{
                "control_id": "C1", "source_ids": ["sourcea"], "spans": [{"start": 0, "end": 8}],
                "observation": body,
            }],
        })}


class _ObservationEchoingRecordingLLM(_RecordingLLM):
    """Copy only the complete short source into observation."""

    def complete(self, messages: list) -> dict:
        self.messages.append(messages)
        submission = json.loads(messages[1]["content"])["vendor_submission"]
        body = submission["sources"][0]["text"]
        return {"content": json.dumps({
            "control_requirements": [{
                "control_id": "C1", "requirement_text": "BCP test evidence", "support_level": "supported",
                "claim": "A limited observation was generated.", "source_ids": ["sourcea"],
            }],
            "current_evidence_basis": [{
                "control_id": "C1", "source_ids": ["sourcea"],
                "spans": [{"start": 0, "end": len(body)}], "observation": body,
            }],
        })}


class _CatalogueBoundaryLLM(_RecordingLLM):
    """Exercise catalogue reconstruction, rejection, and exact-echo handling."""

    def __init__(self, attack: str) -> None:
        super().__init__()
        self.attack = attack

    def complete(self, messages: list) -> dict:
        self.messages.append(messages)
        submission = json.loads(messages[1]["content"])["vendor_submission"]
        body = submission["sources"][0]["text"] if submission.get("sources") else ""
        control_id = "MODEL-NOT-IN-CATALOGUE" if self.attack == "unknown_control" else "C1"
        requirement_text = body if self.attack == "requirement_text" else "BCP test evidence"
        return {"content": json.dumps({
            "control_requirements": [{
                "control_id": control_id, "requirement_text": requirement_text,
                "support_level": "supported", "claim": "Recovery exercise completed.",
                "source_ids": ["sourcea"],
            }],
            "current_evidence_basis": [{
                "control_id": control_id, "source_ids": ["sourcea"],
                "spans": [{"start": 0, "end": 8}], "observation": "Recovery exercise recorded.",
            }],
        })}


def _state(store: TransientPayloadStore, *, text: str = _SENTINEL) -> dict:
    payload_ref = store.put({
        "documents": [{
            "text": text,
            "control_ids": ["C1"],
            "document_version": "1",
            "content_hash": "document-hash",
        }]
    }, tenant_id="tenant", org_id="org", case_ref="case")
    state = {
        "tenant_id": "tenant", "org_id": "org", "case_ref": "case",
        "evidence_payload_ref": payload_ref,
        "vendor_type": "cloud",
        "control_catalogue_ref": "catalogue-ref",
        "catalogue_version": "1",
        "catalogue_content_hash": "catalogue-hash",
        "perspective_pack_version": "1",
        "perspective_pack_content_hash": "perspective-hash",
        "sealed_content": {
            "catalogue_sealed_ref": "catalogue",
            "perspective_pack_sealed_ref": "perspective",
        },
    }
    # Exercise the real Stage 1 bridge: it consumes the inbound payload handle and mints
    # the separate one-shot evidence_text_ref that Stage 3a is allowed to resolve.
    return {**state, **DeterministicCheckNode({"payload_store": store}).execute(state)}


def _node(store: TransientPayloadStore, llm: object) -> CurrentAssessmentNode:
    return CurrentAssessmentNode({
        "payload_store": store,
        "llm": llm,
        "sealed_content": {
            "catalogue": {"controls": [{"control_id": "C1", "requirement_text": "BCP test evidence"}]},
            "perspective": {"rules": [_PERSPECTIVE_RULE, _OTHER_VENDOR_RULE, _OTHER_CATALOGUE_RULE]},
        },
    })


def _message_payloads(llm: _RecordingLLM) -> tuple[dict, dict]:
    messages = llm.messages[0]
    return json.loads(messages[0]["content"]), json.loads(messages[1]["content"])


def test_prompt_carries_the_current_evidence_body_and_its_span_bounds() -> None:
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    _node(store, llm).execute(_state(store))
    assert [message["role"] for message in llm.messages[0]] == ["system", "user"]
    trusted, untrusted = _message_payloads(llm)
    sources = untrusted["vendor_submission"]["sources"]
    assert [item["source_id"] for item in sources] == ["sourcea"]
    assert sources[0]["text"] == _SENTINEL
    # span_end must be the value Stage 4 verifies against, or cited spans cannot pass.
    assert sources[0]["span_end"] == len(_SENTINEL)
    assert sources[0]["control_ids"] == ["C1"]
    assert _SENTINEL not in json.dumps(trusted, ensure_ascii=False)


def test_prompt_selects_only_the_applicable_perspective_rules() -> None:
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    _node(store, llm).execute(_state(store))
    trusted, _ = _message_payloads(llm)
    approved = trusted["approved_assessment_configuration"]
    assert approved["perspective_rules"] == [_PERSPECTIVE_RULE]
    assert approved["catalogue_content_hash"] == "catalogue-hash"
    assert approved["perspective_pack_content_hash"] == "perspective-hash"


def test_prompt_separates_trusted_perspective_rules_from_untrusted_evidence() -> None:
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    _node(store, llm).execute(_state(store))
    trusted, untrusted = _message_payloads(llm)
    assert trusted["approved_assessment_configuration"]["perspective_rules"] == [_PERSPECTIVE_RULE]
    assert trusted["trust_boundary"] == {
        "approved_assessment_configuration": "TRUSTED_CONFIG",
        "vendor_submission": "UNTRUSTED_INPUT",
    }
    assert untrusted["trust"] == "UNTRUSTED_INPUT"
    assert "expected_evidence" not in json.dumps(untrusted, ensure_ascii=False)
    assert _SENTINEL not in json.dumps(trusted, ensure_ascii=False)


@pytest.mark.parametrize("perspective", [None, {}, {"rules": []}])
def test_missing_or_empty_perspective_pack_fails_closed_without_calling_llm(perspective: dict | None) -> None:
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    sealed_content = {
        "catalogue": {"controls": [{"control_id": "C1", "requirement_text": "BCP test evidence"}]},
    }
    if perspective is not None:
        sealed_content["perspective"] = perspective
    node = CurrentAssessmentNode({"payload_store": store, "llm": llm, "sealed_content": sealed_content})

    result = node.execute(_state(store))

    assert llm.messages == []
    assert result["degradation_reason"] == "perspective_pack_unavailable"
    assert result["evidence_brief"]["control_requirements"][0]["support_level"] == "undetermined"


def test_prompt_states_the_output_contract_the_validators_enforce() -> None:
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    _node(store, llm).execute(_state(store))
    instruction = _message_payloads(llm)[0]["instruction"]
    for value in _SUPPORT_LEVELS:
        assert value in instruction, f"{value!r} is accepted downstream but never named in the prompt"
    for key in ("control_requirements", "current_evidence_basis", "source_ids", "spans", "observation"):
        assert key in instruction
    for rule in ("MUST NOT have a basis", "Orphan", "duplicate", "at most 100 spans"):
        assert rule in instruction


def test_prior_findings_are_never_placed_in_the_stage3a_prompt() -> None:
    """docs/02 §221 — anchoring prevention is structural, not advisory."""
    store = TransientPayloadStore()
    llm = _RecordingLLM()
    state = _state(store)
    state["prior_findings"] = [{"control_id": "C1", "support_level": "insufficient", "claim": "leak me"}]
    _node(store, llm).execute(state)
    assert "leak me" not in json.dumps(llm.messages[0], ensure_ascii=False)


def test_evidence_body_is_not_returned_into_state() -> None:
    store = TransientPayloadStore()
    result = _node(store, _BodyEchoingRecordingLLM()).execute(_state(store))
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)
    assert result["degradation_reason"] == "llm_draft_rejected"


@pytest.mark.parametrize(
    "body",
    ["short-secret-1234", "x" * _VERBATIM_GUARD_CHARS],
    ids=["below-window", "at-window"],
)
def test_source_at_or_below_verbatim_window_is_fully_protected(body: str) -> None:
    """A complete short source in observation is an egress, not a usable assessment."""
    store = TransientPayloadStore()

    result = _node(store, _ObservationEchoingRecordingLLM()).execute(_state(store, text=body))

    assert result["degradation_reason"] == "llm_draft_rejected"
    assert body not in json.dumps(result, ensure_ascii=False, default=str)


def test_evidence_egress_rejection_emits_a_nonsensitive_security_event(monkeypatch: pytest.MonkeyPatch) -> None:
    store = TransientPayloadStore()
    state = _state(store)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "shared.utils.audit_logger.emit_trace_event",
        lambda event_type, payload, _state: events.append((event_type, payload)),
    )

    result = _node(store, _BodyEchoingRecordingLLM()).execute(state)

    egress_events = [payload for event_type, payload in events if event_type == "stage3a_evidence_egress_rejected"]
    assert egress_events == [{"check": "evidence_match", "model_string_count": 5, "source_count": 1}]
    assert result["degradation_reason"] == "llm_draft_rejected"
    assert _SENTINEL not in json.dumps(events, ensure_ascii=False, default=str)


@pytest.mark.parametrize(("attack", "expected_payload", "expected_reason"), [
    ("requirement_text", {"field": "requirement_text", "action": "reconstructed", "count": 1}, ""),
    ("unknown_control", {"field": "control_id", "action": "rejected", "count": 1}, "llm_draft_rejected"),
    ("exact_echo", None, ""),
])
def test_catalogue_boundary_audit_distinguishes_override_rejection_and_exact_echo(
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    expected_payload: dict | None,
    expected_reason: str,
) -> None:
    store = TransientPayloadStore()
    state = _state(store)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "shared.utils.audit_logger.emit_trace_event",
        lambda event_type, payload, _state: events.append((event_type, payload)),
    )

    result = _node(store, _CatalogueBoundaryLLM(attack)).execute(state)

    catalogue_events = [
        payload for event_type, payload in events if event_type == "stage3a_catalogue_boundary_enforced"
    ]
    assert catalogue_events == ([] if expected_payload is None else [expected_payload])
    assert result["degradation_reason"] == expected_reason
    assert _SENTINEL not in json.dumps(events, ensure_ascii=False, default=str)
    assert "MODEL-NOT-IN-CATALOGUE" not in json.dumps(events, ensure_ascii=False, default=str)


@pytest.mark.parametrize("mutate", ["spent", "no_store"])
def test_unreadable_evidence_degrades_loudly_not_silently(mutate: str) -> None:
    """A RETRY re-enters this node with the one-shot handle already consumed.

    Drafting "undetermined" with no reason would look like a clean assessment, so the
    node must carry a degradation_reason instead.
    """
    store = TransientPayloadStore()
    state = _state(store)
    if mutate == "spent":
        store.resolve(state["evidence_text_ref"], tenant_id="tenant", org_id="org", case_ref="case")
        node = _node(store, _RecordingLLM())
    else:
        node = CurrentAssessmentNode({
            "llm": _RecordingLLM(),
            "sealed_content": {
                "catalogue": {"controls": [{"control_id": "C1", "requirement_text": "BCP test evidence"}]},
                "perspective": {"rules": [_PERSPECTIVE_RULE]},
            },
        })
    result = node.execute(state)
    assert result["degradation_reason"], "evidence could not be read but no reason was recorded"


@pytest.mark.parametrize(
    ("documents", "config", "reason"),
    [
        ([{"text": "a"}, {"text": "b"}], {"max_evidence_documents": 1}, "document count"),
        ([{"text": "abc"}, {"text": "def"}], {"max_aggregate_evidence_chars": 5}, "aggregate evidence characters"),
        ([{"text": "a"}] * 21, {}, "document count"),
        ([{"text": "x" * 133_334}] * 3, {}, "aggregate evidence characters"),
    ],
    ids=["configured-document-count", "configured-aggregate", "default-document-count", "default-aggregate"],
)
def test_stage1_evidence_caps_fail_closed_without_truncation(
    documents: list[dict],
    config: dict,
    reason: str,
) -> None:
    store = TransientPayloadStore()
    payload_ref = store.put({"documents": documents}, tenant_id="tenant", org_id="org", case_ref="case")
    state = {
        "tenant_id": "tenant",
        "org_id": "org",
        "case_ref": "case",
        "evidence_payload_ref": payload_ref,
    }

    result = DeterministicCheckNode({"payload_store": store, **config}).execute(state)

    assert result["status"] == "error"
    assert reason in result["error_log"][0]
    assert "source_registry" not in result
    assert "evidence_text_ref" not in result


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ({"max_evidence_documents": 0}, "max_evidence_documents"),
        ({"max_aggregate_evidence_chars": "5"}, "max_aggregate_evidence_chars"),
    ],
)
def test_stage1_invalid_evidence_cap_configuration_fails_closed(config: dict, reason: str) -> None:
    store = TransientPayloadStore()
    payload_ref = store.put({"documents": [{"text": "a"}]}, tenant_id="tenant", org_id="org", case_ref="case")
    state = {
        "tenant_id": "tenant",
        "org_id": "org",
        "case_ref": "case",
        "evidence_payload_ref": payload_ref,
    }

    result = DeterministicCheckNode({"payload_store": store, **config}).execute(state)

    assert result["status"] == "error"
    assert reason in result["error_log"][0]
