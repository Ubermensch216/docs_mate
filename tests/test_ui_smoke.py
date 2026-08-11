"""UI 셸 스모크 시험.

오프스크린으로 돌리므로 폰트는 없다. 글자 모양이 아니라 구조·상태 전환·
레이아웃 누수를 본다.

MainWindow는 반드시 fixture로 만든다. 백그라운드 스레드를 띄운 채 창이
파괴되면 프로세스가 죽기 때문이다 — 실제로 이 시험이 그 결함을 잡아냈다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.shell import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    yield database
    database.close()


@pytest.fixture
def make_window(qapp):
    """창을 만들고 반드시 정리한다."""
    created: list[MainWindow] = []

    def factory(db: Database) -> MainWindow:
        window = MainWindow(db)
        created.append(window)
        return window

    yield factory

    for window in created:
        window.shutdown()
        window.close()
        window.deleteLater()
    qapp.processEvents()


def _add_docs(db: Database, count: int = 4, parsed: int = 2) -> int:
    """분석이 끝난 상태로 넣는다. 파이프라인이 자동 재개되지 않도록 한다."""
    source_id = db.add_source(r"D:\전임자업무")
    for i in range(count):
        db.upsert_document(source_id, {
            "path": rf"D:\전임자업무\문서{i}.hwp",
            "filename": f"문서{i}.hwp",
            "ext": ".hwp",
            "size": 1000 + i,
            "hash": f"h{i}",
            "parse_status": "ok" if i < parsed else "empty",
            "eff_date": f"202{i % 4 + 2}-09-01",
            "eff_year": 2022 + (i % 4),
            "eff_date_kind": "body",
            "eff_precision": "month",
        })
    return source_id


def test_window_starts_on_onboarding_when_no_source(make_window, db):
    window = make_window(db)
    assert window.stack.currentWidget() is window.views["onboarding"]
    # 자료원이 없으면 메뉴를 누를 수 없어야 한다 — 빈 화면으로 보내지 않는다.
    assert not window.sidebar._buttons["tasks"].isEnabled()


def test_window_switches_to_tasks_once_source_exists(make_window, db):
    _add_docs(db)
    window = make_window(db)
    assert window.stack.currentWidget() is window.views["tasks"]
    assert window.sidebar._buttons["tasks"].isEnabled()


@pytest.mark.parametrize("key", ["tasks", "calendar", "documents", "ask"])
def test_every_view_navigates_and_refreshes(make_window, db, key):
    _add_docs(db)
    window = make_window(db)
    window.go(key)
    assert window.stack.currentWidget() is window.views[key]


def test_refresh_does_not_leak_widgets(make_window, db):
    """clear_layout 회귀 시험.

    deleteLater()만 쓰면 옛 위젯이 남아 새 내용과 겹쳐 그려진다.
    같은 상태로 여러 번 새로 고쳐도 위젯 수가 늘지 않아야 한다.
    """
    _add_docs(db)
    view = make_window(db).views["tasks"]

    view.refresh()
    first = view.column.count()
    for _ in range(5):
        view.refresh()
    assert view.column.count() == first


def test_calendar_refuses_to_claim_cycles_without_enough_years(make_window, db):
    """자료가 한 해치뿐이면 반복을 주장하지 않는다."""
    source_id = db.add_source(r"D:\한해자료")
    db.upsert_document(source_id, {
        "path": r"D:\한해자료\a.hwp", "filename": "a.hwp", "ext": ".hwp",
        "eff_date": "2024-09-01", "eff_year": 2024, "parse_status": "ok",
        "hash": "x",
    })
    window = make_window(db)
    window.go("calendar")
    texts = [
        label.text()
        for label in window.views["calendar"].findChildren(QLabel)
        if label.text()
    ]
    assert any("판단할 자료가 부족" in t for t in texts), texts


def test_ask_view_withholds_until_analysis_done(make_window, db):
    """분석 전에는 질문 입력을 막는다. 근거 없이 답하지 않기 위해서다."""
    _add_docs(db)
    view = make_window(db).views["ask"]
    view.refresh()
    assert not view.send.isEnabled()
    assert view.notice.label.text()


def test_documents_view_lists_rows(make_window, db):
    _add_docs(db, count=3, parsed=3)
    window = make_window(db)
    window.go("documents")
    assert window.views["documents"].table.rowCount() == 3


def test_documents_view_flags_filesystem_only_dates(make_window, db):
    """파일 수정일로만 판정한 시점은 그렇다고 밝혀야 한다."""
    source_id = db.add_source(r"D:\단서없음")
    db.upsert_document(source_id, {
        "path": r"D:\단서없음\회의자료.txt", "filename": "회의자료.txt", "ext": ".txt",
        "parse_status": "ok", "eff_date": "2023-06-02", "hash": "y",
        "eff_date_kind": "fs", "eff_precision": "day",
    })
    window = make_window(db)
    window.go("documents")
    when = window.views["documents"].table.item(0, 2).text()
    assert "파일 날짜" in when, when


def test_documents_view_collapses_exact_duplicates(make_window, db):
    """완전 중복은 한 줄로 접힌다. 목록이 줄어드는 것이 정리의 실감이다."""
    source_id = db.add_source(r"D:\중복")
    for i in range(3):
        db.upsert_document(source_id, {
            "path": rf"D:\중복\보고서_사본{i}.hwp",
            "filename": f"보고서_사본{i}.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": "SAME",
        })
    window = make_window(db)
    window.go("documents")
    view = window.views["documents"]

    assert view.table.rowCount() == 1
    assert "3개 묶음" in view.table.item(0, 0).text()

    view.collapse_dups.setChecked(False)
    assert view.table.rowCount() == 3


def test_documents_view_hides_non_document_files_by_default(make_window, db):
    source_id = db.add_source(r"D:\혼합")
    db.upsert_document(source_id, {
        "path": r"D:\혼합\보고서.hwp", "filename": "보고서.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "a",
    })
    db.upsert_document(source_id, {
        "path": r"D:\혼합\사진.jpg", "filename": "사진.jpg", "ext": ".jpg",
        "parse_status": "skipped", "hash": "b",
    })
    window = make_window(db)
    window.go("documents")
    view = window.views["documents"]

    assert view.table.rowCount() == 1
    view.documents_only.setChecked(False)
    assert view.table.rowCount() == 2
