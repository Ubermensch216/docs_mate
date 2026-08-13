"""FTS5 검색 시험 (RAG 개선 R3).

문서 화면과 질문 화면(RAG)이 같은 규칙을 써야 한다. 규칙이 갈라지면
"문서 화면에서는 찾아지는데 질문하면 못 찾는" 상황이 생긴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.search import fts


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    project = Database(tmp_path / "project.db")
    project.init()
    yield project
    project.close()


# ── 판단 규칙 ───────────────────────────────────────────────────────

def test_short_terms_are_not_usable_for_trigram():
    """trigram은 3글자 미만 질의를 처리하지 못한다 — schema.sql의 실측 근거."""
    assert not fts.usable_for_trigram("가")
    assert not fts.usable_for_trigram("가나")
    assert fts.usable_for_trigram("가나다")


def test_usable_check_trims_whitespace():
    assert not fts.usable_for_trigram("  가나  ")


def test_escape_wraps_each_long_token_and_ands_them():
    assert fts.escape_match("행정사무감사 제출자료") == '"행정사무감사" AND "제출자료"'


def test_escape_drops_short_tokens():
    assert fts.escape_match("행정사무감사 및") == '"행정사무감사"'


def test_escape_falls_back_to_the_whole_term_when_every_token_is_short():
    assert fts.escape_match("및 또") == '"및 또"'


def test_escape_match_any_ors_the_tokens_instead_of_anding():
    """질문 문장은 조사·어미가 섞여 있어 모든 단어가 원문에 그대로 있을 리 없다."""
    assert fts.escape_match_any("행정사무감사 제출자료") == '"행정사무감사" OR "제출자료"'


# ── 문서 검색 ───────────────────────────────────────────────────────

def test_search_document_ids_finds_indexed_filename(db: Database):
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/행정사무감사_제출자료.hwp", "filename": "행정사무감사_제출자료.hwp",
        "ext": ".hwp", "parse_status": "ok",
    })
    db.index_document(doc_id, "행정사무감사_제출자료.hwp",
                      "/자료/행정사무감사_제출자료.hwp", "", "요구자료 접수 및 제출")

    assert doc_id in fts.search_document_ids(db, "행정사무감사")


def test_search_document_ids_is_empty_for_short_terms(db: Database):
    assert fts.search_document_ids(db, "가나") == []


def test_search_document_ids_swallows_bad_syntax(db: Database):
    """이스케이프해도 깨지는 입력이 있을 수 있다 — 예외 대신 빈 목록."""
    assert fts.search_document_ids(db, "((()") == []


# ── 조각 검색 ───────────────────────────────────────────────────────

def test_search_chunk_ids_finds_exact_term(db: Database):
    """RAG 하이브리드 검색의 절반 — 벡터가 못 잡는 정확 일치를 여기서 잡는다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/문서.hwp", "filename": "문서.hwp", "ext": ".hwp",
        "parse_status": "ok",
    })
    chunk_id, _text = db.replace_document_chunks(
        doc_id, [(1, "1문단", "부서별자료요청 공문을 9월에 접수했다")]
    )[0]

    assert chunk_id in fts.search_chunk_ids(db, "부서별자료요청")


def test_search_chunk_ids_excludes_missing_documents(db: Database):
    """원본이 사라진 문서는 벡터 검색과 같은 기준으로 제외해야 후보 모집단이 맞는다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/사라진문서.hwp", "filename": "사라진문서.hwp", "ext": ".hwp",
        "parse_status": "ok",
    })
    db.replace_document_chunks(doc_id, [(1, "1문단", "고유한검색어테스트")])
    db.mark_missing([doc_id])

    assert fts.search_chunk_ids(db, "고유한검색어테스트") == []


def test_search_chunk_ids_respects_the_limit(db: Database):
    source_id = db.add_source("/자료")
    for i in range(5):
        doc_id = db.upsert_document(source_id, {
            "path": f"/자료/문서{i}.hwp", "filename": f"문서{i}.hwp", "ext": ".hwp",
            "parse_status": "ok",
        })
        db.replace_document_chunks(doc_id, [(1, "1문단", "공통키워드 내용입니다")])

    assert len(fts.search_chunk_ids(db, "공통키워드", limit=3)) == 3


def test_search_chunk_ids_ranks_more_specific_matches_first(db: Database):
    source_id = db.add_source("/자료")
    exact_doc = db.upsert_document(source_id, {
        "path": "/자료/정확.hwp", "filename": "정확.hwp", "ext": ".hwp", "parse_status": "ok",
    })
    exact_chunk, _ = db.replace_document_chunks(
        exact_doc, [(1, "1문단", "행정사무감사")]
    )[0]
    noisy_doc = db.upsert_document(source_id, {
        "path": "/자료/잡음.hwp", "filename": "잡음.hwp", "ext": ".hwp", "parse_status": "ok",
    })
    db.replace_document_chunks(noisy_doc, [(
        1, "1문단",
        "행정사무감사에 대한 이런저런 이야기와 다른 여러 문단이 섞인 훨씬 긴 문서 내용" * 3,
    )])

    ranked = fts.search_chunk_ids(db, "행정사무감사")
    assert ranked and ranked[0] == exact_chunk


def test_search_chunk_ids_is_empty_for_short_terms(db: Database):
    assert fts.search_chunk_ids(db, "가") == []


def test_search_chunk_ids_matches_a_natural_question_via_any_token(db: Database):
    """전체 문장이 아니라 단어 하나만 일치해도 후보가 된다 — RAG 질문은 문장이다."""
    source_id = db.add_source("/자료")
    doc_id = db.upsert_document(source_id, {
        "path": "/자료/부서별자료요청.hwp", "filename": "부서별자료요청.hwp",
        "ext": ".hwp", "parse_status": "ok",
    })
    chunk_id, _ = db.replace_document_chunks(
        doc_id, [(1, "1문단", "부서별자료요청 문서는 9월에 작성되었다")]
    )[0]

    # 질문 문장의 다른 단어("작성됐어?")는 원문과 글자가 다르다 — 그래도
    # "부서별자료요청"이 정확히 일치하므로 후보에 들어야 한다.
    assert chunk_id in fts.search_chunk_ids(db, "부서별자료요청 문서는 언제 작성됐어?")
