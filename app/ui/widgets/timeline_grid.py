"""연도×월 격자 위젯 — When과 How가 공유하는 근거를 그린다.

세로로 읽으면 반복 주기(When), 가로로 읽으면 처리 순서(How, Step 8)다.
그래서 이 위젯을 업무 상세와 일정 화면 양쪽에서 재사용한다.

신뢰도는 색이 아니라 기호로 구분한다 — 색만으로 상태를 나타내지 않는다는
원칙(PRD §18.4)을 격자에도 적용한다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QWidget

from .. import theme

MONTH_LABELS = [f"{m}" for m in range(1, 13)]

FILLED_HIGH = "●"     # 이 연도에 해당 달 문서가 있고 전체 주기 신뢰도가 높음
FILLED_LOW = "○"      # 문서는 있으나 주기 신뢰도가 낮음(관측 2년 등)
EMPTY = "·"
CURRENT = "◎"          # 진행 중인 이번 달


class TimelineGrid(QWidget):
    """연도를 행으로, 1~12월을 열로 그리는 격자.

    cells: {(year, month): True}면 그 칸에 문서가 있다는 뜻.
    current: (year, month) — 진행 중 표시(◎)를 줄 셀.
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
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SP_XS)

        for col, label in enumerate(MONTH_LABELS, start=1):
            head = QLabel(label)
            head.setObjectName("Small")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(head, 0, col)

        mark = FILLED_HIGH if confidence == "high" else FILLED_LOW

        for row, year in enumerate(years, start=1):
            year_label = QLabel(str(year))
            year_label.setObjectName("Small")
            layout.addWidget(year_label, row, 0)

            for month in range(1, 13):
                filled = cells.get((year, month), False)
                is_current = current == (year, month)
                symbol = CURRENT if is_current else (mark if filled else EMPTY)
                cell = QLabel(symbol)
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                cell.setFixedWidth(18)
                cell.setObjectName("Muted" if filled or is_current else "Small")
                if is_current:
                    cell.setStyleSheet(f"color: {theme.PRIMARY}; font-weight: 600;")
                layout.addWidget(cell, row, month)


def legend_text() -> str:
    return f"{FILLED_HIGH} 확인됨   {FILLED_LOW} 확인됨(자료 적음)   {CURRENT} 진행 중"
