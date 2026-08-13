"""문서를 검색·질의응답용 조각(chunk)으로 나눈다.

v1은 파서가 나눠 둔 문단·표·페이지·시트·슬라이드 경계를 그대로 하나씩
조각으로 썼다. 실측(2026-08, 실제 프로젝트 77건)에서 그 결과가 드러났다.

    조각 길이 중앙값 34자, 50자 미만이 63%.
    예: '문서유형: 보고서'(9자), '작성일: 2024. 9. 12.'(18자)

9자짜리 조각의 임베딩은 의미를 담지 못한다. RAG가 Top-8을 모아도 컨텍스트가
300자 남짓이라 모델에게 답할 재료 자체가 없었다 — 질문 화면이 그럴듯하지만
빈 답을 내놓은 원인 중 하나였다(doc/질문화면_RAG_개선계획.md §1 측정 6).

v2는 산문류(문단·페이지)를 목표 400~800자 창으로 병합한다. locator는
범위로 남긴다("1~5문단") — 근거 위치는 잃지 않는다.

표·시트·슬라이드는 병합하지 않는다. 그 경계 자체가 의미이고, locator가
셀 범위·슬라이드 번호를 정확히 가리켜야 근거로서 값을 한다.
"""

from __future__ import annotations

import re

MAX_CHUNK_CHARS = 1200        # 이 이상은 임베딩 하나가 감당하기 버겁다 — 강제 분할
TARGET_MIN_CHARS = 400        # 이 아래에서는 계속 다음 섹션을 끌어와 합친다
TARGET_MAX_CHARS = 800        # 이 위인 섹션은 병합하지 않고 그대로 쓴다

# 산문류 — 순서대로 이어 읽는 글이라 인접 조각을 합쳐도 뜻이 유지된다.
# 표·시트·슬라이드·노트는 그 자체가 구조이므로 넣지 않는다.
MERGEABLE_KINDS = {"paragraph", "page"}

# v1은 버전을 남기지 않았다. v2부터 추적하므로 "2"에서 시작한다.
CHUNKING_VERSION = "2"

_LOCATOR_NUMBER = re.compile(r"^(\d+)(\D.*)$")


def build_chunks(sections: list[tuple[str, str, str]]) -> list[tuple[int, str, str]]:
    """(kind, locator, text) 목록을 (ordinal, locator, text) 조각으로 만든다.

    산문류는 목표 창에 찰 때까지 인접 섹션을 합친다. 그 밖의 종류(표·시트·
    슬라이드·노트)와, 이미 목표 상한을 넘는 섹션은 있는 그대로 하나의
    조각이 된다 — 다만 여전히 MAX_CHUNK_CHARS를 넘으면 강제로 쪼갠다.
    """
    chunks: list[tuple[int, str, str]] = []
    ordinal = 0
    buffer: list[tuple[str, str]] = []   # [(locator, text)], 같은 kind만 쌓인다
    buffer_kind: str | None = None
    buffer_len = 0

    def emit(locator: str, text: str) -> None:
        nonlocal ordinal
        pieces = [text[i : i + MAX_CHUNK_CHARS] for i in range(0, len(text), MAX_CHUNK_CHARS)]
        for piece in pieces:
            if not piece.strip():
                continue
            ordinal += 1
            chunks.append((ordinal, locator, piece))

    def flush() -> None:
        nonlocal buffer, buffer_kind, buffer_len
        if not buffer:
            return
        if len(buffer) == 1:
            locator, text = buffer[0]
            emit(locator, text)
        else:
            merged_locator = _range_locator([loc for loc, _ in buffer])
            merged_text = "\n".join(text for _, text in buffer)
            emit(merged_locator, merged_text)
        buffer = []
        buffer_kind = None
        buffer_len = 0

    for kind, locator, text in sections:
        if not text or not text.strip():
            continue
        body = text.strip()

        if kind not in MERGEABLE_KINDS or len(body) >= TARGET_MAX_CHARS:
            flush()
            emit(locator, body)
            continue

        if buffer and buffer_kind != kind:
            flush()

        buffer.append((locator, body))
        buffer_kind = kind
        buffer_len += len(body)
        # 불변식: 매 반복 시작 시 buffer_len < TARGET_MIN_CHARS다(이 줄이
        # 그 조건을 유지한다). 그래서 buffer_len(<400) + body(<800)는
        # MAX_CHUNK_CHARS(1200)를 넘지 않는다 — flush()가 강제 분할을
        # 부를 일이 없다는 뜻이고, 그래서 병합 조각이 중간에서 잘리지 않는다.
        if buffer_len >= TARGET_MIN_CHARS:
            flush()

    flush()
    return chunks


def _range_locator(locators: list[str]) -> str:
    """['1문단', ..., '5문단'] → '1~5문단'. 숫자+단위 형식이 아니면 양끝만 잇는다."""
    first, last = locators[0], locators[-1]
    if first == last:
        return first
    head = _LOCATOR_NUMBER.match(first)
    tail = _LOCATOR_NUMBER.match(last)
    if head and tail and head.group(2) == tail.group(2):
        return f"{head.group(1)}~{tail.group(1)}{head.group(2)}"
    return f"{first}~{last}"
