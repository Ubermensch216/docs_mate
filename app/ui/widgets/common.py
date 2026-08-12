"""공통 위젯 — 신뢰 표현 3종과 레이아웃 조각."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import status
from .. import theme

DETAIL_WIDTH = 520   # 빈 상태 설명문 폭. 한글은 한 줄이 너무 길면 읽기 어렵다.


def open_original(parent: QWidget, db, path: str) -> None:
    """원본을 사용자의 기본 프로그램으로 연다. 읽기만 하고 고치지 않는다.

    모든 화면이 같은 방식으로 열어야 한다 — 감사 기록도 한 곳에서 남긴다.
    원본이 사라졌을 때 조용히 실패하면 사용자는 프로그램이 멈춘 줄 안다.
    """
    from PySide6.QtWidgets import QMessageBox

    if not Path(path).exists():
        QMessageBox.information(
            parent, "원본 열기",
            f"원본을 찾을 수 없습니다:\n{path}\n\n"
            f"옮겨졌거나 지워졌을 수 있습니다. 분석 기록은 그대로 남아 있습니다.",
        )
        return
    db.audit("document.open", path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — 사용자가 명시적으로 연 원본
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def clear_layout(layout) -> None:
    """레이아웃을 즉시 비운다.

    deleteLater()만 쓰면 이벤트 루프가 돌기 전까지 옛 위젯이 살아 있어 새
    내용과 겹쳐 그려진다. setParent(None)으로 화면에서 먼저 떼어낸다.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)
            child.deleteLater()


def view_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("ViewTitle")
    return label


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def body_label(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    return label


def muted_label(text: str, small: bool = False, wrap: bool = True) -> QLabel:
    """wrap=False는 줄바꿈되면 어색한 짧은 표시(뱃지 옆 문구 등)에 쓴다."""
    label = QLabel(text)
    label.setObjectName("Small" if small else "Muted")
    label.setWordWrap(wrap)
    if not wrap:
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def note_label(text: str) -> QLabel:
    """화면 설명 문구. 목록 항목과 같은 회색 글씨로 흘리면 내용과 섞여 읽힌다.

    옅은 판 위에 얹어 "이건 안내지 자료가 아니다"를 배경으로 먼저 말한다.
    """
    label = QLabel(text)
    label.setObjectName("PageNote")
    label.setWordWrap(True)
    label.setMaximumWidth(theme.CONTENT_MAX_W)
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


class Badge(QLabel):
    """상태 뱃지. 색만으로 구분하지 않도록 항상 기호와 글자를 함께 쓴다."""

    def __init__(self, text: str, kind: str = "neutral", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName(f"Badge{kind.capitalize()}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    @classmethod
    def state(cls, state: str, parent: QWidget | None = None) -> "Badge":
        """4단계 신뢰 상태 뱃지. 문구·기호·색은 core/status.py가 정한다.

        화면이 raw confidence를 직접 해석하지 않게 하는 것이 요점이다 —
        같은 뜻이 화면마다 다르게 보이는 것을 막는다 (계획서 §9).
        """
        badge = cls(status.label(state), parent=parent)
        badge.setObjectName(status.badge_style(state))
        return badge

    @classmethod
    def confidence(cls, level: str, parent: QWidget | None = None) -> "Badge":
        """AI confidence만 가진 옛 호출부용. 내부적으로 4단계로 접는다."""
        return cls.state(status.from_confidence(level), parent=parent)


class Card(QFrame):
    """카드 컨테이너. 내용은 body 레이아웃에 채운다.

    tone="attention"/"primary"면 왼쪽에 굵은 띠가 붙는다. 뱃지 하나보다
    멀리서 보이므로, 훑어볼 때 급한 카드가 먼저 눈에 걸린다.
    """

    def __init__(self, parent: QWidget | None = None, tone: str = ""):
        super().__init__(parent)
        self.setObjectName(f"Card{tone.capitalize()}" if tone else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.SP_LG, theme.SP_LG, theme.SP_LG, theme.SP_LG)
        self.body.setSpacing(theme.SP_MD)


class SubPanel(QFrame):
    """카드 안에서 근거를 묶는 옅은 판. 어디까지가 근거인지 경계를 만든다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubPanel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        self.body.setSpacing(theme.SP_XS)


class ListRow(QFrame):
    """목록 한 줄. 카드보다 가볍지만 테두리로 '한 건'이라는 덩어리를 만든다.

    줄만 늘어놓으면 어디서 한 건이 끝나는지 보이지 않아, 옆에 붙은 뱃지가
    어느 줄 것인지도 헷갈린다. 내용은 row 레이아웃에 채운다.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ListRow")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        self.row.setSpacing(theme.SP_MD)


class EvidenceChip(QPushButton):
    """근거 문서 칩. 모든 AI 주장 옆에 붙는다.

    클릭하면 원본으로 간다. 근거로 돌아갈 수 없는 주장은 화면에 올리지 않는
    것이 원칙이므로, 이 위젯이 붙지 않는 AI 결과는 설계 오류다.
    """

    opened = Signal(int)

    def __init__(self, doc_id: int, label: str, locator: str = "",
                 parent: QWidget | None = None):
        text = f"📄 {label}" + (f" · {locator}" if locator else "")
        super().__init__(text, parent)
        self.doc_id = doc_id
        self.setObjectName("Link")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{label}\n{locator}\n클릭하면 원본을 엽니다")
        self.clicked.connect(lambda: self.opened.emit(self.doc_id))


class UnknownBlock(QFrame):
    """자료에서 확인되지 않은 구간을 명시적으로 그린다.

    빈칸으로 두면 사용자는 시스템이 놓쳤는지 자료가 없는지 구분할 수 없다.
    없는 것을 지어내지 않는다는 원칙을 화면에서 실행하는 위젯이다.
    """

    def __init__(self, message: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("UnknownBlock")
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        self.label = QLabel()
        self.label.setObjectName("UnknownText")
        self.label.setWordWrap(True)
        row.addWidget(self.label)
        self.setMessage(message)

    def setMessage(self, message: str) -> None:
        self.label.setText(f"⚠ {message}" if message else "")


class EmptyState(QWidget):
    """빈 상태. 무엇이 없는지와 다음에 할 일을 함께 보여준다."""

    def __init__(
        self,
        title: str,
        detail: str = "",
        action: str = "",
        on_action: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, theme.SP_XL * 2, 0, 0)
        column.setSpacing(theme.SP_MD)
        column.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(heading)

        if detail:
            text = QLabel(detail)
            text.setObjectName("Muted")
            text.setWordWrap(True)
            text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            # 폭을 고정한 뒤 높이를 직접 계산해 넣는다. word wrap 라벨의 sizeHint는
            # 부모 레이아웃이 heightForWidth를 물어보지 않으면 한 줄 높이로 잡혀
            # 둘째 줄부터 잘린다.
            text.setFixedWidth(DETAIL_WIDTH)
            text.setMinimumHeight(text.heightForWidth(DETAIL_WIDTH))
            column.addWidget(text)

        if action and on_action:
            button = QPushButton(action)
            button.setObjectName("Primary")
            button.clicked.connect(on_action)
            column.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
