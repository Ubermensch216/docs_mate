"""주기 표현 서식 시험.

업무 상세와 일정 화면이 같은 문장을 쓰도록 한 곳에 모은 함수들이다.
"""

from __future__ import annotations

from datetime import date

from app.ui.views.cycle_format import (
    cycle_headline,
    cycle_note,
    guess_from_row,
    next_occurrence_text,
    parse_months,
)


def _row(**overrides) -> dict:
    base = dict(kind="yearly", months="9,10,11", day_hint=None,
                years_observed=3, confidence="high")
    base.update(overrides)
    return base


def test_parse_months_handles_empty_and_populated():
    assert parse_months(None) == []
    assert parse_months("") == []
    assert parse_months("9,10,11") == [9, 10, 11]


def test_headline_for_monthly_with_day_hint():
    row = _row(kind="monthly", months="", day_hint="5~10일")
    assert cycle_headline(row) == "매월 5~10일"


def test_headline_for_monthly_without_day_hint():
    row = _row(kind="monthly", months="", day_hint=None)
    assert cycle_headline(row) == "매월"


def test_headline_groups_consecutive_months_into_a_range():
    row = _row(months="9,10,11")
    assert cycle_headline(row) == "매년 9~11월"


def test_headline_separates_non_consecutive_months():
    row = _row(months="3,12")
    assert cycle_headline(row) == "매년 3월·12월"


def test_headline_handles_single_month():
    row = _row(months="5")
    assert cycle_headline(row) == "매년 5월"


def test_headline_handles_mixed_ranges_and_singles():
    row = _row(months="1,2,3,9")
    assert cycle_headline(row) == "매년 1~3월·9월"


def test_note_reports_years_and_korean_confidence():
    row = _row(years_observed=4, confidence="high")
    assert cycle_note(row) == "4년 확인 · 신뢰도 높음"
    row = _row(years_observed=2, confidence="medium")
    assert cycle_note(row) == "2년 확인 · 신뢰도 보통"


def test_guess_from_row_roundtrips_months():
    guess = guess_from_row(_row(months="9,10,11"))
    assert guess.months == [9, 10, 11]
    assert guess.kind == "yearly"


def test_next_occurrence_text_is_none_for_monthly():
    row = _row(kind="monthly", months="")
    assert next_occurrence_text(row, date(2026, 8, 11)) is None


def test_next_occurrence_text_reports_year_month_and_dday():
    row = _row(months="9,10,11")
    text = next_occurrence_text(row, date(2026, 8, 11))
    assert text == "2026년 9월 (D-21)"


def test_next_occurrence_text_wraps_to_next_year():
    row = _row(months="3")
    text = next_occurrence_text(row, date(2026, 8, 11))
    assert text.startswith("2027년 3월")
