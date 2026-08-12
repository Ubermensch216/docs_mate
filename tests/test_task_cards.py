"""업무 카드 그리드 시험.

한 화면에서 여러 업무를 조망하는 것이 목적이므로, 폭에 따라 열 수가
실제로 바뀌는지와 카드가 정보를 정확히 담는지를 본다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from app.core import status  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.widgets import FlowGrid, TaskCard, color_for  # noqa: E402
from app.ui.widgets.flow_grid import MAX_COLUMNS, MIN_CARD_WIDTH  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


def _card(**overrides) -> TaskCard:
    base = dict(
        task_id=1, name="행정사무감사", description="의회 요구자료를 취합해 제출합니다.",
        span_text="17건 · 2022~2025", months=[9, 10, 11], cycle_text="매년 9~11월",
        confidence="high", review_state=status.CONFIRMED, reading_count=4,
    )
    base.update(overrides)
    return TaskCard(**base)


# ── 색 인덱싱 ───────────────────────────────────────────────────────

def test_same_task_name_always_gets_the_same_color():
    """색은 장식이 아니라 인덱스다 — 재실행해도 같아야 한다."""
    assert color_for("행정사무감사") == color_for("행정사무감사")


def test_different_task_names_usually_differ_in_color():
    names = ["행정사무감사", "예산관리", "수질통계", "계약관리", "업무계획"]
    colors = {color_for(n) for n in names}
    assert len(colors) >= 3, "색이 지나치게 겹치면 카드 구분이 안 된다"


# ── 카드 내용 ───────────────────────────────────────────────────────

def test_card_shows_name_span_and_cycle(qapp):
    card = _card()
    texts = [l.text() for l in card.findChildren(QLabel) if l.text()]
    assert any("행정사무감사" in t for t in texts)
    assert any("17건" in t for t in texts)
    assert any("매년 9~11월" in t for t in texts)


def test_card_month_strip_marks_only_active_months(qapp):
    card = _card(months=[9, 10, 11])
    strip = next(
        l.text() for l in card.findChildren(QLabel)
        if l.text() and set(l.text()) <= {"▉", "·"} and len(l.text()) == 12
    )
    assert len(strip) == 12
    assert [i + 1 for i, ch in enumerate(strip) if ch == "▉"] == [9, 10, 11]


def test_card_without_cycle_shows_empty_strip_and_says_so(qapp):
    """반복 주기를 모르면 지어내지 않는다."""
    card = _card(months=None, cycle_text=None)
    texts = [l.text() for l in card.findChildren(QLabel) if l.text()]
    assert any("반복 주기 미확인" in t for t in texts)
    strip = next(t for t in texts if set(t) <= {"▉", "·"} and len(t) == 12)
    assert "▉" not in strip


def test_card_shows_review_badge_only_when_needed(qapp):
    # 카드를 지역 변수로 붙들어야 한다 — 임시 객체로 두면 findChildren이
    # 도는 사이 파이썬 GC가 카드를 수거해 C++ 쪽 위젯이 먼저 파괴된다.
    reviewed = _card(review_state=status.INFERRED)
    clean = _card(review_state=status.CONFIRMED)
    texts_review = [l.text() for l in reviewed.findChildren(QLabel) if l.text()]
    texts_clean = [l.text() for l in clean.findChildren(QLabel) if l.text()]
    assert any("확인 필요" in t for t in texts_review)
    assert not any("확인 필요" in t for t in texts_clean)


def test_confirmed_card_says_it_is_confirmed(qapp):
    """진행도에서 세는 것과 카드에서 보이는 것이 같아야 한다 (§18)."""
    card = _card(review_state=status.CONFIRMED)
    texts = [l.text() for l in card.findChildren(QLabel) if l.text()]
    assert any("확인함" in t for t in texts)


def test_card_clip_long_description_but_keeps_full_text_in_tooltip(qapp):
    long_text = "가" * 200
    card = _card(description=long_text)
    label = next(
        l for l in card.findChildren(QLabel)
        if l.toolTip() == long_text
    )
    assert len(label.text()) < len(long_text)
    assert label.text().endswith("…")


def test_card_emits_task_id_when_clicked(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    card = _card(task_id=42)
    received = []
    card.opened.connect(received.append)

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, QPoint(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    card.mouseReleaseEvent(event)
    assert received == [42]


# ── 반응형 그리드 ───────────────────────────────────────────────────

def _grid_with(qapp, count: int, width: int) -> FlowGrid:
    """holder를 grid에 붙들어 둔다 — 지역 변수로만 두면 함수가 끝날 때
    파이썬 GC가 부모를 수거하면서 자식 위젯까지 함께 파괴된다."""
    grid = FlowGrid()
    holder = QWidget()
    holder.resize(width, 800)
    grid.setParent(holder)
    grid._test_holder = holder   # 수명 유지용
    grid.resize(width, 800)
    for i in range(count):
        grid.add_card(_card(task_id=i, name=f"업무{i}"))
    grid.resize(width, 800)
    return grid


def test_grid_uses_more_columns_when_wider(qapp):
    narrow = _grid_with(qapp, 6, 560)
    wide = _grid_with(qapp, 6, 1400)
    assert wide._columns > narrow._columns


def test_grid_never_exceeds_the_column_cap(qapp):
    grid = _grid_with(qapp, 12, 4000)
    assert grid._columns <= MAX_COLUMNS


def test_grid_always_has_at_least_one_column(qapp):
    grid = _grid_with(qapp, 3, 50)
    assert grid._columns == 1


def test_grid_column_count_respects_minimum_card_width(qapp):
    """카드가 읽을 수 없을 만큼 좁아지면 안 된다."""
    width = MIN_CARD_WIDTH * 2 + theme.SP_MD
    grid = _grid_with(qapp, 6, width)
    assert grid._columns == 2


def test_grid_clear_removes_every_card(qapp):
    grid = _grid_with(qapp, 5, 1200)
    grid.clear()
    assert grid._cards == []
    assert grid._grid.count() == 0


def test_grid_places_all_cards(qapp):
    grid = _grid_with(qapp, 7, 1200)
    assert grid._grid.count() == 7
