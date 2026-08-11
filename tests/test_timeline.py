"""주기 탐지 시험 — When의 게이트.

가장 중요한 계약: **자료가 부족하면 반복을 주장하지 않는다.** 그리고
파일 수정일로만 판정된 문서는 격자에 들어가지 않는다 — 복사 한 번에
오염되는 근거로 "매년 반복"이라 말하면 신뢰를 잃는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.timeline import (
    MONTHLY,
    QUARTERLY,
    YEARLY,
    DatedDoc,
    build_grid,
    day_hint,
    detect_cycle,
)


def _docs(year_months: list[tuple[int, int]], trustworthy: bool = True) -> list[DatedDoc]:
    return [
        DatedDoc(doc_id=i, year=y, month=m, trustworthy=trustworthy)
        for i, (y, m) in enumerate(year_months, start=1)
    ]


# ── 격자 ────────────────────────────────────────────────────────────

def test_grid_excludes_untrustworthy_documents():
    """파일 수정일로만 판정된 문서는 격자에 놓지 않는다."""
    docs = _docs([(2024, 9), (2025, 9)], trustworthy=False)
    grid = build_grid(docs)
    assert grid.years == []
    assert grid.doc_ids(2024, 9) == []


def test_grid_excludes_month_less_documents():
    """연도만 있고 월이 없는 문서는 격자에 놓을 수 없다."""
    docs = [DatedDoc(doc_id=1, year=2024, month=None)]
    grid = build_grid(docs)
    assert grid.years == []


def test_grid_places_documents_by_year_month():
    grid = build_grid(_docs([(2024, 9), (2024, 9), (2025, 10)]))
    assert grid.doc_ids(2024, 9) == [1, 2]
    assert grid.doc_ids(2025, 10) == [3]
    assert grid.years == [2024, 2025]


# ── 자료 부족 → 반복 주장 금지 ──────────────────────────────────────

def test_single_year_yields_no_cycle():
    """자료가 1개 연도뿐이면 반복을 주장하지 않는다 (제품 원칙 5)."""
    grid = build_grid(_docs([(2024, 9), (2024, 10), (2024, 11)]))
    assert detect_cycle(grid) is None


def test_no_documents_yields_no_cycle():
    assert detect_cycle(build_grid([])) is None


def test_all_untrustworthy_yields_no_cycle():
    grid = build_grid(_docs([(2023, 9), (2024, 9), (2025, 9)], trustworthy=False))
    assert detect_cycle(grid) is None


def test_scattered_months_with_no_pattern_yield_no_cycle():
    grid = build_grid(_docs([(2023, 2), (2024, 7), (2025, 11)]))
    assert detect_cycle(grid) is None


# ── 매월 반복 ───────────────────────────────────────────────────────

def test_monthly_pattern_is_detected():
    year_months = [(y, m) for y in (2024, 2025) for m in range(1, 11)]   # 10/12달
    grid = build_grid(_docs(year_months))
    guess = detect_cycle(grid)
    assert guess.kind == MONTHLY
    assert guess.months == []


def test_monthly_next_occurrence_is_not_meaningful():
    """매월 반복은 '항상 이번 달'이라 다음 예상 시점이 없다."""
    year_months = [(y, m) for y in (2024, 2025) for m in range(1, 13)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.next_occurrence(date(2026, 8, 11)) is None


# ── 특정 달 반복 (연간) ─────────────────────────────────────────────

def test_yearly_pattern_with_three_consecutive_months():
    """실측 표본: 행정사무감사가 매년 9~11월에 반복된다."""
    year_months = [(y, m) for y in (2022, 2023, 2024, 2025) for m in (9, 10, 11)]
    grid = build_grid(_docs(year_months))
    guess = detect_cycle(grid)
    assert guess.kind == YEARLY
    assert guess.months == [9, 10, 11]
    assert guess.years_observed == 4
    assert guess.confidence == "high"


def test_yearly_pattern_with_two_separated_months():
    """실측 표본: 예산관리가 매년 3월과 12월에 반복된다."""
    year_months = [(y, m) for y in (2023, 2024, 2025) for m in (3, 12)]
    grid = build_grid(_docs(year_months))
    guess = detect_cycle(grid)
    assert guess.kind == YEARLY
    assert guess.months == [3, 12]


def test_confidence_is_medium_with_only_two_years():
    year_months = [(y, 9) for y in (2024, 2025)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.years_observed == 2
    assert guess.confidence == "medium"


def test_confidence_is_high_with_three_or_more_years():
    year_months = [(y, 9) for y in (2023, 2024, 2025)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.confidence == "high"


def test_one_off_month_does_not_pollute_the_pattern():
    """한 해에만 우연히 나온 달을 공통 주기로 오인하면 안 된다."""
    year_months = [(y, 9) for y in (2022, 2023, 2024, 2025)]
    year_months.append((2024, 4))   # 2024년에만 4월 문서 하나
    grid = build_grid(_docs(year_months))
    guess = detect_cycle(grid)
    assert guess.months == [9]


def test_missing_one_year_out_of_several_is_still_recognized():
    """매년 반복이어도 한 해쯤은 자료가 없을 수 있다 — 다수결로 인정한다."""
    year_months = [(y, 9) for y in (2022, 2023, 2025)]   # 2024년 누락
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess is not None
    assert guess.months == [9]


# ── 분기 반복 ───────────────────────────────────────────────────────

def test_quarterly_pattern_is_distinguished_from_yearly():
    """실측 표본: 수질통계가 분기(1,4,7,10월)마다 반복된다."""
    year_months = [(y, m) for y in (2023, 2024, 2025) for m in (1, 4, 7, 10)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.kind == QUARTERLY
    assert guess.months == [1, 4, 7, 10]


def test_four_unevenly_spaced_months_is_yearly_not_quarterly():
    year_months = [(y, m) for y in (2023, 2024, 2025) for m in (1, 2, 3, 9)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.kind == YEARLY


# ── 다음 예상 시점 ──────────────────────────────────────────────────

def test_next_occurrence_within_the_same_year():
    year_months = [(y, m) for y in (2022, 2023, 2024, 2025) for m in (9, 10, 11)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.next_occurrence(date(2026, 8, 11)) == date(2026, 9, 1)


def test_next_occurrence_wraps_to_next_year():
    year_months = [(y, 3) for y in (2023, 2024, 2025)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.next_occurrence(date(2026, 8, 11)) == date(2027, 3, 1)


def test_next_occurrence_on_the_boundary_month():
    """이번 달이 이미 반복 달이면 올해가 아니라 내년 것을 가리켜야 한다."""
    year_months = [(y, 9) for y in (2023, 2024, 2025)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.next_occurrence(date(2026, 9, 15)) == date(2027, 9, 1)


# ── applies_to_month ────────────────────────────────────────────────

def test_applies_to_month_for_monthly_is_always_true():
    year_months = [(y, m) for y in (2024, 2025) for m in range(1, 13)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.applies_to_month(1)
    assert guess.applies_to_month(12)


def test_applies_to_month_for_yearly_checks_membership():
    year_months = [(y, m) for y in (2023, 2024, 2025) for m in (9, 10, 11)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert guess.applies_to_month(10)
    assert not guess.applies_to_month(3)


# ── 근거 문서 ───────────────────────────────────────────────────────

def test_evidence_doc_ids_only_include_matching_months():
    # 3월은 2023년에만 나와 다수결(3개 연도 중 2개 이상)을 못 채운다.
    docs = _docs([(2023, 9), (2023, 3), (2024, 9), (2025, 9)])
    guess = detect_cycle(build_grid(docs))
    assert guess.months == [9]
    # 3월 문서(2번)는 근거에 없어야 한다
    assert set(guess.evidence_doc_ids) == {1, 3, 4}


def test_evidence_is_capped():
    year_months = [(y, m) for y in range(2000, 2026) for m in (9, 10, 11)]
    guess = detect_cycle(build_grid(_docs(year_months)))
    assert len(guess.evidence_doc_ids) <= 20


# ── 일(day) 힌트 ────────────────────────────────────────────────────

def test_day_hint_from_narrow_range():
    docs = [
        DatedDoc(doc_id=1, year=2024, month=9, day=5),
        DatedDoc(doc_id=2, year=2025, month=9, day=8),
    ]
    assert day_hint(docs, months=[9]) == "5~8일"


def test_day_hint_none_when_spread_too_wide():
    docs = [
        DatedDoc(doc_id=1, year=2024, month=9, day=1),
        DatedDoc(doc_id=2, year=2025, month=9, day=28),
    ]
    assert day_hint(docs, months=[9]) is None


def test_day_hint_none_with_insufficient_samples():
    docs = [DatedDoc(doc_id=1, year=2024, month=9, day=5)]
    assert day_hint(docs, months=[9]) is None


def test_day_hint_ignores_untrustworthy_and_other_months():
    docs = [
        DatedDoc(doc_id=1, year=2024, month=9, day=5),
        DatedDoc(doc_id=2, year=2025, month=9, day=6, trustworthy=False),
        DatedDoc(doc_id=3, year=2025, month=3, day=20),
        DatedDoc(doc_id=4, year=2025, month=9, day=7),
    ]
    assert day_hint(docs, months=[9]) == "5~7일"


def test_day_hint_single_day_repeated():
    docs = [
        DatedDoc(doc_id=1, year=2024, month=12, day=1),
        DatedDoc(doc_id=2, year=2025, month=12, day=1),
    ]
    assert day_hint(docs, months=[12]) == "1일"
