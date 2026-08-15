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

from ..core.chunking import CHUNKING_VERSION
from .migrations import SCHEMA_VERSION, add_column, upgrade

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


def representative_predicate(alias: str = "d") -> str:
    """완전 중복본 중 대표 하나만 통과시키는 SQL 조각.

    When·How·문서 화면·RAG가 전부 같은 기준을 써야 한다 — 화면마다 다른
    문서를 대표로 고르면, 예를 들어 업무 상세에서는 A가 대표인데 질문
    화면 근거에는 B가 나오는 식으로 사용자가 혼란스럽다. 네 곳에 각자
    같은 문장을 박아 두던 것을 여기 하나로 모은다.
    """
    return (
        f"({alias}.hash IS NULL OR {alias}.id = ("
        f"SELECT MIN(x.id) FROM documents x "
        f"WHERE x.hash = {alias}.hash AND x.missing_since IS NULL))"
    )


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
    return open_project_at((root or default_project_dir()) / name)


def open_project_at(directory: Path | str) -> "Database":
    """폴더 하나를 프로젝트로 연다.

    이름이 아니라 경로를 받는 통로가 따로 필요하다 — 프로젝트 목록(registry)은
    저장 위치 바깥(USB·공유 폴더)의 폴더도 열 수 있어야 하고, 그 경우 '이름'
    으로는 가리킬 수 없다.
    """
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    db = Database(folder / "project.db")
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
        """스키마를 최신으로 맞춘다. 기존 프로젝트는 승격하고, 새 프로젝트는 만든다.

        순서가 중요하다. 옛 DB에 schema.sql을 먼저 부으면 v2 표만 생기고
        v2 열은 빠진 어중간한 상태가 된다. 버전을 먼저 읽고 승격한 뒤에
        신규 설치용 스키마를 붓는다(전부 IF NOT EXISTS라 안전하다).
        """
        current = self._stored_version()
        if current is not None and current != SCHEMA_VERSION:
            new_version = upgrade(self.con, self.path, current)
            self.con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._add_missing_columns()
            self.audit("schema.migrate", f"v{current} → v{new_version}")
        else:
            self.con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._add_missing_columns()
            if current is None:
                self.set_meta("schema_version", SCHEMA_VERSION)

        # 두 경로 모두를 지나야 한다 — 스키마 승격 경로에서 빠뜨리면
        # 옛 조각을 지울 기회를 영영 놓친다(이 코드가 나오기 전까지는
        # 승격 경로가 여기서 그냥 return 해 버렸다).
        self.sync_chunking_version()

    def sync_chunking_version(self) -> bool:
        """조각 규칙이 바뀌면 옛 조각을 지우고 다시 만들게 한다.

        메타에 버전이 없다고 '새 프로젝트'로 넘겨짚으면 안 된다 — v1은
        애초에 버전을 남기지 않았으므로, 메타가 비어 있어도 chunks 표에
        이미 행이 있으면 그건 옛 규칙(하한 없이 문단 하나 = 조각 하나)으로
        만든 조각이다. 실제 데이터가 있는지로 신규/기존을 가른다.

        chunks_checked도 함께 지운다 — 그러지 않으면 재개 판정
        (ui/shell.py _resume_if_pending)이 '이미 다 됐다'고 믿고 다시
        돌지 않는다.
        """
        current = self.get_meta("chunking_version")
        if current == CHUNKING_VERSION:
            return False
        had_chunks = self.con.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is not None
        if had_chunks:
            self.con.execute("DELETE FROM chunks")   # CASCADE로 embeddings도 함께 지워진다
            self.con.execute("DELETE FROM meta WHERE key = 'chunks_checked'")
            self.audit("chunking.reindex", detail=f"{current or '?'} → {CHUNKING_VERSION}")
        self.set_meta("chunking_version", CHUNKING_VERSION)
        return had_chunks

    def _stored_version(self) -> str | None:
        """meta 표가 아직 없을 수 있다 — 그때는 새 프로젝트다."""
        try:
            return self.get_meta("schema_version")
        except sqlite3.OperationalError:
            return None

    def _add_missing_columns(self) -> None:
        """CREATE TABLE IF NOT EXISTS는 이미 있는 표에 새 열을 붙이지 않는다.

        버전을 올리지 않고 붙일 수 있는 열만 여기서 처리한다. 표 추가나
        데이터 이관이 필요한 변경은 db/migrations.py로 간다.
        """
        for table, column, ddl in ADDED_COLUMNS:
            add_column(self.con, table, column, ddl)

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

    def change_source_path(self, source_id: int, path: str | Path, kind: str = "") -> bool:
        """자료원이 가리키는 폴더를 바꾼다.

        최초 지정이 곧 확정이던 것이 문제였다 — 자료를 옮기거나 USB에서
        네트워크 드라이브로 갈아타면 앱을 다시 만들 수밖에 없었다.

        옛 경로로 쌓아 둔 문서 기록은 지우지 않는다. 다음 분석에서 새 폴더를
        훑고, 옛 경로의 파일은 평소처럼 '원본 없음'으로 표시된다(ING-008).
        분석 결과와 손으로 고친 교정값을 폴더 하나 바꿨다고 잃게 할 수는 없다.

        이미 다른 자료원이 쓰는 경로면 False를 돌려준다 (path UNIQUE).
        """
        text = str(Path(path))
        taken = self.con.execute(
            "SELECT id FROM sources WHERE path = ? AND id != ?", (text, source_id)
        ).fetchone()
        if taken:
            return False
        old = self.con.execute(
            "SELECT path FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if old is None:
            return False
        if kind:
            self.con.execute(
                "UPDATE sources SET path = ?, kind = ? WHERE id = ?",
                (text, kind, source_id),
            )
        else:
            self.con.execute(
                "UPDATE sources SET path = ? WHERE id = ?", (text, source_id)
            )
        self.audit("source.change", text, detail=f"이전: {old['path']}")
        return True

    def remove_source(self, source_id: int) -> None:
        self.con.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.audit("source.remove", str(source_id))

    def sources(self) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM sources ORDER BY id").fetchall()

    def source_document_counts(self) -> dict[int, int]:
        """자료원별 문서 건수. 제거가 무엇을 지우는지 미리 보여주기 위한 것."""
        rows = self.con.execute(
            "SELECT source_id, COUNT(*) AS n FROM documents "
            "WHERE parse_status != 'skipped' GROUP BY source_id"
        ).fetchall()
        return {row["source_id"]: row["n"] for row in rows}

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

    def sections(self, doc_id: int) -> list[sqlite3.Row]:
        """문서 본문을 원래 순서대로. 근거 원문을 앱 안에서 보여줄 때 쓴다."""
        return self.con.execute(
            "SELECT kind, ordinal, locator, text FROM document_sections "
            "WHERE doc_id = ? ORDER BY ordinal",
            (doc_id,),
        ).fetchall()

    def section_counts(self, doc_ids: Sequence[int]) -> dict[int, int]:
        """문서별 대목 수. 버전 비교에서 '8문단 / 7문단'을 말하는 근거다."""
        ids = [int(i) for i in doc_ids]
        if not ids:
            return {}
        marks = ", ".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT doc_id, COUNT(*) AS n FROM document_sections "
            f"WHERE doc_id IN ({marks}) GROUP BY doc_id",
            ids,
        ).fetchall()
        return {row["doc_id"]: row["n"] for row in rows}

    def version_siblings(self, doc_id: int) -> list[sqlite3.Row]:
        """같은 것을 여러 번 저장한 것으로 보이는 파일들 (자신 포함).

        '이 파일이 최신본인가'는 이름으로 답할 수 없다 — `_최종`과
        `_진짜최종`이 나란히 있는 것이 현장이다. 그래서 **같은 업무의 같은
        단계**에 놓인 파일, 그리고 내용이 완전히 같은 사본을 후보로 모은다.
        판정은 화면이 아니라 파일 수정 시각이 한다.
        """
        return self.con.execute(
            """
            SELECT DISTINCT d.id, d.filename, d.path, d.ext, d.hash,
                   d.fs_mtime, d.eff_date, d.eff_precision, d.size
            FROM documents d
            WHERE d.missing_since IS NULL AND (
                  d.id = :doc
               OR (d.hash IS NOT NULL AND d.hash = (
                      SELECT hash FROM documents WHERE id = :doc))
               OR d.id IN (
                      SELECT s.doc_id FROM task_steps s
                      WHERE s.doc_id IS NOT NULL AND (s.task_id, s.label) IN (
                          SELECT x.task_id, x.label FROM task_steps x WHERE x.doc_id = :doc)))
            ORDER BY d.fs_mtime DESC, d.filename
            """,
            {"doc": doc_id},
        ).fetchall()

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
            WHERE t.not_a_task = 0 AND t.merged_into IS NULL
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
            "UPDATE tasks SET name = ?, status = 'edited', origin = 'user', "
            "review_state = 'confirmed', reviewed_at = datetime('now') WHERE id = ?",
            (name, task_id),
        )
        self.con.execute(
            "INSERT INTO corrections(target, target_id, field, before_val, after_val) "
            "VALUES ('tasks', ?, 'name', ?, ?)",
            (task_id, before["name"] if before else None, name),
        )
        self.audit("task.rename", str(task_id), f"{before['name'] if before else ''} → {name}")
        return True

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

        사람이 확정한 주기가 하나라도 있으면 아예 손대지 않는다. AI 제안만
        지우고 새로 넣으면 사람 것과 AI 것이 나란히 남아, 화면이 어느 쪽을
        고르느냐에 따라 사용자의 확정이 무시된 것처럼 보인다 (NFR-SAF-004).
        """
        if self.con.execute(
            "SELECT 1 FROM task_cycles WHERE task_id = ? AND decided_by = 'user'",
            (task_id,),
        ).fetchone():
            return
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
        """사람이 확정한 주기가 있으면 무조건 그것이다. 관찰 연도 수는 그다음."""
        return self.con.execute(
            "SELECT * FROM task_cycles WHERE task_id = ? "
            "ORDER BY (decided_by = 'user') DESC, years_observed DESC LIMIT 1",
            (task_id,),
        ).fetchone()

    def task_documents_in_month(
        self, task_id: int, month: int, limit: int = 3
    ) -> list[sqlite3.Row]:
        """이 업무가 그 달에 남긴 문서. 최근 연도부터.

        일정 화면의 핵심 재료다. "9월에 행정사무감사가 있습니다"는 달력도
        할 수 있는 말이지만, "작년 9월엔 이 공문으로 시작했습니다"는 전임자의
        자료를 읽은 시스템만 할 수 있다. 날짜를 업무기억으로 바꾸는 지점이다.

        파일 수정일로만 판정된 시점은 뺀다 — 복사만 해도 바뀌므로 When·How가
        이미 배제하는 기준이고, 여기서만 느슨하게 하면 근거가 헐거워진다.
        """
        return self.con.execute(
            f"""
            SELECT d.id, d.filename, d.path, d.eff_year, d.eff_month, d.eff_date
            FROM task_docs td
            JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.eff_month = ? AND d.missing_since IS NULL
              AND d.eff_year IS NOT NULL AND d.eff_date_kind != 'fs'
              AND {representative_predicate('d')}
            ORDER BY d.eff_year DESC, d.eff_date
            LIMIT ?
            """,
            (task_id, month, limit),
        ).fetchall()

    def task_step_for_month(self, task_id: int, month: int) -> sqlite3.Row | None:
        """그 달에 있었던 처리 단계 중 가장 최근 연도의 것. '뭐부터 했나'에 답한다."""
        return self.con.execute(
            "SELECT s.*, d.filename, d.path FROM task_steps s "
            "LEFT JOIN documents d ON d.id = s.doc_id "
            "WHERE s.task_id = ? AND s.month = ? "
            "ORDER BY s.year DESC, s.ordinal LIMIT 1",
            (task_id, month),
        ).fetchone()

    def all_cycles(self) -> list[sqlite3.Row]:
        """일정 화면의 재료. '업무 아님'과 합쳐진 업무는 빠진다.

        kind='none'(사람이 '반복 아님'으로 확정)도 뺀다 — 그것은 일정이
        아니라 '일정 없음'이라는 판정이라 일정 화면에 올릴 것이 없다.
        """
        return self.con.execute(
            "SELECT c.*, t.name AS task_name, t.id AS task_id, "
            "       t.review_state AS task_review_state "
            "FROM task_cycles c JOIN tasks t ON t.id = c.task_id "
            "WHERE t.not_a_task = 0 AND t.merged_into IS NULL AND c.kind != 'none' "
            "ORDER BY c.years_observed DESC"
        ).fetchall()

    def task_grid_documents(self, task_id: int) -> list[sqlite3.Row]:
        """이 업무 문서의 연도·월·시점출처를 격자 계산용으로 돌려준다.

        완전 중복본은 하나만 남긴다 — 같은 문서가 여러 번 복사됐다고
        그 달의 반복 근거가 더 세지면 안 된다.
        """
        return self.con.execute(
            f"""
            SELECT d.id, d.eff_year AS year, d.eff_month AS month,
                   d.eff_date, d.eff_precision, d.eff_date_kind
            FROM task_docs td
            JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.missing_since IS NULL AND d.eff_year IS NOT NULL
              AND {representative_predicate('d')}
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
            f"""
            SELECT d.id, d.filename, d.eff_month AS month, d.eff_date,
                   d.eff_precision
            FROM task_docs td
            JOIN documents d ON d.id = td.doc_id
            WHERE td.task_id = ? AND d.eff_year = ? AND d.missing_since IS NULL
              AND d.eff_month IS NOT NULL AND d.eff_date_kind != 'fs'
              AND {representative_predicate('d')}
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

        사람이 이 연도를 한 번이라도 고쳤으면 연도 전체를 건드리지 않는다.
        순서는 단계 하나가 아니라 줄 전체가 의미이기 때문이다 (NFR-SAF-004).
        """
        if self.con.execute(
            "SELECT 1 FROM task_steps WHERE task_id = ? AND year = ? "
            "AND decided_by = 'user'",
            (task_id, year),
        ).fetchone():
            return
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

    def documents_needing_chunks(self, model: str) -> list[sqlite3.Row]:
        """조각이 아직 없거나, 조각은 있는데 임베딩이 빠진 문서.

        '조각이 없는 문서'만 골라서는 안 된다. 임베딩이 배치 중간에 실패하면
        조각은 남고 임베딩만 비는데, 그 문서는 다음부터 '조각이 있는 문서'로
        분류돼 영원히 다시 시도되지 않는다. 실제로 그렇게 최신 행정사무감사
        문서 하나가 질문 화면에서 통째로 빠져 있었다.

        모델을 함께 보는 이유: 임베딩 모델을 바꾸면 옛 벡터는 검색에서
        걸러지므로(search/rag.py가 model로 필터한다) 없는 것과 같다.
        """
        return self.con.execute(
            """
            SELECT d.id, d.filename FROM documents d
            WHERE d.missing_since IS NULL AND d.parse_status IN ('ok', 'partial')
              AND (NOT EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.id)
                   OR EXISTS (SELECT 1 FROM chunks c
                              LEFT JOIN embeddings e
                                     ON e.chunk_id = c.id AND e.model = ?
                              WHERE c.doc_id = d.id AND e.chunk_id IS NULL))
            ORDER BY d.id
            """,
            (model,),
        ).fetchall()

    def chunks_needing_embedding(self, doc_id: int, model: str) -> list[tuple[int, str]]:
        rows = self.con.execute(
            "SELECT c.id, c.text FROM chunks c "
            "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ? "
            "WHERE c.doc_id = ? AND e.chunk_id IS NULL ORDER BY c.ordinal",
            (model, doc_id),
        ).fetchall()
        return [(row["id"], row["text"]) for row in rows]

    def unembedded_chunk_count(self, model: str | None = None) -> int:
        """질문 화면이 아직 못 보는 조각 수. 0이어야 자료 전체를 근거로 쓴다."""
        if model:
            return self.con.execute(
                "SELECT COUNT(*) AS n FROM chunks c "
                "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ? "
                "WHERE e.chunk_id IS NULL",
                (model,),
            ).fetchone()["n"]
        return self.con.execute(
            "SELECT COUNT(*) AS n FROM chunks c "
            "LEFT JOIN embeddings e ON e.chunk_id = c.id WHERE e.chunk_id IS NULL"
        ).fetchone()["n"]

    def documents_missing_from_search(self, model: str | None = None) -> list[sqlite3.Row]:
        """조각이 하나도 검색되지 않는 문서. 사용자에게 이름을 보여 줘야 한다."""
        join = "e.chunk_id = c.id" + (" AND e.model = ?" if model else "")
        params = (model,) if model else ()
        return self.con.execute(
            f"SELECT d.id, d.filename, COUNT(c.id) AS chunks "
            f"FROM documents d JOIN chunks c ON c.doc_id = d.id "
            f"LEFT JOIN embeddings e ON {join} "
            f"WHERE d.missing_since IS NULL "
            f"GROUP BY d.id HAVING SUM(e.chunk_id IS NOT NULL) = 0",
            params,
        ).fetchall()

    def save_chunk_embedding(self, chunk_id: int, model: str, dim: int, vector: bytes) -> None:
        self.con.execute(
            "INSERT INTO embeddings(chunk_id, model, dim, vector) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET "
            "model = excluded.model, dim = excluded.dim, vector = excluded.vector",
            (chunk_id, model, dim, vector),
        )

    def save_question(
        self, question: str, answer: str, citations: str, withheld: bool, model: str,
        filters: str | None = None,
    ) -> int:
        cur = self.con.execute(
            "INSERT INTO questions(question, answer, citations, withheld, model, filters) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (question, answer, citations, int(withheld), model, filters),
        )
        return cur.lastrowid

    def rate_question(self, question_id: int, rating: str) -> None:
        self.con.execute(
            "UPDATE questions SET rating = ? WHERE id = ?", (rating, question_id)
        )

    def document_context(self, doc_ids: Sequence[int]) -> dict[int, dict]:
        """문서마다 '이게 무슨 업무의 어느 단계이고, 최신본인가'를 한 번에 모은다.

        질문 화면의 근거 목록이 파일명만 늘어놓으면, 사용자는 그 파일이
        어느 업무의 것인지·지금 열어도 되는 최신본인지 알 수 없다. 좌측
        메뉴가 '이 파일이 최신본인가'를 약속하는데 답변 화면이 그 약속을
        지키지 않는 것이 실제로 지적된 문제다.

        판정은 **아는 것만** 한다. 같은 내용(hash 동일) 사본이 여럿이면 파일
        수정일이 가장 늦은 것을 최신으로 보고, 그 업무의 최신 연도보다 오래된
        자료면 지난 연도로 표시한다. 내용이 '비슷한' 문서를 최신본으로 고르는
        일은 하지 않는다 — 그건 추측이다.
        """
        ids = [int(i) for i in doc_ids]
        if not ids:
            return {}
        marks = ", ".join("?" * len(ids))
        rows = self.con.execute(
            f"""
            SELECT d.id, d.filename, d.path, d.hash, d.fs_mtime,
                   d.eff_date, d.eff_precision, d.eff_date_kind, d.eff_year,
                   (SELECT t.id FROM task_docs td JOIN tasks t ON t.id = td.task_id
                     WHERE td.doc_id = d.id AND t.not_a_task = 0 AND t.merged_into IS NULL
                     ORDER BY td.is_primary DESC, t.name LIMIT 1)      AS task_id,
                   (SELECT t.name FROM task_docs td JOIN tasks t ON t.id = td.task_id
                     WHERE td.doc_id = d.id AND t.not_a_task = 0 AND t.merged_into IS NULL
                     ORDER BY td.is_primary DESC, t.name LIMIT 1)      AS task_name,
                   (SELECT s.label FROM task_steps s WHERE s.doc_id = d.id
                     ORDER BY s.year DESC, s.ordinal LIMIT 1)          AS step_label,
                   (SELECT COUNT(*) FROM documents x
                     WHERE x.hash = d.hash AND d.hash IS NOT NULL
                       AND x.missing_since IS NULL)                    AS copies,
                   (SELECT MAX(x.fs_mtime) FROM documents x
                     WHERE x.hash = d.hash AND d.hash IS NOT NULL
                       AND x.missing_since IS NULL)                    AS newest_copy_at
            FROM documents d
            WHERE d.id IN ({marks})
            """,
            ids,
        ).fetchall()

        out: dict[int, dict] = {}
        latest_year: dict[int, int | None] = {}
        for row in rows:
            item = dict(row)
            task_id = item["task_id"]
            if task_id is not None and task_id not in latest_year:
                latest_year[task_id] = self.con.execute(
                    "SELECT MAX(d.eff_year) AS y FROM task_docs td "
                    "JOIN documents d ON d.id = td.doc_id "
                    "WHERE td.task_id = ? AND d.missing_since IS NULL",
                    (task_id,),
                ).fetchone()["y"]
            item["task_latest_year"] = latest_year.get(task_id)
            out[item["id"]] = item
        return out

    # ── 인수인계 진행도 (계획서 §18) ────────────────────────────────
    # 진행도는 새로 쌓는 자료가 아니라 **A-1 교정의 부산물**이다. 사용자가
    # 업무를 확인하고 주기를 확정하면 그 자체로 진행도가 오른다. 그래서
    # 확인 상태를 따로 적어 두지 않고 기존 표에서 센다 — 두 벌로 적으면
    # 반드시 어긋나고, 어긋난 진행도는 아예 없느니만 못하다.
    #
    # 예외가 하나 있다. '읽었다'는 어느 표에도 남지 않으므로 그것만
    # handover_checks에 적는다.
    _LIVE_TASK = "t.not_a_task = 0 AND t.merged_into IS NULL"
    _TASK_DONE = "(t.review_state = 'confirmed' OR t.status IN ('approved','edited'))"

    def handover_counts(self) -> dict[str, int]:
        """네 축의 (확인함, 전체). 계획서 §18의 네 줄이 여기서 나온다."""
        one = self.con.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM tasks t WHERE {self._LIVE_TASK}) AS tasks_total,
              (SELECT COUNT(*) FROM tasks t
                WHERE {self._LIVE_TASK} AND {self._TASK_DONE})       AS tasks_done,
              (SELECT COUNT(*) FROM task_reading r JOIN tasks t ON t.id = r.task_id
                WHERE {self._LIVE_TASK})                             AS reading_total,
              (SELECT COUNT(*) FROM task_reading r JOIN tasks t ON t.id = r.task_id
                JOIN handover_checks h
                  ON h.kind = 'reading' AND h.target_id = r.doc_id AND h.state = 'done'
                WHERE {self._LIVE_TASK})                             AS reading_done,
              (SELECT COUNT(*) FROM task_cycles c JOIN tasks t ON t.id = c.task_id
                WHERE {self._LIVE_TASK})                             AS cycles_total,
              (SELECT COUNT(*) FROM task_cycles c JOIN tasks t ON t.id = c.task_id
                WHERE {self._LIVE_TASK} AND c.decided_by = 'user')   AS cycles_done,
              (SELECT COUNT(DISTINCT s.task_id) FROM task_steps s JOIN tasks t ON t.id = s.task_id
                WHERE {self._LIVE_TASK})                             AS steps_total,
              (SELECT COUNT(DISTINCT s.task_id) FROM task_steps s JOIN tasks t ON t.id = s.task_id
                WHERE {self._LIVE_TASK} AND s.decided_by = 'user')   AS steps_done
            """
        ).fetchone()
        return {key: one[key] or 0 for key in one.keys()}

    def reading_marks(self) -> set[int]:
        """읽음으로 표시한 문서 id."""
        rows = self.con.execute(
            "SELECT target_id FROM handover_checks WHERE kind = 'reading' AND state = 'done'"
        ).fetchall()
        return {row["target_id"] for row in rows}

    def mark_reading(self, doc_id: int, done: bool = True) -> None:
        """'이 문서 읽었다' 표시. 되돌릴 수 있어야 하므로 지우지 않고 상태로 둔다."""
        self.con.execute(
            "INSERT INTO handover_checks(kind, target_id, state, updated_at) "
            "VALUES ('reading', ?, ?, datetime('now')) "
            "ON CONFLICT(kind, target_id) DO UPDATE SET "
            "state = excluded.state, updated_at = excluded.updated_at",
            (doc_id, "done" if done else "pending"),
        )
        self.audit("reading.mark", str(doc_id), "done" if done else "pending")

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

    # ── 교정 (계획서 §11) ───────────────────────────────────────────
    # 모든 교정은 네 가지를 함께 한다. 하나라도 빠지면 계약 위반이다.
    #   ① 값을 바꾼다
    #   ② 이제 사람이 정한 값임을 표시한다 (user / edited / confirmed)
    #   ③ corrections에 무엇이 무엇으로 바뀌었는지 남긴다
    #   ④ audit에 식별자만 남긴다 (본문은 절대 남기지 않는다, SEC-005)
    # ②가 없으면 다음 재분석이 덮어쓰고, ③이 없으면 되돌릴 수 없다.

    def _record(self, target: str, target_id: int | None, field: str,
                before: Any, after: Any, doc_id: int | None = None,
                scope: str = "single") -> None:
        self.con.execute(
            "INSERT INTO corrections(target, target_id, doc_id, field, "
            "before_val, after_val, scope) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (target, target_id, doc_id,  field,
             None if before is None else str(before),
             None if after is None else str(after), scope),
        )

    # ── 업무 교정 ───────────────────────────────────────────────────
    def edit_task_description(self, task_id: int, description: str) -> None:
        before = self.con.execute(
            "SELECT description FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.con.execute(
            "UPDATE tasks SET description = ?, status = 'edited', origin = 'user', "
            "review_state = 'confirmed', reviewed_at = datetime('now') WHERE id = ?",
            (description, task_id),
        )
        self._record("tasks", task_id, "description",
                     before["description"] if before else None, description)
        self.audit("task.describe", str(task_id))

    def confirm_task(self, task_id: int) -> None:
        """'이 업무 확인함'. 4단계 상태와 옛 status를 함께 올린다."""
        self.con.execute(
            "UPDATE tasks SET status = 'approved', review_state = 'confirmed', "
            "reviewed_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
        self._record("tasks", task_id, "review_state", None, "confirmed")
        self.audit("task.confirm", str(task_id))

    def mark_not_a_task(self, task_id: int, flag: bool = True) -> None:
        """'업무 아님' 처리. 지우지 않는다.

        지워 버리면 다음 재분석이 같은 묶음을 다시 만들어 사용자에게 또
        묻는다. 판정을 남겨야 그 질문을 한 번으로 끝낼 수 있다.
        """
        self.con.execute(
            "UPDATE tasks SET not_a_task = ?, review_state = 'confirmed', "
            "reviewed_at = datetime('now'), origin = 'user' WHERE id = ?",
            (int(flag), task_id),
        )
        self._record("tasks", task_id, "not_a_task", int(not flag), int(flag))
        self.audit("task.not_a_task" if flag else "task.restore", str(task_id))

    def merge_tasks(self, source_id: int, target_id: int) -> bool:
        """source를 target에 합친다. source는 지우지 않고 흔적으로 남긴다.

        주기·처리순서는 옮기지 않는다. 두 업무의 격자가 합쳐지면 다시
        계산해야 맞는 값이 나오는데, 그 계산을 여기서 몰래 하면 사용자가
        모르는 사이에 근거가 바뀐다. 재분석에 맡기고 표시만 지운다.
        """
        if source_id == target_id:
            return False
        if self.task(source_id) is None or self.task(target_id) is None:
            return False

        # 이미 target에 있는 문서는 건너뛴다 (복합 PK 충돌 방지).
        self.con.execute(
            "INSERT OR IGNORE INTO task_docs(task_id, doc_id, origin, confidence, evidence) "
            "SELECT ?, doc_id, 'user', confidence, evidence FROM task_docs WHERE task_id = ?",
            (target_id, source_id),
        )
        self.con.execute("DELETE FROM task_docs WHERE task_id = ?", (source_id,))
        self.con.execute("DELETE FROM task_reading WHERE task_id = ?", (source_id,))
        # 합쳐진 묶음의 옛 주기·순서는 더 이상 근거가 없다.
        self.con.execute("DELETE FROM task_cycles WHERE task_id = ?", (source_id,))
        self.con.execute("DELETE FROM task_steps WHERE task_id = ?", (source_id,))

        source = self.task(source_id)
        self.con.execute(
            "UPDATE tasks SET merged_into = ?, review_state = 'confirmed', "
            "reviewed_at = datetime('now'), origin = 'user' WHERE id = ?",
            (target_id, source_id),
        )
        self.con.execute(
            "UPDATE tasks SET status = 'edited', origin = 'user', "
            "review_state = 'confirmed', reviewed_at = datetime('now') WHERE id = ?",
            (target_id,),
        )
        self._record("tasks", source_id, "merged_into", None, target_id)
        self.audit("task.merge", f"{source_id}→{target_id}", source["name"])
        return True

    def split_task(self, task_id: int, doc_ids: Sequence[int], new_name: str) -> int | None:
        """고른 문서를 떼어 새 업무로 만든다. 새 업무 id를 돌려준다."""
        new_name = new_name.strip()
        if not new_name or not doc_ids:
            return None
        if self.con.execute(
            "SELECT id FROM tasks WHERE name = ?", (new_name,)
        ).fetchone():
            return None

        cur = self.con.execute(
            "INSERT INTO tasks(name, origin, status, confidence, review_state, "
            "reviewed_at) VALUES (?, 'user', 'edited', 'high', 'confirmed', "
            "datetime('now'))",
            (new_name,),
        )
        new_id = cur.lastrowid
        marks = ", ".join("?" * len(doc_ids))
        self.con.execute(
            f"INSERT OR IGNORE INTO task_docs(task_id, doc_id, origin, confidence) "
            f"SELECT ?, doc_id, 'user', confidence FROM task_docs "
            f"WHERE task_id = ? AND doc_id IN ({marks})",
            (new_id, task_id, *doc_ids),
        )
        self.con.execute(
            f"DELETE FROM task_docs WHERE task_id = ? AND doc_id IN ({marks})",
            (task_id, *doc_ids),
        )
        self.con.execute(
            f"DELETE FROM task_reading WHERE task_id = ? AND doc_id IN ({marks})",
            (task_id, *doc_ids),
        )
        self._record("tasks", task_id, "split", task_id, new_id)
        self.audit("task.split", f"{task_id}→{new_id}", f"{len(doc_ids)}건")
        return new_id

    # ── 문서 배정 교정 ──────────────────────────────────────────────
    def assign_document(self, task_id: int, doc_id: int) -> None:
        """문서를 업무에 붙인다. 한 문서가 여러 업무에 속할 수 있다."""
        self.con.execute(
            "INSERT OR IGNORE INTO task_docs(task_id, doc_id, origin, confidence) "
            "VALUES (?, ?, 'user', 'high')",
            (task_id, doc_id),
        )
        self.con.execute(
            "UPDATE task_docs SET origin = 'user' WHERE task_id = ? AND doc_id = ?",
            (task_id, doc_id),
        )
        self._record("task_docs", task_id, "task", None, task_id, doc_id=doc_id)
        self.audit("document.assign", f"{task_id}/{doc_id}")

    def move_document(self, from_task: int, to_task: int, doc_id: int) -> None:
        self.assign_document(to_task, doc_id)
        self.detach_document(from_task, doc_id)
        self.audit("document.move", f"{from_task}→{to_task}/{doc_id}")

    def set_primary_document(self, task_id: int, doc_id: int) -> None:
        """대표 문서는 업무당 하나다."""
        self.con.execute(
            "UPDATE task_docs SET is_primary = 0 WHERE task_id = ?", (task_id,)
        )
        self.con.execute(
            "UPDATE task_docs SET is_primary = 1, origin = 'user' "
            "WHERE task_id = ? AND doc_id = ?",
            (task_id, doc_id),
        )
        self._record("task_docs", task_id, "is_primary", None, 1, doc_id=doc_id)
        self.audit("document.primary", f"{task_id}/{doc_id}")

    def primary_document(self, task_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT d.* FROM task_docs td JOIN documents d ON d.id = td.doc_id "
            "WHERE td.task_id = ? AND td.is_primary = 1 LIMIT 1",
            (task_id,),
        ).fetchone()

    def unclassified_documents(self, limit: int = 200) -> list[sqlite3.Row]:
        """어느 업무에도 속하지 않은 문서. 미분류가 남는 것은 정상이다."""
        return self.con.execute(
            "SELECT d.* FROM documents d "
            "WHERE d.missing_since IS NULL AND d.parse_status IN ('ok','partial') "
            "  AND NOT EXISTS(SELECT 1 FROM task_docs td WHERE td.doc_id = d.id) "
            "ORDER BY d.eff_date DESC NULLS LAST, d.filename LIMIT ?",
            (limit,),
        ).fetchall()

    def document_tasks(self, doc_id: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT t.id, t.name, td.origin, td.is_primary FROM task_docs td "
            "JOIN tasks t ON t.id = td.task_id "
            "WHERE td.doc_id = ? AND t.not_a_task = 0 AND t.merged_into IS NULL "
            "ORDER BY t.name",
            (doc_id,),
        ).fetchall()

    # ── 시점 교정 ───────────────────────────────────────────────────
    def set_document_date(
        self, doc_id: int, value: str | None, precision: str = "day",
        kind: str = "user",
    ) -> None:
        """사람이 고른 시점. value가 None이면 '날짜 모름'으로 확정한다.

        '모름'을 확정하는 것도 하나의 판단이다. 그래서 eff_date를 비우되
        date_decided_by는 user로 남긴다 — 그러지 않으면 다음 재분석이
        빈칸을 보고 다시 추정해 사용자의 판단을 지운다.
        """
        before = self.document(doc_id)
        year = month = None
        if value:
            parts = value.split("-")
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 and precision != "year" else None

        self.update_document(
            doc_id,
            eff_date=value,
            eff_date_kind=kind if value else None,
            eff_precision=precision if value else None,
            eff_year=year,
            eff_month=month,
            date_decided_by="user",
        )
        self._record("documents", doc_id, "eff_date",
                     before["eff_date"] if before else None, value, doc_id=doc_id)
        self.audit("document.date", str(doc_id), value or "모름")

    # ── 주기 교정 (When) ────────────────────────────────────────────
    def set_task_cycle(
        self, task_id: int, kind: str, months: str, day_hint: str | None = None,
    ) -> None:
        """사람이 확정한 주기. AI 제안을 지우고 그 자리에 놓는다."""
        before = self.task_cycle(task_id)
        self.con.execute("DELETE FROM task_cycles WHERE task_id = ?", (task_id,))
        self.con.execute(
            "INSERT INTO task_cycles(task_id, kind, months, day_hint, "
            "years_observed, confidence, decided_by, evidence) "
            "VALUES (?, ?, ?, ?, ?, 'high', 'user', ?)",
            (task_id, kind, months, day_hint,
             before["years_observed"] if before else 0,
             before["evidence"] if before else None),
        )
        self._record("task_cycles", task_id, "months",
                     before["months"] if before else None, months)
        self.audit("cycle.set", str(task_id), f"{kind}:{months}")

    def confirm_task_cycle(self, task_id: int) -> bool:
        """AI가 찾은 주기를 사람이 그대로 인정한다. 값은 그대로, 상태만 올린다."""
        cycle = self.task_cycle(task_id)
        if cycle is None:
            return False
        self.con.execute(
            "UPDATE task_cycles SET decided_by = 'user', confidence = 'high' WHERE id = ?",
            (cycle["id"],),
        )
        self._record("task_cycles", task_id, "decided_by", "ai", "user")
        self.audit("cycle.confirm", str(task_id))
        return True

    def mark_no_cycle(self, task_id: int) -> None:
        """'반복 아님'. 빈 주기를 사람 판정으로 남겨 재분석이 되살리지 못하게 한다."""
        self.con.execute("DELETE FROM task_cycles WHERE task_id = ?", (task_id,))
        self.con.execute(
            "INSERT INTO task_cycles(task_id, kind, months, years_observed, "
            "confidence, decided_by) VALUES (?, 'none', '', 0, 'high', 'user')",
            (task_id,),
        )
        self._record("task_cycles", task_id, "kind", None, "none")
        self.audit("cycle.none", str(task_id))

    # ── 처리 순서 교정 (How) ────────────────────────────────────────
    def _claim_steps(self, task_id: int, year: int) -> None:
        """이 연도의 단계를 통째로 사람 소유로 넘긴다.

        한 단계만 고쳐도 연도 전체를 넘기는 이유: 순서는 단계 하나가 아니라
        줄 전체가 의미다. 일부만 사람 것으로 두면 재분석이 나머지를 갈아
        끼워 사용자가 만든 순서가 뒤엉킨다.
        """
        self.con.execute(
            "UPDATE task_steps SET decided_by = 'user' WHERE task_id = ? AND year = ?",
            (task_id, year),
        )

    def edit_step_label(self, step_id: int, label: str) -> None:
        row = self.con.execute(
            "SELECT task_id, year, label FROM task_steps WHERE id = ?", (step_id,)
        ).fetchone()
        if row is None:
            return
        self._claim_steps(row["task_id"], row["year"])
        self.con.execute("UPDATE task_steps SET label = ? WHERE id = ?", (label, step_id))
        self._record("task_steps", step_id, "label", row["label"], label)
        self.audit("step.rename", str(step_id))

    def move_step(self, step_id: int, offset: int) -> None:
        """단계를 위(-1)나 아래(+1)로 옮긴다."""
        row = self.con.execute(
            "SELECT task_id, year, ordinal FROM task_steps WHERE id = ?", (step_id,)
        ).fetchone()
        if row is None:
            return
        neighbour = self.con.execute(
            "SELECT id, ordinal FROM task_steps WHERE task_id = ? AND year = ? "
            "AND ordinal = ?",
            (row["task_id"], row["year"], row["ordinal"] + offset),
        ).fetchone()
        if neighbour is None:
            return
        self._claim_steps(row["task_id"], row["year"])
        self.con.execute("UPDATE task_steps SET ordinal = ? WHERE id = ?",
                         (row["ordinal"], neighbour["id"]))
        self.con.execute("UPDATE task_steps SET ordinal = ? WHERE id = ?",
                         (neighbour["ordinal"], step_id))
        self._record("task_steps", step_id, "ordinal", row["ordinal"],
                     neighbour["ordinal"])
        self.audit("step.move", str(step_id), str(offset))

    def delete_step(self, step_id: int) -> None:
        row = self.con.execute(
            "SELECT task_id, year, ordinal, label FROM task_steps WHERE id = ?",
            (step_id,),
        ).fetchone()
        if row is None:
            return
        self._claim_steps(row["task_id"], row["year"])
        self.con.execute("DELETE FROM task_steps WHERE id = ?", (step_id,))
        self.con.execute(
            "UPDATE task_steps SET ordinal = ordinal - 1 "
            "WHERE task_id = ? AND year = ? AND ordinal > ?",
            (row["task_id"], row["year"], row["ordinal"]),
        )
        self._record("task_steps", step_id, "deleted", row["label"], None)
        self.audit("step.delete", str(step_id))

    def add_step(self, task_id: int, year: int, label: str, after_ordinal: int = 0,
                 doc_id: int | None = None) -> int:
        """빠진 단계를 사람이 끼워 넣는다.

        근거 문서가 없으면 is_inferred=1로 저장한다 — 화면에서 점선으로
        그려 "이건 자료가 아니라 사람이 채운 칸"임을 계속 드러낸다.
        """
        self._claim_steps(task_id, year)
        self.con.execute(
            "UPDATE task_steps SET ordinal = ordinal + 1 "
            "WHERE task_id = ? AND year = ? AND ordinal > ?",
            (task_id, year, after_ordinal),
        )
        cur = self.con.execute(
            "INSERT INTO task_steps(task_id, year, ordinal, label, doc_id, "
            "is_inferred, decided_by) VALUES (?, ?, ?, ?, ?, ?, 'user')",
            (task_id, year, after_ordinal + 1, label, doc_id, 0 if doc_id else 1),
        )
        self._record("task_steps", cur.lastrowid, "added", None, label)
        self.audit("step.add", f"{task_id}/{year}")
        return cur.lastrowid

    def set_step_document(self, step_id: int, doc_id: int | None) -> None:
        row = self.con.execute(
            "SELECT task_id, year, doc_id FROM task_steps WHERE id = ?", (step_id,)
        ).fetchone()
        if row is None:
            return
        self._claim_steps(row["task_id"], row["year"])
        self.con.execute(
            "UPDATE task_steps SET doc_id = ?, is_inferred = ? WHERE id = ?",
            (doc_id, 0 if doc_id else 1, step_id),
        )
        self._record("task_steps", step_id, "doc_id", row["doc_id"], doc_id)
        self.audit("step.evidence", str(step_id))

    def corrections(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM corrections ORDER BY id DESC LIMIT ?", (limit,)
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
