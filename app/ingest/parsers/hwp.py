"""HWP 5.0 파서 (OLE 복합문서).

한컴오피스 설치나 외부 변환 도구에 의존하지 않는다. 폐쇄망 반입 대상이므로
의존성을 늘리지 않는 것이 중요하다.

구조
  FileHeader           256바이트. 서명 + 버전 + 속성 비트(압축/암호)
  BodyText/Section0..N 레코드 스트림. 속성 비트가 서면 raw deflate로 압축
  PrvText              미리보기 텍스트(UTF-16LE, 무압축). 본문 실패 시 폴백

레코드 헤더는 4바이트 리틀엔디언에 tag(10) / level(10) / size(12)가 담긴다.
size가 0xFFF이면 뒤따르는 4바이트가 실제 크기다.

표 안의 글자도 PARA_TEXT로 나오므로 텍스트는 얻지만 표 구조는 보존되지 않는다.
이 한계는 parse_report에서 그대로 드러난다.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import olefile

from .base import (
    EMPTY,
    ENCRYPTED,
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

PARSER = "hwp"

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # 67

# HWP 제어문자 분류 (한글 문서 파일 형식 5.0 기준)
CHAR_CTRL = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}          # 1 wchar
INLINE_CTRL = {4, 5, 6, 7, 8, 9, 19, 20}                          # 8 wchar
EXTENDED_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}  # 8 wchar


def parse(path: Path) -> ParseResult:
    if not olefile.isOleFile(str(path)):
        head = path.open("rb").read(32)
        if head.startswith(b"HWP Document File V3"):
            return failed(PARSER, "HWP 3.0 형식 — 지원하지 않음", UNSUPPORTED)
        return failed(PARSER, "OLE 복합문서가 아님", UNSUPPORTED)

    try:
        ole = olefile.OleFileIO(str(path))
    except Exception as exc:  # olefile은 다양한 예외를 던진다
        return failed(PARSER, f"OLE 열기 실패: {exc}")

    with ole:
        if not ole.exists("FileHeader"):
            return failed(PARSER, "FileHeader 없음", UNSUPPORTED)

        header = ole.openstream("FileHeader").read()
        if len(header) < 40 or not header.startswith(b"HWP Document File"):
            return failed(PARSER, "HWP 서명 불일치", UNSUPPORTED)

        props = int.from_bytes(header[36:40], "little")
        compressed = bool(props & 0x01)
        if props & 0x02:
            # 암호 문서는 해제를 시도하지 않는다 (PAR-006)
            return failed(PARSER, "암호로 보호된 문서", ENCRYPTED)

        meta = _read_summary(ole)
        streams = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText"),
            key=lambda e: e[1],
        )

        budget = [MAX_CHARS]
        out: list[Section] = []
        ordinal = 0
        errors: list[str] = []

        for entry in streams:
            try:
                data = ole.openstream(entry).read()
                if compressed:
                    data = zlib.decompress(data, -15)
            except Exception as exc:
                errors.append(f"{'/'.join(entry)}: {exc}")
                continue

            for tag, _level, payload in _records(data):
                if tag != HWPTAG_PARA_TEXT:
                    continue
                para = _decode_para(payload)
                if not para.strip():
                    continue
                body = clip(para, budget)
                if not body:
                    break
                ordinal += 1
                out.append(Section("paragraph", ordinal, f"{ordinal}문단", body))

        if out:
            note = f"일부 스트림 실패: {'; '.join(errors)}" if errors else None
            status = PARTIAL if errors else OK
            return ParseResult(
                status=status, parser=PARSER, sections=out, meta=meta, note=note
            )

        # 본문을 못 읽으면 미리보기 텍스트로 폴백한다.
        preview = _preview_text(ole)
        if preview:
            body = clip(preview, [MAX_CHARS])
            return ParseResult(
                status=PARTIAL,
                parser=PARSER,
                sections=[Section("preview", 1, "미리보기", body)],
                meta=meta,
                note="본문 스트림 추출 실패 — PrvText 미리보기로 폴백",
            )

    if errors:
        return failed(PARSER, "; ".join(errors))
    return ParseResult(status=EMPTY, parser=PARSER, meta=meta)


def _records(buf: bytes):
    """레코드 스트림을 (tag, level, payload)로 순회한다."""
    pos, size = 0, len(buf)
    while pos + 4 <= size:
        header = int.from_bytes(buf[pos : pos + 4], "little")
        pos += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        length = (header >> 20) & 0xFFF
        if length == 0xFFF:
            if pos + 4 > size:
                return
            length = int.from_bytes(buf[pos : pos + 4], "little")
            pos += 4
        if pos + length > size:
            return
        yield tag, level, buf[pos : pos + length]
        pos += length


def _decode_para(data: bytes) -> str:
    """PARA_TEXT 레코드를 UTF-16LE로 읽고 제어문자를 걷어낸다."""
    out: list[str] = []
    i, end = 0, len(data) - 1
    while i < end:
        code = int.from_bytes(data[i : i + 2], "little")
        if code in CHAR_CTRL:
            if code in (10, 13):
                out.append("\n")
            i += 2
        elif code in INLINE_CTRL or code in EXTENDED_CTRL:
            i += 16  # 제어문자 1 + 정보 6 + 제어문자 1 = 8 wchar
        elif 0xD800 <= code <= 0xDFFF:
            i += 2  # 단독 서로게이트는 버린다
        else:
            out.append(chr(code))
            i += 2
    return "".join(out)


def _preview_text(ole: olefile.OleFileIO) -> str:
    if not ole.exists("PrvText"):
        return ""
    try:
        return ole.openstream("PrvText").read().decode("utf-16-le", errors="replace").strip()
    except Exception:
        return ""


def _read_summary(ole: olefile.OleFileIO) -> DocMeta:
    """\x05HwpSummaryInformation에서 제목·작성자·작성일을 취한다."""
    meta = DocMeta()
    try:
        props = ole.getproperties("\x05HwpSummaryInformation", convert_time=True)
    except Exception:
        return meta
    # 표준 SummaryInformation 프로퍼티 ID
    meta.title = _clean(props.get(2))
    meta.author = _clean(props.get(4))
    meta.created = _clean(props.get(12))
    meta.modified = _clean(props.get(13))
    return meta


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
