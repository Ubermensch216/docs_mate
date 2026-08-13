"""RAG 답변 시험 — 질문 화면의 정책을 코드로 강제하는지 확인한다.

정책: 자료만 근거로 답한다 · 근거 부족하면 유보한다 · 사실마다 출처를 남긴다.

실제 Ollama를 부르지 않는다. FakeClient로 임베딩·생성 결과를 통제해
정책 분기를 결정적으로 시험한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.client import Health
from app.db import Database
from app.search.rag import ask
from app.search.vector import pack


class FakeClient:
    """OllamaClient과 같은 인터페이스를 흉내 낸다."""

    def __init__(
        self,
        embedding_ready: bool = True,
        query_vector: list[float] | None = None,
        gen_response: dict | None = None,
        embed_error: str | None = None,
        gen_error: str | None = None,
        gen_raw: str | None = None,
    ):
        self.embed_model = "bge-m3"
        self.gen_model = "gemma4:e2b"
        self._ready = embedding_ready
        self._query_vector = query_vector or [1.0, 0.0, 0.0]
        self._gen_response = gen_response
        self._embed_error = embed_error
        self._gen_error = gen_error
        self._gen_raw = gen_raw

    def health(self) -> Health:
        return Health(
            ok=self._ready, message="ok" if self._ready else "연결 안 됨",
            embedding_ready=self._ready, generation_ready=self._ready,
        )

    def embed(self, texts: list[str]):
        if self._embed_error:
            return None, self._embed_error
        return [list(self._query_vector) for _ in texts], None

    def generate_json(self, prompt: str, schema: dict, num_predict: int = 400, temperature: float = 0.0):
        if self._gen_error:
            return None, self._gen_error, self._gen_raw or ""
        return self._gen_response, None, self._gen_raw or ""


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    yield database
    database.close()


def _seed_chunk(db: Database, source_id: int, filename: str, locator: str,
                 text: str, vector: list[float], hash_: str) -> tuple[int, int]:
    doc_id = db.upsert_document(source_id, {
        "path": rf"D:\자료\{filename}", "filename": filename, "ext": ".hwp",
        "parse_status": "ok", "hash": hash_,
    })
    chunk_id, _ = db.replace_document_chunks(doc_id, [(1, locator, text)])[0]
    db.save_chunk_embedding(chunk_id, "bge-m3", len(vector), pack(vector))
    return doc_id, chunk_id


# ── 입력 검증 ───────────────────────────────────────────────────────

def test_blank_question_is_withheld_without_calling_ollama(db: Database):
    answer = ask(db, "   ", client=FakeClient())
    assert answer.withheld
    assert answer.error


# ── AI 연결 실패 ────────────────────────────────────────────────────

def test_withholds_when_ollama_unreachable(db: Database):
    answer = ask(db, "질문", client=FakeClient(embedding_ready=False))
    assert answer.withheld
    assert "연결" in answer.error


def test_withholds_when_question_embedding_fails(db: Database):
    answer = ask(db, "질문", client=FakeClient(embed_error="임베딩 실패"))
    assert answer.withheld
    assert answer.error == "임베딩 실패"


# ── 색인 없음 ───────────────────────────────────────────────────────

def test_withholds_when_no_chunks_are_indexed(db: Database):
    answer = ask(db, "질문", client=FakeClient())
    assert answer.withheld
    assert "분석" in answer.error or "자료" in answer.error


# ── 근거 부족 (유사도 낮음) ─────────────────────────────────────────

def test_withholds_when_similarity_is_below_threshold(db: Database):
    """질문과 전혀 다른 방향의 벡터만 있으면 근거로 쓰지 않는다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "무관한문서.hwp", "1문단", "전혀 관련 없는 내용",
                vector=[0.0, 1.0, 0.0], hash_="a")

    answer = ask(db, "질문", client=FakeClient(query_vector=[1.0, 0.0, 0.0]))

    assert answer.withheld
    assert answer.text == "확인 가능한 자료가 부족합니다."
    assert answer.related_docs, "대신 찾은 문서를 보여줘야 합니다"


