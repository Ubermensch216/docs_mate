"""UI 셸 스모크 시험.

오프스크린으로 돌리므로 폰트는 없다. 글자 모양이 아니라 구조·상태 전환·
레이아웃 누수를 본다.

MainWindow는 반드시 fixture로 만든다. 백그라운드 스레드를 띄운 채 창이
파괴되면 프로세스가 죽기 때문이다 — 실제로 이 시험이 그 결함을 잡아냈다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel  # noqa: E402

from app.db import Database  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.shell import MainWindow  # noqa: E402
from app.ui.views import ask as ask_view  # noqa: E402
from app.ui.views import tasks as tasks_view  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    yield database
    database.close()


@pytest.fixture
def make_window(qapp, monkeypatch):
    """창을 만들고 반드시 정리한다.

    MainWindow는 생성 시 미완료 작업(스캔·해시·파싱·의미색인·업무파악·
    주기·순서)이 있으면 백그라운드 파이프라인을 자동으로 켠다(ING-006
    재개). 이 파일은 정적 렌더링만 검증하는데, 우리가 심어 둔 가짜 문서는
    실제 임베딩이 없어 "미완료"로 잡히므로 거의 항상 파이프라인이 켜진다.
    그 파이프라인이 가짜 소스 경로(D:\\자료 등)를 실제로 스캔해 문서를
    '원본 없음'으로 마킹하거나 cycles/steps를 재계산해 시드 데이터를
    덮어쓰면서 조립 중인 어서션과 경합한다 — 실제로 이 경합이 간헐적
    실패를 일으켰다. 자동 재개 자체를 구조적으로 막는다. 파이프라인 동작
    검증은 test_cycles_pipeline.py / test_steps_pipeline.py 등 전용 시험이
    맡는다.
    """
    monkeypatch.setattr(MainWindow, "_resume_if_pending", lambda self: None)

    created: list[MainWindow] = []

    def factory(db: Database) -> MainWindow:
        window = MainWindow(db)
        created.append(window)
        return window

    yield factory

    for window in created:
        window.shutdown()
        window.close()
        window.deleteLater()
    qapp.processEvents()


def _add_docs(db: Database, count: int = 4, parsed: int = 2) -> int:
    """분석이 끝난 상태로 넣는다. 파이프라인이 자동 재개되지 않도록 한다."""
    source_id = db.add_source(r"D:\전임자업무")
    for i in range(count):
        db.upsert_document(source_id, {
            "path": rf"D:\전임자업무\문서{i}.hwp",
            "filename": f"문서{i}.hwp",
            "ext": ".hwp",
            "size": 1000 + i,
            "hash": f"h{i}",
            "parse_status": "ok" if i < parsed else "empty",
            "eff_date": f"202{i % 4 + 2}-09-01",
            "eff_year": 2022 + (i % 4),
            "eff_date_kind": "body",
            "eff_precision": "month",
        })
    return source_id


def test_window_starts_on_onboarding_when_no_source(make_window, db):
    window = make_window(db)
    assert window.stack.currentWidget() is window.views["onboarding"]
    # 자료원이 없으면 메뉴를 누를 수 없어야 한다 — 빈 화면으로 보내지 않는다.
    assert not window.topbar._buttons["tasks"].isEnabled()


def test_window_switches_to_tasks_once_source_exists(make_window, db):
    _add_docs(db)
    window = make_window(db)
    assert window.stack.currentWidget() is window.views["tasks"]
    assert window.topbar._buttons["tasks"].isEnabled()


@pytest.mark.parametrize("key", ["tasks", "calendar", "documents", "ask"])
def test_every_view_navigates_and_refreshes(make_window, db, key):
    _add_docs(db)
    window = make_window(db)
    window.go(key)
    assert window.stack.currentWidget() is window.views[key]


def test_refresh_does_not_leak_widgets(make_window, db):
    """clear_layout 회귀 시험.

    deleteLater()만 쓰면 옛 위젯이 남아 새 내용과 겹쳐 그려진다.
    같은 상태로 여러 번 새로 고쳐도 위젯 수가 늘지 않아야 한다.
    """
    _add_docs(db)
    view = make_window(db).views["tasks"]

    view.refresh()
    first = view.column.count()
    for _ in range(5):
        view.refresh()
    assert view.column.count() == first


def test_calendar_refuses_to_claim_cycles_without_enough_years(make_window, db):
    """업무는 있지만 자료가 한 해치뿐이면 반복을 주장하지 않는다."""
    source_id = db.add_source(r"D:\한해자료")
    db.con.execute(
        "INSERT INTO tasks(name, origin, status, confidence) "
        "VALUES ('단발업무', 'ai', 'proposed', 'medium')"
    )
    task_id = db.con.execute("SELECT id FROM tasks").fetchone()["id"]
    doc_id = db.upsert_document(source_id, {
        "path": r"D:\한해자료\a.hwp", "filename": "a.hwp", "ext": ".hwp",
        "eff_date": "2024-09-01", "eff_year": 2024, "eff_month": 9,
        "eff_date_kind": "body", "parse_status": "ok", "hash": "x",
    })
    db.con.execute(
        "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
        (task_id, doc_id),
    )
    window = make_window(db)
    window.go("calendar")
    texts = [
        label.text()
        for label in window.views["calendar"].findChildren(QLabel)
        if label.text()
    ]
    assert any("판단할 자료가 부족" in t for t in texts), texts


def test_ask_view_withholds_until_analysis_done(make_window, db):
    """분석 전에는 질문 입력을 막는다. 근거 없이 답하지 않기 위해서다."""
    _add_docs(db)
    view = make_window(db).views["ask"]
    view.refresh()
    assert not view.send.isEnabled()
    assert view.notice.label.text()


def test_documents_view_lists_rows(make_window, db):
    _add_docs(db, count=3, parsed=3)
    window = make_window(db)
    window.go("documents")
    assert window.views["documents"].table.rowCount() == 3


def test_documents_view_flags_filesystem_only_dates(make_window, db):
    """파일 수정일로만 판정한 시점은 그렇다고 밝혀야 한다."""
    source_id = db.add_source(r"D:\단서없음")
    db.upsert_document(source_id, {
        "path": r"D:\단서없음\회의자료.txt", "filename": "회의자료.txt", "ext": ".txt",
        "parse_status": "ok", "eff_date": "2023-06-02", "hash": "y",
        "eff_date_kind": "fs", "eff_precision": "day",
    })
    window = make_window(db)
    window.go("documents")
    when = window.views["documents"].table.item(0, 2).text()
    assert "파일 날짜" in when, when


def test_documents_view_collapses_exact_duplicates(make_window, db):
    """완전 중복은 한 줄로 접힌다. 목록이 줄어드는 것이 정리의 실감이다."""
    source_id = db.add_source(r"D:\중복")
    for i in range(3):
        db.upsert_document(source_id, {
            "path": rf"D:\중복\보고서_사본{i}.hwp",
            "filename": f"보고서_사본{i}.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": "SAME",
        })
    window = make_window(db)
    window.go("documents")
    view = window.views["documents"]

    assert view.table.rowCount() == 1
    assert "3개 묶음" in view.table.item(0, 0).text()

    view.collapse_dups.setChecked(False)
    assert view.table.rowCount() == 3


def _seed_task(db: Database) -> int:
    """업무 하나와 문서 세 건을 심는다."""
    source_id = db.add_source(r"D:\자료")
    db.con.execute(
        "INSERT INTO tasks(name, description, origin, status, confidence) "
        "VALUES ('행정사무감사', '의회 요구자료를 취합해 제출하는 업무입니다.', "
        "        'ai', 'proposed', 'high')"
    )
    task_id = db.con.execute("SELECT id FROM tasks").fetchone()["id"]
    for index in range(3):
        name = f"202{index + 3}_행정사무감사_제출자료_최종.docx"
        doc_id = db.upsert_document(source_id, {
            "path": rf"D:\자료\{name}", "filename": name, "ext": ".docx",
            "parse_status": "ok", "hash": f"h{index}", "char_count": 900,
            "eff_year": 2023 + index, "eff_month": 10, "eff_date": f"202{index + 3}-10-01",
            "eff_date_kind": "body", "eff_precision": "month",
        })
        db.con.execute(
            "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
            (task_id, doc_id),
        )
        db.con.execute(
            "INSERT INTO task_reading(task_id, doc_id, ordinal, score, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, doc_id, index + 1, 5.0 - index, "'제출' 표기가 있습니다"),
        )
    return task_id


def test_tasks_view_announces_the_discovered_count(make_window, db):
    """첫 화면이 곧 정체성의 답이다 — 파일 개수가 아니라 업무 개수."""
    _seed_task(db)
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("업무는 1개로 추정됩니다" in t for t in texts), texts


def test_task_detail_shows_reading_list_with_reasons(make_window, db):
    """이유 없는 추천은 만들지 않는다."""
    task_id = _seed_task(db)
    window = make_window(db)
    view = window.views["tasks"]
    view.open_task(task_id)

    texts = _labels(view) + _buttons(view)
    assert any("먼저 읽을 문서" in t for t in texts)
    assert any("'제출' 표기가 있습니다" in t for t in texts), texts
    assert any("2025_행정사무감사" in t for t in texts)


def test_task_detail_opens_on_the_reading_tab(make_window, db):
    """상세는 언제나 '무엇부터 읽나'로 열린다. 탭은 넷이고, 목록에는 없다."""
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    assert view.tabs.isVisible() or not view.isVisible()   # 오프스크린 대비
    assert view._tab == tasks_view.READ
    assert view.stack.currentWidget() is view.pages[tasks_view.READ]

    view.back()
    assert view.stack.currentWidget() is view.list_page
    assert not view.tabs.isVisible()


def test_task_detail_keeps_the_open_tab_across_refresh(make_window, db):
    """분석 중에는 1.5초마다 다시 그린다. 보고 있던 탭을 빼앗으면 안 된다."""
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    view._switch(tasks_view.HOW)
    view.refresh()

    assert view._tab == tasks_view.HOW
    assert view.stack.currentWidget() is view.pages[tasks_view.HOW]


def test_task_detail_tabs_carry_counts(make_window, db):
    """탭 이름의 건수는 누르기 전에 규모를 알려 준다."""
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    labels = _buttons(view)
    docs = len(db.task_documents(task_id))
    assert any(f"이 업무의 문서  {docs}" in t for t in labels), labels


def test_task_detail_can_return_to_the_list(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)
    view.back()
    assert any("추정됩니다" in t for t in _labels(view))


def test_renaming_a_task_marks_it_as_user_edited(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    assert db.rename_task(task_id, "의회 대응")
    view.refresh()

    row = db.task(task_id)
    assert row["name"] == "의회 대응"
    assert row["status"] == "edited"


def test_detaching_a_document_updates_the_view(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)
    victim = db.task_documents(task_id)[0]["id"]

    view._detach(task_id, victim)

    assert victim not in {r["id"] for r in db.task_documents(task_id)}


def test_tasks_view_shows_progress_before_any_task_exists(make_window, db):
    _add_docs(db, count=4, parsed=2)
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("살펴보고 있습니다" in t for t in texts), texts
    assert any("업무는 아직 파악하지 못했습니다" in t for t in texts), texts


def test_unclassified_documents_are_shown_not_hidden(make_window, db):
    """미분류가 남는 것은 정상이다. 숨기면 사용자가 속는다."""
    task_id = _seed_task(db)
    db.upsert_document(1, {
        "path": r"D:\자료\정체불명.hwp", "filename": "정체불명.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "zzz",
    })
    window = make_window(db)
    window.go("tasks")
    texts = _labels(window.views["tasks"])
    assert any("미분류 1건" in t for t in texts), texts


def test_long_guidance_is_folded_into_an_info_dot(make_window, db):
    """설명은 접되 잃지 않는다 — 본문에서 빠진 문장은 ⓘ의 팝업에 있어야 한다."""
    from app.ui.widgets import InfoDot

    _seed_task(db)
    window = make_window(db)
    window.go("tasks")
    view = window.views["tasks"]

    # 본문에는 더 이상 안내 문단이 깔리지 않는다.
    assert not any("카드를 누르면" in t for t in _labels(view))

    dots = view.findChildren(InfoDot)
    assert dots, "설명을 접었으면 접었다는 표시가 화면에 남아야 한다"
    popups = " ".join(d.toolTip() for d in dots)
    assert "카드를 누르면" in popups
    assert "AI가 제안한 것이므로" in popups
    # 스크린리더는 툴팁을 읽지 못한다. 접근 가능한 설명도 함께 붙어야 한다.
    assert all(d.accessibleDescription() for d in dots)


def test_calendar_identity_line_is_never_folded_away(make_window, db):
    """'직접 입력한 달력이 아니다'는 설명이 아니라 정체성이다 — 접지 않는다."""
    _seed_task(db)
    window = make_window(db)
    window.go("calendar")
    texts = _labels(window.views["calendar"])
    assert any("직접 입력한 달력이 아니라" in t for t in texts), texts


def _labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _buttons(widget) -> list[str]:
    from PySide6.QtWidgets import QPushButton

    return [b.text() for b in widget.findChildren(QPushButton) if b.text()]


def _seed_cycle(db: Database, task_id: int, **overrides) -> None:
    row = dict(kind="yearly", months="9,10,11", day_hint=None,
               years_observed=4, confidence="high", evidence="")
    row.update(overrides)
    db.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, day_hint, "
        "years_observed, confidence, evidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, row["kind"], row["months"], row["day_hint"],
         row["years_observed"], row["confidence"], row["evidence"]),
    )


def test_task_detail_shows_when_section_with_cycle(make_window, db):
    """When은 제 탭 안에서 답을 먼저 말한다."""
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    assert any("언제 하는 일인가" in t for t in _buttons(view))
    texts = _labels(view.pages[tasks_view.WHEN])
    assert any("매년 9~11월" in t for t in texts), texts
    assert any("신뢰도" in t for t in _labels(view))


def test_task_detail_without_cycle_explains_insufficient_data(make_window, db):
    """반복을 찾지 못했으면 없다고 정직하게 말한다 — 지어내지 않는다."""
    task_id = _seed_task(db)   # 문서 3건, 연도 2023~2025지만 주기는 안 심음
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    assert any("언제 하는 일인가" in t for t in _buttons(view))
    texts = _labels(view)
    # 확정된 주기가 없으므로 '🔁 매년 ...' 반복 헤드라인은 뜨지 않아야 한다.
    assert not any(t.startswith("🔁") for t in texts), texts
    assert not any("다음 예상 시점" in t for t in texts)


def test_task_detail_shows_next_occurrence_link_to_calendar(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    signals_received = []
    view.go_calendar.connect(lambda: signals_received.append(True))
    for button in _button_widgets(view):
        if "일정에서 보기" in button.text():
            button.click()
            break
    assert signals_received, "일정으로 가는 링크를 찾지 못했습니다"


def test_calendar_shows_recurring_tasks_when_present(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id, months="8")   # 이번 달(테스트 실행 월과 무관하게 8월로 고정)
    window = make_window(db)
    window.go("calendar")
    view = window.views["calendar"]
    texts = _labels(view) + _buttons(view)   # 업무명은 눌러서 상세로 가는 버튼이다
    assert any("연간 패턴" in t for t in texts)
    assert any("행정사무감사" in t for t in texts)


def test_calendar_puts_monthly_cycles_in_the_now_tab(make_window, db):
    """매월 반복은 '항상 이번 달'이다 — 다가올 일이 아니라 지금 챙길 일이다."""
    from app.ui.views import calendar as cal

    task_id = _seed_task(db)
    _seed_cycle(db, task_id, kind="monthly", months="", day_hint="5~10일")
    window = make_window(db)
    window.go("calendar")
    view = window.views["calendar"]

    now_page = _labels(view.pages[cal.NOW])
    later_page = _labels(view.pages[cal.LATER])
    assert any("진행 중" in t for t in now_page), now_page
    assert any("더 뒤에 예정된 반복 업무가 없습니다" in t for t in later_page), later_page


def test_calendar_open_task_signal_navigates_to_task_detail(make_window, db):
    task_id = _seed_task(db)
    _seed_cycle(db, task_id)
    window = make_window(db)
    window.go("calendar")

    for button in _button_widgets(window.views["calendar"]):
        if "행정사무감사" in button.text():
            button.click()
            break
    assert window.stack.currentWidget() is window.views["tasks"]
    assert window.views["tasks"]._task_id == task_id


def test_calendar_without_any_cycles_shows_insufficient_data_message(make_window, db):
    _add_docs(db, count=4, parsed=4)
    window = make_window(db)
    window.go("calendar")
    texts = _labels(window.views["calendar"])
    assert any("업무 보기" in t or "문서 보기" in t or "아직" in t for t in texts), texts


def _button_widgets(widget):
    from PySide6.QtWidgets import QPushButton

    return [b for b in widget.findChildren(QPushButton) if b.text()]


def _seed_chunked_doc(db: Database, source_id: int, filename: str = "문서.hwp") -> int:
    """질문 화면이 '준비됨'으로 보도록 청크·임베딩을 갖춘 문서를 심는다."""
    from app.search.vector import pack

    doc_id = db.upsert_document(source_id, {
        "path": rf"D:\자료\{filename}", "filename": filename, "ext": ".hwp",
        "parse_status": "ok", "hash": filename,
    })
    chunk_id, _ = db.replace_document_chunks(doc_id, [(1, "1문단", "본문")])[0]
    db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([1.0, 0.0, 0.0]))
    return doc_id


def test_ask_view_blocks_input_without_chunks(make_window, db):
    """의미 색인이 없으면 입력을 막는다 — 근거 없이 답하지 않기 위해서다."""
    db.add_source(r"D:\자료")
    db.upsert_document(1, {
        "path": r"D:\자료\a.hwp", "filename": "a.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "a",
    })
    view = make_window(db).views["ask"]
    view.refresh()
    assert not view.send.isEnabled()
    assert "색인" in view.notice.label.text() or "준비" in view.notice.label.text()


def test_ask_view_enables_input_once_chunks_exist(make_window, db):
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    view.refresh()
    assert view.send.isEnabled()
    assert not view.notice.isVisible()


def test_ask_view_renders_withheld_answer_with_related_docs(make_window, db):
    """근거 부족 시 이유와 함께 대신 찾은 문서를 보여준다 — 지어내지 않는다."""
    from app.search.rag import Answer, RelatedDoc

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    answer = Answer(
        question="질문", text="확인 가능한 자료가 부족합니다.", withheld=True,
        related_docs=[RelatedDoc(1, "비슷한문서.hwp", r"D:\자료\비슷한문서.hwp")],
    )
    view._render_answer(answer)

    texts = [w.text() for w in view.findChildren(type(view.input)) if hasattr(w, "text")]
    labels = _labels(view)
    buttons = _button_widgets(view)
    assert any("부족합니다" in t for t in labels)
    assert any("비슷한문서.hwp" in b.text() for b in buttons)


def test_ask_view_renders_answer_with_citations_and_feedback(make_window, db):
    """모든 AI 주장은 근거 문서를 갖는다 — 근거 없는 답은 화면에 올리지 않는다."""
    from app.search.rag import Answer, Citation

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    answer = Answer(
        question="질문", text="2024년 수질 관련 자료 6건이 확인됩니다.[1]", withheld=False,
        citations=[Citation(1, 1, "행감자료.hwp", "3쪽 2문단", r"D:\자료\행감자료.hwp")],
        model="gemma4:e2b",
    )
    view._render_answer(answer)

    texts = _labels(view)
    buttons = _button_widgets(view)
    assert any("6건이 확인됩니다" in t for t in texts)
    # 근거는 답변 옆에 목록으로 서고, 고른 근거의 **원문**이 오른쪽 칸에 펴진다
    # (디자인 개선안 1c — 근거 대조형).
    assert any("행감자료.hwp" in t for t in _labels(view))
    assert view._selected == 1
    # 피드백은 둘뿐이다 — '부정확'과 '출처가 틀림'의 차이를 처음 쓰는 사람이
    # 판단할 수 없어 하나로 합쳤다(디자인 개선안 진단 7).
    assert any("맞아요" in b.text() for b in buttons)
    assert any("사실과 달라요" in b.text() for b in buttons)
    assert not any("출처가 틀림" in b.text() for b in buttons)


def test_ask_view_shows_error_when_generation_fails(make_window, db):
    from app.search.rag import Answer

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    answer = Answer(question="질문", text="", withheld=True, error="로컬 AI에 연결할 수 없습니다")
    view._render_answer(answer)

    texts = _labels(view)
    assert any("연결할 수 없습니다" in t for t in texts)


def test_ask_view_rating_persists_to_the_latest_question(make_window, db):
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    qid = db.save_question("질문", "답변", "[]", False, "gemma4:e2b")

    from app.search.rag import Answer, Citation

    answer = Answer(
        question="질문", text="답변입니다.", withheld=False,
        citations=[Citation(1, 1, "문서.hwp", "1문단", r"D:\자료\문서.hwp")],
    )
    view._render_answer(answer)

    for button in _button_widgets(view):
        if button.text() == "맞아요":
            button.click()
            break

    row = db.con.execute("SELECT rating FROM questions WHERE id = ?", (qid,)).fetchone()
    assert row["rating"] == "helpful"


def test_ask_view_asking_shows_a_waiting_state(make_window, db, monkeypatch):
    """질문을 보내면 즉시 '찾아보는 중' 상태로 바뀌어야 한다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    monkeypatch.setattr(view._runner, "start", lambda q, scope=None: True)
    view.input.setText("질문 있음")
    view._ask()

    texts = _labels(view)
    assert any("답을 만들고 있습니다" in t for t in texts), texts
    assert any("질문 있음" in t for t in texts), "무엇을 묻는 중인지 보여야 한다"
    assert not view.send.isEnabled()

    # 값 없는(indeterminate) 막대라야 한다 — 남은 시간을 아는 척하면
    # 거짓 진행률이 된다. 지금 어느 단계인지 우리는 실제로 모른다.
    from PySide6.QtWidgets import QProgressBar
    bar = view.findChild(QProgressBar)
    assert bar is not None and bar.minimum() == 0 and bar.maximum() == 0


