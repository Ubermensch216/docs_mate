"""스키마 마이그레이션 시험.

프로젝트 DB에는 며칠짜리 분석 결과와 사람이 손으로 고친 교정값이 들어 있다.
버전을 올리다 이걸 잃으면 제품 신뢰가 한 번에 무너지므로, 여기서는
'데이터가 살아남는가'를 실제 v1 DB를 만들어 확인한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import Database
from app.db.migrations import SCHEMA_VERSION, MigrationError, backup_path, upgrade

# v1 시절의 스키마 조각. 그때 실제로 있던 열만 쓴다.
V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'local', excludes TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- meta_created/meta_modified는 일부러 뺐다. 그 두 열은 v1 안에서 나중에
-- 붙은 것이라 실제로 없는 DB가 있고, _add_missing_columns 경로도 함께 시험된다.
CREATE TABLE documents (
    id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
    fs_mtime TEXT, fs_ctime TEXT, hash TEXT, author TEXT, doc_title TEXT,
    eff_date TEXT, eff_date_kind TEXT, eff_precision TEXT,
    eff_year INTEGER, eff_month INTEGER,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parse_error TEXT, parse_note TEXT, parser TEXT, parser_version TEXT,
    char_count INTEGER NOT NULL DEFAULT 0,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    missing_since TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT,
    origin TEXT NOT NULL DEFAULT 'ai', status TEXT NOT NULL DEFAULT 'proposed',
    confidence TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE task_docs (
    task_id INTEGER NOT NULL, doc_id INTEGER NOT NULL,
    origin TEXT NOT NULL DEFAULT 'ai', confidence TEXT NOT NULL DEFAULT 'medium',
    evidence TEXT, PRIMARY KEY (task_id, doc_id)
);
CREATE TABLE task_cycles (
    id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, kind TEXT NOT NULL,
    months TEXT NOT NULL, day_hint TEXT, years_observed INTEGER NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL DEFAULT 'low', decided_by TEXT NOT NULL DEFAULT 'ai',
    evidence TEXT
);
-- v3(chunk_fts)이 참조하므로 v1 시절부터 있던 chunks도 흉내 낸다.
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE embeddings (
    chunk_id INTEGER PRIMARY KEY, model TEXT NOT NULL,
    dim INTEGER NOT NULL, vector BLOB NOT NULL
);
INSERT INTO meta(key, value) VALUES ('schema_version', '1');
"""