def test_related_docs_are_deduplicated_by_document(db: Database):
    source_id = db.add_source(r"D:\자료")
    doc_id = db.upsert_document(source_id, {
        "path": r"D:\자료\문서.hwp", "filename": "문서.hwp", "ext": ".hwp",
        "parse_status": "ok", "hash": "a",
    })
    chunks = db.replace_document_chunks(doc_id, [(1, "1문단", "가"), (2, "2문단", "나")])
    for chunk_id, _ in chunks:
        db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([0.0, 1.0, 0.0]))

    answer = ask(db, "질문", client=FakeClient(query_vector=[1.0, 0.0, 0.0]))
    assert len({d.doc_id for d in answer.related_docs}) == len(answer.related_docs)


# ── 모델이 스스로 유보 ──────────────────────────────────────────────

def test_withholds_when_model_says_not_answered(db: Database):
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "문서.hwp", "1문단", "본문 내용", [1.0, 0.0, 0.0], "a")

    client = FakeClient(gen_response={"answered": False, "sentences": []})
    answer = ask(db, "질문", client=client)

    assert answer.withheld
    assert answer.text == "확인 가능한 자료가 부족합니다."


def test_withholds_when_generation_fails(db: Database):
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "문서.hwp", "1문단", "본문", [1.0, 0.0, 0.0], "a")

    answer = ask(db, "질문", client=FakeClient(gen_error="시간 초과"))
    assert answer.withheld
    assert answer.error == "시간 초과"


def test_empty_answer_text_is_treated_as_withheld(db: Database):
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "문서.hwp", "1문단", "본문", [1.0, 0.0, 0.0], "a")

    client = FakeClient(gen_response={"answered": True, "sentences": []})
    answer = ask(db, "질문", client=client)
    assert answer.withheld


# ── 정상 응답과 출처 ────────────────────────────────────────────────

def test_successful_answer_carries_citations_for_every_context_chunk(db: Database):
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "행감자료.hwp", "3쪽 2문단", "수질 관련 제출자료 내용",
                [1.0, 0.0, 0.0], "a")

    client = FakeClient(
        query_vector=[1.0, 0.0, 0.0],
        gen_response={"answered": True, "sentences": [
            {"text": "수질 관련 제출자료 내용을 확인했습니다", "sources": [1]},
        ]},
    )
    answer = ask(db, "작년 수질 관련 뭐 냈어?", client=client)

    assert not answer.withheld
    assert answer.text == "수질 관련 제출자료 내용을 확인했습니다[1]"
    assert len(answer.citations) == 1
    assert answer.citations[0].filename == "행감자료.hwp"
    assert answer.citations[0].locator == "3쪽 2문단"
    assert answer.citations[0].index == 1


def test_answer_never_returned_without_at_least_one_citation(db: Database):
    """근거 문서가 있어야만 답이 나온다 — 근거 없는 답은 만들지 않는다."""
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "문서.hwp", "1문단", "내용", [1.0, 0.0, 0.0], "a")

    client = FakeClient(gen_response={"answered": True, "sentences": [
        {"text": "내용 확인", "sources": [1]},
    ]})
    answer = ask(db, "질문", client=client)
    assert not answer.withheld
    assert len(answer.citations) >= 1


def test_missing_documents_are_excluded_from_context(db: Database):
    """원본이 사라진 문서는 근거로 쓰지 않는다."""
    source_id = db.add_source(r"D:\자료")
    doc_id, chunk_id = _seed_chunk(
        db, source_id, "사라진문서.hwp", "1문단", "내용", [1.0, 0.0, 0.0], "a"
    )
    db.mark_missing([doc_id])

    answer = ask(db, "질문", client=FakeClient(query_vector=[1.0, 0.0, 0.0]))
    assert answer.withheld   # 유일한 근거가 사라졌으니 답할 것이 없다


def test_ranking_prefers_closer_vectors(db: Database):
    source_id = db.add_source(r"D:\자료")
    _seed_chunk(db, source_id, "가까운문서.hwp", "1문단", "가까움", [1.0, 0.0, 0.0], "a")
    _seed_chunk(db, source_id, "먼문서.hwp", "1문단", "그나마덜가까움", [0.6, 0.4, 0.0], "b")

    client = FakeClient(
        query_vector=[1.0, 0.0, 0.0],
        gen_response={"answered": True, "sentences": [
            {"text": "가까움 문서", "sources": [1]},
        ]},
    )
    answer = ask(db, "질문", client=client)
    assert answer.citations[0].filename == "가까운문서.hwp"
