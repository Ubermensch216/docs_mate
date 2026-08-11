"""벡터 저장소 시험.

문서 1만 건 × 1024차원이면 41MB다. 메모리에 올려 numpy로 전수 계산하는 것이
MVP에서는 가장 단순하고 빠르다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.db import Database
from app.search import VectorStore, normalize, pack, unpack


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    database.init()
    source_id = database.add_source(r"D:\자료")
    yield database, source_id
    database.close()


def _add(database: Database, source_id: int, name: str, vector: list[float]) -> int:
    doc_id = database.upsert_document(source_id, {
        "path": rf"D:\자료\{name}", "filename": name, "ext": ".hwp",
        "parse_status": "ok", "hash": name,
    })
    database.save_doc_embedding(doc_id, "bge-m3", len(vector), pack(vector), 100)
    return doc_id


def test_pack_and_unpack_roundtrip():
    values = [0.5, -0.25, 1.0, 0.0]
    assert unpack(pack(values)).tolist() == pytest.approx(values)


def test_normalize_makes_unit_rows():
    matrix = normalize(np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32))
    assert np.linalg.norm(matrix[0]) == pytest.approx(1.0)
    assert not np.isnan(matrix[1]).any(), "영벡터에서 0으로 나누면 안 됩니다"


def test_empty_store_is_usable():
    store = VectorStore(np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32))
    assert store.size == 0
    assert store.similar(1) == []
    assert store.search([0.1, 0.2]) == []


def test_similar_ranks_closest_first_and_excludes_self(db):
    database, source_id = db
    a = _add(database, source_id, "예산1.hwp", [1.0, 0.0, 0.0])
    b = _add(database, source_id, "예산2.hwp", [0.99, 0.1, 0.0])
    c = _add(database, source_id, "계약.hwp", [0.0, 0.0, 1.0])

    store = VectorStore.load(database.con)
    assert store.size == 3

    ranked = store.similar(a, top=2)
    assert [doc_id for doc_id, _ in ranked] == [b, c]
    assert a not in [doc_id for doc_id, _ in ranked], "자기 자신이 결과에 들어갔습니다"
    assert ranked[0][1] > ranked[1][1]


def test_search_by_raw_vector(db):
    database, source_id = db
    _add(database, source_id, "예산.hwp", [1.0, 0.0])
    target = _add(database, source_id, "계약.hwp", [0.0, 1.0])

    store = VectorStore.load(database.con)
    best = store.search([0.05, 1.0], top=1)
    assert best[0][0] == target


def test_mismatched_dimensions_are_skipped(db):
    """모델을 바꾸면 차원이 달라진다. 섞이면 계산이 깨진다."""
    database, source_id = db
    _add(database, source_id, "a.hwp", [1.0, 0.0, 0.0])
    _add(database, source_id, "b.hwp", [0.0, 1.0, 0.0])
    _add(database, source_id, "c.hwp", [1.0, 0.0])   # 다른 차원

    store = VectorStore.load(database.con)
    assert store.dim == 3
    assert store.size == 2


def test_embedding_is_replaced_not_duplicated(db):
    database, source_id = db
    doc_id = _add(database, source_id, "a.hwp", [1.0, 0.0])
    database.save_doc_embedding(doc_id, "bge-m3", 2, pack([0.0, 1.0]), 50)

    store = VectorStore.load(database.con)
    assert store.size == 1
    assert store.search([0.0, 1.0], top=1)[0][1] == pytest.approx(1.0)
