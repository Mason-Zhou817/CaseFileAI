import pytest

from casefile_ai.infrastructure.parsers.pdf import has_sufficient_native_text


@pytest.mark.parametrize("text", ["", " \n\t" * 20, "。！？" * 20, "�" * 20, "第1页"])
def test_insufficient_native_text_requires_ocr(text: str) -> None:
    assert not has_sufficient_native_text(text)


def test_ten_meaningful_characters_skip_ocr() -> None:
    assert has_sufficient_native_text("一二三四五六七八九十")
    assert not has_sufficient_native_text("一二三四五六七八九")
