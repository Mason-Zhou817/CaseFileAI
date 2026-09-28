"""Small PDF/OCR probe implementation with one-based page provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    text: str
    extraction_method: str
    mean_confidence: float | None


class RapidOcrPageReader:
    """Lazy RapidOCR wrapper used by the Day 1 feasibility probe."""

    def __init__(self) -> None:
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def read(self, page: pymupdf.Page, scale: float = 1.5) -> tuple[str, float | None]:
        matrix = pymupdf.Matrix(scale, scale)  # type: ignore[no-untyped-call]
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        result = self._get_engine()(image)
        texts = tuple(result.txts or ())
        scores = tuple(float(score) for score in (result.scores or ()))
        confidence = sum(scores) / len(scores) if scores else None
        return "\n".join(texts), confidence


def has_sufficient_native_text(text: str, threshold: int = 10) -> bool:
    """Ignore whitespace, punctuation and replacement glyphs when selecting OCR."""
    return sum(character.isalnum() for character in text) >= threshold


def extract_pdf_pages(
    path: Path,
    *,
    native_text_threshold: int = 10,
    ocr_reader: RapidOcrPageReader | None = None,
) -> list[ExtractedPage]:
    """Extract each PDF page, using OCR only when native text is absent."""

    reader = ocr_reader or RapidOcrPageReader()
    extracted: list[ExtractedPage] = []
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        for zero_based_index, page in enumerate(document):
            native_text = page.get_text().strip()
            if has_sufficient_native_text(native_text, native_text_threshold):
                extracted.append(
                    ExtractedPage(
                        page_number=zero_based_index + 1,
                        text=native_text,
                        extraction_method="native",
                        mean_confidence=None,
                    )
                )
                continue

            text, confidence = reader.read(page)
            extracted.append(
                ExtractedPage(
                    page_number=zero_based_index + 1,
                    text=text if text.strip() else native_text,
                    extraction_method="ocr",
                    mean_confidence=confidence,
                )
            )
    return extracted
