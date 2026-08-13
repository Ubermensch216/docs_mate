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

import json
import re
from dataclasses import dataclass, field

import numpy as np

from ..ai.client import OllamaClient
from ..ai.prompts_loader import render
from ..ai.schemas import ASK_SCHEMA
from ..db import Database, representative_predicate
from . import fts
from .query import QueryScope, infer_scope
from .vector import normalize, unpack
from .verify import VerifiedSentence, verify_sentences

TOP_K = 8
# 실측 기준값이 아니라 임시값이다. 실제 자료로 재조정이 필요하다(doc/00 §8.2 방식).
MIN_SIMILARITY = 0.30
MAX_CONTEXT_CHARS = 400
RELATED_FALLBACK = 5

# 두 순위(의미·정확 일치)를 이 깊이까지 각각 훑은 뒤 RRF로 합친다. 최종
# 컨텍스트(TOP_K)보다 넉넉히 깊게 봐야 한쪽에서만 강한 후보를 놓치지 않는다.
RANK_POOL = 40
# RRF(Reciprocal Rank Fusion) 상수. 특정 자료에 맞춰 조정한 값이 아니라
# 원 논문(Cormack et al. 2009)의 관행값이다 — 순위 1위와 20위의 점수 차이를
# 완만하게 만들어, 한쪽 순위표의 잡음 하나가 결과를 뒤집지 않게 한다.
RRF_K = 60
# 한 문서가 근거 예산(TOP_K)을 독점하지 않게 한다. 실측에서 근거 8건이
# 사실상 같은 문서의 사본(최종·수정·복사본 등)에 몰린 사례가 있었다
# (§1 측정 2). 완전 동일본은 대표본 판정으로 걸러지지만, 내용이 조금
# 다른 버전은 hash가 달라 그 판정을 피한다 — 그래서 별도로 상한을 둔다.
MAX_CHUNKS_PER_DOC = 2
# 문장 배열 스키마는 플랫 문자열보다 JSON 구조 오버헤드(중괄호·키·배열)가
# 크다. 500이던 옛 한도는 실측(R1)에서 답을 문장 중간에 잘랐다 — 짧은
# 질문 하나가 완전한 답변 없이 "JSON 형식 오류"로 통째로 사라졌다.
GEN_TOKEN_BUDGET = 900
# 대표본·연도 필터·다양성 상한이 후보를 걸러낼 때, 이 깊이보다 아래까지
# 내려가 빈 자리를 채우지 않는다. 실측으로 잡은 회귀: "작년 예산…" 질문에
# 연도 필터가 2022~2024년의 더 좋은 예산 문서를 제외시키자, 우연히
# 2025년인 무관한 문서(행정사무감사)가 그 빈자리를 채웠다 — 걸러서 생긴
# 빈 자리를 무한정 더 깊은 후보로 메우면, 원래라면 답에 안 나왔을 약한
# 근거가 다양성이라는 명목으로 승격된다. 이 한계를 넘으면 그냥 근거
# 개수가 TOP_K보다 적게 나가는 편이 낫다.
EXPLORATION_WINDOW = TOP_K * 2


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
    # 질문에서 규칙으로 뽑아낸 연도. UI에 직접 보여주진 않지만
    # questions.filters에 남겨 나중에 "이 질문을 왜 이렇게 좁혔는지"
    # 확인할 수 있게 한다.
    inferred_years: list[int] = field(default_factory=list)


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

    # 하이브리드 검색 — 의미(벡터)와 정확 일치(FTS5)를 각각 넉넉히 훑어
    # RRF로 합친다. 문서번호·고유명사처럼 정확히 일치해야 하는 질의는
    # 벡터만으로는 놓치기 쉽다(doc/질문화면_RAG_개선계획.md §1 측정 7).
    scores = matrix @ query_vector
    pool = np.argsort(-scores)[:RANK_POOL]
    vector_ranked = [(int(chunk_ids[i]), float(scores[i])) for i in pool]
    fts_ranked = fts.search_chunk_ids(db, question, limit=RANK_POOL)
    combined = _reciprocal_rank_fusion(vector_ranked, fts_ranked)

    # '근거로 쓸 만큼 강한가'는 두 검색 중 하나라도 확신을 준 경우다.
    # 의미가 가깝거나(MIN_SIMILARITY 이상), 정확히 그 글자가 상위권에 있거나.
    #
    # FTS 쪽은 fts_ranked 전체(RANK_POOL=40)가 아니라 상위 TOP_K만 본다.
    # OR 결합(escape_match_any)은 토큰 하나만 맞아도 걸리므로, "2025년"처럼
    # 흔한 해와 표기가 겹치는 문서는 40위 안 어딘가에 전부 들어온다 —
    # bm25 자체는 이런 문서를 이미 순위 뒤쪽으로 정확히 낮게 매기는데,
    # 위치를 무시하고 "포함되면 무조건 강함"으로 처리하면 그 순위 정보를
    # 버리는 셈이 된다. 실측: "2025년 행정사무감사…" 질문에 수질통계·
    # 월간실적보고 문서까지 강한 근거로 승격돼 다양성 상한이 그 노이즈를
    # 답변에 끌어들였다(doc/질문화면_RAG_개선계획.md §1-D).
    strong_ids = {cid for cid, score in vector_ranked if score >= MIN_SIMILARITY}
    strong_ids |= set(fts_ranked[:TOP_K])

    scope = infer_scope(question)
    strong = _select_context(db, combined, strong_ids, scope)

    if not strong:
        related = _related_documents(db, [cid for cid, _score in combined[:RELATED_FALLBACK]])
        return Answer(
            question=question,
            text="확인 가능한 자료가 부족합니다.",
            withheld=True,
            related_docs=related,
            inferred_years=scope.years,
        )

    context_rows = _load_chunk_context(db, [cid for cid, _ in strong])
    if not context_rows:
        return Answer(question=question, text="확인 가능한 자료가 부족합니다.", withheld=True,
                       inferred_years=scope.years)

    prompt, _version = render("ask", context=_format_context(context_rows), question=question)
    data, gen_error, raw = client.generate_json(prompt, ASK_SCHEMA, num_predict=GEN_TOKEN_BUDGET)

    if data is None and raw:
        # 토큰 한도에 걸려 JSON이 중간에 잘렸을 수 있다 — 완성된 문장까지는
        # 건진다. 통째로 버리면 "짧은 질문 하나가 이유 없이 실패"로 보인다.
        salvaged = _salvage_ask_response(raw)
        if salvaged is not None:
            data, gen_error = salvaged, None

    if gen_error or data is None:
        return Answer(question=question, text="", withheld=True, error=gen_error or "응답 생성 실패",
                       inferred_years=scope.years)

    if not data.get("answered", False):
        related = [RelatedDoc(r["doc_id"], r["filename"], r["path"]) for r in context_rows[:3]]
        return Answer(
            question=question,
            text="확인 가능한 자료가 부족합니다.",
            withheld=True,
            related_docs=_dedupe(related),
            model=client.gen_model,
            inferred_years=scope.years,
        )

    # 모델이 문장·근거를 스스로 짝지어 냈어도, 그 근거가 실제로 그 문장을
    # 뒷받침하는지는 모델이 보장하지 않는다. 여기서 규칙으로 한 번 더
    # 확인한다 — 근거 없는 문장은 화면에 올리지 않는다(§1 측정 1).
    source_text = {
        i + 1: " ".join(row["text"].split())[:MAX_CONTEXT_CHARS]
        for i, row in enumerate(context_rows)
    }
    verified = verify_sentences(data.get("sentences") or [], source_text)

    if not verified.survived:
        related = [RelatedDoc(r["doc_id"], r["filename"], r["path"]) for r in context_rows[:3]]
        return Answer(
            question=question,
            text="확인 가능한 자료가 부족합니다.",
            withheld=True,
            related_docs=_dedupe(related),
            model=client.gen_model,
            inferred_years=scope.years,
        )

    text, citations = _compose_answer(verified.kept, context_rows)
    return Answer(question=question, text=text, withheld=False, citations=citations,
                  model=client.gen_model, inferred_years=scope.years)


