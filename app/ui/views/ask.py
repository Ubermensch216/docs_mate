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

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from ...jobs import AskRunner, WarmupRunner
from ...search.query import QueryScope
from ...search.rag import Answer
from .. import theme
from ..widgets import (
    Card,
    EmptyState,
    Evidence,
    EvidenceChip,
    EvidenceDrawer,
    InfoDot,
    UnknownBlock,
    clear_layout,
    muted_label,
    open_original,
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
        # 사용자가 질문을 타이핑하는 동안 생성 모델을 미리 올려 둔다.
        # 첫 질문만 유독 느린 것은 거의 전부 모델 적재 때문이다.
        self._warmup = WarmupRunner(self)
        self._current_question_id: int | None = None
        self._answer: Answer | None = None

        # 본문 + 근거 서랍. 서랍은 기본으로 숨어 있고 [n]을 누르면 열린다.
        split = QHBoxLayout(self)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        main = QWidget()
        outer = QVBoxLayout(main)
        outer.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        outer.setSpacing(theme.SP_LG)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        split.addWidget(main, 1)

        self.drawer = EvidenceDrawer()
        self.drawer.open_original.connect(self._open)
        split.addWidget(self.drawer)

        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP_SM)
        title_row.addWidget(view_title("자료에 대해 질문하세요"))
        title_row.addWidget(
            InfoDot(
                "등록한 자료만 근거로 답합니다. 자료에 없는 내용은 지어내지 않고, "
                "근거가 부족하면 답을 보류합니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        title_row.addStretch(1)
        outer.addLayout(title_row)

        # 범위 — 화면에서 고른 것이 질문 문장에서 읽어 낸 것을 이긴다.
        scope_row = QHBoxLayout()
        scope_row.setSpacing(theme.SP_SM)
        self.task_scope = QComboBox()
        self.task_scope.setToolTip("특정 업무의 자료만 근거로 삼습니다")
        self.year_scope = QComboBox()
        self.year_scope.setToolTip("특정 연도의 자료만 근거로 삼습니다")
        scope_row.addWidget(muted_label("범위", small=True))
        scope_row.addWidget(self.task_scope)
        scope_row.addWidget(self.year_scope)
        scope_row.addStretch(1)
        outer.addLayout(scope_row)

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
        self._reload_scopes()
        if ready:
            self._warmup.start()   # 한 번만 돈다
        self.input.setEnabled(ready and not self._runner.running)
        self.send.setEnabled(ready and not self._runner.running)
        # 답할 수 있어도 알릴 것이 있으면 띄운다 — 색인에서 빠진 자료가
        # 있다는 사실은 답변 자체만큼 중요하다.
        self.notice.setVisible(bool(message))
        self.notice.setMessage(message)

        if not self._runner.running:
            self._render_idle_or_empty(ready)

    def _reload_scopes(self) -> None:
        """업무·연도 목록을 다시 채운다. 사용자가 고른 값은 살린다.

        분석이 진행되면서 업무가 늘어나므로 목록도 함께 자란다 — 그때마다
        선택이 '전체'로 되돌아가면 사용자는 방금 고른 범위를 잃는다.
        """
        for combo, items, label in (
            (self.task_scope, self._task_items(), "전체 업무"),
            (self.year_scope, self._year_items(), "전체 기간"),
        ):
            chosen = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            for text, value in items:
                combo.addItem(text, value)
            index = combo.findData(chosen)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _task_items(self) -> list[tuple[str, int]]:
        return [(row["name"], row["id"]) for row in self.db.tasks()]

    def _year_items(self) -> list[tuple[str, int]]:
        rows = self.db.con.execute(
            "SELECT DISTINCT eff_year AS y FROM documents "
            "WHERE eff_year IS NOT NULL AND missing_since IS NULL ORDER BY y DESC"
        ).fetchall()
        return [(f"{row['y']}년", row["y"]) for row in rows]

    def _chosen_scope(self) -> QueryScope:
        year = self.year_scope.currentData()
        return QueryScope(
            years=[year] if year is not None else [],
            task_id=self.task_scope.currentData(),
        )

    def _readiness(self) -> tuple[bool, str]:
        """답할 수 있는가, 그리고 **무엇을 아직 못 보는가**.

        준비가 끝났다고만 말하면 사용자는 자료 전체를 근거로 답한다고 믿는다.
        일부 문서가 색인에서 빠져 있으면 그 사실을 밝혀야 한다 — 답이 틀린
        것보다 "왜 그 문서 얘기가 없지?"를 모르는 편이 더 위험하다.
        """
        counts = self.db.counts()
        if counts["total"] == 0:
            return False, "등록된 자료가 없습니다. 먼저 자료원을 추가하세요."
        if counts["chunks_embedded"] == 0:
            return False, (
                "질문에 답할 준비가 끝나지 않았습니다. 근거 없이 답하지 않기 위해 "
                "자료를 다 읽고 의미 색인을 만들 때까지 기다립니다."
            )

        left = self.db.unembedded_chunk_count()
        if left:
            missing = self.db.documents_missing_from_search()
            done = counts["chunks_embedded"]
            text = (
                f"질문 준비 {done:,} / {done + left:,} — 아직 {left:,}조각을 읽는 중입니다. "
                f"지금 답할 수는 있지만 일부 자료는 근거에 포함되지 않습니다."
            )
            if missing:
                names = ", ".join(m["filename"] for m in missing[:3])
                more = f" 외 {len(missing) - 3}건" if len(missing) > 3 else ""
                text += f"\n아직 검색되지 않는 문서: {names}{more}"
            return True, text
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
        self._runner.start(question, self._chosen_scope())
        self.input.setEnabled(False)
        self.send.setEnabled(False)
        # 지난 답의 근거가 서랍에 남아 있으면 새 답의 근거로 오해한다.
        self._answer = None
        self.drawer.dismiss()
        clear_layout(self.result_layout)
        self.result_layout.addWidget(muted_label(f"“{question}” 찾아보는 중…"))

    def _on_answer(self, answer: Answer) -> None:
        self.input.setEnabled(True)
        self.send.setEnabled(True)
        self._render_answer(answer)

    def _render_answer(self, answer: Answer) -> None:
        clear_layout(self.result_layout)
        self._answer = answer

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

        # 문장 끝의 [1][2]를 누를 수 있게 만든다. 근거 목록을 따로 읽고
        # 머릿속에서 짝을 맞추는 것이 아니라, 그 문장에서 바로 근거로 간다
        # (계획서 §14 — Claim → Evidence → Original).
        body = muted_label(_linkify_citations(answer.text))
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(False)
        body.linkActivated.connect(self._show_citation)
        card.body.addWidget(body)

        if answer.citations:
            card.body.addWidget(section_title("근거"))
            for citation in answer.citations:
                chip = EvidenceChip(
                    citation.doc_id, f"[{citation.index}] {citation.filename}",
                    citation.locator,
                )
                chip.opened.connect(
                    lambda _doc_id, n=citation.index: self._show_citation(str(n))
                )
                card.body.addWidget(chip, alignment=Qt.AlignmentFlag.AlignLeft)

        self._add_followups(card, answer)

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

    # ── 근거 서랍 ───────────────────────────────────────────────────
    def _show_citation(self, index: str) -> None:
        """[n]을 눌렀을 때 그 근거만 서랍에 띄운다."""
        if self._answer is None:
            return
        try:
            wanted = int(index)
        except ValueError:
            return
        picked = [c for c in self._answer.citations if c.index == wanted]
        self.drawer.show_evidence(
            [
                Evidence(label=c.filename, locator=c.locator,
                         snippet=c.snippet, path=c.path)
                for c in picked
            ],
            title=f"근거 [{wanted}]",
        )

    # ── 후속 질문 ───────────────────────────────────────────────────
    def _add_followups(self, card: Card, answer: Answer) -> None:
        """이전 대화를 기억하는 대신 **완성된 새 질문**을 만든다 (기준서 §17).

        버튼을 누르면 그 자리에서 독립적으로 검증 가능한 질문 하나가 새로
        실행된다 — 대화처럼 이어가되 챗봇으로 흐르지 않는다.
        """
        suggestions = _followup_questions(answer)
        if not suggestions:
            return

        card.body.addWidget(muted_label("이어서 물어보기", small=True))
        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        for label, question in suggestions:
            button = QPushButton(label)
            button.setToolTip(f"이렇게 묻습니다: {question}")
            button.clicked.connect(lambda _=False, q=question: self._ask_text(q))
            row.addWidget(button)
        row.addStretch(1)
        card.body.addLayout(row)

    def _ask_text(self, question: str) -> None:
        self.input.setText(question)
        self._ask()

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
        open_original(self, self.db, path)


# ── 표시 도우미 ─────────────────────────────────────────────────────

_CITATION_MARK = re.compile(r"\[(\d+)\]")


def _linkify_citations(text: str) -> str:
    """문장 끝의 [1][2]를 누를 수 있는 링크로 바꾼다.

    QLabel의 리치 텍스트를 쓰므로 본문은 반드시 이스케이프한다 — 문서에서
    온 문장에 <, & 같은 글자가 섞이면 태그로 해석돼 답이 깨진다.
    """
    escaped = html.escape(text or "")
    return _CITATION_MARK.sub(
        lambda m: f'<a href="{m.group(1)}" style="text-decoration:none;">[{m.group(1)}]</a>',
        escaped,
    )


def _followup_questions(answer: Answer) -> list[tuple[str, str]]:
    """(버튼 이름, 실제로 실행할 완성된 질문).

    이전 질문을 가리키는 지시대명사("그럼 작년은?")를 만들지 않는다. 각
    질문은 그것만 읽어도 뜻이 통해야 나중에 그 답을 다시 검증할 수 있다.
    """
    question = (answer.question or "").strip()
    if not question:
        return []

    out: list[tuple[str, str]] = []
    years = answer.inferred_years
    if years:
        # "2025년 …"을 "2024년 …"으로 바꾼 완전한 질문을 만든다.
        previous = min(years) - 1
        shifted = re.sub(r"(?<!\d)\d{4}(?=\s*년)", str(previous), question)
        if shifted == question:
            shifted = f"{previous}년 자료로 보면 어떤가요? {question}"
        out.append(("지난해 자료와 비교", shifted))

    documents = {c.filename for c in answer.citations}
    if documents:
        out.append(("근거 문서 더 보기", f"{question} 관련 문서를 모두 알려줘"))
    return out
