"""XLSX / XLSM 파서 (openpyxl).

시트명과 셀 범위를 locator에 남긴다. 검색 근거로 "'검사결과' B4:F28"처럼
표시하려면 파싱 단계에서 좌표를 만들어야 한다(PAR-003).

data_only=True로 열어 수식이 아니라 마지막으로 계산된 표시값을 얻는다.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from .base import (
    EMPTY,
    ENCRYPTED,
    MAX_CELLS_PER_SHEET,
    MAX_CHARS,
    OK,
    PARTIAL,
    UNSUPPORTED,
    DocMeta,
    ParseResult,
    Section,
    clip,
    failed,
)

PARSER = "xlsx"
ROWS_PER_SECTION = 50


def parse(path: Path) -> ParseResult:
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        message = str(exc)
        if "encrypted" in message.lower() or "password" in message.lower():
            return failed(PARSER, "암호로 보호된 통합문서", ENCRYPTED)
        if path.suffix.lower() == ".xls":
            return failed(PARSER, "구형 XLS 형식 — 지원하지 않음", UNSUPPORTED)
        return failed(PARSER, f"통합문서 열기 실패: {exc}")

    # openpyxl Workbook은 컨텍스트 매니저가 아니다. read_only 모드에서는
    # 파일 핸들을 잡고 있으므로 close()를 반드시 불러야 원본이 잠기지 않는다.
    try:
        meta = _read_meta(wb)
        budget = [MAX_CHARS]
        out: list[Section] = []
        ordinal = 0
        truncated: list[str] = []

        for ws in wb.worksheets:
            cells_seen = 0
            buffer: list[str] = []
            first_row = None
            max_col = 0
            row_idx = 0

            for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if cells_seen >= MAX_CELLS_PER_SHEET:
                    truncated.append(ws.title)
                    break
                values = [_fmt(v) for v in row]
                cells_seen += len(values)
                if any(values):
                    max_col = max(max_col, len(values))
                    if first_row is None:
                        first_row = row_idx
                    buffer.append("\t".join(values).rstrip())

                if len(buffer) >= ROWS_PER_SECTION:
                    ordinal += 1
                    out.append(
                        _section(ws.title, ordinal, first_row, row_idx, max_col, buffer, budget)
                    )
                    buffer, first_row, max_col = [], None, 0

            if buffer:
                ordinal += 1
                out.append(
                    _section(ws.title, ordinal, first_row, row_idx, max_col, buffer, budget)
                )
    finally:
        wb.close()

    out = [s for s in out if s.text]
    if not out:
        return ParseResult(status=EMPTY, parser=PARSER, meta=meta)

    note = f"{', '.join(truncated)} 시트는 셀 상한으로 일부만 읽음" if truncated else None
    return ParseResult(
        status=PARTIAL if truncated else OK,
        parser=PARSER,
        sections=out,
        meta=meta,
        note=note,
    )


def _section(sheet, ordinal, first_row, last_row, max_col, buffer, budget) -> Section:
    start = first_row or 1
    end_col = get_column_letter(max(max_col, 1))
    locator = f"'{sheet}' A{start}:{end_col}{last_row}"
    body = clip(f"[{sheet}]\n" + "\n".join(buffer), budget)
    return Section("sheet", ordinal, locator, body)


def _fmt(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_meta(wb) -> DocMeta:
    props = wb.properties
    return DocMeta(
        title=(props.title or "").strip() or None,
        author=(props.creator or "").strip() or None,
        created=props.created.isoformat() if props.created else None,
        modified=props.modified.isoformat() if props.modified else None,
    )
