"""업무 화면 교정 UI 시험.

교정 조작은 대부분 메뉴와 대화상자 뒤에 있어서, 화면이 그려진다고 해서
동작한다는 보장이 없다. 특히 Qt/PySide의 소유권 문제는 '눌러 봐야' 드러난다
— 실제로 하위 메뉴가 GC에 수거돼 빈 메뉴가 뜨는 결함을 여기서 잡았다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.core import status  # noqa: E402
from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.views.tasks import TasksView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture()
def view(tmp_path: Path, qapp) -> TasksView:
    db = Database(tmp_path / "project.db")
    db.init()
    source_id = db.add_source(tmp_path / "자료")
    for i in (1, 2, 3):
        db.upsert_document(source_id, {
            "path": str(tmp_path / f"문서{i}.hwp"), "filename": f"문서{i}.hwp",
            "ext": ".hwp", "parse_status": "ok",
            "eff_date": f"202{i + 2}-09-01", "eff_date_kind": "body",
            "eff_precision": "day", "eff_year": 2022 + i, "eff_month": 9,
        })
    db.con.execute("INSERT INTO tasks(id, name, confidence) VALUES (1, '행정사무감사', 'high')")
    db.con.execute("INSERT INTO tasks(id, name, confidence) VALUES (2, '예산관리', 'medium')")
    db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")
    db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 2)")
    db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (2, 3)")

    widget = TasksView(db)
    yield widget
    widget.setParent(None)
    db.close()


def texts(widget) -> list[str]:
    return [w.text() for w in widget.findChildren(QLabel) if w.text()]


def buttons(widget) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton) if b.text()]


# ── 메뉴가 실제로 살아 있는가 ───────────────────────────────────────

def test_document_submenu_is_owned_and_stays_alive(view: TasksView):
    """부모 없는 하위 메뉴는 함수가 끝나는 순간 파이썬이 수거한다.

    addMenu(str)이 돌려주는 QMenu는 파이썬 쪽이 소유하므로, 지역 변수가
    사라지면 C++ 객체까지 파괴돼 사용자가 눌렀을 때 빈 메뉴가 뜬다.
    (명시적 gc.collect()는 쓰지 않는다 — PySide 위젯이 많은 세션에서
    힙을 망가뜨린다. 참조 카운트만으로도 이 결함은 드러난다.)
    """
    owner = QPushButton()
    menu = view._document_menu(1, 1, False, owner)
    submenu = next(a.menu() for a in menu.actions() if a.menu() is not None)

    assert submenu.parent() is menu, "하위 메뉴에 주인이 없다"
    assert [a.text() for a in submenu.actions()] == ["예산관리"]


def test_edit_menu_offers_every_task_level_correction(view: TasksView):
    owner = QPushButton()
    labels = [a.text() for a in view._edit_menu(1, owner).actions() if a.text()]
    assert labels == [
        "업무명 수정", "업무 설명 수정", "다른 업무와 합치기",
        "두 업무로 나누기", "이건 업무가 아닙니다",
    ]


def test_merge_is_disabled_when_there_is_nothing_to_merge_into(view: TasksView):
    view.db.mark_not_a_task(2)
    owner = QPushButton()
    merge = next(a for a in view._edit_menu(1, owner).actions()
                 if a.text() == "다른 업무와 합치기")
    assert not merge.isEnabled()


def test_menus_do_not_pile_up_on_the_view(view: TasksView):
    """분석 중에는 1.5초마다 다시 그린다. 메뉴가 뷰에 쌓이면 그대로 누수다."""
    from PySide6.QtWidgets import QMenu

    view.open_task(1)
    before = len(view.findChildren(QMenu))
    for _ in range(5):
        view.refresh()
    assert len(view.findChildren(QMenu)) == before


# ── 교정이 화면에 반영되는가 ────────────────────────────────────────

def test_confirming_a_task_updates_the_badge(view: TasksView):
    view.open_task(1)
    assert "이 업무 확인함" in buttons(view)

    view._approve(1)
    assert status.of_task(view.db.task(1)) == status.CONFIRMED
    assert "이 업무 확인함" not in buttons(view)
    assert any(status.label(status.CONFIRMED) in t for t in texts(view))


def test_cycle_actions_appear_and_confirm_in_place(view: TasksView):
    view.db.replace_task_cycles(1, [{
        "kind": "yearly", "months": "9,10,11", "day_hint": None,
        "years_observed": 3, "confidence": "high", "evidence": None,
    }])
    view.open_task(1)
    assert "이 주기가 맞습니다" in buttons(view)

    view._confirm_cycle(1)
    assert status.of_cycle(view.db.task_cycle(1)) == status.CONFIRMED
    assert "이 주기가 맞습니다" not in buttons(view)


def test_no_cycle_is_shown_as_a_decision_not_a_blank(view: TasksView):
    view._no_cycle(1)
    view.open_task(1)
    assert any("반복하지 않는 업무로 확인했습니다" in t for t in texts(view))
    assert "반복 아님" not in buttons(view)


def test_step_controls_respect_the_ends_of_the_list(view: TasksView):
    view.db.replace_task_steps(1, 2023, [
        {"ordinal": 1, "label": "접수", "month": 9, "day_hint": "9월",
         "doc_id": 1, "gap_note": None},
        {"ordinal": 2, "label": "제출", "month": 10, "day_hint": "10월",
         "doc_id": 2, "gap_note": None},
    ])
    view.open_task(1)
    view._change_how_year(1, 2023)

    arrows = [b for b in view.findChildren(QPushButton) if b.text() in ("▲", "▼")]
    assert arrows, "단계 이동 버튼이 없다"
    # 첫 단계의 ▲와 마지막 단계의 ▼는 눌러도 할 일이 없으므로 꺼져 있어야 한다.
    assert not arrows[0].isEnabled()
    assert not arrows[-1].isEnabled()


def test_not_a_task_returns_to_the_list_without_it(view: TasksView):
    view.db.mark_not_a_task(2)
    view.back()
    assert not any("예산관리" in t for t in texts(view))
    assert any("업무는 1개로 추정됩니다" in t for t in texts(view))
