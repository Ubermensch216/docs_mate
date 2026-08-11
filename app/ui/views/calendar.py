"""일정 화면 — When. 전 업무 통합.

"지금 이 달에 나는 뭘 해야 하나"는 다른 어느 화면도 답하지 못한다.
발령 직후 후임자에게 가장 급한 정보다.

일정은 사용자가 입력하지 않는다. 자료에서 발견된다. 그래서 자료가 한 해치뿐이면
반복을 주장하지 않는다 — 없는 것을 지어내지 않는 원칙이 여기서 가장 중요하다.
주기 탐지는 Step 7에서 붙는다.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ...db import Database
from .. import theme
from ..widgets import EmptyState, UnknownBlock, clear_layout, muted_label, view_title


class CalendarView(QWidget):
    go_documents = Signal()

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

        cycles = self.db.con.execute("SELECT COUNT(*) AS n FROM task_cycles").fetchone()["n"]
        years = self.db.con.execute(
            "SELECT COUNT(DISTINCT eff_year) AS n FROM documents WHERE eff_year IS NOT NULL"
        ).fetchone()["n"]

        if cycles:
            # 주기 목록은 Step 7에서 채운다.
            self.column.addWidget(muted_label(f"{cycles}개 반복 업무를 찾았습니다."))
            return

        if years <= 1:
            self.column.addWidget(
                UnknownBlock(
                    "반복 여부를 판단할 자료가 부족합니다. "
                    f"현재 확인된 연도가 {years}개뿐입니다. "
                    "반복 일정은 최소 2개 연도가 관측되어야 제시합니다."
                )
            )
            self.column.addWidget(
                EmptyState(
                    "아직 일정을 만들 수 없습니다",
                    "같은 업무가 여러 해에 걸쳐 같은 시기에 반복되는 것을 확인해야 "
                    "일정으로 제시할 수 있습니다. 근거 없이 추측하지 않습니다.",
                    "문서 보기",
                    self.go_documents.emit,
                )
            )
            return

        self.column.addWidget(
            EmptyState(
                "반복 업무를 아직 찾지 못했습니다",
                f"{years}개 연도의 자료를 확인했습니다. 업무 분류가 끝나면 "
                "연도별 시점을 견주어 반복 주기를 찾습니다.",
                "문서 보기",
                self.go_documents.emit,
            )
        )