def _seed_steps(db: Database, task_id: int, year: int, steps: list[dict]) -> None:
    for step in steps:
        db.con.execute(
            "INSERT INTO task_steps(task_id, year, ordinal, label, month, "
            "day_hint, doc_id, gap_note, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ai')",
            (task_id, year, step["ordinal"], step["label"], step["month"],
             step["day_hint"], step.get("doc_id"), step.get("gap_note")),
        )


def test_task_detail_shows_how_section_with_ordered_steps(make_window, db):
    """How는 What 다음, 문서 목록 앞에 온다. 모든 단계에 근거 문서가 붙는다."""
    task_id = _seed_task(db)
    docs = db.task_documents(task_id)
    _seed_steps(db, task_id, 2025, [
        {"ordinal": 1, "label": "접수", "month": 9, "day_hint": "9월 초",
         "doc_id": docs[0]["id"]},
        {"ordinal": 2, "label": "제출", "month": 10, "day_hint": "10월",
         "doc_id": docs[0]["id"], "gap_note": "1과 2 사이 약 3주는 관련 자료가 없어 확인되지 않습니다"},
    ])
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    assert any("어떻게 처리했나" in t for t in _buttons(view))
    texts = _labels(view.pages[tasks_view.HOW])
    assert any("2025년엔 이렇게 처리한 것으로 보입니다" in t for t in texts), texts
    assert any("접수" in t for t in texts)
    assert any("확인되지 않습니다" in t for t in texts), "공백을 지어내지 않고 밝혀야 합니다"


