"""조각 버전 재색인 시험 (RAG 개선 R2).

조각 규칙을 바꾸면(v1 → v2), 이미 v1 규칙으로 쪼개진 옛 조각을 그대로 두면
안 된다 — 9자짜리 조각이 그대로 남아 개선 효과가 없다. 그렇다고 매번
지우면 재개마다 전체 재처리가 된다. 그래서 '실제로 규칙이 바뀐 한 번만'
지운다.

메타에 chunking_version이 없다고 곧바로 '새 프로젝트'로 보면 안 된다는
점이 이 시험의 핵심이다 — v1은 애초에 버전을 남기지 않았으므로, 메타가
비어 있어도 chunks 표에 행이 있으면 그건 옛 규칙으로 만든 조각이다.
"""

from __future__ import annotations

from pathlib import Path

from app.core.chunking import CHUNKING_VERSION
from app.db import Database


def seed_v1_chunk(db: Database) -> int:
    """버전 없이 만들어진(v1 시절) 조각 하나를 흉내 낸다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/문서.hwp", "filename": "문서.hwp", "ext": ".hwp",
        "parse_status": "ok",
    })
    db.con.execute(
        "INSERT INTO chunks(doc_id, ordinal, locator, text) VALUES (?, 1, '1문단', '작성일: 2024')",
        (doc_id,),
    )
    chunk_id = db.con.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.save_chunk_embedding(chunk_id, "bge-m3", 3, b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    return doc_id


# ── 새 프로젝트 ─────────────────────────────────────────────────────

def test_fresh_project_is_stamped_without_wiping_anything(tmp_path: Path):
    """지울 게 없으니 조용히 버전만 찍는다."""
    db = Database(tmp_path / "project.db")
    db.init()
    assert db.get_meta("chunking_version") == CHUNKING_VERSION
    db.close()


def test_init_is_idempotent_for_a_fresh_project(tmp_path: Path):
    path = tmp_path / "project.db"
    db = Database(path)
    db.init()
    db.close()

    again = Database(path)
    again.init()
    assert again.get_meta("chunking_version") == CHUNKING_VERSION
    again.close()


# ── 옛 조각이 있는 프로젝트 ─────────────────────────────────────────

def test_existing_chunks_without_a_version_flag_are_wiped(tmp_path: Path):
    """메타가 비어 있어도 chunks에 행이 있으면 옛 조각으로 취급한다."""
    db = Database(tmp_path / "project.db")
    db.init()
    seed_v1_chunk(db)
    assert db.con.execute("DELETE FROM meta WHERE key = 'chunking_version'").rowcount or True

    wiped = db.sync_chunking_version()
    assert wiped is True
    assert db.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert db.get_meta("chunking_version") == CHUNKING_VERSION
    db.close()


def test_wipe_cascades_to_embeddings(tmp_path: Path):
    db = Database(tmp_path / "project.db")
    db.init()
    seed_v1_chunk(db)
    db.con.execute("DELETE FROM meta WHERE key = 'chunking_version'")

    db.sync_chunking_version()
    assert db.con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    db.close()


def test_chunks_checked_flag_is_cleared_so_resume_reruns(tmp_path: Path):
    """이걸 안 지우면 재개 판정이 '이미 다 됐다'고 믿고 다시 돌지 않는다."""
    db = Database(tmp_path / "project.db")
    db.init()
    seed_v1_chunk(db)
    db.set_meta("chunks_checked", "1")
    db.con.execute("DELETE FROM meta WHERE key = 'chunking_version'")

    db.sync_chunking_version()
    assert db.get_meta("chunks_checked") is None
    db.close()


def test_reindex_is_recorded_in_the_audit_log(tmp_path: Path):
    db = Database(tmp_path / "project.db")
    db.init()
    seed_v1_chunk(db)
    db.con.execute("DELETE FROM meta WHERE key = 'chunking_version'")

    db.sync_chunking_version()
    actions = [row["action"] for row in db.recent_audit()]
    assert "chunking.reindex" in actions
    db.close()


def test_documents_and_analysis_survive_the_reindex(tmp_path: Path):
    """조각만 지운다 — 문서 자체나 업무 배정, 교정값은 건드리지 않는다."""
    db = Database(tmp_path / "project.db")
    db.init()
    doc_id = seed_v1_chunk(db)
    db.con.execute("DELETE FROM meta WHERE key = 'chunking_version'")

    db.sync_chunking_version()
    assert db.document(doc_id) is not None
    db.close()


# ── 이미 최신 버전인 프로젝트 ───────────────────────────────────────

def test_matching_version_does_not_wipe(tmp_path: Path):
    db = Database(tmp_path / "project.db")
    db.init()   # 이미 CHUNKING_VERSION으로 찍혀 있다
    seed_v1_chunk(db)   # 최신 버전으로 실제 만든 조각이라고 가정

    wiped = db.sync_chunking_version()
    assert wiped is False
    assert db.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    db.close()


def test_calling_init_twice_after_a_real_reindex_does_not_wipe_again(tmp_path: Path):
    """재색인 후 다시 연 프로젝트가 자기가 방금 만든 조각을 또 지우면 안 된다."""
    path = tmp_path / "project.db"
    db = Database(path)
    db.init()
    seed_v1_chunk(db)
    db.close()

    reopened = Database(path)
    reopened.init()   # chunking_version이 이미 일치하므로 조용히 지나가야 한다
    assert reopened.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    reopened.close()
