"""Authoritative pin seam and fail-loud fictional Marketplace sample loader."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.services.payload_store import mint_reference

_SAMPLE_NOTICE = "FICTIONAL SAMPLE"
_SAMPLE_FILES = {
    "control_catalogue": "control_catalogue.sample.json",
    "perspective_pack": "perspective_pack.sample.json",
    "evidence": "evidence.sample.json",
}


def _config_dir() -> Path:
    """Resolve bundled data from this module, never from the process CWD."""

    return Path(__file__).resolve().parents[2] / "config"


def _read_json(name: str, *, config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir if config_dir is not None else _config_dir()) / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"required sample data is unreadable: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"required sample data is invalid JSON: {path.name}") from exc
    if not isinstance(value, dict) or not value:
        raise RuntimeError(f"required sample data must be a non-empty object: {path.name}")
    return value


def canonical_content_hash(content: object) -> str:
    """Return the pin hash over canonical UTF-8 JSON content."""

    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validated_pin_record(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    list_key = "controls" if kind == "control_catalogue" else "rules"
    version_key = "catalogue_version" if kind == "control_catalogue" else "perspective_pack_version"
    required = {
        "sample_notice",
        "ref",
        "version",
        version_key,
        "content_hash",
        "approved",
        "effective_from",
        "effective_to",
        "content",
    }
    if not required.issubset(value):
        raise RuntimeError(f"{kind} sample is missing required keys")
    if _SAMPLE_NOTICE not in str(value["sample_notice"]):
        raise RuntimeError(f"{kind} sample must identify itself as fictional sample data")
    if not all(isinstance(value[key], str) and value[key] for key in ("ref", "version", version_key)):
        raise RuntimeError(f"{kind} sample ref and version must be non-empty strings")
    if value["version"] != value[version_key] or value["version"] != "SAMPLE-2026.1":
        raise RuntimeError(f"{kind} sample version is inconsistent")
    if value["approved"] is not True:
        raise RuntimeError(f"{kind} sample must be explicitly approved for sample use")
    try:
        effective_from = date.fromisoformat(value["effective_from"])
        effective_to = date.fromisoformat(value["effective_to"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{kind} sample effective dates are invalid") from exc
    if effective_from > date.today() or effective_to < date.today():
        raise RuntimeError(f"{kind} sample is outside its effective period")
    content = value["content"]
    items = content.get(list_key) if isinstance(content, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
        raise RuntimeError(f"{kind} sample content must contain non-empty {list_key}")
    expected_hash = canonical_content_hash(content)
    if value["content_hash"] != expected_hash:
        raise RuntimeError(f"{kind} sample content_hash does not match its content")
    return deepcopy(value)


def load_sample_pin_records(*, config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load and fully validate both bundled sample pin records."""

    records = {
        kind: _validated_pin_record(kind, _read_json(filename, config_dir=config_dir))
        for kind, filename in _SAMPLE_FILES.items()
        if kind != "evidence"
    }
    controls = records["control_catalogue"]["content"]["controls"]
    rules = records["perspective_pack"]["content"]["rules"]
    if not 8 <= len(controls) <= 12 or not 4 <= len(rules) <= 8:
        raise RuntimeError("sample catalogue or perspective pack count is outside the declared range")
    if (
        records["control_catalogue"]["ref"] != "SAMPLE-CATALOGUE"
        or records["perspective_pack"]["ref"] != "SAMPLE-PERSPECTIVE-PACK"
    ):
        raise RuntimeError("sample pin references are inconsistent")
    for control in controls:
        if not {"control_id", "requirement_text", "requirement_text_hash"}.issubset(control):
            raise RuntimeError("sample control is missing required keys")
        if (
            not isinstance(control["requirement_text"], str)
            or not control["requirement_text"]
            or control["requirement_text_hash"]
            != hashlib.sha256(control["requirement_text"].encode("utf-8")).hexdigest()
        ):
            raise RuntimeError("sample control requirement text or hash is invalid")
    control_ids = {item.get("control_id") for item in controls}
    if len(control_ids) != len(controls) or not all(
        isinstance(control_id, str) and control_id.startswith("SAMPLE-CTL-") for control_id in control_ids
    ):
        raise RuntimeError("sample controls require unique SAMPLE-CTL identifiers")
    catalogue = records["control_catalogue"]
    for rule in rules:
        if not {
            "rule_id",
            "applies_to",
            "applicability",
            "expected_evidence",
            "minimum_sufficiency",
            "contradiction_signals",
            "verification_questions",
            "approval",
        }.issubset(rule):
            raise RuntimeError("sample perspective rule is missing required keys")
        applies_to = rule.get("applies_to")
        applicability = rule.get("applicability")
        rule_ids = applies_to.get("control_ids") if isinstance(applies_to, dict) else None
        expected_evidence = rule.get("expected_evidence")
        approval = rule.get("approval")
        if (
            not isinstance(rule.get("rule_id"), str)
            or not rule["rule_id"].startswith("SAMPLE-RULE-")
            or not isinstance(applies_to, dict)
            or applies_to.get("catalogue_id") != catalogue["ref"]
            or applies_to.get("catalogue_version") != catalogue["version"]
            or not isinstance(rule_ids, list)
            or not rule_ids
            or not set(rule_ids).issubset(control_ids)
            or not isinstance(applicability, dict)
            or not isinstance(applicability.get("vendor_types"), list)
            or not all(isinstance(item, str) for item in applicability["vendor_types"])
            or not isinstance(applicability.get("service_scope_fingerprints"), list)
            or not all(isinstance(item, str) for item in applicability["service_scope_fingerprints"])
            or not isinstance(expected_evidence, list)
            or not expected_evidence
            or not all(isinstance(item, dict) for item in expected_evidence)
            or not isinstance(rule["minimum_sufficiency"], str)
            or not rule["minimum_sufficiency"]
            or not isinstance(rule["contradiction_signals"], list)
            or not isinstance(rule["verification_questions"], list)
            or not isinstance(approval, dict)
            or approval.get("status") != "approved"
        ):
            raise RuntimeError("sample perspective rule is inconsistent with the catalogue")
    return records


