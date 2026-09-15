"""업무 발견 — What의 본체.

"당신이 인수받은 업무는 7개로 추정됩니다"를 만드는 곳이다.

순서
  1. 문서 벡터를 묶는다 (규칙·수치 계산, 모델 없음)
  2. 묶음마다 한 번씩만 모델에게 이름을 묻는다
  3. 이름을 못 얻으면 파일명 공통 낱말로 임시 이름을 만든다
  4. 묶음마다 먼저 읽을 문서를 규칙으로 고른다

모델을 못 써도 1·3·4는 동작한다. 업무 목록이 아예 안 나오는 상황을 만들지 않는다.

발견 결과는 **제안**이다. 사용자가 승인해야 공식 업무가 된다(KNW-001).
사용자가 고친 업무와 직접 배정한 문서는 재발견이 덮어쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..core import scoring
from ..core.clustering import Cluster, cluster, common_tokens
from ..db import Database
from ..search.vector import VectorStore
from .analyze import name_task
from .client import OllamaClient

MAX_TITLES_FOR_NAMING = 30


@dataclass(slots=True)
class Discovery:
    clusters: int = 0
    tasks: int = 0
    assigned: int = 0
    unassigned: int = 0
    named_by_model: int = 0
    named_by_filename: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"업무 {self.tasks}개", f"배정 {self.assigned:,}건"]
        if self.unassigned:
            parts.append(f"미분류 {self.unassigned:,}건")
        if self.named_by_filename:
            parts.append(f"파일명으로 이름 지음 {self.named_by_filename}개")
        return " · ".join(parts)


def discover(
    db: Database,
    client: OllamaClient | None = None,
    threshold: float | None = None,
    min_size: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> Discovery:
    result = Discovery()

    store = VectorStore.load(db.con, model=client.embed_model if client else None)
    if store.size == 0:
        result.errors.append("의미 색인이 없습니다. 먼저 분석을 끝내세요.")
        return result

    kwargs = {}
    if threshold is not None:
        kwargs["threshold"] = threshold
    if min_size is not None:
        kwargs["min_size"] = min_size
    grouping = cluster(store, **kwargs)

    result.clusters = len(grouping.clusters)
    result.unassigned = len(grouping.unassigned)

    _clear_proposals(db)
    naming_ready = bool(client and client.health().generation_ready)

    for index, group in enumerate(grouping.clusters, start=1):
        if client is not None and getattr(client, "cancelled", False):
            break
        names = _filenames(db, group.doc_ids)
        tokens = common_tokens(names)

        named = _name(client if naming_ready else None, names, tokens)
        if named.error:
            result.errors.append(named.error)
        if named.source == "model":
            result.named_by_model += 1
        else:
            result.named_by_filename += 1

        task_id = _upsert_task(db, named, group)
        assigned = _attach(db, task_id, group.doc_ids)
        result.assigned += assigned
        _store_reading(db, task_id, group.doc_ids)

        if on_progress:
            on_progress(index, len(grouping.clusters), named.label)

    result.tasks = db.con.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
    db.audit("task.discover", detail=result.summary(), result="ok")
    return result


# ── 이름 짓기 ───────────────────────────────────────────────────────

@dataclass(slots=True)
class Named:
    label: str
    description: str | None = None
    source: str = "filename"     # model | filename
    error: str | None = None


def _name(client: OllamaClient | None, names: list[str], tokens: list[str]) -> Named:
    """모델이 없거나 실패하면 파일명 공통 낱말로 대신한다."""
    fallback = _from_tokens(tokens)
    if client is None:
        return Named(fallback)

    naming = name_task(client, names[:MAX_TITLES_FOR_NAMING])
    if naming.error:
        return Named(fallback, error=naming.error)
    if not naming.coherent:
        # 모델이 하나의 업무로 보지 않았다. 억지로 이름을 붙이지 않는다.
        return Named(fallback, description=naming.description)
    return Named(naming.name or fallback, naming.description, source="model")


def _from_tokens(tokens: list[str]) -> str:
    if not tokens:
        return "미확인 업무"
    # 가장 자주 나온 낱말 하나둘로 임시 이름을 만든다.
    return " ".join(tokens[:2])[:20]


def _filenames(db: Database, doc_ids: list[int]) -> list[str]:
    if not doc_ids:
        return []
    marks = ", ".join("?" * len(doc_ids))
    rows = db.con.execute(
        f"SELECT filename FROM documents WHERE id IN ({marks}) ORDER BY eff_date DESC",
        doc_ids,
    ).fetchall()
    return [row["filename"] for row in rows]


# ── 저장 ────────────────────────────────────────────────────────────

def _clear_proposals(db: Database) -> None:
    """이전 제안만 지운다.

    사용자가 승인·수정한 업무와 직접 배정한 문서는 재발견이 덮어쓰지 않는다
    (NFR-SAF-004).
    """
    db.con.execute(
        "DELETE FROM task_docs WHERE origin = 'ai' AND task_id IN ("
        "  SELECT id FROM tasks WHERE origin = 'ai' AND status = 'proposed')"
    )
    db.con.execute(
        "DELETE FROM task_reading WHERE task_id IN ("
        "  SELECT id FROM tasks WHERE origin = 'ai' AND status = 'proposed')"
    )
    db.con.execute(
        "DELETE FROM tasks WHERE origin = 'ai' AND status = 'proposed' "
        "  AND id NOT IN (SELECT task_id FROM task_docs)"
    )


def _upsert_task(db: Database, named: Named, group: Cluster) -> int:
    """같은 이름이 나오면 하나로 합친다.

    한 업무가 두 묶음으로 갈라졌을 때 이름이 같으면 합치는 것이 옳다 —
    업무 분류가 흩어지는 것을 막는 장치다.
    """
    description = named.description or (
        f"파일명과 내용이 비슷한 문서 {group.size}건을 묶었습니다. "
        "이름을 짓지 못해 공통 낱말로 대신했습니다."
    )
    db.con.execute(
        "INSERT INTO tasks(name, description, origin, status, confidence) "
        "VALUES (?, ?, 'ai', 'proposed', ?) ON CONFLICT(name) DO NOTHING",
        (named.label, description, group.confidence),
    )
    row = db.con.execute("SELECT id FROM tasks WHERE name = ?", (named.label,)).fetchone()
    task_id = row["id"]
    # 사람이 고친 업무의 설명은 덮어쓰지 않는다.
    db.con.execute(
        "UPDATE tasks SET description = ? WHERE id = ? AND status = 'proposed'",
        (description, task_id),
    )
    return task_id


def _attach(db: Database, task_id: int, doc_ids: list[int]) -> int:
    """문서를 업무에 붙인다. 사용자가 직접 배정한 문서는 건드리지 않는다."""
    if not doc_ids:
        return 0
    marks = ", ".join("?" * len(doc_ids))
    manual = {
        row["doc_id"]
        for row in db.con.execute(
            f"SELECT doc_id FROM task_docs WHERE origin = 'user' AND doc_id IN ({marks})",
            doc_ids,
        )
    }
    targets = [doc_id for doc_id in doc_ids if doc_id not in manual]
    db.con.executemany(
        "INSERT INTO task_docs(task_id, doc_id, origin, confidence) "
        "VALUES (?, ?, 'ai', 'medium') ON CONFLICT(task_id, doc_id) DO NOTHING",
        [(task_id, doc_id) for doc_id in targets],
    )
    return len(targets)


def _store_reading(db: Database, task_id: int, doc_ids: list[int]) -> None:
    """먼저 읽을 문서와 그 이유를 저장한다."""
    facts = _facts(db, doc_ids)
    picks = scoring.recommend(facts, limit=5)
    db.con.execute("DELETE FROM task_reading WHERE task_id = ?", (task_id,))
    db.con.executemany(
        "INSERT INTO task_reading(task_id, doc_id, ordinal, score, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (task_id, pick.doc_id, ordinal, pick.score, pick.reason)
            for ordinal, pick in enumerate(picks, start=1)
        ],
    )


def _facts(db: Database, doc_ids: list[int]) -> list[scoring.DocFacts]:
    if not doc_ids:
        return []
    marks = ", ".join("?" * len(doc_ids))
    # 완전 중복본은 대표 하나만 남긴다. 같은 문서를 두 번 추천하면
    # 추천 목록 다섯 칸 중 두 칸이 같은 것으로 채워진다.
    rows = db.con.execute(
        f"""
        SELECT d.id, d.filename, d.eff_year, d.eff_month, d.eff_date_kind,
               d.char_count,
               (SELECT COUNT(*) FROM documents x
                 WHERE x.hash = d.hash AND d.hash IS NOT NULL
                   AND x.missing_since IS NULL) AS dup_n,
               EXISTS(SELECT 1 FROM version_groups g WHERE g.latest_doc_id = d.id) AS is_rep
        FROM documents d
        WHERE d.id IN ({marks}) AND d.missing_since IS NULL
          AND (d.hash IS NULL OR d.id = (
                SELECT MIN(y.id) FROM documents y
                WHERE y.hash = d.hash AND y.missing_since IS NULL))
        """,
        doc_ids,
    ).fetchall()
    return [
        scoring.DocFacts(
            doc_id=row["id"],
            filename=row["filename"],
            eff_year=row["eff_year"],
            eff_month=row["eff_month"],
            date_kind=row["eff_date_kind"],
            duplicate_count=max(1, row["dup_n"] or 1),
            char_count=row["char_count"] or 0,
            is_version_representative=bool(row["is_rep"]),
        )
        for row in rows
    ]
