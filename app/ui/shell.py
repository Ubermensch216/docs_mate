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

from ..core import handover
from ..db import Database
from ..jobs import PipelineRunner
from . import icons, theme
from .views.ask import AskView
from .views.calendar import CalendarView
from .views.documents import DocumentsView
from .views.onboarding import OnboardingView
from .views.tasks import TasksView

# 메뉴마다 그 메뉴가 답하는 질문을 함께 적는다. 이름만 있는 메뉴는
# 처음 쓰는 사람에게 "눌러 봐야 아는 것"이지만, 질문이 붙으면 누르기 전에
# 안다 — 인수인계 도구에서 첫 5분이 그 차이로 갈린다.
NAV = [
    ("tasks", "tasks", "업무", "내 업무는 무엇인가"),
    ("calendar", "calendar", "일정", "언제 무엇을 하나"),
    ("documents", "documents", "문서", "이 파일이 최신본인가"),
    ("ask", "ask", "질문", "자료에 직접 물어본다"),
]

# 아이콘이 글자와 같은 색으로 움직여야 "선택된 한 덩어리"로 읽힌다.
# 이모지는 OS가 제 색으로 칠해 버려 이걸 할 수 없다 (icons.py 참고).
#
# 색을 여기에 담아 두지 않고 이름만 적는 이유: 테마를 바꾸면 theme의 색이
# 갈리는데, 값을 import 시점에 복사해 두면 이 표만 옛 색으로 남는다.
NAV_ICON_TOKENS = {
    "normal": "TEXT_MUTED",
    "checked": "PRIMARY",
    "disabled": "TEXT_DISABLED",
}
NAV_ICON_FALLBACK = {"tasks": "▤", "calendar": "▦", "documents": "▧", "ask": "▪"}


class TopBar(QWidget):
    """분석 진행 상황이 상주하는 자리. 누르면 단계별 상세·실패 목록으로 간다."""

    settings_requested = Signal()
    status_clicked = Signal()
    project_requested = Signal()

    def __init__(self, project_name: str = "", parent: QWidget | None = None):
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

        # 어떤 인수인계를 보고 있는지 항상 적어 둔다. 프로젝트가 여럿이 되면
        # 화면만 봐서는 구별되지 않는다 — 업무 목록도 문서 목록도 남의 것과
        # 똑같이 생겼다. 누르면 다른 인수인계로 갈아탄다.
        self.project = QPushButton(project_name or "프로젝트")
        self.project.setObjectName("Link")
        self.project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project.setToolTip("지금 열려 있는 인수인계 — 눌러서 다른 인수인계로 바꿉니다")
        self.project.clicked.connect(self.project_requested.emit)
        row.addWidget(self.project)
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


