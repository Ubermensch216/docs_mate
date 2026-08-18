"""업무 묶기 시험.

묶는 일은 벡터가, 이름 짓는 일만 모델이 한다. 여기서는 벡터 쪽만 본다 —
모델 없이도 결과가 나와야 하고 결과가 결정적이어야 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.clustering import (
    DEFAULT_THRESHOLD,
    cluster,
    common_tokens,
)
from app.search import VectorStore, normalize


def _store(groups: dict[int, list[list[float]]]) -> VectorStore:
    ids, rows = [], []
    for base_id, vectors in groups.items():
        for offset, vector in enumerate(vectors):
            ids.append(base_id + offset)
            rows.append(vector)
    matrix = normalize(np.asarray(rows, dtype=np.float32))
    return VectorStore(np.asarray(ids, dtype=np.int64), matrix)


def _tight(axis: int, n: int, dims: int = 4, jitter: float = 0.05) -> list[list[float]]:
    """한 축에 몰린 벡터 n개. 같은 업무 문서를 흉내 낸다."""
    out = []
    for i in range(n):
        vector = [jitter * ((i % 3) - 1)] * dims
        vector[axis] = 1.0
        out.append(vector)
    return out


def test_empty_store_yields_nothing():
    result = cluster(VectorStore(np.empty(0, np.int64), np.empty((0, 0), np.float32)))
    assert result.clusters == []
    assert result.unassigned == []


def test_single_document_is_unassigned_not_a_task():
    """문서 한 건으로 '이것도 업무입니다'라고 말하면 신뢰를 잃는다."""
    result = cluster(_store({1: [[1.0, 0.0]]}))
    assert result.clusters == []
    assert result.unassigned == [1]


def test_separates_distinct_groups():
    store = _store({10: _tight(0, 5), 20: _tight(1, 4), 30: _tight(2, 3)})
    result = cluster(store, min_size=3)

    assert len(result.clusters) == 3
    sizes = sorted(c.size for c in result.clusters)
    assert sizes == [3, 4, 5]
    # 각 묶음은 같은 축의 문서만 담아야 한다
    for group in result.clusters:
        bases = {doc_id // 10 for doc_id in group.doc_ids}
        assert len(bases) == 1, group.doc_ids


def test_small_groups_go_to_unassigned():
    store = _store({10: _tight(0, 5), 20: _tight(1, 2)})
    result = cluster(store, min_size=3)

    assert len(result.clusters) == 1
    assert sorted(result.unassigned) == [20, 21]


def test_result_is_deterministic():
    store = _store({10: _tight(0, 5), 20: _tight(1, 5)})
    first = cluster(store)
    second = cluster(store)
    assert [c.doc_ids for c in first.clusters] == [c.doc_ids for c in second.clusters]


def test_clusters_are_sorted_by_size():
    store = _store({10: _tight(0, 3), 20: _tight(1, 7), 30: _tight(2, 5)})
    sizes = [c.size for c in cluster(store, min_size=3).clusters]
    assert sizes == sorted(sizes, reverse=True)


def test_cohesion_and_separation_feed_confidence():
    store = _store({10: _tight(0, 5), 20: _tight(1, 5)})
    for group in cluster(store).clusters:
        assert group.cohesion > group.separation
        assert group.confidence == "high"


def test_loose_grouping_is_not_reported_as_high_confidence():
    """느슨하게 묶인 것을 단단하다고 말하면 안 된다."""
    rows = [[1.0, 0.0], [0.60, 0.80], [0.80, 0.60]]
    store = VectorStore(np.array([1, 2, 3]), normalize(np.asarray(rows, np.float32)))
    result = cluster(store, threshold=0.55, min_size=3)
    assert result.clusters[0].confidence in ("medium", "low")


def test_single_cluster_cannot_claim_high_confidence_on_a_free_margin():
    """묶음이 하나뿐이면 분리도를 잴 상대가 없다.

    separation=0을 그대로 쓰면 margin이 부풀어 무엇이든 '단단함'이 된다.
    비교 대상이 없다는 사실 자체를 신뢰도에 반영해야 한다.
    """
    rows = [[1.0, 0.0], [0.80, 0.60], [0.86, 0.51]]
    store = VectorStore(np.array([1, 2, 3]), normalize(np.asarray(rows, np.float32)))
    group = cluster(store, threshold=0.70, min_size=3).clusters[0]

    assert group.has_peers is False
    assert group.cohesion > 0.85          # 응집도만 보면 높지만
    assert group.confidence == "medium"   # 비교 대상이 없어 단정하지 않는다


def test_higher_threshold_splits_more():
    store = _store({10: _tight(0, 6, jitter=0.35)})
    loose = cluster(store, threshold=0.60, min_size=1)
    strict = cluster(store, threshold=0.995, min_size=1)
    assert len(strict.clusters) >= len(loose.clusters)


def test_default_threshold_matches_measured_separation():
    """실측: 동일 업무 0.86~0.94, 타 업무 0.52~0.74. 그 사이여야 한다."""
    assert 0.74 < DEFAULT_THRESHOLD < 0.86


def test_default_threshold_sits_in_the_flat_stretch_that_was_measured():
    """0.81·0.82·0.83이 같은 점수를 낸 구간의 한가운데다 (F1 0.985).

    한 점에서만 좋은 값은 다음 자료에서 무너진다. 이 시험은 누군가 0.80
    같은 옛 값이나 0.84 이상(미분류가 2건→6건으로 뛰던 지점)으로 되돌릴 때
    **왜 그 자리였는지**를 다시 읽게 하려고 둔다.
    """
    assert 0.81 <= DEFAULT_THRESHOLD <= 0.83


# ── 파일명 공통 낱말 ────────────────────────────────────────────────

def test_common_tokens_finds_the_shared_business_word():
    names = [
        "2024_행정사무감사_요구자료접수_최종.docx",
        "2023_행정사무감사_제출자료_진짜최종.docx",
        "2025_행정사무감사_의원질의답변_송부.docx",
    ]
    assert "행정사무감사" in common_tokens(names)


def test_common_tokens_drops_years_and_version_markers():
    names = ["2024_예산_최종.hwp", "2023_예산_진짜최종.hwp", "2025_예산_부장수정.hwp"]
    tokens = common_tokens(names)
    assert "예산" in tokens
    assert not any(t.isdigit() for t in tokens)
    for junk in ("최종", "진짜최종", "부장수정"):
        assert junk not in tokens


def test_common_tokens_returns_nothing_when_names_share_nothing():
    names = ["회의록.hwp", "출장복명서.hwp", "물품구매.hwp"]
    assert common_tokens(names) == []


def test_common_tokens_needs_a_majority():
    """한두 건에만 나온 낱말을 업무 이름으로 쓰면 안 된다."""
    names = ["예산_요구.hwp", "예산_편성.hwp", "예산_집행.hwp", "회의록.hwp"]
    tokens = common_tokens(names)
    assert "예산" in tokens
    assert "회의록" not in tokens
