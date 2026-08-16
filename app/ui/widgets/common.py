"""공통 위젯 — 신뢰 표현 3종과 레이아웃 조각."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
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


def fit_wrapped_text(widget: QWidget) -> None:
    """줄바꿈되는 글자를 담은 판이 제 높이를 갖게 한다.

    Qt는 위젯의 sizePolicy에 hasHeightForWidth가 켜져 있을 때만 "이 폭이면
    높이가 얼마"를 부모 레이아웃에 알린다. 기본값은 꺼짐이라, 안에 든
    QLabel이 두 줄로 접혀도 판은 한 줄 높이만 받는다 — 그러면 뒤따르는
    위젯이 그 위에 겹쳐 그려져 **글자가 통째로 안 읽힌다**(문서 화면
    오른쪽 패널에서 실제로 그렇게 나왔다).

    새로 만드는 판에 줄바꿈 글자를 넣을 때는 이 함수를 함께 부를 것.
    """
    policy = widget.sizePolicy()
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)


class WrapLabel(QLabel):
    """줄바꿈되는 글자. **접힌 만큼 실제로 자리를 차지한다.**

    Qt에서 word-wrap QLabel은 스크롤 영역이나 폭이 정해진 칸 안에서 제 높이를
    제대로 못 받는 일이 흔하다. 부모 레이아웃은 한 줄 높이를 주는데 글자는 두
    줄로 접혀서, 뒤따르는 위젯이 그 위에 겹쳐 그려진다 — 문서 화면 오른쪽
    패널에서 안내 문구가 통째로 뭉개져 안 읽혔다.

    폭이 정해질 때마다 그 폭에서 필요한 높이를 스스로 최소 높이로 잡는다.
    """

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._fit()

    def setText(self, text: str) -> None:  # noqa: N802 — Qt 규약
        super().setText(text)
        self._fit()

    def _fit(self) -> None:
        if not self.wordWrap() or self.width() <= 0:
            return
        needed = self.heightForWidth(self.width())
        # 같은 값을 다시 넣으면 레이아웃이 무한히 다시 계산된다.
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)


class _WrapFrame(QFrame):
    """줄바꿈 글자를 담는 판의 공통 바탕. 높이를 폭에서 계산해 돌려준다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        fit_wrapped_text(self)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — Qt 규약
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt 규약
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            return layout.heightForWidth(width)
        return super().heightForWidth(width)


def view_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("ViewTitle")
    return label


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


class SectionHeader(QWidget):
    """구획 머리.

    When·How 같은 개념 이름은 제품의 뼈대이지만, 처음 이 화면을 여는
    담당자에게는 영어 두 글자일 뿐이다. 그래서 개념 이름은 작은 꼬리표로
    남기고, 큰 글자는 사용자가 실제로 품는 질문으로 적는다.

    hint는 제목 옆 ⓘ로 접힌다. 제목 아래에 한 문단으로 깔면 구획마다
    설명이 쌓여, 정작 내용이 시작되는 지점이 매번 두 줄씩 밀린다.
    """

    def __init__(
        self,
        title: str,
        tag: str = "",
        hint: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, theme.SP_SM, 0, 0)
        column.setSpacing(theme.SP_XS)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_SM)
        if tag:
            mark = QLabel(tag)
            mark.setObjectName("SectionTag")
            mark.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            row.addWidget(mark)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        row.addWidget(heading)
        if hint:
            row.addWidget(InfoDot(hint), 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        column.addLayout(row)


def body_label(text: str, wrap: bool = True) -> QLabel:
    label = WrapLabel(text) if wrap else QLabel(text)
    label.setWordWrap(wrap)
    return label


def muted_label(text: str, small: bool = False, wrap: bool = True) -> QLabel:
    """wrap=False는 줄바꿈되면 어색한 짧은 표시(뱃지 옆 문구 등)에 쓴다."""
    label = WrapLabel(text) if wrap else QLabel(text)
    label.setObjectName("Small" if small else "Muted")
    label.setWordWrap(wrap)
    if not wrap:
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


class ElidedLabel(QLabel):
    """긴 한 낱말(파일명·경로)을 폭에 맞춰 가운데를 접어 보여준다.

    파일명과 경로에는 띄어쓰기가 없어 QLabel의 줄바꿈이 듣지 않는다. 그냥
    두면 줄 하나가 칸보다 넓어져 옆의 버튼을 화면 밖으로 밀거나(프로젝트
    목록에서 실제로 그랬다) 글자가 소리 없이 잘린다.

    접는 자리는 가운데다 — `2025_행정사무감사_..._최종2.docx`처럼 구별에
    필요한 단서가 양 끝에 있다. 전체는 툴팁으로 남긴다.
    """

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._render()

    def setText(self, text: str) -> None:  # noqa: N802 — Qt 규약
        self._full = text
        self.setToolTip(text)
        self._render()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._render()

    def minimumSizeHint(self):  # noqa: N802 — Qt 규약
        """폭을 요구하지 않는다.

        QLabel은 줄바꿈이 없으면 **글자 전체 폭**을 최소 크기로 요구한다.
        그래서 긴 파일명 하나가 카드·패널을 뷰포트보다 넓게 밀어내고, 가로
        스크롤을 꺼 둔 칸에서는 오른쪽이 잘려 나간다(실제로 그렇게 나왔다).
        접는 위젯이 폭을 요구하는 것은 그 자체로 모순이다.
        """
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def _render(self) -> None:
        width = max(self.width(), 80)
        QLabel.setText(
            self, self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width)
        )


