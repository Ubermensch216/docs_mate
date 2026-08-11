"""PDF 파서 (PyMuPDF).

텍스트 레이어가 없는 스캔본을 구분해 OCR 대기 대상으로 표시한다(PAR-004).
MVP에서 OCR은 범위 밖이므로 여기서는 '표시'까지만 한다.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .base import (
    EMPTY,
    ENCRYPTED,
    MAX_CHARS,
    OK,
    PARTIAL,
    DocMeta,
    ParseResult,
    Section,
    clip,
    failed,
)

PARSER = "pdf"

# 쪽당 이 글자 수에 못 미치면 텍스트 레이어가 없다고 본다.
TEXT_LAYER_MIN_CHARS_PER_PAGE = 20


def parse(path: Path) -> ParseResult:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        return failed(PARSER, f"PDF 열기 실패: {exc}")

    with doc:
        if doc.needs_pass:
            return failed(PARSER, "암호로 보호된 PDF", ENCRYPTED)

        meta = _read_meta(doc)
        budget = [MAX_CHARS]
        out: list[Section] = []
        empty_pages = 0

        for index, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text").strip()
            except Exception as exc:
                out.append(Section("page", index, f"{index}쪽", ""))
                empty_pages += 1
                continue
            if not text:
                empty_pages += 1
                continue
            body = clip(text, budget)
            if not body:
                break
            out.append(Section("page", index, f"{index}쪽", body))

        page_count = doc.page_count

    if not out:
        return ParseResult(
            status=EMPTY,
            parser=PARSER,
            meta=meta,
            note="텍스트 레이어 없음 — OCR 필요 (MVP 범위 밖)",
        )

    chars = sum(len(s.text) for s in out)
    if page_count and chars / page_count < TEXT_LAYER_MIN_CHARS_PER_PAGE:
        return ParseResult(
            status=PARTIAL,
            parser=PARSER,
            sections=out,
            meta=meta,
            note=f"텍스트 레이어 빈약({chars}자/{page_count}쪽) — 스캔본 가능성, OCR 필요",
        )
    if empty_pages:
        return ParseResult(
            status=PARTIAL,
            parser=PARSER,
            sections=out,
            meta=meta,
            note=f"{empty_pages}쪽에서 텍스트를 얻지 못함",
        )
    return ParseResult(status=OK, parser=PARSER, sections=out, meta=meta)


def _read_meta(doc) -> DocMeta:
    raw = doc.metadata or {}
    return DocMeta(
        title=(raw.get("title") or "").strip() or None,
        author=(raw.get("author") or "").strip() or None,
        created=(raw.get("creationDate") or "").strip() or None,
        modified=(raw.get("modDate") or "").strip() or None,
    )
