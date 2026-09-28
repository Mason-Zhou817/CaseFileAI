from casefile_ai.application.retrieval_contracts import IndexablePage
from casefile_ai.application.services.chunking import (
    CHUNKING_VERSION,
    OVERLAP_CHARS,
    TARGET_CHARS,
    chunk_page,
)


def page(text: str, page_id: str = "page-1") -> IndexablePage:
    return IndexablePage("case-1", "doc-1", "version-1", page_id, 1, text)


def test_short_page_produces_one_stable_exact_chunk() -> None:
    source = page("王海于2021年3月5日支付50万元。")

    first = chunk_page(source)
    second = chunk_page(source)

    assert first == second
    assert len(first) == 1
    assert first[0].text == source.text
    assert first[0].start_offset == 0
    assert first[0].end_offset == len(source.text)
    assert first[0].chunking_version == CHUNKING_VERSION


def test_long_page_uses_page_local_offsets_and_bounded_overlap() -> None:
    text = "".join(f"第{index:03d}项事实：王海确认支付投资款。" for index in range(80))
    chunks = chunk_page(page(text))

    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.page_id == "page-1"
        assert chunk.case_id == "case-1"
        assert chunk.text == text[chunk.start_offset : chunk.end_offset]
        assert len(chunk.text) <= TARGET_CHARS
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert 0 <= left.end_offset - right.start_offset <= OVERLAP_CHARS


def test_chunks_from_different_pages_never_share_identity() -> None:
    text = "相同页内文本"

    left = chunk_page(page(text, "page-1"))[0]
    right = chunk_page(page(text, "page-2"))[0]

    assert left.chunk_id != right.chunk_id
