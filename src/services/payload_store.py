"""Short-lived, scope-bound storage for evidence bodies.

Only opaque alphabetic references cross the graph/checkpoint boundary.  The
in-memory implementation is intentionally an injection seam; production may
replace it with an equivalent TTL-backed store.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


class PayloadStoreError(RuntimeError):
    """Raised when a payload reference cannot safely be resolved."""


def mint_reference() -> str:
    """Return a cryptographically random reference containing letters only."""
    return "".join(chr(ord("a") + nibble) for byte in secrets.token_bytes(16) for nibble in (byte >> 4, byte & 15))


@dataclass
class _PayloadEntry:
    value: Any
    binding: tuple[str, str, str]
    expires_at: float
    consumed: bool = False


class TransientPayloadStore:
    """TTL and single-use transient store bound to tenant, organisation and case."""

    def __init__(self, ttl_s: int = 900) -> None:
        if not isinstance(ttl_s, int) or ttl_s <= 0:
            raise ValueError("payload TTL must be a positive integer")
        self._ttl_s = ttl_s
        self._entries: dict[str, _PayloadEntry] = {}

    def put(self, value: Any, *, tenant_id: str, org_id: str, case_ref: str) -> str:
        if not all(isinstance(item, str) and item for item in (tenant_id, org_id, case_ref)):
            raise PayloadStoreError("payload binding requires tenant_id, org_id, and case_ref")
        reference = mint_reference()
        self._entries[reference] = _PayloadEntry(
            value=value,
            binding=(tenant_id, org_id, case_ref),
            expires_at=time.monotonic() + self._ttl_s,
        )
        return reference

    def resolve(self, reference: str, *, tenant_id: str, org_id: str, case_ref: str) -> Any:
        entry = self._entries.get(reference)
        if entry is None or entry.consumed or entry.expires_at <= time.monotonic():
            self._entries.pop(reference, None)
            raise PayloadStoreError("payload_ref_unknown_or_expired")
        if entry.binding != (tenant_id, org_id, case_ref):
            raise PayloadStoreError("payload_ref_scope_mismatch")
        entry.consumed = True
        return entry.value
