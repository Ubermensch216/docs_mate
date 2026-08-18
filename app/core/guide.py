"""첫날 / 첫 주 가이드 — "이제 무엇을 확인해야 하는가" (계획서 §18).

진행도(handover.py)가 **얼마나 왔나**를 말한다면, 가이드는 **오늘 무엇까지
하면 되나**를 말한다. 둘은 같은 숫자를 쓰지만 다른 질문이다. 진행도만 있으면
사용자는 72%를 보고도 지금 손댈 것을 스스로 골라야 한다.

단계를 나누는 이유
    할 일 여섯 가지를 한 번에 늘어놓으면 발령 첫날의 사람에게는 그것이
    곧 "오늘 안에 여섯 가지를 해야 한다"로 읽힌다. 실제로 첫날 할 수 있는
    일은 셋뿐이다 — 무슨 업무인지 보고, 먼저 읽을 것을 읽고, 이번 달에
    걸린 것을 챙긴다. 과거 처리 순서와 반복 주기는 자료를 며칠 들여다본
    뒤에야 판단이 선다. 그래서 첫날 것이 끝나야 첫 주 것을 보여 준다.

단계는 **날짜가 아니라 진척으로** 넘어간다. 설치 후 8일째라고 첫 주로
밀어 버리면, 아직 업무 목록도 못 본 사람에게 처리 순서를 확인하라고 하게
된다. 달력은 사람의 사정을 모른다.

항목의 수치는 여기서 세지 않는다(handover_counts·stray_counts). 이 모듈은
"어느 단계인가"와 "다음에 무엇인가"만 판정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import status
from .handover import Area

DAY, WEEK, DONE = "day", "week", "done"

STAGE_LABEL = {DAY: "첫날", WEEK: "첫 주", DONE: "완료"}
STAGE_INTRO = {
    DAY: "오늘은 여기까지만 하면 됩니다",
    WEEK: "첫날 것은 끝났습니다. 이번 주에 볼 것입니다",
    DONE: "인수인계 확인을 마쳤습니다",
}

# (키, 화면 이름, 세는 단위, 단계, 할 일 한 줄)
#
# 순서가 곧 화면 순서다. 단계 안에서도 후임자가 실제로 손대는 차례를 따른다 —
# 무슨 업무인가 → 무엇부터 읽나 → 지금 걸린 것은 무엇인가.
ITEMS: tuple[tuple[str, str, str, str, str], ...] = (
    ("tasks", "업무", "개", DAY, "이름이 맞는지 보고 확인 표시를 합니다"),
    ("reading", "핵심문서", "건", DAY, "먼저 읽을 문서부터 열어 봅니다"),
    ("month", "이번 달 업무", "개", DAY, "이번 달에 걸린 업무부터 확인합니다"),
    ("cycles", "반복 주기", "개", WEEK, "언제 하는 일인지 확정합니다"),
    ("steps", "처리 순서", "개", WEEK, "과거 자료로 재구성한 순서를 확인합니다"),
    ("strays", "미분류 최근 문서", "건", WEEK,
     "업무에 배정하거나 '업무 없음'으로 표시합니다"),
)

_STAGES = (DAY, WEEK)


@dataclass(slots=True)
class Item(Area):
    """진행도의 축 하나에 '어느 단계인가'와 '무엇을 하면 되나'를 붙인 것."""

    stage: str = DAY
    todo: str = ""


@dataclass(slots=True)
class Guide:
    items: list[Item]

    def of_stage(self, stage: str) -> list[Item]:
        """그 단계에서 **셀 것이 있는** 항목만. 아직 못 찾은 축은 화면에
        올리지 않는다 — 0개짜리 할 일은 할 일이 아니다."""
        return [item for item in self.items if item.stage == stage and item.total]

    def stage_done(self, stage: str) -> bool:
        """셀 것이 하나도 없는 단계는 '끝난 것'으로 본다. 분석이 아직 주기를
        못 찾았다고 해서 사용자를 첫날에 묶어 둘 이유는 없다."""
        return all(item.state == status.CONFIRMED for item in self.of_stage(stage))

    @property
    def stage(self) -> str:
        for stage in _STAGES:
            if not self.stage_done(stage):
                return stage
        return DONE

    @property
    def measured(self) -> list[Item]:
        return [item for item in self.items if item.total]

    def stage_label(self) -> str:
        return STAGE_LABEL[self.stage]

    def headline(self) -> str:
        if not self.measured:
            return "아직 확인할 것이 없습니다"
        if self.stage == DONE:
            return STAGE_INTRO[DONE]
        current = self.of_stage(self.stage)
        left = sum(1 for item in current if item.state != status.CONFIRMED)
        return f"{STAGE_LABEL[self.stage]} — {len(current)}가지 중 {left}가지 남음"

    def next_item(self) -> Item | None:
        """지금 단계에서 가장 덜 된 것 하나. 비율이 같으면 ITEMS 순서로 고른다.

        단계를 건너뛰어 고르지 않는다. 첫날 것이 남았는데 처리 순서를
        권하면, 단계를 나눈 의미가 그 자리에서 사라진다.
        """
        if self.stage == DONE:
            return None
        order = {key: index for index, (key, *_rest) in enumerate(ITEMS)}
        pending = [
            item for item in self.of_stage(self.stage)
            if item.state != status.CONFIRMED
        ]
        if not pending:
            return None
        return min(pending, key=lambda item: (item.ratio, order[item.key]))


def summarize(counts: dict[str, int]) -> Guide:
    """`handover_counts()`와 `stray_counts()`를 합친 dict 하나를 받는다."""
    return Guide(
        items=[
            Item(
                key=key,
                label=label,
                unit=unit,
                done=counts.get(f"{key}_done", 0),
                total=counts.get(f"{key}_total", 0),
                stage=stage,
                todo=todo,
            )
            for key, label, unit, stage, todo in ITEMS
        ]
    )
