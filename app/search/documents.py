"""문서 목록의 필터·건수·페이지를 같은 조건으로 조회한다."""

from dataclasses import dataclass

from ..db import Database, representative_predicate
from . import fts


@dataclass
class DocumentPage:
    rows: list
    total: int
    page: int
    pages: int


def find_documents(db: Database, term: str = "", *, documents_only: bool = True,
                   collapse: bool = True, unclassified: bool = False,
                   page: int = 0, page_size: int = 100) -> DocumentPage:
    page_size = max(1, min(500, page_size))
    where, params = [], []
    if documents_only:
        where.append("d.parse_status != 'skipped'")
    if collapse:
        where.append("(d.missing_since IS NOT NULL OR " + representative_predicate("d") + ")")
    if unclassified:
        where.append("NOT EXISTS(SELECT 1 FROM task_docs td WHERE td.doc_id = d.id)")
    term = term.strip()
    if term:
        # LIKE에도 사용자의 %·_를 검색 연산자로 해석하지 않는다.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        text_match = "(d.filename LIKE ? ESCAPE '\\' OR d.path LIKE ? ESCAPE '\\' OR di.author LIKE ? ESCAPE '\\')"
        params.extend([like] * 3)
        if fts.usable_for_trigram(term):
            where.append(f"({text_match} OR d.id IN (SELECT rowid FROM document_fts WHERE document_fts MATCH ?))")
            params.append(fts.escape_match(term))
        else:
            where.append(f"({text_match} OR di.body LIKE ? ESCAPE '\\')")
            params.append(like)
    clause = " AND ".join(where) or "1=1"
    source = "FROM documents d LEFT JOIN document_index di ON di.doc_id = d.id"
    total = db.con.execute(f"SELECT COUNT(*) {source} WHERE {clause}", params).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(0, page), pages - 1)
    rows = db.con.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM documents y WHERE y.hash = d.hash "
        "AND d.hash IS NOT NULL AND y.missing_since IS NULL) AS dup_n, "
        "(SELECT t.name FROM task_docs td JOIN tasks t ON t.id = td.task_id "
        "WHERE td.doc_id = d.id ORDER BY td.is_primary DESC, t.id LIMIT 1) AS task_name "
        f"{source} WHERE {clause} "
        "ORDER BY d.eff_date DESC NULLS LAST, d.filename, d.id LIMIT ? OFFSET ?",
        [*params, page_size, page * page_size],
    ).fetchall()
    return DocumentPage(rows, total, page, pages)
