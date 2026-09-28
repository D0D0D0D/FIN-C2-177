"""GraphNode unit contracts."""

import inspect

import pytest

from framework.errors import SecurityViolationError
from src.nodes.main_node import OutsourcingBriefGraphNode


def test_main_node_requires_trusted_scope() -> None:
    node = OutsourcingBriefGraphNode()
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_input({"tenant_id": "tenant"})


def test_main_node_serializes_only_the_inner_envelope() -> None:
    node = OutsourcingBriefGraphNode()
    payload = node.extract_input({
        "mode": "invoke", "tenant_id": "tenant", "org_id": "org", "case_ref": "case", "vendor_id": "vendor",
        "vendor_type": "cloud", "evidence_payload_ref": "abcdefghijklmnop", "validated_input": {}, "sealed_content": {},
    })
    assert '"tenant_id":"tenant"' in payload
    assert "node_history" not in payload


def test_main_node_execute_contract() -> None:
    assert "_invoke_impl" not in OutsourcingBriefGraphNode.__dict__
    assert list(inspect.signature(OutsourcingBriefGraphNode.execute).parameters)[1] == "state"
