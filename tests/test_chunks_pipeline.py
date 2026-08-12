"""청크 생성 파이프라인 단계 통합 시험.

Pipeline._chunks를 QThread 없이 직접 불러 검증한다. Ollama가 없으면
건너뛴다는 계약(AI는 단일 장애점이 아니다)을 확인하는 것이 이 파일의
핵심이다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai.client import OllamaClient  # noqa: E402
from app.db import Database  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402

DEAD = "http://127.0.0.1:1"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    yield database
    database.close()


def _doc_with_sections(db: Database, source_id: int, filename: str,
                       sections: list[tuple[str, str]], hash_: str = "h1") -> int:
    doc_id = db.upsert_document(source_id, {
        "path": rf"D:\자료\{filename}", "filename": filename, "ext": ".hwp",
        "parse_status": "ok", "hash": hash_,
    })
    db.replace_sections(doc_id, [
        (kind_from(i), i, locator, text) for i, (locator, text) in enumerate(sections, start=1)
    ])
    return doc_id


def kind_from(_i: int) -> str:
    return "paragraph"


def test_chunks_stage_is_skipped_without_ollama(db: Database, monkeypatch):
    """AI는 단일 장애점이 아니다 — 연결이 없으면 조용히 건너뛴다."""
    source_id = db.add_source(r"D:\자료")
    _doc_with_sections(db, source_id, "문서.hwp", [("1문단", "본문 내용입니다")])

    import app.jobs.pipeline as pipeline_module
    monkeypatch.setattr(
        pipeline_module, "OllamaClient", lambda *a, **k: OllamaClient(base_url=DEAD)
    )

    reports = []
    pipeline = Pipeline(db.path)
    pipeline.stage_done.connect(lambda r: reports.append(r))
    pipeline._chunks(db)

    assert db.con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
    assert "건너뜀" in reports[0].note
    # chunks_checked는 표시하지 않는다 — 연결되면 다시 시도해야 하므로.
    assert db.get_meta("chunks_checked") is None


# ── 실제 Ollama가 있을 때만 (선택) ──────────────────────────────────

def _ollama_ready() -> bool:
    if os.environ.get("NUNCHICOACH_LIVE_AI") != "1":
        return False
    return OllamaClient().health().embedding_ready


live = pytest.mark.skipif(
    not _ollama_ready(), reason="NUNCHICOACH_LIVE_AI=1 과 Ollama·bge-m3가 필요합니다"
)


@live
def test_live_chunks_are_embedded_and_queryable(db: Database):
    """청크 생성 → 임베딩 → RAG 검색까지 실제로 이어지는지 끝까지 확인한다."""
    from app.search.rag import ask

    source_id = db.add_source(r"D:\자료")
    _doc_with_sections(db, source_id, "행정사무감사_제출자료.hwp", [
        ("3쪽 2문단", "2024년 행정사무감사 수질 관련 제출자료입니다. "
                    "원수 수질, 정수장별 검사결과, 수질민원 현황을 포함합니다."),
    ])

    Pipeline(db.path)._chunks(db)

    n = db.con.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"]
    assert n >= 1

    answer = ask(db, "행정사무감사 때 수질 관련해서 뭘 제출했어?")
    assert not answer.withheld, answer.error
    assert answer.citations
    assert answer.citations[0].filename == "행정사무감사_제출자료.hwp"
