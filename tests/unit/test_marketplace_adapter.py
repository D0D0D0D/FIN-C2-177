"""Marketplace lifecycle, sample pin, and evidence-linkage contracts."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

import cli
import src.services.pin_resolver as pin_resolver_module
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from src.services.payload_store import PayloadStoreError, TransientPayloadStore
from src.services.pin_resolver import (
    _read_json,
    PinResolver,
    canonical_content_hash,
    load_sample_evidence,
    load_sample_pin_records,
    register_sample_pins,
)

_CONFIG_PATH = Path(__file__).parents[2] / "config" / "config.yaml"
_SAMPLE_CONFIG_DIR = _CONFIG_PATH.parent


def _write_sample_bundle(tmp_path: Path, evidence: object | str) -> None:
    for filename in ("control_catalogue.sample.json", "perspective_pack.sample.json"):
        (tmp_path / filename).write_text(
            (_SAMPLE_CONFIG_DIR / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    evidence_text = evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False)
    (tmp_path / "evidence.sample.json").write_text(evidence_text, encoding="utf-8")


class _SampleDraftLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list) -> dict:
        self.calls += 1
        trusted = json.loads(messages[0]["content"])["approved_assessment_configuration"]
        sources = json.loads(messages[1]["content"])["vendor_submission"]["sources"]
        requirements = []
        basis = []
        for control in trusted["controls"]:
            source = next(item for item in sources if control["control_id"] in item["control_ids"])
            requirements.append(
                {
                    "control_id": control["control_id"],
                    "requirement_text": control["requirement_text"],
                    "support_level": "supported",
                    "claim": f"Sample evidence was mapped to {control['control_id']}.",
                    "source_ids": [source["source_id"]],
                }
            )
            basis.append(
                {
                    "control_id": control["control_id"],
                    "source_ids": [source["source_id"]],
                    "spans": [{"start": 0, "end": min(8, source["span_end"])}],
                    "observation": f"A sample record addresses {control['control_id']}.",
                }
            )
        return {"content": json.dumps({"control_requirements": requirements, "current_evidence_basis": basis})}

    def stream(self, messages: list):
        del messages
        return iter(())

    def bind_tools(self, tools: list):
        del tools
        return self


@pytest.fixture
def marketplace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "AZURE_OPENAI_API_KEY": "sample-key",
        "AZURE_OPENAI_ENDPOINT": "https://sample.invalid",
        "AZURE_OPENAI_DEPLOYMENT": "sample-deployment",
        "ASTAR_TENANT_ID": "sample-tenant",
        "ASTAR_ORG_ID": "sample-org",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _runtime_config(llm: object | None = None) -> dict:
    config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    if llm is not None:
        config["llm"] = llm
    return config


def _invoke(graph: cli.MarketplaceGraph, *, user_input: str = "", marker: str = "runner") -> dict:
    context = InvocationContext(
        session_id=f"{marker}-session",
        thread_id=f"{marker}-thread",
        caller_id="marketplace-caller",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
    )
    with bound_secrets(graph._secrets_provider):
        return graph.invoke(
            user_input,
            ctx=context,
            input_context={"conversation_history": [{"role": "user", "content": "CANARY"}]},
        )


@pytest.mark.parametrize("runner_order", ["1.0.1", "1.0.2"])
def test_runner_orders_produce_a_cited_sample_brief(
    marketplace_env: None,
    runner_order: str,
) -> None:
    llm = _SampleDraftLLM()
    graph = cli.MarketplaceGraph(config=_runtime_config(llm))
    provider = cli._AllowlistedEnvironmentProvider()
    if runner_order == "1.0.1":
        graph.compile()
        graph.provision_secrets(provider)
    else:
        graph.provision_secrets(provider)
        graph.compile()

    result = _invoke(graph, marker=runner_order)

    assert result["status"] == "success"
    assert result["output"]
    assert "CANARY" not in json.dumps(result, ensure_ascii=False, default=str)
    assert llm.calls >= 1
    output = result["output"]
    assert output["mode"] == "invoke"
    assert output["degradation_reason"] not in {
        "perspective_pack_unavailable",
        "llm_unavailable",
    }
    assert output["requires_human_review"] is False
    assert output["applied_pins"]["catalogue_version"] == "SAMPLE-2026.1"
    requirements = output["evidence_brief"]["control_requirements"]
    assert requirements
    assert all(item["control_id"].startswith("SAMPLE-CTL-") for item in requirements)
    assert any(item["support_level"] != "undetermined" for item in requirements)
    source_ids = {item["source_id"] for item in output["evidence_brief"]["evidence_sources"]}
    assert source_ids
    assert output["current_evidence_basis"]
    assert all(set(item["source_ids"]) <= source_ids for item in output["current_evidence_basis"])
    assert all(item["source_ids"] for item in output["evidence_map"])


def test_input_context_is_rebuilt_at_the_super_invoke_call_site(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def recorder(self, user_input, session_id="", ctx=None, input_context=None):
        del self, session_id, ctx
        captured["user_input"] = user_input
        captured["input_context"] = input_context
        return {"status": "success", "output": {}}

    monkeypatch.setattr(
        "src.graph.graph.OutsourcingDueDiligenceBriefAgent.invoke",
        recorder,
    )
    graph = cli.MarketplaceGraph(config=_runtime_config(_SampleDraftLLM()))
    graph.provision_secrets(cli._AllowlistedEnvironmentProvider())

    graph.invoke(
        "",
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": [{"content": "CANARY"}]},
    )

    assert "CANARY" not in json.dumps(captured, ensure_ascii=False)
    assert captured["input_context"]["entry_mode"] == "invoke"
    assert "conversation_history" not in captured["input_context"]


def test_missing_pin_registration_fails_before_graph_invoke(marketplace_env: None) -> None:
    graph = cli.MarketplaceGraph(config=_runtime_config(_SampleDraftLLM()))
    graph.config["pin_resolver"]._records.clear()
    graph.compile()
    graph.provision_secrets(cli._AllowlistedEnvironmentProvider())

    with pytest.raises(HTTPException, match="Pinned content is unavailable"):
        _invoke(graph, marker="missing-pins")


@pytest.mark.parametrize(
    "untrusted_mode",
    [{"mode": "ingest"}, {"entry_mode": "feedback"}],
)
def test_marketplace_input_cannot_reach_write_modes(
    marketplace_env: None,
    untrusted_mode: dict,
) -> None:
    graph = cli.MarketplaceGraph(config=_runtime_config(_SampleDraftLLM()))
    graph.provision_secrets(cli._AllowlistedEnvironmentProvider())
    graph.compile()

    result = _invoke(graph, user_input=json.dumps(untrusted_mode), marker=next(iter(untrusted_mode)))

    assert result["status"] == "success"
    assert result["output"]["mode"] == "invoke"


def test_constructor_injects_default_llm_and_preserves_explicit_llm() -> None:
    default_graph = cli.MarketplaceGraph(config={**_runtime_config(), "llm": None})
    explicit = _SampleDraftLLM()
    explicit_graph = cli.MarketplaceGraph(config=_runtime_config(explicit))

    assert isinstance(default_graph.config["llm"], cli._AzureMarketplaceLLM)
    assert default_graph.config["llm"]._temperature == 0.0
    assert explicit_graph.config["llm"] is explicit
    assert "sample-key" not in repr(vars(default_graph.config["llm"]))


def test_non_client_llm_mapping_is_replaced_and_its_temperature_is_preserved() -> None:
    config = _runtime_config()
    config["llm"] = {"temperature": 0.37}

    graph = cli.MarketplaceGraph(config=config)

    assert isinstance(graph.config["llm"], cli._AzureMarketplaceLLM)
    assert graph.config["llm"]._temperature == 0.37


def test_main_validates_and_passes_file_config(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        cli,
        "run_agent_marketplace",
        lambda graph, **kwargs: calls.append((graph, kwargs)),
    )

    cli.main()

    graph, kwargs = calls[0]
    assert graph is cli.MarketplaceGraph
    assert kwargs["agent_name"] == "FIN-C2-177"
    assert kwargs["namespace"] == "fin"
    assert kwargs["config"] == yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("bad_value", [None, " ", "bad\nvalue"])
def test_main_fails_closed_for_missing_or_unclean_environment(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
    bad_value: str | None,
) -> None:
    if bad_value is None:
        monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    else:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", bad_value)
    monkeypatch.setattr(cli, "run_agent_marketplace", lambda *args, **kwargs: pytest.fail("runner called"))

    with pytest.raises((ValueError, RuntimeError)):
        cli.main()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://sample.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://sample.invalid/openai",
        "https://sample.invalid/v1",
        "https://sample.invalid?api-version=2024-10-21",
        "sample.invalid",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_unusable_azure_endpoint_shape_fails_during_startup(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """AzureOpenAIClient appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces inside complete(); a node that swallows it
    degrades silently while the invocation still reports status: success, so the
    adapter has to reject the shape at startup instead.
    """

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    monkeypatch.setattr(cli, "run_agent_marketplace", lambda *args, **kwargs: pytest.fail("runner called"))

    with pytest.raises(ValueError, match="bare"):
        cli.main()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://sample.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://sample.invalid/openai",
        "https://sample.invalid/v1",
        "https://sample.invalid?api-version=2024-10-21",
        "sample.invalid",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_endpoint_validation_also_applies_to_provision_and_llm_client(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    provider = cli._AllowlistedEnvironmentProvider()
    graph = cli.MarketplaceGraph(config=_runtime_config(_SampleDraftLLM()))

    with pytest.raises(ValueError, match="Azure resource endpoint"):
        graph.provision_secrets(provider)
    with bound_secrets(provider), pytest.raises(ValueError, match="Azure resource endpoint"):
        cli._AzureMarketplaceLLM()._client()


def test_lazy_llm_does_not_retain_connection_values_after_complete(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_configs: list[dict] = []

    class EphemeralClient:
        def __init__(self, config: dict) -> None:
            received_configs.append(dict(config))

        def complete(self, messages: list) -> dict:
            del messages
            return {"content": "{}"}

    # _client() prefers the SDK's own AzureOpenAIClient and only falls back to the
    # local compat class when that module is absent (AgentCore 1.0.0 / 1.0.1). Patch
    # whichever one the installed wheel actually resolves, so this test never reaches
    # a real endpoint on a wheel that ships the SDK client.
    monkeypatch.setattr(cli, "_AzureOpenAIClientCompat", EphemeralClient)
    try:
        azure_module = importlib.import_module("shared.services.llm.azure_openai_client")
    except ModuleNotFoundError:
        pass
    else:
        monkeypatch.setattr(azure_module, "AzureOpenAIClient", EphemeralClient)
    llm = cli._AzureMarketplaceLLM(0.25)
    provider = cli._AllowlistedEnvironmentProvider()

    with bound_secrets(provider):
        assert llm.complete([]) == {"content": "{}"}

    assert received_configs[0]["temperature"] == 0.25
    assert vars(llm) == {"_temperature": 0.25, "_tools": ()}
    retained = repr(vars(llm))
    for value in ("sample-key", "https://sample.invalid", "sample-deployment"):
        assert value not in retained


def test_main_fails_closed_when_sample_validation_fails(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "validate_sample_data", lambda: (_ for _ in ()).throw(RuntimeError("missing sample")))
    monkeypatch.setattr(cli, "run_agent_marketplace", lambda *args, **kwargs: pytest.fail("runner called"))

    with pytest.raises(RuntimeError, match="missing sample"):
        cli.main()


def test_sample_loader_is_cwd_independent_and_missing_files_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_sample_pin_records()["control_catalogue"]["ref"] == "SAMPLE-CATALOGUE"
    with pytest.raises(RuntimeError, match="unreadable"):
        _read_json("control_catalogue.sample.json", config_dir=tmp_path)


def test_sample_evidence_loader_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unreadable"):
        load_sample_evidence(config_dir=tmp_path)


def test_sample_evidence_loader_rejects_invalid_json(tmp_path: Path) -> None:
    _write_sample_bundle(tmp_path, "{not-json")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        load_sample_evidence(config_dir=tmp_path)


def test_sample_evidence_loader_rejects_non_object_top_level(tmp_path: Path) -> None:
    _write_sample_bundle(tmp_path, [])
    with pytest.raises(RuntimeError, match="non-empty object"):
        load_sample_evidence(config_dir=tmp_path)


@pytest.mark.parametrize("documents", [None, []], ids=["not-list", "empty"])
def test_sample_evidence_loader_rejects_invalid_documents(
    tmp_path: Path,
    documents: object,
) -> None:
    evidence = load_sample_evidence()
    evidence["documents"] = documents
    _write_sample_bundle(tmp_path, evidence)

    with pytest.raises(RuntimeError, match="non-empty list"):
        load_sample_evidence(config_dir=tmp_path)


@pytest.mark.parametrize("missing_key", ["text", "control_ids", "document_version"])
def test_sample_evidence_loader_rejects_missing_document_keys(
    tmp_path: Path,
    missing_key: str,
) -> None:
    evidence = load_sample_evidence()
    evidence["documents"][0].pop(missing_key)
    _write_sample_bundle(tmp_path, evidence)

    with pytest.raises(RuntimeError, match="missing required keys"):
        load_sample_evidence(config_dir=tmp_path)


@pytest.mark.parametrize(
    ("vendor_key", "mutation"),
    [
        ("vendor_type", "missing"),
        ("vendor_type", "empty"),
        ("vendor_id", "missing"),
        ("vendor_id", "empty"),
    ],
)
def test_sample_evidence_loader_rejects_missing_or_empty_vendor_fields(
    tmp_path: Path,
    vendor_key: str,
    mutation: str,
) -> None:
    evidence = load_sample_evidence()
    if mutation == "missing":
        evidence.pop(vendor_key)
    else:
        evidence[vendor_key] = ""
    _write_sample_bundle(tmp_path, evidence)

    with pytest.raises(RuntimeError, match="evidence sample"):
        load_sample_evidence(config_dir=tmp_path)


def test_startup_validation_rejects_broken_evidence_sample(
    marketplace_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = load_sample_evidence()
    evidence["documents"] = []
    _write_sample_bundle(tmp_path, evidence)
    monkeypatch.setattr(pin_resolver_module, "_config_dir", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="non-empty list"):
        cli._validate_startup()


def test_sample_hashes_and_pin_registration_are_deterministic() -> None:
    records = load_sample_pin_records()
    evidence = load_sample_evidence()
    for record in records.values():
        assert record["content_hash"] == canonical_content_hash(record["content"])
    for document in evidence["documents"]:
        assert document["content_hash"] == hashlib.sha256(document["text"].encode("utf-8")).hexdigest()
    sealed: dict[str, dict] = {}
    resolver = PinResolver(sealed)
    first = register_sample_pins(resolver)
    second = register_sample_pins(resolver)
    assert first == second
    assert len(resolver._records) == 2


@pytest.mark.parametrize("kind", ["control_catalogue", "perspective_pack"])
def test_sample_loader_rejects_missing_nested_required_keys(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    records = load_sample_pin_records()
    if kind == "control_catalogue":
        records[kind]["content"]["controls"][0].pop("requirement_text")
    else:
        records[kind]["content"]["rules"][0].pop("expected_evidence")
    records[kind]["content_hash"] = canonical_content_hash(records[kind]["content"])

    def sample_record(filename: str, *, config_dir=None):
        del config_dir
        selected = "control_catalogue" if filename.startswith("control_catalogue") else "perspective_pack"
        return records[selected]

    monkeypatch.setattr(pin_resolver_module, "_read_json", sample_record)
    with pytest.raises(RuntimeError, match="missing required keys"):
        load_sample_pin_records()


def test_user_documents_override_sample_and_free_text_has_no_guessed_control_mapping() -> None:
    graph = cli.MarketplaceGraph(config=_runtime_config(_SampleDraftLLM()))
    vendor_type, supplied = graph._marketplace_input(
        json.dumps(
            {
                "vendor_type": "specialist_provider",
                "documents": [
                    {
                        "text": "利用者が提出した架空の確認記録。",
                        "control_ids": ["SAMPLE-CTL-001"],
                        "document_version": "USER-1",
                    }
                ],
            },
            ensure_ascii=True,
        )
    )
    _, free_text = graph._marketplace_input("自由文の証跡")

    assert vendor_type == "specialist_provider"
    assert supplied["documents"][0]["text"] == "利用者が提出した架空の確認記録。"
    assert len(supplied["documents"]) == 1
    assert free_text["documents"][0]["control_ids"] == []


def test_payload_store_rejects_cross_scope_resolution() -> None:
    store = TransientPayloadStore()
    reference = store.put({"documents": []}, tenant_id="tenant-a", org_id="org-a", case_ref="case-a")
    with pytest.raises(PayloadStoreError, match="scope_mismatch"):
        store.resolve(reference, tenant_id="tenant-b", org_id="org-a", case_ref="case-a")
