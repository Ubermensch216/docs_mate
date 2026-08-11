"""문서를 검색·질의응답용 조각(chunk)으로 나눈다.

파서가 이미 문단·표·페이지·시트·슬라이드 단위로 나눠 놓은 것을 그대로 쓴다
(document_sections). 그 경계 자체가 사람이 읽는 단위이자 근거 위치이므로,
다시 자를 필요가 거의 없다 — 다만 시트 블록처럼 너무 큰 조각은 임베딩
품질과 속도를 위해 나눈다.
"""

from __future__ import annotations

MAX_CHUNK_CHARS = 1200


def build_chunks(sections: list[tuple[str, str]]) -> list[tuple[int, str, str]]:
    """(locator, text) 목록을 (ordinal, locator, text) 조각으로 만든다.

    너무 긴 섹션은 같은 locator로 여러 조각으로 나눈다 — 근거 위치는
    유지하되 임베딩 하나가 감당할 분량으로 자른다.
    """
    chunks: list[tuple[int, str, str]] = []
    ordinal = 0
    for locator, text in sections:
        if not text or not text.strip():
            continue
        body = text.strip()
        pieces = [body[i : i + MAX_CHUNK_CHARS] for i in range(0, len(body), MAX_CHUNK_CHARS)]
        for piece in pieces:
            if not piece.strip():
                continue
            ordinal += 1
            chunks.append((ordinal, locator, piece))
    return chunks
