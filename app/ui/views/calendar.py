"""일정 화면 — When. 전 업무 통합.

업무 상세가 "업무 하나의 시간"을 보여준다면, 이 화면은 "지금 이 달에
나는 뭘 해야 하나"라는, 다른 어느 화면도 답하지 못하는 질문에 답한다.
발령 직후 후임자에게 가장 급한 정보다.

일정은 사용자가 입력하지 않는다. 자료에서 발견된다. 그래서 자료가 한 해치
뿐이면 반복을 주장하지 않는다 — 없는 것을 지어내지 않는 원칙이 여기서
가장 중요하다.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from .. import theme
from ..widgets import (
    Card,
    EmptyState,
    UnknownBlock,
    clear_layout,
    divider,
    muted_label,
    section_title,
    view_title,
)
from ..widgets.timeline_grid import CURRENT, EMPTY, FILLED_HIGH, FILLED_LOW, MONTH_LABELS
from .cycle_format import cycle_headline, cycle_note, guess_from_row, next_occurrence_text


class CalendarView(QWidget):
    go_documents = Signal()
    open_task = Signal(int)

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("Content")
        body = QWidget()
        body.setObjectName("Content")
        body.setAutoFillBackground(True)
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        self.column.setSpacing(theme.SP_LG)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.column)
        today = date.today()
        self.column.addWidget(view_title(f"연간 업무 일정 · {today.year}년"))

        cycles = self.db.all_cycles()
        if not cycles:
            self._render_empty(today)
            return

        self._render_this_month(cycles, today)
        self._render_upcoming(cycles, today)
        self._render_full_year(cycles, today)

    # ── 자료 부족 ───────────────────────────────────────────────────
    def _render_empty(self, today: date) -> None:
        years = self.db.con.execute(
            "SELECT COUNT(DISTINCT eff_year) AS n FROM documents WHERE eff_year IS NOT NULL"
        ).fetchone()["n"]
        tasks = self.db.counts()["tasks"]

        if tasks == 0:
            self.column.addWidget(
                EmptyState(
                    "아직 업무를 파악하지 못했습니다",
                    "업무를 먼저 나눠야 그 업무의 반복 시기를 찾을 수 있습니다.",
                    "문서 보기",
                    self.go_documents.emit,
                )
            )
            return

        if years <= 1:
            self.column.addWidget(
                UnknownBlock(
                    f"반복 여부를 판단할 자료가 부족합니다. 현재 확인된 연도가 {years}개뿐입니다. "
                    "반복 일정은 최소 2개 연도가 관측되어야 제시합니다."
                )
            )
        self.column.addWidget(
            EmptyState(
                "반복 업무를 아직 찾지 못했습니다",
                f"업무 {tasks}개를 확인했지만, 뚜렷하게 반복되는 시기를 찾지 못했습니다. "
                "근거 없이 추측하지 않습니다.",
                "업무 보기",
                self.go_documents.emit,
            )
        )

    # ── 이번 달 ──────────────────────────────────────────────────────
    def _render_this_month(self, cycles: list, today: date) -> None:
        current = [row for row in cycles if guess_from_row(row).applies_to_month(today.month)]

        self.column.addWidget(section_title(f"이번 달 · {today.month}월"))
        if not current:
            self.column.addWidget(muted_label("이번 달에 반복되는 업무가 없습니다."))
            return

        for row in current:
            card = Card()
            card.setMaximumWidth(theme.CONTENT_MAX_W)
            head = QHBoxLayout()
            head.setSpacing(theme.SP_SM)
            head.addWidget(muted_label(f"🔁 {row['task_name']}"))
            head.addWidget(muted_label(cycle_headline(row), small=True))
            head.addStretch(1)
            head.addWidget(muted_label(cycle_note(row), small=True))
            card.body.addLayout(head)

            open_btn = QPushButton("이 업무 보기 →")
            open_btn.setObjectName("Link")
            open_btn.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
            card.body.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            self.column.addWidget(card)

    # ── 다가오는 일정 ────────────────────────────────────────────────
    def _render_upcoming(self, cycles: list, today: date) -> None:
        entries = []
        for row in cycles:
            guess = guess_from_row(row)
            next_date = guess.next_occurrence(today)
            if next_date is None:   # 매월 반복은 '다가오는 일정'에 넣지 않는다
                continue
            entries.append((next_date, row))
        entries.sort(key=lambda item: item[0])

        self.column.addWidget(_gap())
        self.column.addWidget(section_title("다가오는 일정"))
        if not entries:
            self.column.addWidget(muted_label("예정된 반복 일정이 없습니다."))
            return

        for next_date, row in entries[:6]:
            line = QHBoxLayout()
            line.setSpacing(theme.SP_SM)
            line.addWidget(muted_label(f"{next_date.month}월", wrap=False))
            name = QPushButton(row["task_name"])
            name.setObjectName("Link")
            name.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
            line.addWidget(name)
            line.addStretch(1)
            text = next_occurrence_text(row, today) or ""
            days = text.split("(")[-1].rstrip(")") if "(" in text else ""
            line.addWidget(muted_label(days, small=True, wrap=False))
            self.column.addLayout(line)

    # ── 연간 전체 ────────────────────────────────────────────────────
    def _render_full_year(self, cycles: list, today: date) -> None:
        self.column.addWidget(_gap())
        self.column.addWidget(section_title("연간 전체"))

        grid = QGridLayout()
        grid.setSpacing(theme.SP_XS)
        for col, label in enumerate(MONTH_LABELS, start=1):
            head = QLabel(label)
            head.setObjectName("Small")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(head, 0, col)

        low_confidence_present = False
        for r, row in enumerate(cycles, start=1):
            name = QLabel(row["task_name"])
            name.setObjectName("Muted")
            grid.addWidget(name, r, 0)

            guess = guess_from_row(row)
            symbol = FILLED_HIGH if row["confidence"] == "high" else FILLED_LOW
            if row["confidence"] != "high":
                low_confidence_present = True

            for month in range(1, 13):
                filled = guess.applies_to_month(month)
                is_current = filled and month == today.month
                cell = QLabel(CURRENT if is_current else (symbol if filled else EMPTY))
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                cell.setFixedWidth(18)
                cell.setObjectName("Muted" if filled else "Small")
                if is_current:
                    cell.setStyleSheet(f"color: {theme.PRIMARY}; font-weight: 600;")
                grid.addWidget(cell, r, month)

        wrapper = QWidget()
        wrapper.setLayout(grid)
        self.column.addWidget(wrapper)

        if low_confidence_present:
            self.column.addWidget(
                muted_label(f"{FILLED_LOW} 2개 연도만 확인됨(추정) — 자료가 더 쌓이면 신뢰도가 오릅니다", small=True)
            )


def _gap() -> QWidget:
    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.addWidget(divider())
    return holder
