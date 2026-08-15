"""주기 정보를 사람이 읽는 문장으로, 그리고 '다음에 언제 오는가'로 바꾼다.

업무 상세(tasks.py)·일정 화면(calendar.py)·업무 홈의 '지금 먼저 확인할 것'이
같은 표현과 **같은 판정**을 써야 한다. 일정 화면에서는 '지금 챙길 일'인데
업무 홈에서는 안 보이면, 사용자는 둘 중 어느 화면을 믿어야 할지 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ...core.timeline import CycleGuess

CONFIDENCE_LABEL = {"high": "높음", "medium": "보통", "low": "낮음"}

# 이 안에 들어오면 '지금 챙길 일'로 올린다. 공직 업무는 한 달 전부터
# 준비 문서가 돌기 시작한다 — D-30을 넘겨 알려 주면 이미 늦다.
SOON_DAYS = 45


@dataclass(slots=True)
class Upcoming:
    """다가오는 반복 하나. 화면이 쓰기 좋은 모양으로 미리 계산해 둔다."""

    row: object
    when: date
    days_away: int
    running_now: bool

    @property
    def task_id(self) -> int:
        return self.row["task_id"]

    @property
    def name(self) -> str:
        return self.row["task_name"]


def upcoming(cycles: list, today: date) -> list[Upcoming]:
    """모든 주기를 '다음에 언제 오는가' 순으로 편다.

    매월 반복은 다음 시점이 따로 없다 — 지금이 곧 그때다. 그래서 항상
    '진행 중'으로 맨 앞에 둔다.
    """
    entries: list[Upcoming] = []
    for row in cycles:
        guess = guess_from_row(row)
        if guess.kind == "monthly":
            entries.append(Upcoming(row=row, when=today, days_away=0, running_now=True))
            continue

        when = guess.next_occurrence(today)
        if when is None:
            continue
        running = guess.applies_to_month(today.month)
        days = 0 if running else (when - today).days
        entries.append(
            Upcoming(row=row, when=today if running else when,
                     days_away=days, running_now=running)
        )

    entries.sort(key=lambda e: (not e.running_now, e.days_away, e.name))
    return entries


def is_now(entry: Upcoming) -> bool:
    """'지금 챙길 일'인가. 이 판정을 쓰는 화면이 셋이라 여기 하나만 둔다."""
    return entry.running_now or entry.days_away <= SOON_DAYS


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
