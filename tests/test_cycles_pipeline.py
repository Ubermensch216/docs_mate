"""주기 계산 파이프라인 단계 통합 시험.

Pipeline._cycles를 실제 QThread 없이 직접 불러 검증한다. AI를 쓰지 않는
단계이므로 Ollama 유무와 무관하게 항상 같은 결과가 나와야 한다.
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


def _task_with_docs(
    db: Database,
    name: str,
    year_months: list[tuple[int, int, str]],
    source_id: int,
) -> int:
    """(year, month, date_kind) 목록으로 문서를 만들고 업무 하나에 몰아 붙인다."""
    db.con.execute(
        "INSERT INTO tasks(name, origin, status, confidence) "
        "VALUES (?, 'ai', 'proposed', 'medium')",
        (name,),
    )
    task_id = db.con.execute("SELECT id FROM tasks WHERE name = ?", (name,)).fetchone()["id"]

    for i, (year, month, kind) in enumerate(year_months):
        path = rf"D:\자료\{name}_{year}_{month}_{i}.hwp"
        doc_id = db.upsert_document(source_id, {
            "path": path, "filename": f"{name}{i}.hwp", "ext": ".hwp",
            "parse_status": "ok", "hash": f"{name}{year}{month}{i}",
            "eff_year": year, "eff_month": month,
            "eff_date": f"{year}-{month:02d}-12", "eff_precision": "day",
            "eff_date_kind": kind,
        })
        db.con.execute(
            "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
            (task_id, doc_id),
        )
    return task_id


def _run_cycles(db: Database) -> None:
    Pipeline(db.path)._cycles(db)


def test_recurring_task_gets_a_cycle(db: Database):
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, m) for y in (2022, 2023, 2024, 2025) for m in (9, 10, 11)]
    task_id = _task_with_docs(db, "행정사무감사", [(y, m, "body") for y, m in year_months], source_id)

    _run_cycles(db)

    cycle = db.task_cycle(task_id)
    assert cycle is not None
    assert cycle["kind"] == "yearly"
    assert cycle["months"] == "9,10,11"
    assert cycle["confidence"] == "high"


def test_single_year_task_gets_no_cycle(db: Database):
    """자료가 1개 연도뿐이면 반복을 주장하지 않는다."""
    source_id = db.add_source(r"D:\자료")
    task_id = _task_with_docs(
        db, "단발성업무", [(2025, 9, "body"), (2025, 10, "body")], source_id
    )

    _run_cycles(db)

    assert db.task_cycle(task_id) is None


def test_filesystem_only_dates_are_excluded_from_cycle_detection(db: Database):
    """파일 수정일로만 판정된 문서는 주기 계산에 넣지 않는다 (doc/00 §8.1)."""
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, 9, "fs") for y in (2022, 2023, 2024, 2025)]
    task_id = _task_with_docs(db, "오염된업무", year_months, source_id)

    _run_cycles(db)

    assert db.task_cycle(task_id) is None


def test_mixed_trustworthy_and_untrustworthy_dates(db: Database):
    """신뢰 가능한 근거만으로도 3개 연도가 있으면 주기를 찾는다."""
    source_id = db.add_source(r"D:\자료")
    year_months = [
        (2022, 9, "body"), (2023, 9, "body"), (2024, 9, "body"),
        (2025, 9, "fs"),   # 이건 근거로 못 쓴다
    ]
    task_id = _task_with_docs(db, "부분신뢰업무", year_months, source_id)

    _run_cycles(db)

    cycle = db.task_cycle(task_id)
    assert cycle is not None
    assert cycle["years_observed"] == 3


def test_monthly_pattern_gets_day_hint(db: Database):
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, m, "body") for y in (2024, 2025) for m in range(1, 13)]
    task_id = _task_with_docs(db, "월간실적", year_months, source_id)

    _run_cycles(db)

    cycle = db.task_cycle(task_id)
    assert cycle["kind"] == "monthly"
    assert cycle["day_hint"] == "12일"   # 표본은 모두 12일에 만들었다


def test_yearly_pattern_omits_day_hint(db: Database):
    """연간 반복에 '9~11월 12일'처럼 붙이면 어느 달인지 모호해진다."""
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, m, "body") for y in (2022, 2023, 2024) for m in (9, 10, 11)]
    task_id = _task_with_docs(db, "행정사무감사", year_months, source_id)

    _run_cycles(db)

    assert db.task_cycle(task_id)["day_hint"] is None


def test_rerun_replaces_ai_cycles_without_duplicating(db: Database):
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, 9, "body") for y in (2022, 2023, 2024)]
    task_id = _task_with_docs(db, "행정사무감사", year_months, source_id)

    _run_cycles(db)
    _run_cycles(db)

    rows = db.con.execute(
        "SELECT COUNT(*) AS n FROM task_cycles WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert rows["n"] == 1


def test_no_tasks_reports_gracefully(db: Database):
    report_holder = []
    pipeline = Pipeline(db.path)
    pipeline.stage_done.connect(lambda r: report_holder.append(r))
    pipeline._cycles(db)

    assert report_holder[0].note == "업무가 없습니다"
    assert db.get_meta("cycles_checked") == "1"


def test_duplicate_copies_do_not_inflate_years_observed(db: Database):
    """완전 중복본이 여러 번 들어와도 근거 세기가 부풀면 안 된다."""
    source_id = db.add_source(r"D:\자료")
    db.con.execute(
        "INSERT INTO tasks(name, origin, status, confidence) "
        "VALUES ('중복업무', 'ai', 'proposed', 'medium')"
    )
    task_id = db.con.execute("SELECT id FROM tasks").fetchone()["id"]

    for year in (2022, 2023, 2024):
        for copy in range(3):   # 매년 같은 문서를 3부씩 복사해 뒀다고 가정
            doc_id = db.upsert_document(source_id, {
                "path": rf"D:\자료\{year}_사본{copy}.hwp",
                "filename": f"{year}_사본{copy}.hwp", "ext": ".hwp",
                "parse_status": "ok", "hash": f"SAME{year}",
                "eff_year": year, "eff_month": 9,
                "eff_date": f"{year}-09-12", "eff_precision": "day",
                "eff_date_kind": "body",
            })
            db.con.execute(
                "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'ai')",
                (task_id, doc_id),
            )

    _run_cycles(db)
    cycle = db.task_cycle(task_id)
    assert cycle["years_observed"] == 3


# ── repo 헬퍼 ───────────────────────────────────────────────────────

def test_all_cycles_joins_task_name(db: Database):
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, 9, "body") for y in (2022, 2023, 2024)]
    _task_with_docs(db, "행정사무감사", year_months, source_id)
    _run_cycles(db)

    rows = db.all_cycles()
    assert len(rows) == 1
    assert rows[0]["task_name"] == "행정사무감사"


def test_replace_task_cycles_preserves_user_decided_rows(db: Database):
    """사람이 확정한 주기는 재계산이 지우지 않는다 (NFR-SAF-004 대비)."""
    source_id = db.add_source(r"D:\자료")
    year_months = [(y, 9, "body") for y in (2022, 2023, 2024)]
    task_id = _task_with_docs(db, "행정사무감사", year_months, source_id)

    db.con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, "
        "confidence, decided_by) VALUES (?, 'yearly', '3', 1, 'high', 'user')",
        (task_id,),
    )

    _run_cycles(db)

    rows = db.con.execute(
        "SELECT decided_by FROM task_cycles WHERE task_id = ?", (task_id,)
    ).fetchall()
    kinds = {r["decided_by"] for r in rows}
    assert "user" in kinds
