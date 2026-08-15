"""인수인계 진행도 — "이제 무엇을 확인해야 하는가" (계획서 §18).

분석이 끝난 자리에서 사용자를 방치하지 않는 것이 이 제품의 신규 핵심
기능이다. 진행도는 **새로 쌓는 자료가 아니라 교정의 부산물**이다(§11):
업무를 확인하고 주기를 확정하면 그만큼 올라간다.

네 축을 고루 본다 — 업무 / 핵심문서 / 반복 업무 / 처리 순서.
문서 수로만 더하면 문서가 많은 프로젝트에서 '문서 읽기 진행도'가 되어,
업무 하나 확인하지 않고도 90%가 나온다.

수치는 `Database.handover_counts()`가 세고, 뜻은 여기서 붙인다. 화면은
raw 숫자를 해석하지 않는다(core/status.py와 같은 규칙).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import status

# (키, 화면 이름, 세는 단위). 순서가 곧 화면에 뜨는 순서다 —
# 후임자가 손대는 순서(무슨 업무인가 → 뭘 읽나 → 언제 → 어떻게)를 따른다.
AREAS: tuple[tuple[str, str, str], ...] = (
    ("tasks", "업무", "개"),
    ("reading", "핵심문서", "건"),
    ("cycles", "반복 업무", "개"),
    ("steps", "처리 순서", "개"),
)


@dataclass(slots=True)
class Area:
    key: str
    label: str
    unit: str
    done: int
    total: int

    @property
    def ratio(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def state(self) -> str:
        """4단계 상태로 말한다. 진행도만 다른 어휘를 쓰면 안 된다(§9)."""
        if not self.total:
            return status.UNKNOWN
        if self.done == self.total:
            return status.CONFIRMED
        if self.done:
            return status.INFERRED
        return status.WEAK

    def sentence(self) -> str:
        """'업무 7개 중 5개 확인' / '처리 순서 4개 확인 필요'."""
        if not self.total:
            return f"{self.label}는 아직 파악하지 못했습니다"
        if self.done == self.total:
            return f"{self.label} {self.total}{self.unit} 모두 확인"
        if self.done == 0:
            return f"{self.label} {self.total}{self.unit} 확인 필요"
        return f"{self.label} {self.total}{self.unit} 중 {self.done}{self.unit} 확인"


@dataclass(slots=True)
class Progress:
    areas: list[Area]

    @property
    def measured(self) -> list[Area]:
        """셀 것이 있는 축만. 아직 못 찾은 축은 진행도를 끌어내리지 않는다 —
        분석이 덜 끝난 상태를 '사용자가 게으른 것'처럼 보이게 하면 안 된다."""
        return [area for area in self.areas if area.total]

    @property
    def percent(self) -> int:
        measured = self.measured
        if not measured:
            return 0
        return round(100 * sum(area.ratio for area in measured) / len(measured))

    @property
    def done(self) -> bool:
        measured = self.measured
        return bool(measured) and all(area.state == status.CONFIRMED for area in measured)

    def headline(self) -> str:
        if not self.measured:
            return "아직 확인할 것이 없습니다"
        if self.done:
            return "인수인계 확인을 마쳤습니다"
        return f"인수인계 진행도 {self.percent}%"

    def next_step(self) -> Area | None:
        """가장 덜 된 축 하나. '다음에 무엇을 하나'에 답한다.

        비율이 같으면 **AREAS 순서**로 고른다(업무 → 핵심문서 → 반복 → 순서).
        후임자가 실제로 손대는 순서이고, 앞의 것을 건너뛰고 뒤의 것부터 하면
        어차피 다시 앞으로 돌아오게 된다. 이름순 같은 임의 기준으로 정하면
        "왜 이것부터?"에 답할 수 없다.
        """
        order = {key: index for index, (key, _label, _unit) in enumerate(AREAS)}
        pending = [area for area in self.measured if area.state != status.CONFIRMED]
        if not pending:
            return None
        return min(pending, key=lambda area: (area.ratio, order[area.key]))


def summarize(counts: dict[str, int]) -> Progress:
    return Progress(
        areas=[
            Area(
                key=key,
                label=label,
                unit=unit,
                done=counts.get(f"{key}_done", 0),
                total=counts.get(f"{key}_total", 0),
            )
            for key, label, unit in AREAS
        ]
    )
