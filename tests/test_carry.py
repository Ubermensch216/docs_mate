"""올해 처리 결과 반영 시험 (계획서 §21, Phase B).

이 기능이 만드는 것은 순환이다 — **과거 기록에서 복원 → 사람이 수행 →
다시 업무기억으로 축적**. 그래서 확인할 것도 그 순환의 이음매다.

  ① 사람이 확정한 해가 다음에 열 때 기본으로 뜨는가 (안 그러면 확정이 헛일)
  ② 가져온 단계가 '자료에서 읽은 것'인 척하지 않는가
  ③ 올해 쌓은 것을 가져오기가 덮어쓰지 않는가
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.core.timeline import default_how_year  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402

THIS_YEAR = date.today().year
LAST_YEAR = THIS_YEAR - 1


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
    database.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    for index, label in enumerate(("자료 요청", "취합", "제출"), start=1):
        name = f"{LAST_YEAR}_{label}.hwp"
        database.upsert_document(source_id, {
            "path": str(tmp_path / name), "filename": name, "ext": ".hwp",
            "parse_status": "ok", "hash": f"h{index}",
            "eff_date": f"{LAST_YEAR}-{index + 8:02d}-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": LAST_YEAR, "eff_month": index + 8,
        })
        database.con.execute(
            "INSERT INTO task_docs(task_id, doc_id) VALUES (1, ?)", (index,)
        )
        database.con.execute(
            "INSERT INTO task_steps(task_id, year, ordinal, label, month, day_hint, "
            "doc_id) VALUES (1, ?, ?, ?, ?, ?, ?)",
            (LAST_YEAR, index, label, index + 8, f"{index + 8}월 초", index),
        )
    yield database
    database.close()


# ── 확정 ────────────────────────────────────────────────────────────

def test_confirming_a_year_claims_every_step_at_once(db: Database):
    """순서는 단계 하나가 아니라 줄 전체가 의미다."""
    assert db.confirm_task_steps(1, LAST_YEAR) == 3
    steps = db.task_steps(1, LAST_YEAR)
    assert all(step["decided_by"] == "user" for step in steps)


def test_confirming_raises_the_handover_progress(db: Database):
    before = db.handover_counts()["steps_done"]
    db.confirm_task_steps(1, LAST_YEAR)
    assert db.handover_counts()["steps_done"] == before + 1


def test_confirming_a_year_without_steps_does_nothing(db: Database):
    assert db.confirm_task_steps(1, THIS_YEAR) == 0
    assert db.confirmed_step_years(1) == []


def test_the_confirmation_is_recorded_for_undo_and_audit(db: Database):
    db.confirm_task_steps(1, LAST_YEAR)
    row = db.recent_audit(1)[0]
    assert row["action"] == "step.confirm_year"
    assert row["target"] == "1"


def test_a_confirmed_year_wins_the_default_view():
    """확정해 두고 다음에 열었을 때 작년이 떠 있으면 그 확정은 아무 데도
    쓰이지 않은 것이 된다."""
    years = [2024, 2025, 2026]
    assert default_how_year(years, today=date(2026, 8, 18)) == 2025
    assert default_how_year(
        years, today=date(2026, 8, 18), confirmed=[2026]
    ) == 2026


def test_a_confirmation_for_a_year_we_no_longer_have_is_ignored():
    assert default_how_year([2025], today=date(2026, 8, 18), confirmed=[2019]) == 2025


# ── 가져오기 ────────────────────────────────────────────────────────

def test_carrying_forward_copies_the_order_and_the_timing(db: Database):
    assert db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR) == 3
    carried = db.task_steps(1, THIS_YEAR)
    assert [step["label"] for step in carried] == ["자료 요청", "취합", "제출"]
    assert [step["month"] for step in carried] == [9, 10, 11]


def test_carried_steps_do_not_pretend_to_have_evidence(db: Database):
    """올해 문서에서 읽은 것이 아니라 작년 것을 옮긴 칸이다."""
    db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR)
    carried = db.task_steps(1, THIS_YEAR)
    assert all(step["doc_id"] is None for step in carried)
    assert all(step["is_inferred"] for step in carried)
    assert all(step["decided_by"] == "user" for step in carried)


def test_carrying_forward_never_overwrites_what_this_year_already_has(db: Database):
    db.add_step(1, THIS_YEAR, "올해 새로 생긴 단계")
    assert db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR) == 0
    assert [step["label"] for step in db.task_steps(1, THIS_YEAR)] == [
        "올해 새로 생긴 단계"
    ]


def test_reanalysis_does_not_wipe_the_carried_flow(db: Database):
    """가져온 순서는 사람 것이다. 올해 문서가 뒤늦게 들어와도 지우지 않는다."""
    db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR)
    db.replace_task_steps(1, THIS_YEAR, [
        {"ordinal": 1, "label": "AI가 새로 찾은 단계", "month": 3,
         "day_hint": "3월 초", "doc_id": 1, "gap_note": None},
    ])
    assert [step["label"] for step in db.task_steps(1, THIS_YEAR)] == [
        "자료 요청", "취합", "제출"
    ]


# ── 화면 ────────────────────────────────────────────────────────────

def _how_page(view: TasksView):
    view.open_task(1)
    view._tab = "how"
    view.refresh()
    return view.pages["how"]


def test_the_how_tab_offers_to_carry_last_year_forward(db: Database, qapp):
    view = TasksView(db)
    try:
        page = _how_page(view)
        view._change_how_year(1, THIS_YEAR)
        page = view.pages["how"]

        button = next(
            b for b in page.findChildren(QPushButton)
            if b.text() == f"{LAST_YEAR}년 흐름 가져오기"
        )
        button.click()

        assert len(db.task_steps(1, THIS_YEAR)) == 3
        texts = [w.text() for w in view.pages["how"].findChildren(QLabel) if w.text()]
        assert any("자료 요청" in t for t in texts)
    finally:
        view.setParent(None)


def test_the_confirmed_year_is_what_opens_next_time(db: Database, qapp):
    """올해는 문서가 없어 '자료가 있는 해' 목록에 없다. 그래도 사람이 확정한
    해가 기본으로 떠야 한다 — 아니면 확정은 아무 데도 쓰이지 않는다."""
    db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR)
    view = TasksView(db)
    try:
        page = _how_page(view)
        texts = [w.text() for w in page.findChildren(QLabel) if w.text()]
        assert any(t.startswith(f"{THIS_YEAR}년 순서는") for t in texts)
        assert not any(t.startswith(f"{LAST_YEAR}년") for t in texts)
    finally:
        view.setParent(None)


def test_a_carried_year_does_not_claim_to_come_from_documents(db: Database, qapp):
    """가져온 순서에 "자료에서 이렇게 보입니다"라고 하면, 이 제품이 가장
    조심해 온 것을 스스로 하는 셈이다."""
    db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR)
    view = TasksView(db)
    try:
        page = _how_page(view)
        texts = [w.text() for w in page.findChildren(QLabel) if w.text()]
        assert f"{THIS_YEAR}년 순서는 자료가 아니라 사람이 적어 둔 것입니다" in texts
        assert not any("이렇게 처리한 것으로 보입니다" in t for t in texts)
    finally:
        view.setParent(None)


def test_the_how_tab_confirms_this_years_flow_in_one_press(db: Database, qapp):
    db.carry_steps_forward(1, LAST_YEAR, THIS_YEAR)
    db.con.execute("UPDATE task_steps SET decided_by = 'ai' WHERE year = ?",
                   (THIS_YEAR,))
    view = TasksView(db)
    try:
        _how_page(view)
        view._change_how_year(1, THIS_YEAR)

        button = next(
            b for b in view.pages["how"].findChildren(QPushButton)
            if b.text() == "올해 처리 결과로 반영"
        )
        button.click()

        assert db.confirmed_step_years(1) == [THIS_YEAR]
        texts = [w.text() for w in view.pages["how"].findChildren(QLabel) if w.text()]
        assert any("담당자가 확인했습니다" in t for t in texts)
    finally:
        view.setParent(None)


def test_a_past_year_asks_to_confirm_in_its_own_words(db: Database, qapp):
    """작년 것은 자료를 보고 '맞다'고 인정하는 일이고, 올해 것은 본인이
    실제로 처리한 결과다. 같은 말을 쓰면 뒤의 뜻이 사라진다."""
    view = TasksView(db)
    try:
        page = _how_page(view)
        labels = [b.text() for b in page.findChildren(QPushButton)]
        assert f"{LAST_YEAR}년 순서 확인함" in labels
        assert "올해 처리 결과로 반영" not in labels
    finally:
        view.setParent(None)
