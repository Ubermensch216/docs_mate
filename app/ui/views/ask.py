"""질문 화면 — 자유 탐색.

대화가 아니라 단발 질의로 설계한다. 멀티턴 문맥 유지·지시대명사 해석·대화
기억을 만들지 않는다. 챗봇으로 흐르지 않으면서 정형 화면이 못 덮는 질문을
처리하는 방법이다(doc/00 §6.4).

답변 정책 (구현 강제)
  · 검색된 프로젝트 자료만 근거로 답한다. 일반 지식으로 빈칸을 채우지 않는다.
  · 사실 문장마다 출처를 남긴다. 출처 없는 답은 화면에 올리지 않는다.
  · 근거가 부족하면 답하지 않는다 — 대신 찾은 문서 목록을 보여준다.

화면 구성 — **근거 대조형** (디자인 개선안 1c).

    ┌──────────────────────────────────────────────────────────┐
    │ 질문 입력                                                 │
    │ 자주 찾는 것: 칩 …                        내 질문 기록 ▾  │
    ├───────────────────────┬──────────────────────────────────┤
    │ 답변                  │ 근거 원문 | 버전 비교 | 이 업무의 │
    │ 근거 1·2·3 (고르기)   │                                  │
    │ 이 답변으로 할 일     │ 고른 근거의 **원문**을 문단 단위로│
    │ 다음 단계까지 보려면  │ 인용된 대목을 강조해서 보여준다   │
    └───────────────────────┴──────────────────────────────────┘

전에는 근거가 파일명·인용문 한 줄로만 있어서, 답을 믿을지 판단하려면 결국
원본을 열어야 했다. 원본을 여는 순간 화면을 떠나고 맥락을 잃는다. 그래서
**원문 자체를 앱 안 오른쪽에 편다** — 인용된 문단은 강조하고, 나머지는
접어 둔다. 파일이 여러 벌이면 어느 것이 최신본인지도 여기서 답한다.
"""

from __future__ import annotations

import html
import re
from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from ...jobs import AskRunner, WarmupRunner
from ...search.query import QueryScope
from ...search.rag import Answer
from .. import theme
from ..widgets import (
    Badge,
    Card,
    ElidedLabel,
    EmptyState,
    InfoDot,
    SubPanel,
    TabBar,
    UnknownBlock,
    clear_layout,
    muted_label,
    open_original,
    section_title,
    view_title,
)

EXAMPLES = [
    "이번 달에 내가 해야 할 일이 뭐야?",
    "예산 요구자료는 어떤 순서로 만들어?",
    "계약 업무는 무슨 일이야?",
    "작년 9월엔 무슨 일이 많았어?",
]

# 답변 칸. 너무 넓으면 한 줄이 길어져 다음 줄 첫 글자를 눈이 못 찾고,
# 너무 좁으면 문장이 두세 낱말마다 끊긴다.
ANSWER_MIN_W = 380
ANSWER_MAX_W = 560
RECENT_LIMIT = 5
# 칩 줄에 세워 둘 질문 수. 넷을 넘기면 한 줄을 넘어가고, 셋 아래면 고를 것이
# 없어 보인다.
CHIP_LIMIT = 4
# 원문에서 처음부터 펼쳐 두는 대목 수. 인용된 대목은 이 수와 무관하게 항상 편다.
OPEN_SECTIONS = 3

SOURCE, VERSIONS, TASK_DOCS = "source", "versions", "task_docs"


