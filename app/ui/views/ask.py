"""질문 화면 — 자유 탐색.

대화가 아니라 단발 질의로 설계한다. 멀티턴 문맥 유지·지시대명사 해석·대화
기억을 만들지 않는다. 챗봇으로 흐르지 않으면서 정형 화면이 못 덮는 질문을
처리하는 방법이다(doc/00 §6.4).

답변 정책 (구현 강제)
  · 검색된 프로젝트 자료만 근거로 답한다. 일반 지식으로 빈칸을 채우지 않는다.
  · 사실 문장마다 출처를 남긴다. 출처 없는 답은 화면에 올리지 않는다.
  · 근거가 부족하면 답하지 않는다 — 대신 찾은 문서 목록을 보여준다.

화면 구성 — 세로로 쌓지 않고 셋으로 가른다.

    ┌────────────────────────────────────────────────────┐
    │ 질문 입력 · 범위                                    │
    ├──────────┬──────────────────────┬──────────────────┤
    │ 물어보기 │  답변 (주인공)        │  근거            │
    │ 최근 질문│                      │  인용문·원본     │
    └──────────┴──────────────────────┴──────────────────┘

이전에는 전부 한 줄로 쌓여 있었고, 근거 목록이 답변 카드 안에서 세로
공간을 다 잡아먹어 **정작 답이 화면 아래로 밀려났다**. 답이 주인공이고
근거는 그 옆에 붙어 있어야 한다 — 답을 읽으면서 근거를 곁눈질할 수 있어야
"근거를 확인한다"가 실제 동작이 된다.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
    EvidenceDrawer,
    InfoDot,
    UnknownBlock,
    clear_layout,
    muted_label,
    open_original,
    view_title,
)

EXAMPLES = [
    ("이번 달에 내가 해야 할 일이 뭐야?", "When"),
    ("작년 9월엔 무슨 일이 많았어?", "When"),
    ("예산 요구자료는 어떤 순서로 만들어?", "How"),
    ("계약 업무는 무슨 일이야?", "What"),
]

GUIDE_W = 208
# 이보다 좁아지면 왼쪽 칸을 접는다. 셋을 다 욱여넣으면 정작 답변 칸이
# 두세 낱말마다 줄바꿈되는 폭이 되어 읽기가 더 나빠진다.
GUIDE_MIN_WINDOW = 1000
RECENT_LIMIT = 5


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

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SP_XL, theme.SP_LG, 0, 0)
        root.setSpacing(theme.SP_MD)
        root.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.SP_LG)
        self.guide = self._build_guide()
        body.addWidget(self.guide)
        body.addWidget(self._build_center(), 1)

        self.drawer = EvidenceDrawer()
        self.drawer.open_original.connect(self._open)
        body.addWidget(self.drawer)
        root.addLayout(body, 1)

        self.refresh()

    # ── 구성 ────────────────────────────────────────────────────────
    def _build_header(self) -> QVBoxLayout:
        head = QVBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.setContentsMargins(0, 0, theme.SP_XL, 0)

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
        head.addLayout(title_row)

        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        self.input = QLineEdit()
        self.input.setPlaceholderText("작년 행감 때 수질 관련해서 뭘 제출했어?")
        self.input.setMinimumHeight(34)
        self.input.returnPressed.connect(self._ask)
        row.addWidget(self.input, 1)
        self.send = QPushButton("질문")
        self.send.setObjectName("Primary")
        self.send.setMinimumHeight(34)
        self.send.clicked.connect(self._ask)
        row.addWidget(self.send)
        head.addLayout(row)

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
        head.addLayout(scope_row)

        self.notice = UnknownBlock("")
        head.addWidget(self.notice)
        return head

    def _build_guide(self) -> QWidget:
        """왼쪽 — 무엇을 물을 수 있는지, 무엇을 이미 물었는지."""
        holder = QWidget()
        holder.setFixedWidth(GUIDE_W)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, theme.SP_LG)
        column.setSpacing(theme.SP_SM)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)

        column.addWidget(muted_label("이렇게 물어보세요", small=True))
        for text, _kind in EXAMPLES:
            column.addWidget(self._chip(text))

        column.addSpacing(theme.SP_MD)
        column.addWidget(muted_label("최근 질문", small=True))
        self.recent_column = QVBoxLayout()
        self.recent_column.setSpacing(theme.SP_XS)
        column.addLayout(self.recent_column)
        return holder

    def _chip(self, text: str) -> QPushButton:
        chip = QPushButton(_clip(text, 20))
        chip.setObjectName("Chip")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(text)
        chip.clicked.connect(lambda _=False, t=text: self._ask_text(t))
        return chip

    def _build_center(self) -> QWidget:
        """가운데 — 답변. 이 화면의 주인공이라 남는 공간을 전부 받는다."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        self.result_layout = QVBoxLayout(body)
        self.result_layout.setContentsMargins(0, 0, theme.SP_MD, theme.SP_LG)
        self.result_layout.setSpacing(theme.SP_MD)
        self.result_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        self.result_area = scroll
        return scroll

    # ── 반응형 ──────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self.guide.setVisible(self.width() >= GUIDE_MIN_WINDOW)

    # ── 준비 상태 ───────────────────────────────────────────────────
    def refresh(self) -> None:
        ready, message = self._readiness()
        self._reload_scopes()
        self._reload_recent()
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

    def _reload_recent(self) -> None:
        """이미 물어본 질문을 다시 꺼내 쓸 수 있게 한다.

        답을 못 얻은 질문도 남긴다 — 자료가 더 색인된 뒤에 다시 물으면
        답이 나올 수 있고, 무엇을 이미 시도했는지 아는 것 자체가 정보다.
        """
        clear_layout(self.recent_column)
        rows = self.db.con.execute(
            "SELECT question, MAX(id) AS last FROM questions "
            "WHERE question <> '' GROUP BY question ORDER BY last DESC LIMIT ?",
            (RECENT_LIMIT,),
        ).fetchall()
        if not rows:
            self.recent_column.addWidget(
                muted_label("아직 물어본 질문이 없습니다", small=True)
            )
            return
        for row in rows:
            self.recent_column.addWidget(self._chip(row["question"]))

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
        self.drawer.hide()
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
            return
        self.result_layout.addWidget(
            EmptyState(
                "무엇이든 물어보세요",
                "등록한 자료에서 찾아 답합니다. 답에는 문장마다 근거가 붙고, "
                "그 근거를 눌러 원문을 확인할 수 있습니다.",
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
        # 지난 답의 근거가 옆에 남아 있으면 새 답의 근거로 오해한다.
        self._answer = None
        self.drawer.hide()
        clear_layout(self.result_layout)
        self.result_layout.addWidget(_thinking(question))

    def _ask_text(self, question: str) -> None:
        self.input.setText(question)
        self._ask()

    def _on_answer(self, answer: Answer) -> None:
        self.input.setEnabled(True)
        self.send.setEnabled(True)
        self._render_answer(answer)
        self._reload_recent()

    def _render_answer(self, answer: Answer) -> None:
        clear_layout(self.result_layout)
        self._answer = answer

        if answer.error:
            self.drawer.hide()
            self.result_layout.addWidget(UnknownBlock(answer.error))
            return

        if answer.withheld:
            self.drawer.hide()
            self.result_layout.addWidget(self._withheld_card(answer))
            return

        card = Card()
        card.setObjectName("AnswerCard")

        # 문장 끝의 [1][2]를 누를 수 있게 만든다. 근거를 따로 읽고 머릿속에서
        # 짝을 맞추는 것이 아니라, 그 문장에서 바로 옆 패널의 근거로 간다
        # (계획서 §14 — Claim → Evidence → Original).
        body = muted_label(_linkify_citations(answer.text))
        body.setObjectName("AnswerBody")
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(False)
        body.linkActivated.connect(self._show_citation)
        card.body.addWidget(body)

        self._add_followups(card, answer)
        self._add_feedback(card)
        self.result_layout.addWidget(card)

        # 근거는 답 옆에 상주한다. 열고 닫게 하면 "확인"이 한 단계 더 먼
        # 일이 되어 결국 아무도 안 누른다.
        self.drawer.show_evidence(
            [
                Evidence(label=c.filename, locator=c.locator, snippet=c.snippet,
                         path=c.path, mark=c.index)
                for c in answer.citations
            ],
            title="근거",
        )

    def _withheld_card(self, answer: Answer) -> QWidget:
        card = Card()
        card.body.addWidget(
            muted_label(answer.text or "확인 가능한 자료가 부족합니다.")
        )
        if answer.related_docs:
            card.body.addWidget(muted_label("대신 찾은 문서", small=True))
            for related in answer.related_docs:
                open_btn = QPushButton(f"📄 {related.filename}")
                open_btn.setObjectName("Link")
                open_btn.clicked.connect(lambda _=False, p=related.path: self._open(p))
                card.body.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    # ── 근거 ────────────────────────────────────────────────────────
    def _show_citation(self, index: str) -> None:
        """[n]을 눌렀을 때 옆 패널의 그 근거를 짚어 준다."""
        try:
            self.drawer.highlight(int(index))
        except ValueError:
            return

    # ── 후속 질문 ───────────────────────────────────────────────────
    def _add_followups(self, card: Card, answer: Answer) -> None:
        """이전 대화를 기억하는 대신 **완성된 새 질문**을 만든다 (기준서 §17).

        버튼을 누르면 그 자리에서 독립적으로 검증 가능한 질문 하나가 새로
        실행된다 — 대화처럼 이어가되 챗봇으로 흐르지 않는다.
        """
        suggestions = _followup_questions(answer)
        if not suggestions:
            return

        card.body.addWidget(_divider())
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

    def _add_feedback(self, card: Card) -> None:
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


def _thinking(question: str) -> QWidget:
    """생성 중 표시.

    가느다란 막대 하나면 충분하다. 회전하는 그림이나 말풍선은 이 제품의
    성격에 맞지 않고(계획서 §26), 단계를 지어내 보여주는 것은 거짓
    진행률이다 — 우리는 지금 어느 단계인지 실제로 알지 못한다.
    """
    holder = Card()
    holder.body.addWidget(muted_label(f"“{_clip(question, 60)}”"))
    holder.body.addWidget(muted_label("자료에서 찾아 답을 만들고 있습니다", small=True))

    bar = QProgressBar()
    bar.setObjectName("Thinking")
    bar.setRange(0, 0)          # 값 없는 진행 — 남은 시간을 아는 척하지 않는다
    bar.setTextVisible(False)
    bar.setFixedHeight(3)
    holder.body.addWidget(bar)
    return holder


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
    if not question or answer.withheld:
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

    if {c.filename for c in answer.citations}:
        out.append(("근거 문서 더 보기", f"{question} 관련 문서를 모두 알려줘"))
    return out
