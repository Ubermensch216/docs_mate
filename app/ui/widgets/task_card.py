"""업무 카드 — 업무 화면의 기본 단위.

한 화면에서 여러 업무를 조망하는 것이 목적이므로, 카드 하나가 3초 안에
읽혀야 한다. 그래서 정보를 네 층으로 못 박고 그 이상 넣지 않는다.

  1. 색 띠   — 업무를 구분하는 시각 앵커 (같은 업무는 항상 같은 색)
  2. 이름·규모 — 무슨 업무이고 얼마나 큰가
  3. 월 스트립 — 언제 하는 일인가 (12칸을 한 줄로 압축)
  4. 상태 칩  — 신뢰도·확인 필요 여부

색은 장식이 아니라 인덱스다. 업무 이름을 해시해 고정 팔레트에서 고르므로
같은 업무는 재실행해도 같은 색을 갖는다. 다만 색만으로 뜻을 전하지는
않는다 — 신뢰도·상태는 항상 기호와 글자를 함께 쓴다(PRD §18.4).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme

# 업무 구분용 팔레트. 채도를 낮춰 공직 문서 환경에 어울리게 하고, 서로
# 충분히 떨어진 색상만 골라 인접 카드가 헷갈리지 않게 한다.
TASK_COLORS = [
    "#1B5FA8",  # 청
    "#0F7B6C",  # 청록
    "#B45309",  # 주황
    "#7C3AED",  # 보라
    "#B91C1C",  # 적
    "#15803D",  # 녹
    "#0369A1",  # 하늘
    "#A16207",  # 황토
]

MONTH_ON = "▉"
MONTH_OFF = "·"


def color_for(name: str) -> str:
    """업무 이름에서 고정 색을 뽑는다. 같은 이름은 항상 같은 색."""
    return TASK_COLORS[sum(ord(c) for c in name) % len(TASK_COLORS)]


class TaskCard(QFrame):
    """클릭하면 상세로 들어가는 업무 카드."""

    opened = Signal(int)

    def __init__(
        self,
        task_id: int,
        name: str,
        description: str | None,
        span_text: str,
        months: list[int] | None,
        cycle_text: str | None,
        confidence: str,
        review_state: str,
        reading_count: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._task_id = task_id
        self.setObjectName("TaskCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        # Ignored 정책 + 낮은 최소 폭: 카드가 자기 내용 폭을 주장하면
        # 좁은 창에서 그리드가 밀려 잘린다. 폭 결정권은 그리드에 준다.
        self.setMinimumWidth(200)
        self.setToolTip(f"{name} — 눌러서 자세히 보기")

        accent = color_for(name)
        self.setStyleSheet(
            f"""
            QFrame#TaskCard {{
                background: {theme.BG};
                border: 1px solid {theme.BORDER};
                border-left: 4px solid {accent};
                border-radius: {theme.RADIUS}px;
            }}
            QFrame#TaskCard:hover {{
                border-color: {theme.BORDER_STRONG};
                border-left: 4px solid {accent};
                background: {theme.SURFACE};
            }}
            """
        )

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_LG, theme.SP_MD, theme.SP_LG, theme.SP_MD)
        column.setSpacing(theme.SP_SM)

        column.addWidget(_title(name, accent))
        column.addWidget(_meta(span_text))

        if description:
            column.addWidget(_description(description))

        column.addWidget(_month_strip(months, accent))
        column.addWidget(_cycle_line(cycle_text))
        column.addLayout(_footer(confidence, review_state, reading_count))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit(self._task_id)
        super().mouseReleaseEvent(event)


# ── 구성 요소 ───────────────────────────────────────────────────────

def _title(name: str, accent: str) -> QLabel:
    label = QLabel(name)
    label.setWordWrap(True)
    label.setStyleSheet(
        f"color: {accent}; font-size: {theme.FS_SECTION}px; font-weight: 600;"
    )
    return label


def _meta(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Small")
    return label


def _description(text: str) -> QLabel:
    """설명은 두 줄까지만. 카드마다 높이가 들쭉날쭉하면 격자가 어수선해진다."""
    label = QLabel(_clip(text, 58))
    label.setObjectName("Muted")
    label.setWordWrap(True)
    label.setToolTip(text)
    label.setMinimumHeight(theme.FS_BODY * 2 + 6)
    # 줄바꿈 라벨도 자기 '한 줄 폭'을 최소 폭으로 주장한다. 그대로 두면
    # 카드가 좁아지지 못해 그리드 열 수 계산이 무너진다.
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    return label


def _month_strip(months: list[int] | None, accent: str) -> QLabel:
    """12개월을 한 줄로 압축해 '언제 하는 일인가'를 즉시 보여준다.

    반복 주기를 모르는 업무는 빈 칸만 그린다 — 없는 것을 지어내지 않는다.
    """
    active = set(months or [])
    cells = "".join(MONTH_ON if m in active else MONTH_OFF for m in range(1, 13))
    label = QLabel(cells)
    label.setStyleSheet(
        f"color: {accent if active else theme.TEXT_DISABLED}; "
        f"font-size: {theme.FS_SMALL}px; letter-spacing: 2px;"
    )
    label.setToolTip(
        "1월부터 12월까지. 채워진 칸이 이 업무를 하는 달입니다."
        if active else "반복 주기를 아직 찾지 못했습니다."
    )
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return label


def _cycle_line(cycle_text: str | None) -> QLabel:
    label = QLabel(f"🔁 {cycle_text}" if cycle_text else "· 반복 주기 미확인")
    label.setObjectName("Small")
    if not cycle_text:
        label.setStyleSheet(f"color: {theme.TEXT_DISABLED};")
    return label


def _footer(confidence: str, review_state: str, reading_count: int) -> QHBoxLayout:
    from ...core import status
    from .common import Badge

    row = QHBoxLayout()
    row.setSpacing(theme.SP_SM)

    note, kind = _CONFIDENCE.get(confidence, _CONFIDENCE["low"])
    row.addWidget(Badge(note, kind))

    # 확인된 업무는 그렇다고 말해 준다. 인수인계 진행도(§18)에서 세는 것과
    # 카드에서 보이는 것이 같아야 사용자가 진도를 신뢰한다.
    if review_state == status.CONFIRMED:
        row.addWidget(Badge(f"{status.symbol(status.CONFIRMED)} 확인함", "ok"))
    else:
        row.addWidget(Badge(f"{status.symbol(review_state)} 확인 필요", "attention"))
    row.addStretch(1)

    if reading_count:
        count = QLabel(f"📄 {reading_count}")
        count.setObjectName("Small")
        count.setToolTip(f"먼저 읽을 문서 {reading_count}건")
        row.addWidget(count)
    return row


# 카드에서는 짧게 쓴다 — 목록에서 문장을 읽게 하면 조망이 안 된다.
_CONFIDENCE = {
    "high": ("● 단단함", "ok"),
    "medium": ("◐ 보통", "neutral"),
    "low": ("○ 느슨함", "attention"),
}


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
