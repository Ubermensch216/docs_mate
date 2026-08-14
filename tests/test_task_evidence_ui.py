"""업무 화면의 근거 서랍 시험 (계획서 §14·§29).

질문 화면에서만 쓰던 서랍을 업무 설명·반복 주기·처리 단계·먼저 읽을 문서로
넓혔다. 확인할 것은 두 가지다.

  ① 주장 옆에 근거로 가는 길이 실제로 있는가
  ② 그 근거가 **그 주장의** 근거인가 (다른 업무·다른 탭의 것이 남지 않는가)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views import evidence  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture()
def view(tmp_path: Path, qapp):
    db = Database(tmp_path / "project.db")
    db.init()
    source_id = db.add_source(tmp_path / "자료")
    for index, year in enumerate((2023, 2024, 2025), start=1):
        doc_id = db.upsert_document(source_id, {
            "path": str(tmp_path / f"{year}_행정사무감사.hwp"),
            "filename": f"{year}_행정사무감사.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": f"h{index}",
            "eff_date": f"{year}-09-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": year, "eff_month": 9,
        })
        db.replace_sections(doc_id, [
            ("paragraph", 1, f"{index}쪽", f"{year}년 행정사무감사 요구자료입니다"),
        ])

    db.con.execute(
        "INSERT INTO tasks(id, name, description, confidence) "
        "VALUES (1, '행정사무감사', '해마다 9월에 요구자료를 준비합니다', 'high')"
    )
    for doc_id in (1, 2, 3):
        db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, ?)", (doc_id,))
    db.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, confidence, "
        "evidence) VALUES (1, 'yearly', '9', 3, 'high', '1,2,3')"
    )
    db.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, doc_id) "
        "VALUES (1, 2025, 1, '요구자료 제출', 9, 3)"
    )
    db.con.execute(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, reason) "
        "VALUES (1, 3, 1, '가장 최근 자료입니다')"
    )

    widget = TasksView(db)
    yield widget
    widget.setParent(None)
    db.close()


def _buttons(widget) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if b.text()]


def _labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _click(widget, text: str) -> None:
    """**보고 있는 화면 안에서만** 누른다.

    view 전체에서 찾으면 다른 탭의 같은 이름 버튼이 먼저 잡힌다 — 실제로
    '근거 3건'이 설명과 주기 양쪽에 있고, 문서 탭의 파일명 버튼은 원본을
    열어 모달 창을 띄운다(시험이 그대로 멈춘다).
    """
    for button in _buttons(widget):
        if text in button.text():
            button.click()
            return
    raise AssertionError(f"'{text}' 버튼이 없다: {[b.text() for b in _buttons(widget)]}")


def _head(view: TasksView):
    """머리 띠(제목·상태·설명)가 담긴 위젯."""
    return view.head.parentWidget()


# ── 근거로 가는 길이 있는가 ─────────────────────────────────────────

def test_cycle_claim_offers_its_evidence(view: TasksView):
    view.open_task(1)
    view._switch("when")

    _click(view.pages["when"], "근거 3건")

    assert not view.drawer.isHidden()
    texts = _labels(view.drawer)
    assert any("2023_행정사무감사.hwp" in t for t in texts)
    # 왜 근거인지는 서랍 제목이 말한다 — 같은 문장을 건마다 되풀이하지 않는다.
    assert any("반복 주기의 근거 3건" in t for t in texts)


def test_step_claim_offers_its_anchor_document(view: TasksView):
    view.open_task(1)
    view._switch("how")

    _click(view.pages["how"], "2025_행정사무감사.hwp")

    texts = _labels(view.drawer)
    assert any("요구자료 제출" in t for t in texts)          # 왜 이것이 근거인가
    assert any("2025년 행정사무감사 요구자료입니다" in t for t in texts)   # 실제 인용문


def test_task_description_offers_the_documents_it_came_from(view: TasksView):
    view.open_task(1)

    _click(_head(view), "근거 3건")

    texts = _labels(view.drawer)
    assert any("업무 설명의 근거 3건" in t for t in texts)
    assert any("2025_행정사무감사.hwp" in t for t in texts)


def test_reading_pick_shows_the_document_and_the_reason(view: TasksView):
    view.open_task(1)
    view._switch("read")

    _click(view.pages["read"], "2025_행정사무감사.hwp")

    texts = _labels(view.drawer)
    assert any("가장 최근 자료입니다" in t for t in texts)
    assert any("2025년 행정사무감사 요구자료입니다" in t for t in texts)


def test_user_written_description_gets_no_ai_evidence(view: TasksView):
    """사람이 적은 설명에 AI의 근거를 붙이면 거짓말이 된다."""
    view.db.edit_task_description(1, "내가 직접 적은 설명")
    view.open_task(1)

    assert not any("근거" in b.text() for b in _buttons(_head(view)))
    assert evidence.for_task(view.db, view.db.task(1), view.db.task_documents(1)) == []


# ── 남의 근거가 남지 않는가 ─────────────────────────────────────────

def test_drawer_closes_when_the_tab_changes(view: TasksView):
    view.open_task(1)
    view._switch("when")
    _click(view.pages["when"], "근거 3건")
    assert not view.drawer.isHidden()

    view._switch("how")
    assert view.drawer.isHidden()


def test_drawer_closes_when_going_back_to_the_list(view: TasksView):
    view.open_task(1)
    view._switch("when")
    _click(view.pages["when"], "근거 3건")

    view.back()
    assert view.drawer.isHidden()


# ── 근거를 만드는 규칙 ──────────────────────────────────────────────

def test_evidence_keeps_the_order_it_was_given(view: TasksView):
    items = evidence.from_documents(view.db, [3, 1])
    assert [item.label for item in items] == [
        "2025_행정사무감사.hwp", "2023_행정사무감사.hwp"
    ]


def test_missing_document_is_dropped_not_faked(view: TasksView):
    items = evidence.from_documents(view.db, [1, 999])
    assert [item.label for item in items] == ["2023_행정사무감사.hwp"]


def test_document_without_text_gets_no_invented_snippet(view: TasksView, tmp_path: Path):
    source_id = view.db.sources()[0]["id"]
    doc_id = view.db.upsert_document(source_id, {
        "path": str(tmp_path / "그림.pdf"), "filename": "그림.pdf",
        "ext": ".pdf", "parse_status": "empty",
    })

    item = evidence.from_documents(view.db, [doc_id])[0]
    assert item.snippet == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,2,3", [1, 2, 3]),
        ("[1, 2]", [1, 2]),           # 옛 자료의 JSON 배열
        ('["4"]', [4]),
        ("", []),
        (None, []),
        ("없음", []),
    ],
)
def test_evidence_doc_id_parsing(raw, expected):
    assert evidence.parse_doc_ids(raw) == expected
