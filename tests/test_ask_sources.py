"""질문 화면 — 근거 대조형 시험 (디자인 개선안 1c 반영).

개선안의 진단은 이랬다. 답은 줄글인데 근거는 파일명 한 줄뿐이라, 답을
믿을지 판단하려면 결국 원본을 열어야 한다. 원본을 여는 순간 화면을 떠난다.
어느 파일이 최신본인지도 화면이 답하지 않는다.

그래서 오른쪽 칸이 **원문 뷰어**가 됐다. 여기서 확인할 것은 셋이다.

  ① 근거를 고르면 그 문서의 원문이 펴지고, 인용된 대목이 강조되는가
  ② '어느 파일이 최신본인가'에 이름이 아니라 수정 시각으로 답하는가
  ③ 모르는 것을 지어내지 않는가 (본문이 없으면 없다고 말하는가)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

from app.db import Database  # noqa: E402
from app.search.rag import Answer, Citation  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.ask import AskView, _cited_ordinals, _freshness  # noqa: E402


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
    # 1: 최신본(사본 둘 중 늦게 저장) / 2: 같은 내용 이전 저장 / 3: 지난 연도
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
    database.replace_sections(1, [
        ("paragraph", 1, "1문단", "본 문서는 2024년 10월 행정사무감사 업무 중 "
                                  "'제출자료' 단계에서 작성되었다."),
        ("paragraph", 2, "2문단", "전년도(2023년) 자료를 참고하여 동일한 형식으로 "
                                  "구성하였다."),
        ("paragraph", 3, "3문단", "제출 목록은 예산 집행 내역, 계약 현황 순으로 편철하였다."),
        ("paragraph", 4, "4문단", "붙임 1. 예산 집행 내역"),
        ("paragraph", 5, "5문단", "붙임 2. 계약 현황"),
        ("paragraph", 6, "6문단", "붙임 3. 위탁 시설 운영 실적"),
    ])
    database.replace_sections(2, [("paragraph", 1, "1문단", "본 문서는 제출자료다.")])
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
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, doc_id) "
        "VALUES (7, 2024, 2, '제출자료', 10, 2)"
    )
    database.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month) "
        "VALUES (7, 2024, 3, '지적사항 조치', 11)"
    )
    yield database
    database.close()


@pytest.fixture
def view(db: Database, qapp):
    widget = AskView(db)
    widget.resize(1400, 860)
    yield widget
    widget.setParent(None)


def _answer(*doc_ids: int, locator: str = "1문단", snippet: str = "") -> Answer:
    return Answer(
        question="행감에 제출했던 자료들의 목록을 알려줘",
        text="제출자료는 3건입니다.[1]",
        withheld=False,
        citations=[
            Citation(index=i, doc_id=doc_id, filename=f"문서{doc_id}", locator=locator,
                     path=f"D:/문서{doc_id}.docx", snippet=snippet)
            for i, doc_id in enumerate(doc_ids, start=1)
        ],
    )


def _buttons(widget) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton) if b.text()]


def _labels(widget) -> list[str]:
    return [w.text() for w in widget.findChildren(QLabel) if w.text()]


def _reader(view: AskView) -> list[str]:
    holder = view.reader_column.parentWidget()
    return [w.text() for w in holder.findChildren(QLabel) if w.text()]


def _picked(view: AskView) -> list[QFrame]:
    return [
        frame for frame in view.findChildren(QFrame)
        if frame.objectName() == "EvidencePick" and frame.property("picked")
    ]


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


def test_version_siblings_gathers_the_files_that_look_like_the_same_thing(db: Database):
    """`_최종`과 `_진짜최종`이 나란히 있는 것이 현장이다."""
    ids = {row["id"] for row in db.version_siblings(1)}
    assert ids == {1, 2}                      # 같은 내용 사본 + 같은 단계
    assert {row["id"] for row in db.version_siblings(3)} == {3}


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
    """붙일 말이 없을 때 붙이는 뱃지는 정보가 아니라 소음이다."""
    label, _kind = _freshness(
        {"copies": 1, "task_latest_year": 2024, "eff_year": 2024, "fs_mtime": "x"}
    )
    assert label == ""


# ── 인용 위치 찾기 ──────────────────────────────────────────────────

def test_the_cited_paragraph_is_found_by_its_locator(db: Database):
    sections = db.sections(1)
    citation = _answer(1, locator="2문단").citations[0]
    assert _cited_ordinals(sections, citation) == {2}


def test_the_cited_paragraph_is_found_by_the_quoted_text(db: Database):
    """locator가 쪽·시트처럼 문단 번호가 아닐 때는 인용문으로 찾는다."""
    sections = db.sections(1)
    citation = _answer(
        1, locator="3쪽", snippet="제출 목록은 예산 집행 내역, 계약 현황 순으로"
    ).citations[0]
    assert _cited_ordinals(sections, citation) == {3}


def test_nothing_is_highlighted_when_the_place_cannot_be_found(db: Database):
    """엉뚱한 대목을 강조하는 것보다 아무것도 칠하지 않는 편이 낫다."""
    sections = db.sections(1)
    citation = _answer(1, locator="7쪽", snippet="없는 문장").citations[0]
    assert _cited_ordinals(sections, citation) == set()


# ── 화면: 근거 대조 ─────────────────────────────────────────────────

def test_the_first_evidence_opens_on_the_right(view: AskView):
    view._render_answer(_answer(1, 2, 3))

    assert view._selected == 1
    assert len(_picked(view)) == 1
    assert any("오른쪽에서 보고 있음" in t for t in _labels(view))
    assert any("본 문서는 2024년 10월" in t for t in _reader(view))


def test_choosing_another_evidence_switches_the_reader(view: AskView):
    view._render_answer(_answer(1, 2, 3))
    view._select(2)

    assert view._selected == 2
    assert any("본 문서는 제출자료다." in t for t in _reader(view))
    assert not any("전년도(2023년)" in t for t in _reader(view))


def test_clicking_a_citation_mark_in_the_answer_opens_that_document(view: AskView):
    view._render_answer(_answer(1, 2, 3))
    view._show_citation("3")
    assert view._selected == 3


def test_the_cited_paragraph_is_highlighted_and_the_rest_folds(view: AskView):
    view._render_answer(_answer(1, locator="1문단"))
    holder = view.reader_column.parentWidget()

    cited = [
        w.text() for w in holder.findChildren(QLabel) if w.objectName() == "ParaCited"
    ]
    assert cited and "제출자료' 단계에서 작성되었다" in cited[0]
    # 문서 전체를 다 펴면 원문 뷰어가 아니라 스크롤 지옥이 된다.
    assert any("접힘" in text for text in _buttons(holder))


def test_folded_paragraphs_can_be_opened(view: AskView):
    view._render_answer(_answer(1, locator="1문단"))
    holder = view.reader_column.parentWidget()
    fold = next(b for b in holder.findChildren(QPushButton) if "접힘" in b.text())
    fold.click()

    assert any("붙임 3. 위탁 시설 운영 실적" in t for t in _reader(view))


def test_a_document_without_text_says_so_instead_of_showing_nothing(view: AskView):
    view._render_answer(_answer(3))
    assert any("본문을 읽지 못해" in t for t in _reader(view))


# ── 화면: 버전 비교 ─────────────────────────────────────────────────

def test_version_tab_answers_which_file_is_the_latest(view: AskView):
    view._render_answer(_answer(1, 2))
    view._switch_reader("versions")

    texts = _reader(view)
    assert any("어느 파일이 최신본인가" in t for t in texts)
    assert any("수정 시각으로 비교했습니다" in t for t in texts)
    assert any("최신본" in t for t in texts)
    assert any("이전 저장" in t for t in texts)
    assert any("10. 14. 17:22" in t for t in texts)      # 이름이 아니라 시각


def test_version_tab_says_when_there_is_only_one_copy(view: AskView):
    view._render_answer(_answer(3))
    view._switch_reader("versions")
    assert any("이 문서 한 벌만 있습니다" in t for t in _reader(view))


# ── 화면: 이 업무의 문서 ────────────────────────────────────────────

def test_task_tab_lists_the_documents_and_leads_to_the_task(view: AskView):
    opened: list[int] = []
    view.open_task.connect(opened.append)
    view._render_answer(_answer(1))
    view._switch_reader("task_docs")

    holder = view.reader_column.parentWidget()
    assert any("행정사무감사의 문서" in t for t in _reader(view))
    next(b for b in holder.findChildren(QPushButton) if b.text() == "업무 열기 →").click()
    assert opened == [7]


def test_the_next_step_panel_points_at_what_comes_after(view: AskView):
    """답 하나로 끝나는 화면은 '그래서 다음은?'에 답하지 못한다."""
    view._render_answer(_answer(1, 2))

    labels = _labels(view)
    assert any("다음 단계까지 보려면" in t for t in labels)
    assert any("지적사항 조치" in t for t in labels)


# ── 화면: 머리 ──────────────────────────────────────────────────────

def test_chips_put_recent_questions_first_and_never_repeat(db: Database, qapp):
    """칩 줄은 늘 네 개다 — 최근 물은 것을 앞에 두고 모자라면 예시로 채운다.

    예전에는 이미 물어본 예시를 지우기만 해서 칩이 하나만 남는 일이 있었다.
    한 개짜리 목록은 목록이 아니고, 같은 문장이 두 번 보이면 고를 것이
    늘어난 것처럼 착각하게 된다.
    """
    from app.ui.views.ask import CHIP_LIMIT, EXAMPLES

    db.save_question("작년 행감 자료 뭐야?", "답", "[]", False, "gemma4:e2b")
    db.save_question(EXAMPLES[0], "답", "[]", False, "gemma4:e2b")
    widget = AskView(db)
    try:
        chips = [
            b.text() for b in widget.findChildren(QPushButton)
            if b.objectName() == "Chip"
        ]
        assert len(chips) == CHIP_LIMIT
        assert len(set(chips)) == len(chips)          # 같은 문장이 두 번 서지 않는다
        assert chips[0] == EXAMPLES[0]                # 가장 최근에 물은 것이 앞
        assert "작년 행감 자료 뭐야?" in chips
        assert "내 질문 기록 2건" in widget.history.text()
        assert "▾" not in widget.history.text()       # 메뉴 화살표는 Qt가 그린다
    finally:
        widget.setParent(None)


def test_question_chips_are_not_clipped(view: AskView):
    """'이번 달에 내가 해야 할 일이…'처럼 잘리면 고를 수 있는 것이 안 읽힌다."""
    from app.ui.views.ask import EXAMPLES

    chips = [
        b.text() for b in view.findChildren(QPushButton) if b.objectName() == "Chip"
    ]
    assert EXAMPLES[0] in chips
    assert not any("…" in text for text in chips)


def test_a_withheld_answer_shows_no_reader(view: AskView):
    view._render_answer(
        Answer(question="질문", text="확인 가능한 자료가 부족합니다.", withheld=True)
    )
    assert view.reader_tabs.isHidden() or not view.reader_tabs.isVisible()
    assert any("부족합니다" in t for t in _labels(view))
