"""Deterministic, page-local character-window chunking."""

from __future__ import annotations

import hashlib

from casefile_ai.application.retrieval_contracts import ChunkDraft, IndexablePage
from casefile_ai.domain.ids import derived_id

CHUNKING_VERSION = "page-char-v1-target500-overlap80-boundary100"
TARGET_CHARS = 500
OVERLAP_CHARS = 80
BOUNDARY_LOOKBACK = 100
_BOUNDARIES = frozenset("\n。！？；")


def chunk_page(page: IndexablePage) -> list[ChunkDraft]:
    """Split one page without normalizing text, preserving exact offsets."""

    if not page.text:
        return []
    drafts: list[ChunkDraft] = []
    start = 0
    length = len(page.text)
    while start < length:
        proposed_end = min(start + TARGET_CHARS, length)
        end = proposed_end
        if proposed_end < length:
            boundary_floor = max(start + 1, proposed_end - BOUNDARY_LOOKBACK)
            for position in range(proposed_end - 1, boundary_floor - 1, -1):
                if page.text[position] in _BOUNDARIES:
                    end = position + 1
                    break
        chunk_text = page.text[start:end]
        text_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        chunk_index = len(drafts)
        drafts.append(
            ChunkDraft(
                chunk_id=derived_id(
                    "chunk",
                    page.page_id,
                    CHUNKING_VERSION,
                    chunk_index,
                    start,
                    end,
                    text_hash,
                ),
                case_id=page.case_id,
                page_id=page.page_id,
                chunk_index=chunk_index,
                text=chunk_text,
                text_hash=text_hash,
                start_offset=start,
                end_offset=end,
                chunking_version=CHUNKING_VERSION,
            )
        )
        if end == length:
            break
        next_start = max(0, end - OVERLAP_CHARS)
        start = next_start if next_start > start else end
    return drafts
