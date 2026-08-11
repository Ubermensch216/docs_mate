"""확장자 → 파서 디스패처.

파서는 예외를 던지지 않는다는 계약을 여기서 최종 보증한다. 어떤 파일이
무슨 이유로 실패하든 ParseResult로 돌아와야 조사 파이프라인이 멈추지 않는다.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import docx as _docx
from . import hwp as _hwp
from . import hwpx as _hwpx
from . import pdf as _pdf
from . import pptx as _pptx
from . import text as _text
from . import xlsx as _xlsx
from .base import (
    MAX_BYTES,
    TOO_LARGE,
    UNSUPPORTED,
    DocMeta,
    ParseResult,
    Section,
    failed,
)

__all__ = ["parse", "supported_extensions", "ParseResult", "Section", "DocMeta"]

_REGISTRY = {
    ".hwp": _hwp.parse,
    ".hwpx": _hwpx.parse,
    ".pdf": _pdf.parse,
    ".xlsx": _xlsx.parse,
    ".xlsm": _xlsx.parse,
    ".docx": _docx.parse,
    ".pptx": _pptx.parse,
    ".txt": _text.parse,
    ".csv": _text.parse,
    ".md": _text.parse,
    ".log": _text.parse,
    # 구형 포맷은 명시적으로 '지원 안 함'을 돌려준다. 조용히 빠지면
    # 사용자가 왜 검색에 안 걸리는지 알 수 없다.
    ".xls": _xlsx.parse,
    ".doc": _docx.parse,
    ".ppt": _pptx.parse,
}


def supported_extensions() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def parse(path: str | Path) -> ParseResult:
    path = Path(path)
    parser = _REGISTRY.get(path.suffix.lower())
    if parser is None:
        return failed("none", f"지원하지 않는 확장자: {path.suffix or '(없음)'}", UNSUPPORTED)

    try:
        size = path.stat().st_size
    except OSError as exc:
        return failed("none", f"파일 접근 실패: {exc}")

    if size > MAX_BYTES:
        return failed("none", f"크기 상한 초과 ({size:,}바이트)", TOO_LARGE)

    started = time.perf_counter()
    try:
        result = parser(path)
    except Exception as exc:  # 파서가 놓친 예외의 최종 방어선
        result = failed(path.suffix.lower().lstrip("."), f"{type(exc).__name__}: {exc}")
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
