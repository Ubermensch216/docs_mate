"""화면이 내놓는 주장을 근거 목록으로 바꾼다 (계획서 §29).

서랍(`widgets/evidence_drawer.py`)은 `Evidence` 목록만 받는다. 그 목록을
만드는 일은 화면마다 다르므로 — 주기는 문서 id 목록에서, 단계는 앵커 문서
하나에서, 업무 설명은 묶인 문서 전체에서 — 여기 한 곳에 모은다. 화면마다
따로 만들면 같은 근거가 화면마다 다르게 보인다.

한 가지 규칙: **없는 근거를 지어내지 않는다.** 인용문을 못 뽑으면 빈 채로
넘기고, 서랍이 "인용문을 뽑지 못했습니다"라고 말한다.
"""

from __future__ import annotations

import sqlite3

from ...db import Database
from ..widgets import Evidence

SNIPPET_LIMIT = 180
# 파일명과 같은 첫 대목을 건너뛰기 위해 몇 대목까지 살펴볼지.
SNIPPET_LOOKAHEAD = 5
# 업무 이름·설명은 묶인 문서 제목에서 나온다(ai/discover.py). 전부 나열하면
# 근거가 아니라 목록이 되므로 최근 것부터 끊는다.
MAX_TASK_DOCS = 8


def snippet(db: Database, doc_id: int, filename: str = "") -> tuple[str, str]:
    """(locator, 인용문). 본문을 못 읽은 문서면 빈 문자열 둘.

    첫 대목이 파일명과 같은 문서가 흔하다(제목 줄이 그대로 첫 문단이다).
    그것을 인용문으로 올리면 서랍에 같은 글자가 두 번 나오고, 근거를 보탠
    것처럼 보이지만 실제로는 아무것도 더해 주지 않는다. 그래서 파일명과
    다른 첫 대목을 찾고, 없으면 첫 대목을 그대로 쓴다.
    """
    rows = db.con.execute(
        "SELECT locator, text FROM document_sections "
        "WHERE doc_id = ? AND trim(text) != '' ORDER BY ordinal LIMIT ?",
        (doc_id, SNIPPET_LOOKAHEAD),
    ).fetchall()
    if not rows:
        return "", ""

    stem = filename.rsplit(".", 1)[0].strip()
    for row in rows:
        text = " ".join(row["text"].split())
        if not stem or text != stem:
            return row["locator"] or "", _clip(text)
    return rows[0]["locator"] or "", _clip(" ".join(rows[0]["text"].split()))


def _clip(text: str) -> str:
    return text[:SNIPPET_LIMIT] + "…" if len(text) > SNIPPET_LIMIT else text


def from_documents(db: Database, doc_ids, note: str = "") -> list[Evidence]:
    """문서 id 목록을 근거로 바꾼다. 순서는 넘긴 순서를 지킨다 —
    화면이 정한 우선순위(최신순 등)가 서랍에서 뒤집히면 안 된다."""
    ids = [int(i) for i in doc_ids]
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    rows = {
        row["id"]: row
        for row in db.con.execute(
            f"SELECT id, filename, path, eff_date, eff_precision, eff_date_kind "
            f"FROM documents WHERE id IN ({marks})",
            ids,
        ).fetchall()
    }
    out: list[Evidence] = []
    for doc_id in ids:
        row = rows.get(doc_id)
        if row is None:          # 문서가 지워졌거나 자료원에서 빠졌다
            continue
        locator, text = snippet(db, doc_id, row["filename"])
        out.append(
            Evidence(
                label=row["filename"],
                locator=" · ".join(part for part in (_when(row), locator) if part),
                snippet=text,
                path=row["path"],
                note=note,
            )
        )
    return out


def for_cycle(db: Database, cycle: sqlite3.Row | None) -> list[Evidence]:
    """반복 주기의 근거 = 그 판단에 쓰인 문서들의 시점."""
    if cycle is None:
        return []
    # 왜 근거인지는 서랍 제목("반복 주기의 근거")이 이미 말한다. 같은 문장을
    # 16건 옆에 되풀이하면 근거 목록이 읽히지 않는다.
    return from_documents(db, parse_doc_ids(cycle["evidence"]))


def for_step(db: Database, step: sqlite3.Row) -> list[Evidence]:
    """처리 단계의 근거 = 그 단계를 만든 앵커 문서 하나."""
    if not step["doc_id"]:
        return []
    return from_documents(db, [step["doc_id"]], note=f"‘{step['label']}’ 단계의 근거 문서")


def for_task(db: Database, task: sqlite3.Row, docs) -> list[Evidence]:
    """업무 이름·설명의 근거.

    이름과 설명은 묶인 문서 **제목**에서 나온다(ai/discover.py의 `_name`).
    그러니 근거도 문서 목록이다 — 인용문을 그럴듯하게 붙이는 대신, 무엇을
    보고 지은 이름인지를 그대로 보여준다.
    """
    if (task["origin"] or "") == "user" or (task["status"] or "") == "edited":
        # 사람이 적은 설명에 AI의 근거를 붙이면 거짓말이 된다.
        return []
    return from_documents(db, [row["id"] for row in docs][:MAX_TASK_DOCS])


def parse_doc_ids(raw: str | None) -> list[int]:
    """근거 열은 "12,15,19" 꼴이다(jobs/pipeline.py). 옛 자료에 JSON 배열이
    들어 있을 수 있어 대괄호·따옴표도 함께 걷어 낸다."""
    if not raw:
        return []
    out: list[int] = []
    for piece in str(raw).strip("[]").split(","):
        piece = piece.strip().strip('"').strip("'")
        if piece.isdigit():
            out.append(int(piece))
    return out


def _when(row: sqlite3.Row) -> str:
    value = row["eff_date"]
    if not value:
        return ""
    precision = row["eff_precision"] or "day"
    if precision == "year":
        text = f"{value[:4]}년"
    elif precision == "month":
        text = f"{value[:4]}.{value[5:7]}"
    else:
        text = value
    return f"{text} (파일 날짜)" if row["eff_date_kind"] == "fs" else text
