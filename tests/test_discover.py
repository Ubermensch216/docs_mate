"""업무 발견 통합 시험.

계약 세 가지.
  1. 모델이 없어도 업무 목록이 나온다 (파일명 공통 낱말로 이름을 대신한다)
  2. 사용자가 고친 업무와 직접 배정한 문서를 재발견이 덮어쓰지 않는다
  3. 같은 이름이 나온 묶음은 하나로 합친다 — 업무 분류가 흩어지지 않게
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.ai.discover import discover
from app.db import Database
from app.search import pack


@pytest.fixture
def project(tmp_path: Path):
    db = Database(tmp_path / "p.db")
    db.init()
    yield db
    db.close()


def _seed(db: Database, groups: dict[str, int], dims: int = 6) -> dict[str, list[int]]:
    """업무별로 서로 먼 벡터 묶음을 심는다."""
    source_id = db.add_source(r"D:\자료")
    made: dict[str, list[int]] = {}
    for axis, (label, count) in enumerate(groups.items()):
        ids = []
        for index in range(count):
            name = f"202{index % 4 + 2}_{label}_자료{index}.hwp"
            doc_id = db.upsert_document(source_id, {
                "path": rf"D:\자료\{name}", "filename": name, "ext": ".hwp",
                "parse_status": "ok", "hash": f"{label}{index}",
                "char_count": 800, "eff_year": 2022 + index % 4,
                "eff_date": f"202{index % 4 + 2}-09-01", "eff_date_kind": "body",
            })
            vector = [0.02] * dims
            vector[axis] = 1.0
            db.save_doc_embedding(doc_id, "bge-m3", dims, pack(vector), 100)
            ids.append(doc_id)
        made[label] = ids
    return made


def test_discovery_works_without_a_model(project):
    """Ollama가 없어도 업무 목록이 나와야 한다."""
    _seed(project, {"행정사무감사": 5, "예산관리": 4})
    result = discover(project, client=None, min_size=3)

    assert result.clusters == 2
    assert result.tasks == 2
    assert result.named_by_model == 0
    assert result.named_by_filename == 2

    names = {row["name"] for row in project.tasks()}
    assert "행정사무감사" in names
    assert "예산관리" in names


def test_documents_are_attached_and_reading_list_is_built(project):
    seeded = _seed(project, {"행정사무감사": 5})
    discover(project, client=None, min_size=3)

    task = project.tasks()[0]
    assert task["doc_count"] == 5
    picks = project.task_reading(task["id"])
    assert picks, "먼저 읽을 문서를 고르지 못했습니다"
    assert all(pick["reason"] for pick in picks), "이유 없는 추천이 있습니다"
    assert {p["doc_id"] for p in picks} <= set(seeded["행정사무감사"])


def test_small_groups_stay_unclassified(project):
    _seed(project, {"행정사무감사": 5, "잡다": 2})
    result = discover(project, client=None, min_size=3)

    assert result.tasks == 1
    assert result.unassigned == 2
    assert project.unclassified_count() == 2


def test_rediscovery_is_idempotent(project):
    _seed(project, {"행정사무감사": 5, "예산관리": 4})
    discover(project, client=None, min_size=3)
    first = {(r["name"], r["doc_count"]) for r in project.tasks()}

    discover(project, client=None, min_size=3)
    assert {(r["name"], r["doc_count"]) for r in project.tasks()} == first


def test_user_renamed_task_survives_rediscovery(project):
    """사람이 고친 값은 재분석이 덮어쓰지 않는다 (NFR-SAF-004)."""
    _seed(project, {"행정사무감사": 5})
    discover(project, client=None, min_size=3)

    task_id = project.tasks()[0]["id"]
    assert project.rename_task(task_id, "의회 대응")

    discover(project, client=None, min_size=3)
    names = {row["name"] for row in project.tasks()}
    assert "의회 대응" in names

    row = project.task(task_id)
    assert row["status"] == "edited"
    assert row["origin"] == "user"


def test_manually_assigned_document_is_not_reassigned(project):
    seeded = _seed(project, {"행정사무감사": 5, "예산관리": 4})
    discover(project, client=None, min_size=3)

    # 사용자가 예산 문서 하나를 행감 업무로 직접 옮긴 상황
    audit_task = next(r["id"] for r in project.tasks() if "행정" in r["name"])
    moved = seeded["예산관리"][0]
    project.con.execute("DELETE FROM task_docs WHERE doc_id = ?", (moved,))
    project.con.execute(
        "INSERT INTO task_docs(task_id, doc_id, origin) VALUES (?, ?, 'user')",
        (audit_task, moved),
    )

    discover(project, client=None, min_size=3)
    rows = project.con.execute(
        "SELECT task_id, origin FROM task_docs WHERE doc_id = ?", (moved,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["task_id"] == audit_task
    assert rows[0]["origin"] == "user"


def test_rename_rejects_a_name_already_in_use(project):
    _seed(project, {"행정사무감사": 5, "예산관리": 4})
    discover(project, client=None, min_size=3)
    tasks = project.tasks()
    assert project.rename_task(tasks[0]["id"], tasks[1]["name"]) is False


def test_detaching_a_document_removes_it_from_the_reading_list(project):
    _seed(project, {"행정사무감사": 5})
    discover(project, client=None, min_size=3)
    task_id = project.tasks()[0]["id"]
    victim = project.task_reading(task_id)[0]["doc_id"]

    project.detach_document(task_id, victim)

    assert victim not in {r["doc_id"] for r in project.task_reading(task_id)}
    assert victim not in {r["id"] for r in project.task_documents(task_id)}
    corrections = project.con.execute("SELECT COUNT(*) AS n FROM corrections").fetchone()
    assert corrections["n"] >= 1, "교정 이력이 남지 않았습니다"


def test_discovery_without_embeddings_reports_why(project):
    project.add_source(r"D:\자료")
    result = discover(project, client=None)
    assert result.tasks == 0
    assert result.errors and "색인" in result.errors[0]


def test_duplicate_copies_are_not_recommended_twice(project):
    """추천 다섯 칸이 같은 문서로 채워지면 안 된다."""
    source_id = project.add_source(r"D:\중복자료")
    for index in range(4):
        name = f"2025_실적_사본{index}.xlsx"
        doc_id = project.upsert_document(source_id, {
            "path": rf"D:\중복자료\{name}", "filename": name, "ext": ".xlsx",
            "parse_status": "ok", "hash": "SAME", "char_count": 500,
            "eff_year": 2025, "eff_date": "2025-08-01", "eff_date_kind": "body",
        })
        project.save_doc_embedding(doc_id, "bge-m3", 3, pack([1.0, 0.01, 0.01]), 100)

    discover(project, client=None, min_size=3)
    picks = project.task_reading(project.tasks()[0]["id"])
    assert len(picks) == 1, [p["filename"] for p in picks]
