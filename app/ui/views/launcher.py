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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...db import registry
from ...db.registry import ProjectEntry
from .. import theme
from ..widgets import (
    Badge,
    ElidedLabel,
    EmptyState,
    clear_layout,
    muted_label,
    view_title,
)


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
                    on_delete=self._delete,
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

    def _delete(self, entry: ProjectEntry) -> None:
        """분석 결과를 실제로 지운다 (계획서 §30-3).

        무엇이 사라지고 무엇이 남는지를 먼저 말한다. 이 도구에서 '지우기'가
        무서운 이유는 사용자가 **원본이 지워지는 것**을 걱정하기 때문이다 —
        그 걱정을 먼저 풀어 주지 않으면 지우지도, 믿지도 못한다.
        """
        files = registry.project_files(entry.path)
        answer = QMessageBox.warning(
            self,
            "분석 결과 완전 삭제",
            f"{entry.name}\n{entry.path}\n\n"
            f"이 인수인계의 분석 결과 파일 {len(files)}개를 지웁니다. "
            "직접 고친 내용·확인 표시·질문 기록이 함께 사라지며 되돌릴 수 없습니다.\n\n"
            "전임자 원본 자료는 지우지 않습니다. 이 프로그램은 원본을 읽기만 합니다.\n\n"
            "계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = registry.delete_project(entry.path, self.root)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "지우지 못했습니다",
                f"{exc.strerror or exc}\n\n다른 창에서 이 인수인계를 열어 두었다면 "
                "닫고 다시 시도하세요.",
            )
            return
        self.refresh()
        QMessageBox.information(
            self, "삭제 완료", f"파일 {len(removed)}개를 지웠습니다. 원본 자료는 그대로입니다."
        )


class _ProjectRow(QFrame):
    """프로젝트 한 줄 — 이름 / 경로 / 마지막으로 연 날짜 + 조작.

    줄 전체를 두 번 눌러도 열린다. 목록에서 고르는 화면은 '열기' 버튼을
    찾게 하지 않는 편이 빠르다.
    """

    def __init__(self, entry: ProjectEntry, is_current: bool,
                 on_open, on_rename, on_forget, on_delete,
                 parent: QWidget | None = None):
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

        where = ElidedLabel(str(entry.path))
        where.setObjectName("Mono")
        column.addWidget(where)

        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SP_SM)
        bottom.addWidget(
            muted_label(f"마지막으로 연 날짜  {entry.opened_text()}", small=True, wrap=False)
        )
        bottom.addStretch(1)

        # 손대는 것 셋은 ⋯ 안에 넣는다. 확인 대화상자가 막아 주기는 하지만,
        # **'완전 삭제'가 '열기' 바로 옆에 서 있는 것 자체가 위험**하다 —
        # 목록에서 프로젝트를 여는 동작은 빠르게 반복되고, 그 속도로 누르는
        # 손은 한 칸 옆을 짚는다. 되돌릴 수 없는 것은 한 번 더 열게 한다.
        more = QPushButton("⋯")
        more.setObjectName("IconButton")
        more.setFixedWidth(26)
        more.setToolTip("이름 바꾸기 · 목록에서 빼기 · 완전 삭제")
        menu = QMenu(more)          # 메뉴의 부모는 언제나 그 메뉴를 여는 버튼이다
        menu.addAction("이름 바꾸기", lambda: on_rename(entry))
        forget = menu.addAction("목록에서 빼기", lambda: on_forget(entry))
        forget.setToolTip("분석 결과는 지우지 않습니다")
        menu.addSeparator()
        # 빼기와 지우기를 같은 무게로 두지 않는다. 하나는 되돌릴 수 있고
        # 하나는 아니다 — 이름이 그 차이를 먼저 말해야 한다.
        delete = menu.addAction("완전 삭제", lambda: on_delete(entry))
        delete.setToolTip("분석 결과를 지웁니다. 원본 자료는 건드리지 않습니다.")
        delete.setEnabled(not is_current)
        if is_current:
            delete.setToolTip("지금 열려 있는 인수인계는 지울 수 없습니다")
        more.setMenu(menu)
        self.menu = menu            # 시험이 메뉴 항목을 찾을 수 있게 남긴다
        bottom.addWidget(more)

        open_button = QPushButton("열기")
        open_button.setObjectName("Primary")
        open_button.setEnabled(entry.exists or not entry.external)
        open_button.clicked.connect(lambda: on_open(entry))
        bottom.addWidget(open_button)
        column.addLayout(bottom)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        self._on_open(self._entry)
        super().mouseDoubleClickEvent(event)
