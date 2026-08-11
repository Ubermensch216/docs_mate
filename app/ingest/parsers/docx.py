"""DOCX 파서 (python-docx).

문단과 표를 문서 순서대로 훑는다. 순서를 지켜야 나중에 "3쪽 2문단" 같은
근거 위치가 실제 읽기 순서와 맞는다.
"""

from __future__ import annotations

from pathlib import Path

import docx as _docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import (
    EMPTY,
    ENCRYPTED,
    MAX_CHARS,
    OK,
    UNSUPPORTED,
    DocMeta,
    ParseResult,
    Section,
    clip,
    failed,
)

PARSER = "docx"


def parse(path: Path) -> ParseResult:
    if path.suffix.lower() == ".doc":
        return failed(PARSER, "구형 DOC 형식 — 지원하지 않음", UNSUPPORTED)
    try:
        document = _docx.Document(str(path))
    except Exception as exc:
        message = str(exc).lower()
        if "encrypted" in message or "password" in message:
            return failed(PARSER, "암호로 보호된 문서", ENCRYPTED)
        return failed(PARSER, f"문서 열기 실패: {exc}")

    meta = _read_meta(document)
    budget = [MAX_CHARS]
    out: list[Section] = []
    ordinal = 0
    table_no = 0

    for block in _blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            body = clip(text, budget)
            if not body:
                break
            ordinal += 1
            out.append(Section("paragraph", ordinal, f"{ordinal}문단", body))
        else:
            table_no += 1
            text = _table_text(block)
            if not text:
                continue
            body = clip(text, budget)
            if not body:
                break
            ordinal += 1
            out.append(Section("table", ordinal, f"표{table_no}", body))

    if not out:
        return ParseResult(status=EMPTY, parser=PARSER, meta=meta)
    return ParseResult(status=OK, parser=PARSER, sections=out, meta=meta)


def _blocks(document):
    """본문 자식 요소를 문서 순서대로 문단/표 객체로 돌려준다."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _table_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = [c.text.strip().replace("\n", " ") for c in row.cells]
        if any(cells):
            rows.append("\t".join(cells))
    return "\n".join(rows)


def _read_meta(document) -> DocMeta:
    props = document.core_properties
    return DocMeta(
        title=(props.title or "").strip() or None,
        author=(props.author or "").strip() or None,
        created=props.created.isoformat() if props.created else None,
        modified=props.modified.isoformat() if props.modified else None,
    )
