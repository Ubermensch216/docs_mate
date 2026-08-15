"""질문 화면의 '찾은 문서' 시험 (디자인 개선안 반영).

개선안의 진단은 이랬다 — 답은 줄글인데 질문은 목록을 요구했고, 어느 파일이
최신본인지 화면이 답하지 않으며, "업무 중 '제출자료' 단계"라는 구조가
텍스트로만 있어 그 업무로 갈 수 없다.

그래서 **모델이 만든 문장은 그대로 두고, 우리가 아는 사실을 표로 덧붙인다.**
여기서 확인할 것은 그 사실이 정확한가와, 모르는 것을 지어내지 않는가다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.db import Database  # noqa: E402
from app.search.rag import Answer, Citation  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.ask import AskView, _freshness  # noqa: E402


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
    # 1: 최신 연도 · 사본 둘 중 늦게 저장된 것 / 2: 같은 내용 사본 / 3: 지난 연도
    database.upsert_document(source_id, {
        "path": str(tmp_path / "2024_행정사무감사_제출자료_진짜최종.docx"),
        "filename": "2024_행정사무감사_제출자료_진짜최종.docx", "ext": ".docx",
        "parse_status": "ok", "hash": "SAME", "fs_mtime": "2024-10-14T17:22:00",
        "eff_date": "2024-10-12", "eff_precision": "day", "eff_date_kind": "body",
        "eff_year": 2024, "eff_month": 10,
    })
    database.upsert_document(source_id, {
        "path": str(tmp_path / "2024_행정사무감사_제출자료_최종.hwpx"),
        "filename": "2024_행정사무감사_제출자료_최종.hwpx", "ext": ".hwpx",
        "parse_status": "ok", "hash": "SAME", "fs_mtime": "2024-10-12T09:40:00",
        "eff_date": "2024-10-12", "eff_precision": "day", "eff_date_kind": "body",
        "eff_year": 2024, "eff_month": 10,
    })
    database.upsert_document(source_id, {
        "path": str(tmp_path / "2022_행정사무감사_제출자료_최종.docx"),
        "filename": "2022_행정사무감사_제출자료_최종.docx", "ext": ".docx",
        "parse_status": "ok", "hash": "OLD", "fs_mtime": "2022-10-12T09:00:00",
        "eff_date": "2022-10-12", "eff_precision": "day", "eff_date_kind": "body",
        "eff_year": 2022, "eff_month": 10,
    })
    database.con.execute("INSERT INTO tasks(id, name) VALUES (7, '행정사무감사')")
    for doc_id in (1, 2, 3):
        database.con.execute(
            "INSERT INTO task_docs(task_id, doc_id) VALUES (7, ?)", (doc_id,)
        )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, doc_id) "
        "VALUES (7, 2024, 1, '제출자료', 10, 1)"
    )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month) "
        "VALUES (7, 2024, 2, '지적사항 조치', 11)"
    )
    yield database
    database.close()


def _answer(*doc_ids: int) -> Answer:
    return Answer(
        question="행감에 제출했던 자료들의 목록을 알려줘",
        text="제출자료는 3건입니다.[1]",
        withheld=False,
        citations=[
            Citation(index=i, doc_id=doc_id, filename=f"문서{doc_id}", locator="1문단",
                     path=f"D:/문서{doc_id}.docx", snippet="본 문서는 …")
            for i, doc_id in enumerate(doc_ids, start=1)
        ],
    )


def _buttons(widget) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton) if b.text()]


def _labels(widget) -> list[str]:
    return [w.text() for w in widget.findChildren(QLabel) if w.text()]


# ── 문서 맥락 (DB) ──────────────────────────────────────────────────

def test_context_knows_the_task_the_step_and_the_copies(db: Database):
    context = db.document_context([1, 3])

    assert context[1]["task_name"] == "행정사무감사"
    assert context[1]["step_label"] == "제출자료"
    assert context[1]["copies"] == 2
    assert context[1]["task_latest_year"] == 2024
    assert context[3]["step_label"] is None      # 단계에 안 걸린 문서는 비워 둔다


def test_context_ignores_documents_that_do_not_exist(db: Database):
    assert db.document_context([999]) == {}
    assert db.document_context([]) == {}


# ── 최신본 판정 ─────────────────────────────────────────────────────

def test_the_newest_of_identical_copies_is_marked(db: Database):
    label, kind = _freshness(db.document_context([1])[1])
    assert label == "같은 사본 2개 중 최신"
    assert kind == "ok"


def test_the_older_copy_is_flagged_as_a_duplicate(db: Database):
    label, kind = _freshness(db.document_context([2])[2])
    assert "같은 내용 사본" in label
    assert kind == "attention"


def test_an_older_year_says_a_newer_one_exists(db: Database):
    label, _kind = _freshness(db.document_context([3])[3])
    assert label == "2024년 자료 있음"


def test_nothing_is_claimed_when_there_is_nothing_to_claim():
    """사본도 없고 최신 연도인 문서에는 뱃지를 붙이지 않는다 —
    붙일 말이 없을 때 붙이는 뱃지는 정보가 아니라 소음이다."""
    label, _kind = _freshness(
        {"copies": 1, "task_latest_year": 2024, "eff_year": 2024, "fs_mtime": "x"}
    )
    assert label == ""


# ── 화면 ────────────────────────────────────────────────────────────

def test_answer_lists_the_documents_it_used(db: Database, qapp):
    view = AskView(db)
    try:
        view._render_answer(_answer(1, 2, 3))

        labels = _labels(view)
        buttons = _buttons(view)
        assert any("찾은 문서 3건" in t for t in labels)
        assert "2024_행정사무감사_제출자료_진짜최종.docx" in buttons
        assert "행정사무감사 › 제출자료" in buttons      # 업무 › 단계 경로
        assert any("2024. 10. 12." in t for t in labels)
        assert any("같은 사본 2개 중 최신" in t for t in labels)
    finally:
        view.setParent(None)


def test_the_task_path_leads_to_the_task(db: Database, qapp):
    """텍스트로만 적혀 있으면 '그래서 그 업무가 어디 있는데?'로 끝난다."""
    view = AskView(db)
    opened: list[int] = []
    view.open_task.connect(opened.append)
    try:
        view._render_answer(_answer(1))
        crumb = next(
            b for b in view.findChildren(QPushButton) if b.text() == "행정사무감사 › 제출자료"
        )
        crumb.click()
        assert opened == [7]
    finally:
        view.setParent(None)


def test_the_flow_of_the_task_fills_the_space_below_the_answer(db: Database, qapp):
    view = AskView(db)
    try:
        view._render_answer(_answer(1, 2))

        labels = _labels(view)
        assert any("이 업무의 흐름" in t for t in labels)
        assert any("지적사항 조치" in t for t in labels)   # 다음 단계까지 보인다
        assert any("지금 보는 단계" in t for t in labels)
    finally:
        view.setParent(None)


def test_a_withheld_answer_shows_no_source_table(db: Database, qapp):
    view = AskView(db)
    try:
        view._render_answer(
            Answer(question="질문", text="확인 가능한 자료가 부족합니다.", withheld=True)
        )
        assert not any("찾은 문서" in t for t in _labels(view))
    finally:
        view.setParent(None)


def test_example_questions_do_not_repeat_what_was_already_asked(db: Database, qapp):
    """아홉 칸 중 실제 선택지가 다섯이던 문제 — 같은 문장을 두 목록에 올리지 않는다."""
    from app.ui.views.ask import EXAMPLES

    db.save_question(EXAMPLES[0][0], "답", "[]", False, "gemma4:e2b")
    view = AskView(db)
    try:
        chips = [
            w.text() for w in view.guide.findChildren(QLabel)
            if w.objectName() == "ChipText"
        ]
        assert chips.count(EXAMPLES[0][0]) == 1
    finally:
        view.setParent(None)


def test_question_chips_are_not_clipped(db: Database, qapp):
    """'이번 달에 내가 해야 할 일이…'처럼 잘리면 고를 수 있는 것이 안 읽힌다."""
    from app.ui.views.ask import EXAMPLES

    view = AskView(db)
    try:
        chips = [
            w.text() for w in view.guide.findChildren(QLabel)
            if w.objectName() == "ChipText"
        ]
        assert EXAMPLES[0][0] in chips
        assert not any("…" in text for text in chips)
    finally:
        view.setParent(None)
