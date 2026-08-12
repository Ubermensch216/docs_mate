"""연도×월 격자 위젯 — When과 How가 공유하는 근거를 그린다.

세로로 읽으면 반복 주기(When), 가로로 읽으면 처리 순서(How, Step 8)다.

점을 열두 개 찍는 대신 연속한 달을 하나의 막대로 잇는다. "2024년엔 9월부터
11월까지 문서가 있었다"는 사실이 점 세 개가 아니라 하나의 기간으로 보여야
사용자가 반복을 눈으로 확인할 수 있다. 일정 화면의 연간 패턴 격자와 같은
모양을 쓰므로, 두 화면이 같은 것을 말하고 있다는 것도 함께 읽힌다.

신뢰도는 색이 아니라 기호로 구분한다 — 색만으로 상태를 나타내지 않는다는
원칙(PRD §18.4)을 격자에도 적용한다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme

MONTH_LABELS = [f"{m}" for m in range(1, 13)]

FILLED_HIGH = "●"     # 이 연도에 해당 달 문서가 있고 전체 주기 신뢰도가 높음
FILLED_LOW = "○"      # 문서는 있으나 주기 신뢰도가 낮음(관측 2년 등)
EMPTY = "·"
CURRENT = "◎"          # 진행 중인 이번 달

YEAR_W = 64
CELL_W = 36
ROW_H = 30
BAR_H = 20


class TimelineGrid(QWidget):
    """연도를 행으로, 1~12월을 열로 그리는 격자.

    cells: {(year, month): True}면 그 칸에 문서가 있다는 뜻.
    current: (year, month) — 진행 중 표시를 줄 셀.
    """

    def __init__(
        self,
        years: list[int],
        cells: dict[tuple[int, int], bool],
        confidence: str = "medium",
        current: tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("GridPanel")
        panel.setFixedWidth(YEAR_W + CELL_W * 12 + theme.SP_MD * 2)
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        current_month = current[1] if current else None
        stack.addWidget(_header(current_month))

        strong = confidence == "high"
        for index, year in enumerate(years):
            filled = [cells.get((year, m), False) for m in range(1, 13)]
            stack.addWidget(
                _row(year, filled, index, index == len(years) - 1,
                     strong, current if current and current[0] == year else None)
            )

        outer.addWidget(panel, alignment=Qt.AlignmentFlag.AlignLeft)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)


def _header(current_month: int | None) -> QFrame:
    head = QFrame()
    head.setObjectName("GridHeadRow")
    head.setFixedHeight(ROW_H)
    row = QHBoxLayout(head)
    row.setContentsMargins(theme.SP_MD, 0, theme.SP_MD, 0)
    row.setSpacing(0)

    corner = QLabel("연도")
    corner.setObjectName("GridHeadCell")
    corner.setFixedWidth(YEAR_W)
    row.addWidget(corner)

    for index, label in enumerate(MONTH_LABELS, start=1):
        now = index == current_month
        cell = QLabel(label)
        cell.setObjectName("GridHeadCellNow" if now else "GridHeadCell")
        cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cell.setFixedWidth(CELL_W)
        if now:
            cell.setFixedHeight(ROW_H - theme.SP_SM)
            row.addWidget(cell, 0, Qt.AlignmentFlag.AlignBottom)
            continue
        row.addWidget(cell)
    return head


def _row(
    year: int,
    filled: list[bool],
    index: int,
    last: bool,
    strong: bool,
    current: tuple[int, int] | None,
) -> QFrame:
    line = QFrame()
    line.setObjectName(
        ("GridRow" if index % 2 == 0 else "GridRowAlt") + ("Last" if last else "")
    )
    line.setFixedHeight(ROW_H)
    row = QHBoxLayout(line)
    row.setContentsMargins(theme.SP_MD, 0, theme.SP_MD, 0)
    row.setSpacing(0)

    label = QLabel(f"{year}년")
    label.setObjectName("GridName")
    label.setFixedWidth(YEAR_W)
    row.addWidget(label)

    for month in range(1, 13):
        row.addWidget(_slot(filled, month, strong, current))
    return line


def _slot(
    filled: list[bool], month: int, strong: bool, current: tuple[int, int] | None
) -> QWidget:
    """칸은 줄 높이를 다 쓰고 그 안의 막대만 낮다 — 이번 달 세로 띠가
    끊기지 않으면서 가로 막대는 행마다 떨어져 보인다."""
    is_current = current is not None and current[1] == month
    slot = QWidget()
    slot.setFixedWidth(CELL_W)
    slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    if is_current:
        slot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        slot.setStyleSheet(f"background: {theme.PRIMARY_SOFT};")

    box = QVBoxLayout(slot)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(
        _bar(filled, month, strong, is_current), 0, Qt.AlignmentFlag.AlignVCenter
    )
    return slot


def _bar(filled: list[bool], month: int, strong: bool, is_current: bool) -> QLabel:
    bar = QLabel()
    bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    bar.setFixedSize(CELL_W, BAR_H)

    if not filled[month - 1]:
        bar.setText(CURRENT if is_current else EMPTY)
        color = theme.PRIMARY if is_current else theme.TEXT_DISABLED
        bar.setStyleSheet(f"color: {color}; background: transparent;")
        return bar

    starts = month == 1 or not filled[month - 2]
    ends = month == 12 or not filled[month]
    radius = BAR_H // 2
    left = radius if starts else 0
    right = radius if ends else 0
    fill = theme.CYCLE_STRONG if strong else theme.CYCLE_SOFT
    ink = theme.TEXT_ON_PRIMARY if strong else theme.TEXT

    # 구간의 첫 칸에만 기호를 찍는다. 칸마다 반복하면 막대가 글자밭이 된다.
    if is_current:
        mark = CURRENT
    elif starts:
        mark = FILLED_HIGH if strong else FILLED_LOW
    else:
        mark = ""
    bar.setText(mark)
    bar.setStyleSheet(
        f"background: {fill}; color: {ink}; font-weight: 700;"
        f"border-top-left-radius: {left}px; border-bottom-left-radius: {left}px;"
        f"border-top-right-radius: {right}px; border-bottom-right-radius: {right}px;"
    )
    return bar


def legend_text() -> str:
    return (
        f"{FILLED_HIGH} 문서가 있는 달   {FILLED_LOW} 문서가 있으나 자료가 적음   "
        f"{CURRENT} 이번 달   {EMPTY} 문서 없음"
    )
