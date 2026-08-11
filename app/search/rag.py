"""RAG 검색과 답변 — 질문 화면의 엔진.

정체성의 마지막 조각이다. What·When·How가 정형 화면으로 답하지 못하는
질문을 여기서 받는다. 대화가 아니라 단발 질의로 설계한다 — 멀티턴 문맥을
유지하지 않는다. 그래야 챗봇으로 흐르지 않는다(doc/00 §6.4).

정책 (구현 강제)
  · 검색된 프로젝트 자료만 근거로 답한다. 일반 지식으로 빈칸을 채우지 않는다.
  · 사실 문장마다 출처를 남긴다. 출처 없는 답은 만들지 않는다.
  · 근거가 부족하면 답하지 않는다 — 대신 찾은 문서 목록을 보여준다.
  · 자료가 충돌하면 한쪽을 고르지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..ai.client import OllamaClient
from ..ai.prompts_loader import render
from ..ai.schemas import ASK_SCHEMA
from ..db import Database
from .vector import normalize, unpack

TOP_K = 8
# 실측 기준값이 아니라 임시값이다. 실제 자료로 재조정이 필요하다(doc/00 §8.2 방식).
MIN_SIMILARITY = 0.30
MAX_CONTEXT_CHARS = 400
RELATED_FALLBACK = 5


@dataclass(slots=True)
class Citation:
    index: int
    doc_id: int
    filename: str
    locator: str
    path: str


@dataclass(slots=True)
class RelatedDoc:
    doc_id: int
    filename: str
    path: str


@dataclass(slots=True)
class Answer:
    question: str
    text: str
    withheld: bool
    citations: list[Citation] = field(default_factory=list)
    related_docs: list[RelatedDoc] = field(default_factory=list)
    model: str = ""
    error: str | None = None


def ask(db: Database, question: str, client: OllamaClient | None = None) -> Answer:
    """질문 하나에 답한다. 이전 질문을 기억하지 않는다."""
    question = question.strip()
    if not question:
        return Answer(question=question, text="", withheld=True, error="빈 질문입니다")

    client = client or OllamaClient()
    health = client.health()
    if not health.embedding_ready:
        return Answer(
            question=question, text="", withheld=True,
            error=f"로컬 AI에 연결할 수 없습니다 — {health.message}",
        )

    vectors, error = client.embed([question])
    if error or not vectors:
        return Answer(question=question, text="", withheld=True, error=error or "질문 임베딩 실패")
    query_vector = normalize(np.asarray(vectors, dtype=np.float32))[0]

    chunk_ids, matrix = _load_chunk_matrix(db, client.embed_model)
    if chunk_ids.size == 0:
        return Answer(
            question=question, text="", withheld=True,
            error="아직 자료를 읽는 중입니다. 분석이 끝난 뒤 다시 물어보세요.",
        )

    scores = matrix @ query_vector
    ranked = np.argsort(-scores)[:TOP_K]

    strong = [(int(chunk_ids[i]), float(scores[i])) for i in ranked if scores[i] >= MIN_SIMILARITY]
    if not strong:
        related = _related_documents(db, [int(chunk_ids[i]) for i in ranked[:RELATED_FALLBACK]])
        return Answer(
            question=question,
            text="확인 가능한 자료가 부족합니다.",
            withheld=True,
            related_docs=related,
        )

    context_rows = _load_chunk_context(db, [cid for cid, _ in strong])
    if not context_rows:
        return Answer(question=question, text="확인 가능한 자료가 부족합니다.", withheld=True)

    prompt, _version = render("ask", context=_format_context(context_rows), question=question)
    data, gen_error = client.generate_json(prompt, ASK_SCHEMA, num_predict=500)
    if gen_error or data is None:
        return Answer(question=question, text="", withheld=True, error=gen_error or "응답 생성 실패")

    if not data.get("answered", False):
        related = [RelatedDoc(r["doc_id"], r["filename"], r["path"]) for r in context_rows[:3]]
        return Answer(
            question=question,
            text="확인 가능한 자료가 부족합니다.",
            withheld=True,
            related_docs=_dedupe(related),
            model=client.gen_model,
        )

    text = " ".join(str(data.get("answer") or "").split())
    if not text:
        return Answer(question=question, text="확인 가능한 자료가 부족합니다.", withheld=True)

    citations = [
        Citation(index=i + 1, doc_id=r["doc_id"], filename=r["filename"],
                 locator=r["locator"], path=r["path"])
        for i, r in enumerate(context_rows)
    ]
    return Answer(question=question, text=text, withheld=False, citations=citations, model=client.gen_model)


# ── 내부 ────────────────────────────────────────────────────────────

def _load_chunk_matrix(db: Database, model: str) -> tuple[np.ndarray, np.ndarray]:
    rows = db.con.execute(
        "SELECT e.chunk_id, e.dim, e.vector FROM embeddings e "
        "JOIN chunks c ON c.id = e.chunk_id "
        "JOIN documents d ON d.id = c.doc_id "
        "WHERE e.model = ? AND d.missing_since IS NULL",
        (model,),
    ).fetchall()
    if not rows:
        return np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32)

    dim = rows[0]["dim"]
    usable = [r for r in rows if r["dim"] == dim]
    ids = np.fromiter((r["chunk_id"] for r in usable), dtype=np.int64, count=len(usable))
    matrix = np.vstack([unpack(r["vector"]) for r in usable]).astype(np.float32, copy=False)
    return ids, normalize(matrix)


def _load_chunk_context(db: Database, chunk_ids: list[int]) -> list:
    if not chunk_ids:
        return []
    marks = ", ".join("?" * len(chunk_ids))
    rows = db.con.execute(
        f"SELECT c.id AS chunk_id, c.doc_id, c.locator, c.text, d.filename, d.path "
        f"FROM chunks c JOIN documents d ON d.id = c.doc_id "
        f"WHERE c.id IN ({marks}) AND d.missing_since IS NULL",
        chunk_ids,
    ).fetchall()
    order = {cid: i for i, cid in enumerate(chunk_ids)}
    return sorted(rows, key=lambda r: order.get(r["chunk_id"], len(chunk_ids)))


def _format_context(rows) -> str:
    parts = []
    for i, row in enumerate(rows, start=1):
        text = " ".join(row["text"].split())[:MAX_CONTEXT_CHARS]
        parts.append(f"[{i}] ({row['filename']} · {row['locator']})\n{text}")
    return "\n\n".join(parts)


def _related_documents(db: Database, chunk_ids: list[int]) -> list[RelatedDoc]:
    rows = _load_chunk_context(db, chunk_ids)
    seen: list[RelatedDoc] = []
    known: set[int] = set()
    for row in rows:
        if row["doc_id"] in known:
            continue
        known.add(row["doc_id"])
        seen.append(RelatedDoc(row["doc_id"], row["filename"], row["path"]))
    return seen


def _dedupe(items: list[RelatedDoc]) -> list[RelatedDoc]:
    out: list[RelatedDoc] = []
    seen: set[int] = set()
    for item in items:
        if item.doc_id in seen:
            continue
        seen.add(item.doc_id)
        out.append(item)
    return out
