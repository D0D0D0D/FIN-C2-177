"""AGENTIC STAR Marketplace entry adapter for FIN-C2-177."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from framework.schemas.invocation_context import InvocationContext
from framework.secrets.base import SecretProvider
from framework.secrets.context import current_secrets
from langgraph.checkpoint.memory import InMemorySaver
from shared.services.llm.base_llm import BaseLLM

try:
    from framework.utils.config_loader import load_agent_config
except ImportError:  # AgentCore 1.0.0 / 1.0.1 compatibility.
    from framework.utils.config_loader import load_config

    def load_agent_config(agent_dir: Path) -> dict[str, Any]:
        config_path = agent_dir / "config" / "config.yaml"
        return load_config(str(config_path)) if config_path.exists() else {}


try:
    from shared.bootstrap import run_agent_marketplace
except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility.
    if exc.name != "shared.bootstrap":
        raise
    _BOOTSTRAP_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def run_agent_marketplace(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError(
            "The installed AgentCore wheel does not provide Marketplace bootstrap support"
        ) from _BOOTSTRAP_IMPORT_ERROR
else:
    _BOOTSTRAP_IMPORT_ERROR = None


try:
    from shared.secrets.env_provider import EnvProvider
except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility.
    if exc.name != "shared.secrets.env_provider":
        raise

    class EnvProvider(SecretProvider):  # type: ignore[no-redef]
        """Minimal process-environment provider for older AgentCore wheels."""

        def __init__(self, namespace: str = "", agent_name: str = "") -> None:
            self._namespace = namespace
            self._agent_name = agent_name

        def get(self, key: str, default: str | None = None) -> str | None:
            return os.environ.get(key, default)


from src.graph.graph import OutsourcingDueDiligenceBriefAgent
from src.services.kb_service import ScopedKnowledgeBase
from src.services.payload_store import TransientPayloadStore, mint_reference
from src.services.pin_resolver import (
    PinResolver,
    load_sample_evidence,
    register_sample_pins,
    validate_sample_data,
)

_NAMESPACE = "fin"
_AGENT_NAME = "FIN-C2-177"
_PROVIDER_NAMESPACE = "fin-c2-177"
_PROVIDER_AGENT_NAME = "FinancialOutsourcingDueDiligenceEvidenceBriefAgent"
_AZURE_OPENAI_SECRET_KEYS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
)
_SCOPE_KEYS = ("ASTAR_TENANT_ID", "ASTAR_ORG_ID")
_REQUIRED_ENV_KEYS = _AZURE_OPENAI_SECRET_KEYS + _SCOPE_KEYS
_ALLOWED_SECRET_KEYS = frozenset(_REQUIRED_ENV_KEYS)


class _AllowlistedEnvironmentProvider(EnvProvider):  # type: ignore[misc]
    """Expose only values consumed by this Marketplace adapter."""

    def __init__(self) -> None:
        super().__init__(namespace=_PROVIDER_NAMESPACE, agent_name=_PROVIDER_AGENT_NAME)

    def get(self, key: str, default: str | None = None) -> str | None:
        if key not in _ALLOWED_SECRET_KEYS:
            return default
        value = super().get(key)
        return value if value is not None else default


def _require_clean(provider: SecretProvider, key: str) -> str:
    value = provider.require(key)
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"Marketplace environment variable {key!r} must be a clean non-empty string")
    return value


def _require_azure_endpoint(provider: SecretProvider) -> str:
    """Reject ``AZURE_OPENAI_ENDPOINT`` values that are not a bare Azure resource endpoint.

    ``AzureOpenAIClient`` appends its own ``/openai/v1/`` path, so anything
    beyond a scheme and host silently produces a broken request URL, and the
    failure surfaces inside ``complete()``. A node that swallows that exception
    degrades quietly while the invocation still reports ``status: success``,
    which is why this has to be a startup check. The value is reported here,
    never repaired.
    """

    endpoint = _require_clean(provider, "AZURE_OPENAI_ENDPOINT")
    parts = urlsplit(endpoint)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port == -1
        or parts.path.strip("/")
        or parts.query
        or parts.fragment
        or any(character.isspace() for character in endpoint)
    ):
        raise ValueError(
            "Marketplace environment variable 'AZURE_OPENAI_ENDPOINT' must be a bare Azure "
            "resource endpoint -- scheme and host only, with no path (such as "
            "'/openai'), query or fragment"
        )
    return endpoint


def _validate_required_environment(provider: SecretProvider) -> None:
    for key in _REQUIRED_ENV_KEYS:
        if key == "AZURE_OPENAI_ENDPOINT":
            _require_azure_endpoint(provider)
        else:
            _require_clean(provider, key)


def _is_llm_client(candidate: object) -> bool:
    return callable(getattr(candidate, "complete", None))


class _AzureOpenAIClientCompat(BaseLLM):
    """Small Azure chat-completion client for pre-Marketplace AgentCore wheels."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config)

    def complete(self, messages: list[Any]) -> dict[str, Any]:
        endpoint = self._config["azure_endpoint"]
        deployment = quote(self._config["azure_deployment"], safe="")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21"
        body = {"messages": messages, "temperature": self._config["temperature"]}
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"api-key": self._config["api_key"], "content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - operator-owned Azure endpoint
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        message = choices[0].get("message", {}) if choices else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        return {
            "content": content,
            "tool_calls": message.get("tool_calls", []) if isinstance(message, dict) else [],
            "model": payload.get("model", self._config["azure_deployment"]),
            "usage": payload.get("usage", {}),
        }

    def stream(self, messages: list[Any]) -> Iterator[str]:
        content = self.complete(messages).get("content", "")
        if content:
            yield str(content)

    def bind_tools(self, tools: list[Any]) -> BaseLLM:
        if tools:
            raise NotImplementedError("Azure compatibility client does not support tool binding")
        return type(self)(self._config)