# ── 내부 ────────────────────────────────────────────────────────────

_ANSWERED = re.compile(r'"answered"\s*:\s*(true|false)')
_SENTENCE = re.compile(
    r'\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"sources"\s*:\s*\[([^\]]*)\]\s*\}'
)


def _salvage_ask_response(raw: str) -> dict | None:
    """토큰 한도에 걸려 잘린 JSON에서 완성된 문장만 건진다.

    Ollama의 format 강제로 파싱 실패는 드물지만, 문장 배열은 길어질수록
    한도를 넘길 위험이 커진다. 정규식으로 완성된 {"text":…,"sources":[…]}
    객체만 추려낸다 — 중간에 잘린 객체는 패턴에 안 맞아 자연히 빠진다.
    """
    answered_match = _ANSWERED.search(raw)
    if not answered_match:
        return None

    sentences: list[dict] = []
    for match in _SENTENCE.finditer(raw):
        try:
            text = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            continue
        sources = [int(n) for n in re.findall(r"-?\d+", match.group(2))]
        sentences.append({"text": text, "sources": sources})

    if not sentences:
        return None
    return {"answered": answered_match.group(1) == "true", "sentences": sentences}


def _compose_answer(
    sentences: list[VerifiedSentence], context_rows: list,
) -> tuple[str, list[Citation]]:
    """검증에서 살아남은 문장으로 답변 본문과 근거 목록을 만든다.

    원래 컨텍스트 번호([1]~[TOP_K])를 그대로 쓰지 않고, 실제로 쓰인 것만
    등장 순서대로 [1][2][3]으로 다시 매긴다 — 검증에서 몇 문장이 버려지면
    번호가 듬성듬성해지는데(예: [1][5][7]), 그대로 보여주면 "인용 전량
    나열" 문제(§1 측정 1)의 변형이 된다. 실제로 근거가 된 것만, 깔끔한
    번호로 보여준다.
    """
    by_index = {i + 1: row for i, row in enumerate(context_rows)}
    citations: list[Citation] = []
    renumbered: dict[int, int] = {}
    parts: list[str] = []

    for sentence in sentences:
        marks: list[int] = []
        for source in sentence.sources:
            row = by_index.get(source)
            if row is None:
                continue
            if source not in renumbered:
                renumbered[source] = len(citations) + 1
                citations.append(Citation(
                    index=renumbered[source], doc_id=row["doc_id"],
                    filename=row["filename"], locator=row["locator"], path=row["path"],
                ))
            marks.append(renumbered[source])
        suffix = "".join(f"[{m}]" for m in sorted(set(marks)))
        parts.append(f"{sentence.text}{suffix}" if suffix else sentence.text)

    return " ".join(parts), citations


