"""업무 화면 — What + How. 홈을 겸한다.

첫 화면이 곧 정체성의 답이다. `파일 12,842건`이 아니라
`당신이 인수받은 업무는 7개입니다`가 여기에 온다.

먼저 읽을 문서에는 반드시 이유를 붙인다. 이유 없는 별점은 신뢰를 만들지 못한다.

상세를 탭으로 나눈 이유
    한 업무가 답해야 하는 질문은 넷이고, 서로 성격이 다르다. 넷을 세로로
    이어 붙이면 화면은 "긴 문서 하나"가 되어, 지금 읽는 문단이 어느 질문의
    답인지 스크롤 도중에 잃는다. 게다가 각 구획마다 교정 버튼이 붙으므로
    이어 붙인 화면은 버튼밭이 된다.

    탭으로 끊으면 한 번에 한 질문만 보이고, 탭 이름에 건수를 달아 **누르기
    전에** 규모를 알린다. 확인이 필요한 탭에는 ◐를 붙여, 손댈 곳이 어느
    탭인지 열어 보지 않고도 알 수 있게 한다.
"""

from __future__ import annotations

from datetime import date
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import handover, status, timeline
from ...db import Database
from .. import theme
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    EvidenceDrawer,
    FlowGrid,
    InfoDot,
    ListRow,
    Page,
    SubPanel,
    TabBar,
    TaskCard,
    TimelineGrid,
    UnknownBlock,
    clear_layout,
    hint_row,
    legend_text,
    muted_label,
    open_original,
    section_title,
    view_title,
)
from . import evidence
from .cycle_format import (
    cycle_headline,
    cycle_note,
    is_now,
    next_occurrence_text,
    parse_months,
    upcoming,
)

STAGES = [
    ("파일 찾기", "total", None),
    ("내용 읽기", "parsed", "documents"),
    ("의미 색인", "embedded", "documents"),
    ("업무 파악하기", "in_task", "documents"),
]

# 상세 화면의 네 질문. 라벨은 개념 이름이 아니라 사용자가 실제로 품는 질문이다.
READ, WHEN, HOW, DOCS = "read", "when", "how", "docs"
DETAIL_TABS = (
    (READ, "먼저 읽을 문서"),
    (WHEN, "언제 하는 일인가"),
    (HOW, "어떻게 처리했나"),
    (DOCS, "이 업무의 문서"),
)
DETAIL_HINTS = {
    READ: "이 업무를 처음 맡았다면 이 순서로 읽으세요. 고른 이유를 함께 적었습니다.",
    WHEN: "자료에 남은 문서의 시점을 연도별로 편 것입니다. "
          "세로로 같은 달이 겹치면 반복입니다.",
    HOW: "전임자가 남긴 문서의 순서로 되짚은 것입니다. 틀린 곳은 여기서 바로 고칩니다.",
    DOCS: "★은 이 업무를 대표하는 문서입니다. ⋯을 누르면 다른 업무로 옮기거나 "
          "대표로 지정합니다.",
}