class _AzureMarketplaceLLM(BaseLLM):  # type: ignore[misc]
    """Resolve Azure credentials only when the graph performs an LLM operation."""

    def __init__(self, temperature: float = 0.0, *, tools: tuple[Any, ...] = ()) -> None:
        self._temperature = float(temperature)
        self._tools = tools

    def _client(self) -> BaseLLM:
        provider = current_secrets()
        values = {
            "AZURE_OPENAI_API_KEY": _require_clean(provider, "AZURE_OPENAI_API_KEY"),
            "AZURE_OPENAI_ENDPOINT": _require_azure_endpoint(provider),
            "AZURE_OPENAI_DEPLOYMENT": _require_clean(provider, "AZURE_OPENAI_DEPLOYMENT"),
        }

        # Secret validation deliberately precedes this optional import so missing
        # deployment configuration is reported accurately on older SDK wheels.
        try:
            from shared.services.llm.azure_openai_client import AzureOpenAIClient
        except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility.
            if exc.name != "shared.services.llm.azure_openai_client":
                raise
            AzureOpenAIClient = _AzureOpenAIClientCompat  # type: ignore[misc,assignment]

        client = cast(
            BaseLLM,
            AzureOpenAIClient(
                {
                    "api_key": values["AZURE_OPENAI_API_KEY"],
                    "azure_endpoint": values["AZURE_OPENAI_ENDPOINT"],
                    "azure_deployment": values["AZURE_OPENAI_DEPLOYMENT"],
                    "temperature": self._temperature,
                }
            ),
        )
        return client.bind_tools(list(self._tools)) if self._tools else client

    def complete(self, messages: list[Any]) -> dict[str, Any]:
        return cast(dict[str, Any], self._client().complete(messages))

    def stream(self, messages: list[Any]) -> Iterator[str]:
        yield from self._client().stream(messages)

    def bind_tools(self, tools: list[Any]) -> "_AzureMarketplaceLLM":
        return type(self)(self._temperature, tools=tuple(tools))


