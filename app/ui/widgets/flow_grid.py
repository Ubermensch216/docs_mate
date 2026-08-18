"""폭에 따라 열 수가 바뀌는 카드 그리드.

업무 카드를 한 화면에서 조망하려면 창을 넓혔을 때 열이 늘어나야 한다.
QGridLayout은 열 수가 고정이라 이 역할을 못 하고, 흔히 쓰는 FlowLayout은
카드 폭이 제각각이 되어 시각적으로 어수선해진다.

그래서 두 성질을 함께 만족시킨다.
  · 사용 가능한 폭에 맞춰 열 수를 다시 계산한다 (반응형)
  · 그 열 수 안에서 카드 폭은 모두 같게 나눈다 (정렬된 격자)

resize 때마다 다시 배치하되, 열 수가 실제로 바뀔 때만 재배치한다 —
폭이 1px 바뀔 때마다 전체를 다시 그리면 창 크기 조절이 버벅인다.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from .. import theme

MIN_CARD_WIDTH = 260   # 이보다 좁아지면 카드 내용이 읽기 어려워진다
MAX_COLUMNS = 4        # 너무 잘게 쪼개면 카드 하나하나가 빈약해 보인다


class FlowGrid(QWidget):
    """카드를 담는 반응형 격자."""

    def __init__(
        self,
        min_card_width: int = MIN_CARD_WIDTH,
        max_columns: int = MAX_COLUMNS,
        spacing: int = theme.SP_MD,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._min_card_width = min_card_width
        self._max_columns = max_columns
        self._cards: list[QWidget] = []
        self._columns = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def add_card(self, card: QWidget) -> None:
        self._cards.append(card)
        # 첫 배치는 현재 폭 기준으로 즉시 한다. resizeEvent를 기다리면
        # 창이 한 번도 리사이즈되지 않은 경우 영영 배치되지 않는다.
        self._relayout(force=True)

    def clear(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._columns = 0

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._relayout()

    def _lock_height(self) -> None:
        """카드가 필요로 하는 높이를 **하한으로 못 박는다.**

        힌트만으로는 부족하다. 세로 공간이 모자라면 Qt는 힌트를 무시하고
        줄일 수 있는 위젯부터 줄이는데, 줄바꿈 라벨의 '최소'는 한 줄이라
        카드가 제 내용보다 작아진다 — 그러면 카드 안에서 제목·설명·뱃지가
        서로 겹쳐 그려진다(업무 홈에 건강도 판을 붙이자 실제로 그랬다).

        minimumHeight는 힌트가 아니라 제약이라 그 아래로는 눌리지 않고,
        대신 바깥 스크롤이 생긴다. 세로 스크롤 안에 사는 위젯이니 그게 맞다.
        """
        if not self._cards:
            self.setMinimumHeight(0)
            return
        rows = (len(self._cards) + self._columns - 1) // max(1, self._columns)
        tallest = max(card.sizeHint().height() for card in self._cards)
        spacing = self._grid.spacing()
        self.setMinimumHeight(rows * tallest + (rows - 1) * spacing)

    def _column_count(self) -> int:
        width = self.width()
        if width <= 0:
            return 1
        spacing = self._grid.spacing()
        # n개 열이 들어가려면: n*최소폭 + (n-1)*간격 <= 폭
        fits = (width + spacing) // (self._min_card_width + spacing)
        return max(1, min(self._max_columns, int(fits)))

    def _relayout(self, force: bool = False) -> None:
        columns = self._column_count()
        if not force and columns == self._columns:
            return
        self._columns = columns

        while self._grid.count():
            self._grid.takeAt(0)

        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)

        # 남는 열을 균등하게 늘려 카드 폭을 같게 만든다. 이전에 쓰던
        # stretch가 남아 있으면 열 폭이 어긋나므로 먼저 지운다.
        for col in range(self._max_columns):
            self._grid.setColumnStretch(col, 1 if col < columns else 0)

        self._lock_height()

    def _lock_height(self) -> None:
        """카드가 필요로 하는 높이를 **하한으로 못 박는다.**

        힌트만으로는 부족하다. 세로 공간이 모자라면 Qt는 힌트를 무시하고
        줄일 수 있는 위젯부터 줄이는데, 줄바꿈 라벨의 '최소'는 한 줄이라
        카드가 제 내용보다 작아진다 — 그러면 카드 안에서 제목·설명·뱃지가
        서로 겹쳐 그려진다(업무 홈에 건강도 판을 붙이자 실제로 그랬다).

        minimumHeight는 힌트가 아니라 제약이라 그 아래로 눌리지 않고, 대신
        바깥에 스크롤이 생긴다. 세로 스크롤 안에 사는 위젯이니 그게 맞다.
        """
        if not self._cards:
            self.setMinimumHeight(0)
            return
        columns = max(1, self._columns)
        rows = (len(self._cards) + columns - 1) // columns
        tallest = max(card.sizeHint().height() for card in self._cards)
        spacing = self._grid.spacing()
        self.setMinimumHeight(rows * tallest + (rows - 1) * spacing)
