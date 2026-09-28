"""Stable identifier helpers.

Root entities receive random UUIDv4 identifiers. Derived source records use UUIDv5
over their complete versioned identity so unchanged inputs resolve consistently.
"""

from __future__ import annotations

import uuid
from typing import NewType

CaseId = NewType("CaseId", str)
DocumentId = NewType("DocumentId", str)
DocumentVersionId = NewType("DocumentVersionId", str)
PageId = NewType("PageId", str)
ChunkId = NewType("ChunkId", str)
ClaimId = NewType("ClaimId", str)
EvidenceId = NewType("EvidenceId", str)
ConflictId = NewType("ConflictId", str)

_NAMESPACE = uuid.UUID("e9c97a92-09e2-4f98-845e-a18b4380771a")


def new_id() -> str:
    """Return a persisted random root identifier."""

    return str(uuid.uuid4())


def derived_id(kind: str, *identity_parts: object) -> str:
    """Return a deterministic ID for an immutable, fully-versioned record."""

    if not kind.strip():
        raise ValueError("kind must not be empty")
    normalized = "\x1f".join([kind, *(str(part) for part in identity_parts)])
    return str(uuid.uuid5(_NAMESPACE, normalized))
