"""Chinese-aware FTS5 preprocessing for the MVP keyword index.

SQLite's built-in unicode61 tokenizer recognizes Unicode characters but does
not perform Chinese word segmentation. We therefore index a deterministic,
space-separated shadow representation while retaining the original chunk text
elsewhere for citation display.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import jieba  # type: ignore[import-untyped]

jieba.setLogLevel(20)

TOKENIZATION_VERSION = "jieba-bigram-date-amount-page-v2"

_HAN_RUN = re.compile(r"[\u3400-\u9fff]+")
_DATE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日")
_DATE_NUMERIC = re.compile(
    r"(?<!\d)(?P<year>20\d{2})(?P<separator>[-/])(?P<month>\d{1,2})"
    r"(?P=separator)(?P<day>\d{1,2})(?!\d)"
)
_PAGE_REFERENCE = re.compile(r"第\s*(?P<page>\d{1,3})\s*[页頁]")
_TEN_THOUSAND_AMOUNT = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*万\s*元")
_YUAN_AMOUNT = re.compile(r"(?<![\d.])(?P<amount>\d{3,})\s*元")
_SAFE_TOKEN = re.compile(r"^[\w\u3400-\u9fff]+$", re.UNICODE)


@dataclass(frozen=True, slots=True)
class KeywordHit:
    document_id: str
    score: float


def _amount_token(value: Decimal) -> str:
    integral = value.quantize(Decimal("1"))
    return f"amount_{integral}"


def _date_token(year: str, month: str, day: str) -> str | None:
    try:
        value = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return f"date_{value.year:04d}_{value.month:02d}_{value.day:02d}"


def chinese_search_tokens(text: str) -> tuple[str, ...]:
    """Build deterministic Chinese word, bigram, date, and amount tokens."""

    normalized = unicodedata.normalize("NFKC", text).strip()
    candidates: list[str] = []

    for token in jieba.lcut(normalized, cut_all=False):
        token = token.strip().lower()
        if token and _SAFE_TOKEN.fullmatch(token):
            candidates.append(token)

    for run in _HAN_RUN.findall(normalized):
        if len(run) == 1:
            candidates.append(run)
        else:
            candidates.extend(run[index : index + 2] for index in range(len(run) - 1))

    for pattern in (_DATE, _DATE_NUMERIC):
        for match in pattern.finditer(normalized):
            token = _date_token(match.group("year"), match.group("month"), match.group("day"))
            if token is not None:
                candidates.append(token)

    for match in _PAGE_REFERENCE.finditer(normalized):
        number = int(match.group("page"))
        if 1 <= number <= 500:
            candidates.append(f"page_{number}")

    for match in _TEN_THOUSAND_AMOUNT.finditer(normalized):
        candidates.append(_amount_token(Decimal(match.group("amount")) * 10_000))

    for match in _YUAN_AMOUNT.finditer(normalized):
        candidates.append(_amount_token(Decimal(match.group("amount"))))

    return tuple(dict.fromkeys(candidates))


def prepare_fts_text(text: str, *, page_number: int | None = None) -> str:
    """Return the pre-tokenized shadow text stored in FTS5."""
    tokens = chinese_search_tokens(text)
    if page_number is not None:
        return " ".join((*tokens, f"page_{page_number}"))
    return " ".join(tokens)


def prepare_match_query(query: str) -> str:
    """Return safely quoted terms, constraining a single explicit page reference."""

    tokens = chinese_search_tokens(query)
    normalized_fact_tokens = tuple(
        token for token in tokens if token.startswith(("date_", "amount_"))
    )
    page_references = {int(match.group("page")) for match in _PAGE_REFERENCE.finditer(
        unicodedata.normalize("NFKC", query)
    ) if 1 <= int(match.group("page")) <= 500}
    if len(page_references) == 1 and not normalized_fact_tokens:
        page_token = f"page_{next(iter(page_references))}"
        without_page = _PAGE_REFERENCE.sub(" ", unicodedata.normalize("NFKC", query))
        terms = chinese_search_tokens(without_page)
        if terms:
            return f'"{page_token}" AND (' + " OR ".join(f'"{token}"' for token in terms) + ")"
        return f'"{page_token}"'
    selected = normalized_fact_tokens or tokens
    return " OR ".join(f'"{token}"' for token in selected)


class ChineseFtsProbeIndex:
    """Minimal in-memory-compatible FTS5 index used by the Day 1 probe."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chinese_chunks "
            "USING fts5(document_id UNINDEXED, search_text, tokenize='unicode61')"
        )

    def add(self, document_id: str, text: str) -> None:
        self._connection.execute(
            "INSERT INTO chinese_chunks(document_id, search_text) VALUES (?, ?)",
            (document_id, prepare_fts_text(text)),
        )
        self._connection.commit()

    def search(self, query: str, limit: int = 10) -> list[KeywordHit]:
        match_query = prepare_match_query(query)
        if not match_query:
            return []
        rows = self._connection.execute(
            "SELECT document_id, bm25(chinese_chunks) AS score "
            "FROM chinese_chunks WHERE chinese_chunks MATCH ? "
            "ORDER BY score LIMIT ?",
            (match_query, limit),
        ).fetchall()
        return [KeywordHit(document_id=str(row[0]), score=float(row[1])) for row in rows]