def make_v1(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(V1)
    con.execute(
        "INSERT INTO sources(id, path) VALUES (1, '/자료')",
    )
    con.execute(
        "INSERT INTO documents(id, source_id, path, filename, ext, eff_date, eff_year) "
        "VALUES (1, 1, '/자료/행감.hwp', '행감.hwp', '.hwp', '2025-09-01', 2025)"
    )
    # 사람이 이미 확인한 업무 하나, AI가 낮은 확신으로 만든 업무 하나.
    con.execute(
        "INSERT INTO tasks(id, name, status, confidence, origin) "
        "VALUES (1, '행정사무감사', 'approved', 'high', 'ai')"
    )
    con.execute(
        "INSERT INTO tasks(id, name, status, confidence, origin) "
        "VALUES (2, '계약관리', 'proposed', 'low', 'ai')"
    )
    con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")
    con.execute(
        "INSERT INTO task_cycles(task_id, kind, months, years_observed, "
        "confidence, decided_by) VALUES (1, 'yearly', '9,10,11', 4, 'high', 'user')"
    )
    con.commit()
    con.close()


def open_v1(tmp_path: Path) -> Database:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "project.db"
    make_v1(path)
    db = Database(path)
    db.init()
    return db


# ── 승격 ────────────────────────────────────────────────────────────

def test_v1_project_opens_and_reports_v2(tmp_path: Path):
    db = open_v1(tmp_path)
    assert db.get_meta("schema_version") == SCHEMA_VERSION
    db.close()


def test_analysis_results_survive_the_upgrade(tmp_path: Path):
    """며칠짜리 분석 결과를 잃지 않는다 — 이 시험이 이 파일의 존재 이유다."""
    db = open_v1(tmp_path)

    assert db.con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert db.con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    assert db.con.execute("SELECT COUNT(*) FROM task_docs").fetchone()[0] == 1

    cycle = db.con.execute("SELECT * FROM task_cycles").fetchone()
    assert cycle["months"] == "9,10,11"
    assert cycle["decided_by"] == "user"      # 사람이 확정한 주기가 그대로다
    db.close()


def test_confirmed_tasks_stay_confirmed(tmp_path: Path):
    """어제 확인한 업무가 오늘 '미확인'으로 되돌아가면 안 된다."""
    db = open_v1(tmp_path)
    states = dict(db.con.execute("SELECT name, review_state FROM tasks").fetchall())
    assert states["행정사무감사"] == "confirmed"   # status='approved'였다
    assert states["계약관리"] == "weak"            # confidence='low'였다
    db.close()


def test_new_columns_and_tables_exist(tmp_path: Path):
    db = open_v1(tmp_path)
    cols = {r[1] for r in db.con.execute("PRAGMA table_info(tasks)")}
    assert {"review_state", "reviewed_at", "not_a_task", "merged_into"} <= cols
    assert "is_primary" in {r[1] for r in db.con.execute("PRAGMA table_info(task_docs)")}
    assert "date_decided_by" in {
        r[1] for r in db.con.execute("PRAGMA table_info(documents)")
    }

    tables = {
        r[0] for r in db.con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"handover_checks", "task_health"} <= tables
    db.close()


def test_backup_is_written_before_upgrading(tmp_path: Path):
    db = open_v1(tmp_path)
    backup = backup_path(tmp_path / "project.db", "1")
    assert backup.exists()

    # 백업은 승격 전 상태 그대로여야 한다.
    con = sqlite3.connect(backup)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    assert not any(r[1] == "review_state" for r in con.execute("PRAGMA table_info(tasks)"))
    con.close()
    db.close()


def test_upgrade_is_idempotent(tmp_path: Path):
    """앱을 여러 번 열어도 같은 결과여야 한다."""
    db = open_v1(tmp_path)
    db.close()

    again = Database(tmp_path / "project.db")
    again.init()
    assert again.get_meta("schema_version") == SCHEMA_VERSION
    assert again.con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    again.close()


# ── 신규 설치와 승격 결과가 같은가 ──────────────────────────────────

def _schema_of(db: Database) -> dict[str, set[str]]:
    tables = [
        r[0] for r in db.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'document_fts%' "
            "AND name NOT LIKE 'chunk_fts%'"
        )
    ]
    return {t: {r[1] for r in db.con.execute(f"PRAGMA table_info({t})")} for t in tables}


def test_migrated_schema_matches_a_fresh_install(tmp_path: Path):
    """가장 흔한 사고 지점. schema.sql만 고치고 마이그레이션을 빠뜨리는 실수."""
    migrated = open_v1(tmp_path / "old")
    fresh = Database(tmp_path / "new" / "project.db")
    fresh.init()

    old_schema, new_schema = _schema_of(migrated), _schema_of(fresh)
    assert set(old_schema) == set(new_schema)
    for table in new_schema:
        assert old_schema[table] == new_schema[table], f"{table} 열이 다르다"

    migrated.close()
    fresh.close()


# ── 거부해야 하는 경우 ──────────────────────────────────────────────

def test_newer_database_is_refused(tmp_path: Path):
    """앱보다 새로운 DB를 억지로 열면 데이터를 망친다."""
    path = tmp_path / "project.db"
    make_v1(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    con.commit()

    with pytest.raises(MigrationError, match="더 새로운 버전"):
        upgrade(con, path, "99")
    con.close()


def test_failed_step_rolls_back_and_keeps_the_old_version(tmp_path: Path, monkeypatch):
    """실패하면 반쯤 올라간 DB를 남기지 않는다."""
    from app.db import migrations

    def explode(_con):
        raise sqlite3.OperationalError("일부러 낸 오류")

    monkeypatch.setitem(migrations.STEPS, "2", explode)

    path = tmp_path / "project.db"
    make_v1(path)
    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row

    with pytest.raises(MigrationError):
        upgrade(con, path, "1")

    version = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert version == "1"
    assert not any(r[1] == "review_state" for r in con.execute("PRAGMA table_info(tasks)"))
    con.close()
