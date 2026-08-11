"""질문 화면 — 자유 탐색.

대화가 아니라 단발 질의로 설계한다. 멀티턴 문맥 유지·지시대명사 해석·대화
기억을 만들지 않는다. 챗봇으로 흐르지 않으면서 정형 화면이 못 덮는 질문을
처리하는 방법이다.

답변 생성은 Step 9에서 붙는다. 지금은 Ollama 연결 상태만 정직하게 알린다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from .. import theme
from ..widgets import Card, EmptyState, UnknownBlock, muted_label, section_title, view_title

EXAMPLES = [
    ("이번 달에 내가 해야 할 일이 뭐야?", "When"),
    ("작년 9월엔 무슨 일이 많았어?", "When"),
    ("예산 요구자료는 어떤 순서로 만들어?", "How"),
    ("계약 업무는 무슨 일이야?", "What"),
]


class AskView(QWidget):
    go_documents = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        outer.setSpacing(theme.SP_LG)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        outer.addWidget(view_title("자료에 대해 질문하세요"))
        outer.addWidget(
            muted_label(
                "등록한 자료만 근거로 답합니다. 자료에 없는 내용은 지어내지 않고, "
                "근거가 부족하면 답을 보류합니다."
            )
        )

        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        self.input = QLineEdit()
        self.input.setPlaceholderText("작년 행감 때 수질 관련해서 뭘 제출했어?")
        self.input.returnPressed.connect(self._ask)
        row.addWidget(self.input, 1)
        self.send = QPushButton("질문")
        self.send.setObjectName("Primary")
        self.send.clicked.connect(self._ask)
        row.addWidget(self.send)
        outer.addLayout(row)

        examples = Card()
        examples.setMaximumWidth(theme.CONTENT_MAX_W)
        examples.body.addWidget(section_title("이렇게 물어보세요"))
        for text, kind in EXAMPLES:
            chip = QPushButton(f"{text}   ({kind})")
            chip.setObjectName("Link")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, t=text: self.input.setText(t))
            examples.body.addWidget(chip, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(examples)

        self.notice = UnknownBlock("")
        outer.addWidget(self.notice)

        self.result = EmptyState(
            "아직 답할 준비가 되지 않았습니다",
            "문서 내용을 읽고 업무를 파악한 뒤에 질문에 답할 수 있습니다. "
            "그때까지는 [문서]에서 직접 찾아볼 수 있습니다.",
            "문서 보기",
            self.go_documents.emit,
        )
        outer.addWidget(self.result)

        self.refresh()

    def refresh(self) -> None:
        ready, message = self._readiness()
        self.input.setEnabled(ready)
        self.send.setEnabled(ready)
        self.notice.setVisible(not ready)
        self.notice.setMessage(message)

    def _readiness(self) -> tuple[bool, str]:
        counts = self.db.counts()
        if counts["total"] == 0:
            return False, "등록된 자료가 없습니다. 먼저 자료원을 추가하세요."
        if counts["analyzed"] == 0:
            return False, (
                "문서 내용 분석이 끝나지 않아 아직 질문에 답할 수 없습니다. "
                "근거 없이 답하지 않기 위해 기다립니다."
            )
        return True, ""

    def _ask(self) -> None:
        question = self.input.text().strip()
        if not question:
            return
        # 답변 생성은 Step 9. 근거 없이 답하지 않는다는 정책만 먼저 지킨다.
        self.db.audit("question.ask", detail=f"{len(question)}자")
