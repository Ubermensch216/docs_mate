"""문장별 근거 검증 — 규칙 기반, LLM 호출 없음 (RAG 개선 R5, 이 계획의 핵심).

모델에게 "sources"를 스스로 밝히라고 시켜도, 그 sources가 실제로 그 문장을
뒷받침하는지는 모델이 보장하지 않는다 — v1이 겪은 "그럴듯하지만 근거 없는
답"이 문장 단위로 재발할 수 있다. 그래서 규칙으로 한 번 더 확인한다.
생성 호출을 늘리지 않는다(원칙 6 — 대량 처리는 규칙 기반으로).

검증 순서 (하나라도 걸리면 그 문장은 버린다)
  ① sources가 비어 있다 — 근거를 스스로 못 댔다.
  ② 존재하지 않는 근거 번호를 인용했다 — 컨텍스트에 없는 조각을 지어냈다.
  ③ 문장의 숫자·연도·금액이 인용한 조각 어디에도 없다 — 숫자를 지어냈다.
     (실측: "1분기 41건, 2분기 38건…"처럼 무관한 표를 그대로 옮겨 붙인
     사례가 있었다. 이 검사는 '엉뚱한 근거를 정확히 인용한' 경우까지는
     잡지 못한다 — 그건 검색 단계의 몫이다.)
  ④ 인용한 조각과 겹치는 낱말이 거의 없다 — 인용은 했지만 그 내용과
     무관한 말을 하고 있다는 신호.

주의: 이 검증은 '문장이 인용한 근거에 충실한가'만 본다. '그 근거가
질문과 애초에 관련 있는가'는 검색 단계(R3·R4)와 프롬프트(R6)의 몫이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER = re.compile(r"\d[\d,.]*%?")
_MIN_NUMBER_LEN = 2   # 한두 자리 순번("1분기"의 "1")까지 걸면 오탐이 잦다
# 실측 기준값이 아니라 임시값이다 — R1 평가셋으로 재조정 대상(rag_report).
MIN_OVERLAP = 0.25


@dataclass(slots=True)
class VerifiedSentence:
    text: str
    sources: list[int]


@dataclass(slots=True)
class Dropped:
    text: str
    reason: str


@dataclass(slots=True)
class VerifyResult:
    kept: list[VerifiedSentence] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)

    @property
    def survived(self) -> bool:
        return bool(self.kept)


def verify_sentences(
    raw_sentences: list[Any], source_text: dict[int, str],
) -> VerifyResult:
    """모델이 낸 sentences 배열을 검증해 살아남은 것만 돌려준다.

    source_text는 {1-based 근거 번호: 그 조각 본문}. _format_context가
    매긴 번호와 같은 번호 체계를 써야 한다 — 다르면 멀쩡한 인용도
    '존재하지 않는 번호'로 오판한다.
    """
    result = VerifyResult()
    for item in raw_sentences:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue   # 빈 문장은 조용히 넘긴다 — 사유를 남길 것도 없다

        sources = _clean_sources(item.get("sources"))
        if not sources:
            result.dropped.append(Dropped(text, "근거를 스스로 밝히지 않았다"))
            continue

        valid_sources = [s for s in sources if s in source_text]
        if not valid_sources:
            result.dropped.append(Dropped(text, "존재하지 않는 근거 번호를 인용했다"))
            continue

        cited_text = " ".join(source_text[s] for s in valid_sources)

        missing = _unsupported_numbers(text, cited_text)
        if missing:
            result.dropped.append(
                Dropped(text, f"인용한 근거에 없는 숫자를 썼다: {', '.join(missing)}")
            )
            continue

        if not _overlaps_enough(text, cited_text):
            result.dropped.append(Dropped(text, "인용한 근거와 겹치는 말이 거의 없다"))
            continue

        result.kept.append(VerifiedSentence(text=text, sources=valid_sources))
    return result


def _clean_sources(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, bool):   # bool은 int의 서브클래스라 따로 걸러야 한다
            continue
        if isinstance(item, int):
            out.append(item)
    return out


def _unsupported_numbers(text: str, cited_text: str) -> list[str]:
    numbers = {n for n in _NUMBER.findall(text) if len(n) >= _MIN_NUMBER_LEN}
    return sorted(n for n in numbers if n not in cited_text)


def _overlaps_enough(text: str, cited_text: str) -> bool:
    tokens = [t for t in text.split() if len(t) >= 2]
    if not tokens:
        return True   # 판단할 낱말이 없으면(조사뿐 등) 막지 않는다
    hits = sum(1 for t in tokens if t in cited_text)
    return (hits / len(tokens)) >= MIN_OVERLAP
