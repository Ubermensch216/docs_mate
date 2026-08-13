"""문장별 근거 검증 시험 (RAG 개선 R5 — 이 계획의 핵심).

모델이 "sources"를 스스로 밝혀도, 그 근거가 실제로 문장을 뒷받침하는지는
모델이 보장하지 않는다. 이 파일은 verify_sentences()가 그 격차를 규칙으로
메우는지 확인한다 — 근거 없는 문장, 지어낸 숫자, 존재하지 않는 인용
번호를 실제로 걸러내는가.
"""

from __future__ import annotations

from app.search.verify import verify_sentences

SOURCES = {
    1: "2025년 행정사무감사 요구자료 접수는 9월에 이루어졌다",
    2: "계약관리 업무의 처리건수는 1분기 41건이었다",
}


def sentence(text: str, sources: list) -> dict:
    return {"text": text, "sources": sources}


# ── 근거 없는 문장 ──────────────────────────────────────────────────

def test_sentence_without_sources_is_dropped():
    result = verify_sentences([sentence("근거 없이 말한다", [])], SOURCES)
    assert result.kept == []
    assert result.dropped[0].reason == "근거를 스스로 밝히지 않았다"


def test_sentence_with_null_sources_is_dropped():
    """모델이 sources를 아예 안 주면 None일 수 있다 — []와 같이 취급한다."""
    result = verify_sentences([{"text": "말한다", "sources": None}], SOURCES)
    assert result.kept == []


def test_blank_text_is_silently_skipped_not_reported_as_dropped():
    """빈 문장은 검증 실패가 아니라 애초에 문장이 아니다 — 사유를 남기지 않는다."""
    result = verify_sentences([sentence("   ", [1])], SOURCES)
    assert result.kept == []
    assert result.dropped == []


# ── 존재하지 않는 근거 번호 ─────────────────────────────────────────

def test_sentence_citing_a_nonexistent_source_is_dropped():
    result = verify_sentences([sentence("9월에 접수되었다", [99])], SOURCES)
    assert result.kept == []
    assert result.dropped[0].reason == "존재하지 않는 근거 번호를 인용했다"


def test_partially_valid_sources_keep_only_the_real_ones():
    """일부는 진짜 번호, 일부는 지어낸 번호 — 진짜 것만으로 검증한다."""
    result = verify_sentences([sentence("9월에 접수되었다", [1, 99])], SOURCES)
    assert result.kept and result.kept[0].sources == [1]


# ── 숫자 검증 ───────────────────────────────────────────────────────

def test_number_absent_from_cited_source_is_dropped():
    """실측: '41건/38건/45건/39건'처럼 무관한 표를 그대로 옮겨 붙인 사례가 있었다."""
    result = verify_sentences(
        [sentence("2분기에는 88건이 처리되었다", [1])], SOURCES,
    )
    assert result.kept == []
    assert "88" in result.dropped[0].reason


def test_number_present_in_cited_source_survives():
    result = verify_sentences(
        [sentence("1분기 처리건수는 41건이었다", [2])], SOURCES,
    )
    assert len(result.kept) == 1


def test_single_digit_numbers_are_not_flagged():
    """'1분기'의 '1'처럼 한 자리 순번까지 걸면 정상 문장을 과하게 지운다."""
    result = verify_sentences(
        [sentence("1분기 처리건수를 확인했다", [2])], SOURCES,
    )
    assert len(result.kept) == 1


def test_number_check_looks_only_at_the_sentences_own_cited_sources():
    """근거로 밝히지 않은 다른 조각에 그 숫자가 있어도 소용없다 — 인용을 안 했다."""
    result = verify_sentences(
        [sentence("1분기 처리건수는 41건이었다", [1])], SOURCES,   # source 1에는 '41'이 없다
    )
    assert result.kept == []


# ── 어휘 중첩 ───────────────────────────────────────────────────────

def test_sentence_unrelated_to_its_cited_source_is_dropped():
    result = verify_sentences(
        [sentence("오늘 날씨가 참 좋고 기분이 상쾌하다", [1])], SOURCES,
    )
    assert result.kept == []
    assert result.dropped[0].reason == "인용한 근거와 겹치는 말이 거의 없다"


def test_sentence_grounded_in_its_source_survives():
    result = verify_sentences(
        [sentence("행정사무감사 요구자료 접수는 9월에 이루어졌다", [1])], SOURCES,
    )
    assert len(result.kept) == 1


# ── 여러 문장 ───────────────────────────────────────────────────────

def test_some_sentences_survive_while_others_are_dropped():
    """전부 버리지 않는다 — 나쁜 문장 하나가 좋은 문장까지 끌고 내려가지 않는다."""
    result = verify_sentences([
        sentence("행정사무감사 요구자료 접수는 9월에 이루어졌다", [1]),
        sentence("근거 없이 지어낸 말이다", []),
    ], SOURCES)
    assert len(result.kept) == 1
    assert len(result.dropped) == 1


def test_multi_source_sentence_needs_the_number_in_any_cited_source():
    """여러 근거를 합친 문장은 숫자가 그중 하나에만 있어도 통과한다."""
    result = verify_sentences(
        [sentence("접수는 9월이었고 처리건수는 41건이었다", [1, 2])], SOURCES,
    )
    assert len(result.kept) == 1


def test_empty_input_yields_empty_result():
    result = verify_sentences([], SOURCES)
    assert result.kept == []
    assert result.dropped == []
    assert not result.survived


def test_non_dict_items_are_ignored_without_crashing():
    """모델이 스키마를 어겨 문자열 등을 섞어 보내도 죽지 않는다."""
    result = verify_sentences(["문자열", 42, None, sentence("행정사무감사 접수", [1])], SOURCES)
    assert len(result.kept) == 1


def test_boolean_is_not_mistaken_for_a_source_index():
    """bool은 파이썬에서 int의 서브클래스다 — 실수로 근거 번호로 세면 안 된다."""
    result = verify_sentences([sentence("접수되었다", [True, False])], SOURCES)
    assert result.kept == []
    assert result.dropped[0].reason == "근거를 스스로 밝히지 않았다"


def test_survived_reflects_whether_anything_is_kept():
    assert verify_sentences([sentence("행정사무감사 접수", [1])], SOURCES).survived
    assert not verify_sentences([sentence("근거 없음", [])], SOURCES).survived