def test_task_detail_without_steps_explains_absence(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    assert any("어떻게 처리했나" in t for t in _buttons(view))
    texts = _labels(view.pages[tasks_view.HOW])
    assert any("재구성할 자료가 없습니다" in t for t in texts), texts


def test_task_detail_how_year_selector_switches_steps(make_window, db, qapp):
    """연도 전환 드롭다운을 바꾸면 그 해의 단계가 보여야 한다."""
    task_id = _seed_task(db)
    docs = db.task_documents(task_id)
    _seed_steps(db, task_id, 2025, [
        {"ordinal": 1, "label": "제출_2025", "month": 10, "day_hint": "10월",
         "doc_id": docs[0]["id"]},
    ])
    _seed_steps(db, task_id, 2023, [
        {"ordinal": 1, "label": "제출_2023", "month": 10, "day_hint": "10월",
         "doc_id": docs[0]["id"]},
    ])
    view = make_window(db).views["tasks"]
    view.open_task(task_id)

    from PySide6.QtWidgets import QComboBox

    combo = view.findChild(QComboBox)
    assert combo is not None

    index_2023 = combo.findData(2023)
    assert index_2023 >= 0
    combo.setCurrentIndex(index_2023)
    qapp.processEvents()

    texts = _labels(view)
    assert any("제출_2023" in t for t in texts), texts
    assert not any("제출_2025" in t for t in texts)


def test_task_detail_how_step_shows_evidence_before_the_original(make_window, db, tmp_path):
    """단계에 붙은 문서를 누르면 **먼저 근거**가 열리고, 원본은 그 안에서 연다.

    원본을 바로 띄우면 확인이 아니라 이탈이다 — 한글이 뜨는 데 몇 초가 걸리고
    화면을 떠난다(계획서 §14의 Claim → Evidence → Original)."""
    task_id = _seed_task(db)
    real_file = tmp_path / "실제문서.hwp"
    real_file.write_text("dummy", encoding="utf-8")

    source_id = db.add_source(str(tmp_path))
    doc_id = db.upsert_document(source_id, {
        "path": str(real_file), "filename": "실제문서.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "real1",
        "eff_year": 2025, "eff_month": 10, "eff_date": "2025-10-12",
        "eff_precision": "day", "eff_date_kind": "body",
    })
    _seed_steps(db, task_id, 2025, [
        {"ordinal": 1, "label": "제출", "month": 10, "day_hint": "10월", "doc_id": doc_id},
    ])
    db.replace_sections(doc_id, [("paragraph", 1, "1쪽", "2025년 제출 자료입니다")])
    view = make_window(db).views["tasks"]
    view.open_task(task_id)
    view._switch("how")

    for button in _button_widgets(view):
        if "실제문서.hwp" in button.text():
            button.click()
            break

    assert not view.drawer.isHidden()      # 창을 띄우지 않는 시험이라 isVisible은 못 쓴다
    assert any("2025년 제출 자료입니다" in text for text in _labels(view.drawer))

    opened = []
    view.drawer.open_original.connect(opened.append)
    for button in _button_widgets(view.drawer):
        if button.text() == "원본 열기":
            button.click()
    assert opened == [str(real_file)]


def test_status_dialog_shows_stage_progress(make_window, db):
    """상단 바를 누르면 단계별 진행을 볼 수 있어야 한다 (doc/00 §6.5)."""
    from app.jobs.pipeline import StageReport
    from app.ui.status_dialog import StatusDialog

    window = make_window(db)
    window.stage_reports["파일 찾기"] = StageReport("파일 찾기", done=10, total=10, note="문서 5건")
    window.stage_reports["내용 읽기"] = StageReport("내용 읽기", done=3, total=5)

    dialog = StatusDialog(window)
    texts = _labels(dialog)
    assert any("파일 찾기" in t and "10" in t for t in texts)
    assert any("내용 읽기" in t and "3" in t for t in texts)
    dialog.close()


def test_status_dialog_lists_failures_from_every_stage(make_window, db):
    from app.jobs.pipeline import StageReport
    from app.ui.status_dialog import StatusDialog

    window = make_window(db)
    report = StageReport("내용 읽기", done=2, total=2)
    report.errors = ["암호_문서.hwp: 암호로 보호된 문서"]
    window.stage_reports["내용 읽기"] = report

    dialog = StatusDialog(window)
    texts = _labels(dialog)
    assert any("암호로 보호된 문서" in t for t in texts)
    dialog.close()


def test_status_dialog_toggle_reflects_runner_state(make_window, db):
    from app.ui.status_dialog import StatusDialog

    window = make_window(db)
    dialog = StatusDialog(window)
    assert dialog.toggle.text() == "이어서 실행"
    dialog.close()


def test_status_bar_click_opens_status_dialog(make_window, db, monkeypatch):
    class _DummyDialog:
        def exec(self):
            return None

        def refresh(self) -> None:
            pass

    window = make_window(db)
    opened = []
    monkeypatch.setattr(
        "app.ui.status_dialog.StatusDialog",
        lambda *a, **k: opened.append(True) or _DummyDialog(),
    )
    window.topbar.status_clicked.emit()
    assert opened == [True]


def test_settings_shows_audit_log_entries(make_window, db):
    from app.ui.views.settings import SettingsDialog

    db.audit("document.open", r"D:\자료\문서.hwp", result="ok")
    dialog = SettingsDialog(db, make_window(db))
    texts = _labels(dialog)
    assert any("document.open" in t for t in texts)
    dialog.close()


def test_settings_writes_audit_log_csv(make_window, db, tmp_path):
    """다이얼로그는 거치지 않는다 — 네이티브 파일 다이얼로그를 몽키패치로
    가로채려다 실제 모달 창이 뜨며 시험이 멈춘 사고가 있었다. 쓰기 로직만
    직접 부른다."""
    from app.ui.views.settings import SettingsDialog

    db.audit("document.open", r"D:\자료\문서.hwp", result="ok")
    out_path = tmp_path / "audit.csv"

    dialog = SettingsDialog(db, make_window(db))
    count = dialog._write_audit_csv(str(out_path))

    assert count >= 1
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8-sig")
    assert "document.open" in content
    dialog.close()


@pytest.fixture
def restore_theme(qapp):
    """테마를 건드리는 시험은 끝나고 반드시 되돌린다.

    QApplication은 세션 하나를 공유하므로, 어둡게로 바꿔 놓고 끝내면 뒤에
    도는 시험이 남의 색으로 그려진다.
    """
    yield
    theme.apply(qapp, "medium", "light")


def test_settings_text_size_has_three_steps_and_rescales(make_window, db, qapp,
                                                         restore_theme):
    """글자 크기는 켬/끔이 아니라 소·중·대 3단계다."""
    from app.ui.views.settings import SettingsDialog

    dialog = SettingsDialog(db, make_window(db))
    assert dialog.size_choice.current() == "medium"

    dialog.size_choice.chosen.emit("large")
    assert db.get_meta("text_size") == "large"
    assert "font-size: 17px" in qapp.styleSheet()     # 14 * 1.2

    dialog.size_choice.chosen.emit("small")
    assert db.get_meta("text_size") == "small"
    assert "font-size: 13px" in qapp.styleSheet()     # 14 * 0.9
    dialog.close()


def test_settings_migrates_the_old_large_text_setting(make_window, db, qapp,
                                                      restore_theme):
    """예전에 '글자 크게 보기'를 켜 둔 사람은 갱신 후에도 크게 봐야 한다."""
    from app.ui.views.settings import SettingsDialog

    db.set_meta("large_text", "1")
    dialog = SettingsDialog(db, make_window(db))
    assert dialog.size_choice.current() == "large"
    dialog.close()


def test_settings_theme_switches_the_palette_and_persists(make_window, db, qapp,
                                                          restore_theme):
    from app.ui.views.settings import SettingsDialog

    dialog = SettingsDialog(db, make_window(db))
    assert dialog.theme_choice.current() == "system"

    dialog.theme_choice.chosen.emit("dark")
    assert db.get_meta("theme_mode") == "dark"
    assert theme.current_mode() == "dark"
    assert theme.BG == theme.DARK["BG"]
    assert theme.DARK["BG"] in qapp.styleSheet()

    dialog.theme_choice.chosen.emit("light")
    assert db.get_meta("theme_mode") == "light"
    assert theme.BG == theme.LIGHT["BG"]
    dialog.close()


def test_status_colors_follow_the_theme(restore_theme, qapp):
    """색을 값으로 담아 둔 표가 있으면 테마를 바꿔도 그 표만 옛 색으로 남는다."""
    from app.ui.views.documents import PARSE_LABEL

    theme.apply(qapp, "medium", "light")
    light = theme.color(PARSE_LABEL["ok"][1])
    theme.apply(qapp, "medium", "dark")
    assert theme.color(PARSE_LABEL["ok"][1]) != light


def test_settings_groups_ai_and_activity_under_system(make_window, db):
    """로컬 AI 현황과 최근 활동은 '시스템' 한 갈래에서 함께 본다."""
    from app.ui.views.settings import SettingsDialog

    db.audit("document.open", r"D:\자료\문서.hwp", result="ok")
    dialog = SettingsDialog(db, make_window(db))
    texts = _labels(dialog.panes["system"])

    assert any("로컬 AI" in t for t in texts)
    assert any("최근 활동" in t for t in texts)
    assert any("저장 위치" in t for t in texts)
    assert any("document.open" in t for t in texts)
    # 화면 갈래에는 그것들이 섞여 있지 않다 — 매일 쓰는 것과 가끔 보는 것.
    assert not any("최근 활동" in t for t in _labels(dialog.panes["appearance"]))
    dialog.close()


def test_settings_lists_source_folders_with_a_way_to_change_them(make_window, db):
    """자료 폴더는 최초 지정으로 확정되지 않는다 — 바꿀 수 있어야 한다."""
    from app.ui.views.settings import SettingsDialog

    db.add_source(r"D:\전임자자료")
    dialog = SettingsDialog(db, make_window(db))
    pane = dialog.panes["sources"]

    assert any(r"D:\전임자자료" in t for t in _labels(pane))
    assert any("위치 변경" in t for t in _buttons(pane))
    assert any("폴더 추가" in t for t in _buttons(pane))
    # '읽는 곳'과 '쓰는 곳'의 차이를 화면에서 직접 설명한다.
    assert any("읽는 곳" in t for t in _labels(pane))
    dialog.close()


def test_changing_a_source_folder_keeps_the_analysis(db):
    """폴더를 바꿨다고 쌓아 둔 분석과 교정값을 잃게 할 수는 없다."""
    source_id = db.add_source(r"D:\옛폴더")
    db.upsert_document(source_id, {
        "path": r"D:\옛폴더\보고서.hwp", "filename": "보고서.hwp",
        "ext": ".hwp", "parse_status": "ok", "hash": "a",
    })

    assert db.change_source_path(source_id, r"D:\새폴더") is True
    assert db.sources()[0]["path"] == r"D:\새폴더"
    assert db.counts()["documents"] == 1

    # 이미 다른 자료원이 쓰는 경로로는 바꿀 수 없다 (경로는 유일하다).
    other = db.add_source(r"D:\다른폴더")
    assert db.change_source_path(other, r"D:\새폴더") is False


def test_removing_a_source_folder_reports_what_it_deletes(db):
    source_id = db.add_source(r"D:\지울폴더")
    db.upsert_document(source_id, {
        "path": r"D:\지울폴더\문서.hwp", "filename": "문서.hwp",
        "ext": ".hwp", "parse_status": "ok", "hash": "b",
    })

    assert db.source_document_counts()[source_id] == 1
    db.remove_source(source_id)
    assert db.sources() == []
    assert db.counts()["documents"] == 0


def test_documents_view_hides_non_document_files_by_default(make_window, db):
    source_id = db.add_source(r"D:\혼합")
    db.upsert_document(source_id, {
        "path": r"D:\혼합\보고서.hwp", "filename": "보고서.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "a",
    })
    db.upsert_document(source_id, {
        "path": r"D:\혼합\사진.jpg", "filename": "사진.jpg", "ext": ".jpg",
        "parse_status": "skipped", "hash": "b",
    })
    window = make_window(db)
    window.go("documents")
    view = window.views["documents"]

    assert view.table.rowCount() == 1
    view.documents_only.setChecked(False)
    assert view.table.rowCount() == 2


# ── 모델 예열 (R8) ──────────────────────────────────────────────────

def test_ask_view_warms_up_the_model_once_when_ready(make_window, db, monkeypatch):
    """첫 질문만 유독 느린 것은 거의 전부 모델 적재 때문이다.

    사용자가 타이핑하는 동안 미리 올려 둔다. 화면을 오갈 때마다 다시
    부르면 그것대로 자원 낭비이므로 한 번만 돈다.
    """
    from app.jobs import ask_task

    calls = []
    monkeypatch.setattr(
        ask_task.WarmupRunner, "start",
        lambda self: calls.append(1) or True,
    )

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    window = make_window(db)
    view = window.views["ask"]

    view.refresh()
    view.refresh()
    assert len(calls) >= 1


def test_ask_view_does_not_warm_up_before_the_index_is_ready(make_window, db, monkeypatch):
    """색인이 없으면 질문할 수도 없다 — 모델을 미리 올릴 이유가 없다."""
    from app.jobs import ask_task

    calls = []
    monkeypatch.setattr(
        ask_task.WarmupRunner, "start",
        lambda self: calls.append(1) or True,
    )

    _add_docs(db, count=2, parsed=0)
    window = make_window(db)
    window.views["ask"].refresh()
    assert calls == []


def test_warmup_runs_only_once_even_when_started_repeatedly():
    """WarmupRunner 자체의 계약 — 두 번째 호출부터는 아무 일도 하지 않는다."""
    from app.jobs import WarmupRunner

    runner = WarmupRunner()
    started = [runner.start() for _ in range(3)]
    runner.stop()
    assert started.count(True) == 1


# ── 인용 링크·근거 서랍·후속 질문 (R9) ──────────────────────────────

def _answered(**overrides):
    from app.search.rag import Answer, Citation

    base = dict(
        question="2025년 행정사무감사 때 뭘 제출했어?",
        text="수질검사 결과를 제출했다.[1] 정수장 운영현황도 함께 냈다.[2]",
        withheld=False,
        citations=[
            Citation(index=1, doc_id=1, filename="2025_행감_제출자료.hwp",
                     locator="3쪽", path=r"D:\자료\a.hwp", snippet="수질검사 결과"),
            Citation(index=2, doc_id=2, filename="정수장_운영현황.xlsx",
                     locator="'운영'!B4", path=r"D:\자료\b.xlsx", snippet="운영현황 표"),
        ],
        inferred_years=[2025],
    )
    base.update(overrides)
    return Answer(**base)


def test_ask_answer_renders_citation_marks_as_links(make_window, db):
    """[1]을 눌러 근거로 갈 수 있어야 한다 — 목록을 따로 읽고 짝을 맞추게 하지 않는다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    view._render_answer(_answered())

    labels = [l.text() for l in view.findChildren(QLabel) if l.text()]
    assert any('<a href="1"' in t for t in labels), labels


def test_evidence_list_shows_every_citation_beside_the_answer(make_window, db):
    """근거는 답 옆에 상주한다 — 열고 닫게 하면 결국 아무도 안 누른다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    view._render_answer(_answered())

    body = chr(10).join(_labels(view))
    assert "2025_행감_제출자료.hwp" in body
    assert "정수장_운영현황.xlsx" in body


def test_clicking_a_citation_opens_that_document_on_the_right(make_window, db):
    """[n]은 '열기'가 아니라 '이 문서의 원문을 오른쪽에 펴라'는 뜻이다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    view._render_answer(_answered())

    view._show_citation("2")
    assert view._selected == 2
    picked = [
        w for w in view.findChildren(QFrame)
        if w.objectName() == "EvidencePick" and w.property("picked")
    ]
    assert len(picked) == 1
    assert "정수장_운영현황.xlsx" in chr(10).join(
        l.text() for l in picked[0].findChildren(QLabel) if l.text()
    )


def test_answer_text_with_angle_brackets_is_not_swallowed_as_markup(make_window, db):
    """문서에서 온 문장에 <, & 가 섞여도 답이 깨지면 안 된다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    view._render_answer(_answered(text="A<B 그리고 C&D 였다.[1]"))

    labels = [l.text() for l in view.findChildren(QLabel) if l.text()]
    assert any("A&lt;B" in t and "C&amp;D" in t for t in labels), labels


def test_new_question_clears_the_previous_evidence(make_window, db, monkeypatch):
    """지난 답의 근거가 남아 있으면 새 답의 근거로 오해한다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]
    monkeypatch.setattr(view._runner, "start", lambda q, scope=None: True)

    view._render_answer(_answered())
    view._show_citation("1")
    assert view._selected == 1

    view.input.setText("다른 질문")
    view._ask()
    assert view._selected is None
    assert view.reader_tabs.isHidden() or not view.reader_tabs.isVisible()


def test_followup_buttons_build_complete_standalone_questions(make_window, db):
    """이전 대화를 기억하는 대신 완성된 새 질문을 만든다 (기준서 §17)."""
    from app.ui.views.ask import _followup_questions

    suggestions = _followup_questions(_answered())
    assert suggestions
    for label, question in suggestions:
        assert label and question
        # 지시대명사만으로 이루어진 질문은 나중에 그 답을 검증할 수 없다.
        assert "그럼" not in question
        assert len(question) > len(label)


def test_followup_shifts_the_year_to_the_previous_one(make_window, db):
    from app.ui.views.ask import _followup_questions

    suggestions = dict(
        (label, question) for label, question in _followup_questions(_answered())
    )
    assert "2024년" in suggestions["지난해 자료와 비교"]


def test_withheld_answer_offers_no_followups(make_window, db):
    """답하지 못한 것에 '이어서 물어보기'를 붙이면 있지도 않은 답을 있는 것처럼 만든다."""
    from app.ui.views.ask import _followup_questions

    withheld = _answered(withheld=True, text="확인 가능한 자료가 부족합니다.",
                         citations=[], inferred_years=[])
    assert _followup_questions(withheld) == []


# ── 범위 드롭다운 (R9) ──────────────────────────────────────────────

def test_scope_dropdowns_default_to_everything(make_window, db):
    """기본은 전체다 — 사용자가 고르기 전에 몰래 좁히지 않는다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    assert view.task_scope.currentData() is None
    assert view.year_scope.currentData() is None
    assert view._chosen_scope().years == []
    assert view._chosen_scope().task_id is None


def test_scope_dropdowns_list_tasks_and_years(make_window, db):
    task_id = _seed_task(db)
    view = make_window(db).views["ask"]
    view.refresh()

    tasks = [view.task_scope.itemData(i) for i in range(view.task_scope.count())]
    years = [view.year_scope.itemData(i) for i in range(view.year_scope.count())]
    assert task_id in tasks
    assert 2023 in years


def test_choosing_a_scope_is_passed_to_the_engine(make_window, db, monkeypatch):
    task_id = _seed_task(db)
    view = make_window(db).views["ask"]
    view.refresh()

    captured = {}
    monkeypatch.setattr(
        view._runner, "start",
        lambda q, scope=None: captured.update(question=q, scope=scope) or True,
    )
    view.task_scope.setCurrentIndex(view.task_scope.findData(task_id))
    view.year_scope.setCurrentIndex(view.year_scope.findData(2023))
    view.input.setText("뭘 제출했어?")
    view._ask()

    assert captured["scope"].task_id == task_id
    assert captured["scope"].years == [2023]


def test_scope_selection_survives_a_refresh(make_window, db):
    """분석이 진행되며 목록이 자란다 — 그때마다 선택이 풀리면 안 된다."""
    task_id = _seed_task(db)
    view = make_window(db).views["ask"]
    view.refresh()

    view.task_scope.setCurrentIndex(view.task_scope.findData(task_id))
    view.refresh()
    assert view.task_scope.currentData() == task_id


# ── 근거 대조형 레이아웃 (디자인 개선안 1c) ─────────────────────────

def test_ask_screen_puts_the_source_text_next_to_the_answer(make_window, db, qapp):
    """답을 믿을지 판단하려면 원문을 봐야 한다. 원본을 열면 화면을 떠난다.

    그래서 원문을 앱 안 오른쪽 칸에 편다. 답변 칸은 읽기 좋은 폭으로 묶고,
    남는 폭은 전부 원문이 받는다 — 대조가 이 화면의 일이다.
    """
    from app.search.rag import Answer, Citation

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    window = make_window(db)
    window.resize(1400, 860)
    window.show()
    qapp.processEvents()
    window.go("ask")
    view = window.views["ask"]

    view._render_answer(Answer(
        question="질문", text="답변입니다.[1]", withheld=False,
        citations=[Citation(1, 1, "근거.hwp", "3쪽", r"D:\자료\근거.hwp", "인용문")],
    ))
    qapp.processEvents()

    assert not view.reader.isHidden()
    assert view.reader.width() > view.answer_pane.width()
    assert view.answer_pane.width() <= ask_view.ANSWER_MAX_W


def test_reader_still_fits_at_the_smallest_window(make_window, db, qapp):
    """가장 좁은 창에서도 두 칸이 선다.

    세로 사이드바(216px)를 걷어 낸 것이 이걸 가능하게 했다. 예전 구조에서는
    최소 창에서 원문 칸이 한 줄에 대여섯 글자가 되어 접어야 했다.
    """
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    window = make_window(db)
    window.show()
    window.resize(*theme.WINDOW_MIN)
    qapp.processEvents()
    window.go("ask")
    view = window.views["ask"]
    qapp.processEvents()

    assert not view.reader.isHidden()
    assert view.reader.width() >= 480


def test_navigation_lives_in_the_top_bar(make_window, db, qapp):
    """세로 사이드바를 걷어 내고 메뉴를 상단 띠로 옮겼다 (디자인 개선안 1c).

    본문이 폭을 다 받는 것이 이 변경의 목적이다 — 질문 화면이 '답변 | 원문'
    두 칸을 쓰는데 왼쪽에 216px 메뉴까지 서면 원문 칸이 대조하기 어려워진다.
    """
    _add_docs(db)
    window = make_window(db)
    window.resize(*theme.WINDOW_MIN)
    window.show()
    qapp.processEvents()

    assert [b.text() for b in window.topbar._buttons.values()] == [
        "업무", "일정", "문서", "질문"
    ]
    assert not hasattr(window, "sidebar")
    # 메뉴가 답하는 질문은 버리지 않고 툴팁으로 옮겼다.
    assert "내 업무는 무엇인가" in window.topbar._buttons["tasks"].toolTip()

    window.topbar._buttons["documents"].click()
    assert window.stack.currentWidget() is window.views["documents"]
    assert window.topbar._buttons["documents"].isChecked()

    # 본문은 창 폭을 그대로 받는다.
    assert window.stack.width() == window.width()


def test_top_bar_keeps_the_read_only_promise_and_the_progress(make_window, db, qapp):
    """사이드바에 있던 두 가지(읽기 전용 약속·인수인계 진행도)를 잃지 않는다."""
    from app.core import handover

    _add_docs(db)
    db.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    window = make_window(db)
    window.show()
    qapp.processEvents()

    assert window.topbar.promise.text() == "읽기 전용"
    assert not window.topbar.handover.isHidden()
    assert "인수인계" in window.topbar.handover.text()

    # 잴 것이 없으면 숨긴다 — 분석 전의 0%는 잘못된 질책이다.
    window.topbar.show_handover(handover.summarize({}))
    assert window.topbar.handover.isHidden()


def test_question_chips_sit_in_one_row(make_window, db):
    from PySide6.QtWidgets import QPushButton

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    chips = [b for b in view.findChildren(QPushButton) if b.objectName() == "Chip"]
    assert len(chips) == len(ask_view.EXAMPLES)


def test_past_questions_live_in_the_history_menu(make_window, db):
    """예시와 지난 질문을 두 목록으로 쌓으면 같은 문장이 두 번 보인다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    db.save_question("작년 행감 자료 뭐야?", "답", "[]", False, "m")
    view = make_window(db).views["ask"]
    view.refresh()

    assert "내 질문 기록 1건" in view.history.text()
    assert [a.text() for a in view.history.menu().actions()] == ["작년 행감 자료 뭐야?"]


def test_clicking_a_chip_asks_that_question(make_window, db, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    source_id = db.add_source(r"D:\자료")
    _seed_chunked_doc(db, source_id)
    view = make_window(db).views["ask"]

    captured = {}
    monkeypatch.setattr(view._runner, "start",
                        lambda q, scope=None: captured.update(q=q) or True)
    chip = next(b for b in view.findChildren(QPushButton) if b.objectName() == "Chip")
    chip.click()
    assert captured["q"] == chip.text()


# ── 읽히는 화면 ─────────────────────────────────────────────────────

def test_wrapped_text_gets_the_height_it_needs(qapp):
    """두 줄로 접힌 글자가 한 줄 높이만 받으면 아랫줄이 잘려 안 읽힌다.

    문서 화면 오른쪽 패널의 안내 문구가 실제로 그렇게 뭉개졌다. 원인은
    설치되지 않은 글꼴 이름이 QFont에 남아 **재는 글꼴과 그리는 글꼴이
    갈린** 것이었다(theme.font_family 참고).
    """
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

    from app.ui.widgets import UnknownBlock

    holder = QWidget()
    column = QVBoxLayout(holder)
    block = UnknownBlock(
        "파일 수정일로만 판정했습니다. 폴더째 복사하면 바뀌는 값이라 "
        "반복 주기 계산에서는 제외됩니다."
    )
    column.addWidget(block)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFixedWidth(360)
    scroll.setWidget(holder)
    scroll.resize(360, 400)
    scroll.show()
    qapp.processEvents()
    try:
        label = block.label
        assert label.height() >= label.heightForWidth(label.width())
        assert block.height() >= label.height()
    finally:
        scroll.close()
        scroll.deleteLater()


def test_the_font_actually_exists_on_this_machine():
    """없는 글꼴 이름을 쓰면 잰 높이와 그린 높이가 어긋난다."""
    from PySide6.QtGui import QFontDatabase

    name = theme.font_family()
    assert name, "설치된 후보 글꼴이 하나도 없다"
    assert name in set(QFontDatabase.families())
    assert name in theme.stylesheet("medium")


def test_every_screen_separates_controls_from_results(make_window, db, qapp):
    """조작하는 자리(흰 띠)와 결과를 읽는 자리(회색 바탕)를 가른다.

    같은 흰 바탕에 이어 놓으면 어디까지가 입력이고 어디부터가 결과인지
    눈이 매번 다시 찾는다 — 질문 화면이 그렇다는 지적을 받았다.
    """
    from PySide6.QtWidgets import QWidget

    _add_docs(db)
    window = make_window(db)
    for key in ("tasks", "documents", "ask"):
        window.go(key)
        view = window.views[key]
        headers = [
            w for w in view.findChildren(QWidget) if w.objectName() == "ViewHeader"
        ]
        assert headers, f"{key} 화면에 머리띠가 없다"
        assert view.objectName() == "Canvas", f"{key} 화면 바탕이 회색이 아니다"


# ── 글자 크기 하한 ──────────────────────────────────────────────────

def test_small_text_size_never_goes_below_the_readable_floor():
    """'작게'를 골랐을 때 부기 글자가 11px이 되어 한글이 뭉개졌다.

    알파벳은 10px에서도 읽히지만 한글은 자소 셋이 한 칸에 들어가 획이
    서로 붙는다. 비율로 줄이되 하한 아래로는 내리지 않는다.
    """
    for size in theme.TEXT_SIZE_ORDER:
        scale = theme.TEXT_SIZES[size]
        for base in (theme.FS_TITLE, theme.FS_SECTION, theme.FS_BODY, theme.FS_SMALL):
            assert theme._scaled(base, scale) >= theme.MIN_FONT_PX


def test_stylesheet_has_no_font_size_below_the_floor():
    """실제로 칠해지는 값에도 하한이 걸려야 한다 — 계산식만 고쳐서는 부족하다."""
    import re

    for size in theme.TEXT_SIZE_ORDER:
        sizes = [int(px) for px in re.findall(r"font-size:\s*(\d+)px", theme.stylesheet(size))]
        assert sizes, "스타일시트에 글자 크기가 없다"
        assert min(sizes) >= theme.MIN_FONT_PX, f"{size}: {min(sizes)}px"


def test_settings_preview_matches_the_size_actually_applied():
    """설정 화면이 '(11px)'이라고 적어 놓고 실제로는 12px이면 거짓말이다."""
    for size in theme.TEXT_SIZE_ORDER:
        assert theme.body_px(size) == theme._scaled(
            theme.FS_BODY, theme.TEXT_SIZES[size]
        )


def test_larger_sizes_still_grow():
    """하한을 넣었다고 '크게'가 안 커지면 접근성 설정이 무의미해진다."""
    assert theme.body_px("large") > theme.body_px("medium") > theme.body_px("small")
