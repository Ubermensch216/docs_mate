"""TXT / CSV / MD 등 평문 파서.

공직 자료에는 CP949로 저장된 옛 텍스트 파일이 흔하므로 인코딩 추정 순서를
UTF-8 → CP949 → Latin-1(손실 허용)로 둔다.
"""

from __future__ import annotations

from pathlib import Path

from .base import EMPTY, MAX_CHARS, OK, DocMeta, ParseResult, Section, clip, failed

PARSER = "text"
ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


def parse(path: Path) -> ParseResult:
    raw = path.read_bytes()
    text = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")

    text = text.strip()
    if not text:
        return ParseResult(status=EMPTY, parser=PARSER, meta=DocMeta())

    budget = [MAX_CHARS]
    sections: list[Section] = []
    for i, block in enumerate(_blocks(text), start=1):
        body = clip(block, budget)
        if not body:
            break
        sections.append(Section("paragraph", i, f"{i}문단", body))

    return ParseResult(status=OK, parser=PARSER, sections=sections, meta=DocMeta())


def _blocks(text: str) -> list[str]:
    """빈 줄 기준으로 문단을 나눈다. 문단이 없으면 줄 단위로 묶는다."""
    parts = [p.strip() for p in text.split("\n\n")]
    parts = [p for p in parts if p]
    if len(parts) > 1:
        return parts
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # 줄이 아주 많으면 40줄씩 묶어 조각 수를 억제한다.
    if len(lines) > 40:
        return ["\n".join(lines[i : i + 40]) for i in range(0, len(lines), 40)]
    return ["\n".join(lines)] if lines else []
