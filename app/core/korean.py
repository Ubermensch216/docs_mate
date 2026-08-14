"""한국어 낱말 대조 — 조사·어미·문장부호를 넘어 같은 말인지 본다.

한국어는 낱말 뒤에 조사와 어미가 붙는다. "계약관리는"과 "계약관리",
"작성되었다"와 "작성"은 사람에게는 같은 말이지만 문자열로는 다르다.
낱말 완전일치로 대조하면 자연어 문장이 거의 다 헛돈다.

이 프로젝트에서 같은 실수를 세 번 했다.
  · R3 — FTS AND 결합이 질문 문장의 모든 낱말을 요구해 항상 실패했다.
    (escape_match_any로 해결)
  · R7 — 재정렬의 질의어 대조가 "계약관리는"을 못 찾았다.
  · R7 — 근거 검증의 어휘 중첩 계산이 같은 이유로 정상 문장을 버렸다.

형태소 분석기를 들이지 않는다. 조사·어미는 보통 한두 글자라 뒤에서
두 글자까지만 떼어 보면 충분하고, 폐쇄망에 사전을 반입하는 비용을 아직
지불할 이유가 없다(원칙 1 — 규칙으로 되는 것은 규칙으로).
"""

from __future__ import annotations

import re

MIN_STEM = 2
_MAX_SUFFIX = 2   # 떼어 볼 조사·어미 최대 길이
_TRAILING = ",.·:;!?\"')]}"
_WORD = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9,.]*")


def tokens(text: str) -> list[str]:
    """대조에 쓸 낱말만 남긴다. 한 글자짜리는 뜻을 가르지 못해 뺀다."""
    found = _WORD.findall(text or "")
    return [w for w in (strip_punctuation(f) for f in found) if len(w) >= MIN_STEM]


def strip_punctuation(word: str) -> str:
    """뒤에 붙은 문장부호만 뗀다. '1,234'의 쉼표는 남긴다."""
    return word.rstrip(_TRAILING)


def stem(word: str) -> str:
    """contains()가 받아 줄 수 있는 가장 짧은 형태 — 대조가 가장 넓어지는 지점.

    낱말이 얼마나 흔한지 셀 때 이 형태로 세야 한다. 조사가 붙은 "업무는"은
    드물지만 어간 "업무"는 어느 공문에나 있다 — 붙은 형태로 세면 흔한
    낱말을 드물다고 잘못 판정한다(실측으로 겪었다).
    """
    word = strip_punctuation(word)
    return word[: max(MIN_STEM, len(word) - _MAX_SUFFIX)]


def contains(text: str, word: str) -> bool:
    """word가 text에 있는가. 조사·어미가 붙어 있어도 어간이 겹치면 참."""
    word = strip_punctuation(word)
    if len(word) < MIN_STEM:
        return False
    if word in text:
        return True
    floor = max(MIN_STEM, len(word) - _MAX_SUFFIX)
    return any(word[:length] in text for length in range(len(word) - 1, floor - 1, -1))


def overlap_ratio(text: str, reference: str) -> float:
    """text의 낱말 중 reference에도 있는 비율. 판단할 낱말이 없으면 1.0."""
    words = tokens(text)
    if not words:
        return 1.0
    hits = sum(1 for w in words if contains(reference, w))
    return hits / len(words)
