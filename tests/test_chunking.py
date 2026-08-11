"""청크 분할 시험."""

from __future__ import annotations

from app.core.chunking import MAX_CHUNK_CHARS, build_chunks


def test_short_sections_become_single_chunks():
    sections = [("1문단", "짧은 내용"), ("2문단", "또 다른 내용")]
    chunks = build_chunks(sections)
    assert [c[1:] for c in chunks] == [("1문단", "짧은 내용"), ("2문단", "또 다른 내용")]


def test_ordinals_are_sequential_across_sections():
    sections = [("1문단", "a"), ("2문단", "b"), ("3문단", "c")]
    chunks = build_chunks(sections)
    assert [c[0] for c in chunks] == [1, 2, 3]


def test_empty_or_blank_sections_are_skipped():
    sections = [("1문단", ""), ("2문단", "   "), ("3문단", "실제 내용")]
    chunks = build_chunks(sections)
    assert len(chunks) == 1
    assert chunks[0][1] == "3문단"


def test_long_section_is_split_with_the_same_locator():
    text = "가" * (MAX_CHUNK_CHARS * 2 + 100)
    chunks = build_chunks([("'시트1' A1:Z999", text)])
    assert len(chunks) == 3
    assert all(c[1] == "'시트1' A1:Z999" for c in chunks)
    assert sum(len(c[2]) for c in chunks) == len(text)


def test_split_pieces_do_not_exceed_the_limit():
    text = "나" * (MAX_CHUNK_CHARS * 3)
    chunks = build_chunks([("locator", text)])
    assert all(len(c[2]) <= MAX_CHUNK_CHARS for c in chunks)


def test_no_sections_yields_no_chunks():
    assert build_chunks([]) == []


def test_whitespace_is_trimmed_from_chunk_text():
    chunks = build_chunks([("1문단", "  앞뒤 공백  ")])
    assert chunks[0][2] == "앞뒤 공백"