def _reciprocal_rank_fusion(
    vector_ranked: list[tuple[int, float]], fts_ranked: list[int], k: int = RRF_K,
) -> list[tuple[int, float]]:
    """의미 순위와 정확 일치 순위를 하나로 합친다.

    코사인 유사도와 FTS5 bm25는 값의 스케일이 전혀 다르다 — 0.4와 -12.3을
    직접 비교할 수 없다. RRF는 값이 아니라 순위(몇 번째로 좋은가)만 보고
    합치므로 스케일 문제가 애초에 생기지 않는다. 규칙만으로 되는 표준적인
    방법이라 생성 호출을 늘리지 않고도 두 검색을 결합할 수 있다.
    """
    scores: dict[int, float] = {}
    for rank, (chunk_id, _score) in enumerate(vector_ranked):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, chunk_id in enumerate(fts_ranked):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: -item[1])


def _select_context(
    db: Database, combined: list[tuple[int, float]], strong_ids: set[int], scope: QueryScope,
) -> list[tuple[int, float]]:
    """RRF 순위에서 실제로 컨텍스트에 넣을 TOP_K를 고른다.

    순서가 중요하다. 먼저 탐색 범위를 EXPLORATION_WINDOW로 못 박는다 —
    그 아래는 걸러서 생긴 빈 자리를 메우는 데도 쓰지 않는다. 그다음 연도로
    좁힌다. 대표본 판정과 연도 필터는 '완전히 사라지면 되돌린다' — 근거가
    아예 없어지는 것보다는 약한 근거라도 보류 판단에 넘기는 편이 낫다
    (모를 때 모른다고 답하는 건 이 함수가 아니라 그다음 단계, LLM과 후속
    검증의 몫이다).
    """
    eligible = [(cid, score) for cid, score in combined if cid in strong_ids][:EXPLORATION_WINDOW]
    if not eligible:
        return []

    meta = _load_chunk_meta(db, [cid for cid, _score in eligible])

    representative = _representative_chunk_ids(db, [cid for cid, _score in eligible])
    narrowed = [(cid, s) for cid, s in eligible if cid in representative]
    if narrowed:
        eligible = narrowed

    if scope.has_year:
        matching = [
            (cid, s) for cid, s in eligible
            if meta.get(cid, {}).get("eff_year") in scope.years
        ]
        if matching:
            eligible = matching

    doc_of = {cid: meta[cid]["doc_id"] for cid, _s in eligible if cid in meta}
    return _cap_per_document(eligible, doc_of)[:TOP_K]


