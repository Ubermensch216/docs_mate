"""파서 계약 시험.

HWP 5.0은 합성이 어려워 실파일 표본이 없다. 대신 컨테이너(OLE) 밖의 순수
로직 — 레코드 스트림 파싱과 제어문자 디코딩 — 을 합성 바이트로 검증한다.
컨테이너 부분은 실제 문서로 별도 확인이 필요하다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.ingest.parsers import parse, supported_extensions
from app.ingest.parsers.base import ENCRYPTED, OK, PARTIAL, UNSUPPORTED
from app.ingest.parsers.hwp import HWPTAG_PARA_TEXT, _decode_para, _records

FIXTURES = Path(__file__).parent / "fixtures" / "sample_tree"


# ── HWP 레코드 스트림 ────────────────────────────────────────────────

def _record(tag: int, level: int, payload: bytes) -> bytes:
    """HWP 레코드 헤더 4바이트: tag(10) | level(10) | size(12)."""
    size = len(payload)
    if size >= 0xFFF:
        header = tag | (level << 10) | (0xFFF << 20)
        return header.to_bytes(4, "little") + size.to_bytes(4, "little") + payload
    header = tag | (level << 10) | (size << 20)
    return header.to_bytes(4, "little") + payload


def test_records_reads_tag_level_and_payload():
    stream = _record(HWPTAG_PARA_TEXT, 1, b"ABCD") + _record(99, 2, b"XY")
    got = list(_records(stream))
    assert got == [(HWPTAG_PARA_TEXT, 1, b"ABCD"), (99, 2, b"XY")]


def test_records_handles_extended_size_marker():
    """size가 0xFFF이면 뒤따르는 4바이트가 실제 크기다."""
    payload = b"Z" * 5000
    got = list(_records(_record(HWPTAG_PARA_TEXT, 0, payload)))
    assert got == [(HWPTAG_PARA_TEXT, 0, payload)]


def test_records_stops_on_truncated_stream():
    """잘린 스트림에서 무한 루프나 예외 없이 멈춰야 한다."""
    stream = _record(HWPTAG_PARA_TEXT, 0, b"OK!!")
    truncated = stream[:-2]  # payload 일부 소실
    assert list(_records(truncated)) == []
    assert list(_records(b"\x01\x02")) == []  # 헤더도 안 되는 길이


# ── HWP 문단 텍스트 디코딩 ──────────────────────────────────────────

def _wchars(*codes: int) -> bytes:
    return b"".join(c.to_bytes(2, "little") for c in codes)


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def test_decode_para_reads_korean_text():
    assert _decode_para(_utf16("행정사무감사 제출자료")) == "행정사무감사 제출자료"


def test_decode_para_turns_para_break_into_newline():
    data = _utf16("첫째줄") + _wchars(13) + _utf16("둘째줄")
    assert _decode_para(data) == "첫째줄\n둘째줄"


def test_decode_para_skips_extended_control_block():
    """확장 제어문자는 8 wchar(16바이트)를 차지한다. 그 안의 값이 본문으로
    새어 나오면 안 된다."""
    control = _wchars(2) + _wchars(0x0041) * 6 + _wchars(2)  # 총 8 wchar
    assert len(control) == 16
    data = _utf16("앞") + control + _utf16("뒤")
    assert _decode_para(data) == "앞뒤"


def test_decode_para_skips_inline_control_block():
    control = _wchars(9) + _wchars(0x0042) * 6 + _wchars(9)
    data = _utf16("가") + control + _utf16("나")
    assert _decode_para(data) == "가나"


def test_decode_para_drops_lone_surrogates():
    data = _utf16("정상") + _wchars(0xD800) + _utf16("문자")
    assert _decode_para(data) == "정상문자"


def test_decode_para_on_odd_length_does_not_raise():
    assert _decode_para(_utf16("가나다")[:-1]) == "가나"


# ── 디스패처 계약 ────────────────────────────────────────────────────

def test_parse_never_raises_on_garbage(tmp_path: Path):
    """어떤 쓰레기 입력에도 예외 대신 ParseResult가 나와야 한다 (ING-007)."""
    cases = {
        "broken.hwp": b"not an ole file at all",
        "broken.hwpx": b"PK\x03\x04 truncated garbage",
        "broken.pdf": b"%PDF-1.4 but not really",
        "broken.xlsx": b"\x00\x01\x02",
        "broken.docx": b"",
        "broken.pptx": b"random",
    }
    for name, blob in cases.items():
        path = tmp_path / name
        path.write_bytes(blob)
        result = parse(path)
        assert result.status != OK, name
        assert result.error, name


def test_parse_rejects_unknown_extension(tmp_path: Path):
    path = tmp_path / "sample.zip"
    path.write_bytes(b"PK")
    assert parse(path).status == UNSUPPORTED


def test_parse_reports_legacy_hwp3_as_unsupported(tmp_path: Path):
    path = tmp_path / "old.hwp"
    path.write_bytes(b"HWP Document File V3.00 \x1a" + b"\x00" * 64)
    result = parse(path)
    assert result.status == UNSUPPORTED
    assert "3.0" in (result.error or "")


def test_text_parser_reads_cp949(tmp_path: Path):
    """옛 공직 자료에는 CP949 텍스트가 흔하다."""
    path = tmp_path / "old.txt"
    path.write_bytes("2024년 수질통계 보고".encode("cp949"))
    result = parse(path)
    assert result.status == OK
    assert "수질통계" in result.text


def test_every_registered_extension_is_dispatchable():
    assert ".hwp" in supported_extensions()
    assert ".hwpx" in supported_extensions()


# ── 표본 트리 (있을 때만) ────────────────────────────────────────────

needs_fixtures = pytest.mark.skipif(
    not FIXTURES.is_dir(),
    reason="python -m app.tools.make_fixtures 로 표본을 먼저 생성하세요",
)


@needs_fixtures
def test_fixture_tree_parses_without_failure():
    failures = []
    for path in FIXTURES.rglob("*"):
        if not path.is_file():
            continue
        result = parse(path)
        if not result.ok:
            failures.append((path.name, result.status, result.error))
    assert not failures, failures


@needs_fixtures
def test_sections_always_carry_a_locator():
    """근거 위치가 없는 조각이 있으면 나중에 출처를 붙일 수 없다 (PAR-002)."""
    for path in FIXTURES.rglob("*.xlsx"):
        result = parse(path)
        assert result.sections
        for section in result.sections:
            assert section.locator, (path.name, section.ordinal)
            assert section.kind


@needs_fixtures
def test_xlsx_locator_contains_sheet_and_range():
    path = next(FIXTURES.rglob("*.xlsx"))
    result = parse(path)
    locators = [s.locator for s in result.sections]
    assert any("'" in loc and ":" in loc for loc in locators), locators


@needs_fixtures
def test_parsing_does_not_modify_originals():
    """원본 무변경은 제품 원칙 1이다. 파싱만으로 바이트가 바뀌면 안 된다."""
    targets = [p for p in FIXTURES.rglob("*") if p.is_file()][:40]
    before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in targets}
    for path in targets:
        parse(path)
    after = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in targets}
    assert before == after