def note_label(text: str) -> QLabel:
    """화면 설명 문구. 목록 항목과 같은 회색 글씨로 흘리면 내용과 섞여 읽힌다.

    옅은 판 위에 얹어 "이건 안내지 자료가 아니다"를 배경으로 먼저 말한다.

    설명이 길거나 한 번 읽으면 그만인 것은 InfoDot으로 접는다. 상주해야
    하는 안내(예: 지금 이 목록이 무엇으로 정렬돼 있는가)만 여기 남긴다.
    """
    label = WrapLabel(text)
    label.setObjectName("PageNote")
    label.setWordWrap(True)
    label.setMaximumWidth(theme.CONTENT_MAX_W)
    return label


class InfoDot(QLabel):
    """설명을 접어 두는 ⓘ. 마우스를 올리면 팝업으로 편다.

    안내문은 한 번 읽으면 그만이지만 화면에는 영원히 남는다. 넷째 방문부터
    그 문단은 읽히지 않으면서 자리만 차지하고, 목록·카드와 같은 회색
    글씨라서 눈은 매번 "이건 내용인가 설명인가"를 판별해야 한다. 그 비용이
    화면을 산만하게 만든다.

    그래서 설명은 기본적으로 접고, 필요한 사람만 펴게 한다. 접었다는 사실
    자체는 보여야 하므로(안 보이는 도움말은 없는 도움말이다) 제목 옆에
    작은 원으로 항상 자리를 지킨다.
    """

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__("i", parent)
        self.setObjectName("InfoDot")
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setInfo(text)

    def setInfo(self, text: str) -> None:  # noqa: N802 — Qt 명명 규약에 맞춘다
        # 서식 있는 툴팁은 Qt가 스스로 줄을 바꾼다. 폭을 주지 않으면 한 줄로
        # 길게 늘어져 화면 밖으로 나간다.
        self.setToolTip(
            f'<div style="max-width:320px; line-height:150%;">{text}</div>'
        )
        self.setAccessibleDescription(text)


