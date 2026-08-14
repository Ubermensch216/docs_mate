"""시작 화면 — 첫 실행 1회. 메뉴가 아니다.

세 단계로 끝낸다: 폴더 선택 → 확인 → 분석 시작.
읽기 전용 원칙을 여기서 명시적으로 알린다. 공직 자료를 다루는 프로그램에
대한 첫 신뢰가 이 문장에서 만들어진다.
"""

from __future__ import annotations

import stat
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from ...ingest.parsers import supported_extensions
from .. import theme
from ..widgets import Card, body_label, muted_label, view_title

# 사전 점검에서 훑을 최대 항목 수. 네트워크 공유 폴더에서 멈추지 않도록 상한을 둔다.
PRECHECK_CAP = 40_000
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "node_modules", ".git"}


class OnboardingView(QWidget):
    started = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._folders: list[Path] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_XL * 2, theme.SP_XL, theme.SP_XL * 2, theme.SP_XL)
        outer.setSpacing(theme.SP_LG)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        outer.addWidget(view_title("전임자 자료가 있는 폴더를 선택하세요"))
        outer.addWidget(
            muted_label(
                "로컬 폴더, USB, 네트워크 공유 폴더를 함께 등록할 수 있습니다. "
                "여러 곳에 흩어져 있어도 하나로 묶어 살펴봅니다."
            )
        )

        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)

        self.list = QListWidget()
        self.list.setFixedHeight(140)
        self.list.setAlternatingRowColors(True)
        card.body.addWidget(self.list)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SP_SM)
        add = QPushButton("+ 폴더 추가")
        add.clicked.connect(self._add_folder)
        buttons.addWidget(add)

        self.remove = QPushButton("선택 제거")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self._remove_selected)
        buttons.addWidget(self.remove)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self.summary = muted_label("아직 선택한 폴더가 없습니다.")
        card.body.addWidget(self.summary)

        notice = body_label(
            "ⓘ 이 프로그램은 원본을 수정·이동·삭제·이름 변경하지 않습니다. "
            "읽기만 하고, 분석 결과는 따로 저장합니다."
        )
        notice.setObjectName("Muted")
        card.body.addWidget(notice)

        outer.addWidget(card)

        self.start = QPushButton("분석 시작")
        self.start.setObjectName("Primary")
        self.start.setEnabled(False)
        self.start.clicked.connect(self._start)
        outer.addWidget(self.start, alignment=Qt.AlignmentFlag.AlignLeft)

        self.list.itemSelectionChanged.connect(
            lambda: self.remove.setEnabled(bool(self.list.selectedItems()))
        )

    # ── 폴더 ────────────────────────────────────────────────────────
    def _add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "전임자 자료 폴더 선택")
        if not chosen:
            return
        path = Path(chosen)
        if path in self._folders:
            return
        self._folders.append(path)
        self.list.addItem(QListWidgetItem(str(path)))
        self._precheck()

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            row = self.list.row(item)
            self.list.takeItem(row)
            del self._folders[row]
        self._precheck()

    def _precheck(self) -> None:
        """읽기 권한과 대략적인 규모를 실행 전에 보여준다 (PRJ-004)."""
        if not self._folders:
            self.summary.setText("아직 선택한 폴더가 없습니다.")
            self.start.setEnabled(False)
            return

        supported = set(supported_extensions())
        files = 0
        target = 0
        size = 0
        blocked = 0                 # 읽을 수 없던 **파일** 수 (§23)
        denied: list[str] = []      # 아예 들어가지 못한 폴더
        capped = False

        for folder in self._folders:
            try:
                for path in folder.rglob("*"):
                    if files >= PRECHECK_CAP:
                        capped = True
                        break
                    if any(part in SKIP_DIRS for part in path.parts):
                        continue
                    # is_file()로 먼저 거르지 않는다 — 권한이 없으면 그 함수는
                    # 오류 대신 False를 돌려주어, 못 읽는 파일이 '없는 파일'과
                    # 구별되지 않는다. 사용자에게는 그 둘이 전혀 다르다.
                    try:
                        info = path.stat()
                    except OSError:
                        blocked += 1
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    files += 1
                    size += info.st_size
                    if path.suffix.lower() in supported:
                        target += 1
            except (PermissionError, OSError) as exc:
                denied.append(f"{folder} ({exc.strerror or exc})")

        prefix = "약 " if capped else ""
        text = (
            f"{prefix}파일 {files:,}개 · {_human(size)} · "
            f"분석 대상 문서 {target:,}건 · 접근 불가 {blocked:,}개"
        )
        if capped:
            text += f"  (사전 점검은 {PRECHECK_CAP:,}건까지만 훑습니다)"
        if denied:
            text += "\n⚠ 열 수 없는 폴더: " + ", ".join(denied)
        if blocked:
            text += (
                f"\nⓘ 파일 {blocked:,}개는 권한이나 잠금 때문에 지금 읽을 수 없습니다. "
                "나머지는 그대로 분석합니다."
            )
        if target == 0 and not denied:
            text += "\n⚠ 분석할 수 있는 문서를 찾지 못했습니다."

        self.summary.setText(text)
        self.start.setEnabled(target > 0)

    def _start(self) -> None:
        for folder in self._folders:
            self.db.add_source(folder, kind=_source_kind(folder))
        self.db.audit("scan.request", detail=f"{len(self._folders)}개 자료원")
        self.started.emit()

    def refresh(self) -> None:
        pass


def _source_kind(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\"):
        return "unc"
    return "local"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.1f}{unit}" if unit != "B" else f"{int(value):,}B"
        value /= 1024
    return f"{value:,.1f}TB"
