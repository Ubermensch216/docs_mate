"""SQLite 접근 계층.

프로젝트 하나 = DB 파일 하나. 백업과 이동이 쉽고 설치 부담이 없다.

원본 파일은 여기에 들어오지 않는다. 경로와 분석 결과만 저장한다.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 스키마가 자란 뒤에 열린 옛 DB에 붙일 열들. (표, 열, DDL)
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("documents", "meta_created", "meta_created TEXT"),
    ("documents", "meta_modified", "meta_modified TEXT"),
)

APP_DIR_NAME = "NunchiCoach"
# 제품 이름을 바꾸기 전에 쓰던 폴더. 이미 만들어 둔 프로젝트가 여기 있으면
# 새 이름으로 갈아타면서 사용자가 자기 분석 결과를 잃는다. 새로 만들지는
# 않되, 남아 있으면 그대로 이어 쓴다.
LEGACY_APP_DIR_NAME = "WorkMemory"


def default_project_dir() -> Path:
    """%LOCALAPPDATA%\\NunchiCoach\\projects — 사용자 권한으로 제한된다 (SEC-003).

    옛 이름(WorkMemory) 폴더가 이미 있고 새 폴더는 아직 없다면 옛 폴더를
    그대로 쓴다. 이름을 바꿨다고 사용자의 기존 프로젝트가 사라지면 안 된다.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"

    current = root / APP_DIR_NAME / "projects"
    legacy = root / LEGACY_APP_DIR_NAME / "projects"
    if not current.exists() and legacy.exists():
        return legacy
    return current


