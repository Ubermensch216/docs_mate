"""인수인계 진행도 시험 (계획서 §18, Phase B).

진행도는 새로 쌓는 자료가 아니라 **교정의 부산물**이다. 그래서 여기서
확인할 것은 두 가지다.

  ① 사용자가 확인·교정한 만큼 정확히 오르는가
  ② 아직 분석이 덜 된 축이 사용자를 질책하지 않는가 (0%로 끌어내리지 않는가)
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.core import handover, status  # noqa: E402
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
    database.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    database.con.execute("INSERT INTO tasks(id, name) VALUES (2, '예산관리')")
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 2)")
    database.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 1, 1, '가장 최근 자료입니다')"
    )
    database.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 2, 2, '처음 요구가 온 문서입니다')"
    )
    database.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence) "
        "VALUES (1, 'yearly', '9', 2, 'medium')"
    )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, doc_id) "
        "VALUES (1, 2025, 1, '요구자료 접수', 9, 1)"
    )
    yield database
    database.close()


# ── 셈 ──────────────────────────────────────────────────────────────

def test_counts_start_at_zero_confirmed(db: Database):
    counts = db.handover_counts()
    assert counts["tasks_total"] == 2 and counts["tasks_done"] == 0
    assert counts["reading_total"] == 2 and counts["reading_done"] == 0
    assert counts["cycles_total"] == 1 and counts["cycles_done"] == 0
    assert counts["steps_total"] == 1 and counts["steps_done"] == 0


def test_confirming_a_task_moves_the_number(db: Database):
    db.confirm_task(1)
    assert db.handover_counts()["tasks_done"] == 1


def test_renaming_a_task_also_counts_as_confirmed(db: Database):
    """사람이 만진 값은 무조건 확인된 것이다(core/status.py의 규칙)."""
    db.rename_task(2, "예산 편성")
    assert db.handover_counts()["tasks_done"] == 1


def test_marking_a_document_read_moves_the_number(db: Database):
    db.mark_reading(1)
    assert db.handover_counts()["reading_done"] == 1

    db.mark_reading(1, False)          # 되돌릴 수 있어야 한다
    assert db.handover_counts()["reading_done"] == 0


def test_reading_mark_is_recorded_without_the_document_body(db: Database):
    db.mark_reading(1)
    row = db.recent_audit(1)[0]
    assert row["action"] == "reading.mark"
    assert row["target"] == "1"


def test_confirming_a_cycle_and_steps_moves_the_numbers(db: Database):
    db.set_task_cycle(1, "yearly", "9,10")
    db.edit_step_label(1, "요구자료 접수 확인")

    counts = db.handover_counts()
    assert counts["cycles_done"] == 1
    assert counts["steps_done"] == 1


def test_a_task_marked_not_a_task_leaves_the_denominator(db: Database):
    """'업무 아님'은 확인해야 할 일이 아니다 — 분모에서 빠져야 한다."""
    db.mark_not_a_task(2)
    assert db.handover_counts()["tasks_total"] == 1


# ── 요약 ────────────────────────────────────────────────────────────

def test_percent_weighs_the_four_areas_equally():
    """문서 수로만 더하면 '문서 읽기 진행도'가 된다 — 업무를 하나도 확인하지
    않고도 90%가 나온다."""
    counts = {
        "tasks_total": 2, "tasks_done": 1,
        "reading_total": 100, "reading_done": 100,
        "cycles_total": 2, "cycles_done": 0,
        "steps_total": 2, "steps_done": 0,
    }
    progress = handover.summarize(counts)
    assert progress.percent == round(100 * (0.5 + 1.0 + 0 + 0) / 4)


def test_areas_with_nothing_to_count_do_not_drag_the_number_down():
    """아직 주기를 못 찾은 상태는 '사용자가 게으른 것'이 아니다."""
    counts = {"tasks_total": 2, "tasks_done": 2,
              "reading_total": 0, "reading_done": 0,
              "cycles_total": 0, "cycles_done": 0,
              "steps_total": 0, "steps_done": 0}
    progress = handover.summarize(counts)

    assert progress.percent == 100
    assert progress.done
    assert [area.key for area in progress.measured] == ["tasks"]


def test_nothing_measured_says_so_instead_of_zero_percent():
    progress = handover.summarize({})
    assert progress.percent == 0
    assert not progress.done
    assert progress.headline() == "아직 확인할 것이 없습니다"
    assert progress.next_step() is None


def test_sentences_read_like_the_plan():
    progress = handover.summarize({
        "tasks_total": 7, "tasks_done": 5,
        "reading_total": 18, "reading_done": 18,
        "cycles_total": 5, "cycles_done": 0,
        "steps_total": 0, "steps_done": 0,
    })
    said = [area.sentence() for area in progress.areas]
    assert "업무 7개 중 5개 확인" in said
    assert "핵심문서 18건 모두 확인" in said
    assert "반복 업무 5개 확인 필요" in said
    assert progress.headline().startswith("인수인계 진행도")


def test_area_states_use_the_shared_status_vocabulary():
    progress = handover.summarize({
        "tasks_total": 2, "tasks_done": 2,
        "reading_total": 2, "reading_done": 1,
        "cycles_total": 2, "cycles_done": 0,
        "steps_total": 0, "steps_done": 0,
    })
    by_key = {area.key: area.state for area in progress.areas}
    assert by_key == {
        "tasks": status.CONFIRMED, "reading": status.INFERRED,
        "cycles": status.WEAK, "steps": status.UNKNOWN,
    }


def test_next_step_prefers_the_order_a_person_actually_works_in():
    """비율이 같으면 업무 → 핵심문서 → 반복 → 순서. 임의 기준(이름순)으로
    고르면 '왜 이것부터?'에 답할 수 없다."""
    progress = handover.summarize({
        "tasks_total": 2, "tasks_done": 1,
        "reading_total": 2, "reading_done": 0,
        "cycles_total": 2, "cycles_done": 0,
        "steps_total": 2, "steps_done": 0,
    })
    assert progress.next_step().key == "reading"


# ── 화면 ────────────────────────────────────────────────────────────

def test_start_here_lists_what_to_check_first(db: Database, qapp):
    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any("지금 먼저 확인할 것" in t for t in texts)
        assert any("아직 확인하지 않은 업무 2개" in t for t in texts)
        assert any("아직 읽지 않은 핵심 문서 2건" in t for t in texts)
        assert any("인수인계 진행도" in t for t in texts)
    finally:
        view.setParent(None)


def test_start_here_disappears_when_everything_is_checked(db: Database, qapp, monkeypatch):
    """할 일이 없는데 '할 일' 판이 남아 있으면 매번 읽고 지나가야 한다.

    시점을 9월에서 멀리 잡는다 — 다가오는 반복 업무는 확인 여부와 무관하게
    올라오는 항목이라(계획서 §7.1) 그것까지 사라지길 기대하면 안 된다.
    """
    import app.ui.views.tasks as tasks_view

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 1)

    monkeypatch.setattr(tasks_view, "date", FixedDate)
    db.confirm_task(1)
    db.confirm_task(2)
    db.mark_reading(1)
    db.mark_reading(2)
    db.con.execute("UPDATE task_cycles SET decided_by = 'user'")
    db.edit_step_label(1, "요구자료 접수 확인")   # 첫 주 항목까지 마쳐야 판이 사라진다

    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert not any("지금 먼저 확인할 것" in t for t in texts)
    finally:
        view.setParent(None)


def test_upcoming_tasks_show_up_in_start_here(db: Database, qapp, monkeypatch):
    """일정 화면과 같은 판정을 써야 한다 — 두 화면이 다른 말을 하면 못 믿는다."""
    import app.ui.views.tasks as tasks_view

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 1)          # 9월 반복 업무가 '지금'인 시점

    monkeypatch.setattr(tasks_view, "date", FixedDate)
    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any("이번 달 안에 시작될 업무 1개" in t for t in texts)
        assert any("행정사무감사" in t for t in texts)
    finally:
        view.setParent(None)


def test_marking_read_from_the_reading_tab_raises_the_progress(db: Database, qapp):
    view = TasksView(db)
    try:
        view.open_task(1)
        before = db.handover_counts()["reading_done"]

        button = next(
            b for b in view.pages["read"].findChildren(QPushButton)
            if b.text() == "읽음 표시"
        )
        button.click()

        assert db.handover_counts()["reading_done"] == before + 1
        assert any(
            b.text() == "읽음" for b in view.pages["read"].findChildren(QPushButton)
        )
    finally:
        view.setParent(None)
