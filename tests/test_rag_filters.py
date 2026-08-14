"""근거 선별 시험 (RAG 개선 R4) — 연도 필터·대표본 판정·문서 다양성 상한.

실측(§1 측정 2·3)에서 확인된 두 가지 실패를 겨냥한다.
  · "작년 예산 요구자료는 누가 만들었어?"에 모든 연도 문서가 섞여 나왔다.
  · "수질통계는 언제 작성해?"의 근거 8건이 사실상 같은 문서의 사본이었다.

`ask()`를 통째로 돌리는 것보다 `_select_context`를 직접 시험하는 편이
결정적이다 — 생성 단계(FakeClient)의 우연에 기대지 않고 선별 로직 자체를
검증할 수 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.search.query import QueryScope
from app.search.rag import MAX_CHUNKS_PER_DOC, TOP_K, _select_context
from app.search.vector import pack

NO_SCOPE = QueryScope(years=[])


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    project = Database(tmp_path / "project.db")
    project.init()
    yield project
    project.close()


def add_doc(db: Database, source_id: int, filename: str, year: int | None = None,
            hash_: str | None = None) -> int:
    return db.upsert_document(source_id, {
        "path": f"/자료/{filename}", "filename": filename, "ext": ".hwp",
        "parse_status": "ok", "eff_year": year, "hash": hash_,
    })


def add_chunk(db: Database, doc_id: int, text: str, locator: str = "1문단") -> int:
    chunk_id, _ = db.replace_document_chunks(doc_id, [(1, locator, text)])[0]
    return chunk_id


# ── 연도 필터 ───────────────────────────────────────────────────────

def test_year_scope_keeps_only_matching_documents(db: Database):
    source_id = db.add_source("/자료")
    old_doc = add_doc(db, source_id, "2022_예산.hwp", year=2022)
    new_doc = add_doc(db, source_id, "2025_예산.hwp", year=2025)
    old_chunk = add_chunk(db, old_doc, "2022년 예산 요구자료")
    new_chunk = add_chunk(db, new_doc, "2025년 예산 요구자료")

    combined = [(old_chunk, 0.9), (new_chunk, 0.8)]
    strong_ids = {old_chunk, new_chunk}

    result = _select_context(db, combined, strong_ids, QueryScope(years=[2025]))
    assert [cid for cid, _s in result] == [new_chunk]


def test_year_scope_is_soft_when_nothing_matches(db: Database):
    """연도 판정이 틀리거나 없을 수 있다 — 전부 사라지면 원래 후보로 되돌린다."""
    source_id = db.add_source("/자료")
    doc_id = add_doc(db, source_id, "연도미상.hwp", year=None)
    chunk_id = add_chunk(db, doc_id, "연도를 알 수 없는 문서")

    combined = [(chunk_id, 0.9)]
    result = _select_context(db, combined, {chunk_id}, QueryScope(years=[2025]))
    assert [cid for cid, _s in result] == [chunk_id]


def test_no_year_scope_keeps_every_year(db: Database):
    source_id = db.add_source("/자료")
    a = add_chunk(db, add_doc(db, source_id, "a.hwp", year=2022), "가")
    b = add_chunk(db, add_doc(db, source_id, "b.hwp", year=2025), "나")

    result = _select_context(db, [(a, 0.9), (b, 0.8)], {a, b}, NO_SCOPE)
    assert {cid for cid, _s in result} == {a, b}


# ── 대표본 판정(완전 중복 제거) ─────────────────────────────────────

def test_exact_duplicate_documents_collapse_to_the_representative(db: Database):
    source_id = db.add_source("/자료")
    original = add_doc(db, source_id, "원본.hwp", hash_="같은해시")
    copy = add_doc(db, source_id, "사본.hwp", hash_="같은해시")
    original_chunk = add_chunk(db, original, "내용")
    copy_chunk = add_chunk(db, copy, "내용")

    result = _select_context(
        db, [(copy_chunk, 0.9), (original_chunk, 0.8)],
        {copy_chunk, original_chunk}, NO_SCOPE,
    )
    kept = [cid for cid, _s in result]
    assert original_chunk in kept
    assert copy_chunk not in kept


def test_representative_filter_uses_the_same_rule_as_other_screens(db: Database):
    """When·How·문서 화면과 다른 문서를 대표로 고르면 사용자가 혼란스럽다."""
    from app.db import representative_predicate

    source_id = db.add_source("/자료")
    a = add_doc(db, source_id, "a.hwp", hash_="h")
    b = add_doc(db, source_id, "b.hwp", hash_="h")
    expected_representative = db.con.execute(
        f"SELECT id FROM documents d WHERE d.id IN (?, ?) AND {representative_predicate('d')}",
        (a, b),
    ).fetchone()["id"]

    chunk_a = add_chunk(db, a, "내용")
    chunk_b = add_chunk(db, b, "내용")
    chunk_of = {chunk_a: a, chunk_b: b}

    result = _select_context(db, [(chunk_a, 0.9), (chunk_b, 0.8)], {chunk_a, chunk_b}, NO_SCOPE)
    kept_docs = {chunk_of[cid] for cid, _s in result}
    assert kept_docs == {expected_representative}


# ── 문서 다양성 상한 ────────────────────────────────────────────────

def test_a_single_document_cannot_exceed_the_per_document_cap(db: Database):
    """완전 동일본이 아니라도(hash가 다른 버전) 한 문서가 자리를 독점하면 안 된다."""
    source_id = db.add_source("/자료")
    doc_id = add_doc(db, source_id, "수질통계.hwp", hash_="v1")
    chunks = [
        add_chunk(db, doc_id, f"내용{i}", locator=f"{i}문단") for i in range(5)
    ]
    combined = [(cid, 1.0 - i * 0.01) for i, cid in enumerate(chunks)]

    result = _select_context(db, combined, set(chunks), NO_SCOPE)
    assert len(result) <= MAX_CHUNKS_PER_DOC


def test_diversity_cap_leaves_room_for_other_documents(db: Database):
    """실측 재현: 8자리 근거 예산이 사본 여러 개(hash가 각기 다른 버전)에 몰리면 안 된다."""
    source_id = db.add_source("/자료")
    hoarder = add_doc(db, source_id, "몰린문서.hwp", hash_="hoarder")
    hoarder_chunks = [
        add_chunk(db, hoarder, f"몰린내용{i}", locator=f"{i}문단") for i in range(6)
    ]
    other = add_doc(db, source_id, "다른문서.hwp", hash_="other")
    other_chunk = add_chunk(db, other, "다른 내용")

    combined = [(cid, 0.9 - i * 0.01) for i, cid in enumerate(hoarder_chunks)]
    combined.append((other_chunk, 0.5))

    result = _select_context(
        db, combined, set(hoarder_chunks) | {other_chunk}, NO_SCOPE,
    )
    kept_ids = [cid for cid, _s in result]
    assert other_chunk in kept_ids, "다양성 상한이 없으면 다른 문서가 근거 자리를 못 얻는다"
    assert sum(1 for cid in kept_ids if cid in hoarder_chunks) <= MAX_CHUNKS_PER_DOC


def test_year_filter_does_not_reach_past_the_exploration_window_for_filler(db: Database):
    """실측으로 잡은 회귀.

    연도 필터가 다른 연도의 더 좋은 후보를 제외시키면, 빈 자리가 생긴다.
    그 빈 자리를 아무리 깊은 후보로든 채우면, 우연히 그 연도인 무관한
    문서가 승격된다("작년 예산…" 질문에 2025년인 행정사무감사 문서가
    끼어들었다). EXPLORATION_WINDOW 밖의 후보는 빈 자리를 채우는 데
    쓰지 않아야 한다 — 근거가 적게 나가는 것이 엉뚱한 근거가 나가는
    것보다 낫다.
    """
    from app.search.rag import EXPLORATION_WINDOW

    source_id = db.add_source("/자료")

    # 다른 연도의 진짜 관련 문서 — EXPLORATION_WINDOW 안에서 순위가 높다.
    on_topic_other_year = add_chunk(
        db, add_doc(db, source_id, "2022_예산.hwp", year=2022), "2022년 예산 요구자료"
    )
    # 탐색 범위를 채우는 중립 필러(연도 무관) — 전부 실제 문서라야
    # 대표본 판정을 통과해 순위 자리를 실제로 차지한다.
    fillers = [
        add_chunk(db, add_doc(db, source_id, f"필러{i}.hwp"), f"필러 내용 {i}")
        for i in range(EXPLORATION_WINDOW - 1)
    ]
    # 질문 연도와 같지만 무관한 문서 — 탐색 범위 훨씬 밖에 있다.
    off_topic_same_year = add_chunk(
        db, add_doc(db, source_id, "2025_무관.hwp", year=2025), "2025년 전혀 다른 업무"
    )

    combined = [(on_topic_other_year, 0.9)]
    combined += [(cid, 0.5 - i * 0.001) for i, cid in enumerate(fillers)]
    combined.append((off_topic_same_year, 0.01))

    strong_ids = {on_topic_other_year, off_topic_same_year, *fillers}
    result = _select_context(db, combined, strong_ids, QueryScope(years=[2025]))

    kept = [cid for cid, _s in result]
    assert off_topic_same_year not in kept, "탐색 범위 밖의 후보가 빈 자리를 채웠다"


def test_result_never_exceeds_top_k(db: Database):
    source_id = db.add_source("/자료")
    chunks = []
    for i in range(TOP_K + 10):
        doc_id = add_doc(db, source_id, f"문서{i}.hwp", hash_=f"h{i}")
        chunks.append(add_chunk(db, doc_id, f"내용{i}"))
    combined = [(cid, 1.0 - i * 0.001) for i, cid in enumerate(chunks)]

    result = _select_context(db, combined, set(chunks), NO_SCOPE)
    assert len(result) <= TOP_K


# ── 빈 입력 ─────────────────────────────────────────────────────────

def test_no_eligible_candidates_yields_empty_result(db: Database):
    assert _select_context(db, [(1, 0.9)], set(), NO_SCOPE) == []


# ── 근접 중복 축약 (R7) ─────────────────────────────────────────────

def test_identical_tables_across_versioned_files_do_not_fill_the_context(db: Database):
    """R6에서 넘어온 실패 그대로.

    "계약관리 처리 순서"를 물었는데 근거 8건이 전부 같은 [집계] 표였다.
    파일이 여덟 개(_부장수정·_송부·_최종…)라 hash 중복 제거도, 문서당
    상한도 통과했다. 내용으로 접어야 잡힌다.
    """
    source_id = db.add_source("/자료")
    table = "[집계]\n구분\t1분기\t2분기\n처리건수\t41\t38"

    dupes = []
    for i, suffix in enumerate(
        ["부장수정", "송부", "최종", "최종2", "수정", "복사본", "진짜최종", "초안"]
    ):
        doc_id = add_doc(db, source_id, f"2025_월간실적보고_{suffix}.xlsx",
                         year=2025, hash_=f"h{i}")   # 파일마다 hash가 다르다
        dupes.append(add_chunk(db, doc_id, table))

    # 진짜 관련 있는 다른 문서 하나 — 순위는 아래지만 자리를 얻어야 한다.
    real = add_chunk(
        db, add_doc(db, source_id, "2025_계약관리_용역계약서.pdf", year=2025, hash_="real"),
        "계약관리 용역계약 체결 절차",
    )

    combined = [(cid, 0.02 - i * 0.001) for i, cid in enumerate(dupes)]
    combined.append((real, 0.005))

    result = _select_context(
        db, combined, set(dupes) | {real}, NO_SCOPE, "계약관리 업무는 어떤 순서로 처리해?",
    )
    kept = [cid for cid, _s in result]

    assert sum(1 for cid in kept if cid in dupes) == 1, "같은 표가 여러 자리를 차지했다"
    assert real in kept, "중복본에 밀려 진짜 관련 문서가 근거에서 빠졌다"


def test_generic_words_are_not_treated_as_distinctive(db: Database):
    """'업무'처럼 어느 문서에나 있는 낱말로 관련성을 재면 무엇이든 통과한다.

    실측으로 겪었다 — 무관한 월간실적보고 문단이 '업무' 하나로 이웃 확장을
    통과해, 모델이 그 표 숫자로 계약관리 질문에 답해 버렸다.
    """
    from app.search.rag import _distinctive_terms

    source_id = db.add_source("/자료")
    for i in range(6):
        add_chunk(db, add_doc(db, source_id, f"문서{i}.hwp", hash_=f"h{i}"),
                  f"본 문서는 업무 중 {i}단계에서 작성되었다")
    add_chunk(db, add_doc(db, source_id, "계약.hwp", hash_="c"), "계약관리 절차")

    kept = _distinctive_terms(db, ["업무는", "계약관리"])
    assert "계약관리" in kept
    assert "업무는" not in kept


def test_distinctive_filter_keeps_everything_when_all_words_are_common(db: Database):
    """전부 흔한 낱말뿐이면 가릴 근거가 없다 — 막지 않는다."""
    from app.search.rag import _distinctive_terms

    source_id = db.add_source("/자료")
    for i in range(4):
        add_chunk(db, add_doc(db, source_id, f"문서{i}.hwp", hash_=f"h{i}"), "업무 자료")

    assert _distinctive_terms(db, ["업무는", "자료를"]) == ["업무는", "자료를"]


def test_query_term_match_is_promoted_within_the_selection(db: Database):
    """재정렬이 실제 선별 흐름 안에서 동작하는지 — 단위 시험만으로는 부족하다."""
    source_id = db.add_source("/자료")
    off_topic = add_chunk(
        db, add_doc(db, source_id, "무관.hwp", year=2025, hash_="a"), "전혀 다른 주제"
    )
    on_topic = add_chunk(
        db, add_doc(db, source_id, "계약.hwp", year=2025, hash_="b"), "계약관리 처리 절차"
    )

    result = _select_context(
        db, [(off_topic, 0.02), (on_topic, 0.019)],
        {off_topic, on_topic}, NO_SCOPE, "계약관리는 어떻게 처리해?",
    )
    assert [cid for cid, _s in result][0] == on_topic
