"""HWPX 파서.

HWPX는 ZIP + XML이므로 외부 의존성 없이 안정적으로 읽을 수 있다.
본문은 Contents/section*.xml 안의 <hp:p>(문단) / <hp:t>(텍스트 런) 구조다.

네임스페이스 URI가 생성 버전마다 달라질 수 있어 local-name으로만 판별한다.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

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

PARSER = "hwpx"


def parse(path: Path) -> ParseResult:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        return failed(PARSER, f"ZIP 구조 손상: {exc}")

    with zf:
        names = zf.namelist()
        if any(n.startswith("META-INF/") and "encryption" in n.lower() for n in names):
            return failed(PARSER, "암호화된 HWPX", ENCRYPTED)

        sections_xml = sorted(
            n for n in names if n.lower().startswith("contents/section") and n.lower().endswith(".xml")
        )
        if not sections_xml:
            return failed(PARSER, "Contents/section*.xml 없음", UNSUPPORTED)

        meta = _read_meta(zf, names)
        budget = [MAX_CHARS]
        out: list[Section] = []
        ordinal = 0

        for name in sections_xml:
            try:
                root = etree.fromstring(zf.read(name))
            except etree.XMLSyntaxError as exc:
                return failed(PARSER, f"{name} XML 오류: {exc}")

            for para_text, in_table in _paragraphs(root):
                if not para_text.strip():
                    continue
                body = clip(para_text, budget)
                if not body:
                    break
                ordinal += 1
                out.append(
                    Section(
                        "table" if in_table else "paragraph",
                        ordinal,
                        f"{ordinal}문단" + ("(표)" if in_table else ""),
                        body,
                    )
                )

    if not out:
        return ParseResult(status=EMPTY, parser=PARSER, meta=meta)
    return ParseResult(status=OK, parser=PARSER, sections=out, meta=meta)


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _paragraphs(root):
    """(문단 텍스트, 표 안 여부) 순회. 문서 순서를 유지한다."""
    for el in root.iter():
        if _local(el) != "p":
            continue
        runs = [t.text for t in el.iter() if _local(t) == "t" and t.text]
        if not runs:
            continue
        in_table = any(_local(a) in ("tbl", "tc") for a in el.iterancestors())
        yield "".join(runs), in_table


def _read_meta(zf: zipfile.ZipFile, names: list[str]) -> DocMeta:
    """version.xml / Contents/content.hpf 등에서 얻을 수 있는 것만 취한다."""
    meta = DocMeta()
    for cand in ("Contents/content.hpf", "meta.xml", "settings.xml"):
        if cand not in names:
            continue
        try:
            root = etree.fromstring(zf.read(cand))
        except etree.XMLSyntaxError:
            continue
        for el in root.iter():
            name = _local(el).lower()
            value = (el.text or "").strip()
            if not value:
                continue
            if name == "title" and not meta.title:
                meta.title = value
            elif name in ("creator", "author") and not meta.author:
                meta.author = value
            elif name in ("date", "created") and not meta.created:
                meta.created = value
            elif name == "modified" and not meta.modified:
                meta.modified = value
    return meta
