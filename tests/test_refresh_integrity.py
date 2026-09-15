"""원본 갱신·접근 복구가 검색과 사용자 교정에 미치는 영향."""
import os
from pathlib import Path

import pytest

from app.db import Database
from app.ingest.scanner import scan_source
from app.jobs.pipeline import Pipeline
from app.search.vector import pack


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "report.txt"
    file.write_text("2023-03-01 old budget 820", encoding="utf-8")
    db = Database(tmp_path / "p.db")
    db.init()
    sid = db.add_source(source)
    scan_source(db, sid, source)
    worker = Pipeline(db.path)
    worker._hash(db)
    worker._parse(db)
    worker._date(db)
    db.save_doc_embedding(1, "bge-m3", 2, pack([1, 0]), 20)
    cid = db.replace_document_chunks(1, [(1, "1", "old budget 820")])[0][0]
    db.save_chunk_embedding(cid, "bge-m3", 2, pack([1, 0]))
    yield db, source, file, sid, worker
    db.close()


def test_changed_document_invalidates_every_automatic_search_layer(project):
    db, source, file, sid, worker = project
    file.write_text("2026-08-15 revised budget amount 1820", encoding="utf-8")
    scan_source(db, sid, source)
    assert db.document(1)["eff_date"] is None
    for table in ("chunks", "embeddings", "doc_embeddings", "document_index"):
        assert db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    worker._hash(db)
    worker._parse(db)
    worker._date(db)
    assert db.document(1)["eff_year"] == 2026
    assert "1820" in db.sections(1)[0]["text"]
    assert [row["id"] for row in db.documents_needing_chunks("bge-m3")] == [1]


def test_user_date_and_task_link_survive_document_change(project):
    db, source, file, sid, worker = project
    db.set_document_date(1, "2024-07-02", "day")
    db.con.execute("INSERT INTO tasks(id,name,status) VALUES(1,'confirmed','approved')")
    db.assign_document(1, 1)
    file.write_text("2026-08-15 revised long content", encoding="utf-8")
    scan_source(db, sid, source)
    worker._parse(db)
    worker._date(db)
    assert db.document(1)["eff_date"] == "2024-07-02"
    assert db.document_tasks(1)[0]["id"] == 1


def test_restored_original_with_same_metadata_is_seen_again(project):
    db, source, file, sid, worker = project
    stat, body = file.stat(), file.read_bytes()
    file.unlink()
    scan_source(db, sid, source)
    assert db.document(1)["missing_since"]
    file.write_bytes(body)
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    scan_source(db, sid, source)
    assert db.document(1)["missing_since"] is None


def test_disconnected_source_is_not_treated_as_deleted(project):
    db, source, file, sid, worker = project
    offline = source.with_name("offline")
    source.rename(offline)
    stats = scan_source(db, sid, source)
    assert stats.errors
    assert db.document(1)["missing_since"] is None


def test_failed_invalidation_rolls_back_old_index(project, monkeypatch):
    db, source, file, sid, worker = project
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.invalidate_document_analysis(1)
            raise RuntimeError("simulated failure")
    assert db.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert db.document(1)["eff_year"] == 2023
