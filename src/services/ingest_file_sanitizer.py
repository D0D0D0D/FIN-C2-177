"""Strict text-only ingest quality gate."""

from __future__ import annotations


class EvidenceFileError(ValueError):
    pass


def validate_text_evidence(value: object, *, min_chars: int = 1, max_chars: int = 200_000) -> str:
    """Accept text evidence only; binary/PDF/Office uploads are rejected by default."""
    if not isinstance(value, str):
        raise EvidenceFileError("only text evidence is supported")
    if not min_chars <= len(value.strip()) <= max_chars:
        raise EvidenceFileError("evidence text fails the configured quality gate")
    if value.startswith(("%PDF-", "PK\x03\x04", "\xd0\xcf\x11\xe0")):
        raise EvidenceFileError("binary document formats require a dedicated extractor")
    return value
