"""애플리케이션 셸 — 상단 상태 바 + 4메뉴 사이드바 + 본문.

메뉴는 넷뿐이다. 각각 후임자의 질문 하나에 대응한다.
  업무  What + How   내 업무는 뭐고, 뭐부터 읽고, 어떻게 처리하나
  일정  When         이번 달엔 뭘 해야 하나
  문서  검증         이 파일이 뭐지, 최신본 맞나
  질문  자유 탐색    위 화면에 없는 걸 물어본다

분석 진행률은 메뉴가 아니라 상단 바에 상주한다. 상태는 화면이 아니다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..db import Database
from ..jobs import PipelineRunner
from . import theme
from .views.ask import AskView
from .views.calendar import CalendarView
from .views.documents import DocumentsView
from .views.onboarding import OnboardingView
from .views.tasks import TasksView

# 메뉴마다 그 메뉴가 답하는 질문을 함께 적는다. 이름만 있는 메뉴는
# 처음 쓰는 사람에게 "눌러 봐야 아는 것"이지만, 질문이 붙으면 누르기 전에
# 안다 — 인수인계 도구에서 첫 5분이 그 차이로 갈린다.
NAV = [
    ("tasks", "🗂", "업무", "내 업무는 무엇인가"),
    ("calendar", "📅", "일정", "언제 무엇을 하나"),
    ("documents", "📄", "문서", "이 파일이 최신본인가"),
    ("ask", "💬", "질문", "자료에 직접 물어본다"),
]


class TopBar(QWidget):
    """분석 진행 상황이 상주하는 자리. 누르면 단계별 상세·실패 목록으로 간다."""

    settings_requested = Signal()
    status_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.TOPBAR_H)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_LG, 0, theme.SP_LG, 0)
        row.setSpacing(theme.SP_MD)

        logo_path = Path(__file__).parent / "assets" / "logo.png"
        if logo_path.exists():
            mark = QLabel()
            mark.setPixmap(
                QPixmap(str(logo_path)).scaledToHeight(
                    theme.TOPBAR_H - 16, Qt.TransformationMode.SmoothTransformation
                )
            )
            row.addWidget(mark)

        name = QLabel("눈치코치")
        name.setObjectName("AppName")
        row.addWidget(name)
        row.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setObjectName("TopProgress")
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        row.addWidget(self.progress)

        self.status = QPushButton("자료원이 없습니다")
        self.status.setObjectName("Link")
        self.status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status.setToolTip("눌러서 단계별 진행과 실패 목록을 봅니다")
        self.status.clicked.connect(self.status_clicked.emit)
        row.addWidget(self.status)

        settings = QPushButton("⚙")
        settings.setObjectName("Link")
        settings.setFixedWidth(32)
        settings.setToolTip("설정")
        settings.clicked.connect(self.settings_requested.emit)
        row.addWidget(settings)

    def show_progress(self, done: int, total: int, note: str = "") -> None:
        if total <= 0:
            self.progress.hide()
            self.status.setText(note or "대기 중")
            return
        self.progress.show()
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        suffix = f" · {note}" if note else ""
        self.status.setText(f"{done:,} / {total:,} 분석 중{suffix}")

    def show_idle(self, text: str) -> None:
        self.progress.hide()
        self.status.setText(text)


def _nav_item(icon: str, label: str, question: str, shortcut: str) -> QPushButton:
    """메뉴 한 칸. 이름 아래에 그 메뉴가 답하는 질문을 함께 적는다.

    글자를 버튼 텍스트 하나로 넣으면 이름과 질문의 크기를 나눌 수 없어
    둘 다 같은 무게로 읽힌다. 그래서 라벨을 버튼 안에 넣고, 라벨은 마우스를
    통과시켜 버튼 어디를 눌러도 눌리게 한다.
    """
    button = QPushButton()
    button.setObjectName("NavItem")
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(f"{label} — {question}  ({shortcut})")

    row = QHBoxLayout(button)
    row.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_SM, theme.SP_SM)
    row.setSpacing(theme.SP_MD)

    mark = QLabel(icon)
    mark.setObjectName("NavIcon")
    mark.setFixedWidth(20)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row.addWidget(mark)

    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(0)
    title = QLabel(label)
    title.setObjectName("NavTitle")
    text.addWidget(title)
    hint = QLabel(question)
    hint.setObjectName("NavHint")
    text.addWidget(hint)
    row.addLayout(text)
    row.addStretch(1)

    for child in (mark, title, hint):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return button


class Sidebar(QWidget):
    navigated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(theme.SIDEBAR_W)
        # QWidget을 상속한 위젯은 이 속성이 없으면 스타일시트의 배경·테두리를
        # 그리지 않는다. 지금까지 사이드바가 흰 여백처럼 보이던 이유가 여기였다.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_SM, theme.SP_MD, theme.SP_SM, theme.SP_MD)
        column.setSpacing(theme.SP_XS)

        section = QLabel("어디를 볼까요")
        section.setObjectName("NavSection")
        column.addWidget(section)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for index, (key, icon, label, question) in enumerate(NAV, start=1):
            button = _nav_item(icon, label, question, f"Ctrl+{index}")
            button.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self.group.addButton(button)
            column.addWidget(button)
            self._buttons[key] = button

        column.addStretch(1)

        # 이 도구가 지키는 약속. 사이드바 바닥의 흐린 한 줄로 흘리면
        # 읽히지 않는다 — 원본을 건드리지 않는다는 것이 채택의 조건이다.
        promise = QFrame()
        promise.setObjectName("NavPromise")
        promise_box = QVBoxLayout(promise)
        promise_box.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        promise_box.setSpacing(2)
        head = QLabel("🔒 읽기 전용")
        head.setObjectName("NavPromiseHead")
        promise_box.addWidget(head)
        hint = QLabel("원본은 수정하지 않습니다")
        hint.setObjectName("NavPromiseText")
        hint.setWordWrap(True)
        promise_box.addWidget(hint)
        column.addWidget(promise)

    def select(self, key: str) -> None:
        button = self._buttons.get(key)
        if button:
            button.setChecked(True)

    def setEnabledNav(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle("눈치코치 — 업무 인수인계 도구")
        self.setMinimumSize(*theme.WINDOW_MIN)

        root = QWidget()
        root.setObjectName("Content")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.topbar = TopBar()
        self.topbar.settings_requested.connect(self._open_settings)
        self.topbar.status_clicked.connect(self._open_status)
        outer.addWidget(self.topbar)
        self.stage_reports: dict[str, object] = {}
        self._status_dialog = None

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigated.connect(self.go)
        split.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.views: dict[str, QWidget] = {
            "onboarding": OnboardingView(self.db),
            "tasks": TasksView(self.db),
            "calendar": CalendarView(self.db),
            "documents": DocumentsView(self.db),
            "ask": AskView(self.db),
        }
        for view in self.views.values():
            self.stack.addWidget(view)
        split.addWidget(self.stack, 1)
        outer.addLayout(split, 1)

        self.setCentralWidget(root)

        self.views["onboarding"].started.connect(self._on_scan_started)
        for key in ("tasks", "calendar", "ask"):
            view = self.views[key]
            if hasattr(view, "go_documents"):
                view.go_documents.connect(lambda: self.go("documents"))

        # When(일정)과 What(업무)은 서로 되돌아간다 — 일정에서 업무를 열고,
        # 업무 상세에서 일정으로 넘어간다.
        self.views["tasks"].go_calendar.connect(lambda: self.go("calendar"))
        self.views["calendar"].open_task.connect(self._open_task_from_calendar)

        # 처리는 백그라운드에서 돈다. UI는 완료를 기다리지 않고 이미 처리된
        # 결과부터 보여준다.
        self.runner = PipelineRunner(self.db.path, self)
        self.runner.progress.connect(self._on_progress)
        self.runner.stage_done.connect(self._on_stage_done)
        self.runner.finished.connect(self._on_pipeline_finished)
        self.runner.failed.connect(self._on_pipeline_failed)

        self._tick = QTimer(self)
        self._tick.setInterval(1500)
        self._tick.timeout.connect(self._refresh_current)

        self._install_shortcuts()
        self.refresh()
        self._resume_if_pending()

    # ── 내비게이션 ──────────────────────────────────────────────────
    def go(self, key: str) -> None:
        view = self.views.get(key)
        if view is None:
            return
        self.stack.setCurrentWidget(view)
        self.sidebar.select(key)
        if hasattr(view, "refresh"):
            view.refresh()

    def _install_shortcuts(self) -> None:
        for index, (key, _icon, _label, _question) in enumerate(NAV, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=key: self.go(k))

    # ── 상태 ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """자료원이 없으면 시작 마법사만 보여준다. 빈 메뉴를 누르게 하지 않는다."""
        has_source = bool(self.db.sources())
        self.sidebar.setEnabledNav(has_source)
        if not has_source:
            self.stack.setCurrentWidget(self.views["onboarding"])
            self.topbar.show_idle("자료원이 없습니다")
            return

        if not self.runner.running:
            self._show_idle_status()
        if self.stack.currentWidget() is self.views["onboarding"]:
            self.go("tasks")

    def _show_idle_status(self) -> None:
        counts = self.db.counts()
        parts = [f"파일 {counts['total']:,}건"]
        if counts["parse_failed"]:
            parts.append(f"읽지 못함 {counts['parse_failed']:,}")
        if counts["tasks"]:
            parts.append(f"업무 {counts['tasks']}개")
        self.topbar.show_idle(" · ".join(parts))

    # ── 백그라운드 처리 ─────────────────────────────────────────────
    def _resume_if_pending(self) -> None:
        """앱을 껐다 켜도 중단 지점부터 이어서 처리한다 (ING-006).

        남은 일은 별도 큐가 아니라 문서 상태로 판단하므로, 강제 종료 뒤에도
        같은 질의가 그대로 집어낸다.
        """
        if not self.db.sources():
            return
        pending = self.db.con.execute(
            "SELECT COUNT(*) AS n FROM documents d "
            "LEFT JOIN doc_embeddings e ON e.doc_id = d.id "
            "WHERE d.missing_since IS NULL AND d.parse_status != 'skipped' "
            "  AND (d.hash IS NULL OR d.parse_status = 'pending' "
            "       OR d.eff_date IS NULL "
            "       OR (d.parse_status IN ('ok','partial') AND e.doc_id IS NULL))"
        ).fetchone()["n"]
        counts = self.db.counts()
        # 문서 처리는 끝났는데 업무를 아직 못 찾았거나, 업무는 찾았는데
        # 주기를 아직 못 살핀 경우도 이어서 해야 한다.
        needs_discovery = counts["embedded"] > 0 and counts["in_task"] == 0
        needs_cycles = counts["tasks"] > 0 and self.db.get_meta("cycles_checked") is None
        needs_steps = counts["tasks"] > 0 and self.db.get_meta("steps_checked") is None
        # 질문 준비는 플래그가 아니라 실제 상태로 판단한다. 플래그만 보면
        # 한 번 세운 뒤에는 임베딩이 빠진 조각을 영영 다시 시도하지 않는다.
        # 위 pending 질의가 이미 쓰는 방식이고, 이쪽만 달랐다.
        needs_chunks = (
            counts["documents"] > 0
            and (self.db.get_meta("chunks_checked") is None
                 or self.db.unembedded_chunk_count() > 0)
        )
        if pending or needs_discovery or needs_cycles or needs_steps or needs_chunks:
            self._start_pipeline()

    def _start_pipeline(self) -> None:
        if self.runner.running:
            return
        self.runner.start()
        self._tick.start()
        self.topbar.show_progress(0, 1, "시작하는 중")

    def _on_scan_started(self) -> None:
        self.refresh()
        self._start_pipeline()

    def _on_progress(self, stage: str, done: int, total: int, note: str) -> None:
        if total:
            self.topbar.show_progress(done, total, stage)
        else:
            self.topbar.show_idle(f"{stage} · {done:,}건")

    def _on_pipeline_finished(self) -> None:
        self._tick.stop()
        self._show_idle_status()
        self._refresh_current()

    def _on_pipeline_failed(self, message: str) -> None:
        self._tick.stop()
        self.topbar.show_idle(f"⚠ 처리 중 오류: {message}")

    def _refresh_current(self) -> None:
        view = self.stack.currentWidget()
        if hasattr(view, "refresh"):
            view.refresh()

    def _open_status(self) -> None:
        from .status_dialog import StatusDialog

        dialog = StatusDialog(self, self)
        self._status_dialog = dialog
        # 열려 있는 동안 진행 상황이 계속 갱신되도록 tick에 물린다.
        self._tick.timeout.connect(dialog.refresh)
        try:
            dialog.exec()
        finally:
            self._tick.timeout.disconnect(dialog.refresh)
            self._status_dialog = None

    def _on_stage_done(self, report) -> None:
        self.stage_reports[report.stage] = report

    def _open_task_from_calendar(self, task_id: int) -> None:
        self.go("tasks")
        self.views["tasks"].open_task(task_id)

    def _open_settings(self) -> None:
        from .views.settings import SettingsDialog

        SettingsDialog(self.db, self).exec()

    def shutdown(self) -> None:
        """백그라운드 스레드를 안전하게 세운다.

        실행 중인 QThread가 파괴되면 프로세스가 죽는다. 창이 닫힐 때뿐 아니라
        테스트처럼 closeEvent 없이 정리되는 경로에서도 불러야 한다.
        """
        self._tick.stop()
        self.runner.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        self.shutdown()
        super().closeEvent(event)
