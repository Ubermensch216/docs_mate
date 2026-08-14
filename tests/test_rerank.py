"""근접 중복 축약·규칙 재정렬 시험 (RAG 개선 R7).

R6에서 넘어온 마지막 실패를 겨냥한다 — "계약관리 처리 순서"를 물었을 때
근거 8건이 전부 같은 [집계] 표였다. 파일은 여덟 개라 hash 중복 제거와
문서당 상한을 모두 통과했다. 내용으로 접어야 잡힌다.
"""

from __future__ import annotations

from app.search.rerank import (
    Candidate,
    collapse_near_duplicates,
    rerank,
)

TABLE = "[집계]\n구분\t1분기\t2분기\n처리건수\t41\t38"


def candidate(chunk_id: int, text: str, score: float = 0.01,
              filename: str = "문서.xlsx", eff_year: int | None = None) -> Candidate:
    return Candidate(chunk_id=chunk_id, score=score, text=text,
                     filename=filename, eff_year=eff_year)


# ── 근접 중복 축약 ──────────────────────────────────────────────────

def test_identical_content_across_different_files_collapses():
    """R6의 실패 그대로 — 파일 여덟 개, 내용 하나."""
    candidates = [
        candidate(i, TABLE, score=0.02 - i * 0.001, filename=f"실적취합_{i}.xlsx")
        for i in range(8)
    ]
    collapsed = collapse_near_duplicates(candidates)
    assert len(collapsed) == 1


def test_collapse_keeps_the_highest_ranked_copy():
    candidates = [
        candidate(1, TABLE, score=0.02, filename="최종.xlsx"),
        candidate(2, TABLE, score=0.01, filename="복사본.xlsx"),
    ]
    collapsed = collapse_near_duplicates(candidates)
    assert [c.chunk_id for c in collapsed] == [1]


def test_whitespace_only_differences_are_treated_as_the_same_content():
    candidates = [
        candidate(1, "구분  1분기\t2분기"),
        candidate(2, "구분 1분기 2분기"),
        candidate(3, "구분\n1분기\n2분기"),
    ]
    assert len(collapse_near_duplicates(candidates)) == 1


def test_different_content_survives_collapse():
    candidates = [
        candidate(1, "처리건수 41건"),
        candidate(2, "처리건수 38건"),   # 숫자가 다르면 다른 내용이다
    ]
    assert len(collapse_near_duplicates(candidates)) == 2


def test_collapse_preserves_order():
    candidates = [
        candidate(1, "가"), candidate(2, "나"), candidate(3, "가"), candidate(4, "다"),
    ]
    assert [c.chunk_id for c in collapse_near_duplicates(candidates)] == [1, 2, 4]


def test_collapse_handles_empty_input():
    assert collapse_near_duplicates([]) == []


# ── 규칙 재정렬 ─────────────────────────────────────────────────────

def test_query_term_present_in_text_is_promoted():
    """질의어가 본문에 그대로 있는 것이 가장 강한 신호다."""
    ranked = rerank([
        candidate(1, "다른 주제의 내용입니다", score=0.02),
        candidate(2, "계약관리 처리 절차입니다", score=0.019),
    ], question="계약관리 업무는 어떤 순서로 처리해?")
    assert ranked[0].chunk_id == 2


def test_query_term_matches_through_korean_particles():
    """한국어는 조사·어미가 붙는다 — 완전일치만 보면 자연어 질문이 거의 다 헛돈다.

    R3에서 FTS AND 결합이 같은 이유로 실패했고 escape_match_any로 고쳤다.
    여기서 같은 실수를 반복하지 않는지 못 박는다.
    """
    ranked = rerank([
        candidate(1, "전혀 다른 주제입니다", score=0.02),
        candidate(2, "계약관리 처리 절차입니다", score=0.019),
    ], question="계약관리는 어떻게 처리해?")
    assert ranked[0].chunk_id == 2


def test_short_query_words_do_not_match_everything():
    """접두 일치가 너무 느슨하면 아무 문서나 걸린다."""
    ranked = rerank([
        candidate(1, "아무 관련 없는 내용", score=0.02),
        candidate(2, "역시 관련 없는 내용", score=0.019),
    ], question="계약관리는 어떻게 처리해?")
    assert [c.chunk_id for c in ranked] == [1, 2]   # 순위가 그대로여야 한다


def test_final_marker_in_filename_is_promoted():
    ranked = rerank([
        candidate(1, "내용", score=0.02, filename="예산요구자료_초안.xlsx"),
        candidate(2, "내용", score=0.0199, filename="예산요구자료_송부.xlsx"),
    ], question="예산")
    assert ranked[0].chunk_id == 2


def test_latest_year_is_promoted():
    ranked = rerank([
        candidate(1, "내용", score=0.02, eff_year=2022),
        candidate(2, "내용", score=0.0199, eff_year=2025),
    ], question="자료", this_year=2025)
    assert ranked[0].chunk_id == 2


def test_rerank_does_not_overturn_a_large_search_gap():
    """규칙은 밀어 줄 뿐, 검색 순위를 통째로 뒤집으면 안 된다."""
    ranked = rerank([
        candidate(1, "질의어 없음", score=0.020, filename="초안.xlsx", eff_year=2020),
        candidate(2, "질의어 없음", score=0.002, filename="송부.xlsx", eff_year=2025),
    ], question="계약관리", this_year=2025)
    assert ranked[0].chunk_id == 1


def test_bonuses_stack():
    """여러 신호가 겹치면 더 크게 밀어 준다."""
    plain = candidate(1, "무관한 내용", score=0.02, filename="초안.xlsx", eff_year=2020)
    strong = candidate(2, "계약관리 내용", score=0.017, filename="송부.xlsx", eff_year=2025)
    ranked = rerank([plain, strong], question="계약관리", this_year=2025)
    assert ranked[0].chunk_id == 2


def test_rerank_handles_empty_input():
    assert rerank([], question="질문") == []


def test_rerank_is_stable_when_no_rule_applies():
    candidates = [candidate(1, "가", score=0.02), candidate(2, "나", score=0.01)]
    ranked = rerank(candidates, question="전혀다른질의어")
    assert [c.chunk_id for c in ranked] == [1, 2]


def test_rerank_uses_the_same_final_markers_as_the_reading_list():
    """업무 화면과 질문 근거가 다른 기준으로 순위를 매기면 사용자가 혼란스럽다."""
    from app.core.scoring import FINAL_MARKERS

    marker = FINAL_MARKERS[0][0]   # '송부'
    ranked = rerank([
        candidate(1, "내용", score=0.02, filename="문서_초안.xlsx"),
        candidate(2, "내용", score=0.0199, filename=f"문서_{marker}.xlsx"),
    ], question="문서")
    assert ranked[0].chunk_id == 2
