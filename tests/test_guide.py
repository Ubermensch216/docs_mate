"""첫날 / 첫 주 가이드 시험 (계획서 §18, Phase B).

가이드가 지켜야 할 약속은 셋이다.

  ① 첫날 사람에게 첫 주 것을 시키지 않는다 (단계는 진척으로 넘어간다)
  ② 아직 못 찾은 것을 할 일로 세지 않는다 (0개짜리 할 일은 할 일이 아니다)
  ③ '미분류'가 영원히 끝나지 않는 항목이 되지 않는다 (최근 자료만 센다)
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core import guide, status  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.cycle_format import month_counts  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402

FULL_DAY = {
    "tasks_total": 2, "tasks_done": 2,
    "reading_total": 2, "reading_done": 2,
    "month_total": 1, "month_done": 1,
}


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
    for index, (year, name) in enumerate(
        ((2024, "2024_행정사무감사.hwp"), (2025, "2025_행정사무감사.hwp"),
         (2025, "2025_출장복명서.hwp"), (2019, "2019_옛자료.hwp")),
        start=1,
    ):
        database.upsert_document(source_id, {
            "path": str(tmp_path / name), "filename": name, "ext": ".hwp",
            "parse_status": "ok", "hash": f"h{index}",
            "eff_date": f"{year}-09-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": year, "eff_month": 9,
        })
    database.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 2)")
    database.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 2, 1, '가장 최근 자료입니다')"
    )
    database.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence) "
        "VALUES (1, 'yearly', '9', 2, 'medium')"
    )
    yield database
    database.close()


# ── 단계 ────────────────────────────────────────────────────────────

def test_first_day_hides_the_first_week_items():
    """발령 첫날 사람에게 여섯 줄을 보이면 '오늘 여섯 가지를 하라'로 읽힌다."""
    plan = guide.summarize({
        "tasks_total": 2, "tasks_done": 0,
        "reading_total": 3, "reading_done": 0,
        "month_total": 1, "month_done": 0,
        "cycles_total": 2, "cycles_done": 0,
        "steps_total": 2, "steps_done": 0,
        "strays_total": 4, "strays_done": 0,
    })
    assert plan.stage == guide.DAY
    assert [item.key for item in plan.of_stage(guide.DAY)] == [
        "tasks", "reading", "month"
    ]
    assert plan.next_item().key == "tasks"


def test_the_stage_moves_on_progress_not_on_the_calendar():
    """설치 8일째라고 첫 주로 밀면, 업무 목록도 못 본 사람에게 처리 순서를
    확인하라고 하게 된다."""
    counts = dict(FULL_DAY, cycles_total=2, cycles_done=0, steps_total=1, steps_done=0)
    plan = guide.summarize(counts)

    assert plan.stage == guide.WEEK
    assert plan.next_item().key == "cycles"


def test_everything_confirmed_reaches_the_done_stage():
    plan = guide.summarize(dict(
        FULL_DAY, cycles_total=2, cycles_done=2, steps_total=1, steps_done=1,
        strays_total=3, strays_done=3,
    ))
    assert plan.stage == guide.DONE
    assert plan.next_item() is None
    assert plan.headline() == "인수인계 확인을 마쳤습니다"


def test_areas_with_nothing_to_count_do_not_hold_the_user_in_a_stage():
    """분석이 아직 주기를 못 찾았다고 사용자를 첫날에 묶어 둘 이유는 없다."""
    plan = guide.summarize({"tasks_total": 2, "tasks_done": 2})

    assert plan.stage == guide.DONE
    assert [item.key for item in plan.measured] == ["tasks"]
    assert plan.of_stage(guide.WEEK) == []


def test_nothing_analysed_yet_says_so_instead_of_pretending_to_have_work():
    plan = guide.summarize({})
    assert plan.headline() == "아직 확인할 것이 없습니다"
    assert plan.next_item() is None


def test_next_item_never_jumps_a_stage():
    """첫날 것이 남았는데 처리 순서를 권하면 단계를 나눈 의미가 사라진다."""
    plan = guide.summarize({
        "tasks_total": 2, "tasks_done": 1,      # 50% — 첫 주 것보다 많이 됐다
        "reading_total": 2, "reading_done": 2,
        "month_total": 2, "month_done": 2,
        "steps_total": 4, "steps_done": 0,      # 0% — 비율만 보면 여기가 먼저다
    })
    assert plan.stage == guide.DAY
    assert plan.next_item().key == "tasks"


def test_lines_read_like_the_plan():
    plan = guide.summarize({
        "tasks_total": 7, "tasks_done": 5,
        "reading_total": 18, "reading_done": 12,
        "month_total": 2, "month_done": 0,
    })
    said = [item.sentence() for item in plan.of_stage(guide.DAY)]
    assert said == [
        "업무 7개 중 5개 확인",
        "핵심문서 18건 중 12건 확인",
        "이번 달 업무 2개 확인 필요",
    ]
    assert plan.headline() == "첫날 — 3가지 중 3가지 남음"
    assert plan.items[0].state == status.INFERRED


# ── 이번 달 업무 ────────────────────────────────────────────────────

def test_this_month_counts_tasks_not_cycles(db: Database):
    """한 업무에 주기가 여럿이면 그 업무가 두 번 세어져서는 안 된다."""
    db.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence) "
        "VALUES (1, 'yearly', '10', 2, 'medium')"
    )
    counts = month_counts(db.all_cycles(), date(2026, 9, 1))
    assert counts == {"month_total": 1, "month_done": 0}


def test_this_month_ignores_tasks_that_are_not_running_now(db: Database):
    counts = month_counts(db.all_cycles(), date(2026, 3, 1))
    assert counts["month_total"] == 0


def test_confirming_the_task_completes_the_this_month_item(db: Database):
    db.confirm_task(1)
    counts = month_counts(db.all_cycles(), date(2026, 9, 1))
    assert counts == {"month_total": 1, "month_done": 1}


def test_renaming_also_counts_as_confirmed_here(db: Database):
    """review_state만 보면 옛 열에만 흔적을 남기는 경로에서 확인이 강등된다."""
    db.rename_task(1, "행정사무감사 대응")
    assert month_counts(db.all_cycles(), date(2026, 9, 1))["month_done"] == 1


# ── 미분류 최근 문서 ────────────────────────────────────────────────

def test_strays_count_only_recent_documents(db: Database):
    """미분류 전체를 세면 수천 건짜리 할 일이 되어 영원히 끝나지 않는다.

    자료 최신 연도는 2025년이다. 2019년 문서는 어디에도 안 붙어 있어도
    인수인계에서 확인할 것이 아니다.
    """
    counts = db.stray_counts()
    assert counts["strays_total"] == 1        # 2025_출장복명서만
    assert counts["strays_done"] == 0


def test_assigning_the_document_removes_it_from_the_list(db: Database):
    db.assign_document(1, 3)
    assert db.stray_counts()["strays_total"] == 0


def test_marking_no_task_finishes_the_item_without_hiding_the_document(db: Database):
    db.mark_stray(3)
    counts = db.stray_counts()
    assert counts == {"strays_total": 1, "strays_done": 1}
    assert 3 in db.stray_marks()
    assert any(row["id"] == 3 for row in db.unclassified_documents())

    db.mark_stray(3, False)                   # 되돌릴 수 있어야 한다
    assert db.stray_counts()["strays_done"] == 0


def test_the_mark_is_recorded_without_the_document_body(db: Database):
    db.mark_stray(3)
    row = db.recent_audit(1)[0]
    assert row["action"] == "stray.mark"
    assert row["target"] == "3"


# ── 화면 ────────────────────────────────────────────────────────────

def test_home_shows_the_first_day_checklist(db: Database, qapp, monkeypatch):
    import app.ui.views.tasks as tasks_view

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 1)

    monkeypatch.setattr(tasks_view, "date", FixedDate)
    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any(t.startswith("첫날 —") for t in texts)
        assert any("업무 1개 확인 필요" in t for t in texts)
        assert any("첫 주에 볼 것" in t for t in texts)
        # 첫날 단계에서는 첫 주 항목 자체를 적지 않는다
        assert not any("반복 주기" in t for t in texts)
    finally:
        view.setParent(None)


def test_home_moves_to_the_first_week_after_the_first_day_is_done(
    db: Database, qapp, monkeypatch
):
    import app.ui.views.tasks as tasks_view

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 1)

    monkeypatch.setattr(tasks_view, "date", FixedDate)
    db.confirm_task(1)
    db.mark_reading(2)

    view = TasksView(db)
    try:
        texts = [w.text() for w in view.findChildren(QLabel) if w.text()]
        assert any(t.startswith("첫 주 —") for t in texts)
        assert any("반복 주기 1개 확인 필요" in t for t in texts)
        assert any("미분류 최근 문서 1건 확인 필요" in t for t in texts)
    finally:
        view.setParent(None)


def test_documents_view_offers_the_no_task_mark(db: Database, qapp):
    """미분류 문서를 끝낼 방법이 화면에 있어야 한다. 없으면 그 문서는
    영원히 할 일로 남는다."""
    from PySide6.QtWidgets import QPushButton

    from app.ui.views.documents import DocumentsView

    view = DocumentsView(db)
    try:
        view._show_detail(3)                  # 어느 업무에도 안 붙은 2025년 문서
        button = next(
            b for b in view.findChildren(QPushButton) if b.text() == "업무 없음으로 표시"
        )
        button.click()

        assert db.stray_counts()["strays_done"] == 1
        view._show_detail(3)
        assert any(
            b.text() == "업무 없음 표시 해제" for b in view.findChildren(QPushButton)
        )
    finally:
        view.setParent(None)
