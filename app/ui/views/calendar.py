"""일정 화면 — When. 전 업무 통합.

이 화면은 달력이 아니다. 사용자가 일정을 입력하지 않고, 시스템이 전임자
자료에서 반복 시기를 **발견**한다. 그래서 제목부터 "자료에서 발견한 업무
일정"이며, 자료가 한 해치뿐이면 반복을 주장하지 않는다.

네 개의 탭으로 나눈다.

  연간 패턴      1년 전체 그림은 어떻게 생겼나?   ← 첫 화면
  지금 챙길 일   지금 당장 손대야 하는 게 있나?
  앞으로 올 일   몇 달 뒤엔 뭐가 오나?
  확인 필요      이 예측을 믿어도 되나?

탭으로 나눈 이유는 스크롤을 줄이려는 것만이 아니다. 탭 이름에 건수를 달면
사용자가 **누르기 전에** 규모를 안다 — "지금 챙길 일 2", "확인 필요 5"가
그 자체로 정보다. 한 줄로 이어 붙였을 때는 끝까지 스크롤해야 알 수 있었다.

'지금 챙길 일' 탭의 핵심은 **"작년 이맘때 무슨 문서가 있었나"**다.
"9월에 행정사무감사가 있습니다"는 달력도 할 수 있는 말이지만, "작년 9월엔
이 공문으로 시작했습니다"는 전임자 자료를 읽은 시스템만 할 수 있다.
날짜를 업무기억으로 바꾸는 지점이 거기다.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import status
from ...db import Database
from .. import theme
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    InfoDot,
    ListRow,
    Page,
    SubPanel,
    TabBar,
    UnknownBlock,
    hint_row,
    muted_label,
    open_original,
    view_title,
)
from .cycle_format import (
    SOON_DAYS,
    Upcoming,
    cycle_headline,
    guess_from_row,
    is_now,
    parse_months,
    upcoming,
)

MONTH_NAMES = [f"{m}월" for m in range(1, 13)]
MAX_UPCOMING = 8
MAX_REVIEW = 12
EVIDENCE_PER_ITEM = 2

YEAR, NOW, LATER, REVIEW = "year", "now", "later", "review"
TABS = (
    (YEAR, "연간 패턴"),
    (NOW, "지금 챙길 일"),
    (LATER, "앞으로 올 일"),
    (REVIEW, "확인 필요"),
)


class CalendarView(QWidget):
    go_documents = Signal()
    open_task = Signal(int)

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._tab = YEAR
        self.setObjectName("Canvas")
        self.setAutoFillBackground(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 제목·설명·탭은 흰 띠 하나에 묶는다. 본문 바탕과 색이 갈리면
        # 탭이 "본문 위에 떠 있는 글자"가 아니라 "머리에 붙은 손잡이"로
        # 읽힌다 — 탭을 못 알아보던 문제의 절반이 이 경계였다.
        header = QWidget()
        header.setObjectName("ViewHeader")
        header.setAutoFillBackground(True)
        head_column = QVBoxLayout(header)
        head_column.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, 0)
        head_column.setSpacing(theme.SP_SM)

        # 여기만 ⓘ로 접지 않는다. "직접 입력한 달력이 아니다"는 화면 설명이
        # 아니라 이 제품의 정체성이고, 어느 탭에 있든 보여야 한다(계획서 §12).
        # 접히면 사용자는 자기가 만든 일정표로 오해한 채로 쓰게 된다.
        # 대신 한 줄로 줄이고, 나머지 사연은 ⓘ로 넘긴다.
        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP_SM)
        title_row.addWidget(view_title("자료에서 발견한 업무 일정"))
        title_row.addWidget(
            InfoDot(
                "전임자 자료에 남은 시기를 읽어 추정한 일정입니다. 달마다 어떤 "
                "문서가 있었는지를 세로로 겹쳐 보고 반복을 찾습니다. 확인하고 "
                "고칠 수 있으며, 확인한 것은 다시 분석해도 바뀌지 않습니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        title_row.addStretch(1)
        head_column.addLayout(title_row)

        self.lead = muted_label("직접 입력한 달력이 아니라, 자료에서 읽어 낸 추정입니다.")
        head_column.addWidget(self.lead)

        self.tabs = TabBar(TABS)
        self.tabs.switched.connect(self._switch)
        head_column.addSpacing(theme.SP_SM)
        head_column.addWidget(self.tabs)
        outer.addWidget(header)

        self.stack = QStackedWidget()
        self.pages: dict[str, Page] = {}
        for key, _label in TABS:
            page = Page()
            self.pages[key] = page
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)

        # 주기가 하나도 없을 때는 탭이 의미가 없다. 그때만 쓰는 자리.
        self.blank = Page()
        outer.addWidget(self.blank, 1)

        self.refresh()

    # ── 진입점 ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        """분석 중에는 1.5초마다 불린다. 보고 있던 탭을 절대 빼앗지 않는다."""
        today = date.today()
        cycles = self.db.all_cycles()

        if not cycles:
            self.tabs.setVisible(False)
            self.stack.setVisible(False)
            self.blank.setVisible(True)
            self._render_empty(self.blank.reset(), today)
            return

        self.tabs.setVisible(True)
        self.stack.setVisible(True)
        self.blank.setVisible(False)

        entries = upcoming(cycles, today)
        now = [e for e in entries if is_now(e)]
        later = [e for e in entries if not is_now(e)]
        pending = [c for c in cycles if status.of_cycle(c) != status.CONFIRMED]

        self._render_year(self.pages[YEAR].reset(), cycles, today)
        self._render_now(self.pages[NOW].reset(), now, today)
        self._render_later(self.pages[LATER].reset(), later, today)
        self._render_review(self.pages[REVIEW].reset(), pending)

        self.tabs.set_count(YEAR, "연간 패턴", len(cycles))
        self.tabs.set_count(NOW, "지금 챙길 일", len(now))
        self.tabs.set_count(LATER, "앞으로 올 일", len(later))
        self.tabs.set_count(REVIEW, "확인 필요", len(pending))

        self._switch(self._tab)

    def _switch(self, key: str) -> None:
        self._tab = key if key in self.pages else YEAR
        self.stack.setCurrentWidget(self.pages[self._tab])
        self.tabs.select(self._tab)

    # ── 자료 부족 ───────────────────────────────────────────────────
    def _render_empty(self, column: QVBoxLayout, today: date) -> None:
        years = self.db.con.execute(
            "SELECT COUNT(DISTINCT eff_year) AS n FROM documents WHERE eff_year IS NOT NULL"
        ).fetchone()["n"]
        tasks = self.db.counts()["tasks"]

        if tasks == 0:
            column.addWidget(
                EmptyState(
                    "아직 업무를 파악하지 못했습니다",
                    "업무를 먼저 나눠야 그 업무의 반복 시기를 찾을 수 있습니다.",
                    "문서 보기",
                    self.go_documents.emit,
                )
            )
            return

        if years <= 1:
            column.addWidget(
                UnknownBlock(
                    f"반복 여부를 판단할 자료가 부족합니다. 확인된 연도가 {years}개뿐입니다. "
                    "반복 일정은 최소 2개 연도가 관측되어야 제시합니다."
                )
            )
        column.addWidget(
            EmptyState(
                "반복 업무를 아직 찾지 못했습니다",
                f"업무 {tasks}개를 확인했지만 뚜렷하게 반복되는 시기를 찾지 못했습니다. "
                "근거 없이 추측하지 않습니다. 업무 화면에서 주기를 직접 지정할 수 있습니다.",
                "업무 보기",
                self.go_documents.emit,
            )
        )

    # ── 연간 패턴 ──────────────────────────────────────────────────
    def _render_year(self, column: QVBoxLayout, cycles: list, today: date) -> None:
        column.addWidget(
            hint_row(
                f"{today.year}년",
                f"가로 한 줄이 업무 하나입니다. 막대가 그 업무를 하는 달이고, "
                f"이번 달({today.month}월)은 세로로 표시됩니다. "
                f"업무 이름을 누르면 상세로 갑니다.",
                strong=True,
            )
        )

        panel = QFrame()
        panel.setObjectName("GridPanel")
        panel.setFixedWidth(_grid_width())
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(_grid_header(today.month))

        for index, row in enumerate(cycles):
            stack.addWidget(
                self._grid_row(row, today.month, index, last=index == len(cycles) - 1)
            )

        column.addWidget(panel, alignment=Qt.AlignmentFlag.AlignLeft)
        column.addWidget(muted_label(_legend(), small=True))

    def _grid_row(self, row, current_month: int, index: int, last: bool = False) -> QFrame:
        """격자 한 줄. 연속된 달은 하나의 막대로 이어 붙인다.

        점 열두 개를 늘어놓으면 '9,10,11월'이 세 개의 점이지 한 덩어리의
        기간으로 보이지 않는다. 이어 붙여야 한 해의 모양이 읽힌다.
        """
        line = QFrame()
        line.setObjectName(
            ("GridRow" if index % 2 == 0 else "GridRowAlt") + ("Last" if last else "")
        )
        line.setFixedHeight(theme.GRID_ROW_H)
        cells = QHBoxLayout(line)
        cells.setContentsMargins(theme.SP_MD, 0, theme.SP_MD, 0)
        cells.setSpacing(0)

        # 격자가 읽기만 하는 그림이면 여기서 뭔가 이상해도 확인하러 갈 길이 없다.
        name = QPushButton(_elide(row["task_name"], theme.GRID_NAME_W - theme.SP_SM))
        name.setObjectName("GridName")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.setFixedWidth(theme.GRID_NAME_W)
        name.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
        name.setToolTip(f"{row['task_name']}\n{cycle_headline(row)} · {_observed(row)}")
        cells.addWidget(name)

        guess = guess_from_row(row)
        months = [guess.applies_to_month(m) for m in range(1, 13)]
        state = status.of_cycle(row)
        for month in range(1, 13):
            cells.addWidget(_slot(months, month, current_month, state))
        return line

    # ── 지금 챙길 일 ───────────────────────────────────────────────
    def _render_now(self, column: QVBoxLayout, now: list[Upcoming], today: date) -> None:
        if not now:
            column.addWidget(
                EmptyState(
                    f"{SOON_DAYS}일 안에 시작되는 업무가 없습니다",
                    "[앞으로 올 일] 탭에서 올해 남은 일정을 볼 수 있습니다.",
                )
            )
            return

        column.addWidget(
            hint_row(
                f"{len(now)}건",
                f"앞으로 {SOON_DAYS}일 안에 시작되거나 지금 진행 중인 업무입니다.",
            )
        )
        for entry in now:
            column.addWidget(self._now_card(entry, today))

    def _now_card(self, entry: Upcoming, today: date) -> QWidget:
        # 급한 것은 왼쪽 띠로 먼저 말한다. 카드가 여러 장 쌓이면 뱃지 하나는
        # 훑는 눈에 걸리지 않는다.
        urgent = entry.running_now or entry.days_away <= 7
        card = Card(tone="attention" if urgent else "primary")
        card.setMaximumWidth(theme.CONTENT_MAX_W)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(_urgency(entry))

        name = QPushButton(entry.name)
        name.setObjectName("CardTitle")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.setToolTip("눌러서 업무 상세로 갑니다")
        name.clicked.connect(lambda _=False, t=entry.task_id: self.open_task.emit(t))
        head.addWidget(name)
        head.addStretch(1)
        head.addWidget(Badge.state(status.of_cycle(entry.row)))
        card.body.addLayout(head)

        card.body.addWidget(
            muted_label(f"{cycle_headline(entry.row)} · {_observed(entry.row)}", small=True)
        )
        self._add_last_time(card, entry)
        return card

    def _add_last_time(self, card: Card, entry: Upcoming) -> None:
        """작년 이맘때 — 이 화면이 달력과 갈라지는 지점.

        먼저 처리 단계를 찾고(무엇부터 했나), 그다음 그 달의 실제 문서를
        보여준다(무엇을 보면 되나). 둘 다 없으면 없다고 말한다.
        """
        month = entry.when.month
        step = self.db.task_step_for_month(entry.task_id, month)
        docs = self.db.task_documents_in_month(
            entry.task_id, month, limit=EVIDENCE_PER_ITEM
        )

        if step is None and not docs:
            card.body.addWidget(
                UnknownBlock(f"{month}월에 무엇부터 했는지는 자료에서 확인되지 않았습니다.")
            )
            return

        if step is not None:
            card.body.addWidget(
                muted_label(f"{step['year']}년에는 '{step['label']}'부터 시작했습니다")
            )
        if not docs:
            return

        # 근거는 옅은 판에 묶는다. 카드 본문과 같은 흰 바탕에 흘려 두면
        # 어디까지가 '작년에 실제로 있던 문서'인지 경계가 사라진다.
        panel = SubPanel()
        panel.body.addWidget(muted_label("그때 남긴 문서", small=True))
        for doc in docs:
            row = QHBoxLayout()
            row.setSpacing(theme.SP_SM)
            row.addWidget(muted_label(f"{doc['eff_year']}년", small=True, wrap=False))
            link = QPushButton(f"📄 {doc['filename']}")
            link.setObjectName("Link")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setToolTip(f"{doc['path']}\n클릭하면 원본을 엽니다")
            link.clicked.connect(lambda _=False, p=doc["path"]: self._open(p))
            row.addWidget(link)
            row.addStretch(1)
            panel.body.addLayout(row)
        card.body.addWidget(panel)

    # ── 앞으로 올 일 ───────────────────────────────────────────────
    def _render_later(self, column: QVBoxLayout, later: list[Upcoming], today: date) -> None:
        if not later:
            column.addWidget(
                EmptyState(
                    "더 뒤에 예정된 반복 업무가 없습니다",
                    "찾은 반복 업무가 모두 곧 시작됩니다. [지금 챙길 일] 탭을 보세요.",
                )
            )
            return

        column.addWidget(
            hint_row("가까운 순서", "업무 이름을 누르면 그 업무의 상세로 갑니다.")
        )
        for entry in later[:MAX_UPCOMING]:
            line = ListRow()
            line.setMaximumWidth(theme.CONTENT_MAX_W)

            when = QLabel(_when_label(entry, today))
            when.setObjectName("WhenChip")
            when.setAlignment(Qt.AlignmentFlag.AlignCenter)
            when.setFixedWidth(80)
            line.row.addWidget(when)

            name = QPushButton(entry.name)
            name.setObjectName("RowTitle")
            name.setCursor(Qt.CursorShape.PointingHandCursor)
            name.clicked.connect(lambda _=False, t=entry.task_id: self.open_task.emit(t))
            line.row.addWidget(name)

            line.row.addWidget(
                muted_label(cycle_headline(entry.row), small=True, wrap=False)
            )
            line.row.addStretch(1)
            line.row.addWidget(Badge.state(status.of_cycle(entry.row)))
            column.addWidget(line)

        if len(later) > MAX_UPCOMING:
            column.addWidget(
                muted_label(f"… 외 {len(later) - MAX_UPCOMING}개", small=True)
            )

    # ── 확인 필요 ──────────────────────────────────────────────────
    def _render_review(self, column: QVBoxLayout, pending: list) -> None:
        """확인을 한자리에서 끝내는 곳.

        처음 열면 모든 주기가 미확인이므로 카드로 늘어놓으면 화면이 카드로만
        가득 찬다. 한 줄씩 좁게 쌓고, 근거가 약한 것(WEAK)을 위로 올린다 —
        판단이 실제로 필요한 건 그쪽이다.
        """
        if not pending:
            column.addWidget(
                EmptyState(
                    "모든 반복 주기를 확인했습니다",
                    "이 일정은 다시 분석해도 바뀌지 않고, 다음 담당자에게 "
                    "그대로 전달됩니다.",
                )
            )
            return

        pending = sorted(
            pending,
            key=lambda c: (status.of_cycle(c) != status.WEAK, c["years_observed"]),
        )
        column.addWidget(
            hint_row(
                "근거가 약한 것부터",
                "맞다고 확인해 두면 다시 분석해도 바뀌지 않고, 다음 담당자에게 "
                "그대로 전달됩니다.",
            )
        )

        for row in pending[:MAX_REVIEW]:
            line = ListRow()
            line.setMaximumWidth(theme.CONTENT_MAX_W)
            line.row.setSpacing(theme.SP_SM)
            line.row.addWidget(Badge.state(status.of_cycle(row)))

            name = QPushButton(row["task_name"])
            name.setObjectName("RowTitle")
            name.setCursor(Qt.CursorShape.PointingHandCursor)
            name.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
            line.row.addWidget(name)

            line.row.addWidget(muted_label(cycle_headline(row), small=True, wrap=False))
            line.row.addWidget(
                muted_label(f"· {_review_reason(row)}", small=True, wrap=False)
            )
            line.row.addStretch(1)

            # 세 동작의 무게가 다르다는 것이 모양에서 보여야 한다 — 확인은
            # 테두리 있는 강조, 나머지는 조용한 버튼.
            for label, style, slot in (
                ("맞습니다", "Confirm", lambda _=False, t=row["task_id"]: self._confirm(t)),
                ("수정", "Quiet", lambda _=False, t=row["task_id"]: self._edit(t)),
                ("반복 아님", "Quiet", lambda _=False, t=row["task_id"],
                                      n=row["task_name"]: self._not_recurring(t, n)),
            ):
                button = QPushButton(label)
                button.setObjectName(style)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(slot)
                line.row.addWidget(button)

            column.addWidget(line)

        if len(pending) > MAX_REVIEW:
            column.addWidget(
                muted_label(
                    f"… 외 {len(pending) - MAX_REVIEW}개는 업무 화면에서 확인할 수 있습니다",
                    small=True,
                )
            )

    # ── 교정 ────────────────────────────────────────────────────────
    # 주기가 틀렸다는 것을 사용자가 가장 먼저 알아채는 자리가 이 화면이다.
    # 여기서 업무 상세까지 찾아가게 하면 대부분 고치지 않고 넘어간다.

    def _confirm(self, task_id: int) -> None:
        self.db.confirm_task_cycle(task_id)
        self.refresh()

    def _edit(self, task_id: int) -> None:
        cycle = self.db.task_cycle(task_id)
        current = ",".join(str(m) for m in parse_months(cycle["months"])) if cycle else ""
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

    def _not_recurring(self, task_id: int, name: str) -> None:
        answer = QMessageBox.question(
            self, "반복 아님",
            f"'{name}'은(는) 반복 업무가 아닌가요?\n\n"
            f"일정에서 내려가고, 다시 분석해도 되살아나지 않습니다.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.db.mark_no_cycle(task_id)
            self.refresh()

    def _open(self, path: str) -> None:
        open_original(self, self.db, path)


# ── 계산 ────────────────────────────────────────────────────────────

def _urgency(entry: Upcoming) -> QLabel:
    """언제인지를 한눈에. 숫자보다 말이 먼저 읽힌다."""
    if entry.running_now:
        return Badge("진행 중", "attention")
    if entry.days_away <= 7:
        return Badge(f"{entry.days_away}일 뒤", "attention")
    return Badge(f"{entry.when.month}월 · {entry.days_away}일 뒤", "neutral")


def _when_label(entry: Upcoming, today: date) -> str:
    if entry.when.year != today.year:
        return f"내년 {entry.when.month}월"
    return f"{entry.when.month}월"


def _observed(row) -> str:
    """'4개 연도에서 확인'. 신뢰도라는 말보다 관측 사실이 더 정직하다."""
    years = row["years_observed"]
    if not years:
        return "담당자가 직접 지정"
    return f"{years}개 연도에서 확인"


def _review_reason(row) -> str:
    if status.of_cycle(row) == status.WEAK:
        return f"{row['years_observed']}개 연도뿐 — 우연일 수 있습니다"
    return _observed(row)


def _legend() -> str:
    """격자 범례. status.label()은 이미 기호+문구라 여기서는 기호만 쓴다."""
    return "   ".join(
        f"{status.symbol(state)} {text}"
        for state, text in (
            (status.CONFIRMED, "담당자가 확인함"),
            (status.INFERRED, "자료에서 추정"),
            (status.WEAK, "자료가 부족"),
        )
    )


def _elide(text: str, width: int) -> str:
    """이름이 칸을 넘치면 잘라 준다. 전체 이름은 툴팁에 남는다."""
    metrics = QFontMetrics(QLabel().font())
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)


def _grid_width() -> int:
    return theme.GRID_NAME_W + theme.GRID_CELL_W * 12 + theme.SP_MD * 2


def _grid_header(current_month: int) -> QFrame:
    """월 머리줄. 이번 달만 칠해 세로 기준선을 만든다."""
    head = QFrame()
    head.setObjectName("GridHeadRow")
    head.setFixedHeight(theme.GRID_ROW_H)
    row = QHBoxLayout(head)
    row.setContentsMargins(theme.SP_MD, 0, theme.SP_MD, 0)
    row.setSpacing(0)

    corner = QLabel("업무")
    corner.setObjectName("GridHeadCell")
    corner.setFixedWidth(theme.GRID_NAME_W)
    row.addWidget(corner)

    for index, label in enumerate(MONTH_NAMES, start=1):
        cell = QLabel(label)
        now = index == current_month
        cell.setObjectName("GridHeadCellNow" if now else "GridHeadCell")
        cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cell.setFixedWidth(theme.GRID_CELL_W)
        if now:
            cell.setFixedHeight(theme.GRID_ROW_H - theme.SP_SM)
            row.addWidget(cell, 0, Qt.AlignmentFlag.AlignBottom)
            continue
        row.addWidget(cell)
    return head


# 막대 색. 상태를 색만으로 구분하지 않도록 막대 안에 기호를 함께 찍는다.
# 값이 아니라 색 이름을 담는다 — 테마를 바꿔도 이 표만 옛 색으로 남지 않도록
# theme.color()로 꺼내 쓴다.
_BAR = {
    status.CONFIRMED: ("CYCLE_STRONG", "TEXT_ON_PRIMARY"),
    status.INFERRED: ("CYCLE_SOFT", "TEXT"),
    status.WEAK: ("CYCLE_WEAK", "TEXT_MUTED"),
}


def _slot(months: list[bool], month: int, current_month: int, state: str) -> QWidget:
    """격자 한 칸.

    칸(slot)은 줄 높이를 다 쓰고, 그 안의 막대만 낮다. 그래야 이번 달 세로
    띠는 끊기지 않으면서 가로 막대는 행마다 떨어져 보인다.
    """
    slot = QWidget()
    slot.setFixedWidth(theme.GRID_CELL_W)
    slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    if month == current_month:
        slot.setAutoFillBackground(True)
        slot.setStyleSheet(f"background: {theme.PRIMARY_SOFT};")

    box = QVBoxLayout(slot)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(_bar(months, month, state), 0, Qt.AlignmentFlag.AlignVCenter)
    return slot


def _bar(months: list[bool], month: int, state: str) -> QLabel:
    """막대 한 조각. 연속 구간의 양 끝만 둥글려 하나의 기간으로 보이게 한다."""
    filled = months[month - 1]
    bar = QLabel()
    bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    bar.setFixedHeight(theme.GRID_BAR_H)
    bar.setFixedWidth(theme.GRID_CELL_W)

    if not filled:
        bar.setText("·")
        bar.setStyleSheet(f"color: {theme.TEXT_DISABLED}; background: transparent;")
        return bar

    fill_token, ink_token = _BAR.get(state, _BAR[status.WEAK])
    fill, ink = theme.color(fill_token), theme.color(ink_token)
    starts = month == 1 or not months[month - 2]
    ends = month == 12 or not months[month]
    radius = theme.GRID_BAR_H // 2
    left = radius if starts else 0
    right = radius if ends else 0

    # 구간의 첫 칸에만 기호를 찍는다. 칸마다 반복하면 막대가 글자밭이 된다.
    bar.setText(status.symbol(state) if starts else "")
    bar.setStyleSheet(
        f"background: {fill}; color: {ink}; font-weight: 700;"
        f"border-top-left-radius: {left}px; border-bottom-left-radius: {left}px;"
        f"border-top-right-radius: {right}px; border-bottom-right-radius: {right}px;"
    )
    return bar
