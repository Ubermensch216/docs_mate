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
from . import icons, stages, theme
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

# 가로 띠에서는 메뉴가 글자만으로 선다. 아이콘을 함께 넣으면 넷이 한 줄에
# 늘어서면서 폭을 두 배로 먹고, 정작 오른쪽 상태 문구가 밀려난다.
# 아이콘 체계(icons.py)는 설정 창처럼 세로 목록이 있는 곳에서 계속 쓴다.


class TopBar(QWidget):
    """검은 띠 하나에 길과 상태를 모두 담는다 (디자인 개선안 1c).

    왼쪽부터 제품·프로젝트 → 메뉴 넷 → 오른쪽에 지금 상태. 세로 사이드바를
    쓰던 것을 가로 띠로 바꾼 이유는 본문이다 — 질문 화면이 '답변 | 근거 원문'
    두 칸을 쓰는데 왼쪽에 216px 메뉴까지 서면 원문 칸이 대조하기 어려운 폭이
    된다. 메뉴는 하루에 몇 번 누르고 본문은 내내 읽는다.

    띠를 어둡게 두는 것은 색을 늘리는 것이 아니라 **길과 내용을 가르는** 일이다.
    본문은 그대로 무채색 종이로 남는다.
    """

    settings_requested = Signal()
    status_clicked = Signal()
    project_requested = Signal()
    navigated = Signal(str)
    handover_clicked = Signal()

    def __init__(self, project_name: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.TOPBAR_H)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_LG, 0, theme.SP_LG, 0)
        row.setSpacing(theme.SP_SM)

        logo_path = Path(__file__).parent / "assets" / "logo.png"
        if logo_path.exists():
            mark = QLabel()
            mark.setPixmap(
                QPixmap(str(logo_path)).scaledToHeight(
                    theme.TOPBAR_H - 22, Qt.TransformationMode.SmoothTransformation
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
        self.project.setObjectName("TopLink")
        self.project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project.setToolTip("지금 열려 있는 인수인계 — 눌러서 다른 인수인계로 바꿉니다")
        self.project.clicked.connect(self.project_requested.emit)
        row.addWidget(self.project)

        row.addWidget(_bar_divider())

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for index, (key, _icon, label, question) in enumerate(NAV, start=1):
            button = QPushButton(label)
            button.setObjectName("NavTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # 메뉴 이름 아래 적던 질문은 띠에서는 들어갈 자리가 없다. 버리지
            # 않고 툴팁으로 옮긴다 — 처음 쓰는 사람에게 이 넷이 각각 무엇에
            # 답하는지가 이 제품의 뼈대다.
            button.setToolTip(f"{label} — {question}  (Ctrl+{index})")
            button.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self.group.addButton(button)
            row.addWidget(button)
            self._buttons[key] = button

        row.addStretch(1)

        # 인수인계 진행도. 사이드바가 없어진 자리를 대신한다 — 어느 화면에
        # 있든 "얼마나 남았나"가 보여야 확인 작업이 끝이 있는 일로 느껴진다.
        self.handover = QPushButton("")
        self.handover.setObjectName("TopHandover")
        self.handover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.handover.clicked.connect(self.handover_clicked.emit)
        self.handover.hide()
        row.addWidget(self.handover)

        self.progress = QProgressBar()
        self.progress.setObjectName("TopProgress")
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.hide()
        row.addWidget(self.progress)

        # 이 도구가 지키는 약속. 상태 문구 앞에 붙여 늘 함께 보이게 한다 —
        # 원본을 건드리지 않는다는 것이 이 제품 채택의 조건이다.
        self.promise = QLabel("읽기 전용")
        self.promise.setObjectName("TopPromise")
        self.promise.setToolTip("원본을 수정·이동·삭제·이름 변경하지 않습니다")
        row.addWidget(self.promise)

        self.status = QPushButton("자료원이 없습니다")
        self.status.setObjectName("TopLink")
        self.status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status.setToolTip("눌러서 단계별 진행과 실패 목록을 봅니다")
        self.status.clicked.connect(self.status_clicked.emit)
        row.addWidget(self.status)

        settings = QPushButton("⚙")
        settings.setObjectName("TopLink")
        settings.setFixedWidth(30)
        settings.setToolTip("설정")
        settings.clicked.connect(self.settings_requested.emit)
        row.addWidget(settings)

    # ── 길 ──────────────────────────────────────────────────────────
    def select(self, key: str) -> None:
        button = self._buttons.get(key)
        if button:
            button.setChecked(True)

    def setEnabledNav(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)

    # ── 상태 ────────────────────────────────────────────────────────
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

    def show_handover(self, progress) -> None:
        """진행도를 갱신한다. 잴 것이 없으면 아예 숨긴다 —
        분석이 끝나기 전의 0%는 '아무것도 안 했다'는 잘못된 질책이다."""
        if not progress.measured:
            self.handover.hide()
            return
        self.handover.show()
        self.handover.setText(f"인수인계 {progress.percent}%")
        lines = [area.sentence() for area in progress.measured]
        nxt = progress.next_step()
        if nxt is not None:
            lines.append(f"다음에 확인할 것 · {nxt.sentence()}")
        self.handover.setToolTip(chr(10).join(lines))


def _bar_divider() -> QFrame:
    line = QFrame()
    line.setObjectName("TopDivider")
    line.setFixedWidth(1)
    line.setFixedHeight(16)
    return line


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
        self.topbar.navigated.connect(self.go)
        # 진행도를 누르면 확인할 것이 있는 자리로 데려간다. 숫자만 보여 주고
        # 어디로 가야 하는지 말하지 않으면 그 숫자는 채근일 뿐이다.
        self.topbar.handover_clicked.connect(lambda: self.go("tasks"))
        outer.addWidget(self.topbar)
        self.stage_reports: dict[str, object] = {}
        self._status_dialog = None

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
        outer.addWidget(self.stack, 1)

        self.setCentralWidget(root)

        self.views["onboarding"].started.connect(self._on_scan_started)
        for key in ("tasks", "calendar", "ask"):
            view = self.views[key]
            if hasattr(view, "go_documents"):
                view.go_documents.connect(lambda: self.go("documents"))

        # When(일정)과 What(업무)은 서로 되돌아간다 — 일정에서 업무를 열고,
        # 업무 상세에서 일정으로 넘어간다.
        self.views["tasks"].go_calendar.connect(lambda: self.go("calendar"))
        # 업무 화면에서 확인·교정이 일어나면 상단 띠의 진행도가 그 자리에서 오른다.
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
        self.topbar.select(key)
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
        self.topbar.setEnabledNav(has_source)
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
        from ..ai.settings import model_names
        from ..ingest.parsers.base import PARSER_VERSION
        embed_model = model_names(self.db)[1]
        pending = self.db.con.execute(
            "SELECT COUNT(*) AS n FROM documents d "
            "LEFT JOIN doc_embeddings e ON e.doc_id = d.id AND e.model = ? "
            "WHERE d.missing_since IS NULL AND d.parse_status != 'skipped' "
            "  AND (d.hash IS NULL OR d.parse_status = 'pending' "
            "       OR d.eff_date IS NULL "
            "       OR (d.parse_status IN ('ok','partial') AND "
            "           (e.doc_id IS NULL OR COALESCE(d.parser_version,'') != ?)))",
            (embed_model, PARSER_VERSION),
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
                 or self.db.unembedded_chunk_count(embed_model) > 0)
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
        # 상시 화면에는 일감 이름이 아니라 지금 무엇이 일어나는지를 적는다
        # (계획서 §24). 기술 이름은 상태 상세에만 둔다.
        if total:
            self.topbar.show_progress(done, total, stages.running(stage))
        else:
            self.topbar.show_idle(f"{stages.running(stage)} · {done:,}건")

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
        """상단 띠의 진행도를 지금 상태로 맞춘다.

        업무를 확인하거나 문서를 읽음 표시하면 그 자리에서 올라가야 한다.
        화면을 옮겨야만 갱신되면, 사용자는 자기가 한 일이 반영됐는지 확인하러
        메뉴를 한 번씩 눌러 보게 된다.
        """
        self.topbar.show_handover(handover.summarize(self.db.handover_counts()))

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
        dialog.models_changed.connect(self._on_sources_changed)
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

        스타일시트만으로 끝나지 않는 것이 있다. 일정 격자·업무 카드는 칸마다
        색을 직접 칠하므로 화면을 다시 조립해야 한다.
        """
        # 지금 보이는 화면만 다시 짓는다. 나머지는 그 화면으로 갈 때
        # go()가 어차피 refresh를 부르므로 미리 만들 이유가 없다.
        self._refresh_current()
        self.update()

    def shutdown(self) -> bool:
        """백그라운드 스레드를 안전하게 세운다.

        실행 중인 QThread가 파괴되면 프로세스가 죽는다. 창이 닫힐 때뿐 아니라
        테스트처럼 closeEvent 없이 정리되는 경로에서도 불러야 한다.
        """
        self._tick.stop()
        runners = [self.runner]
        for view in self.views.values():
            for name in ("_runner", "_summaries", "_warmup"):
                runner = getattr(view, name, None)
                if runner is not None:
                    runners.append(runner)
        for runner in runners:
            runner.stop()
        return not any(runner.running for runner in runners)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        if not self.shutdown():
            event.ignore()
            self.topbar.show_idle("진행 중인 작업을 마친 뒤 안전하게 닫습니다…")
            QTimer.singleShot(250, self.close)
            return
        super().closeEvent(event)
