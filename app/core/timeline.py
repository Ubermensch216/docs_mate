"""연도×월 격자와 반복 주기 탐지 — When의 엔진.

같은 업무의 문서를 연도×월 격자에 놓으면 세로로 읽어 반복 주기(When)가,
가로로 읽어 처리 순서(How, Step 8)가 나온다. 이 모듈은 격자를 만들고
세로축(주기)을 읽는다. 격자 자체는 Step 8에서도 재사용한다.

핵심 규칙 — 없는 것을 지어내지 않는다.
  · 자료가 min_years(기본 2개 연도) 미만이면 반복을 주장하지 않는다.
  · 파일 수정일(⑤)로만 판정된 문서는 격자에 넣지 않는다. 폴더째 복사하면
    바뀌는 값이라, 그런 근거로 "매년 반복"이라 말하면 신뢰를 잃는다
    (doc/00 §8.1).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

MONTHLY = "monthly"
QUARTERLY = "quarterly"
YEARLY = "yearly"

MIN_YEARS_FOR_CYCLE = 2
MONTHLY_COVERAGE_THRESHOLD = 0.7   # 연도당 12달 중 이 비율 이상 채워지면 '매월'
QUARTERLY_MONTH_COUNT = 4
QUARTERLY_SPACING = 3               # 4개 달이 대략 3개월 간격이면 분기
MAJORITY_RATIO = 0.6                # 관측 연도 중 이 비율 이상에 나온 달만 '공통'
DAY_HINT_MAX_SPREAD = 10            # 관측된 날짜 범위가 이보다 넓으면 힌트를 만들지 않는다
MAX_EVIDENCE = 20


@dataclass(slots=True)
class DatedDoc:
    """주기 탐지에 쓰는 최소 정보. DB 행을 그대로 넘기지 않는다."""

    doc_id: int
    year: int
    month: int | None = None
    day: int | None = None
    trustworthy: bool = True   # False면 파일 수정일로만 판정된 것


@dataclass(slots=True)
class Grid:
    years: list[int]
    cells: dict[tuple[int, int], list[int]]   # (year, month) -> doc_ids

    def doc_ids(self, year: int, month: int) -> list[int]:
        return self.cells.get((year, month), [])

    def months_in(self, year: int) -> set[int]:
        return {m for (y, m), ids in self.cells.items() if y == year and ids}


def build_grid(docs: list[DatedDoc]) -> Grid:
    """신뢰할 수 있는 문서만 격자에 놓는다."""
    trusted = [d for d in docs if d.trustworthy and d.month]
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for d in trusted:
        cells[(d.year, d.month)].append(d.doc_id)
    years = sorted({d.year for d in trusted})
    return Grid(years=years, cells=dict(cells))


@dataclass(slots=True)
class CycleGuess:
    kind: str
    months: list[int]           # yearly/quarterly에서 반복되는 달. monthly는 빈 리스트
    years_observed: int
    confidence: str              # high(3년+) | medium(2년)
    day_hint: str | None
    evidence_doc_ids: list[int]

    def applies_to_month(self, month: int) -> bool:
        return self.kind == MONTHLY or month in self.months

    def next_occurrence(self, today: date | None = None) -> date | None:
        """다음 예상 시점. 매월 반복은 '항상 이번 달'이라 의미가 없어 None."""
        if self.kind == MONTHLY or not self.months:
            return None
        today = today or date.today()
        upcoming = sorted(m for m in self.months if m > today.month)
        if upcoming:
            return date(today.year, upcoming[0], 1)
        return date(today.year + 1, min(self.months), 1)


def detect_cycle(grid: Grid, min_years: int = MIN_YEARS_FOR_CYCLE) -> CycleGuess | None:
    """격자의 세로축을 읽어 반복 주기를 찾는다."""
    months_by_year = {y: grid.months_in(y) for y in grid.years}
    years_with_docs = sorted(y for y, months in months_by_year.items() if months)
    if len(years_with_docs) < min_years:
        return None

    if _monthly_coverage(months_by_year, years_with_docs) >= MONTHLY_COVERAGE_THRESHOLD:
        return _build_guess(MONTHLY, [], months_by_year, years_with_docs, grid)

    common = _common_months(months_by_year, years_with_docs)
    if not common:
        return None

    kind = (
        QUARTERLY
        if len(common) == QUARTERLY_MONTH_COUNT and _evenly_spaced(common, QUARTERLY_SPACING)
        else YEARLY
    )
    return _build_guess(kind, sorted(common), months_by_year, years_with_docs, grid)


def _monthly_coverage(months_by_year: dict[int, set[int]], years: list[int]) -> float:
    ratios = [len(months_by_year[y]) / 12 for y in years]
    return sum(ratios) / len(ratios)


def _common_months(months_by_year: dict[int, set[int]], years: list[int]) -> set[int]:
    """관측 연도 대부분에서 나타난 달만 '공통'으로 본다.

    매년 완벽히 똑같은 달에 문서가 남는 경우는 드물다. 한 해쯤 어긋나도
    반복으로 인정하되, 한두 해에만 나온 달을 우연히 공통으로 잡지 않도록
    다수결(majority)을 쓴다.
    """
    counts: dict[int, int] = defaultdict(int)
    for y in years:
        for m in months_by_year[y]:
            counts[m] += 1
    threshold = max(2, round(len(years) * MAJORITY_RATIO))
    return {m for m, n in counts.items() if n >= threshold}


def _evenly_spaced(months: set[int], spacing: int) -> bool:
    ordered = sorted(months)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    gaps.append(12 - ordered[-1] + ordered[0])   # 12월→1월 순환 간격
    return all(abs(gap - spacing) <= 1 for gap in gaps)


def _build_guess(
    kind: str,
    months: list[int],
    months_by_year: dict[int, set[int]],
    years_with_docs: list[int],
    grid: Grid,
) -> CycleGuess:
    if kind == MONTHLY:
        matching_years = years_with_docs
        evidence = [
            doc_id
            for y in years_with_docs
            for m in sorted(months_by_year[y])
            for doc_id in grid.doc_ids(y, m)
        ]
    else:
        matching_years = [y for y in years_with_docs if months_by_year[y] & set(months)]
        evidence = [
            doc_id
            for y in matching_years
            for m in months
            for doc_id in grid.doc_ids(y, m)
        ]

    years_observed = len(matching_years)
    confidence = "high" if years_observed >= 3 else "medium"
    return CycleGuess(
        kind=kind,
        months=months,
        years_observed=years_observed,
        confidence=confidence,
        day_hint=None,
        evidence_doc_ids=evidence[:MAX_EVIDENCE],
    )


def day_hint(docs: list[DatedDoc], months: list[int] | None) -> str | None:
    """관측된 날짜의 일(day) 범위로 '5~10일' 같은 힌트를 만든다.

    범위가 너무 넓으면(자료가 뒤섞여 있으면) 힌트를 만들지 않는다 — 부정확한
    힌트보다 없는 편이 낫다.
    """
    days = [
        d.day for d in docs
        if d.trustworthy and d.day and (not months or d.month in months)
    ]
    if len(days) < 2:
        return None
    lo, hi = min(days), max(days)
    if hi - lo > DAY_HINT_MAX_SPREAD:
        return None
    return f"{lo}일" if lo == hi else f"{lo}~{hi}일"
