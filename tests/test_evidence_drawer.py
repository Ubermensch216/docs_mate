"""근거 서랍 시험 (RAG 개선 R9, 계획서 §14·§29).

AI가 무언가를 주장하면 사용자는 확인할 수 있어야 한다. 원본을 바로 여는
것은 확인이 아니라 이탈이다 — 앱 안에서 근거를 먼저 보여주고, 원본 열기는
그다음 선택으로 둔다. 이 서랍은 질문 화면만의 위젯이 아니라 업무 설명·주기·
처리 순서도 함께 쓰는 공용 컴포넌트다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.widgets import Evidence, EvidenceDrawer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


def texts(widget) -> list[str]:
    return [w.text() for w in widget.findChildren(QLabel) if w.text()]


# ── 기본 동작 ───────────────────────────────────────────────────────

def test_drawer_starts_hidden(qapp):
    """근거를 볼 일이 없을 때 화면 폭을 차지하면 안 된다."""
    drawer = EvidenceDrawer()
    assert not drawer.isVisible()


def test_showing_evidence_reveals_the_drawer(qapp):
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="행감.hwp", locator="3쪽", snippet="내용")])
    assert not drawer.isHidden()


def test_drawer_shows_filename_locator_and_quote(qapp):
    """세 가지가 다 있어야 사용자가 '이 문장이 저기서 나왔구나'를 확인한다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([
        Evidence(label="2025_행감_제출자료.hwp", locator="3쪽",
                 snippet="행정사무감사 요구자료를 제출하였다"),
    ])
    body = "\n".join(texts(drawer))
    assert "2025_행감_제출자료.hwp" in body
    assert "3쪽" in body
    assert "행정사무감사 요구자료를 제출하였다" in body


def test_quote_is_visually_marked_as_a_quotation(qapp):
    """인용문은 '앱이 쓴 말'이 아니라 '문서에 있던 말'이다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="a.hwp", snippet="원문 그대로")])
    quote = next(
        w for w in drawer.findChildren(QLabel) if w.objectName() == "EvidenceQuote"
    )
    assert "원문 그대로" in quote.text()
    assert quote.text().startswith("“")


def test_dismiss_hides_the_drawer(qapp):
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="a.hwp")])
    drawer.dismiss()
    assert drawer.isHidden()


# ── 없는 것을 숨기지 않는다 ─────────────────────────────────────────

def test_empty_evidence_says_so_instead_of_opening_blank(qapp):
    """조용히 닫으면 사용자는 클릭이 먹지 않은 줄 안다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([])
    assert not drawer.isHidden()
    assert any("근거를 찾지 못했습니다" in t for t in texts(drawer))


def test_missing_snippet_is_explained(qapp):
    """인용문을 못 뽑았으면 빈칸이 아니라 이유를 밝힌다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="a.hwp", locator="1문단", snippet="")])
    assert any("인용문을 뽑지 못했습니다" in t for t in texts(drawer))


# ── 원본으로 가는 길 ────────────────────────────────────────────────

def test_open_original_emits_the_path(qapp):
    drawer = EvidenceDrawer()
    received: list[str] = []
    drawer.open_original.connect(received.append)
    drawer.show_evidence([Evidence(label="a.hwp", path=r"D:\자료\a.hwp")])

    button = next(
        b for b in drawer.findChildren(QPushButton) if b.text() == "원본 열기"
    )
    button.click()
    assert received == [r"D:\자료\a.hwp"]


def test_no_open_button_without_a_path(qapp):
    """경로가 없으면 열 수 없다 — 눌러도 아무 일 없는 버튼을 두지 않는다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="a.hwp", snippet="내용")])
    assert not any(b.text() == "원본 열기" for b in drawer.findChildren(QPushButton))


# ── 여러 근거 ───────────────────────────────────────────────────────

def test_multiple_evidence_items_are_all_shown(qapp):
    drawer = EvidenceDrawer()
    drawer.show_evidence([
        Evidence(label="a.hwp", snippet="첫째"),
        Evidence(label="b.hwp", snippet="둘째"),
    ])
    body = "\n".join(texts(drawer))
    assert "a.hwp" in body and "b.hwp" in body
    assert "첫째" in body and "둘째" in body


def test_reopening_replaces_the_previous_contents(qapp):
    """옛 근거가 남아 있으면 새 주장의 근거로 오해한다."""
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="옛문서.hwp", snippet="옛 내용")])
    drawer.show_evidence([Evidence(label="새문서.hwp", snippet="새 내용")])
    body = "\n".join(texts(drawer))
    assert "새문서.hwp" in body
    assert "옛문서.hwp" not in body


def test_title_reflects_how_many_evidence_items(qapp):
    drawer = EvidenceDrawer()
    drawer.show_evidence([Evidence(label="a.hwp"), Evidence(label="b.hwp")])
    assert any("2건" in t for t in texts(drawer))


def test_drawer_width_matches_the_layout_spec(qapp):
    """계획서 §28 — 360~420px."""
    assert 360 <= theme.DRAWER_W <= 420
    assert EvidenceDrawer().width() == theme.DRAWER_W