class TasksView(QWidget):
    go_documents = Signal()
    go_calendar = Signal()
    state_changed = Signal()   # 확인·교정이 일어났다 (셸의 진행도가 듣는다)

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._task_id: int | None = None   # None이면 목록, 값이 있으면 상세
        self._how_year: dict[int, int] = {}   # task_id -> 사용자가 고른 연도
        self._tab = READ
        self.setObjectName("Canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # "지금 무엇을 보고 있는가"는 스크롤을 따라 사라지면 안 된다.
        # 제목·상태·조작을 흰 띠에 고정하고, 그 아래를 회색 본문으로 둔다.
        # 탭은 그 띠의 아래 모서리를 물고 앉는다 — 떠 있는 글자가 아니라
        # 머리에 붙은 손잡이로 보여야 한다.
        header = QWidget()
        header.setObjectName("ViewHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head_wrap = QVBoxLayout(header)
        head_wrap.setContentsMargins(theme.SP_XL, theme.SP_LG, theme.SP_XL, 0)
        head_wrap.setSpacing(theme.SP_MD)

        self.head = QVBoxLayout()
        self.head.setContentsMargins(0, 0, 0, 0)
        self.head.setSpacing(theme.SP_SM)
        head_wrap.addLayout(self.head)

        self.tabs = TabBar(DETAIL_TABS)
        self.tabs.switched.connect(self._switch)
        self.tabs.setVisible(False)
        head_wrap.addWidget(self.tabs)

        # 탭이 없을 때(목록)는 머리 띠 아래 여백을 대신 채운다.
        self._head_pad = QWidget()
        self._head_pad.setFixedHeight(theme.SP_LG)
        head_wrap.addWidget(self._head_pad)
        outer.addWidget(header)

        # 카드 그리드는 세로로만 늘어나야 한다. 가로 스크롤을 허용하면
        # 좁은 창에서 열 수가 줄지 않고 카드가 잘린 채 옆으로 밀린다(Page가 막는다).
        self.stack = QStackedWidget()
        self.list_page = Page()
        self.stack.addWidget(self.list_page)
        self.pages: dict[str, Page] = {}
        for key, _label in DETAIL_TABS:
            # 본문 기둥의 폭을 묶는다. 창을 넓혔을 때 한 줄이 끝없이 길어지면
            # 다음 줄 첫 글자를 눈이 못 찾는다.
            page = Page(max_width=theme.CONTENT_MAX_W)
            self.pages[key] = page
            self.stack.addWidget(page)

        # 근거 서랍은 질문 화면과 같은 위젯이다(계획서 §29). 업무 화면에서는
        # 상주가 아니라 부를 때만 연다 — 여기서는 근거가 늘 있는 것이 아니라
        # 어떤 주장(설명·주기·단계)을 짚었을 때 생긴다.
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stack, 1)
        self.drawer = EvidenceDrawer()
        self.drawer.open_original.connect(self._open)
        body.addWidget(self.drawer)
        outer.addLayout(body, 1)

        self.column = self.list_page.column
        self.refresh()

    # ── 진입점 ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        """분석 중에는 1.5초마다 불린다. 보고 있던 탭을 절대 빼앗지 않는다."""
        clear_layout(self.head)
        if self._task_id is not None and self.db.task(self._task_id):
            self._render_detail(self._task_id)
        else:
            self._task_id = None
            self.tabs.setVisible(False)
            self._head_pad.setVisible(True)
            self.stack.setCurrentWidget(self.list_page)
            self.column = self.list_page.reset()
            self._render_list()
        self.state_changed.emit()

    def _switch(self, key: str) -> None:
        self._tab = key
        # 다른 탭의 주장을 짚어 둔 근거가 그대로 남으면, 지금 보는 화면의
        # 근거인 줄 알고 읽는다. 화면이 바뀌면 서랍도 닫는다.
        self.drawer.dismiss()
        self.stack.setCurrentWidget(self.pages[key])

    def open_task(self, task_id: int) -> None:
        self._task_id = task_id
        self._tab = READ   # 새 업무를 열면 언제나 "무엇부터 읽나"부터다
        self.drawer.dismiss()
        self.refresh()

    def back(self) -> None:
        self._task_id = None
        self.drawer.dismiss()
        self.refresh()

    # ── 목록 ────────────────────────────────────────────────────────
    def _render_list(self) -> None:
        counts = self.db.counts()
        tasks = self.db.tasks()

        if not tasks:
            self._render_progress(counts)
            return

        # 제목 옆 ⓘ 하나에 "이 숫자가 어디서 나왔는가"와 "카드를 어떻게
        # 읽는가"를 함께 접는다. 둘 다 첫 방문에만 필요한 말이다.
        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP_SM)
        title_row.addWidget(
            view_title(f"당신이 인수받은 업무는 {len(tasks)}개로 추정됩니다")
        )
        title_row.addWidget(
            InfoDot(
                f"전임자 자료 {counts['documents']:,}건을 살펴본 결과입니다. "
                "AI가 제안한 것이므로 확인하고 고칠 수 있습니다.<br><br>"
                "카드를 누르면 그 업무를 자세히 봅니다. 왼쪽 띠 색은 업무를 "
                "구분하는 표시이고, 아래 열두 칸은 1월부터 12월까지 그 업무를 "
                "하는 달입니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        title_row.addStretch(1)
        self.head.addLayout(title_row)

        # 인수인계에서 가장 먼저 궁금한 건 "얼마나 끝냈나"다. 카드를 세어
        # 알아내게 하지 말고 머리에서 바로 말한다.
        done = sum(1 for row in tasks if status.of_task(row) == status.CONFIRMED)
        progress = handover.summarize(self.db.handover_counts())
        marks = QHBoxLayout()
        marks.setSpacing(theme.SP_SM)
        marks.addWidget(Badge(f"{status.symbol(status.CONFIRMED)} 확인함 {done}", "ok"))
        if done < len(tasks):
            marks.addWidget(Badge(f"◐ 확인 필요 {len(tasks) - done}", "attention"))
        marks.addStretch(1)
        self.head.addLayout(marks)

        self._render_start_here(tasks, progress)

        # 카드 그리드로 한 화면 조망. 창을 넓히면 열이 늘어난다.
        grid = FlowGrid()
        for row in tasks:
            grid.add_card(self._task_card(row))
        self.column.addWidget(grid)

        self._render_leftovers(counts)

    def _render_start_here(self, tasks: list, progress) -> None:
        """‘지금 먼저 확인할 것’ (계획서 §7.1·§18).

        분석이 끝난 자리에서 "이제 뭘 하지"를 사용자가 스스로 알아내게 두지
        않는다. 별도 Dashboard 메뉴를 만들지 않고 업무 홈 맨 위에 둔다 —
        메뉴가 늘면 이 제품이 답하는 네 질문의 구조가 무너진다.

        빈 목록은 아예 그리지 않는다. 할 일이 없는데 '할 일' 판이 남아 있으면
        그 판을 매번 읽고 지나가야 한다.
        """
        today = date.today()
        soon = [
            entry for entry in upcoming(self.db.all_cycles(), today) if is_now(entry)
        ]
        unconfirmed = [
            row for row in tasks if status.of_task(row) != status.CONFIRMED
        ]
        marks = self.db.reading_marks()
        unread = [
            pick for row in tasks for pick in self.db.task_reading(row["id"])
            if pick["doc_id"] not in marks
        ]
        if not (soon or unconfirmed or unread):
            return

        card = Card(tone="primary")
        card.setMaximumWidth(theme.CONTENT_MAX_W)
        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(section_title("지금 먼저 확인할 것"))
        head.addWidget(
            InfoDot(
                "인수인계를 어디부터 손대야 할지 알려 줍니다. "
                "확인한 만큼 진행도가 오르고, 이 목록은 줄어듭니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        head.addStretch(1)
        head.addWidget(muted_label(progress.headline(), small=True, wrap=False))
        card.body.addLayout(head)

        if soon:
            names = ", ".join(entry.name for entry in soon[:3])
            more = f" 외 {len(soon) - 3}개" if len(soon) > 3 else ""
            card.body.addWidget(
                self._start_row(
                    f"이번 달 안에 시작될 업무 {len(soon)}개",
                    f"{names}{more}",
                    "일정에서 보기 →",
                    self.go_calendar.emit,
                )
            )
        if unconfirmed:
            names = ", ".join(row["name"] for row in unconfirmed[:3])
            more = f" 외 {len(unconfirmed) - 3}개" if len(unconfirmed) > 3 else ""
            first = unconfirmed[0]["id"]
            card.body.addWidget(
                self._start_row(
                    f"아직 확인하지 않은 업무 {len(unconfirmed)}개",
                    f"{names}{more}",
                    "첫 업무 열기 →",
                    lambda: self.open_task(first),
                )
            )
        if unread:
            names = ", ".join(pick["filename"] for pick in unread[:2])
            more = f" 외 {len(unread) - 2}건" if len(unread) > 2 else ""
            first_task = next(
                row["id"] for row in tasks
                if any(p["doc_id"] == unread[0]["doc_id"]
                       for p in self.db.task_reading(row["id"]))
            )
            card.body.addWidget(
                self._start_row(
                    f"아직 읽지 않은 핵심 문서 {len(unread)}건",
                    f"{names}{more}",
                    "먼저 읽을 문서 →",
                    lambda: self.open_task(first_task),
                )
            )
        self.column.addWidget(card)

    def _start_row(self, headline: str, detail: str, action: str, slot) -> QWidget:
        panel = SubPanel()
        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(headline)
        title.setObjectName("StartHereHead")
        text.addWidget(title)
        text.addWidget(muted_label(detail, small=True))
        row.addLayout(text, 1)

        link = QPushButton(action)
        link.setObjectName("Link")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda _=False: slot())
        row.addWidget(link, 0, Qt.AlignmentFlag.AlignVCenter)
        panel.body.addLayout(row)
        return panel

    def _task_card(self, row) -> QWidget:
        cycle = self.db.task_cycle(row["id"])
        card = TaskCard(
            task_id=row["id"],
            name=row["name"],
            description=row["description"],
            span_text=_span(row),
            months=_active_months(cycle),
            cycle_text=cycle_headline(cycle) if cycle else None,
            confidence=row["confidence"],
            review_state=status.of_task(row),
            reading_count=len(self.db.task_reading(row["id"])),
        )
        card.opened.connect(self.open_task)
        return card

    def _render_leftovers(self, counts: dict) -> None:
        unclassified = self.db.unclassified_count()
        parts = []
        if unclassified:
            parts.append(f"미분류 {unclassified:,}건")
        if counts["duplicate_extra"]:
            parts.append(f"중복 {counts['duplicate_extra']:,}건")
        if counts["parse_failed"]:
            parts.append(f"읽지 못함 {counts['parse_failed']:,}건")
        if not parts:
            return

        # 남은 것을 화면 밑에 흐린 글씨로 흘리면 사용자는 못 본 채 넘어간다.
        # 판에 얹어 "이만큼은 업무에 못 넣었다"를 분명히 말한다.
        panel = SubPanel()
        panel.setMaximumWidth(theme.CONTENT_MAX_W)
        head_row = QHBoxLayout()
        head_row.setSpacing(theme.SP_SM)
        head = QLabel("⚠ " + " · ".join(parts))
        head.setObjectName("LeftoverHead")
        head_row.addWidget(head)
        head_row.addWidget(
            InfoDot(
                "미분류가 남는 것은 정상입니다. 확신이 없는 문서를 억지로 "
                "업무에 밀어 넣지 않습니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        head_row.addStretch(1)
        link = QPushButton("문서에서 보기 →")
        link.setObjectName("Link")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(self.go_documents.emit)
        head_row.addWidget(link)
        panel.body.addLayout(head_row)
        self.column.addWidget(panel)

    def _render_progress(self, counts: dict) -> None:
        if counts["total"] == 0:
            self.head.addWidget(view_title("업무"))
            self.column.addWidget(
                EmptyState(
                    "아직 살펴본 자료가 없습니다",
                    "자료원을 등록하면 파일을 먼저 찾고, 내용을 읽은 뒤 업무를 파악합니다.",
                )
            )
            return

        self.head.addWidget(view_title("자료를 살펴보고 있습니다"))
        self.head.addWidget(
            muted_label("네 단계를 차례로 지납니다. 다 끝나야 업무가 나옵니다.")
        )
        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)
        for label, done_key, total_key in STAGES:
            done = counts.get(done_key, 0)
            limit = counts.get(total_key, 0) if total_key else done
            line = QLabel(_stage_line(label, done, limit, total_key))
            line.setObjectName(
                "StageDone" if _stage_done(done, limit, total_key) else "Muted"
            )
            card.body.addWidget(line)
        self.column.addWidget(card)

        self.column.addWidget(
            EmptyState(
                "업무는 아직 파악하지 못했습니다",
                "내용을 읽고 의미 색인을 만들어야 업무를 나눌 수 있습니다. "
                "그동안 먼저 찾은 파일부터 [문서]에서 볼 수 있습니다.",
                "문서 보기",
                self.go_documents.emit,
            )
        )

    # ── 상세 ────────────────────────────────────────────────────────
    def _render_detail(self, task_id: int) -> None:
        row = self.db.task(task_id)
        self.tabs.setVisible(True)
        self._head_pad.setVisible(False)

        back = QPushButton("←  업무 목록")
        back.setObjectName("BackLink")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        back.clicked.connect(self.back)
        self.head.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        # 제목과 조작을 한 줄에 둔다. 제목은 왼쪽, 손댈 것은 오른쪽 —
        # 스크롤을 내려도 이 줄은 머리 띠에 남는다.
        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP_SM)
        title_row.addWidget(view_title(row["name"]))
        title_row.addStretch(1)

        edit = QPushButton("수정 ▾")
        edit.setObjectName("MenuButton")
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.setToolTip("업무명·설명을 고치거나 다른 업무와 합치고 나눕니다")
        edit.setMenu(self._edit_menu(task_id, edit))
        title_row.addWidget(edit)
        if status.of_task(row) != status.CONFIRMED:
            approve = QPushButton("이 업무 확인함")
            approve.setObjectName("Primary")
            approve.setCursor(Qt.CursorShape.PointingHandCursor)
            approve.clicked.connect(lambda: self._approve(task_id))
            title_row.addWidget(approve)
        self.head.addLayout(title_row)

        # 상태 두 축과 규모를 한 줄에 모은다. 왼쪽은 "사람이 확인했는가"
        # (§9의 4단계), 그다음은 "묶음이 얼마나 단단한가"(§7.2) — 다른
        # 질문이므로 함께 보여야 하지만, 각각 한 줄씩 차지할 것은 아니다.
        picks = self.db.task_reading(task_id)
        docs = self.db.task_documents(task_id)
        marks = QHBoxLayout()
        marks.setSpacing(theme.SP_SM)
        marks.addWidget(Badge.state(status.of_task(row)))
        note, kind = status.cluster_note(row["confidence"])
        marks.addWidget(Badge(note, kind))
        # db.task()는 집계 열을 들고 오지 않는다. 규모는 문서 목록에서 센다.
        meta = QLabel(_doc_span(docs))
        meta.setObjectName("MetaChip")
        meta.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        marks.addWidget(meta)
        marks.addStretch(1)
        self.head.addLayout(marks)

        # 설명은 상태보다 아래다. 위에 두면 제목과 붙어 두 줄 제목처럼 읽힌다.
        if row["description"]:
            about = QHBoxLayout()
            about.setSpacing(theme.SP_SM)
            about.addWidget(muted_label(row["description"], small=True), 1)
            basis = evidence.for_task(self.db, row, docs)
            if basis:
                about.addWidget(
                    self._evidence_button(
                        f"근거 {len(basis)}건",
                        "이 이름과 설명을 어느 문서에서 얻었는지 봅니다",
                        lambda: self._show_evidence(basis, "업무 설명의 근거"),
                    )
                )
            self.head.addLayout(about)

        cycle = self.db.task_cycle(task_id)
        self.tabs.set_count(READ, "먼저 읽을 문서", len(picks))
        self.tabs.set_count(DOCS, "이 업무의 문서", len(docs))
        # 손댈 곳이 있는 탭은 열어 보기 전에 알려 준다.
        self.tabs.set_label(
            WHEN,
            "언제 하는 일인가"
            + ("  ◐" if cycle and status.of_cycle(cycle) != status.CONFIRMED else ""),
        )

        self._render_reading(self.pages[READ].reset(), picks)
        self._render_when(self.pages[WHEN].reset(), task_id, cycle)
        self._render_how(self.pages[HOW].reset(), task_id)
        self._render_by_year(self.pages[DOCS].reset(), task_id, docs)

        self.tabs.select(self._tab)
        self.stack.setCurrentWidget(self.pages[self._tab])

    def _render_reading(self, column: QVBoxLayout, picks) -> None:
        column.addWidget(hint_row("이 순서로 읽으세요", DETAIL_HINTS[READ]))
        marks = self.db.reading_marks()

        if not picks:
            column.addWidget(
                UnknownBlock("추천할 문서를 고르지 못했습니다. "
                             "이 업무의 문서가 적거나 시점을 확인하지 못했습니다.")
            )
            return

        for index, pick in enumerate(picks, start=1):
            card = Card()
            card.body.setSpacing(theme.SP_SM)

            top = QHBoxLayout()
            top.setSpacing(theme.SP_MD)
            rank = QLabel(str(index))
            rank.setObjectName("RankChip")
            rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rank.setFixedSize(24, 24)
            top.addWidget(rank, 0, Qt.AlignmentFlag.AlignTop)

            name = QPushButton(f"📄 {pick['filename']}")
            name.setObjectName("CardTitle")
            name.setCursor(Qt.CursorShape.PointingHandCursor)
            name.setToolTip(f"{pick['path']}\n클릭하면 이 문서의 첫 대목을 옆에서 봅니다")
            name.clicked.connect(
                lambda _=False, d=pick["doc_id"], r=pick["reason"]: self._show_evidence(
                    evidence.from_documents(self.db, [d], note=f"고른 이유 · {r}"),
                    "먼저 읽을 문서",
                )
            )
            top.addWidget(name)
            top.addStretch(1)
            when = QLabel(_when(pick))
            when.setObjectName("MetaChip")
            when.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            top.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)

            # 읽음 표시는 인수인계 진행도의 네 축 중 하나다(§18). '읽었다'는
            # 어느 표에도 남지 않아서 이것만 따로 적어 둔다(handover_checks).
            read = QPushButton("읽음" if pick["doc_id"] in marks else "읽음 표시")
            read.setObjectName("Confirm" if pick["doc_id"] in marks else "Quiet")
            read.setCheckable(True)
            read.setChecked(pick["doc_id"] in marks)
            read.setCursor(Qt.CursorShape.PointingHandCursor)
            read.setToolTip("읽은 문서를 표시해 두면 어디까지 봤는지 남습니다")
            read.clicked.connect(
                lambda checked, d=pick["doc_id"]: self._mark_read(d, checked)
            )
            top.addWidget(read, 0, Qt.AlignmentFlag.AlignTop)
            card.body.addLayout(top)

            # 이유 없는 추천은 만들지 않는다. 판에 얹어 "이건 근거"라고 말한다.
            reason = SubPanel()
            reason.body.addWidget(muted_label(f"고른 이유 · {pick['reason']}", small=True))
            card.body.addWidget(reason)
            column.addWidget(card)

    def _render_when(self, column: QVBoxLayout, task_id: int, cycle) -> None:
        """When — 격자를 세로로 읽어 반복 주기를 보여준다.

        같은 격자를 Step 8에서 가로로 읽어 처리 순서(How)를 만든다.

        읽는 순서를 화면 순서와 맞춘다. ① 결론 한 줄 → ② 근거 격자 →
        ③ 교정. 결론과 근거가 섞이면 무엇이 주장이고 무엇이 증거인지
        구분되지 않는다.
        """
        rows = self.db.task_grid_documents(task_id)
        docs = [
            timeline.DatedDoc(
                doc_id=r["id"], year=r["year"], month=r["month"],
                day=int(r["eff_date"][8:10]) if r["eff_precision"] == "day" else None,
                trustworthy=r["eff_date_kind"] != "fs",
            )
            for r in rows
        ]
        grid = timeline.build_grid(docs)

        if not grid.years:
            column.addWidget(
                UnknownBlock("이 업무 문서의 시점을 확인할 수 없어 반복 여부를 판단하지 못했습니다.")
            )
            column.addWidget(self._cycle_actions(task_id, cycle))
            return

        if cycle and cycle["kind"] == "none":
            # 사람이 '반복 아님'으로 확정한 업무. 빈칸으로 두면 시스템이
            # 못 찾은 것인지 사람이 아니라고 한 것인지 구분되지 않는다.
            answer = Card(tone="primary")
            head = QHBoxLayout()
            head.setSpacing(theme.SP_SM)
            head.addWidget(Badge(status.label(status.CONFIRMED), "ok"))
            head.addStretch(1)
            answer.body.addLayout(head)
            line = QLabel("반복하지 않는 업무로 확인했습니다")
            line.setObjectName("Answer")
            answer.body.addWidget(line)
            column.addWidget(answer)
            column.addWidget(self._cycle_actions(task_id, cycle))
            return

        if cycle:
            # 결론을 한 줄로 먼저 말한다. 격자는 그 근거다.
            answer = Card(tone="primary")
            answer.body.setSpacing(theme.SP_SM)
            head = QHBoxLayout()
            head.setSpacing(theme.SP_SM)
            line = QLabel(f"🔁 {cycle_headline(cycle)} 반복")
            line.setObjectName("Answer")
            head.addWidget(line)
            head.addWidget(Badge.state(status.of_cycle(cycle)))
            head.addStretch(1)
            answer.body.addLayout(head)
            answer.body.addWidget(muted_label(cycle_note(cycle), small=True))

            next_text = next_occurrence_text(cycle, date.today())
            if next_text:
                foot = QHBoxLayout()
                foot.setSpacing(theme.SP_SM)
                nxt = QLabel(f"다음 예상 시점  {next_text}")
                nxt.setObjectName("WhenChip")
                nxt.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                foot.addWidget(nxt)
                foot.addStretch(1)
                go = QPushButton("연간 일정에서 보기 →")
                go.setObjectName("Link")
                go.setCursor(Qt.CursorShape.PointingHandCursor)
                go.clicked.connect(self.go_calendar.emit)
                foot.addWidget(go)
                answer.body.addLayout(foot)
            column.addWidget(answer)

        if len(grid.years) < 2:
            column.addWidget(
                UnknownBlock(
                    f"반복 여부를 판단할 자료가 부족합니다. 현재 확인된 연도가 "
                    f"{len(grid.years)}개뿐입니다. 최소 2개 연도가 있어야 반복을 제시합니다."
                )
            )

        years = grid.years[-6:]   # 화면 폭을 넘지 않도록 최근 6개 연도만
        cells = {(y, m): bool(grid.doc_ids(y, m)) for y in years for m in range(1, 13)}
        today = date.today()
        current = (today.year, today.month) if today.year in years else None
        confidence = cycle["confidence"] if cycle else "low"

        column.addWidget(hint_row("이렇게 판단한 근거", DETAIL_HINTS[WHEN]))
        column.addWidget(TimelineGrid(years, cells, confidence, current))
        column.addWidget(muted_label(legend_text(), small=True))

        column.addWidget(self._cycle_actions(task_id, cycle))

    def _cycle_actions(self, task_id: int, cycle) -> QWidget:
        """추정을 사실로 바꾸는 자리. AI 결과 옆에는 항상 교정 수단이 있어야 한다.

        본문에 섞어 두면 버튼이 내용처럼 보인다. 얇은 줄로 한 번 끊고
        '조작 구역'을 따로 만든다.
        """
        bar = QFrame()
        bar.setObjectName("ActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, theme.SP_MD, 0, 0)
        row.setSpacing(theme.SP_SM)

        if cycle is not None and status.of_cycle(cycle) != status.CONFIRMED:
            confirm = QPushButton("이 주기가 맞습니다")
            confirm.setObjectName("Confirm")
            confirm.setCursor(Qt.CursorShape.PointingHandCursor)
            confirm.clicked.connect(lambda: self._confirm_cycle(task_id))
            row.addWidget(confirm)

        edit = QPushButton("주기 수정")
        edit.setObjectName("Quiet")
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.clicked.connect(lambda: self._edit_cycle(task_id))
        row.addWidget(edit)

        basis = evidence.for_cycle(self.db, cycle)
        if basis:
            row.addWidget(
                self._evidence_button(
                    f"근거 {len(basis)}건",
                    "이 주기를 어느 문서의 시점에서 읽었는지 봅니다",
                    lambda: self._show_evidence(basis, "반복 주기의 근거"),
                )
            )

        if cycle is None or cycle["kind"] != "none":
            none = QPushButton("반복 아님")
            none.setObjectName("Quiet")
            none.setCursor(Qt.CursorShape.PointingHandCursor)
            none.clicked.connect(lambda: self._no_cycle(task_id))
            row.addWidget(none)
        row.addStretch(1)
        return bar

    def _render_how(self, column: QVBoxLayout, task_id: int) -> None:
        """How — 같은 격자를 가로로 읽어 처리 순서를 재현한다.

        인과관계 추론이 아니라 시간 근접 + 문서 유형 순서에 기반한
        재구성이므로 "~로 보입니다" 톤을 유지한다. 모든 단계는 문서에
        앵커링되고, 근거 없는 공백은 지어내지 않고 그렇다고 밝힌다.
        """
        years = self.db.task_years(task_id)
        if not years:
            column.addWidget(UnknownBlock("처리 순서를 재구성할 자료가 없습니다."))
            return

        default_year = timeline.default_how_year(years)
        selected = self._how_year.get(task_id, default_year)
        if selected not in years:
            selected = default_year

        header = QHBoxLayout()
        header.setSpacing(theme.SP_SM)
        lead = QLabel(f"{selected}년엔 이렇게 처리한 것으로 보입니다")
        lead.setObjectName("Answer")
        header.addWidget(lead)
        header.addWidget(InfoDot(DETAIL_HINTS[HOW]), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)

        ordered_years = sorted(years, reverse=True)
        combo = QComboBox()
        for y in ordered_years:
            combo.addItem(f"{y}년으로 보기", y)
        combo.setCurrentIndex(ordered_years.index(selected))
        combo.currentIndexChanged.connect(
            lambda _index, t=task_id, c=combo: self._change_how_year(t, c.currentData())
        )
        header.addWidget(combo)
        column.addLayout(header)

        steps = self.db.task_steps(task_id, selected)
        if not steps:
            column.addWidget(
                UnknownBlock(f"{selected}년에는 처리 순서를 재구성할 자료가 없습니다.")
            )
            add = QPushButton("＋ 단계 직접 추가")
            add.setObjectName("Link")
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.clicked.connect(lambda: self._add_step(task_id, selected, 0))
            column.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)
            return

        if any(s["decided_by"] == "user" for s in steps):
            column.addWidget(
                muted_label(
                    f"{status.symbol(status.CONFIRMED)} 이 연도의 순서는 담당자가 "
                    f"확인했습니다. 다시 분석해도 바뀌지 않습니다.",
                    small=True,
                )
            )

        last = len(steps)
        for position, step in enumerate(steps, start=1):
            line = ListRow()
            line.row.setSpacing(theme.SP_MD)

            # 번호는 순서 그 자체다. 흐린 글자로 두면 순서가 아니라 장식이 된다.
            mark = QLabel(_circled(step["ordinal"]))
            mark.setObjectName("StepMark")
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setFixedWidth(24)
            line.row.addWidget(mark)

            when = QLabel(step["day_hint"] or "시점 미상")
            when.setObjectName("StepWhen")
            when.setAlignment(Qt.AlignmentFlag.AlignCenter)
            when.setFixedWidth(84)
            line.row.addWidget(when)

            name = QLabel(step["label"])
            name.setObjectName("StepLabel")
            if step["is_inferred"]:
                # 사람이 채운 칸도 근거가 없으면 없다고 계속 드러낸다.
                name.setObjectName("StepLabelInferred")
                name.setToolTip("근거 문서가 없는 단계입니다")
            line.row.addWidget(name)

            if step["filename"]:
                # 원본을 바로 열지 않는다. 한글이 뜨는 데 몇 초가 걸리고 화면을
                # 떠나야 하므로, 확인이 아니라 이탈이 된다(계획서 §14).
                # 근거를 옆에서 먼저 보여주고 원본 열기는 그 안의 버튼으로 둔다.
                doc_btn = QPushButton(f"📄 {step['filename']}")
                doc_btn.setObjectName("Link")
                doc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                doc_btn.setToolTip(f"{step['path']}\n클릭하면 이 단계의 근거를 옆에서 봅니다")
                doc_btn.clicked.connect(
                    lambda _=False, s=step: self._show_evidence(
                        evidence.for_step(self.db, s), "처리 단계의 근거"
                    )
                )
                line.row.addWidget(doc_btn)
            line.row.addStretch(1)

            # 조작은 둘만 밖에 둔다. 순서 바꾸기는 자주 쓰고 결과가 눈앞에서
            # 확인되지만, 이름·추가·삭제까지 다섯 개를 늘어놓으면 줄마다
            # 버튼밭이 되어 정작 단계 이름이 안 읽힌다. 나머지는 ⋯ 안으로.
            for text, tip, slot, enabled in (
                ("▲", "위로", lambda _=False, s=step["id"]: self._move_step(s, -1),
                 position > 1),
                ("▼", "아래로", lambda _=False, s=step["id"]: self._move_step(s, 1),
                 position < last),
            ):
                button = QPushButton(text)
                button.setObjectName("IconButton")
                button.setToolTip(tip)
                button.setFixedWidth(26)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setEnabled(enabled)
                button.clicked.connect(slot)
                line.row.addWidget(button)

            more = QPushButton("⋯")
            more.setObjectName("IconButton")
            more.setFixedWidth(26)
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setToolTip("이름을 고치거나, 단계를 넣고 뺍니다")
            more.setMenu(self._step_menu(task_id, selected, step, more))
            line.row.addWidget(more)

            column.addWidget(line)

            # 근거 없는 공백은 지어내지 않고 그렇다고 밝힌다.
            if step["gap_note"]:
                column.addWidget(UnknownBlock(step["gap_note"]))

    def _step_menu(self, task_id: int, year: int, step, owner: QWidget) -> QMenu:
        """메뉴의 부모는 항상 그 메뉴를 여는 버튼이다 (아래 '교정' 절 참고)."""
        menu = QMenu(owner)
        step_id, label, ordinal = step["id"], step["label"], step["ordinal"]
        menu.addAction("단계 이름 수정", lambda: self._rename_step(step_id, label))
        menu.addAction(
            "이 아래에 단계 추가", lambda: self._add_step(task_id, year, ordinal)
        )
        menu.addSeparator()
        menu.addAction("이 단계 빼기", lambda: self._delete_step(step_id, label))
        return menu

    def _change_how_year(self, task_id: int, year: int) -> None:
        self._how_year[task_id] = year
        self.refresh()

    def _render_by_year(self, column: QVBoxLayout, task_id: int, rows) -> None:
        column.addWidget(hint_row("연도별 분포", DETAIL_HINTS[DOCS]))

        if not rows:
            column.addWidget(UnknownBlock("이 업무에 배정된 문서가 없습니다."))
            return

        by_year: dict[str, int] = {}
        for row in rows:
            key = str(row["eff_year"]) if row["eff_year"] else "연도 미상"
            by_year[key] = by_year.get(key, 0) + 1

        years = QHBoxLayout()
        years.setSpacing(theme.SP_SM)
        for year, count in sorted(by_year.items(), key=lambda kv: kv[0], reverse=True):
            chip = QLabel(f"{year} ({count})")
            chip.setObjectName("YearChip")
            chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            years.addWidget(chip)
        years.addStretch(1)
        column.addLayout(years)

        primary = self.db.primary_document(task_id)
        primary_id = primary["id"] if primary else None

        for row in rows[:30]:
            item = ListRow()
            item.row.setSpacing(theme.SP_MD)

            star = QLabel("★" if row["id"] == primary_id else "")
            star.setObjectName("PrimaryMark")
            star.setAlignment(Qt.AlignmentFlag.AlignCenter)
            star.setFixedWidth(16)
            item.row.addWidget(star)

            name = QPushButton(f"📄 {row['filename']}")
            name.setObjectName("RowTitle")
            name.setCursor(Qt.CursorShape.PointingHandCursor)
            name.setToolTip(
                f"{row['path']}\n클릭하면 원본을 엽니다"
                + ("\n이 업무의 대표 문서입니다" if row["id"] == primary_id else "")
            )
            name.clicked.connect(lambda _=False, p=row["path"]: self._open(p))
            item.row.addWidget(name)
            item.row.addStretch(1)
            item.row.addWidget(muted_label(_when(row), small=True, wrap=False))

            more = QPushButton("⋯")
            more.setObjectName("IconButton")
            more.setFixedWidth(26)
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setToolTip("이 문서를 다른 업무로 옮기거나 대표로 지정합니다")
            more.setMenu(
                self._document_menu(task_id, row["id"], row["id"] == primary_id, more)
            )
            item.row.addWidget(more)
            column.addWidget(item)

        if len(rows) > 30:
            column.addWidget(
                muted_label(f"… 외 {len(rows) - 30:,}건은 [문서]에서 볼 수 있습니다", small=True)
            )

    # ── 교정 ────────────────────────────────────────────────────────
    # AI가 지식을 만들고, 사람이 지식을 확정한다. 여기 있는 조작이 그
    # '확정' 쪽 절반이다 (계획서 §11). 모든 조작은 db의 교정 함수를 지나며,
    # 그 함수들이 재분석 보호와 기록을 함께 처리한다.
    #
    # 메뉴의 부모는 항상 그 메뉴를 여는 버튼이다. 뷰에 붙이면 두 가지가
    # 어긋난다. ① 분석 중에는 1.5초마다 다시 그리므로 뷰에 메뉴가 계속
    # 쌓인다. ② 부모 없이 만든 하위 메뉴는 파이썬 GC가 먼저 수거해 버려서
    # 눌렀을 때 빈 메뉴가 뜬다. 버튼에 붙이면 화면이 지워질 때 함께 사라진다.

    def _edit_menu(self, task_id: int, owner: QWidget) -> QMenu:
        menu = QMenu(owner)
        menu.addAction("업무명 수정", lambda: self._rename(task_id))
        menu.addAction("업무 설명 수정", lambda: self._edit_description(task_id))
        menu.addSeparator()
        merge = menu.addAction("다른 업무와 합치기", lambda: self._merge(task_id))
        merge.setEnabled(len(self.db.tasks()) > 1)
        menu.addAction("두 업무로 나누기", lambda: self._split(task_id))
        menu.addSeparator()
        menu.addAction("이건 업무가 아닙니다", lambda: self._not_a_task(task_id))
        return menu

    def _edit_description(self, task_id: int) -> None:
        row = self.db.task(task_id)
        text, ok = QInputDialog.getMultiLineText(
            self, "업무 설명 수정",
            "이 업무가 무엇인지 한두 문장으로 적어 주세요",
            row["description"] or "",
        )
        if not ok:
            return
        self.db.edit_task_description(task_id, text.strip())
        self.refresh()

    def _merge(self, task_id: int) -> None:
        others = [t for t in self.db.tasks() if t["id"] != task_id]
        if not others:
            return
        name = self.db.task(task_id)["name"]
        choice, ok = QInputDialog.getItem(
            self, "업무 합치기",
            f"'{name}'을(를) 어느 업무에 합칠까요?\n"
            f"문서가 모두 그쪽으로 옮겨지고, 이 업무는 목록에서 사라집니다.",
            [t["name"] for t in others], 0, False,
        )
        if not ok:
            return
        target = next(t for t in others if t["name"] == choice)
        if self.db.merge_tasks(task_id, target["id"]):
            self.open_task(target["id"])

    def _split(self, task_id: int) -> None:
        docs = self.db.task_documents(task_id)
        if len(docs) < 2:
            QMessageBox.information(
                self, "업무 나누기", "나누려면 문서가 2건 이상이어야 합니다."
            )
            return
        picked, name = SplitDialog.run(self, self.db.task(task_id)["name"], docs)
        if not picked:
            return
        new_id = self.db.split_task(task_id, picked, name)
        if new_id is None:
            QMessageBox.information(
                self, "업무 나누기", f"'{name}' 업무가 이미 있습니다. 다른 이름을 쓰세요."
            )
            return
        self.open_task(new_id)

    def _not_a_task(self, task_id: int) -> None:
        name = self.db.task(task_id)["name"]
        answer = QMessageBox.question(
            self, "업무 아님",
            f"'{name}'을(를) 업무 목록에서 내릴까요?\n\n"
            f"문서는 그대로 남고 삭제되지 않습니다. 다시 분석해도 이 판단은 "
            f"유지되므로 같은 묶음을 또 묻지 않습니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.mark_not_a_task(task_id)
        self.back()

    # ── 문서 교정 ───────────────────────────────────────────────────
    def _document_menu(self, task_id: int, doc_id: int, is_primary: bool,
                       owner: QWidget) -> QMenu:
        menu = QMenu(owner)
        if not is_primary:
            menu.addAction("대표 문서로 지정", lambda: self._set_primary(task_id, doc_id))
        others = [t for t in self.db.tasks() if t["id"] != task_id]
        if others:
            # addMenu(str)이 돌려주는 하위 메뉴는 주인이 없어 GC 대상이 된다.
            # 부모를 주고 만든 뒤에 붙인다.
            move = QMenu("다른 업무로 옮기기", menu)
            menu.addMenu(move)
            for other in others:
                move.addAction(
                    other["name"],
                    lambda _=False, t=other["id"]: self._move_document(task_id, t, doc_id),
                )
        menu.addSeparator()
        menu.addAction("이 업무 아님", lambda: self._detach(task_id, doc_id))
        return menu

    def _set_primary(self, task_id: int, doc_id: int) -> None:
        self.db.set_primary_document(task_id, doc_id)
        self.refresh()

    def _move_document(self, from_task: int, to_task: int, doc_id: int) -> None:
        self.db.move_document(from_task, to_task, doc_id)
        self.refresh()

    # ── When 교정 ───────────────────────────────────────────────────
    def _confirm_cycle(self, task_id: int) -> None:
        self.db.confirm_task_cycle(task_id)
        self.refresh()

    def _edit_cycle(self, task_id: int) -> None:
        cycle = self.db.task_cycle(task_id)
        current = cycle["months"] if cycle and cycle["kind"] == "yearly" else ""
        text, ok = QInputDialog.getText(
            self, "반복 주기 수정",
            "이 업무를 하는 달을 쉼표로 적어 주세요.  예: 9,10,11\n"
            "매달 하는 업무라면 '매월'이라고 적으세요.",
            text=current,
        )
        if not ok:
            return
        text = text.strip()
        if text in ("매월", "매달"):
            self.db.set_task_cycle(task_id, "monthly", "")
        else:
            months = sorted({
                int(p) for p in text.replace("월", "").split(",")
                if p.strip().isdigit() and 1 <= int(p) <= 12
            })
            if not months:
                QMessageBox.information(
                    self, "반복 주기 수정", "1~12 사이의 달을 쉼표로 구분해 적어 주세요."
                )
                return
            self.db.set_task_cycle(task_id, "yearly", ",".join(str(m) for m in months))
        self.refresh()

    def _no_cycle(self, task_id: int) -> None:
        self.db.mark_no_cycle(task_id)
        self.refresh()

    # ── How 교정 ────────────────────────────────────────────────────
    def _rename_step(self, step_id: int, current: str) -> None:
        text, ok = QInputDialog.getText(self, "단계 이름 수정", "단계 이름", text=current)
        if ok and text.strip():
            self.db.edit_step_label(step_id, text.strip())
            self.refresh()

    def _move_step(self, step_id: int, offset: int) -> None:
        self.db.move_step(step_id, offset)
        self.refresh()

    def _delete_step(self, step_id: int, label: str) -> None:
        answer = QMessageBox.question(self, "단계 삭제", f"'{label}' 단계를 뺄까요?")
        if answer == QMessageBox.StandardButton.Yes:
            self.db.delete_step(step_id)
            self.refresh()

    def _add_step(self, task_id: int, year: int, after_ordinal: int) -> None:
        text, ok = QInputDialog.getText(
            self, "단계 추가",
            "자료에는 없지만 실제로 있었던 단계를 적어 주세요.\n"
            "근거 문서가 없는 단계는 점선으로 표시됩니다.",
        )
        if ok and text.strip():
            self.db.add_step(task_id, year, text.strip(), after_ordinal)
            self.refresh()

    def _rename(self, task_id: int) -> None:
        row = self.db.task(task_id)
        name, ok = QInputDialog.getText(self, "업무명 수정", "업무 이름", text=row["name"])
        name = name.strip()
        if not ok or not name or name == row["name"]:
            return
        if not self.db.rename_task(task_id, name):
            QMessageBox.information(
                self, "업무명 수정", f"'{name}' 업무가 이미 있습니다. 다른 이름을 쓰세요."
            )
            return
        self.refresh()

    def _approve(self, task_id: int) -> None:
        self.db.confirm_task(task_id)
        self.refresh()

    def _detach(self, task_id: int, doc_id: int) -> None:
        self.db.detach_document(task_id, doc_id)
        self.refresh()

    def _mark_read(self, doc_id: int, done: bool) -> None:
        self.db.mark_reading(doc_id, done)
        self.refresh()

    def _open(self, path: str) -> None:
        open_original(self, self.db, path)

    # ── 근거 (계획서 §14·§29) ───────────────────────────────────────
    def _evidence_button(self, text: str, tip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Link")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tip)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _show_evidence(self, items, title: str) -> None:
        """근거를 옆 서랍에 편다. 빈 목록도 연다 — 조용히 아무 일도 일어나지
        않으면 사용자는 버튼이 고장 난 줄 안다. 서랍이 없다고 말해 준다."""
        self.drawer.show_evidence(items, title)


class SplitDialog(QDialog):
    """업무 나누기 — 떼어 낼 문서를 고르고 새 업무 이름을 짓는다.

    AI가 두 업무를 하나로 묶는 일은 흔하다(같은 부서, 비슷한 어휘). 그때
    사용자가 할 수 있는 일이 '삭제'뿐이면 자료를 잃는다. 그래서 나누기가
    교정 UX의 핵심 조작 중 하나다.
    """

    def __init__(self, parent: QWidget, task_name: str, docs) -> None:
        super().__init__(parent)
        self.setWindowTitle("업무 나누기")
        self.setMinimumWidth(520)

        column = QVBoxLayout(self)
        column.setSpacing(theme.SP_MD)
        column.addWidget(
            muted_label(f"'{task_name}'에서 떼어 낼 문서를 고르세요. "
                        f"고르지 않은 문서는 그대로 남습니다.")
        )

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for doc in docs:
            item = QListWidgetItem(f"{doc['filename']}   ({_when(doc)})")
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self.list.addItem(item)
        column.addWidget(self.list)

        column.addWidget(muted_label("새 업무 이름", small=True))
        self.name = QLineEdit()
        self.name.setPlaceholderText("예: 행감 질의대응")
        column.addWidget(self.name)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setEnabled(False)
        self.list.itemSelectionChanged.connect(self._revalidate)
        self.name.textChanged.connect(self._revalidate)

    def _revalidate(self) -> None:
        self._ok.setEnabled(
            bool(self.list.selectedItems()) and bool(self.name.text().strip())
        )

    @classmethod
    def run(cls, parent: QWidget, task_name: str, docs) -> tuple[list[int], str]:
        dialog = cls(parent, task_name, docs)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return [], ""
        picked = [
            item.data(Qt.ItemDataRole.UserRole) for item in dialog.list.selectedItems()
        ]
        return picked, dialog.name.text().strip()


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


def _stage_done(done: int, limit: int, total_key: str | None) -> bool:
    """끝난 단계는 진하게, 남은 단계는 흐리게 — 어디까지 왔는지가 보여야 한다."""
    return total_key is None or (limit > 0 and done >= limit)


def _active_months(cycle) -> list[int] | None:
    """카드 월 스트립에 채울 달. 주기를 못 찾았으면 None(빈 칸)."""
    if cycle is None:
        return None
    if cycle["kind"] == "monthly":
        return list(range(1, 13))
    return parse_months(cycle["months"]) or None


def _span(row) -> str:
    count = f"{row['doc_count']:,}건"
    first, last = row["first_year"], row["last_year"]
    if first and last:
        return f"{count} · {first}~{last}" if first != last else f"{count} · {first}"
    return count


def _doc_span(docs) -> str:
    """상세 머리의 규모 한 조각. 몇 건인지와 몇 년치인지를 함께 말한다."""
    years = sorted({d["eff_year"] for d in docs if d["eff_year"]})
    text = f"문서 {len(docs):,}건"
    if not years:
        return text
    period = str(years[0]) if years[0] == years[-1] else f"{years[0]}~{years[-1]}"
    return f"{text} · {period}"


def _when(row) -> str:
    if not row["eff_date"]:
        return "시점 미상"
    precision = row["eff_precision"] or "day"
    year, month, *_ = (row["eff_date"] or "").split("-") + ["", ""]
    if precision == "year":
        text = f"{year}년"
    elif precision == "month":
        text = f"{year}.{int(month):02d}"
    else:
        text = row["eff_date"]
    if row["eff_date_kind"] == "fs":
        text += " (파일 날짜)"
    return text


def _circled(index: int) -> str:
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    return circled[index - 1] if 1 <= index <= len(circled) else f"{index}."