def hint_row(text: str, info: str, strong: bool = False) -> QWidget:
    """짧은 머리말 + ⓘ 한 쌍. 설명을 접어 두는 자리의 기본형.

    ⓘ만 덩그러니 두면 무엇에 대한 설명인지 알 수 없다. 항상 짧은 말과
    함께 둔다 — 말이 무엇인지 말하고, 원이 자세한 것을 맡는다.
    """
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.SP_SM)
    label = QLabel(text)
    label.setObjectName("Answer" if strong else "BlockLabel")
    row.addWidget(label)
    row.addWidget(InfoDot(info), 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    return box


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


class TabBar(QWidget):
    """화면 안 탭. 사이드바 메뉴와 헷갈리지 않도록 밑줄 형태로 만든다.

    탭 이름에 건수를 달 수 있는 것이 요점이다 — 사용자가 **누르기 전에**
    규모를 안다. 한 줄로 이어 붙였을 때는 끝까지 스크롤해야 알 수 있었다.
    """

    switched = Signal(str)

    def __init__(self, tabs, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TabBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_XS)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for key, label in tabs:
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
        self._buttons[key].setText(label if not count else f"{label}  {count}")

    def set_label(self, key: str, text: str) -> None:
        self._buttons[key].setText(text)

    def select(self, key: str) -> None:
        self._buttons[key].setChecked(True)


class IconChoice(QWidget):
    """아이콘 하나로 뜻이 서는 배타 선택 묶음 (글자 크기·테마 등).

    라디오 버튼과 글자로 늘어놓으면 셋 다 같은 회색 한 줄이라, 고르기 전에
    읽어야 한다. 그림이 먼저 뜻을 말하게 두면 훑는 눈이 바로 멈춘다.

    글자를 완전히 지우지는 않는다 — 아이콘만 있는 조작은 "눌러 봐야 아는 것"이
    되고, 특히 시스템/밝게/어둡게처럼 결과가 즉시 눈에 보이지 않는 항목(시스템)이
    섞이면 그림만으로는 모호하다. 그림 아래 작은 글자를 확인용으로 붙인다.

    options는 (키, 아이콘 이름, 짧은 글자, 툴팁) 네 쌍이다.
    """

    chosen = Signal(str)

    def __init__(
        self,
        options,
        current: str = "",
        icon_size: int = 26,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("ChoiceGroup")
        self._icon_size = icon_size
        self._items: dict[str, QPushButton] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_SM)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for key, icon_name, caption, tip in options:
            button = QPushButton()
            button.setObjectName("ChoiceItem")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.setAccessibleName(f"{caption} — {tip}")

            column = QVBoxLayout(button)
            column.setContentsMargins(theme.SP_SM, theme.SP_SM, theme.SP_SM, theme.SP_SM)
            column.setSpacing(theme.SP_XS)
            column.setAlignment(Qt.AlignmentFlag.AlignCenter)

            mark = QLabel()
            mark.setFixedSize(icon_size, icon_size)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)

            label = QLabel(caption)
            label.setObjectName("ChoiceCaption")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)

            for child in (mark, label):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

            button._icon_name = icon_name   # 색을 다시 칠할 때 쓴다
            button._icon_mark = mark
            button.clicked.connect(lambda _=False, k=key: self.chosen.emit(k))
            self._group.addButton(button)
            row.addWidget(button)
            self._items[key] = button

        row.addStretch(1)
        if current in self._items:
            self._items[current].setChecked(True)
        self.repaint_icons()

    def select(self, key: str) -> None:
        button = self._items.get(key)
        if button:
            button.setChecked(True)
        self.repaint_icons()

    def current(self) -> str:
        for key, button in self._items.items():
            if button.isChecked():
                return key
        return ""

    def repaint_icons(self) -> None:
        """아이콘은 글자와 같은 색으로 움직여야 한 덩어리로 읽힌다.

        테마를 바꾼 직후에도 불러야 한다 — 스타일시트는 갈렸는데 아이콘만
        옛 색으로 남으면 고른 칸이 어디인지 흐려진다.
        """
        from .. import icons

        for button in self._items.values():
            tint = theme.PRIMARY if button.isChecked() else theme.TEXT_MUTED
            art = icons.pixmap(button._icon_name, tint, self._icon_size)
            if art is None:      # QtSvg가 없는 환경 — 글자로 물러선다
                button._icon_mark.setText("◆")
                button._icon_mark.setStyleSheet(f"color: {tint};")
                continue
            button._icon_mark.setPixmap(art)


class Page(QWidget):
    """탭 하나의 내용. 각자 스크롤한다 — 탭을 바꿔도 남의 스크롤이 따라오지 않는다.

    바탕은 흰색이 아니라 한 단계 낮춘 회색이다. 흰 종이 위에 흰 카드를
    올리면 카드가 카드로 보이지 않는다 — 시인성 문제 절반이 거기서 왔다.

    max_width를 주면 본문 기둥의 폭을 묶는다. 창을 넓혔을 때 한 줄이
    끝없이 길어지면 다음 줄 첫 글자를 눈이 못 찾는다.
    """

    def __init__(self, max_width: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("PageScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body.setObjectName("PageBody")
        body.setAutoFillBackground(True)
        outer_body = QHBoxLayout(body)
        outer_body.setContentsMargins(
            theme.SP_XL, theme.SP_LG, theme.SP_XL, theme.SP_XL
        )
        outer_body.setSpacing(0)

        holder = QWidget()
        holder.setObjectName("PageColumn")
        if max_width:
            holder.setMaximumWidth(max_width)
        self.column = QVBoxLayout(holder)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(theme.SP_MD)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer_body.addWidget(holder, 1)
        if max_width:
            outer_body.addStretch(0)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def reset(self) -> QVBoxLayout:
        clear_layout(self.column)
        return self.column


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


class Card(_WrapFrame):
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


class SubPanel(_WrapFrame):
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


class UnknownBlock(_WrapFrame):
    """자료에서 확인되지 않은 구간을 명시적으로 그린다.

    빈칸으로 두면 사용자는 시스템이 놓쳤는지 자료가 없는지 구분할 수 없다.
    없는 것을 지어내지 않는다는 원칙을 화면에서 실행하는 위젯이다.
    """

    def __init__(self, message: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("UnknownBlock")
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SP_MD, theme.SP_SM, theme.SP_MD, theme.SP_SM)
        self.label = WrapLabel()
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
