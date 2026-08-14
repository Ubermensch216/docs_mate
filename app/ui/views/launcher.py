"""시작 화면 — 어떤 인수인계를 열 것인가.

메뉴가 아니라 앱보다 앞에 서는 창이다. 지금까지는 프로젝트가 사실상 하나
(`--project default`)뿐이어서, 두 번째 인수인계를 받은 사람은 앱을 다시 쓸
방법이 없었다. 계획서 §22가 이 화면을 P0로 둔 이유다.

여는 방법은 셋이다 — 최근 목록에서 고르기 / 새로 시작하기 / 폴더에서 열기.
셋 다 한 화면에 함께 둔다. '새로 시작'을 다른 단계로 숨기면 처음 켠 사람이
빈 목록 앞에서 무엇을 눌러야 하는지 모른다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...db import registry
from ...db.registry import ProjectEntry
from .. import theme
from ..widgets import Badge, EmptyState, clear_layout, muted_label, view_title


class LauncherDialog(QDialog):
    """프로젝트를 하나 고르거나 만들어 돌려준다.

    고른 결과는 `chosen`에 담긴다. 아무것도 고르지 않고 닫으면 None —
    호출자(main.py)는 그 경우 앱을 그냥 끝낸다.
    """

    def __init__(self, root: Path | None = None, parent: QWidget | None = None,
                 current: Path | None = None):
        super().__init__(parent)
        self.root = root
        self.current = Path(current) if current else None
        self.chosen: ProjectEntry | None = None

        self.setWindowTitle("눈치코치 — 인수인계 선택")
        self.setMinimumSize(720, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_LG)
        outer.setSpacing(theme.SP_MD)

        outer.addWidget(view_title("어떤 인수인계를 여시겠습니까?"))
        outer.addWidget(
            muted_label(
                "인수인계 하나가 프로젝트 하나입니다. 부서를 옮기거나 다른 업무를 "
                "새로 받으면 새 인수인계를 만드세요 — 서로 섞이지 않습니다."
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(theme.SP_SM)
        self.list.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SP_SM)
        new = QPushButton("+ 새 인수인계 시작")
        new.setObjectName("Primary")
        new.clicked.connect(self._create)
        buttons.addWidget(new)

        existing = QPushButton("폴더에서 열기…")
        existing.setToolTip("USB나 공유 폴더에 있는 프로젝트 폴더를 직접 고릅니다")
        existing.clicked.connect(self._open_folder)
        buttons.addWidget(existing)
        buttons.addStretch(1)

        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        outer.addLayout(buttons)

        self.refresh()

    # ── 목록 ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        clear_layout(self.list)
        entries = registry.list_projects(self.root)
        if not entries:
            self.list.addWidget(
                EmptyState(
                    "아직 만든 인수인계가 없습니다",
                    "‘새 인수인계 시작’을 눌러 전임자 자료 폴더를 지정하면 됩니다.",
                )
            )
            return
        for entry in entries:
            self.list.addWidget(
                _ProjectRow(
                    entry,
                    is_current=self.current is not None and entry.path == self.current,
                    on_open=self._open,
                    on_rename=self._rename,
                    on_forget=self._forget,
                )
            )

    # ── 동작 ────────────────────────────────────────────────────────
    def _open(self, entry: ProjectEntry) -> None:
        if entry.external and not entry.exists:
            QMessageBox.warning(
                self,
                "열 수 없음",
                f"이 폴더에 닿을 수 없습니다.\n{entry.path}\n\n"
                "USB나 네트워크 폴더라면 연결한 뒤 다시 시도하세요.",
            )
            return
        registry.touch(entry.path, self.root)
        self.chosen = entry
        self.accept()

    def _create(self) -> None:
        name, ok = QInputDialog.getText(
            self, "새 인수인계", "이름을 붙여 주세요 (예: 2026년 총무팀 인수인계)"
        )
        if not ok:
            return
        self.chosen = registry.create_project(name, self.root)
        self.accept()

    def _open_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "프로젝트 폴더 선택")
        if not chosen:
            return
        folder = Path(chosen)
        if not (folder / registry.DB_NAME).exists():
            answer = QMessageBox.question(
                self,
                "새 인수인계",
                f"{folder}\n\n이 폴더에는 분석 결과가 없습니다. "
                "여기에 새 인수인계를 만들까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.chosen = registry.register(folder, root=self.root)
        self.accept()

    def _rename(self, entry: ProjectEntry) -> None:
        name, ok = QInputDialog.getText(
            self, "이름 바꾸기", "새 이름", text=entry.name
        )
        if not ok or not name.strip():
            return
        registry.rename(entry.path, name, self.root)
        self.refresh()

    def _forget(self, entry: ProjectEntry) -> None:
        answer = QMessageBox.question(
            self,
            "목록에서 빼기",
            f"{entry.name}\n{entry.path}\n\n"
            "목록에서만 뺍니다. 분석 결과 폴더와 원본 자료는 그대로 남습니다.\n"
            "다시 보려면 ‘폴더에서 열기’로 같은 폴더를 고르면 됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        registry.forget(entry.path, self.root)
        self.refresh()


class _ProjectRow(QFrame):
    """프로젝트 한 줄 — 이름 / 경로 / 마지막으로 연 날짜 + 조작.

    줄 전체를 두 번 눌러도 열린다. 목록에서 고르는 화면은 '열기' 버튼을
    찾게 하지 않는 편이 빠르다.
    """

    def __init__(self, entry: ProjectEntry, is_current: bool,
                 on_open, on_rename, on_forget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubPanel")
        self._entry = entry
        self._on_open = on_open

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_MD, theme.SP_MD, theme.SP_MD, theme.SP_MD)
        column.setSpacing(theme.SP_XS)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        name = QLabel(entry.name)
        name.setObjectName("SectionTitle")
        head.addWidget(name)
        if is_current:
            head.addWidget(Badge("지금 열려 있음", "ok"))
        elif entry.external:
            head.addWidget(Badge("다른 위치", "neutral"))
        if not entry.exists:
            head.addWidget(Badge("폴더 없음", "attention"))
        head.addStretch(1)
        column.addLayout(head)

        column.addWidget(_ElidedPath(str(entry.path)))

        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SP_SM)
        bottom.addWidget(
            muted_label(f"마지막으로 연 날짜  {entry.opened_text()}", small=True, wrap=False)
        )
        bottom.addStretch(1)

        rename = QPushButton("이름 바꾸기")
        rename.setObjectName("Quiet")
        rename.clicked.connect(lambda: on_rename(entry))
        bottom.addWidget(rename)

        forget = QPushButton("목록에서 빼기")
        forget.setObjectName("Quiet")
        forget.setToolTip("분석 결과는 지우지 않습니다")
        forget.clicked.connect(lambda: on_forget(entry))
        bottom.addWidget(forget)

        open_button = QPushButton("열기")
        open_button.setObjectName("Primary")
        open_button.setEnabled(entry.exists or not entry.external)
        open_button.clicked.connect(lambda: on_open(entry))
        bottom.addWidget(open_button)
        column.addLayout(bottom)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        self._on_open(self._entry)
        super().mouseDoubleClickEvent(event)


class _ElidedPath(QLabel):
    """긴 경로를 가운데를 접어서 보여준다.

    경로는 띄어쓰기가 없는 한 낱말이라 QLabel의 줄바꿈이 듣지 않는다. 그냥
    두면 줄 하나가 창보다 넓어져 오른쪽 버튼들이 화면 밖으로 밀린다
    (실제로 그렇게 나왔다). 접는 자리는 가운데다 — 드라이브와 폴더 이름이
    양 끝에 있고, 사용자가 프로젝트를 구별하는 단서도 그 양 끝이다.
    """

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Mono")
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        width = max(self.width(), 80)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width))
