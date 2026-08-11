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


# ── 처리 순서 (How) — 같은 격자를 가로로 읽는다 ─────────────────────

GAP_THRESHOLD_DAYS = 10   # 이 이상 비면 '확인되지 않는 구간'으로 표시한다


@dataclass(slots=True)
class StepCandidate:
    doc_id: int
    month: int
    day: int | None
    label: str


@dataclass(slots=True)
class Step:
    ordinal: int
    doc_id: int
    label: str
    month: int
    day_hint: str
    gap_note: str | None = None


def day_bucket(day: int | None) -> str:
    """일(day)을 초·중·말로 뭉친다. 정밀한 날짜가 없으면 빈 문자열."""
    if day is None:
        return ""
    if day <= 10:
        return "초"
    if day <= 20:
        return "중"
    return "말"


def reconstruct_steps(
    candidates: list[StepCandidate],
    year: int,
    gap_threshold_days: int = GAP_THRESHOLD_DAYS,
) -> list[Step]:
    """문서를 시간순으로 늘어놓아 처리 순서를 재현한다.

    이것은 인과관계 추론이 아니라 **시간 근접 + 문서 유형 순서**에 기반한
    재구성이다. 모든 단계는 문서에 앵커링된다 — 문서 없는 단계는 만들지
    않는다. 두 단계 사이에 근거 없는 공백이 있으면 지어내지 않고 그렇다고
    밝힌다.

    day가 없는(월 단위로만 알려진) 문서 사이의 공백은 재지 않는다 — 정확한
    날짜를 모르는데 "2주 비었다"고 말하면 없는 것을 지어내는 셈이다.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (c.month, c.day if c.day is not None else 1, c.doc_id),
    )

    steps: list[Step] = []
    prev_date: date | None = None
    for index, candidate in enumerate(ordered, start=1):
        bucket = day_bucket(candidate.day)
        day_hint_text = f"{candidate.month}월 {bucket}" if bucket else f"{candidate.month}월"

        gap_note = None
        if candidate.day is not None and prev_date is not None:
            try:
                current_date = date(year, candidate.month, candidate.day)
            except ValueError:
                current_date = None
            if current_date is not None:
                delta_days = (current_date - prev_date).days
                if delta_days >= gap_threshold_days:
                    weeks = max(1, round(delta_days / 7))
                    gap_note = (
                        f"{index - 1}과 {index} 사이 약 {weeks}주는 "
                        "관련 자료가 없어 확인되지 않습니다"
                    )
                prev_date = current_date
        elif candidate.day is not None:
            prev_date = date(year, candidate.month, candidate.day)

        steps.append(
            Step(
                ordinal=index,
                doc_id=candidate.doc_id,
                label=candidate.label,
                month=candidate.month,
                day_hint=day_hint_text,
                gap_note=gap_note,
            )
        )
    return steps


def default_how_year(years: list[int], today: date | None = None) -> int | None:
    """처리 순서를 보여줄 기본 연도. 완결된 최근 연도를 우선한다.

    올해는 아직 진행 중이라 처리 순서가 끝까지 안 보일 수 있다. 완결된
    연도가 있으면 그걸 먼저 보여주고, 없으면(자료가 올해뿐이면) 올해라도
    보여준다 — 아무것도 안 보여주는 것보다는 낫다.
    """
    if not years:
        return None
    today = today or date.today()
    past = [y for y in years if y < today.year]
    return max(past) if past else max(years)


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