def _normalised_documents(documents: object, allowed_control_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(documents, list):
        raise ValueError("documents must be a list")
    normalised: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("each evidence document must be an object")
        text = document.get("text")
        control_ids = document.get("control_ids", [])
        if not isinstance(text, str) or not text:
            raise ValueError("each evidence document requires non-empty text")
        if not isinstance(control_ids, list) or not all(
            isinstance(control_id, str) and control_id in allowed_control_ids for control_id in control_ids
        ):
            raise ValueError("evidence control_ids must reference the bundled sample catalogue")
        document_version = document.get("document_version", "marketplace-paste")
        if not isinstance(document_version, str) or not document_version:
            raise ValueError("evidence document_version must be a non-empty string")
        normalised.append(
            {
                "text": text,
                "control_ids": list(control_ids),
                "document_version": document_version,
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    return normalised


class MarketplaceGraph(OutsourcingDueDiligenceBriefAgent):
    """Marketplace-only invoke boundary with sample pins and lazy Azure wiring."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        merged = dict(config or {})
        llm_candidate = merged.get("llm")
        if not _is_llm_client(llm_candidate):
            temperature = merged.get("llm_temperature", 0.0)
            if isinstance(llm_candidate, Mapping):
                temperature = llm_candidate.get("temperature", temperature)
            merged["llm"] = _AzureMarketplaceLLM(float(temperature))
        sealed_content: dict[str, dict] = {}
        pin_resolver = PinResolver(sealed_content)
        sample_records = register_sample_pins(pin_resolver)
        sample_evidence = load_sample_evidence()
        merged["payload_store"] = TransientPayloadStore(int(merged.get("payload_ttl_s", 900)))
        merged["kb_service"] = ScopedKnowledgeBase()
        merged["sealed_content"] = sealed_content
        merged["pin_resolver"] = pin_resolver
        merged["inner_checkpointer"] = InMemorySaver()
        self._sample_records = sample_records
        self._sample_evidence = sample_evidence
        super().__init__(config=merged)

    def _marketplace_input(self, user_input: str) -> tuple[str, dict[str, Any]]:
        sample = deepcopy(self._sample_evidence)
        allowed_control_ids = {
            item["control_id"] for item in self._sample_records["control_catalogue"]["content"]["controls"]
        }
        stripped = user_input.strip()
        vendor_type = sample["vendor_type"]
        if not stripped:
            documents = sample["documents"]
        elif stripped.startswith("{"):
            try:
                supplied = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError("Marketplace JSON input is invalid") from exc
            if not isinstance(supplied, dict):
                raise ValueError("Marketplace JSON input must be an object")
            if "vendor_type" in supplied:
                if not isinstance(supplied["vendor_type"], str) or not supplied["vendor_type"]:
                    raise ValueError("vendor_type must be a non-empty string")
                vendor_type = supplied["vendor_type"]
            documents = sample["documents"] if "documents" not in supplied else supplied["documents"]
        else:
            # Free text has no trustworthy control mapping. Keeping the list empty
            # prevents the model from treating it as evidence for every control.
            documents = [
                {
                    "text": user_input,
                    "control_ids": [],
                    "document_version": "marketplace-paste",
                }
            ]
        return vendor_type, {"documents": _normalised_documents(documents, allowed_control_ids)}

    def _resolved_sample_pins(self) -> tuple[dict[str, str], dict[str, str]]:
        catalogue = self._sample_records["control_catalogue"]
        perspective = self._sample_records["perspective_pack"]
        requested = {
            "control_catalogue_ref": catalogue["ref"],
            "control_catalogue_version": catalogue["version"],
            "control_catalogue_content_hash": catalogue["content_hash"],
            "perspective_pack_ref": perspective["ref"],
            "perspective_pack_version": perspective["version"],
            "perspective_pack_content_hash": perspective["content_hash"],
        }
        resolver = cast(PinResolver, self.config["pin_resolver"])
        resolved_catalogue, catalogue_sealed_ref = resolver.resolve(requested, "control_catalogue")
        resolved_perspective, perspective_sealed_ref = resolver.resolve(requested, "perspective_pack")
        pins = {
            "control_catalogue_ref": resolved_catalogue["ref"],
            "catalogue_version": resolved_catalogue["version"],
            "catalogue_content_hash": resolved_catalogue["content_hash"],
            "perspective_pack_ref": resolved_perspective["ref"],
            "perspective_pack_version": resolved_perspective["version"],
            "perspective_pack_content_hash": resolved_perspective["content_hash"],
            "mapping_pack_version": "",
            "mapping_pack_content_hash": "",
        }
        sealed = {
            "catalogue_sealed_ref": catalogue_sealed_ref,
            "perspective_pack_sealed_ref": perspective_sealed_ref,
        }
        return pins, sealed

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del input_context
        provider = cast(SecretProvider, self._secrets_provider)
        tenant_id = _require_clean(provider, "ASTAR_TENANT_ID")
        org_id = _require_clean(provider, "ASTAR_ORG_ID")
        vendor_type, evidence = self._marketplace_input(user_input)
        case_ref = mint_reference()
        payload_ref = cast(TransientPayloadStore, self.config["payload_store"]).put(
            evidence,
            tenant_id=tenant_id,
            org_id=org_id,
            case_ref=case_ref,
        )
        pins, sealed_content = self._resolved_sample_pins()
        marketplace_input_context = {
            "tenant_id": tenant_id,
            "org_id": org_id,
            "entry_mode": "invoke",
            "case_ref": case_ref,
            "vendor_id": self._sample_evidence["vendor_id"],
            "evidence_payload_ref": payload_ref,
            "pins": pins,
            "sealed_content": sealed_content,
        }
        marketplace_ctx = replace(ctx, hitl_allowed=False) if ctx is not None else None
        return cast(
            dict[str, Any],
            super().invoke(
                json.dumps({"vendor_type": vendor_type}, ensure_ascii=False),
                session_id=session_id,
                ctx=marketplace_ctx,
                input_context=marketplace_input_context,
            ),
        )

    def provision_secrets(self, provider: SecretProvider) -> None:
        del provider
        environment_provider = _AllowlistedEnvironmentProvider()
        _validate_required_environment(environment_provider)
        _require_azure_endpoint(environment_provider)
        super().provision_secrets(environment_provider)


def _validate_startup() -> None:
    provider = _AllowlistedEnvironmentProvider()
    _validate_required_environment(provider)
    validate_sample_data()


def main() -> None:
    _validate_startup()
    config = load_agent_config(Path(__file__).resolve().parent)
    run_agent_marketplace(
        MarketplaceGraph,
        agent_name=_AGENT_NAME,
        namespace=_NAMESPACE,
        config=config,
    )


if __name__ == "__main__":
    main()