def open_project(name: str = "default", root: Path | None = None) -> "Database":
    directory = (root or default_project_dir()) / name
    directory.mkdir(parents=True, exist_ok=True)
    db = Database(directory / "project.db")
    db.init()
    return db


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._con: sqlite3.Connection | None = None

    # ── 연결 ────────────────────────────────────────────────────────
    @property
    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA synchronous = NORMAL")
            self._con = con
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── 스키마 ──────────────────────────────────────────────────────
    def init(self) -> None:
        self.con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._add_missing_columns()
        current = self.get_meta("schema_version")
        if current is None:
            self.set_meta("schema_version", SCHEMA_VERSION)
        elif current != SCHEMA_VERSION:
            raise RuntimeError(
                f"스키마 버전 불일치: DB={current}, 앱={SCHEMA_VERSION}"
            )

    def _add_missing_columns(self) -> None:
        """CREATE TABLE IF NOT EXISTS는 이미 있는 표에 새 열을 붙이지 않는다.

        전진 방향의 열 추가만 여기서 처리한다. 열 삭제·형 변경을 포함한
        본격적인 마이그레이션은 Step 10에서 다룬다.
        """
        for table, column, ddl in ADDED_COLUMNS:
            existing = {
                row["name"] for row in self.con.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def get_meta(self, key: str) -> str | None:
        row = self.con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ── 자료원 ──────────────────────────────────────────────────────
    def add_source(self, path: str | Path, kind: str = "local", excludes: str = "") -> int:
        text = str(Path(path))
        cur = self.con.execute(
            "INSERT INTO sources(path, kind, excludes) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, excludes = excluded.excludes",
            (text, kind, excludes),
        )
        if cur.lastrowid:
            self.audit("source.add", text)
            return cur.lastrowid
        row = self.con.execute("SELECT id FROM sources WHERE path = ?", (text,)).fetchone()
        return row["id"]

    def remove_source(self, source_id: int) -> None:
        self.con.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.audit("source.remove", str(source_id))

    def sources(self) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM sources ORDER BY id").fetchall()

    # ── 문서 ────────────────────────────────────────────────────────
    def upsert_document(self, source_id: int, fields: dict[str, Any]) -> int:
        """경로를 키로 문서를 등록·갱신한다. 재스캔에서 기존 분석 결과를 잃지 않는다."""
        columns = ["source_id", *fields.keys()]
        values = [source_id, *fields.values()]
        placeholders = ", ".join("?" * len(columns))
        updates = ", ".join(f"{c} = excluded.{c}" for c in fields if c != "path")
        sql = (
            f"INSERT INTO documents({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(path) DO UPDATE SET {updates}, last_seen = datetime('now'), "
            f"missing_since = NULL"
        )
        self.con.execute(sql, values)
        row = self.con.execute(
            "SELECT id FROM documents WHERE path = ?", (fields["path"],)
        ).fetchone()
        return row["id"]

    def update_document(self, doc_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.con.execute(
            f"UPDATE documents SET {assignments} WHERE id = ?",
            (*fields.values(), doc_id),
        )

    def mark_missing(self, doc_ids: Iterable[int]) -> int:
        """원본이 사라져도 기록을 지우지 않는다 (ING-008)."""
        ids = list(doc_ids)
        if not ids:
            return 0
        marks = ", ".join("?" * len(ids))
        self.con.execute(
            f"UPDATE documents SET missing_since = datetime('now') "
            f"WHERE id IN ({marks}) AND missing_since IS NULL",
            ids,
        )
        return len(ids)

    def documents(self, limit: int = 500, offset: int = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM documents ORDER BY eff_date DESC NULLS LAST, filename LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    def document(self, doc_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

    def paths_for_source(self, source_id: int) -> dict[str, sqlite3.Row]:
        rows = self.con.execute(
            "SELECT id, path, size, fs_mtime, hash, parse_status, parser_version "
            "FROM documents WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        return {row["path"]: row for row in rows}

    # ── 본문·검색 색인 ──────────────────────────────────────────────
    def replace_sections(self, doc_id: int, sections: Sequence[tuple[str, int, str, str]]) -> None:
        self.con.execute("DELETE FROM document_sections WHERE doc_id = ?", (doc_id,))
        if sections:
            self.con.executemany(
                "INSERT INTO document_sections(doc_id, kind, ordinal, locator, text) "
                "VALUES (?, ?, ?, ?, ?)",
                [(doc_id, *s) for s in sections],
            )

    def index_document(self, doc_id: int, filename: str, path: str, author: str, body: str) -> None:
        self.con.execute(
            "INSERT INTO document_index(doc_id, filename, path, author, body) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET "
            "filename = excluded.filename, path = excluded.path, "
            "author = excluded.author, body = excluded.body",
            (doc_id, filename, path, author or "", body),
        )

    def replace_dates(self, doc_id: int, candidates: Sequence[tuple[str, str, str, str, str]]) -> None:
        """(kind, value, precision, raw, locator) 후보를 통째로 갈아 끼운다."""
        self.con.execute("DELETE FROM document_dates WHERE doc_id = ?", (doc_id,))
        if candidates:
            self.con.executemany(
                "INSERT OR REPLACE INTO document_dates"
                "(doc_id, kind, value, precision, raw, locator) VALUES (?, ?, ?, ?, ?, ?)",
                [(doc_id, *c) for c in candidates],
            )

    def dates(self, doc_id: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM document_dates WHERE doc_id = ?", (doc_id,)
        ).fetchall()

    # ── AI 결과 ─────────────────────────────────────────────────────
    def save_analysis(self, doc_id: int, **fields: Any) -> None:
        """AI 결과는 '제안' 상태로 저장한다.

        사람이 고친 값(status='edited')은 덮어쓰지 않는다 (NFR-SAF-004).
        """
        columns = ["doc_id", *fields.keys()]
        placeholders = ", ".join("?" * len(columns))
        updates = ", ".join(f"{c} = excluded.{c}" for c in fields)
        self.con.execute(
            f"INSERT INTO ai_document({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET {updates}, updated_at = datetime('now') "
            f"WHERE ai_document.status != 'edited'",
            [doc_id, *fields.values()],
        )

    def analysis(self, doc_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM ai_document WHERE doc_id = ?", (doc_id,)
        ).fetchone()

    def save_doc_embedding(
        self, doc_id: int, model: str, dim: int, vector: bytes, source_len: int
    ) -> None:
        self.con.execute(
            "INSERT INTO doc_embeddings(doc_id, model, dim, vector, source_len) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET "
            "model = excluded.model, dim = excluded.dim, vector = excluded.vector, "
            "source_len = excluded.source_len, updated_at = datetime('now')",
            (doc_id, model, dim, vector, source_len),
        )

    # ── 집계 (홈·상태 바) ───────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        row = self.con.execute(
            """
            SELECT
              COUNT(*)                                                  AS total,
              SUM(parse_status != 'skipped')                            AS documents,
              SUM(parse_status IN ('ok', 'partial'))                    AS parsed,
              SUM(parse_status = 'pending')                             AS parse_pending,
              SUM(parse_status NOT IN ('ok','partial','pending','skipped')) AS parse_failed,
              SUM(analysis_status = 'done')                             AS analyzed,
              SUM(missing_since IS NOT NULL)                            AS missing,
              SUM(eff_date IS NOT NULL)                                 AS dated
            FROM documents
            """
        ).fetchone()
        counts = {k: (row[k] or 0) for k in row.keys()}
        dup = self.con.execute(
            "SELECT COALESCE(SUM(n), 0) - COUNT(*) AS extra FROM duplicate_groups"
        ).fetchone()
        counts["duplicate_extra"] = dup["extra"] or 0
        counts["tasks"] = self.con.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        counts["embedded"] = self.con.execute(
            "SELECT COUNT(*) AS n FROM doc_embeddings"
        ).fetchone()["n"]
        counts["summarized"] = self.con.execute(
            "SELECT COUNT(*) AS n FROM ai_document WHERE summary IS NOT NULL"
        ).fetchone()["n"]
        counts["in_task"] = self.con.execute(
            "SELECT COUNT(DISTINCT doc_id) AS n FROM task_docs"
        ).fetchone()["n"]
        counts["chunks"] = self.con.execute(
            "SELECT COUNT(*) AS n FROM chunks"
        ).fetchone()["n"]
        counts["chunks_embedded"] = self.con.execute(
            "SELECT COUNT(*) AS n FROM embeddings"
        ).fetchone()["n"]
        counts["cycles_found"] = self.con.execute(
            "SELECT COUNT(*) AS n FROM task_cycles"
        ).fetchone()["n"]
        return counts

    # ── 업무 (What) ─────────────────────────────────────────────────
    def tasks(self) -> list[sqlite3.Row]:
        """업무 목록. 문서 수가 아니라 후임자 관점 중요도 순으로 정렬한다.

        최근 활동(최신 연도)과 규모를 함께 본다. 문서가 많다고 중요한 업무는
        아니다 — 회의록이 수백 건 쌓인 폴더가 그 예다.
        """
        return self.con.execute(
            """
            SELECT t.*,
                   COUNT(td.doc_id)   AS doc_count,
                   MIN(d.eff_year)    AS first_year,
                   MAX(d.eff_year)    AS last_year,
                   COUNT(DISTINCT d.eff_year) AS year_count
            FROM tasks t
            LEFT JOIN task_docs td ON td.task_id = t.id
            LEFT JOIN documents d ON d.id = td.doc_id AND d.missing_since IS NULL
            GROUP BY t.id
            ORDER BY COALESCE(MAX(d.eff_year), 0) DESC,
                     COUNT(DISTINCT d.eff_year) DESC,
                     COUNT(td.doc_id) DESC
            """
        ).fetchall()

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def task_reading(self, task_id: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT r.*, d.filename, d.path, d.eff_date, d.eff_precision, d.eff_date_kind "
            "FROM task_reading r JOIN documents d ON d.id = r.doc_id "
            "WHERE r.task_id = ? ORDER BY r.ordinal",
            (task_id,),
        ).fetchall()

    def task_documents(self, task_id: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT d.*, td.origin AS assign_origin FROM task_docs td "
            "JOIN documents d ON d.id = td.doc_id "
            "WHERE td.task_id = ? ORDER BY d.eff_date DESC NULLS LAST, d.filename",
            (task_id,),
        ).fetchall()

    def rename_task(self, task_id: int, name: str) -> bool:
        """사람이 고친 이름은 재발견이 덮어쓰지 않는다."""
        existing = self.con.execute(
            "SELECT id FROM tasks WHERE name = ? AND id != ?", (name, task_id)
        ).fetchone()
        if existing:
            return False
        before = self.con.execute(
            "SELECT name FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.con.execute(
            "UPDATE tasks SET name = ?, status = 'edited', origin = 'user' WHERE id = ?",
            (name, task_id),
        )
        self.con.execute(
            "INSERT INTO corrections(target, target_id, field, before_val, after_val) "
            "VALUES ('tasks', ?, 'name', ?, ?)",
            (task_id, before["name"] if before else None, name),
        )
        self.audit("task.rename", str(task_id), f"{before['name'] if before else ''} → {name}")
        return True

    def approve_task(self, task_id: int) -> None:
        self.con.execute("UPDATE tasks SET status = 'approved' WHERE id = ?", (task_id,))
        self.audit("task.approve", str(task_id))

    def detach_document(self, task_id: int, doc_id: int) -> None:
        self.con.execute(
            "DELETE FROM task_docs WHERE task_id = ? AND doc_id = ?", (task_id, doc_id)
        )
        self.con.execute("DELETE FROM task_reading WHERE task_id = ? AND doc_id = ?",
                         (task_id, doc_id))
        self.con.execute(
            "INSERT INTO corrections(target, target_id, doc_id, field, before_val, after_val) "
            "VALUES ('task_docs', ?, ?, 'task', ?, NULL)",
            (task_id, doc_id, str(task_id)),
        )
        self.audit("task.detach", f"{task_id}/{doc_id}")

    # ── 주기 (When) ─────────────────────────────────────────────────
    def replace_task_cycles(self, task_id: int, cycles: list[dict]) -> None:
        """이 업무의 AI 제안 주기를 통째로 갈아 끼운다.

        사람이 확정한 주기(decided_by='user')는 지금 이 단계에 UI가 없어
        발생하지 않지만, 미래를 위해 건드리지 않는다 (NFR-SAF-004).
        """
        self.con.execute(
            "DELETE FROM task_cycles WHERE task_id = ? AND decided_by = 'ai'",
            (task_id,),
        )
        for cycle in cycles:
            self.con.execute(
                "INSERT INTO task_cycles"
                "(task_id, kind, months, day_hint, years_observed, confidence, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id, cycle["kind"], cycle["months"], cycle.get("day_hint"),
                    cycle["years_observed"], cycle["confidence"], cycle.get("evidence"),
                ),
            )

    def task_cycle(self, task_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM task_cycles WHERE task_id = ? ORDER BY years_observed DESC LIMIT 1",
            (task_id,),
        ).fetchone()

    def all_cycles(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT c.*, t.name AS task_name, t.id AS task_id "
            "FROM task_cycles c JOIN tasks t ON t.id = c.task_id "
            "ORDER BY c.years_observed DESC"
        ).fetchall()

    def task_grid_documents(self, task_id: int) -> list[sqlite3.Row]:
        """이 업무 문서의 연도·월·시점출처를 격자 계산용으로 돌려준다.

        완전 중복본은 하나만 남긴다 — 같은 문서가 여러 번 복사됐다고
        그 달의 반복 근거가 더 세지면 안 된다.
        """
        return self.con.execute(
            """
            SELECT d.id, d.eff_year AS year, d.eff_month AS month,
                   d.eff_date, d.eff_precision, d.eff_date_kind
            FROM task_docs td
            JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.missing_since IS NULL AND d.eff_year IS NOT NULL
              AND (d.hash IS NULL OR d.id = (
                    SELECT MIN(x.id) FROM documents x
                    WHERE x.hash = d.hash AND x.missing_since IS NULL))
            """,
            (task_id,),
        ).fetchall()

    # ── 처리 순서 (How) ─────────────────────────────────────────────
    def task_year_documents(self, task_id: int, year: int) -> list[sqlite3.Row]:
        """이 업무·이 연도의 문서를 처리 순서 재구성용으로 돌려준다.

        완전 중복본은 하나만, 파일 수정일로만 판정된 것은 제외한다 — When과
        같은 기준이다. 같은 격자를 가로로 읽는 것이 How이기 때문이다.
        """
        return self.con.execute(
            """
            SELECT d.id, d.filename, d.eff_month AS month, d.eff_date,
                   d.eff_precision
            FROM task_docs td
            JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.eff_year = ? AND d.missing_since IS NULL
              AND d.eff_month IS NOT NULL AND d.eff_date_kind != 'fs'
              AND (d.hash IS NULL OR d.id = (
                    SELECT MIN(x.id) FROM documents x
                    WHERE x.hash = d.hash AND x.missing_since IS NULL))
            """,
            (task_id, year),
        ).fetchall()

    def task_years(self, task_id: int) -> list[int]:
        rows = self.con.execute(
            """
            SELECT DISTINCT d.eff_year AS year
            FROM task_docs td JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.missing_since IS NULL
              AND d.eff_year IS NOT NULL AND d.eff_month IS NOT NULL
              AND d.eff_date_kind != 'fs'
            ORDER BY d.eff_year
            """,
            (task_id,),
        ).fetchall()
        return [row["year"] for row in rows]

    def replace_task_steps(self, task_id: int, year: int, steps: list[dict]) -> None:
        """이 업무·이 연도의 AI 제안 단계를 통째로 갈아 끼운다.

        사람이 고친 단계(decided_by='user')는 건드리지 않는다 (NFR-SAF-004).
        """
        self.con.execute(
            "DELETE FROM task_steps WHERE task_id = ? AND year = ? AND decided_by = 'ai'",
            (task_id, year),
        )
        for step in steps:
            self.con.execute(
                "INSERT INTO task_steps"
                "(task_id, year, ordinal, label, month, day_hint, doc_id, "
                " is_inferred, gap_note) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    task_id, year, step["ordinal"], step["label"], step["month"],
                    step["day_hint"], step["doc_id"], step.get("gap_note"),
                ),
            )

    def task_steps(self, task_id: int, year: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT s.*, d.filename, d.path FROM task_steps s "
            "LEFT JOIN documents d ON d.id = s.doc_id "
            "WHERE s.task_id = ? AND s.year = ? ORDER BY s.ordinal",
            (task_id, year),
        ).fetchall()

    # ── 질문 (RAG 재료) ─────────────────────────────────────────────
    def replace_document_chunks(
        self, doc_id: int, pieces: list[tuple[int, str, str]]
    ) -> list[tuple[int, str]]:
        """이 문서의 조각을 통째로 갈아 끼운다. (chunk_id, text) 목록을 돌려준다.

        임베딩은 chunks에 FK CASCADE로 걸려 있어 조각을 지우면 함께 지워진다.
        """
        self.con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        out: list[tuple[int, str]] = []
        for ordinal, locator, text in pieces:
            cur = self.con.execute(
                "INSERT INTO chunks(doc_id, ordinal, locator, text) VALUES (?, ?, ?, ?)",
                (doc_id, ordinal, locator, text),
            )
            out.append((cur.lastrowid, text))
        return out

    def save_chunk_embedding(self, chunk_id: int, model: str, dim: int, vector: bytes) -> None:
        self.con.execute(
            "INSERT INTO embeddings(chunk_id, model, dim, vector) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET "
            "model = excluded.model, dim = excluded.dim, vector = excluded.vector",
            (chunk_id, model, dim, vector),
        )

    def save_question(
        self, question: str, answer: str, citations: str, withheld: bool, model: str
    ) -> int:
        cur = self.con.execute(
            "INSERT INTO questions(question, answer, citations, withheld, model) "
            "VALUES (?, ?, ?, ?, ?)",
            (question, answer, citations, int(withheld), model),
        )
        return cur.lastrowid

    def rate_question(self, question_id: int, rating: str) -> None:
        self.con.execute(
            "UPDATE questions SET rating = ? WHERE id = ?", (rating, question_id)
        )

    def unclassified_count(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) AS n FROM documents d "
            "WHERE d.missing_since IS NULL AND d.parse_status IN ('ok','partial') "
            "  AND NOT EXISTS(SELECT 1 FROM task_docs td WHERE td.doc_id = d.id)"
        ).fetchone()["n"]

    def duplicate_groups(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM duplicate_groups ORDER BY n DESC LIMIT ?", (limit,)
        ).fetchall()

    # ── 감사 로그 ───────────────────────────────────────────────────
    def audit(self, action: str, target: str | None = None,
              detail: str | None = None, result: str | None = None) -> None:
        """본문이나 민감정보를 남기지 않는다 (SEC-005). 식별자와 결과만 기록한다."""
        self.con.execute(
            "INSERT INTO audit_logs(action, target, detail, result) VALUES (?, ?, ?, ?)",
            (action, target, detail, result),
        )

    def recent_audit(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
