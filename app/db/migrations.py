"""스키마 마이그레이션.

프로젝트 하나가 DB 파일 하나다. 그 안에 몇 시간~며칠짜리 분석 결과가 들어
있고, 사용자가 손으로 고친 교정값이 쌓인다. 스키마를 올릴 때 이것을 잃으면
제품 신뢰가 한 번에 무너진다.

원칙
  1. 올리기 전에 항상 백업한다. (project.db.bak-v1)
  2. 각 단계는 트랜잭션이다. 실패하면 통째로 되돌리고 옛 버전을 유지한다.
  3. 앱이 DB보다 낮은 버전이면 열지 않는다 — 내려쓰기는 지원하지 않는다.
  4. 마이그레이션 결과는 새로 설치한 스키마와 같아야 한다 (tests에서 강제).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

# 이 앱이 쓰는 스키마 버전. schema.sql과 함께 올린다.
SCHEMA_VERSION = "2"


def column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))


def add_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """이미 있으면 조용히 넘긴다. CREATE TABLE IF NOT EXISTS는 열을 붙이지 않는다."""
    if not column_exists(con, table, column):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


# ── v1 → v2 ─────────────────────────────────────────────────────────
# 교정 UX(계획서 §11)와 인수인계 진행도(§18), 업무기억 건강도(§19)의 자리를
# 만든다. 기존 표는 지우지 않고 열만 늘린다.

def _to_v2(con: sqlite3.Connection) -> None:
    # 업무 — 확인 상태, '업무 아님', 합치기 흔적
    add_column(con, "tasks", "review_state",
               "review_state TEXT NOT NULL DEFAULT 'inferred'")
    add_column(con, "tasks", "reviewed_at", "reviewed_at TEXT")
    add_column(con, "tasks", "not_a_task", "not_a_task INTEGER NOT NULL DEFAULT 0")
    add_column(con, "tasks", "merged_into",
               "merged_into INTEGER REFERENCES tasks(id)")

    # 대표 문서 지정
    add_column(con, "task_docs", "is_primary",
               "is_primary INTEGER NOT NULL DEFAULT 0")

    # 시점 교정 — 후보(document_dates)는 그대로 두고 '누가 골랐는가'만 기록
    add_column(con, "documents", "date_decided_by",
               "date_decided_by TEXT NOT NULL DEFAULT 'ai'")

    for statement in _V2_TABLES:
        con.execute(statement)

    # 데이터 이관: 이미 사람이 확인·수정한 업무는 confirmed로 올린다.
    # 이걸 빠뜨리면 사용자가 어제 확인한 업무가 오늘 다시 '미확인'으로 보인다.
    con.execute(
        "UPDATE tasks SET review_state = 'confirmed' "
        "WHERE status IN ('approved', 'edited') OR origin = 'user'"
    )
    con.execute(
        "UPDATE tasks SET review_state = 'weak' "
        "WHERE review_state = 'inferred' AND confidence = 'low'"
    )


# 신규 설치(schema.sql)와 같은 결과를 내야 한다. 어긋나면
# tests/test_migrations.py가 잡는다.
#
# executescript()를 쓰지 않는 이유: sqlite3 모듈은 executescript 직전에
# 열려 있던 트랜잭션을 암묵적으로 COMMIT한다. 그러면 아래 upgrade()의
# BEGIN이 풀려 롤백 보장이 사라진다. 문장 단위로 실행한다.
_V2_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS handover_checks (
        id         INTEGER PRIMARY KEY,
        kind       TEXT NOT NULL,
        target_id  INTEGER,
        state      TEXT NOT NULL DEFAULT 'pending',
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (kind, target_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_health (
        task_id    INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        score      INTEGER NOT NULL DEFAULT 0,
        findings   TEXT,
        checked_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
)


# 버전 문자열 → 그 버전으로 올리는 함수.
STEPS: dict[str, Callable[[sqlite3.Connection], None]] = {
    "2": _to_v2,
}


class MigrationError(RuntimeError):
    """마이그레이션 실패. 메시지는 사용자에게 그대로 보여도 되는 한글이다."""


def upgrade(con: sqlite3.Connection, path: Path, current: str) -> str:
    """current에서 SCHEMA_VERSION까지 순서대로 올린다. 최종 버전을 돌려준다."""
    if current == SCHEMA_VERSION:
        return current

    try:
        start = int(current)
        target = int(SCHEMA_VERSION)
    except ValueError as exc:
        raise MigrationError(f"알 수 없는 스키마 버전입니다: {current}") from exc

    if start > target:
        raise MigrationError(
            f"이 프로젝트는 더 새로운 버전(v{current})에서 만들어졌습니다. "
            f"현재 앱은 v{SCHEMA_VERSION}까지 지원합니다. 앱을 최신으로 올리세요."
        )

    backup(con, path, current)

    version = current
    for step in range(start + 1, target + 1):
        name = str(step)
        migrate = STEPS.get(name)
        if migrate is None:
            raise MigrationError(f"v{name}으로 올리는 방법이 없습니다.")
        con.execute("BEGIN")
        try:
            migrate(con)
            con.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (name,),
            )
            con.execute("COMMIT")
        except Exception as exc:
            con.execute("ROLLBACK")
            raise MigrationError(
                f"프로젝트를 v{name} 형식으로 올리지 못했습니다: {exc}\n"
                f"원래 상태로 되돌렸습니다. 백업: {backup_path(path, current).name}"
            ) from exc
        version = name

    return version


def backup_path(path: Path, version: str) -> Path:
    return path.with_name(f"{path.name}.bak-v{version}")


def backup(con: sqlite3.Connection, path: Path, version: str) -> Path:
    """SQLite 백업 API로 복사한다.

    파일을 그냥 copy하면 WAL 모드에서 아직 본체에 반영되지 않은 내용이
    빠진다. backup()은 열려 있는 연결에서도 일관된 사본을 만든다.
    """
    target = backup_path(path, version)
    with sqlite3.connect(target) as sink:
        con.backup(sink)
    return target
