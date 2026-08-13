"""질문 연도 추론 시험 (RAG 개선 R4).

실측(§1 측정 3): "작년 예산 요구자료는 누가 만들었어?"에 2022~2025년
자료가 뒤섞여 근거로 들어왔다. 연도를 규칙으로 뽑아 필터링의 재료로
쓴다 — 생성 모델에게 맡기지 않는다(원칙 1).
"""

from __future__ import annotations

from datetime import date

from app.search.query import infer_scope

TODAY = date(2026, 8, 12)


def test_absolute_year_is_recognized():
    scope = infer_scope("2025년 행정사무감사 때 뭘 제출했어?", today=TODAY)
    assert scope.years == [2025]


def test_absolute_year_requires_a_suffix():
    """그냥 4자리 숫자는 연도가 아닐 수 있다 — 문서번호 등과 헷갈리면 안 된다."""
    scope = infer_scope("문서번호 2025 찾아줘", today=TODAY)
    assert scope.years == []


def test_year_with_gukyeondo_suffix_is_recognized():
    scope = infer_scope("2025년도 예산 편성 현황", today=TODAY)
    assert scope.years == [2025]


def test_last_year_resolves_relative_to_today():
    scope = infer_scope("작년 예산 요구자료는 누가 만들었어?", today=TODAY)
    assert scope.years == [2025]


def test_alternate_spelling_of_last_year_also_resolves():
    for phrase in ("지난해 실적", "지난 해 실적", "전년도 실적", "전년 실적"):
        assert infer_scope(phrase, today=TODAY).years == [2025], phrase


def test_this_year_resolves_to_the_current_year():
    for phrase in ("올해 계획이 뭐야", "금년도 계획", "이번 해 계획"):
        assert infer_scope(phrase, today=TODAY).years == [2026], phrase


def test_next_year_resolves_to_next_year():
    scope = infer_scope("내년 예산은 어떻게 돼", today=TODAY)
    assert scope.years == [2027]


def test_no_year_mention_yields_an_empty_scope():
    scope = infer_scope("계약관리 업무는 어떤 순서로 처리해?", today=TODAY)
    assert scope.years == []
    assert not scope.has_year


def test_multiple_years_are_all_kept_and_sorted():
    scope = infer_scope("2022년과 2024년 자료를 비교해줘", today=TODAY)
    assert scope.years == [2022, 2024]


def test_duplicate_mentions_are_not_repeated():
    """'올해'와 '2026년'이 같은 해를 가리키면 한 번만 남아야 한다."""
    scope = infer_scope("올해도 2026년에도 다시 확인했다", today=TODAY)
    assert scope.years == [2026]


def test_has_year_reflects_presence():
    assert infer_scope("2025년 자료", today=TODAY).has_year
    assert not infer_scope("아무 연도 언급 없음", today=TODAY).has_year
