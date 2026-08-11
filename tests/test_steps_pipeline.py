"""처리 순서 재현 파이프라인 단계 통합 시험.

Pipeline._steps를 QThread 없이 직접 불러 검증한다. AI를 쓰지 않으므로
Ollama 유무와 무관하게 항상 같은 결과가 나와야 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    yield database
    database.close()


def _task(db: Database, name: str = "행정사무감사") -> int:
    db.con.execute(
        "INSERT INTO tasks(name, origin, status, confidence) "
        "VALUES (?, 'ai', 'proposed', 'medium')",
        (name,),
    )
    return db.con.execute("SELECT id FROM tasks WHERE name = ?", (name,)).fetchone()["id"]


def _doc(db: Database, source_id: int, task_id: int, filename: str,
         year: int, month: int, day: int | None, kind: str = "body") -> int:
    date_str = f"{year}-{month:02d}-{day:02d}" if day else f"{year}-{month:02d}-01"
    precision = "day" if day else "month"
    doc_id = db.upsert_document(source_id, {
        "path": rf"D:\자료\{filename}", "filename": filename, "ext": ".hwp",
        "parse_status": "ok", "hash": filename,
        "eff_year": year, "eff_month": month, "eff_date": date_str,
        "eff_precision": precision, "eff_date_kind": kind,
    })
    db.con.execute(
        "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
        (task_id, doc_id),
    )
    return doc_id


def _run_steps(db: Database) -> None:
    Pipeline(db.path)._steps(db)


def test_steps_are_reconstructed_in_chronological_order(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "2024_행정사무감사_의원질의답변.hwp", 2024, 11, 5)
    _doc(db, source_id, task_id, "2024_행정사무감사_요구자료접수.hwp", 2024, 9, 3)
    _doc(db, source_id, task_id, "2024_행정사무감사_제출자료.hwp", 2024, 10, 12)

    _run_steps(db)

    steps = db.task_steps(task_id, 2024)
    assert [s["label"] for s in steps] == ["접수", "제출", "질의응답 대응"]
    assert [s["ordinal"] for s in steps] == [1, 2, 3]


def test_every_step_carries_its_source_document(db: Database):
    """모든 단계는 문서에 앵커링된다 — 문서 없는 단계를 만들지 않는다."""
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "2024_제출자료.hwp", 2024, 10, 12)

    _run_steps(db)

    steps = db.task_steps(task_id, 2024)
    assert len(steps) == 1
    assert steps[0]["doc_id"] is not None
    assert steps[0]["filename"] == "2024_제출자료.hwp"
    assert steps[0]["path"] == r"D:\자료\2024_제출자료.hwp"


def test_gap_between_precise_dates_is_recorded(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "요구자료접수.hwp", 2024, 9, 3)
    _doc(db, source_id, task_id, "제출자료.hwp", 2024, 10, 12)   # 39일 뒤

    _run_steps(db)

    steps = db.task_steps(task_id, 2024)
    assert steps[1]["gap_note"] is not None
    assert "확인되지 않습니다" in steps[1]["gap_note"]


def test_filesystem_only_documents_are_excluded(db: Database):
    """파일 수정일로만 판정된 문서는 처리 순서에서도 뺀다."""
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "오염된문서.hwp", 2024, 9, 3, kind="fs")

    _run_steps(db)

    assert db.task_steps(task_id, 2024) == []


def test_duplicate_copies_produce_a_single_step(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    for i in range(3):
        db.upsert_document(source_id, {
            "path": rf"D:\자료\사본{i}.hwp", "filename": f"사본{i}.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": "SAME",
            "eff_year": 2024, "eff_month": 9, "eff_date": "2024-09-03",
            "eff_precision": "day", "eff_date_kind": "body",
        })
    ids = db.con.execute("SELECT id FROM documents WHERE hash = 'SAME'").fetchall()
    for row in ids:
        db.con.execute(
            "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
            (task_id, row["id"]),
        )

    _run_steps(db)

    assert len(db.task_steps(task_id, 2024)) == 1


def test_multiple_years_are_all_reconstructed(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "2023_제출.hwp", 2023, 10, 12)
    _doc(db, source_id, task_id, "2024_제출.hwp", 2024, 10, 12)

    _run_steps(db)

    assert len(db.task_steps(task_id, 2023)) == 1
    assert len(db.task_steps(task_id, 2024)) == 1
    assert db.task_years(task_id) == [2023, 2024]


def test_rerun_replaces_ai_steps_without_duplicating(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "제출.hwp", 2024, 10, 12)

    _run_steps(db)
    _run_steps(db)

    rows = db.con.execute(
        "SELECT COUNT(*) AS n FROM task_steps WHERE task_id = ? AND year = 2024", (task_id,)
    ).fetchone()
    assert rows["n"] == 1


def test_user_edited_step_survives_rerun(db: Database):
    """사람이 고친 단계는 재계산이 덮어쓰지 않는다 (NFR-SAF-004)."""
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "제출.hwp", 2024, 10, 12)
    _run_steps(db)

    db.con.execute(
        "INSERT INTO task_steps(task_id, year, ordinal, label, month, "
        "day_hint, decided_by) VALUES (?, 2024, 99, '사용자 단계', 10, '10월', 'user')",
        (task_id,),
    )

    _run_steps(db)

    labels = {r["label"] for r in db.task_steps(task_id, 2024)}
    assert "사용자 단계" in labels


def test_no_tasks_completes_without_error(db: Database):
    report_holder = []
    pipeline = Pipeline(db.path)
    pipeline.stage_done.connect(lambda r: report_holder.append(r))
    pipeline._steps(db)

    assert report_holder[0].note == "재구성할 자료가 없습니다"
    assert db.get_meta("steps_checked") == "1"


def test_task_years_ignores_untrustworthy_and_monthless_documents(db: Database):
    source_id = db.add_source(r"D:\자료")
    task_id = _task(db)
    _doc(db, source_id, task_id, "정상.hwp", 2024, 9, 3, kind="body")
    _doc(db, source_id, task_id, "오염.hwp", 2025, 9, 3, kind="fs")

    assert db.task_years(task_id) == [2024]
