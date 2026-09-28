"""Ordered extractive provenance; shared by extraction and coverage.

Punctuation/whitespace may be omitted. Numeric atoms cannot be shortened or
assembled into a new value. Semantic scope and negation need the Claim validator.
"""

from __future__ import annotations

import re
import unicodedata

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def visible_characters(value: str) -> tuple[str, list[int]]:
    positions = [index for index, character in enumerate(value)
                 if not character.isspace() and not unicodedata.category(character).startswith("P")]
    return "".join(value[index] for index in positions), positions


def extractive_positions(source: str, claim: str) -> tuple[int, ...] | None:
    """Match ordered answer characters, preserving whole numeric tokens.

    Returning source positions enables coverage of shared subjects in several
    claims without changing stored Claim text or stable ID derivation.
    """
    source_numbers = iter(_NUMBER.findall(source))
    for number in _NUMBER.findall(claim):
        if not any(candidate == number for candidate in source_numbers):
            return None
    source_visible, positions = visible_characters(source)
    claim_visible, _ = visible_characters(claim)
    if not claim_visible:
        return None
    contiguous = source_visible.find(claim_visible)
    if contiguous >= 0:
        return tuple(positions[contiguous:contiguous + len(claim_visible)])
    cursor = 0
    matched = []
    for character in claim_visible:
        found = source_visible.find(character, cursor)
        if found < 0:
            return None
        matched.append(positions[found])
        cursor = found + 1
    return tuple(matched)
