"""벡터 저장·검색.

문서 1만 건 × 1024차원 float32 = 41MB다. 메모리에 올려 numpy로 전수 계산하면
수십 밀리초에 끝난다. sqlite-vec 같은 확장을 폐쇄망에 반입하는 비용을 MVP에서
지불할 이유가 없다.

교체 가능하도록 인터페이스는 좁게 유지한다. 규모가 커지면 이 파일만 바꾼다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

DTYPE = np.float32


def pack(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=DTYPE).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=DTYPE)


def normalize(matrix: np.ndarray) -> np.ndarray:
    """행별 L2 정규화. 정규화해 두면 코사인 유사도가 내적 한 번으로 끝난다."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


@dataclass(slots=True)
class VectorStore:
    """문서 벡터를 통째로 올려 두고 쓰는 단순 저장소."""

    ids: np.ndarray
    matrix: np.ndarray   # 정규화된 (n, dim)

    @property
    def size(self) -> int:
        return int(self.ids.size)

    @property
    def dim(self) -> int:
        return int(self.matrix.shape[1]) if self.matrix.size else 0

    @classmethod
    def load(cls, con: sqlite3.Connection, model: str | None = None) -> "VectorStore":
        sql = "SELECT e.doc_id, e.dim, e.vector FROM doc_embeddings e JOIN documents d ON d.id=e.doc_id WHERE d.missing_since IS NULL AND d.parse_status IN ('ok','partial')"
        params: tuple = ()
        if model:
            sql += " AND e.model = ?"
            params = (model,)
        rows = con.execute(sql + " ORDER BY doc_id", params).fetchall()
        if not rows:
            return cls(np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=DTYPE))

        dim = rows[0][1]
        usable = [r for r in rows if r[1] == dim]
        ids = np.fromiter((r[0] for r in usable), dtype=np.int64, count=len(usable))
        matrix = np.vstack([unpack(r[2]) for r in usable]).astype(DTYPE, copy=False)
        return cls(ids, normalize(matrix))

    def similar(self, doc_id: int, top: int = 10) -> list[tuple[int, float]]:
        """주어진 문서와 가까운 문서들을 돌려준다."""
        where = np.nonzero(self.ids == doc_id)[0]
        if where.size == 0:
            return []
        return self._rank(self.matrix[where[0]], top, exclude=int(where[0]))

    def search(self, vector: list[float] | np.ndarray, top: int = 10) -> list[tuple[int, float]]:
        query = np.asarray(vector, dtype=DTYPE)
        if query.size != self.dim or self.size == 0:
            return []
        norm = np.linalg.norm(query)
        return self._rank(query / (norm or 1.0), top)

    def _rank(self, query: np.ndarray, top: int, exclude: int | None = None) -> list[tuple[int, float]]:
        scores = self.matrix @ query
        if exclude is not None:
            scores[exclude] = -1.0
        count = min(top, scores.size)
        best = np.argpartition(-scores, count - 1)[:count]
        best = best[np.argsort(-scores[best])]
        return [(int(self.ids[i]), float(scores[i])) for i in best]
