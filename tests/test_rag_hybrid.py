"""하이브리드 검색(RRF 결합) 시험 (RAG 개선 R3).

의미 검색(벡터)과 정확 일치 검색(FTS)은 점수 스케일이 다르다 — 코사인
유사도와 bm25 값을 직접 비교할 수 없다. RRF가 순위만 보고 합치는지,
그리고 '강한 근거' 판정이 두 검색 중 하나만 확신해도 통과하는지를 여기서
못 박는다. 실제 답변 생성 흐름은 test_rag.py가 다룬다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.search.rag import MIN_SIMILARITY, RRF_K, _reciprocal_rank_fusion, ask
from app.search.vector import pack
from tests.test_rag import FakeClient


# ── RRF 결합 자체 ───────────────────────────────────────────────────

def test_top_of_both_lists_ranks_highest():
    combined = _reciprocal_rank_fusion([(1, 0.9), (2, 0.5)], [1, 3])
    assert combined[0][0] == 1   # 두 순위 모두에서 1위


def test_appearing_in_only_one_list_still_counts():
    combined = _reciprocal_rank_fusion([(1, 0.9)], [2])
    ids = [cid for cid, _score in combined]
    assert set(ids) == {1, 2}


def test_score_scale_of_vector_ranked_does_not_leak_into_the_result():
    """벡터 점수가 뭐든(0.99든 -50이든) RRF는 순위만 본다."""
    a = _reciprocal_rank_fusion([(1, 0.99), (2, 0.98)], [])
    b = _reciprocal_rank_fusion([(1, -50.0), (2, -51.0)], [])
    assert [cid for cid, _s in a] == [cid for cid, _s in b]


def test_lower_rrf_k_makes_top_rank_dominate_more():
    """K가 작을수록 1위와 2위의 점수 차이가 커진다 — RRF 공식 자체의 성질."""
    combined_small_k = dict(_reciprocal_rank_fusion([(1, 1.0), (2, 0.9)], [], k=1))
    combined_large_k = dict(_reciprocal_rank_fusion([(1, 1.0), (2, 0.9)], [], k=1000))
    gap_small = combined_small_k[1] - combined_small_k[2]
    gap_large = combined_large_k[1] - combined_large_k[2]
    assert gap_small > gap_large


def test_empty_inputs_yield_empty_result():
    assert _reciprocal_rank_fusion([], []) == []


def test_default_k_matches_the_module_constant():
    """실측 근거 없는 관행값이라고 문서에 적었다 — 상수가 실제로 쓰이는지 확인한다."""
    with_default = dict(_reciprocal_rank_fusion([(1, 1.0)], []))
    with_explicit = dict(_reciprocal_rank_fusion([(1, 1.0)], [], k=RRF_K))
    assert with_default == with_explicit


# ── ask()에 결합된 하이브리드 검색 ──────────────────────────────────

@pytest.fixture()
def db(tmp_path: Path) -> Database:
    project = Database(tmp_path / "project.db")
    project.init()
    yield project
    project.close()


def test_exact_term_match_counts_as_strong_even_below_similarity_threshold(db: Database):
    """벡터만으로는 놓치는 정확 일치를 FTS가 구제한다 — 하이브리드의 핵심 목적.

    질문 벡터와 직교하는(유사도 0에 가까운) 벡터를 가진 조각이라도, 질문의
    고유명사가 본문에 정확히 있으면 근거로 채택되어야 한다.
    """
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/부서별자료요청.hwp", "filename": "부서별자료요청.hwp",
        "ext": ".hwp", "parse_status": "ok",
    })
    chunk_id, _ = db.replace_document_chunks(
        doc_id, [(1, "1문단", "부서별자료요청 문서는 9월에 작성되었다")]
    )[0]
    # 질문 벡터와 직교 — 순수 벡터 검색이었다면 MIN_SIMILARITY를 넘지 못한다.
    db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([0.0, 1.0, 0.0]))

    client = FakeClient(
        query_vector=[1.0, 0.0, 0.0],
        gen_response={"answered": True, "sentences": [
            {"text": "부서별자료요청 문서는 9월에 작성되었다", "sources": [1]},
        ]},
    )
    answer = ask(db, "부서별자료요청 문서는 언제 작성됐어?", client=client)

    assert not answer.withheld, "FTS 정확 일치가 있는데도 유보했다"
    assert answer.citations[0].filename == "부서별자료요청.hwp"


def test_weak_on_both_sides_still_withholds(db: Database):
    """FTS가 있다고 무조건 통과시키면 안 된다 — 둘 다 약하면 여전히 유보한다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/무관.hwp", "filename": "무관.hwp", "ext": ".hwp", "parse_status": "ok",
    })
    chunk_id, _ = db.replace_document_chunks(
        doc_id, [(1, "1문단", "전혀 다른 주제의 내용")]
    )[0]
    db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([0.0, 1.0, 0.0]))

    answer = ask(db, "질문", client=FakeClient(query_vector=[1.0, 0.0, 0.0]))
    assert answer.withheld