class NavItem(QPushButton):
    """메뉴 한 칸. 이름 아래에 그 메뉴가 답하는 질문을 함께 적는다.

    글자를 버튼 텍스트 하나로 넣으면 이름과 질문의 크기를 나눌 수 없어
    둘 다 같은 무게로 읽힌다. 그래서 라벨을 버튼 안에 넣고, 라벨은 마우스를
    통과시켜 버튼 어디를 눌러도 눌리게 한다.

    이름과 질문은 **다른 층위**다. 이름은 내가 가는 곳, 질문은 그곳이 답하는
    것 — 둘을 붙여 놓으면 두 줄짜리 한 문장으로 읽혀 위계가 사라진다.
    글자 크기·무게·색을 벌리고 줄 사이도 벌려 두 층으로 갈라 놓는다.
    """

    def __init__(self, icon: str, label: str, question: str, shortcut: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._icon = icon
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{label} — {question}  ({shortcut})")

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_MD, theme.SP_MD, theme.SP_SM, theme.SP_MD)
        row.setSpacing(theme.SP_MD)

        self.mark = QLabel()
        self.mark.setObjectName("NavIcon")
        self.mark.setFixedSize(22, 22)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(theme.SP_XS)   # 이름과 질문이 붙으면 한 덩어리로 읽힌다
        title = QLabel(label)
        title.setObjectName("NavTitle")
        text.addWidget(title)
        hint = QLabel(question)
        hint.setObjectName("NavHint")
        text.addWidget(hint)
        row.addLayout(text)
        row.addStretch(1)

        for child in (self.mark, title, hint):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.toggled.connect(lambda _checked: self._paint_icon())
        self._paint_icon()

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        """비활성으로 바뀔 때도 아이콘이 글자를 따라가야 한다."""
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self._paint_icon()

    def _paint_icon(self) -> None:
        state = (
            "disabled" if not self.isEnabled()
            else "checked" if self.isChecked()
            else "normal"
        )
        color = theme.color(NAV_ICON_TOKENS[state])
        art = icons.pixmap(self._icon, color, 20)
        if art is None:
            # QtSvg가 없는 환경. 이모지 대신 무채색 기호로 물러선다.
            self.mark.setText(NAV_ICON_FALLBACK.get(self._icon, "·"))
            self.mark.setStyleSheet(f"color: {color};")
            return
        self.mark.setPixmap(art)


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
        # 칸끼리 붙어 있으면 두 줄짜리 메뉴 넷이 여덟 줄 문단으로 보인다.
        column.setSpacing(theme.SP_SM)

        section = QLabel("어디를 볼까요")
        section.setObjectName("NavSection")
        column.addWidget(section)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for index, (key, icon, label, question) in enumerate(NAV, start=1):
            button = NavItem(icon, label, question, f"Ctrl+{index}")
            button.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self.group.addButton(button)
            column.addWidget(button)
            self._buttons[key] = button

        column.addStretch(1)

        # 인수인계 진행도는 메뉴 아래에 상주한다 (계획서 §18·§6). 어느 화면에
        # 있든 "얼마나 남았나"가 보여야, 확인 작업이 끝이 있는 일로 느껴진다.
        # 업무 홈에만 두면 다른 화면에서 교정하는 동안에는 사라진다.
        self.progress_box = QFrame()
        self.progress_box.setObjectName("NavProgress")
        progress_col = QVBoxLayout(self.progress_box)
        progress_col.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        progress_col.setSpacing(theme.SP_XS)
        self._progress_text = QLabel("인수인계 진행도")
        self._progress_text.setObjectName("NavProgressText")
        progress_col.addWidget(self._progress_text)
        self.handover = QProgressBar()
        self.handover.setObjectName("NavProgressBar")
        self.handover.setTextVisible(False)
        self.handover.setFixedHeight(6)
        self.handover.setRange(0, 100)
        progress_col.addWidget(self.handover)
        self._progress_hint = QLabel("")
        self._progress_hint.setObjectName("NavProgressHint")
        self._progress_hint.setWordWrap(True)
        progress_col.addWidget(self._progress_hint)
        self.progress_box.hide()
        column.addWidget(self.progress_box)

        # 이 도구가 지키는 약속. 사이드바 바닥의 흐린 한 줄로 흘리면
        # 읽히지 않는다 — 원본을 건드리지 않는다는 것이 채택의 조건이다.
        promise = QFrame()
        promise.setObjectName("NavPromise")
        promise_box = QVBoxLayout(promise)
        promise_box.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        promise_box.setSpacing(theme.SP_XS)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(theme.SP_XS)
        self._lock = QLabel()
        self._lock.setFixedSize(14, 14)
        head_row.addWidget(self._lock)
        head = QLabel("읽기 전용")
        head.setObjectName("NavPromiseHead")
        head_row.addWidget(head)
        head_row.addStretch(1)
        promise_box.addLayout(head_row)
        hint = QLabel("원본은 수정하지 않습니다")
        hint.setObjectName("NavPromiseText")
        hint.setWordWrap(True)
        promise_box.addWidget(hint)
        column.addWidget(promise)

        self.repaint_icons()

    def repaint_icons(self) -> None:
        """테마가 바뀌면 아이콘도 새 색으로 다시 그려야 한다.

        스타일시트는 글자만 갈아 준다. 아이콘은 그릴 때 색을 넣는 방식이라
        (icons.py), 다시 그리지 않으면 어두운 바탕에 어두운 선이 남는다.
        """
        lock_art = icons.pixmap("lock", theme.CONFIRMED, 14)
        if lock_art is None:
            self._lock.setText("▪")
            self._lock.setStyleSheet(f"color: {theme.CONFIRMED};")
        else:
            self._lock.setPixmap(lock_art)
        for button in self._buttons.values():
            button._paint_icon()

    def select(self, key: str) -> None:
        button = self._buttons.get(key)
        if button:
            button.setChecked(True)

    def show_handover(self, progress) -> None:
        """진행도를 갱신한다. 잴 것이 없으면 아예 숨긴다 —
        분석이 끝나기 전의 0%는 '아무것도 안 했다'는 잘못된 질책이다."""
        if not progress.measured:
            self.progress_box.hide()
            return
        self.progress_box.show()
        self.handover.setValue(progress.percent)
        self._progress_text.setText(progress.headline())
        nxt = progress.next_step()
        self._progress_hint.setText(nxt.sentence() if nxt else "확인할 것이 남지 않았습니다")

    def setEnabledNav(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)


