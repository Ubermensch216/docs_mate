"""먼저 읽을 문서 추천.

정체성 문장의 "무엇부터 읽어야 하는가"에 답하는 부분이다.

두 가지 원칙을 지킨다.
  1. **AI 단일 점수로 순위를 정하지 않는다.** 규칙으로 셀 수 있는 신호(최근성,
     최종본 표기, 중복 횟수, 문서 유형)를 주로 쓴다. 모델이 없어도 추천이 나온다.
  2. **추천마다 이유를 만든다.** 이유 없는 별점은 신뢰를 만들지 못한다. 점수를
     매긴 근거를 사람이 읽을 수 있는 한 문장으로 함께 낸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# 문서 유형 가중치. 후임자가 먼저 봐야 할 것에 높은 값을 준다.
TYPE_WEIGHTS: tuple[tuple[str, float, str], ...] = (
    (r"인수인계", 3.0, "인수인계 문서입니다"),
    (r"업무계획|주요업무", 2.4, "그 해 업무 전체를 조망하는 문서입니다"),
    (r"현황|총괄", 2.0, "현황을 한눈에 정리한 문서입니다"),
    (r"지침|매뉴얼|편람", 2.0, "처리 기준을 담은 문서입니다"),
    (r"제출자료|답변", 1.6, "실제로 대외에 낸 산출물입니다"),
    (r"실적|결산", 1.4, "한 해 결과를 정리한 문서입니다"),
    (r"요구자료|접수", 1.2, "업무가 시작되는 지점입니다"),
    (r"회의|회의록", 0.6, ""),
    (r"참고|기타", 0.2, ""),
)

# 최종본으로 볼 만한 파일명 표기. 강한 것부터.
FINAL_MARKERS: tuple[tuple[str, float, str], ...] = (
    (r"송부", 1.8, "'송부' 표기가 있어 실제 보낸 판본으로 보입니다"),
    (r"결재|결제", 1.8, "'결재' 표기가 있어 확정본으로 보입니다"),
    (r"제출", 1.4, "'제출' 표기가 있습니다"),
    (r"진짜최종|최최종|final", 1.2, "가장 나중 판본으로 보입니다"),
    (r"최종", 1.0, "'최종' 표기가 있습니다"),
)

RECENCY_SPAN_YEARS = 4

# 이유를 두 문장만 보여준다. 어느 것을 남길지는 중요도로 정한다.
PRIORITY_PINNED = 100
PRIORITY_FINAL = 70      # '송부'·'결재' 같은 결정적 단서
PRIORITY_TYPE = 60
PRIORITY_RECENCY = 50
PRIORITY_DUPLICATE = 40
MAX_REASONS = 2


@dataclass(slots=True)
class Recommendation:
    doc_id: int
    filename: str
    score: float
    reason: str
    components: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class DocFacts:
    """추천에 필요한 사실만 담는다. DB 행을 그대로 넘기지 않는다."""

    doc_id: int
    filename: str
    eff_year: int | None = None
    eff_month: int | None = None
    date_kind: str | None = None
    duplicate_count: int = 1
    char_count: int = 0
    is_version_representative: bool = False
    user_pinned: bool = False


def recommend(
    facts: list[DocFacts],
    limit: int = 5,
    today: date | None = None,
) -> list[Recommendation]:
    """먼저 읽을 문서를 고른다. 이유를 반드시 함께 낸다."""
    if not facts:
        return []
    this_year = (today or date.today()).year

    scored: list[Recommendation] = []
    for item in facts:
        parts: dict[str, float] = {}
        # (중요도, 문장). 생성 순서가 아니라 중요도로 골라야 가장 쓸모 있는
        # 근거가 살아남는다 — '송부' 표기 같은 결정적 단서를 순서 때문에
        # 버리면 추천을 신뢰할 근거가 사라진다.
        reasons: list[tuple[int, str]] = []

        if item.user_pinned:
            parts["사용자 지정"] = 10.0
            reasons.append((PRIORITY_PINNED, "사용자가 대표 문서로 지정했습니다"))

        recency, recency_reason = _recency(item, this_year)
        parts["최근성"] = recency
        if recency_reason:
            reasons.append((PRIORITY_RECENCY, recency_reason))

        weight, type_reason = _type_weight(item.filename)
        parts["문서 유형"] = weight
        if type_reason:
            reasons.append((PRIORITY_TYPE, type_reason))

        final, final_reason = _final_marker(item.filename)
        parts["최종본 표기"] = final
        if final_reason:
            reasons.append((PRIORITY_FINAL, final_reason))

        if item.duplicate_count > 1:
            parts["복사 횟수"] = min(1.5, 0.5 * (item.duplicate_count - 1))
            reasons.append(
                (PRIORITY_DUPLICATE,
                 f"같은 문서가 {item.duplicate_count}곳에 복사되어 있습니다")
            )

        if item.is_version_representative:
            parts["버전 대표"] = 0.8

        parts["분량"] = _volume(item.char_count)

        # 시점을 파일 수정일로만 판정했다면 최근성을 신뢰할 수 없다.
        if item.date_kind == "fs":
            parts["시점 불확실"] = -0.8

        score = sum(parts.values())
        scored.append(
            Recommendation(
                doc_id=item.doc_id,
                filename=item.filename,
                score=round(score, 3),
                reason=_join(reasons),
                components={k: round(v, 3) for k, v in parts.items() if v},
            )
        )

    scored.sort(key=lambda r: (-r.score, r.filename))
    return scored[:limit]


def _recency(item: DocFacts, this_year: int) -> tuple[float, str]:
    if item.eff_year is None:
        return 0.0, ""
    age = this_year - item.eff_year
    if age < 0:
        age = 0
    value = max(0.0, (RECENCY_SPAN_YEARS - age) / RECENCY_SPAN_YEARS) * 2.0
    if age == 0:
        return value, "올해 자료입니다"
    if age == 1:
        return value, "작년 자료로, 올해 작성 시 기준이 됩니다"
    return value, ""


def _type_weight(filename: str) -> tuple[float, str]:
    for pattern, weight, reason in TYPE_WEIGHTS:
        if re.search(pattern, filename, re.IGNORECASE):
            return weight, reason
    return 1.0, ""


def _final_marker(filename: str) -> tuple[float, str]:
    for pattern, weight, reason in FINAL_MARKERS:
        if re.search(pattern, filename, re.IGNORECASE):
            return weight, reason
    return 0.0, ""


def _volume(char_count: int) -> float:
    """빈 문서와 알맹이 있는 문서를 가른다. 길다고 더 주지는 않는다."""
    if char_count <= 0:
        return -0.5
    if char_count < 200:
        return 0.0
    return 0.5


def _join(reasons: list[tuple[int, str]]) -> str:
    if not reasons:
        return "이 업무에서 비교적 최근 자료입니다"
    best = sorted(reasons, key=lambda item: -item[0])[:MAX_REASONS]
    return ". ".join(text for _priority, text in best)