class AskView(QWidget):
    go_documents = Signal()
    open_task = Signal(int)     # 근거 문서가 속한 업무로 이동

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._runner = AskRunner(db.path, self)
        self._runner.done.connect(self._on_answer)
        # 사용자가 질문을 타이핑하는 동안 생성 모델을 미리 올려 둔다.
        # 첫 질문만 유독 느린 것은 거의 전부 모델 적재 때문이다.
        self._warmup = WarmupRunner(self)
        self._answer: Answer | None = None
        self._context: dict[int, dict] = {}
        self._selected: int | None = None      # 지금 오른쪽에 편 근거 번호
        self._reader_tab = SOURCE
        self._expanded: set[int] = set()       # 원문을 다 편 문서

        self.setObjectName("Canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 묻는 자리와 읽는 자리를 흰 띠와 회색 바탕으로 가른다. 둘이 같은 흰
        # 바탕에 이어져 있으면 어디까지가 조작이고 어디부터가 결과인지 눈이
        # 매번 다시 찾아야 한다 — 다른 화면이 쓰는 구조를 이 화면만 따르지
        # 않고 있었다.
        header = QWidget()
        header.setObjectName("ViewHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head_wrap = QVBoxLayout(header)
        head_wrap.setContentsMargins(theme.SP_XL, theme.SP_LG, theme.SP_XL, theme.SP_LG)
        head_wrap.setSpacing(theme.SP_MD)
        head_wrap.addLayout(self._build_header())
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(theme.SP_XL, theme.SP_LG, theme.SP_XL, theme.SP_LG)
        body.setSpacing(theme.SP_LG)
        body.addWidget(self._build_answer_pane())
        body.addWidget(self._build_reader_pane(), 1)
        root.addLayout(body, 1)

        self.refresh()

    # ── 머리 ────────────────────────────────────────────────────────
    def _build_header(self) -> QVBoxLayout:
        head = QVBoxLayout()
        head.setSpacing(theme.SP_SM)

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
        self.input.setMinimumHeight(theme.CONTROL_H)
        self.input.returnPressed.connect(self._ask)
        row.addWidget(self.input, 1)
        self.send = QPushButton("질문")
        self.send.setObjectName("Primary")
        self.send.setMinimumHeight(theme.CONTROL_H)
        self.send.setMinimumWidth(88)
        self.send.clicked.connect(self._ask)
        row.addWidget(self.send)
        head.addLayout(row)

        # 예시 질문과 지난 질문을 **한 줄**에 둔다. 세로로 두 목록을 쌓으면
        # 같은 문장이 두 번 보이고, 화면 왼쪽을 통째로 잡아먹는다.
        chips = QHBoxLayout()
        chips.setSpacing(theme.SP_SM)
        chips.addWidget(muted_label("자주 찾는 것", small=True, wrap=False))
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(theme.SP_SM)
        chips.addLayout(self.chip_row)
        chips.addStretch(1)
        self.history = QPushButton("내 질문 기록")
        self.history.setObjectName("Quiet")
        self.history.setMinimumHeight(theme.CONTROL_H - 4)
        self.history.setCursor(Qt.CursorShape.PointingHandCursor)
        chips.addWidget(self.history)
        head.addLayout(chips)

        # 범위 — 화면에서 고른 것이 질문 문장에서 읽어 낸 것을 이긴다.
        scope_row = QHBoxLayout()
        scope_row.setSpacing(theme.SP_SM)
        self.task_scope = QComboBox()
        self.task_scope.setToolTip("특정 업무의 자료만 근거로 삼습니다")
        self.task_scope.setMinimumWidth(200)
        self.year_scope = QComboBox()
        self.year_scope.setToolTip("특정 연도의 자료만 근거로 삼습니다")
        self.year_scope.setMinimumWidth(160)
        scope_row.addWidget(
            muted_label("찾는 범위", small=True, wrap=False), 0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        scope_row.addWidget(self.task_scope)
        scope_row.addWidget(self.year_scope)
        scope_row.addStretch(1)
        head.addLayout(scope_row)

        self.notice = UnknownBlock("")
        head.addWidget(self.notice)
        return head

    def _chip(self, text: str) -> QPushButton:
        """질문 하나를 담은 칩. **글자를 자르지 않는다.**

        예전에는 20자에서 잘라 "이번 달에 내가 해야 할 일이…"처럼 문장이
        끊겼다. 고를 수 있는 질문이 무엇인지 읽히지 않으면 칩은 장식이다.
        """
        chip = QPushButton(text)
        chip.setObjectName("Chip")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(text)
        chip.clicked.connect(lambda _=False, t=text: self._ask_text(t))
        return chip

    # ── 왼쪽: 답변 ──────────────────────────────────────────────────
    def _build_answer_pane(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("AnswerPane")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(ANSWER_MIN_W)
        scroll.setMaximumWidth(ANSWER_MAX_W)

        holder = QWidget()
        holder.setObjectName("PaneBody")
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, theme.SP_SM, 0)
        column.setSpacing(theme.SP_MD)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 답은 흰 카드 위에 놓는다. 회색 바탕과 갈라야 "여기부터가 답"이
        # 경계로 보인다.
        card = Card()
        card.setObjectName("AnswerCard")
        self.answer_column = card.body
        column.addWidget(card)
        # 카드가 남는 세로 공간을 다 먹으면 내용 아래로 흰 판이 길게 늘어진다.
        column.addStretch(1)

        scroll.setWidget(holder)
        self.answer_pane = scroll
        return scroll

    # ── 오른쪽: 근거 원문 ───────────────────────────────────────────
    def _build_reader_pane(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("ReaderPane")
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.reader_tabs = TabBar(
            ((SOURCE, "근거 원문"), (VERSIONS, "버전 비교"), (TASK_DOCS, "이 업무의 문서"))
        )
        self.reader_tabs.switched.connect(self._switch_reader)
        column.addWidget(self.reader_tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.reader_column = QVBoxLayout(body)
        self.reader_column.setContentsMargins(
            theme.SP_LG, theme.SP_LG, theme.SP_LG, theme.SP_LG
        )
        self.reader_column.setSpacing(theme.SP_MD)
        self.reader_column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        column.addWidget(scroll, 1)

        self.reader = holder
        return holder

    def _switch_reader(self, key: str) -> None:
        self._reader_tab = key
        self._render_reader()

    # ── 준비 상태 ───────────────────────────────────────────────────
    def refresh(self) -> None:
        ready, message = self._readiness()
        self._reload_scopes()
        self._reload_questions()
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

    def _reload_questions(self) -> None:
        """예시 칩과 '내 질문 기록'을 다시 채운다.

        이미 물어본 질문은 예시에서 뺀다 — 예전에는 아홉 칸 중 실제 선택지가
        다섯이었다. 같은 문장이 두 목록에 그대로 반복되면 목록이 길어 보일
        뿐 고를 것은 늘지 않는다.
        """
        clear_layout(self.chip_row)
        rows = self.db.con.execute(
            "SELECT question, MAX(id) AS last FROM questions "
            "WHERE question <> '' GROUP BY question ORDER BY last DESC LIMIT ?",
            (RECENT_LIMIT,),
        ).fetchall()
        asked = [row["question"] for row in rows]

        # 최근에 물은 것을 앞에 두고 모자라는 자리를 예시로 채워 **늘 네 개**를
        # 유지한다. 예전에는 이미 물어본 예시를 지우기만 해서 칩이 하나만 남는
        # 일이 생겼다 — 한 개짜리 목록은 목록이 아니다.
        chips: list[str] = []
        for text in [*asked, *EXAMPLES]:
            if text not in chips:
                chips.append(text)
        for text in chips[:CHIP_LIMIT]:
            self.chip_row.addWidget(self._chip(text))

        # 버튼에 메뉴를 달면 Qt가 화살표를 스스로 그린다. 글자에 ▾를 또 적으면
        # 화살표가 두 개로 보인다.
        self.history.setText(f"내 질문 기록 {len(asked)}건" if asked else "내 질문 기록")
        self.history.setEnabled(bool(asked))
        # 메뉴의 주인은 항상 그 메뉴를 여는 버튼이다 — 부모가 없으면 파이썬이
        # 함수가 끝나는 순간 수거해 빈 메뉴가 뜬다(업무 화면에서 겪었다).
        menu = QMenu(self.history)
        for question in asked:
            menu.addAction(question, lambda q=question: self._ask_text(q))
        self.history.setMenu(menu if asked else None)

    def _reload_scopes(self) -> None:
        """업무·연도 목록을 다시 채운다. 사용자가 고른 값은 살린다."""
        for combo, items, label in (
            (self.task_scope, self._task_items(), "업무 전체"),
            (self.year_scope, self._year_items(), "기간 제한 없음"),
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
                "자료를 다 읽고 서로 견줄 준비가 될 때까지 기다립니다."
            )

        left = self.db.unembedded_chunk_count()
        if left:
            missing = self.db.documents_missing_from_search()
            done = counts["chunks_embedded"]
            text = (
                f"질문에 답할 준비를 하는 중 {done:,} / {done + left:,} — "
                f"아직 {left:,}조각을 읽고 있습니다. "
                f"지금 답할 수는 있지만 일부 자료는 근거에 포함되지 않습니다."
            )
            if missing:
                names = ", ".join(m["filename"] for m in missing[:3])
                more = f" 외 {len(missing) - 3}건" if len(missing) > 3 else ""
                text += f"\n아직 검색되지 않는 문서: {names}{more}"
            return True, text
        return True, ""

    def _render_idle_or_empty(self, ready: bool) -> None:
        clear_layout(self.answer_column)
        clear_layout(self.reader_column)
        self._answer = None
        self._selected = None
        self.reader_tabs.setVisible(False)
        if not ready:
            self.answer_column.addWidget(
                EmptyState(
                    "아직 답할 준비가 되지 않았습니다",
                    "문서를 읽고 서로 견줄 준비가 된 뒤에 질문에 답할 수 있습니다. "
                    "그때까지는 [문서]에서 직접 찾아볼 수 있습니다.",
                    "문서 보기",
                    self.go_documents.emit,
                )
            )
            return
        self.answer_column.addWidget(
            EmptyState(
                "무엇이든 물어보세요",
                "등록한 자료에서 찾아 답합니다. 답에는 문장마다 근거가 붙고, "
                "그 근거의 원문을 오른쪽에서 바로 확인할 수 있습니다.",
            )
        )
        self._reader_placeholder()

    def _reader_placeholder(self) -> None:
        """빈 원문 칸을 흰 여백으로 두지 않는다.

        아무 설명 없는 큰 빈 판은 "고장 났나"로 읽힌다. 무엇이 여기 올지
        한 줄로 말해 두면 같은 여백이 '아직 비어 있는 자리'가 된다.
        """
        clear_layout(self.reader_column)
        self.reader_tabs.setVisible(False)
        hint = muted_label(
            "답을 만들면 근거로 쓴 문서의 원문이 여기에 펼쳐집니다. 인용한 "
            "대목은 칠해서 보여 주고, 같은 문서가 여러 벌이면 어느 것이 "
            "최신본인지도 여기서 확인할 수 있습니다."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.reader_column.addStretch(1)
        self.reader_column.addWidget(hint)
        self.reader_column.addStretch(2)

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
        self._selected = None
        self._expanded.clear()
        self._reader_placeholder()
        clear_layout(self.answer_column)
        self.answer_column.addWidget(_thinking(question))

    def _ask_text(self, question: str) -> None:
        self.input.setText(question)
        self._ask()

    def _on_answer(self, answer: Answer) -> None:
        self.input.setEnabled(True)
        self.send.setEnabled(True)
        self._render_answer(answer)
        self._reload_questions()

    # ── 답변 ────────────────────────────────────────────────────────
    def _render_answer(self, answer: Answer) -> None:
        clear_layout(self.answer_column)
        clear_layout(self.reader_column)
        self._answer = answer
        self._context = self.db.document_context([c.doc_id for c in answer.citations])

        if answer.error:
            self.reader_tabs.setVisible(False)
            self.answer_column.addWidget(UnknownBlock(answer.error))
            return

        if answer.withheld:
            self.reader_tabs.setVisible(False)
            self.answer_column.addWidget(self._withheld_card(answer))
            return

        self.answer_column.addWidget(muted_label("답변", small=True))
        # 문장 끝의 [1][2]를 누를 수 있게 만든다. 근거를 따로 읽고 머릿속에서
        # 짝을 맞추는 것이 아니라, 그 문장에서 바로 오른쪽 원문으로 간다
        # (계획서 §14 — Claim → Evidence → Original).
        body = muted_label(_linkify_citations(answer.text))
        body.setObjectName("AnswerBody")
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(False)
        body.linkActivated.connect(self._show_citation)
        self.answer_column.addWidget(body)

        marks = {c.index for c in answer.citations}
        if self._selected not in marks:
            # 첫 근거를 기본으로 편다. 사용자가 고른 것이 있으면 그것을 지킨다 —
            # 목록을 다시 그릴 때마다 오른쪽이 1번으로 돌아가면 대조가 안 된다.
            self._selected = answer.citations[0].index if answer.citations else None
        self._render_evidence_list()
        self._render_actions()
        self._render_next_step()
        self._render_reader()

    def _render_evidence_list(self) -> None:
        """근거 목록. 고르면 오른쪽 원문이 그 문서로 바뀐다."""
        if self._answer is None or not self._answer.citations:
            return
        self.evidence_box = QVBoxLayout()
        self.evidence_box.setSpacing(theme.SP_SM)
        for citation in self._answer.citations:
            info = self._context.get(citation.doc_id, {})
            self.evidence_box.addWidget(self._evidence_card(citation, info))
        self.answer_column.addLayout(self.evidence_box)

    def _evidence_card(self, citation, info: dict) -> QWidget:
        chosen = citation.index == self._selected
        panel = QFrame()
        panel.setObjectName("EvidencePick")
        panel.setProperty("picked", chosen)
        panel.setCursor(Qt.CursorShape.PointingHandCursor)
        panel.mousePressEvent = (          # noqa: SLF001 — 판 전체가 누르는 자리다
            lambda _event, index=citation.index: self._select(index)
        )

        row = QHBoxLayout(panel)
        row.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        row.setSpacing(theme.SP_SM)

        mark = QLabel(str(citation.index))
        mark.setObjectName("SourceMarkOn" if chosen else "SourceMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(20, 20)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(theme.SP_XS)
        name = ElidedLabel(citation.filename or info.get("filename") or "")
        name.setObjectName("EvidenceName" if chosen else "Muted")
        column.addWidget(name)
        column.addWidget(muted_label(self._evidence_note(citation, info), small=True))
        row.addLayout(column, 1)
        return panel

    def _evidence_note(self, citation, info: dict) -> str:
        """이 파일에 대해 **아는 것만** 한 줄로. 모르면 그 자리를 비운다."""
        parts = [_document_date(info)] if info else []
        label, _kind = _freshness(info)
        if label:
            parts.append(label)
        if citation.index == self._selected:
            parts.append("오른쪽에서 보고 있음")
        return "  ·  ".join(part for part in parts if part)

    def _render_actions(self) -> None:
        if self._answer is None:
            return
        self.answer_column.addWidget(_divider())
        self.answer_column.addWidget(muted_label("이 답변으로 할 일", small=True))
        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)

        for label, question in _followup_questions(self._answer):
            button = QPushButton(label)
            button.setToolTip(f"이렇게 묻습니다: {question}")
            button.clicked.connect(lambda _=False, q=question: self._ask_text(q))
            row.addWidget(button)

        # 피드백은 둘뿐이다. '부정확'과 '출처가 틀림'의 차이를 처음 쓰는
        # 사람이 판단할 수 없다 — 고를 수 없는 선택지는 답을 왜곡한다.
        for label, rating in (("맞아요", "helpful"), ("사실과 달라요", "inaccurate")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, r=rating, b=button: self._rate(r, b))
            row.addWidget(button)
        row.addStretch(1)
        self.answer_column.addLayout(row)

    def _render_next_step(self) -> None:
        """'다음 단계까지 보려면' — 답 하나로 끝나지 않게 한다.

        인수받는 사람에게 필요한 것은 이 문서 하나가 아니라 그 문서가 놓인
        순서다. 근거가 한 업무에 몰려 있을 때만, 그리고 **뒤에 실제로 남은
        단계가 있을 때만** 말한다.
        """
        task_id = self._dominant_task()
        if task_id is None:
            return
        years = [
            info["eff_year"] for info in self._context.values()
            if info.get("task_id") == task_id and info.get("eff_year")
        ]
        if not years:
            return
        year = max(years)
        steps = self.db.task_steps(task_id, year)
        here = {
            info.get("step_label") for info in self._context.values()
            if info.get("task_id") == task_id
        }
        labels = [step["label"] for step in steps]
        after = [label for label in labels if label not in here]
        if not steps or not after:
            return

        panel = SubPanel()
        panel.body.addWidget(muted_label("다음 단계까지 보려면", small=True))
        task = self.db.task(task_id)
        panel.body.addWidget(
            muted_label(
                f"이 업무에는 ‘{after[0]}’ 단계가 이어집니다. "
                f"올해 같은 일을 맡는다면 그 순서를 먼저 보세요."
            )
        )
        go = QPushButton(f"{task['name'] if task else ''} 처리 순서 보기 →")
        go.setObjectName("Link")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(lambda _=False, t=task_id: self.open_task.emit(t))
        panel.body.addWidget(go, alignment=Qt.AlignmentFlag.AlignLeft)
        self.answer_column.addWidget(panel)

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

    # ── 근거 고르기 ─────────────────────────────────────────────────
    def _show_citation(self, index: str) -> None:
        try:
            self._select(int(index))
        except ValueError:
            return

    def _select(self, index: int) -> None:
        if self._answer is None:
            return
        if index not in {c.index for c in self._answer.citations}:
            return
        self._selected = index
        self._reader_tab = SOURCE
        # 고른 표시는 목록 전체를 다시 그려야 옮겨진다(이전 선택도 풀려야 한다).
        self._rebuild_answer_column()

    def _rebuild_answer_column(self) -> None:
        answer = self._answer
        if answer is None:
            return
        self._render_answer(answer)

    def _selected_citation(self):
        if self._answer is None or self._selected is None:
            return None
        for citation in self._answer.citations:
            if citation.index == self._selected:
                return citation
        return None

    def _dominant_task(self) -> int | None:
        tasks = [
            info["task_id"] for info in self._context.values() if info.get("task_id")
        ]
        return Counter(tasks).most_common(1)[0][0] if tasks else None

    # ── 오른쪽 그리기 ───────────────────────────────────────────────
    def _render_reader(self) -> None:
        clear_layout(self.reader_column)
        citation = self._selected_citation()
        if citation is None:
            self.reader_tabs.setVisible(False)
            return

        info = self._context.get(citation.doc_id, {})
        siblings = self.db.version_siblings(citation.doc_id)
        task_id = info.get("task_id")
        task_docs = self.db.task_documents(task_id) if task_id else []

        self.reader_tabs.setVisible(True)
        self.reader_tabs.set_count(VERSIONS, "버전 비교", len(siblings))
        self.reader_tabs.set_count(TASK_DOCS, "이 업무의 문서", len(task_docs))
        self.reader_tabs.select(self._reader_tab)

        if self._reader_tab == VERSIONS:
            self._render_versions(citation, siblings)
        elif self._reader_tab == TASK_DOCS:
            self._render_task_docs(task_id, task_docs)
        else:
            self._render_source(citation, info)

    def _render_source(self, citation, info: dict) -> None:
        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        mark = QLabel(str(citation.index))
        mark.setObjectName("SourceMarkOn")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(20, 20)
        head.addWidget(mark)
        head.addWidget(section_title(citation.filename or info.get("filename") or ""), 1)
        open_button = QPushButton("원본 열기")
        open_button.setObjectName("Quiet")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(
            lambda _=False, p=info.get("path") or citation.path: self._open(p)
        )
        head.addWidget(open_button)
        self.reader_column.addLayout(head)

        sections = self.db.sections(citation.doc_id)
        card = Card(tone="static")
        card.body.addWidget(_meta_strip(self.db, citation, info, len(sections)))

        if not sections:
            card.body.addWidget(
                UnknownBlock(
                    "이 문서의 본문을 읽지 못해 원문을 보여줄 수 없습니다. "
                    "‘원본 열기’로 직접 확인하세요."
                )
            )
            self.reader_column.addWidget(card)
            return

        cited = _cited_ordinals(sections, citation)
        opened = self._visible_ordinals(citation.doc_id, sections, cited)
        hidden: list[int] = []
        for section in sections:
            ordinal = section["ordinal"]
            if ordinal in opened:
                if hidden:
                    card.body.addWidget(self._folded(citation.doc_id, hidden))
                    hidden = []
                card.body.addWidget(_paragraph(section, ordinal in cited))
            else:
                hidden.append(ordinal)
        if hidden:
            card.body.addWidget(self._folded(citation.doc_id, hidden))
        self.reader_column.addWidget(card)

    def _visible_ordinals(self, doc_id: int, sections, cited: set[int]) -> set[int]:
        """처음에는 인용된 대목과 그 앞뒤만 편다.

        문서 전체를 다 펴면 오른쪽 칸이 원문 뷰어가 아니라 스크롤 지옥이 된다.
        인용된 곳을 눈이 바로 찾을 수 있는 것이 이 칸의 목적이다.
        """
        if doc_id in self._expanded:
            return {section["ordinal"] for section in sections}
        opened = set(cited)
        for ordinal in list(cited):
            opened.add(ordinal + 1)
        opened |= {section["ordinal"] for section in sections[:OPEN_SECTIONS]}
        return opened

    def _folded(self, doc_id: int, ordinals: list[int]) -> QWidget:
        first, last = ordinals[0], ordinals[-1]
        span = f"{first}~{last}문단" if first != last else f"{first}문단"
        button = QPushButton(f"{span} 접힘 — 눌러서 펼치기")
        button.setObjectName("Fold")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, d=doc_id: self._expand(d))
        return button

    def _expand(self, doc_id: int) -> None:
        self._expanded.add(doc_id)
        self._render_reader()

    def _render_versions(self, citation, siblings) -> None:
        """'어느 파일이 최신본인가' — 이름이 아니라 수정 시각으로 답한다."""
        self.reader_column.addWidget(section_title("어느 파일이 최신본인가"))
        if len(siblings) < 2:
            self.reader_column.addWidget(
                muted_label(
                    "같은 내용으로 여러 번 저장한 파일을 찾지 못했습니다. "
                    "이 문서 한 벌만 있습니다.",
                )
            )
            return

        self.reader_column.addWidget(
            muted_label("이름만으로는 판단할 수 없어 파일 수정 시각으로 비교했습니다",
                        small=True)
        )
        counts = self.db.section_counts([row["id"] for row in siblings])
        newest = max((row["fs_mtime"] or "") for row in siblings)
        # 수정 시각이 같은 파일이 여럿이면 시각으로는 가릴 수 없다. 그때
        # 둘 다 '최신본'이라고 붙이면 답이 아니라 혼란이다 — 못 가린다고
        # 말하고 판단 재료(문단 수)를 옆에 둔다.
        tied = sum(1 for row in siblings if (row["fs_mtime"] or "") == newest) > 1
        if tied:
            self.reader_column.addWidget(
                UnknownBlock(
                    "가장 최근에 저장된 파일이 여럿입니다. 파일 수정 시각이 같아 "
                    "어느 것이 최신본인지 시각으로는 가릴 수 없습니다 — 문단 수와 "
                    "내용을 직접 확인하세요."
                )
            )
        for row in siblings:
            latest = bool(row["fs_mtime"]) and row["fs_mtime"] == newest
            panel = SubPanel()
            panel.setProperty("picked", latest and not tied)
            top = QHBoxLayout()
            top.setSpacing(theme.SP_SM)
            if latest:
                mark = Badge("가장 최근 저장" if tied else "최신본",
                             "attention" if tied else "ok")
            else:
                mark = Badge("이전 저장", "neutral")
            top.addWidget(mark)
            top.addWidget(muted_label(row["ext"] or "", small=True, wrap=False))
            top.addStretch(1)
            panel.body.addLayout(top)

            name = ElidedLabel(row["filename"])
            name.setObjectName("EvidenceName" if latest else "Muted")
            panel.body.addWidget(name)

            facts = [f"수정 {_stamp(row['fs_mtime'])}"]
            if counts.get(row["id"]):
                facts.append(f"{counts[row['id']]}문단")
            if row["id"] == citation.doc_id:
                facts.append("지금 보는 문서")
            panel.body.addWidget(muted_label("  ·  ".join(facts), small=True))

            open_button = QPushButton("원본 열기")
            open_button.setObjectName("Quiet")
            open_button.setCursor(Qt.CursorShape.PointingHandCursor)
            open_button.clicked.connect(lambda _=False, p=row["path"]: self._open(p))
            panel.body.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)
            self.reader_column.addWidget(panel)

    def _render_task_docs(self, task_id: int | None, docs) -> None:
        task = self.db.task(task_id) if task_id else None
        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(section_title(f"{task['name']}의 문서" if task else "이 업무의 문서"))
        head.addStretch(1)
        if task_id:
            go = QPushButton("업무 열기 →")
            go.setObjectName("Link")
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.clicked.connect(lambda _=False, t=task_id: self.open_task.emit(t))
            head.addWidget(go)
        self.reader_column.addLayout(head)

        if not docs:
            self.reader_column.addWidget(
                muted_label("이 근거 문서는 아직 어느 업무에도 배정되지 않았습니다.")
            )
            return
        for row in docs:
            panel = SubPanel()
            line = QHBoxLayout()
            line.setSpacing(theme.SP_SM)
            name = ElidedLabel(row["filename"])
            name.setToolTip(row["path"])
            line.addWidget(name, 1)
            line.addWidget(
                muted_label(_document_date(dict(row)), small=True, wrap=False)
            )
            open_button = QPushButton("원본 열기")
            open_button.setObjectName("Quiet")
            open_button.setCursor(Qt.CursorShape.PointingHandCursor)
            open_button.clicked.connect(lambda _=False, p=row["path"]: self._open(p))
            line.addWidget(open_button)
            panel.body.addLayout(line)
            self.reader_column.addWidget(panel)

    # ── 부속 ────────────────────────────────────────────────────────
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
_ORDINAL_IN_LOCATOR = re.compile(r"(\d+)\s*문단")


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


def _paragraph(section, cited: bool) -> QWidget:
    """원문 한 대목. 인용된 곳은 칠하고 번호를 함께 적는다."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.SP_MD)

    number = QLabel(str(section["ordinal"]))
    number.setObjectName("ParaNumber")
    number.setFixedWidth(24)
    number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    row.addWidget(number)

    text = QLabel(" ".join((section["text"] or "").split()))
    text.setObjectName("ParaCited" if cited else "ParaText")
    text.setWordWrap(True)
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    row.addWidget(text, 1)
    return holder


def _meta_strip(db: Database, citation, info: dict, sections: int) -> QWidget:
    """`보고서 · 작성 2024. 10. 12. · 수정 2024. 10. 14. · 8문단 · 인용 1곳`."""
    analysis = db.analysis(citation.doc_id)
    parts = []
    kind = (analysis["doc_type"] if analysis else "") or (info.get("ext") or "").lstrip(".")
    if kind:
        parts.append(kind)
    parts.append(f"작성 {_document_date(info)}")
    if info.get("fs_mtime"):
        parts.append(f"수정 {_stamp(info['fs_mtime'])}")
    if sections:
        parts.append(f"{sections}문단")
    if citation.locator:
        parts.append(f"인용 {citation.locator}")
    return muted_label("   ·   ".join(parts), small=True)


def _cited_ordinals(sections, citation) -> set[int]:
    """인용된 대목이 몇 번째인가.

    locator가 '3문단'이면 그 번호를 그대로 쓴다. 페이지·시트처럼 문단 번호가
    아닌 형태이거나 비어 있으면 **인용문과 글자가 겹치는 대목**을 찾는다.
    못 찾으면 아무것도 칠하지 않는다 — 엉뚱한 곳을 강조하는 것보다 낫다.
    """
    found: set[int] = set()
    for section in sections:
        if citation.locator and section["locator"] == citation.locator:
            found.add(section["ordinal"])
    if found:
        return found

    match = _ORDINAL_IN_LOCATOR.search(citation.locator or "")
    if match:
        wanted = int(match.group(1))
        if any(section["ordinal"] == wanted for section in sections):
            return {wanted}

    snippet = " ".join((citation.snippet or "").split())[:40]
    if len(snippet) >= 8:
        for section in sections:
            if snippet in " ".join((section["text"] or "").split()):
                return {section["ordinal"]}
    return found


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _stamp(value: str | None) -> str:
    """'2024-10-14T17:22:00' → '10. 14. 17:22'."""
    if not value:
        return "—"
    date_part, _, time_part = value.partition("T")
    pieces = date_part.split("-")
    if len(pieces) < 3:
        return value
    text = f"{int(pieces[1])}. {int(pieces[2])}."
    return f"{text} {time_part[:5]}" if time_part else text


def _document_date(info: dict) -> str:
    """'2024. 10. 12.' — 아는 만큼만 적는다."""
    value = info.get("eff_date")
    if not value:
        return "시점 미상"
    precision = info.get("eff_precision") or "day"
    if precision == "year":
        text = f"{value[:4]}년"
    elif precision == "month":
        text = f"{value[:4]}. {int(value[5:7])}."
    else:
        text = f"{value[:4]}. {int(value[5:7])}. {int(value[8:10])}."
    return f"{text} (파일 날짜)" if info.get("eff_date_kind") == "fs" else text


def _freshness(info: dict) -> tuple[str, str]:
    """(뱃지 글자, 뱃지 종류). 판정할 수 없으면 빈 문자열.

    좌측 메뉴가 '이 파일이 최신본인가'를 약속하므로 답변 화면도 같은 질문에
    답해야 한다. 단, **아는 것만** 말한다. 내용이 똑같은 사본이 여럿일 때
    파일 수정일로 최신을 고르는 것까지가 우리가 아는 사실이고, 내용이
    '비슷한' 문서 중 최신을 고르는 것은 추측이라 하지 않는다.
    """
    copies = info.get("copies") or 0
    if copies > 1:
        newest = info.get("newest_copy_at")
        if newest and info.get("fs_mtime") == newest:
            return f"같은 사본 {copies}개 중 최신", "ok"
        return f"같은 내용 사본 {copies}개", "attention"

    latest_year = info.get("task_latest_year")
    year = info.get("eff_year")
    if latest_year and year and year < latest_year:
        return f"{latest_year}년 자료 있음", "neutral"
    return "", "neutral"


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
    return out
