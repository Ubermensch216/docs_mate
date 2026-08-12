"""색인 무결성 시험 (RAG 개선 R0).

질문 화면은 임베딩된 조각만 근거로 쓴다. 그래서 임베딩이 빠진 문서는
'조금 덜 정확한' 것이 아니라 **아예 존재하지 않는 것**이 된다.

실제 프로젝트 DB에서 최신 행정사무감사 문서 하나가 그 상태였다 — 조각 9개
전부 미임베딩. 후임자가 가장 먼저 물어볼 문서가 질문 화면에서 통째로 빠져
있었고, 앱을 다시 켜도 복구되지 않았다. 원인이 두 개라 여기서 둘 다 막는다.

  ① _chunks 단계가 '조각이 없는 문서'만 골랐다 — 조각은 있고 임베딩만
     빠진 문서는 다음부터 영원히 건너뛴다.
  ② 재개 판정이 chunks_checked 플래그만 봤다 — 한 번 서면 다시 안 돈다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.search.vector import pack

MODEL = "bge-m3"


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    project = Database(tmp_path / "project.db")
    project.init()
    source_id = project.add_source(tmp_path / "자료")

    for i in (1, 2):
        doc_id = project.upsert_document(source_id, {
            "path": str(tmp_path / f"문서{i}.hwp"), "filename": f"문서{i}.hwp",
            "ext": ".hwp", "parse_status": "ok",
        })
        project.replace_document_chunks(doc_id, [
            (1, "1문단", f"문서{i} 첫 문단입니다"),
            (2, "2문단", f"문서{i} 둘째 문단입니다"),
        ])
    yield project
    project.close()


def embed_all(db: Database, model: str = MODEL) -> None:
    for chunk_id, _text in db.con.execute("SELECT id, text FROM chunks").fetchall():
        db.save_chunk_embedding(chunk_id, model, 3, pack([0.1, 0.2, 0.3]))


def doc_id_of(db: Database, filename: str) -> int:
    return db.con.execute(
        "SELECT id FROM documents WHERE filename = ?", (filename,)
    ).fetchone()["id"]


# ── 빠진 것을 찾아내는가 ────────────────────────────────────────────

def test_document_with_chunks_but_no_embeddings_is_picked_up_again(db: Database):
    """이 시험이 이 파일의 존재 이유다. 조각만 있고 임베딩이 없는 문서."""
    embed_all(db)
    victim = doc_id_of(db, "문서1.hwp")
    db.con.execute(
        "DELETE FROM embeddings WHERE chunk_id IN "
        "(SELECT id FROM chunks WHERE doc_id = ?)", (victim,)
    )

    picked = [row["id"] for row in db.documents_needing_chunks(MODEL)]
    assert victim in picked, "임베딩이 빠진 문서를 다시 집어내지 못한다"


def test_fully_indexed_documents_are_left_alone(db: Database):
    embed_all(db)
    assert db.documents_needing_chunks(MODEL) == []


def test_document_without_any_chunks_is_picked_up(db: Database):
    embed_all(db)
    fresh = db.upsert_document(1, {
        "path": "/새문서.hwp", "filename": "새문서.hwp", "ext": ".hwp",
        "parse_status": "ok",
    })
    assert fresh in [row["id"] for row in db.documents_needing_chunks(MODEL)]


def test_changing_the_embedding_model_invalidates_old_vectors(db: Database):
    """모델을 바꾸면 옛 벡터는 검색에서 걸러진다 — 없는 것과 같다."""
    embed_all(db, model="옛모델")
    assert len(db.documents_needing_chunks(MODEL)) == 2
    assert db.unembedded_chunk_count(MODEL) == 4


def test_only_missing_chunks_are_re_embedded(db: Database):
    """이미 있는 임베딩까지 다시 만들면 재개가 매번 전체 작업이 된다."""
    embed_all(db)
    victim = doc_id_of(db, "문서1.hwp")
    one = db.con.execute(
        "SELECT id FROM chunks WHERE doc_id = ? ORDER BY ordinal LIMIT 1", (victim,)
    ).fetchone()["id"]
    db.con.execute("DELETE FROM embeddings WHERE chunk_id = ?", (one,))

    pending = db.chunks_needing_embedding(victim, MODEL)
    assert [c for c, _t in pending] == [one]


# ── 사용자에게 알리는가 ─────────────────────────────────────────────

def test_missing_documents_are_named_not_just_counted(db: Database):
    """숫자만 보여 주면 어느 자료가 빠졌는지 알 수 없다."""
    embed_all(db)
    victim = doc_id_of(db, "문서1.hwp")
    db.con.execute(
        "DELETE FROM embeddings WHERE chunk_id IN "
        "(SELECT id FROM chunks WHERE doc_id = ?)", (victim,)
    )

    missing = db.documents_missing_from_search()
    assert [m["filename"] for m in missing] == ["문서1.hwp"]


def test_partially_embedded_document_is_not_reported_as_missing(db: Database):
    """조각 하나라도 검색되면 그 문서는 '안 보이는' 것은 아니다."""
    embed_all(db)
    victim = doc_id_of(db, "문서1.hwp")
    one = db.con.execute(
        "SELECT id FROM chunks WHERE doc_id = ? LIMIT 1", (victim,)
    ).fetchone()["id"]
    db.con.execute("DELETE FROM embeddings WHERE chunk_id = ?", (one,))

    assert db.documents_missing_from_search() == []
    assert db.unembedded_chunk_count() == 1


# ── 재개가 실제로 도는가 ────────────────────────────────────────────

def test_resume_triggers_even_after_the_checked_flag_is_set(db: Database):
    """플래그가 섰어도 빠진 조각이 있으면 다시 돌아야 한다."""
    embed_all(db)
    db.set_meta("chunks_checked", "1")
    assert db.unembedded_chunk_count() == 0

    db.con.execute("DELETE FROM embeddings")
    assert db.unembedded_chunk_count() == 4

    # shell._resume_if_pending의 판정과 같은 식
    needs_chunks = db.get_meta("chunks_checked") is None or db.unembedded_chunk_count() > 0
    assert needs_chunks


def test_nothing_to_do_does_not_trigger_a_rerun(db: Database):
    embed_all(db)
    db.set_meta("chunks_checked", "1")
    needs_chunks = db.get_meta("chunks_checked") is None or db.unembedded_chunk_count() > 0
    assert not needs_chunks
