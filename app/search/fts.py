"""FTS5 검색 — 문서 화면과 질문 화면(RAG)이 함께 쓰는 정확 일치 검색.

trigram 토크나이저는 3글자 미만 질의를 처리하지 못한다(실측: unicode61은
"행정사무감사"에서 "사무감사"를 못 찾았고 trigram은 찾았다 — schema.sql
참고). 그래서 이 판단과 질의 이스케이프 규칙을 여기 한 곳에 모은다.
규칙이 화면마다 갈라지면 같은 검색어에 화면마다 다른 결과가 나온다.

의미 검색(벡터)은 "비슷한 뜻"을 찾고, 이 모듈은 "정확히 그 글자"를 찾는다.
문서번호·고유명사처럼 정확히 일치해야 하는 질의는 벡터만으로는 약하다
(doc/질문화면_RAG_개선계획.md §1 측정 7) — RAG는 이 둘을 합쳐 쓴다(R3).
"""

from __future__ import annotations

from ..db import Database

MIN_TRIGRAM_LEN = 3


def usable_for_trigram(term: str) -> bool:
    return len(term.strip()) >= MIN_TRIGRAM_LEN


def escape_match(term: str) -> str:
    """공백으로 나눈 조각을 AND로 묶는다. trigram은 구절 전체를 통으로 찾는다.

    문서 화면의 검색창처럼 사용자가 직접 고른 핵심어에 맞다 — 짧은 단어
    몇 개를 입력하면 전부 들어간 문서만 남긴다.
    """
    parts = [p for p in term.split() if len(p) >= MIN_TRIGRAM_LEN]
    if not parts:
        return '"' + term.replace('"', '""') + '"'
    return " AND ".join('"' + p.replace('"', '""') + '"' for p in parts)


def escape_match_any(term: str) -> str:
    """공백으로 나눈 조각을 OR로 묶는다.

    질문 문장에는 조사·어미가 섞여 있어 문장 전체 단어가 원문에 그대로
    다 있을 리 없다("작성됐어?"는 원문의 "작성되었다"와 다른 문자열이다).
    AND로 묶으면 단어 하나만 어긋나도 통째로 실패한다. RAG의 조각 검색은
    하나라도 정확히 일치하면 후보로 올리고, 최종 채택 여부는 RRF가 다른
    신호(의미 유사도)와 합쳐서 가른다 — 여기서 다 걸러낼 필요가 없다.
    """
    parts = [p for p in term.split() if len(p) >= MIN_TRIGRAM_LEN]
    if not parts:
        return '"' + term.replace('"', '""') + '"'
    return " OR ".join('"' + p.replace('"', '""') + '"' for p in parts)


def search_document_ids(db: Database, term: str, limit: int = 500) -> list[int]:
    """document_fts로 문서 id를 관련도 순으로 찾는다. 실패하거나 너무 짧으면 빈 목록."""
    term = term.strip()
    if not usable_for_trigram(term):
        return []
    try:
        rows = db.con.execute(
            "SELECT rowid FROM document_fts WHERE document_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (escape_match(term), limit),
        ).fetchall()
    except Exception:
        # 이스케이프해도 FTS5 문법을 깨는 입력이 있을 수 있다 — 그때는
        # 검색 없음으로 취급한다. 문서 화면은 이미 LIKE로 폴백한다.
        return []
    return [row["rowid"] for row in rows]


def search_chunk_ids(db: Database, term: str, limit: int = 40) -> list[int]:
    """chunk_fts로 조각 id를 관련도 순으로 찾는다. RAG 하이브리드 검색의 절반이다.

    원본이 사라진 문서의 조각은 뺀다 — 벡터 검색이 이미 하는 것과
    같은 기준이라야, 두 순위를 합칠 때 후보 모집단이 어긋나지 않는다.
    """
    term = term.strip()
    if not usable_for_trigram(term):
        return []
    try:
        rows = db.con.execute(
            "SELECT f.rowid AS chunk_id FROM chunk_fts f "
            "JOIN chunks c ON c.id = f.rowid "
            "JOIN documents d ON d.id = c.doc_id "
            "WHERE chunk_fts MATCH ? AND d.missing_since IS NULL "
            "ORDER BY rank LIMIT ?",
            (escape_match_any(term), limit),
        ).fetchall()
    except Exception:
        return []
    return [row["chunk_id"] for row in rows]
