"""문서 유형 라벨 — 처리 순서(How)의 각 단계 이름을 붙인다.

AI를 쓰지 않는다. 문서 하나하나에 모델을 부르면 비용이 크고(실측
2.5초/건, 1만 건이면 7시간), 처리 단계를 알아내는 데는 파일명의 한국어
업무 표현을 규칙으로 잡는 것으로 충분하다. 결정적으로 정할 수 있는 것을
모델에 맡기지 않는다는 원칙의 적용이다(제품 원칙 3).

순서가 우선순위다 — 파일명 하나에 여러 낱말이 섞이면(예: "요구자료접수")
앞쪽 패턴이 이긴다.
"""

from __future__ import annotations

import re

FALLBACK_LABEL = "문서 처리"

_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile("접수"), "접수"),
    (re.compile("요구|요청"), "자료 요청"),
    (re.compile("취합"), "취합"),
    (re.compile("검토"), "검토"),
    (re.compile("결재|결제"), "결재"),
    (re.compile("제출"), "제출"),
    (re.compile("송부"), "송부"),
    (re.compile("질의|답변"), "질의응답 대응"),
    (re.compile("회의"), "회의"),
    (re.compile("계획"), "계획 수립"),
    (re.compile("통계"), "통계 작성"),
    (re.compile("현황"), "현황 정리"),
    (re.compile("계약"), "계약"),
    (re.compile("보고"), "보고"),
)


def label_document(filename: str) -> str:
    """파일명에서 처리 단계 이름을 추정한다. 못 찾으면 일반 문구로 대신한다."""
    for pattern, label in _PATTERNS:
        if pattern.search(filename):
            return label
    return FALLBACK_LABEL
