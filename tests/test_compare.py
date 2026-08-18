"""연도별 업무 흐름 비교 시험 (계획서 §20, Phase B).

이 기능이 지켜야 할 약속은 셋이다.

  ① 순서가 바뀐 것을 '삭제 + 추가'로 보고하지 않는다
  ② 없어진 단계를 '없앴다'고 단정하지 않는다 (자료가 안 남은 것과 구별 못 한다)
  ③ 모든 변화 주장에 근거 문서가 붙는다
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel  # noqa: E402

from app.core import compare  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402


def steps(*rows) -> list[dict]:
    """(이름, 월) 목록을 단계 행으로. ordinal은 적은 순서 그대로."""
    return [
        {"label": label, "month": month, "day_hint": f"{month}월",
         "ordinal": index, "doc_id": index}
        for index, (label, month) in enumerate(rows, start=1)
    ]


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
    plan = {
        2024: ("자료 요청", "취합", "제출"),
        2025: ("자료 요청", "사전검토", "취합", "제출"),
    }
    doc_id = 0
    database.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    for year, labels in plan.items():
        for month, label in enumerate(labels, start=9):
            doc_id += 1
            name = f"{year}_{label}.hwp"
            database.upsert_document(source_id, {
                "path": str(tmp_path / name), "filename": name, "ext": ".hwp",
                "parse_status": "ok", "hash": f"h{doc_id}",
                "eff_date": f"{year}-{month:02d}-01", "eff_date_kind": "body",
                "eff_precision": "day", "eff_year": year, "eff_month": month,
            })
            database.con.execute(
                "INSERT INTO task_docs(task_id, doc_id) VALUES (1, ?)", (doc_id,)
            )
            database.con.execute(
                "INSERT INTO task_steps(task_id, year, ordinal, label, month, "
                "day_hint, doc_id) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (year, month - 8, label, month, f"{month}월 초", doc_id),
            )
    yield database
    database.close()


# ── 짝지음 ──────────────────────────────────────────────────────────

def test_a_new_step_reads_like_the_plan():
    result = compare.compare(
        2024, steps(("자료 요청", 9), ("취합", 10), ("제출", 11)),
        2025, steps(("자료 요청", 9), ("사전검토", 9), ("취합", 10), ("제출", 11)),
    )
    assert [change.sentence for change in result.changes] == [
        "2025년부터 '사전검토' 단계가 추가된 것으로 보입니다"
    ]
    assert [pair.state for pair in result.pairs] == [
        compare.SAME, compare.ADDED, compare.SAME, compare.SAME
    ]


def test_a_missing_step_is_not_claimed_to_be_abolished():
    """자료가 남지 않은 것과 그만둔 것을 우리는 구별할 수 없다."""
    result = compare.compare(
        2024, steps(("자료 요청", 9), ("검토", 10), ("제출", 11)),
        2025, steps(("자료 요청", 9), ("제출", 11)),
    )
    sentence = result.changes[0].sentence
    assert sentence == "2024년에 있던 '검토' 단계가 2025년 자료에서는 보이지 않습니다"
    assert "없앤" not in sentence and "폐지" not in sentence


def test_reordering_is_one_change_not_two():
    """집합으로 빼면 '취합 삭제 + 취합 추가'가 된다 — 순서가 이 업무의 내용인데."""
    result = compare.compare(
        2024, steps(("취합", 9), ("검토", 10), ("제출", 11)),
        2025, steps(("검토", 9), ("취합", 10), ("제출", 11)),
    )
    assert len(result.changes) == 1
    assert result.changes[0].kind == compare.MOVED
    assert result.changes[0].sentence == (
        "'취합'이 2024년 1번째에서 2025년 2번째로 옮겨진 것으로 보입니다"
    )
    assert [pair.state for pair in result.pairs].count(compare.MOVED) == 2


def test_the_same_flow_says_so_instead_of_listing_everything():
    result = compare.compare(
        2024, steps(("자료 요청", 9), ("제출", 11)),
        2025, steps(("자료 요청", 9), ("제출", 11)),
    )
    assert result.same
    assert result.headline() == "2024년과 2025년의 처리 흐름은 같아 보입니다"


def test_a_one_month_wobble_is_not_a_change():
    """그해 문서 하나가 월말에 걸렸는지 월초에 걸렸는지로도 한 달은 움직인다."""
    result = compare.compare(
        2024, steps(("제출", 11)), 2025, steps(("제출", 12)),
    )
    assert result.same


def test_a_real_shift_is_reported_with_its_direction():
    result = compare.compare(
        2024, steps(("제출", 11)), 2025, steps(("제출", 9)),
    )
    assert result.changes[0].sentence == (
        "'제출'이 11월에서 9월로 2달 앞당겨진 것으로 보입니다"
    )


def test_spacing_in_a_hand_edited_label_does_not_split_a_step():
    """사람이 '사전 검토'로 고쳐 적었다고 다른 단계가 되면 안 된다."""
    result = compare.compare(
        2024, steps(("사전검토", 9)), 2025, steps(("사전 검토", 9)),
    )
    assert result.same


def test_a_year_without_steps_cannot_be_compared():
    result = compare.compare(2024, [], 2025, steps(("제출", 11)))
    assert result.pairs == []
    assert result.headline() == "2024년과 2025년을 견줄 자료가 없습니다"


def test_every_change_carries_a_document_to_check():
    """근거 없는 변화 주장은 이 제품에서 가장 위험한 종류의 말이다."""
    result = compare.compare(
        2024, steps(("취합", 10), ("제출", 11)),
        2025, steps(("사전검토", 9), ("제출", 11)),
    )
    assert result.changes
    assert all(change.row is not None for change in result.changes)
    assert all(change.row["doc_id"] for change in result.changes)


def test_a_repeated_step_is_a_count_change_not_a_new_step():
    """실측에서 잡은 결함 — 분기 통계가 '2025년부터 통계 작성 단계가 추가되었다'로
    두 번 보고됐다. 그 단계는 작년에도 있었다."""
    result = compare.compare(
        2024, steps(("통계 작성", 4), ("통계 작성", 10)),
        2025, steps(("통계 작성", 4), ("통계 작성", 7),
                    ("통계 작성", 10), ("통계 작성", 12)),
    )
    assert [change.kind for change in result.changes] == [compare.REPEAT]
    assert result.changes[0].sentence == (
        "'통계 작성'이 2024년 2번에서 2025년 4번으로 늘었습니다"
    )


def test_a_repeated_step_says_nothing_about_its_timing():
    """몇 번째 것과 몇 번째 것이 대응하는지 우리는 모른다."""
    result = compare.compare(
        2024, steps(("취합", 3), ("취합", 9)),
        2025, steps(("취합", 9), ("취합", 3)),
    )
    assert not any(change.kind == compare.SHIFT for change in result.changes)


def test_a_step_that_only_lost_one_occurrence_is_not_reported_as_missing():
    """'제출'이 두 번에서 한 번이 된 것은 사라진 것이 아니다."""
    result = compare.compare(
        2024, steps(("접수", 9), ("제출", 10), ("제출", 11)),
        2025, steps(("접수", 9), ("제출", 11)),
    )
    assert [change.kind for change in result.changes] == [compare.REPEAT]
    assert "보이지 않습니다" not in result.changes[0].sentence


# ── 화면 ────────────────────────────────────────────────────────────

def test_how_tab_compares_two_years_on_request(db: Database, qapp):
    view = TasksView(db)
    try:
        view.open_task(1)
        view._tab = "how"
        view.refresh()
        page = view.pages["how"]

        texts = [w.text() for w in page.findChildren(QLabel) if w.text()]
        assert not any("달라진 것으로 보이는" in t for t in texts)   # 기본은 비교 꺼짐

        view._change_how_against(1, 2024)   # 기본 선택은 완결된 최근 연도(2025)다
        page = view.pages["how"]
        texts = [w.text() for w in page.findChildren(QLabel) if w.text()]
        assert any("2025년부터 '사전검토' 단계가 추가된" in t for t in texts)
        assert any(t == "2024년" for t in texts) and any(t == "2025년" for t in texts)
    finally:
        view.setParent(None)


def test_the_compare_picker_offers_the_other_years(db: Database, qapp):
    view = TasksView(db)
    try:
        view.open_task(1)
        view._tab = "how"
        view.refresh()
        picker = next(
            c for c in view.pages["how"].findChildren(QComboBox)
            if c.itemText(0) == "비교 안 함"
        )
        assert [picker.itemData(i) for i in range(picker.count())] == [0, 2024]
    finally:
        view.setParent(None)
