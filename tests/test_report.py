"""인수인계 보고서 시험 (계획서 §33, PRD §10.9 · Phase B).

보고서가 지켜야 할 약속은 셋이다.

  ① 확인한 것과 추정한 것이 같은 활자로 앉지 않는다 (RPT-003)
  ② 사실성 문장에는 근거 문서 이름이 붙는다
  ③ 아직 정리되지 않은 것을 빼서 실제보다 깔끔해 보이게 하지 않는다
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.core import report  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402

TODAY = date(2026, 8, 18)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "기본" / "project.db")
    database.init()
    source_id = database.add_source(tmp_path / "자료")
    folder = tmp_path / "자료" / "행정사무감사"
    for index, year in enumerate((2024, 2025), start=1):
        name = f"{year}_행정사무감사_제출자료.hwp"
        database.upsert_document(source_id, {
            "path": str(folder / name), "filename": name, "ext": ".hwp",
            "parse_status": "ok", "hash": f"h{index}",
            "eff_date": f"{year}-09-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": year, "eff_month": 9,
        })
    # 어느 업무에도 안 붙는 문서 하나 — 보고서가 이것을 숨기면 안 된다.
    database.upsert_document(source_id, {
        "path": str(tmp_path / "자료" / "기타" / "2025_출장복명서.hwp"),
        "filename": "2025_출장복명서.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "h9",
        "eff_date": "2025-05-01", "eff_date_kind": "body",
        "eff_precision": "day", "eff_year": 2025, "eff_month": 5,
    })
    database.con.execute(
        "INSERT INTO tasks(id, name, description) "
        "VALUES (1, '행정사무감사', '자료 제출과 답변을 다룹니다')"
    )
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")
    database.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 2)")
    database.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 2, 1, '가장 최근 자료입니다')"
    )
    database.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence) "
        "VALUES (1, 'yearly', '9,10', 2, 'medium')"
    )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, day_hint, "
        "doc_id) VALUES (1, 2025, 1, '제출', 9, '9월 초', 2)"
    )
    yield database
    database.close()


# ── 내용 ────────────────────────────────────────────────────────────

def test_the_report_marks_where_each_line_came_from(db: Database):
    """표시를 지우면 읽는 사람은 전부 사실로 읽는다."""
    text = report.build(db, project="기본", today=TODAY)
    assert report.LEGEND in text
    assert "◐ 매년 9월, 10월" in text          # 아직 사람이 확인 안 한 주기
    db.confirm_task_cycle(1)
    assert "✓ 매년 9월, 10월" in report.build(db, project="기본", today=TODAY)


def test_reading_order_keeps_its_reason(db: Database):
    """이유 없는 순번은 신뢰를 만들지 못한다 — 화면에서 붙인 이유가 함께 가야 한다."""
    text = report.build(db, project="기본", today=TODAY)
    assert "1. `2025_행정사무감사_제출자료.hwp` — 가장 최근 자료입니다" in text


def test_each_step_names_the_document_it_came_from(db: Database):
    text = report.build(db, project="기본", today=TODAY)
    assert "1. 제출 (9월 초) — `2025_행정사무감사_제출자료.hwp`" in text


def test_a_hand_written_flow_does_not_claim_to_come_from_documents(db: Database):
    """가져오거나 손으로 적은 순서에 근거가 있는 척하면 안 된다 (§21과 같은 규칙)."""
    db.carry_steps_forward(1, 2025, 2026)
    text = report.build(db, project="기본", today=TODAY)
    assert "이 해 순서는 자료가 아니라 사람이 적어 둔 것입니다." in text
    assert "근거 문서 없음" in text


def test_the_confirmed_year_is_the_one_that_gets_handed_over(db: Database):
    """확정한 해를 두고 더 오래된 해를 실으면 낡은 절차를 물려주는 일이 된다."""
    db.carry_steps_forward(1, 2025, 2026)
    assert "**어떻게 처리했나** (2026년)" in report.build(db, today=TODAY)


def test_the_report_says_what_is_still_unconfirmed(db: Database):
    text = report.build(db, project="기본", today=TODAY)
    assert "## 확인 상태" in text
    assert "아직 손볼 곳이 있는 업무" in text
    assert "아직 확인되지 않은 것 —" in text


def test_the_report_does_not_hide_the_leftovers(db: Database):
    """미분류가 남는 것은 정상이지만, 넘겨받는 사람도 그 사실을 알아야 한다."""
    text = report.build(db, project="기본", today=TODAY)
    assert "어느 업무에도 넣지 못한 문서가 1건 있습니다." in text


def test_the_report_tells_where_the_files_actually_are(db: Database):
    """후임자가 가장 먼저 묻는 것 중 하나 — "그 파일들 어디 있어요?"."""
    text = report.build(db, project="기본", today=TODAY)
    assert "**자료가 있는 곳**" in text
    assert "행정사무감사` (2건)" in text


def test_the_yearly_calendar_is_a_table_of_twelve_months(db: Database):
    text = report.build(db, project="기본", today=TODAY)
    assert "## 연간 일정" in text
    assert "| 9월 | 행정사무감사 |" in text
    assert "| 1월 | — |" in text


def test_the_report_repeats_the_promises_the_product_makes(db: Database):
    text = report.build(db, project="기본", today=TODAY)
    assert "원본 파일은 하나도 고치지 않았습니다" in text
    assert "문서를 외부로 보내지 않았습니다" in text


def test_a_project_without_tasks_says_so_instead_of_printing_a_shell(tmp_path: Path):
    empty = Database(tmp_path / "빈" / "project.db")
    empty.init()
    try:
        text = report.build(empty, project="빈", today=TODAY)
        assert "아직 파악된 업무가 없습니다." in text
        assert "## 연간 일정" not in text
    finally:
        empty.close()


# ── 내보내기 ────────────────────────────────────────────────────────

def test_writing_the_report_leaves_a_file_and_an_audit_line(db: Database, qapp, tmp_path):
    view = TasksView(db)
    try:
        out = tmp_path / "보고서.md"
        view._write_report(str(out))

        text = out.read_text(encoding="utf-8-sig")
        assert text.startswith("# 기본 인수인계 보고서")
        assert "행정사무감사" in text
        # 한글 Windows의 메모장이 UTF-8을 못 알아보는 일이 잦다.
        assert out.read_bytes().startswith(b"\xef\xbb\xbf")

        row = db.recent_audit(1)[0]
        assert row["action"] == "report.export"
    finally:
        view.setParent(None)


def test_the_home_screen_offers_the_report(db: Database, qapp):
    view = TasksView(db)
    try:
        assert any(
            b.text() == "보고서 만들기" for b in view.findChildren(QPushButton)
        )
    finally:
        view.setParent(None)
