"""공통 위젯 — 신뢰 표현 3종과 레이아웃 조각."""

from __future__ import annotations

from collections.abc import Callable

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

from .. import theme

DETAIL_WIDTH = 520   # 빈 상태 설명문 폭. 한글은 한 줄이 너무 길면 읽기 어렵다.

# 신뢰 수준 — 보정되지 않은 숫자 확률 대신 4단계를 쓴다 (doc/00 §7.1).
CONFIDENCE_LABELS = {
    "high": ("● 높음", "BadgeOk"),
    "medium": ("◐ 보통", "BadgeNeutral"),
    "low": ("○ 낮음", "BadgeAttention"),
    "unknown": ("? 판정 불가", "BadgeDanger"),
}


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
    def confidence(cls, level: str, parent: QWidget | None = None) -> "Badge":
        text, style = CONFIDENCE_LABELS.get(level, CONFIDENCE_LABELS["unknown"])
        badge = cls(text, parent=parent)
        badge.setObjectName(style)
        return badge


class Card(QFrame):
    """카드 컨테이너. 내용은 body 레이아웃에 채운다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.SP_LG, theme.SP_LG, theme.SP_LG, theme.SP_LG)
        self.body.setSpacing(theme.SP_MD)


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