def load_sample_evidence(*, config_dir: Path | None = None) -> dict[str, Any]:
    """Load fictional sample evidence and verify every document hash and control link."""

    value = _read_json(_SAMPLE_FILES["evidence"], config_dir=config_dir)
    required = {"sample_notice", "vendor_id", "vendor_name", "vendor_type", "documents"}
    if not required.issubset(value) or _SAMPLE_NOTICE not in str(value["sample_notice"]):
        raise RuntimeError("evidence sample is missing its required sample identity")
    if not all(isinstance(value[key], str) and value[key] for key in ("vendor_id", "vendor_name", "vendor_type")):
        raise RuntimeError("evidence sample vendor fields must be non-empty strings")
    documents = value["documents"]
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("evidence sample documents must be a non-empty list")
    control_ids = {
        item["control_id"]
        for item in load_sample_pin_records(config_dir=config_dir)["control_catalogue"]["content"]["controls"]
    }
    for document in documents:
        if not isinstance(document, dict) or not {
            "text",
            "control_ids",
            "document_version",
            "content_hash",
        }.issubset(document):
            raise RuntimeError("evidence sample document is missing required keys")
        if not isinstance(document["text"], str) or not document["text"]:
            raise RuntimeError("evidence sample document text must be non-empty")
        if (
            not isinstance(document["control_ids"], list)
            or not document["control_ids"]
            or not all(isinstance(item, str) and item in control_ids for item in document["control_ids"])
        ):
            raise RuntimeError("evidence sample document has an invalid control mapping")
        if not isinstance(document["document_version"], str) or not document["document_version"]:
            raise RuntimeError("evidence sample document version must be non-empty")
        if document["content_hash"] != hashlib.sha256(document["text"].encode("utf-8")).hexdigest():
            raise RuntimeError("evidence sample document content_hash does not match its text")
    return deepcopy(value)


def validate_sample_data() -> None:
    """Fail loudly unless every bundled Marketplace sample is usable."""

    load_sample_pin_records()
    load_sample_evidence()


class PinResolver:
    """In-memory authoritative pin repository seam; callers never supply content directly."""

    def __init__(self, sealed_content: dict[str, dict]) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._sealed_content = sealed_content

    def register(self, record: dict[str, Any]) -> None:
        ref, version = record.get("ref"), record.get("version")
        if not isinstance(ref, str) or not isinstance(version, str):
            raise ValueError("pin record requires ref and version")
        self._records[(ref, version)] = deepcopy(record)

    def resolve(self, requested: dict[str, Any], kind: str) -> tuple[dict[str, Any], str]:
        ref = requested.get(f"{kind}_ref")
        version = requested.get(f"{kind}_version")
        content_hash = requested.get(f"{kind}_content_hash")
        record = self._records.get((ref, version))
        if not record or not isinstance(content_hash, str):
            raise HTTPException(status_code=400, detail="Pinned content is unavailable.")
        if record.get("content_hash") != content_hash or not record.get("approved"):
            raise HTTPException(status_code=403, detail="Pinned content cannot be used.")
        today = date.today().isoformat()
        if record.get("effective_from", "0000-01-01") > today or record.get("effective_to", "9999-12-31") < today:
            raise HTTPException(status_code=403, detail="Pinned content cannot be used.")
        sealed_ref = mint_reference()
        self._sealed_content[sealed_ref] = deepcopy(record.get("content", {}))
        return deepcopy(record), sealed_ref


def register_sample_pins(resolver: PinResolver) -> dict[str, dict[str, Any]]:
    """Register the bundled records; repeated calls overwrite with identical values."""

    records = load_sample_pin_records()
    for record in records.values():
        resolver.register(record)
    return records
