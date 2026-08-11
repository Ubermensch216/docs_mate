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
    """업무는 있지만 자료가 한 해치뿐이면 반복을 주장하지 않는다."""
    source_id = db.add_source(r"D:\한해자료")
    db.con.execute(
        "INSERT INTO tasks(name, origin, status, confidence) "
        "VALUES ('단발업무', 'ai', 'proposed', 'medium')"
    )
    task_id = db.con.execute("SELECT id FROM tasks").fetchone()["id"]
    doc_id = db.upsert_document(source_id, {
        "path": r"D:\한해자료\a.hwp", "filename": "a.hwp", "ext": ".hwp",
        "eff_date": "2024-09-01", "eff_year": 2024, "eff_month": 9,
        "eff_date_kind": "body", "parse_status": "ok", "hash": "x",
    })
    db.con.execute(
        "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
        (task_id, doc_id),
    )
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


def _seed_task(db: Database) -> int:
    """업무 하나와 문서 세 건을 심는다."""
    source_id = db.add_source(r"D:\자료")
    db.con.execute(
        "INSERT INTO tasks(name, description, origin, status, confidence) "
        "VALUES ('행정사무감사', '의회 요구자료를 취합해 제출하는 업무입니다.', "
        "        'ai', 'proposed', 'high')"
    )
    task_id = db.con.execute("SELECT id FROM tasks").fetchone()["id"]
    for index in range(3):
        name = f"202{index + 3}_행정사무감사_제출자료_최종.docx"
        doc_id = db.upsert_document(source_id, {
            "path": rf"D:\자료\{name}", "filename": name, "ext": ".docx",
            "parse_status": "ok", "hash": f"h{index}", "char_count": 900,
            "eff_year": 2023 + index, "eff_month": 10, "eff_date": f"202{index + 3}-10-01",
            "eff_date_kind": "body", "eff_precision": "month",
        })
        db.con.execute(
            "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
            (task_id, doc_id),
        )
        db.con.execute(
            "INSERT INTO task_reading(task_id, doc_id, ordinal, score, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, doc_id, index + 1, 5.0 - index, "'제출' 표기가 있습니다"),
        )
    return task_id


def test_tasks_view_announces_the_discovered_count(make_window, db):
    """첫 화면이 곧 정체성의 답이다 — 파일 개수가 아니라 업무 개수."""
    _seed_task(db)
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("업무는 1개로 추정됩니다" in t for t in texts), texts


def test_task_detail_shows_reading_list_with_reasons(make_window, db):
    """이유 없는 추천은 만들지 않는다."""
    task_id = _seed_task(db)
    window = make_window(db)
    view = window.views["tasks"]
    view.open_task(task_id)

    texts = _labels(view) + _buttons(view)
    assert any("먼저 읽을 문서" in t for t in texts)
    assert any("'제출' 표기가 있습니다" in t for t in texts), texts
    assert any("2025_행정사무감사" in t for t in texts)


def test_task_detail_can_return_to_the_list(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)
    view.back()
    assert any("추정됩니다" in t for t in _labels(view))


def test_renaming_a_task_marks_it_as_user_edited(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    assert db.rename_task(task_id, "의회 대응")
    view.refresh()

    row = db.task(task_id)
    assert row["name"] == "의회 대응"
    assert row["status"] == "edited"


def test_detaching_a_document_updates_the_view(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)
    victim = db.task_documents(task_id)[0]["id"]

    view._detach(task_id, victim)

    assert victim not in {r["id"] for r in db.task_documents(task_id)}


def test_tasks_view_shows_progress_before_any_task_exists(make_window, db):
    _add_docs(db, count=4, parsed=2)
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("살펴보고 있습니다" in t for t in texts), texts
    assert any("업무는 아직 파악하지 못했습니다" in t for t in texts), texts


def test_unclassified_documents_are_shown_not_hidden(make_window, db):
    """미분류가 남는 것은 정상이다. 숨기면 사용자가 속는다."""
    task_id = _seed_task(db)
    db.upsert_document(1, {
        "path": r"D:\자료\정체불명.hwp", "filename": "정체불명.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "zzz",
    })
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("미분류 1건" in t for t in texts), texts


def _labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _buttons(widget) -> list[str]:
    from PySide6.QtWidgets import QPushButton

    return [b.text() for b in widget.findChildren(QPushButton) if b.text()]


def _seed_cycle(db: Database, task_id: int, **overrides) -> None:
    row = dict(kind="yearly", months="9,10,11", day_hint=None,
               years_observed=4, confidence="high", evidence="")
    row.update(overrides)
    db.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, day_hint, "
        "years_observed, confidence, evidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, row["kind"], row["months"], row["day_hint"],
         row["years_observed"], row["confidence"], row["evidence"]),
    )


def test_task_detail_shows_when_section_with_cycle(make_window, db):
    """업무 상세는 한 화면에서 What 다음에 When을 보여준다."""
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    texts = _labels(view)
    assert any("When" in t for t in texts)
    assert any("매년 9~11월" in t for t in texts), texts
    assert any("신뢰도" in t for t in texts)


def test_task_detail_without_cycle_explains_insufficient_data(make_window, db):
    """반복을 찾지 못했으면 없다고 정직하게 말한다 — 지어내지 않는다."""
    task_id = _seed_task(db)   # 문서 3건, 연도 2023~2025지만 주기는 안 심음
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    texts = _labels(view)
    assert any("When" in t for t in texts)
    # 확정된 주기가 없으므로 '🔁 매년 ...' 반복 헤드라인은 뜨지 않아야 한다.
    assert not any(t.startswith("🔁") for t in texts), texts
    assert not any("다음 예상 시점" in t for t in texts)


def test_task_detail_shows_next_occurrence_link_to_calendar(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    signals_received = []
    view.go_calendar.connect(lambda: signals_received.append(True))
    for button in _button_widgets(view):
        if "일정에서 보기" in button.text():
            button.click()
            break
    assert signals_received, "일정으로 가는 링크를 찾지 못했습니다"


def test_calendar_shows_recurring_tasks_when_present(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id, months="8")   # 이번 달(테스트 실행 월과 무관하게 8월로 고정)
    window = make_window(db)
    window.go("calendar")
    texts = _labels(window.views["calendar"])
    assert any("연간 전체" in t for t in texts)
    assert any("행정사무감사" in t for t in texts)


def test_calendar_upcoming_list_excludes_monthly_cycles(make_window, db):
    """매월 반복은 '항상 이번 달'이라 다가오는 일정에 넣지 않는다."""
    task_id = _seed_task(db)
    _seed_cycle(db, task_id, kind="monthly", months="", day_hint="5~10일")
    window = make_window(db)
    window.go("calendar")
    texts = _labels(window.views["calendar"])
    assert any("예정된 반복 일정이 없습니다" in t for t in texts), texts


def test_calendar_open_task_signal_navigates_to_task_detail(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    window = make_window(db)
    window.go("calendar")

    for button in _button_widgets(window.views["calendar"]):
        if "행정사무감사" in button.text():
            button.click()
            break
    assert window.stack.currentWidget() is window.views["tasks"]
    assert window.views["tasks"]._task_id == task_id


def test_calendar_without_any_cycles_shows_insufficient_data_message(make_window, db):
    _add_docs(db, count=4, parsed=4)
    window = make_window(db)
    window.go("calendar")
    texts = _labels(window.views["calendar"])
    assert any("업무 보기" in t or "문서 보기" in t or "아직" in t for t in texts), texts


def _button_widgets(widget):
    from PySide6.QtWidgets import QPushButton

    return [b for b in widget.findChildren(QPushButton) if b.text()]


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
