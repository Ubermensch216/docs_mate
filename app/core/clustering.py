"""업무 후보 발견 — 문서 벡터를 묶는다.

생성 모델로 문서를 하나씩 분류하면 1만 건에 7시간이 걸린다(실측). 임베딩은
11분이면 끝나므로, **묶는 일은 벡터가 하고 이름 짓는 일만 모델에게 맡긴다.**
묶음당 1회씩만 부르면 호출이 1만 번에서 수십 번으로 줄어든다.

같은 업무 문서가 같은 이름을 받는 효과도 있다. 문서마다 모델에게 물으면
"수질통계", "수질 및 환경 관리", "수질관리"가 뒤섞여 업무 분류가 흩어진다.

의존성을 늘리지 않기 위해 sklearn을 쓰지 않는다. 폐쇄망 반입 대상이므로
numpy만으로 구현한다.

알고리즘은 greedy leader clustering이다.
  1. 문서마다 임계값 이상 가까운 이웃 수를 센다
  2. 이웃이 가장 많은 미배정 문서를 씨앗으로 삼는다
  3. 씨앗에서 임계값 이상 가까운 미배정 문서를 그 묶음에 넣는다
  4. 남은 문서가 없을 때까지 반복한다

계층 군집은 n² 거리 행렬(1만 건이면 400MB)이 필요하고, k-means는 k를 미리
알아야 한다. 이 방식은 둘 다 피하면서 결과가 결정적이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..search.vector import VectorStore

# 실측 기준값. 합성 표본에서 동일 업무 0.86~0.94, 타 업무 0.52~0.74였다.
# 실제 자료는 파일명이 지저분해 분리가 덜 뚜렷하므로 재조정이 필요하다.
DEFAULT_THRESHOLD = 0.80
DEFAULT_MIN_SIZE = 3
BLOCK = 512   # 유사도 행렬을 블록으로 나눠 메모리를 억제한다


@dataclass(slots=True)
class Cluster:
    seed_id: int
    doc_ids: list[int]
    cohesion: float = 0.0        # 묶음 내부 평균 유사도
    separation: float = 0.0      # 다른 묶음과의 평균 유사도

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    @property
    def confidence(self) -> str:
        """묶음이 얼마나 단단한지. 숫자 확률을 노출하지 않기 위한 3단계."""
        margin = self.cohesion - self.separation
        if self.cohesion >= 0.85 and margin >= 0.15:
            return "high"
        if self.cohesion >= 0.75 and margin >= 0.08:
            return "medium"
        return "low"


@dataclass(slots=True)
class Clustering:
    clusters: list[Cluster] = field(default_factory=list)
    unassigned: list[int] = field(default_factory=list)
    threshold: float = DEFAULT_THRESHOLD

    @property
    def assigned_count(self) -> int:
        return sum(c.size for c in self.clusters)

    def summary(self) -> str:
        return (
            f"묶음 {len(self.clusters)}개 · 배정 {self.assigned_count:,}건 · "
            f"미분류 {len(self.unassigned):,}건"
        )


def cluster(
    store: VectorStore,
    threshold: float = DEFAULT_THRESHOLD,
    min_size: int = DEFAULT_MIN_SIZE,
) -> Clustering:
    """벡터를 묶어 업무 후보를 만든다.

    min_size에 못 미치는 묶음은 업무로 제안하지 않고 미분류로 보낸다. 문서
    두어 건으로 "이것도 업무입니다"라고 말하면 신뢰를 잃는다.
    """
    n = store.size
    if n == 0:
        return Clustering(threshold=threshold)
    if n == 1:
        return Clustering(unassigned=[int(store.ids[0])], threshold=threshold)

    matrix = store.matrix
    neighbours = _count_neighbours(matrix, threshold)
    order = np.argsort(-neighbours, kind="stable")

    assigned = np.zeros(n, dtype=bool)
    groups: list[np.ndarray] = []
    seeds: list[int] = []

    for index in order:
        if assigned[index]:
            continue
        similarity = matrix @ matrix[index]
        members = np.nonzero((similarity >= threshold) & ~assigned)[0]
        if members.size == 0:
            members = np.array([index])
        assigned[members] = True
        groups.append(members)
        seeds.append(int(index))

    result = Clustering(threshold=threshold)
    kept: list[tuple[int, np.ndarray]] = []
    for seed, members in zip(seeds, groups):
        if members.size >= min_size:
            kept.append((seed, members))
        else:
            result.unassigned.extend(int(store.ids[i]) for i in members)

    for seed, members in kept:
        others = np.concatenate([m for s, m in kept if s != seed]) if len(kept) > 1 else None
        result.clusters.append(
            Cluster(
                seed_id=int(store.ids[seed]),
                doc_ids=[int(store.ids[i]) for i in members],
                cohesion=_mean_within(matrix, members),
                separation=_mean_between(matrix, members, others),
            )
        )

    result.clusters.sort(key=lambda c: -c.size)
    return result


def _count_neighbours(matrix: np.ndarray, threshold: float) -> np.ndarray:
    n = matrix.shape[0]
    counts = np.zeros(n, dtype=np.int32)
    for start in range(0, n, BLOCK):
        stop = min(start + BLOCK, n)
        block = matrix[start:stop] @ matrix.T
        counts[start:stop] = (block >= threshold).sum(axis=1)
    return counts


def _mean_within(matrix: np.ndarray, members: np.ndarray) -> float:
    if members.size < 2:
        return 1.0
    block = matrix[members] @ matrix[members].T
    total = float(block.sum()) - members.size    # 대각선(자기 자신) 제거
    pairs = members.size * members.size - members.size
    return total / pairs if pairs else 1.0


def _mean_between(matrix: np.ndarray, members: np.ndarray, others: np.ndarray | None) -> float:
    if others is None or others.size == 0:
        return 0.0
    return float((matrix[members] @ matrix[others].T).mean())


def common_tokens(names: list[str], top: int = 6, min_share: float = 0.4) -> list[str]:
    """파일명에서 공통 낱말을 뽑는다.

    공직 자료는 파일명에 업무 이름이 그대로 들어 있는 경우가 많다("2024_행정
    사무감사_요구자료"). 모델에게 이름을 물을 때 함께 주면 정확도가 오르고,
    모델을 못 쓰는 상황에서는 이것만으로 임시 이름을 만들 수 있다.
    """
    from collections import Counter

    if not names:
        return []
    counter: Counter = Counter()
    for name in names:
        for token in set(_tokenize(name)):
            counter[token] += 1

    floor = max(2, int(len(names) * min_share))
    return [token for token, count in counter.most_common(top * 3) if count >= floor][:top]


def _tokenize(name: str) -> list[str]:
    import re
    from pathlib import Path

    stem = Path(name).stem
    # 버전 꼬리표와 연도는 업무 이름이 아니다.
    stem = re.sub(r"(진짜|최|정말)*(최종|최신|수정|송부|결재|부장수정|사본|복사본)\d*", " ", stem)
    stem = re.sub(r"(?:19|20)\d{2}", " ", stem)
    parts = re.split(r"[\s_\-.()\[\]]+", stem)
    return [p for p in parts if len(p) >= 2 and not p.isdigit()]
