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

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
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
    UnknownBlock,
    clear_layout,
    muted_label,
    open_original,
    view_title,
)
from .cycle_format import cycle_headline, guess_from_row, parse_months

# 이 안에 들어오면 '지금 챙길 일'로 올린다. 공직 업무는 한 달 전부터
# 준비 문서가 돌기 시작한다 — D-30을 넘겨 알려 주면 이미 늦다.
SOON_DAYS = 45
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


@dataclass(slots=True)
class Upcoming:
    """다가오는 반복 하나. 화면이 쓰기 좋은 모양으로 미리 계산해 둔다."""

    row: object
    when: date
    days_away: int
    running_now: bool

    @property
    def task_id(self) -> int:
        return self.row["task_id"]

    @property
    def name(self) -> str:
        return self.row["task_name"]


class TabBar(QWidget):
    """화면 안 탭. 사이드바 메뉴와 헷갈리지 않도록 밑줄 형태로 만든다."""

    switched = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TabBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_XS)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for key, label in TABS:
            button = QPushButton(label)
            button.setObjectName("Tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.switched.emit(k))
            self._group.addButton(button)
            row.addWidget(button)
            self._buttons[key] = button
        row.addStretch(1)

    def set_count(self, key: str, label: str, count: int | None) -> None:
        """탭 이름에 건수를 단다. 누르기 전에 규모를 알 수 있어야 한다."""
        button = self._buttons[key]
        button.setText(label if not count else f"{label}  {count}")

    def select(self, key: str) -> None:
        self._buttons[key].setChecked(True)


class Page(QWidget):
    """탭 하나의 내용. 각자 스크롤한다 — 탭을 바꿔도 남의 스크롤이 따라오지 않는다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("Content")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body.setObjectName("Content")
        body.setAutoFillBackground(True)
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(0, theme.SP_LG, theme.SP_LG, theme.SP_XL)
        self.column.setSpacing(theme.SP_MD)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def reset(self) -> QVBoxLayout:
        clear_layout(self.column)
        return self.column


class CalendarView(QWidget):
    go_documents = Signal()
    open_task = Signal(int)

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._tab = YEAR

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, 0)
        outer.setSpacing(theme.SP_MD)

        # 정체성 문구는 탭 위에 상주한다. 어느 탭에 있든 "이건 내가 입력한
        # 달력이 아니다"라는 사실이 화면에서 사라지면 안 된다.
        outer.addWidget(view_title("자료에서 발견한 업무 일정"))
        self.lead = muted_label(
            "전임자 자료에 남은 시기를 읽어 추정한 일정입니다. "
            "직접 입력한 달력이 아니므로, 확인하고 고칠 수 있습니다."
        )
        outer.addWidget(self.lead)

        self.tabs = TabBar()
        self.tabs.switched.connect(self._switch)
        outer.addWidget(self.tabs)

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

        entries = _upcoming(cycles, today)
        now = [e for e in entries if _is_now(e)]
        later = [e for e in entries if not _is_now(e)]
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
            muted_label(
                f"{today.year}년 · 가로 한 줄이 업무 하나입니다. 이번 달은 진하게 "
                f"표시됩니다. 업무 이름을 누르면 상세로 갑니다."
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.SP_XS)
        grid.setVerticalSpacing(theme.SP_XS)

        for index, label in enumerate(MONTH_NAMES):
            head = QLabel(label)
            head.setObjectName("Small")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if index + 1 == today.month:
                head.setStyleSheet(f"color: {theme.PRIMARY}; font-weight: 700;")
            grid.addWidget(head, 0, index + 1)

        for r, row in enumerate(cycles, start=1):
            # 격자가 읽기만 하는 그림이면 여기서 뭔가 이상해도 확인하러 갈 길이 없다.
            name = QPushButton(row["task_name"])
            name.setObjectName("Link")
            name.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
            name.setToolTip(f"{cycle_headline(row)} · {_observed(row)}")
            grid.addWidget(name, r, 0)

            guess = guess_from_row(row)
            state = status.of_cycle(row)
            for month in range(1, 13):
                grid.addWidget(
                    _cell(guess.applies_to_month(month), month == today.month, state),
                    r, month,
                )

        wrapper = QWidget()
        wrapper.setLayout(grid)
        wrapper.setMaximumWidth(theme.CONTENT_MAX_W)
        column.addWidget(wrapper)
        column.addWidget(muted_label(_legend(), small=True))

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
            muted_label(f"앞으로 {SOON_DAYS}일 안에 시작되거나 지금 진행 중인 업무입니다.")
        )
        for entry in now:
            column.addWidget(self._now_card(entry, today))

    def _now_card(self, entry: Upcoming, today: date) -> QWidget:
        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(_urgency(entry))

        name = QPushButton(entry.name)
        name.setObjectName("Link")
        name.setStyleSheet(f"font-size: {theme.FS_SECTION}px; font-weight: 600;")
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

        card.body.addWidget(muted_label("그때 남긴 문서", small=True))
        for doc in docs:
            row = QHBoxLayout()
            row.setSpacing(theme.SP_SM)
            row.addWidget(muted_label(f"{doc['eff_year']}년", small=True, wrap=False))
            link = QPushButton(f"📄 {doc['filename']}")
            link.setObjectName("Link")
            link.setToolTip(f"{doc['path']}\n클릭하면 원본을 엽니다")
            link.clicked.connect(lambda _=False, p=doc["path"]: self._open(p))
            row.addWidget(link)
            row.addStretch(1)
            card.body.addLayout(row)

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

        column.addWidget(muted_label("가까운 순서입니다. 업무 이름을 누르면 상세로 갑니다."))
        for entry in later[:MAX_UPCOMING]:
            row = QHBoxLayout()
            row.setSpacing(theme.SP_MD)

            when = muted_label(_when_label(entry, today), wrap=False)
            when.setFixedWidth(96)
            row.addWidget(when)

            name = QPushButton(entry.name)
            name.setObjectName("Link")
            name.clicked.connect(lambda _=False, t=entry.task_id: self.open_task.emit(t))
            row.addWidget(name)

            row.addWidget(muted_label(cycle_headline(entry.row), small=True, wrap=False))
            row.addStretch(1)
            row.addWidget(Badge.state(status.of_cycle(entry.row)))
            column.addLayout(row)

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
            muted_label(
                "맞다고 확인해 두면 다시 분석해도 바뀌지 않고, 다음 담당자에게 "
                "그대로 전달됩니다. 근거가 약한 것부터 보여 줍니다."
            )
        )

        for row in pending[:MAX_REVIEW]:
            line = QHBoxLayout()
            line.setSpacing(theme.SP_SM)
            line.addWidget(Badge.state(status.of_cycle(row)))

            name = QPushButton(row["task_name"])
            name.setObjectName("Link")
            name.clicked.connect(lambda _=False, t=row["task_id"]: self.open_task.emit(t))
            line.addWidget(name)

            line.addWidget(muted_label(cycle_headline(row), small=True, wrap=False))
            line.addWidget(muted_label(f"· {_review_reason(row)}", small=True, wrap=False))
            line.addStretch(1)

            for label, primary, slot in (
                ("맞습니다", True, lambda _=False, t=row["task_id"]: self._confirm(t)),
                ("수정", False, lambda _=False, t=row["task_id"]: self._edit(t)),
                ("반복 아님", False, lambda _=False, t=row["task_id"],
                                    n=row["task_name"]: self._not_recurring(t, n)),
            ):
                button = QPushButton(label)
                button.setObjectName("" if primary else "Link")
                button.clicked.connect(slot)
                line.addWidget(button)

            column.addLayout(line)

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

def _upcoming(cycles: list, today: date) -> list[Upcoming]:
    """모든 주기를 '다음에 언제 오는가' 순으로 편다.

    매월 반복은 다음 시점이 따로 없다 — 지금이 곧 그때다. 그래서 항상
    '진행 중'으로 맨 앞에 둔다.
    """
    entries: list[Upcoming] = []
    for row in cycles:
        guess = guess_from_row(row)
        if guess.kind == "monthly":
            entries.append(Upcoming(row=row, when=today, days_away=0, running_now=True))
            continue

        when = guess.next_occurrence(today)
        if when is None:
            continue
        running = guess.applies_to_month(today.month)
        days = 0 if running else (when - today).days
        entries.append(
            Upcoming(row=row, when=today if running else when,
                     days_away=days, running_now=running)
        )

    entries.sort(key=lambda e: (not e.running_now, e.days_away, e.name))
    return entries


def _is_now(entry: Upcoming) -> bool:
    """'지금 챙길 일'인가. 두 탭이 같은 기준을 쓰도록 한 곳에서 판단한다."""
    return entry.running_now or entry.days_away <= SOON_DAYS


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


def _cell(filled: bool, is_current: bool, state: str) -> QLabel:
    """격자 한 칸. 확인된 주기는 진하게, 추정은 옅게 — 색만으로 구분하지 않는다."""
    if not filled:
        text, color = "·", theme.TEXT_DISABLED
    elif state == status.CONFIRMED:
        text, color = "●", theme.PRIMARY
    elif state == status.INFERRED:
        text, color = "●", theme.TEXT_MUTED
    else:
        text, color = "○", theme.TEXT_MUTED

    cell = QLabel(text)
    cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    cell.setFixedWidth(28)
    weight = "700" if (filled and is_current) else "400"
    cell.setStyleSheet(f"color: {color}; font-weight: {weight};")
    return cell
