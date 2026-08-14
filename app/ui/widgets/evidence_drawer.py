"""근거 서랍 — Claim → Evidence → Original의 가운데 단계 (계획서 §14·§29).

AI가 무언가를 주장하면 사용자는 그것을 확인할 수 있어야 한다. 그런데
원본을 바로 여는 것은 확인이 아니라 **이탈**이다 — 한글이 뜨는 데 몇 초가
걸리고, 문서를 열면 화면을 떠나며, 돌아오면 맥락을 잃는다. 그래서 근거를
앱 안에서 먼저 보여주고, 원본 열기는 그다음 선택으로 둔다.

    주장          "…9월에 접수했다[1]"
      ↓ [1] 클릭
    근거          이 서랍 — 파일명 · 위치 · 인용문
      ↓ [원본 열기]
    원본          한글·엑셀에서 실제 문서

질문 화면만의 위젯이 아니다. 업무 설명·주기·처리 순서·최신본 후보도 모두
같은 서랍을 쓴다(계획서 §29) — 그래야 "근거 기반"이 UI 자체에 박힌다.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .common import clear_layout, muted_label, section_title


@dataclass(slots=True)
class Evidence:
    """서랍에 넣을 근거 하나. DB 행이나 Citation을 그대로 넘기지 않는다 —
    호출하는 화면마다 자료 모양이 다르므로 여기서 한 겹 끊는다."""

    label: str            # 파일명
    locator: str = ""     # "3쪽", "'집계' A1:E3" 등
    snippet: str = ""     # 실제 인용문
    path: str = ""        # 원본 경로
    note: str = ""        # "왜 이것이 근거인가" (선택)


class EvidenceDrawer(QFrame):
    """오른쪽에서 열리는 근거 패널. 기본은 숨어 있다."""

    open_original = Signal(str)   # 원본 경로
    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("EvidenceDrawer")
        self.setFixedWidth(theme.DRAWER_W)
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_LG, theme.SP_MD, theme.SP_LG, theme.SP_LG)
        outer.setSpacing(theme.SP_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        self._title = section_title("근거")
        head.addWidget(self._title)
        head.addStretch(1)
        close = QPushButton("✕")
        close.setObjectName("Link")
        close.setFixedWidth(24)
        close.setToolTip("닫기")
        close.clicked.connect(self.dismiss)
        head.addWidget(close)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(0, 0, theme.SP_SM, 0)
        self.column.setSpacing(theme.SP_MD)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    # ── 조작 ────────────────────────────────────────────────────────
    def show_evidence(self, items: list[Evidence], title: str = "근거") -> None:
        """근거를 채우고 연다. 빈 목록이면 그렇다고 말한다 — 조용히 닫지 않는다."""
        clear_layout(self.column)
        self._title.setText(f"{title} {len(items)}건" if items else title)

        if not items:
            self.column.addWidget(
                muted_label("이 주장에 연결된 근거를 찾지 못했습니다.")
            )
        for index, item in enumerate(items):
            if index:
                self.column.addWidget(_separator())
            self.column.addWidget(self._entry(item))

        self.show()

    def dismiss(self) -> None:
        self.hide()
        self.closed.emit()

    # ── 구성 ────────────────────────────────────────────────────────
    def _entry(self, item: Evidence) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_XS)

        name = muted_label(item.label)
        name.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600;")
        name.setToolTip(item.path or item.label)
        column.addWidget(name)

        if item.locator:
            column.addWidget(muted_label(item.locator, small=True))
        if item.note:
            column.addWidget(muted_label(item.note, small=True))

        if item.snippet:
            quote = muted_label(f"“{item.snippet}”")
            quote.setObjectName("EvidenceQuote")
            column.addWidget(quote)
        else:
            # 인용문을 못 만든 경우를 빈칸으로 두지 않는다 — 사용자가
            # "왜 아무것도 없지?"를 겪지 않게 이유를 밝힌다.
            column.addWidget(
                muted_label("이 문서에서 인용문을 뽑지 못했습니다.", small=True)
            )

        if item.path:
            open_button = QPushButton("원본 열기")
            open_button.clicked.connect(
                lambda _=False, p=item.path: self.open_original.emit(p)
            )
            column.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        return holder


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
