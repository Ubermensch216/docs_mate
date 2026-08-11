"""업무 화면 — What + How. 홈을 겸한다.

첫 화면이 곧 정체성의 답이다. `파일 12,842건`이 아니라
`당신이 인수받은 업무는 7개입니다`가 여기에 온다.

업무 발견(Step 6)과 처리 순서(Step 8)가 붙기 전까지는 진행 상황과
빈 상태만 정직하게 보여준다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ...db import Database
from .. import theme
from ..widgets import Card, EmptyState, clear_layout, muted_label, view_title

STAGES = [
    ("파일 찾기", "total", None),
    ("내용 읽기", "parsed", "documents"),
    ("업무 파악하기", "analyzed", "documents"),
]


class TasksView(QWidget):
    go_documents = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("Content")
        self.body = QWidget()
        self.body.setObjectName("Content")
        self.body.setAutoFillBackground(True)
        self.column = QVBoxLayout(self.body)
        self.column.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        self.column.setSpacing(theme.SP_LG)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.column)
        counts = self.db.counts()
        total = counts["total"]
        tasks = counts["tasks"]

        if tasks:
            self.column.addWidget(view_title(f"당신이 인수받은 업무는 {tasks}개로 추정됩니다"))
            self.column.addWidget(
                muted_label(f"전임자 자료 {total:,}건을 분석했습니다.")
            )
            # 업무 카드는 Step 6에서 채운다.
            return

        if total == 0:
            self.column.addWidget(view_title("업무"))
            self.column.addWidget(
                EmptyState(
                    "아직 살펴본 자료가 없습니다",
                    "자료원을 등록하면 파일을 먼저 찾고, 내용을 읽은 뒤 업무를 파악합니다.",
                )
            )
            return

        # 분석 중 — 전체 스피너 대신 단계별 진행을 보여준다.
        self.column.addWidget(view_title("자료를 살펴보고 있습니다"))
        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)
        for label, done_key, total_key in STAGES:
            done = counts.get(done_key, 0)
            limit = counts.get(total_key, 0) if total_key else done
            card.body.addWidget(muted_label(_stage_line(label, done, limit, total_key)))
        self.column.addWidget(card)

        self.column.addWidget(
            EmptyState(
                "업무는 아직 파악하지 못했습니다",
                "내용을 다 읽어야 업무를 나눌 수 있습니다. "
                "그동안 먼저 찾은 파일부터 [문서]에서 볼 수 있습니다.",
                "문서 보기",
                self.go_documents.emit,
            )
        )


def _stage_line(label: str, done: int, limit: int, total_key: str | None) -> str:
    if total_key is None:
        return f"✓ {label}   {done:,}건 완료"
    if limit == 0:
        return f"○ {label}   대기 중"
    if done >= limit:
        return f"✓ {label}   {done:,}건 완료"
    if done == 0:
        return f"○ {label}   대기 중"
    return f"⣾ {label}   {done:,} / {limit:,}"
