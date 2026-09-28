"""PB-6: every concrete template node uses the framework security call order."""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil

from framework.nodes.base_node import BaseNode
from framework.nodes.graph_node import GraphNode
from typing import ClassVar
from framework.schemas.trust_level import TrustLevel


def _discover_node_classes() -> list[type[BaseNode]]:
    """Discover all concrete nodes so future additions are covered automatically."""
    package = importlib.import_module("src.nodes")
    discovered: list[type[BaseNode]] = []
    seen: set[type[BaseNode]] = set()
    for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix="src.nodes."):
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseNode)
                and value is not BaseNode
                and value not in seen
                and not inspect.isabstract(value)
            ):
                discovered.append(value)
                seen.add(value)
    return discovered


class _SuccessfulSubgraph:
    """Isolated subgraph seam so PB-6 tests GraphNode.execute itself, not workflow data."""

    name = "pb6-subgraph"

    def invoke(self, *_args, **_kwargs) -> dict:
        return {"status": "success"}


def _state_for(node_class: type[BaseNode]) -> dict:
    state = {
        "caller_trust_level": node_class.required_trust_level.value,
        "correlation_id": "pb6-correlation",
        "session_id": "pb6-session",
        "thread_id": "pb6-thread",
        "trace_id": "pb6-trace",
        "caller_id": "pb6-caller",
        "node_history": [],
        "error_log": [],
    }
    name = node_class.__name__
    if name == "PreProcessNode":
        return {**state, "user_input": "{}", "input_context": {"tenant_id": "tenant", "org_id": "org", "entry_mode": "feedback", "case_ref": "case"}}
    if name == "PostProcessNode":
        return {**state, "mode": "ingest", "ingest_result": {}}
    if name == "InputDecodeNode":
        return {**state, "user_input": json.dumps({"mode": "feedback", "tenant_id": "tenant", "org_id": "org", "case_ref": "case"})}
    if issubclass(node_class, GraphNode):
        return {
            **state,
            "mode": "feedback", "tenant_id": "tenant", "org_id": "org", "case_ref": "case",
            "validated_input": {}, "user_input": "{}", "hitl_allowed": False,
        }
    return state


# ── PB-6 S-1 negative boundary（2026-08-17 正本追従で移植）──────────────
# 正本 tests/proof_of_boundary/test_pb_invoke_order.py より。テンプレのノード構成に
# 依存しない常設フィクスチャで「不足 trust では execute() に到達しない」ことを証明する
# （TC-08 の要求。既存の全ノード走査テストは触っていない）。


class _PrivilegedTrustGateFixture(BaseNode):
    """Always-present privileged node used to prove the S-1 negative boundary."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _security_gate_input(self, state):
        return state

    def execute(self, state):
        return {"status": "success"}

    def _security_gate_output(self, result):
        return result


def _trust_predecessor(required: TrustLevel) -> TrustLevel:
    """Return a lower valid trust level; fail loudly if the framework adds one."""
    predecessors = {
        TrustLevel.VERIFIED_EXTERNAL: TrustLevel.ANONYMOUS,
        TrustLevel.INTERNAL: TrustLevel.VERIFIED_EXTERNAL,
    }
    try:
        return predecessors[required]
    except KeyError as exc:
        raise AssertionError(f"no lower trust level defined for {required!r}") from exc


def test_pb6_invoke_execution_order_for_every_concrete_template_node(monkeypatch) -> None:
    """S-1 → node_start → S-2 → execute → S-3 → node_complete for every node."""
    import framework.nodes.base_node as base_node_module

    failures: list[str] = []
    discovered = _discover_node_classes()
    assert discovered, "src.nodes must contain concrete template nodes"
    for node_class in discovered:
        order: list[str] = []
        with monkeypatch.context() as patch:
            patch.setattr(base_node_module, "emit_trace_event", lambda event, _payload, _state: order.append(f"event:{event}"))
            if issubclass(node_class, GraphNode):
                patch.setattr(node_class, "get_subgraph", lambda _self: _SuccessfulSubgraph())
            for method_name, label in (("_security_gate_input", "security_gate_input"), ("execute", "execute"), ("_security_gate_output", "security_gate_output")):
                original = getattr(node_class, method_name)

                def spy(self, argument, _original=original, _label=label):
                    order.append(_label)
                    return _original(self, argument)

                patch.setattr(node_class, method_name, spy)
            node_class()(_state_for(node_class))
        expected = ["event:node_start", "security_gate_input", "execute", "security_gate_output", "event:node_complete"]
        if order != expected:
            failures.append(f"{node_class.__name__}: expected {expected}, got {order}")
    assert not failures, "\n".join(failures)


def test_s1_denial_refuses_execution_before_execute(monkeypatch):
    """TC-08: an always-present privileged node proves the negative S-1 path."""
    import framework.nodes.base_node as base_node_module

    events: list[str] = []
    execute_calls: list[object] = []
    monkeypatch.setattr(
        base_node_module,
        "emit_trace_event",
        lambda event_type, _payload, _state: events.append(event_type),
    )
    original_execute = _PrivilegedTrustGateFixture.execute

    def spy_execute(self, state):
        execute_calls.append(state)
        return original_execute(self, state)

    monkeypatch.setattr(_PrivilegedTrustGateFixture, "execute", spy_execute)
    result = _PrivilegedTrustGateFixture()(
        {
            "caller_trust_level": _trust_predecessor(
                _PrivilegedTrustGateFixture.required_trust_level
            ).value,
            "correlation_id": "tc08-s1-denial",
        }
    )

    assert result["status"] == "error"
    assert "S-1 trust gate denied" in result["error_log"][0]
    assert events == ["s1_denied"]
    assert not execute_calls
