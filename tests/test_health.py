"""업무기억 건강도 시험 (계획서 §19).

"다음 담당자에게 넘기기 전에 무엇이 부족한가"에 답하는 기능이다. 여기서
확인할 것은 셋이다.

  ① 규칙이 사실을 정확히 읽는가 (자료가 있는데 없다고 하지 않는가)
  ② 등급이 **할 일**을 가리키는가 (자료 부족과 확인 필요는 다른 일이다)
  ③ 빠진 것이 없으면 아무 말도 하지 않는가
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core import health  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "project.db")
    database.init()
    source_id = database.add_source(tmp_path / "자료")
    for index, year in enumerate((2024, 2025), start=1):
        database.upsert_document(source_id, {
            "path": str(tmp_path / f"{year}_행정사무감사.hwp"),
            "filename": f"{year}_행정사무감사.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": f"h{index}",
            "eff_date": f"{year}-09-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": year, "eff_month": 9,
        })
    database.con.execute(
        "INSERT INTO tasks(id, name, description) "
        "VALUES (1, '행정사무감사', '의회 요구자료를 취합해 제출합니다')"
    )
    database.con.execute("INSERT INTO tasks(id, name) VALUES (2, '빈업무')")
    database.con.execute(
        "INSERT INTO task_docs(task_id, doc_id, is_primary) VALUES (1, 1, 1)"
    )
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 2)")
    database.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 2, 1, '가장 최근 자료입니다')"
    )
    database.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence) "
        "VALUES (1, 'yearly', '9', 2, 'medium')"
    )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, doc_id) "
        "VALUES (1, 2025, 1, '제출자료', 9, 1)"
    )
    yield database
    database.close()


def _by_name(db: Database) -> dict[str, health.Health]:
    return {item.name: item for item in health.summarize(db.task_health_inputs())}


# ── 규칙 ────────────────────────────────────────────────────────────

def test_it_reads_the_facts_that_are_actually_there(db: Database):
    report = _by_name(db)["행정사무감사"]
    ok = {check.key for check in report.checks if check.ok}

    assert {"description", "documents", "recent", "reading",
            "cycle", "steps", "primary"} <= ok
    assert [check.key for check in report.missing] == ["reviewed"]


def test_confirming_the_task_closes_the_last_gap(db: Database):
    db.confirm_task(1)
    report = _by_name(db)["행정사무감사"]

    assert not report.missing
    assert report.grade == health.GOOD
    assert report.score == 100
    assert report.headline() == "빠진 것이 없습니다"


def test_an_empty_task_is_short_of_material_not_of_attention(db: Database):
    """자료가 없는 것과 사람이 안 본 것은 다른 문제이고 할 일도 다르다."""
    report = _by_name(db)["빈업무"]

    assert report.grade == health.THIN
    assert report.grade_label() == "자료 부족"
    assert {"documents", "reading", "steps"} <= {c.key for c in report.missing}


def test_material_is_there_but_nobody_checked_it(db: Database):
    """자료는 다 있는데 확인만 안 됐으면 '확인 필요'다 — 지금 화면에서 끝난다."""
    report = _by_name(db)["행정사무감사"]
    assert report.grade == health.ATTENTION
    assert report.grade_label() == "확인 필요"


def test_recent_is_measured_against_the_newest_material_not_today(db: Database):
    """2026년에 2025년 자료를 보고 '오래됐다'고 하면 성실히 정리한 사람에게
    틀린 지적을 하는 셈이다."""
    row = {
        "task_id": 1, "name": "옛업무", "description": "설명", "reviewed": 1,
        "doc_count": 3, "has_primary": 1, "reading_count": 1, "has_cycle": 1,
        "step_count": 2, "latest_year": 2021, "this_year": 2025,
    }
    assert not health.evaluate(row).checks[2].ok        # recent

    row["latest_year"] = 2024
    assert health.evaluate(row).checks[2].ok


def test_the_worst_tasks_come_first(db: Database):
    """손댈 곳이 먼저 보여야 한다."""
    order = [item.name for item in health.summarize(db.task_health_inputs())]
    assert order[0] == "빈업무"


def test_the_headline_names_the_heaviest_gap(db: Database):
    report = _by_name(db)["빈업무"]
    # 자료가 없다는 사실이 대표 문서보다 먼저 나와야 한다.
    assert "묶인 문서가 없습니다" in report.headline()
    assert "외 " in report.headline()


# ── 화면 ────────────────────────────────────────────────────────────

def test_the_home_lists_what_is_missing_per_task(db: Database, qapp):
    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any("업무기억 건강도" in t for t in texts)
        assert any("묶인 문서가 없습니다" in t for t in texts)
    finally:
        view.setParent(None)


def test_the_detail_says_what_this_task_still_needs(db: Database, qapp):
    view = TasksView(db)
    try:
        view.open_task(1)
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any("아직 없는 것" in t and "담당자 확인" in t for t in texts)
    finally:
        view.setParent(None)


def test_a_healthy_project_says_so_instead_of_listing_nothing(db: Database, qapp):
    db.confirm_task(1)
    db.con.execute("DELETE FROM tasks WHERE id = 2")
    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any("모든 업무가 넘길 수 있는 상태입니다" in t for t in texts)
    finally:
        view.setParent(None)