def _cap_per_document(
    ranked: list[tuple[int, float]], doc_of: dict[int, int],
) -> list[tuple[int, float]]:
    """한 문서가 MAX_CHUNKS_PER_DOC를 넘겨 근거 자리를 차지하지 못하게 한다."""
    kept: list[tuple[int, float]] = []
    counts: dict[int, int] = {}
    for chunk_id, score in ranked:
        doc_id = doc_of.get(chunk_id)
        if doc_id is not None and counts.get(doc_id, 0) >= MAX_CHUNKS_PER_DOC:
            continue
        kept.append((chunk_id, score))
        if doc_id is not None:
            counts[doc_id] = counts.get(doc_id, 0) + 1
    return kept


def _representative_chunk_ids(db: Database, chunk_ids: list[int]) -> set[int]:
    """완전 중복 문서 중 대표본에 속한 조각만 남긴다.

    When·How·문서 화면과 같은 판정 기준(db.representative_predicate)을
    쓴다 — 화면마다 다른 문서를 대표로 고르면 사용자가 혼란스럽다.
    """
    if not chunk_ids:
        return set()
    marks = ", ".join("?" * len(chunk_ids))
    rows = db.con.execute(
        f"SELECT c.id AS chunk_id FROM chunks c JOIN documents d ON d.id = c.doc_id "
        f"WHERE c.id IN ({marks}) AND {representative_predicate('d')}",
        chunk_ids,
    ).fetchall()
    return {row["chunk_id"] for row in rows}


def _load_chunk_meta(db: Database, chunk_ids: list[int]) -> dict[int, dict]:
    if not chunk_ids:
        return {}
    marks = ", ".join("?" * len(chunk_ids))
    rows = db.con.execute(
        f"SELECT c.id AS chunk_id, c.doc_id, d.eff_year FROM chunks c "
        f"JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({marks})",
        chunk_ids,
    ).fetchall()
    return {
        row["chunk_id"]: {"doc_id": row["doc_id"], "eff_year": row["eff_year"]}
        for row in rows
    }


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
