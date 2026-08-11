"""주기 정보를 사람이 읽는 문장으로 바꾼다.

업무 상세(tasks.py)와 일정 화면(calendar.py)이 같은 표현을 써야 사용자가
두 화면을 오가며 헷갈리지 않는다.
"""

from __future__ import annotations

from datetime import date

from ...core.timeline import CycleGuess

CONFIDENCE_LABEL = {"high": "높음", "medium": "보통", "low": "낮음"}


def parse_months(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(m) for m in raw.split(",") if m.strip()]


def guess_from_row(row) -> CycleGuess:
    """DB 행을 다시 CycleGuess로 만든다. next_occurrence 계산에 쓴다."""
    return CycleGuess(
        kind=row["kind"],
        months=parse_months(row["months"]),
        years_observed=row["years_observed"],
        confidence=row["confidence"],
        day_hint=row["day_hint"],
        evidence_doc_ids=[],
    )


def cycle_headline(row) -> str:
    """'매년 9~11월', '매월 5~10일' 같은 한 줄 요약."""
    if row["kind"] == "monthly":
        text = "매월"
        if row["day_hint"]:
            text += f" {row['day_hint']}"
        return text

    months = parse_months(row["months"])
    text = f"매년 {_format_months(months)}"
    if row["day_hint"]:
        text += f" {row['day_hint']}"
    return text


def cycle_note(row) -> str:
    conf = CONFIDENCE_LABEL.get(row["confidence"], row["confidence"])
    return f"{row['years_observed']}년 확인 · 신뢰도 {conf}"


def next_occurrence_text(row, today: date | None = None) -> str | None:
    """'2026년 9월 (D-24)'. 매월 반복은 '항상 이번 달'이라 해당 없음."""
    guess = guess_from_row(row)
    today = today or date.today()
    next_date = guess.next_occurrence(today)
    if next_date is None:
        return None
    days = (date(next_date.year, next_date.month, 1) - today).days
    return f"{next_date.year}년 {next_date.month}월 (D-{days})"


def _format_months(months: list[int]) -> str:
    """연속된 달은 '9~11월'로, 떨어진 달은 '3월·12월'로 묶는다."""
    if not months:
        return ""
    months = sorted(months)
    ranges: list[tuple[int, int]] = []
    start = prev = months[0]
    for m in months[1:]:
        if m == prev + 1:
            prev = m
            continue
        ranges.append((start, prev))
        start = prev = m
    ranges.append((start, prev))
    parts = [f"{a}월" if a == b else f"{a}~{b}월" for a, b in ranges]
    return "·".join(parts)
