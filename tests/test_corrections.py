"""교정 시험 — 이 제품의 핵심 계약.

계획서 §11의 마지막 문장이자 NFR-SAF-004: **사람이 수정한 값은 자동
재분석이 절대 덮어쓰지 않는다.**

DB가 그 원칙을 지키도록 만들어져 있어도, 재분석 경로가 실제로 그 값을
비켜 가는지는 돌려 봐야 안다. 그래서 여기서는 교정 종류마다
'교정 → 재분석이 하는 일을 그대로 재현 → 값이 살아 있는가'를 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import status
from app.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    project = Database(tmp_path / "project.db")
    project.init()
    source_id = project.add_source(tmp_path / "자료")

    for i in range(1, 6):
        project.upsert_document(source_id, {
            "path": str(tmp_path / f"문서{i}.hwp"),
            "filename": f"문서{i}.hwp",
            "ext": ".hwp",
            "parse_status": "ok",
            "eff_date": f"202{i}-09-01",
            "eff_date_kind": "body",
            "eff_precision": "day",
            "eff_year": 2020 + i,
            "eff_month": 9,
        })

    project.con.execute(
        "INSERT INTO tasks(id, name, description, confidence) "
        "VALUES (1, '행정사무감사', '요구자료를 취합해 제출합니다', 'high')"
    )
    project.con.execute(
        "INSERT INTO tasks(id, name, confidence) VALUES (2, '행감 대응', 'medium')"
    )
    for doc_id in (1, 2, 3):
        project.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, ?)", (doc_id,))
    for doc_id in (4, 5):
        project.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (2, ?)", (doc_id,))

    yield project
    project.close()


def reanalyze_cycles(db: Database, task_id: int) -> None:
    """_cycles 단계가 하는 일. AI가 매번 새로 제안한다고 가정한다."""
    db.replace_task_cycles(task_id, [{
        "kind": "yearly", "months": "3", "day_hint": None,
        "years_observed": 4, "confidence": "high", "evidence": None,
    }])


def reanalyze_steps(db: Database, task_id: int, year: int) -> None:
    """_steps 단계가 하는 일."""
    db.replace_task_steps(task_id, year, [
        {"ordinal": 1, "label": "AI가 새로 만든 단계", "month": 9,
         "day_hint": "9월 초", "doc_id": 1, "gap_note": None},
    ])


# ── 업무 ────────────────────────────────────────────────────────────

def test_renamed_task_survives(db: Database):
    assert db.rename_task(1, "행정사무감사 대응")
    assert db.task(1)["name"] == "행정사무감사 대응"
    assert status.of_task(db.task(1)) == status.CONFIRMED


def test_rename_refuses_duplicate_names(db: Database):
    assert db.rename_task(1, "행감 대응") is False
    assert db.task(1)["name"] == "행정사무감사"


def test_description_edit_is_recorded_and_confirmed(db: Database):
    db.edit_task_description(1, "의회 요구자료 접수부터 질의대응까지")
    row = db.task(1)
    assert row["description"] == "의회 요구자료 접수부터 질의대응까지"
    assert status.of_task(row) == status.CONFIRMED
    assert any(c["field"] == "description" for c in db.corrections())


def test_not_a_task_hides_it_without_deleting(db: Database):
    """지우지 않는다 — 재분석이 같은 묶음을 만들어도 다시 묻지 않기 위해서다."""
    db.mark_not_a_task(2)
    assert [t["id"] for t in db.tasks()] == [1]
    assert db.task(2) is not None
    assert db.task(2)["not_a_task"] == 1


def test_merge_moves_documents_and_keeps_a_trace(db: Database):
    assert db.merge_tasks(2, 1)
    assert [t["id"] for t in db.tasks()] == [1]
    assert {d["id"] for d in db.task_documents(1)} == {1, 2, 3, 4, 5}
    assert db.task(2)["merged_into"] == 1


def test_merge_does_not_duplicate_shared_documents(db: Database):
    db.assign_document(2, 1)          # 문서1이 두 업무에 걸쳐 있다
    assert db.merge_tasks(2, 1)
    ids = [d["id"] for d in db.task_documents(1)]
    assert len(ids) == len(set(ids))


def test_merge_refuses_itself(db: Database):
    assert db.merge_tasks(1, 1) is False


def test_split_creates_a_new_task_with_the_chosen_documents(db: Database):
    new_id = db.split_task(1, [2, 3], "행감 질의대응")
    assert new_id is not None
    assert {d["id"] for d in db.task_documents(new_id)} == {2, 3}
    assert {d["id"] for d in db.task_documents(1)} == {1}
    assert status.of_task(db.task(new_id)) == status.CONFIRMED


def test_split_refuses_an_existing_name(db: Database):
    assert db.split_task(1, [2], "행감 대응") is None


# ── 문서 배정 ───────────────────────────────────────────────────────

def test_document_can_belong_to_several_tasks(db: Database):
    db.assign_document(2, 1)
    assert {t["id"] for t in db.document_tasks(1)} == {1, 2}


def test_move_document_between_tasks(db: Database):
    db.move_document(1, 2, 3)
    assert 3 not in {d["id"] for d in db.task_documents(1)}
    assert 3 in {d["id"] for d in db.task_documents(2)}


def test_primary_document_is_unique_per_task(db: Database):
    db.set_primary_document(1, 2)
    db.set_primary_document(1, 3)
    assert db.primary_document(1)["id"] == 3
    marked = db.con.execute(
        "SELECT COUNT(*) FROM task_docs WHERE task_id = 1 AND is_primary = 1"
    ).fetchone()[0]
    assert marked == 1


def test_unclassified_documents_are_listed(db: Database):
    db.detach_document(1, 3)
    assert [d["id"] for d in db.unclassified_documents()] == [3]
    assert db.unclassified_count() == 1


# ── 시점 ────────────────────────────────────────────────────────────

def test_corrected_date_survives_reanalysis(db: Database):
    db.set_document_date(1, "2024-11-20", precision="day")
    row = db.document(1)
    assert row["eff_date"] == "2024-11-20"
    assert row["eff_year"] == 2024 and row["eff_month"] == 11
    assert status.of_document_date(row) == status.CONFIRMED


def test_unknown_date_is_a_decision_not_a_gap(db: Database):
    """'모름'을 확정하는 것도 판단이다. 재분석이 빈칸을 보고 다시 채우면 안 된다."""
    db.set_document_date(1, None)
    row = db.document(1)
    assert row["eff_date"] is None
    assert row["date_decided_by"] == "user"

    # _date 단계가 고르는 대상에서 실제로 빠지는가 (pipeline._date의 질의와 같다)
    targets = [r["id"] for r in db.con.execute(
        "SELECT id FROM documents WHERE eff_date IS NULL AND missing_since IS NULL "
        "AND parse_status != 'skipped' AND date_decided_by != 'user'"
    )]
    assert 1 not in targets


# ── 주기 (When) ─────────────────────────────────────────────────────

def test_user_cycle_survives_reanalysis(db: Database):
    db.set_task_cycle(1, "yearly", "9,10,11")
    reanalyze_cycles(db, 1)

    cycle = db.task_cycle(1)
    assert cycle["months"] == "9,10,11", "재분석이 사용자 주기를 덮었다"
    assert status.of_cycle(cycle) == status.CONFIRMED


def test_confirming_an_ai_cycle_keeps_the_value_and_raises_the_state(db: Database):
    reanalyze_cycles(db, 1)
    assert db.confirm_task_cycle(1)
    cycle = db.task_cycle(1)
    assert cycle["months"] == "3"
    assert status.of_cycle(cycle) == status.CONFIRMED


def test_no_cycle_survives_reanalysis(db: Database):
    """'반복 아님'은 빈칸이 아니라 판정이다."""
    db.mark_no_cycle(1)
    reanalyze_cycles(db, 1)
    assert db.task_cycle(1)["kind"] == "none"


def test_only_one_cycle_wins_after_correction(db: Database):
    """사람 것과 AI 것이 나란히 남으면 화면이 어느 쪽을 고를지 알 수 없다."""
    reanalyze_cycles(db, 1)
    db.confirm_task_cycle(1)
    reanalyze_cycles(db, 1)
    rows = db.con.execute("SELECT * FROM task_cycles WHERE task_id = 1").fetchall()
    assert len(rows) == 1


# ── 처리 순서 (How) ─────────────────────────────────────────────────

@pytest.fixture()
def with_steps(db: Database) -> Database:
    db.replace_task_steps(1, 2025, [
        {"ordinal": 1, "label": "요구자료 접수", "month": 9, "day_hint": "9월 초",
         "doc_id": 1, "gap_note": None},
        {"ordinal": 2, "label": "자료 취합", "month": 10, "day_hint": "10월 초",
         "doc_id": 2, "gap_note": None},
        {"ordinal": 3, "label": "최종 제출", "month": 11, "day_hint": "11월 초",
         "doc_id": 3, "gap_note": None},
    ])
    return db


def test_renamed_step_survives_reanalysis(with_steps: Database):
    db = with_steps
    step_id = db.task_steps(1, 2025)[0]["id"]
    db.edit_step_label(step_id, "의회 요구자료 접수")
    reanalyze_steps(db, 1, 2025)

    labels = [s["label"] for s in db.task_steps(1, 2025)]
    assert labels[0] == "의회 요구자료 접수"
    assert "AI가 새로 만든 단계" not in labels


def test_reordering_steps_swaps_them(with_steps: Database):
    db = with_steps
    second = db.task_steps(1, 2025)[1]["id"]
    db.move_step(second, -1)
    assert [s["label"] for s in db.task_steps(1, 2025)] == [
        "자료 취합", "요구자료 접수", "최종 제출",
    ]


def test_moving_the_first_step_up_does_nothing(with_steps: Database):
    db = with_steps
    first = db.task_steps(1, 2025)[0]["id"]
    db.move_step(first, -1)
    assert [s["ordinal"] for s in db.task_steps(1, 2025)] == [1, 2, 3]


def test_deleting_a_step_closes_the_gap(with_steps: Database):
    db = with_steps
    second = db.task_steps(1, 2025)[1]["id"]
    db.delete_step(second)
    steps = db.task_steps(1, 2025)
    assert [s["label"] for s in steps] == ["요구자료 접수", "최종 제출"]
    assert [s["ordinal"] for s in steps] == [1, 2]


def test_added_step_without_evidence_is_marked_inferred(with_steps: Database):
    """사람이 채운 칸도 근거가 없으면 없다고 표시한다 — 화면에서 점선이 된다."""
    db = with_steps
    db.add_step(1, 2025, "사전검토", after_ordinal=1)
    steps = db.task_steps(1, 2025)
    assert [s["label"] for s in steps] == [
        "요구자료 접수", "사전검토", "자료 취합", "최종 제출",
    ]
    added = steps[1]
    assert added["is_inferred"] == 1
    assert status.of_step(added) == status.CONFIRMED   # 사람이 넣었으니 확정이다


def test_step_evidence_can_be_changed(with_steps: Database):
    db = with_steps
    step_id = db.task_steps(1, 2025)[0]["id"]
    db.set_step_document(step_id, 5)
    assert db.task_steps(1, 2025)[0]["doc_id"] == 5


def test_untouched_year_is_still_reanalyzed(with_steps: Database):
    """보호는 사람이 만진 연도에만 걸린다. 나머지는 계속 좋아져야 한다."""
    db = with_steps
    db.edit_step_label(db.task_steps(1, 2025)[0]["id"], "손댄 단계")
    reanalyze_steps(db, 1, 2024)
    assert [s["label"] for s in db.task_steps(1, 2024)] == ["AI가 새로 만든 단계"]
    assert db.task_steps(1, 2025)[0]["label"] == "손댄 단계"


# ── 기록 ────────────────────────────────────────────────────────────

def test_every_correction_leaves_a_trail(db: Database):
    """되돌릴 수 없는 교정은 사용자가 무서워서 못 쓴다."""
    db.rename_task(1, "새 이름")
    db.set_document_date(1, "2024-01-01")
    db.set_task_cycle(1, "yearly", "5")

    fields = {c["field"] for c in db.corrections()}
    assert {"name", "eff_date", "months"} <= fields


def test_audit_log_keeps_identifiers_not_content(db: Database):
    """로그에 본문이 새면 DB 하나가 곧 유출이다 (SEC-005)."""
    db.set_document_date(1, "2024-01-01")
    db.edit_task_description(1, "여기에 민감한 업무 설명이 들어간다")

    blob = " ".join(
        str(r["action"]) + str(r["target"] or "") + str(r["detail"] or "")
        for r in db.recent_audit()
    )
    assert "민감한 업무 설명" not in blob
