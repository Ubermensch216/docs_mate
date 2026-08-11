"""질문 화면 — 자유 탐색.

대화가 아니라 단발 질의로 설계한다. 멀티턴 문맥 유지·지시대명사 해석·대화
기억을 만들지 않는다. 챗봇으로 흐르지 않으면서 정형 화면이 못 덮는 질문을
처리하는 방법이다(doc/00 §6.4).

답변 정책 (구현 강제)
  · 검색된 프로젝트 자료만 근거로 답한다. 일반 지식으로 빈칸을 채우지 않는다.
  · 사실 문장마다 출처를 남긴다. 출처 없는 답은 화면에 올리지 않는다.
  · 근거가 부족하면 답하지 않는다 — 대신 찾은 문서 목록을 보여준다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from ...jobs import AskRunner
from ...search.rag import Answer
from .. import theme
from ..widgets import (
    Card,
    EmptyState,
    EvidenceChip,
    UnknownBlock,
    clear_layout,
    muted_label,
    section_title,
    view_title,
)

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
        self._runner = AskRunner(db.path, self)
        self._runner.done.connect(self._on_answer)
        self._current_question_id: int | None = None

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

        self.result_area = QWidget()
        self.result_layout = QVBoxLayout(self.result_area)
        self.result_layout.setContentsMargins(0, 0, 0, 0)
        self.result_layout.setSpacing(theme.SP_MD)
        outer.addWidget(self.result_area, 1)

        self.refresh()

    # ── 준비 상태 ───────────────────────────────────────────────────
    def refresh(self) -> None:
        ready, message = self._readiness()
        self.input.setEnabled(ready and not self._runner.running)
        self.send.setEnabled(ready and not self._runner.running)
        self.notice.setVisible(not ready)
        self.notice.setMessage(message)

        if not self._runner.running:
            self._render_idle_or_empty(ready)

    def _readiness(self) -> tuple[bool, str]:
        counts = self.db.counts()
        if counts["total"] == 0:
            return False, "등록된 자료가 없습니다. 먼저 자료원을 추가하세요."
        if counts["chunks_embedded"] == 0:
            return False, (
                "질문에 답할 준비가 끝나지 않았습니다. 근거 없이 답하지 않기 위해 "
                "자료를 다 읽고 의미 색인을 만들 때까지 기다립니다."
            )
        return True, ""

    def _render_idle_or_empty(self, ready: bool) -> None:
        clear_layout(self.result_layout)
        if not ready:
            self.result_layout.addWidget(
                EmptyState(
                    "아직 답할 준비가 되지 않았습니다",
                    "문서 내용을 읽고 의미 색인을 만든 뒤에 질문에 답할 수 있습니다. "
                    "그때까지는 [문서]에서 직접 찾아볼 수 있습니다.",
                    "문서 보기",
                    self.go_documents.emit,
                )
            )

    # ── 질문 ────────────────────────────────────────────────────────
    def _ask(self) -> None:
        question = self.input.text().strip()
        if not question or self._runner.running:
            return
        self._runner.start(question)
        self.input.setEnabled(False)
        self.send.setEnabled(False)
        clear_layout(self.result_layout)
        self.result_layout.addWidget(muted_label(f"“{question}” 찾아보는 중…"))

    def _on_answer(self, answer: Answer) -> None:
        self.input.setEnabled(True)
        self.send.setEnabled(True)
        self._render_answer(answer)

    def _render_answer(self, answer: Answer) -> None:
        clear_layout(self.result_layout)

        if answer.error:
            self.result_layout.addWidget(UnknownBlock(answer.error))
            return

        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)

        if answer.withheld:
            card.body.addWidget(muted_label(answer.text or "확인 가능한 자료가 부족합니다."))
            if answer.related_docs:
                card.body.addWidget(muted_label("대신 찾은 문서", small=True))
                for related in answer.related_docs:
                    open_btn = QPushButton(f"📄 {related.filename}")
                    open_btn.setObjectName("Link")
                    open_btn.clicked.connect(
                        lambda _=False, p=related.path: self._open(p)
                    )
                    card.body.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            self.result_layout.addWidget(card)
            return

        card.body.addWidget(muted_label(answer.text))

        if answer.citations:
            card.body.addWidget(section_title("근거"))
            for citation in answer.citations:
                chip = EvidenceChip(citation.doc_id, citation.filename, citation.locator)
                chip.opened.connect(lambda _doc_id, p=citation.path: self._open(p))
                card.body.addWidget(chip, alignment=Qt.AlignmentFlag.AlignLeft)

        feedback = QHBoxLayout()
        feedback.setSpacing(theme.SP_MD)
        for label, rating in (("👍 도움됨", "helpful"), ("👎 부정확", "inaccurate"),
                               ("⚠ 출처가 틀림", "wrong_source")):
            button = QPushButton(label)
            button.setObjectName("Link")
            button.clicked.connect(lambda _=False, r=rating, b=button: self._rate(r, b))
            feedback.addWidget(button)
        feedback.addStretch(1)
        card.body.addLayout(feedback)

        self.result_layout.addWidget(card)

    def _rate(self, rating: str, button: QPushButton) -> None:
        row = self.db.con.execute(
            "SELECT id FROM questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return
        self.db.rate_question(row["id"], rating)
        button.setEnabled(False)
        button.setText(button.text() + " ✓")

    def _open(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            self.result_layout.addWidget(UnknownBlock(f"원본을 찾을 수 없습니다: {path}"))
            return
        self.db.audit("document.open", path)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — 사용자가 명시적으로 연 원본
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
