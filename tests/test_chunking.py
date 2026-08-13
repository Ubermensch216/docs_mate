"""청크 분할 시험 (v2 — 조각 재설계).

실측(2026-08, 실제 프로젝트 77건)에서 조각 중앙값이 34자였다. Top-8을
모아도 300자 남짓이라 모델에게 답할 재료가 없었다 — 유보 정확도 0/3의
원인 중 하나였다(doc/질문화면_RAG_개선계획.md). 이 파일은 산문류가 목표
창으로 병합되는지, 표·시트·슬라이드는 병합에서 제외되는지를 못 박는다.
"""

from __future__ import annotations

from app.core.chunking import (
    MAX_CHUNK_CHARS,
    MERGEABLE_KINDS,
    TARGET_MAX_CHARS,
    TARGET_MIN_CHARS,
    build_chunks,
)

PARA = "paragraph"
TABLE = "table"
SHEET = "sheet"
SLIDE = "slide"
PAGE = "page"


# ── 짧은 조각은 병합된다 ────────────────────────────────────────────

def test_short_paragraphs_are_merged_into_one_chunk():
    """이 파일의 존재 이유. 9자짜리 조각을 단독으로 두지 않는다."""
    sections = [
        (PARA, "1문단", "2025_행정사무감사_요구자료접수_최종2"),
        (PARA, "2문단", "2025년 행정사무감사 요구자료 접수"),
        (PARA, "3문단", "문서유형: 공문"),
        (PARA, "4문단", "작성일: 2025. 9. 12."),
    ]
    chunks = build_chunks(sections)
    assert len(chunks) == 1
    assert chunks[0][1] == "1~4문단"
    assert "작성일" in chunks[0][2]


def test_merged_locator_is_a_range():
    chunks = build_chunks([
        (PARA, "1문단", "가" * 100), (PARA, "2문단", "나" * 100),
        (PARA, "3문단", "다" * 100),
    ])
    assert chunks[0][1] == "1~3문단"


def test_merging_stops_once_the_target_window_is_reached():
    """400자를 넘기면 더 끌어오지 않는다 — 무한정 커지지 않는다."""
    sections = [(PARA, f"{i}문단", "가" * 150) for i in range(1, 6)]
    chunks = build_chunks(sections)
    assert len(chunks) >= 2
    for _ordinal, _locator, text in chunks:
        assert len(text) < TARGET_MIN_CHARS + TARGET_MAX_CHARS


def test_a_single_section_already_at_the_target_is_left_alone():
    body = "가" * (TARGET_MAX_CHARS - 1)
    chunks = build_chunks([(PARA, "1문단", body), (PARA, "2문단", "짧음")])
    assert chunks[0] == (1, "1문단", body)
    # 두 번째 섹션은 첫 섹션과 합쳐지지 않는다 — 첫 섹션 자체가 이미 상한급이라
    # 먼저 flush됐다.
    assert chunks[1][1] == "2문단"


# ── 병합 창을 넘지 않는다 (하드 캡) ─────────────────────────────────

def test_merged_chunks_never_exceed_the_hard_cap():
    sections = [(PARA, f"{i}문단", "가" * 90) for i in range(1, 40)]
    chunks = build_chunks(sections)
    assert all(len(text) <= MAX_CHUNK_CHARS for _o, _l, text in chunks)


def test_all_input_text_is_preserved_across_merged_chunks():
    sections = [(PARA, f"{i}문단", f"내용{i}") for i in range(1, 10)]
    chunks = build_chunks(sections)
    combined = "".join(text for _o, _l, text in chunks)
    for i in range(1, 10):
        assert f"내용{i}" in combined


# ── 표·시트·슬라이드는 병합하지 않는다 ──────────────────────────────

def test_tables_are_never_merged_with_neighboring_paragraphs():
    """구조 자체가 의미다 — 표가 문단과 섞이면 locator가 무의미해진다."""
    sections = [
        (PARA, "1문단", "짧은 서론"),
        (TABLE, "표1", "항목\t수량\n요구자료\t12"),
        (PARA, "2문단", "짧은 결론"),
    ]
    chunks = build_chunks(sections)
    locators = [c[1] for c in chunks]
    assert "표1" in locators
    table_chunk = next(c for c in chunks if c[1] == "표1")
    assert table_chunk[2] == "항목\t수량\n요구자료\t12"


def test_consecutive_tables_stay_separate_chunks():
    chunks = build_chunks([(TABLE, "표1", "a"), (TABLE, "표2", "b")])
    assert [c[1] for c in chunks] == ["표1", "표2"]


def test_sheets_and_slides_are_excluded_from_merging():
    assert "sheet" not in MERGEABLE_KINDS
    assert "slide" not in MERGEABLE_KINDS
    assert "table" not in MERGEABLE_KINDS


def test_kind_switch_flushes_the_buffer_even_when_both_are_mergeable():
    """paragraph 다음에 page가 와도 서로 다른 창에 담긴다 — 섞어 합치지 않는다."""
    chunks = build_chunks([
        (PARA, "1문단", "가" * 50), (PAGE, "2쪽", "나" * 50), (PARA, "3문단", "다" * 50),
    ])
    assert [c[1] for c in chunks] == ["1문단", "2쪽", "3문단"]


# ── 원래 있던 규칙(그대로 유지) ─────────────────────────────────────

def test_ordinals_are_sequential():
    chunks = build_chunks([(PARA, "1문단", "a"), (TABLE, "표1", "b")])
    assert [c[0] for c in chunks] == [1, 2]


def test_empty_or_blank_sections_are_skipped():
    sections = [(PARA, "1문단", ""), (PARA, "2문단", "   "), (PARA, "3문단", "실제 내용")]
    chunks = build_chunks(sections)
    assert len(chunks) == 1
    assert chunks[0][1] == "3문단"


def test_long_section_is_split_with_the_same_locator():
    text = "가" * (MAX_CHUNK_CHARS * 2 + 100)
    chunks = build_chunks([(SHEET, "'시트1' A1:Z999", text)])
    assert len(chunks) == 3
    assert all(c[1] == "'시트1' A1:Z999" for c in chunks)
    assert sum(len(c[2]) for c in chunks) == len(text)


def test_split_pieces_do_not_exceed_the_limit():
    text = "나" * (MAX_CHUNK_CHARS * 3)
    chunks = build_chunks([(SHEET, "locator", text)])
    assert all(len(c[2]) <= MAX_CHUNK_CHARS for c in chunks)


def test_no_sections_yields_no_chunks():
    assert build_chunks([]) == []


def test_whitespace_is_trimmed_from_chunk_text():
    chunks = build_chunks([(PARA, "1문단", "  앞뒤 공백  ")])
    assert chunks[0][2] == "앞뒤 공백"


def test_merged_whitespace_only_pieces_do_not_survive():
    chunks = build_chunks([(PARA, "1문단", "실제"), (PARA, "2문단", "   ")])
    assert len(chunks) == 1
    assert chunks[0][2] == "실제"
