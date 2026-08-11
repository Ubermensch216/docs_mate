"""애플리케이션 셸 — 상단 상태 바 + 4메뉴 사이드바 + 본문.

메뉴는 넷뿐이다. 각각 후임자의 질문 하나에 대응한다.
  업무  What + How   내 업무는 뭐고, 뭐부터 읽고, 어떻게 처리하나
  일정  When         이번 달엔 뭘 해야 하나
  문서  검증         이 파일이 뭐지, 최신본 맞나
  질문  자유 탐색    위 화면에 없는 걸 물어본다

분석 진행률은 메뉴가 아니라 상단 바에 상주한다. 상태는 화면이 아니다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
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

NAV = [
    ("tasks", "🗂", "업무"),
    ("calendar", "📅", "일정"),
    ("documents", "📄", "문서"),
    ("ask", "💬", "질문"),
]


class TopBar(QWidget):
    """분석 진행 상황이 상주하는 자리. 클릭하면 단계별 상세로 간다."""

    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.TOPBAR_H)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_LG, 0, theme.SP_LG, 0)
        row.setSpacing(theme.SP_MD)

        name = QLabel("업무기억관")
        name.setObjectName("AppName")
        row.addWidget(name)
        row.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setObjectName("TopProgress")
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        row.addWidget(self.progress)

        self.status = QLabel("자료원이 없습니다")
        self.status.setObjectName("StatusText")
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


class Sidebar(QWidget):
    navigated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(theme.SIDEBAR_W)

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_SM, theme.SP_MD, theme.SP_SM, theme.SP_MD)
        column.setSpacing(theme.SP_XS)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for key, icon, label in NAV:
            button = QPushButton(f"  {icon}   {label}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self.group.addButton(button)
            column.addWidget(button)
            self._buttons[key] = button

        column.addStretch(1)

        hint = QLabel("원본은 수정하지 않습니다")
        hint.setObjectName("Small")
        hint.setWordWrap(True)
        hint.setContentsMargins(theme.SP_MD, 0, theme.SP_MD, 0)
        column.addWidget(hint)

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
        self.setWindowTitle("업무기억관 — 업무 인수인계 도구")
        self.setMinimumSize(*theme.WINDOW_MIN)

        root = QWidget()
        root.setObjectName("Content")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.topbar = TopBar()
        self.topbar.settings_requested.connect(self._open_settings)
        outer.addWidget(self.topbar)

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

        # 처리는 백그라운드에서 돈다. UI는 완료를 기다리지 않고 이미 처리된
        # 결과부터 보여준다.
        self.runner = PipelineRunner(self.db.path, self)
        self.runner.progress.connect(self._on_progress)
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
        for index, (key, _icon, _label) in enumerate(NAV, start=1):
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
        needs_cycles = counts["tasks"] > 0 and counts["cycles_found"] == 0
        if pending or needs_discovery or needs_cycles:
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