def test_generic_token_buried_deep_in_fts_does_not_become_strong_evidence(db: Database):
    """실측으로 잡은 회귀: 흔한 낱말 하나가 무관한 문서를 근거로 승격시켰다.

    "2025년"처럼 그 해의 모든 문서에 박힌 상투 문구는 OR 결합에서 걸리지만,
    bm25는 두 낱말(연도+고유명사)이 겹치는 문서를 한 낱말만 겹치는 문서보다
    항상 위에 둔다. fts_ranked 전체(RANK_POOL=40)를 강한 근거로 쓰면 그
    순위 정보를 버려 노이즈가 근거로 올라온다 — 실제로 "2025년 행정사무
    감사…" 질문에 무관한 2025년 문서 다수가 섞였다(§1-D). 진짜 관련 문서를
    TOP_K개 채울 만큼 마련해야, 두 낱말 일치 문서가 TOP_K 안을 정확히
    채우고 한 낱말짜리 노이즈가 전부 그 아래로 밀리는 상황을 재현한다.
    """
    from app.search.rag import TOP_K

    source_id = db.add_source("/자료")

    def seed(filename: str, text: str, hash_: str) -> int:
        doc_id = db.upsert_document(source_id, {
            "path": f"/자료/{filename}", "filename": filename, "ext": ".hwp",
            "parse_status": "ok", "hash": hash_,
        })
        chunk_id, _ = db.replace_document_chunks(doc_id, [(1, "1문단", text)])[0]
        db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([0.0, 1.0, 0.0]))
        return doc_id

    target_files = set()
    for i in range(TOP_K):
        seed(f"행감{i}.hwp", f"2025년 행정사무감사 제출자료 접수 {i}번째", f"target{i}")
        target_files.add(f"행감{i}.hwp")
    noise_files = set()
    for i in range(12):
        seed(f"무관{i}.hwp", f"2025년 다른 업무 문서 내용 {i}번째", f"noise{i}")
        noise_files.add(f"무관{i}.hwp")

    client = FakeClient(
        query_vector=[1.0, 0.0, 0.0],   # 모든 조각과 직교 — 벡터로는 아무것도 못 건진다
        gen_response={"answered": True, "sentences": [
            {"text": "2025년 행정사무감사 제출자료 접수", "sources": [1]},
        ]},
    )
    answer = ask(db, "2025년 행정사무감사 때 뭘 제출했어?", client=client)

    assert not answer.withheld
    cited_files = {c.filename for c in answer.citations}
    assert cited_files.isdisjoint(noise_files), f"무관한 문서가 섞였다: {cited_files & noise_files}"
    assert cited_files <= target_files


def test_min_similarity_alone_still_qualifies_without_any_fts_hit(db: Database):
    """FTS가 아무것도 못 찾아도, 벡터 유사도가 충분하면 그대로 답한다 — 기존 경로 보존."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/문서.hwp", "filename": "문서.hwp", "ext": ".hwp", "parse_status": "ok",
    })
    chunk_id, _ = db.replace_document_chunks(doc_id, [(1, "1문단", "내용")])[0]
    db.save_chunk_embedding(chunk_id, "bge-m3", 3, pack([1.0, 0.0, 0.0]))

    client = FakeClient(
        query_vector=[1.0, 0.0, 0.0],
        gen_response={"answered": True, "sentences": [
            {"text": "내용 확인", "sources": [1]},
        ]},
    )
    answer = ask(db, "질문", client=client)
    assert not answer.withheld


def test_similarity_threshold_constant_is_still_honored(db: Database):
    """하이브리드를 넣었다고 MIN_SIMILARITY 기준 자체가 느슨해지면 안 된다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/문서.hwp", "filename": "문서.hwp", "ext": ".hwp", "parse_status": "ok",
    })
    chunk_id, _ = db.replace_document_chunks(doc_id, [(1, "1문단", "내용")])[0]
    # MIN_SIMILARITY보다 살짝 낮은 코사인 유사도를 갖도록 벡터를 구성한다.
    below = max(MIN_SIMILARITY - 0.05, 0.0)
    import math
    db.save_chunk_embedding(
        chunk_id, "bge-m3", 2, pack([below, math.sqrt(max(1 - below**2, 0))])
    )

    answer = ask(db, "질문", client=FakeClient(query_vector=[1.0, 0.0]))
    assert answer.withheld