class MainWindow(QMainWindow):
    # 다른 인수인계를 열어 달라 — 창을 새로 만드는 일은 main.py가 한다.
    switch_requested = Signal()

    def __init__(self, db: Database, project_name: str = ""):
        super().__init__()
        self.db = db
        self.project_name = project_name or db.get_meta("project_name") or ""
        title = "눈치코치 — 업무 인수인계 도구"
        self.setWindowTitle(f"{self.project_name} — {title}" if self.project_name else title)
        self.setMinimumSize(*theme.WINDOW_MIN)

        root = QWidget()
        root.setObjectName("Content")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.topbar = TopBar(self.project_name)
        self.topbar.settings_requested.connect(self._open_settings)
        self.topbar.status_clicked.connect(self._open_status)
        self.topbar.project_requested.connect(self.switch_requested.emit)
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
        # 업무 화면에서 확인·교정이 일어나면 사이드바 진행도가 그 자리에서 오른다.
        self.views["tasks"].state_changed.connect(self._sync_handover)
        self.views["calendar"].open_task.connect(self._open_task_from_calendar)
        # 답변의 근거 문서가 어느 업무의 것인지 눌러서 갈 수 있어야 한다.
        # 텍스트로만 적혀 있으면 "그래서 그 업무가 어디 있는데?"로 끝난다.
        self.views["ask"].open_task.connect(self._open_task_from_calendar)

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
        self._sync_handover()

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

        self._sync_handover()
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

    def _sync_handover(self) -> None:
        """사이드바 진행도를 지금 상태로 맞춘다.

        업무를 확인하거나 문서를 읽음 표시하면 그 자리에서 올라가야 한다.
        화면을 옮겨야만 갱신되면, 사용자는 자기가 한 일이 반영됐는지 확인하러
        메뉴를 한 번씩 눌러 보게 된다.
        """
        self.sidebar.show_handover(handover.summarize(self.db.handover_counts()))

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

        dialog = SettingsDialog(self.db, self)
        dialog.sources_changed.connect(self._on_sources_changed)
        dialog.exec()

    def _on_sources_changed(self) -> None:
        """자료 폴더를 더하거나 바꾸거나 뺐다 — 화면과 분석을 다시 맞춘다.

        폴더만 바꿔 놓고 분석을 그대로 두면, 사용자는 새 폴더를 지정했는데도
        옛 목록을 보며 "안 먹혔나" 하게 된다. 파이프라인은 남은 일을 문서
        상태로 찾으므로 그냥 다시 켜면 새 폴더부터 훑는다(ING-006).
        """
        self.refresh()
        if self.db.sources():
            self._start_pipeline()

    def restyle(self) -> None:
        """테마·글자 크기가 바뀐 뒤 창 전체를 새 색으로 다시 그린다.

        스타일시트만으로 끝나지 않는 것이 둘 있다. 아이콘은 그릴 때 색을
        넣고(icons.py), 일정 격자·업무 카드는 칸마다 색을 직접 칠한다. 둘 다
        다시 만들어야 하므로 사이드바는 아이콘을, 본문은 화면 전체를 다시
        조립한다.
        """
        self.sidebar.repaint_icons()
        # 지금 보이는 화면만 다시 짓는다. 나머지는 그 화면으로 갈 때
        # go()가 어차피 refresh를 부르므로 미리 만들 이유가 없다.
        self._refresh_current()
        self.update()

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
