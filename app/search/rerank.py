"""근거 후보 재정렬과 근접 중복 축약 — 규칙 기반, LLM 호출 없음 (RAG 개선 R7).

R6에서 남은 마지막 실패가 여기로 넘어왔다. "계약관리 업무는 어떤 순서로
처리해?"에 근거 8건이 들어왔는데 **전부 같은 [집계] 표**였다.

    [집계] 구분 1분기 2분기 3분기 4분기 / 처리건수 41 38 45 39 …

여덟 건이 서로 다른 여덟 개 문서(_부장수정·_송부·_최종·_최종2 …)라
hash 기반 중복 제거(대표본 판정)와 문서당 상한(MAX_CHUNKS_PER_DOC)을
모두 통과한다 — **파일은 다른데 내용이 같은** 버전 사본이기 때문이다.
그래서 파일이 아니라 **내용**으로 한 번 더 접는다.

재정렬은 '먼저 읽을 문서'(core/scoring.py)가 이미 검증한 규칙을 그대로
쓴다. 같은 자료를 두고 화면마다 다른 기준으로 순위를 매기면 사용자가
"업무 화면에서는 이게 먼저인데 질문 근거에는 왜 저게 먼저지?"를 겪는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core import korean
from ..core.scoring import FINAL_MARKERS

# 재정렬 가중치는 RRF 점수에 비례해 얹는다. RRF 점수는 1/(60+rank) 규모라
# 절대값이 0.016 근처인데, 여기에 1.0짜리 보너스를 더하면 검색 순위가
# 통째로 무의미해진다. 최상위 점수를 기준으로 비율을 맞춘다.
FINAL_MARKER_BONUS = 0.15     # '송부'·'최종' 표기 — 실제로 쓰인 판본일 가능성
RECENT_YEAR_BONUS = 0.20      # 최신 연도 — 후임자가 기준 삼을 자료
TERM_MATCH_BONUS = 0.30       # 질의어가 본문에 그대로 있음 — 가장 강한 신호


@dataclass(slots=True)
class Candidate:
    """재정렬에 필요한 사실만 담는다. DB 행을 그대로 넘기지 않는다."""

    chunk_id: int
    score: float
    text: str
    filename: str = ""
    eff_year: int | None = None


def collapse_near_duplicates(candidates: list[Candidate]) -> list[Candidate]:
    """내용이 같은 조각을 하나로 접는다. 순위가 가장 높은 것만 남긴다.

    hash 기반 대표본 판정은 **파일 전체**가 같아야 걸린다. 표 한 장만 같고
    나머지가 다른 문서들은 그 판정을 피해 근거 자리를 함께 차지한다.
    공백·줄바꿈만 다른 것도 같은 내용으로 본다.

    한계: 숫자 하나가 다른 판본은 접지 않는다 — 그건 실제로 다른 내용이고,
    어느 쪽이 맞는지는 사람이 봐야 한다(자료가 충돌하면 한쪽을 고르지
    않는다는 정책과 같은 이유다).
    """
    seen: dict[str, Candidate] = {}
    out: list[Candidate] = []
    for candidate in candidates:
        key = _content_key(candidate.text)
        if key in seen:
            continue
        seen[key] = candidate
        out.append(candidate)
    return out


def rerank(
    candidates: list[Candidate], question: str, this_year: int | None = None,
) -> list[Candidate]:
    """규칙으로 순위를 조정한다. 검색 순위를 뒤집지 않고 밀어 준다.

    LLM 재정렬 모델을 쓰지 않는다 — 응답 시간 결정(계획서 §0)과 원칙 6에
    따른 것이며, 이 규칙의 한계가 평가셋으로 측정된 뒤에만 재검토한다.
    """
    if not candidates:
        return []

    scale = max(c.score for c in candidates) or 1.0
    terms = _query_terms(question)
    latest = this_year if this_year is not None else _latest_year(candidates)

    adjusted: list[tuple[float, Candidate]] = []
    for candidate in candidates:
        bonus = 0.0
        if _has_final_marker(candidate.filename):
            bonus += FINAL_MARKER_BONUS
        if latest is not None and candidate.eff_year == latest:
            bonus += RECENT_YEAR_BONUS
        if _matches_terms(candidate.text, terms):
            bonus += TERM_MATCH_BONUS
        adjusted.append((candidate.score + bonus * scale, candidate))

    adjusted.sort(key=lambda pair: -pair[0])
    return [
        Candidate(
            chunk_id=c.chunk_id, score=score, text=c.text,
            filename=c.filename, eff_year=c.eff_year,
        )
        for score, c in adjusted
    ]


# ── 내부 ────────────────────────────────────────────────────────────

def _content_key(text: str) -> str:
    return " ".join((text or "").split())


def _has_final_marker(filename: str) -> bool:
    return any(re.search(p, filename, re.IGNORECASE) for p, _w, _r in FINAL_MARKERS)


def _query_terms(question: str) -> list[str]:
    return korean.tokens(question)


def _matches_terms(text: str, terms: list[str]) -> bool:
    """질의어가 본문에 있는가. 조사·어미가 붙은 형태도 잡는다(core/korean.py)."""
    return any(korean.contains(text, term) for term in terms)


def _latest_year(candidates: list[Candidate]) -> int | None:
    years = [c.eff_year for c in candidates if c.eff_year is not None]
    return max(years) if years else None
