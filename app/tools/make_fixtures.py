"""검증용 합성 표본 생성기.

    python -m app.tools.make_fixtures [출력폴더]

실제 전임자 자료가 없는 상태에서 파서·시점추정·타임라인을 검증하기 위해,
공직 자료의 특징을 흉내 낸 표본을 만든다.

일부러 재현하는 특징
  - `최종`, `진짜최종`, `부장수정` 등 버전 난립
  - 같은 업무가 여러 해에 걸쳐 같은 시기에 반복 (When 검증용)
  - 한 해 안에서 요구 → 취합 → 제출 → 질의대응 순서 (How 검증용)
  - 파일명·본문·경로에 각각 다른 날짜 단서 (시점 추정 검증용)
  - 내용이 완전히 같은 중복 파일 (해시 중복 검증용)

⚠ HWP 5.0(.hwp)은 바이너리 OLE 포맷이라 합성이 어렵다. HWP 파서는 반드시
   실제 문서로 검증해야 한다.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

DEFAULT_OUT = Path("tests/fixtures/sample_tree")

# (업무, 연도, 월, 단계, 문서유형, 확장자)
PLAN: list[tuple[str, int, int, str, str, str]] = []
for year in (2022, 2023, 2024, 2025):
    # 행정사무감사 — 매년 9~11월 반복, 연내 4단계
    PLAN += [
        ("행정사무감사", year, 9, "요구자료 접수", "공문", "docx"),
        ("행정사무감사", year, 9, "부서별 자료요청", "공문", "docx"),
        ("행정사무감사", year, 10, "제출자료", "보고서", "docx"),
        ("행정사무감사", year, 11, "의원질의 답변", "보고서", "docx"),
    ]
    # 예산 — 매년 3월 요구, 12월 편성
    PLAN += [
        ("예산관리", year, 3, "예산 요구자료", "요구서", "xlsx"),
        ("예산관리", year, 12, "다음연도 예산편성", "계획서", "xlsx"),
    ]
    # 업무계획 — 매년 12월
    PLAN += [("업무계획", year, 12, "주요업무계획", "계획서", "pptx")]
    # 계약 — 매년 5월, PDF로 보관되는 경우
    PLAN += [("계약관리", year, 5, "용역 계약서", "계약", "pdf")]

# 월간 실적보고 — 매월 반복 (최근 2년만)
for year in (2024, 2025):
    for month in range(1, 13):
        PLAN.append(("월간실적보고", year, month, "실적 취합", "보고서", "xlsx"))

# 수질통계 — 분기별, 3년만 관측 (신뢰도 구분 검증용)
for year in (2023, 2024, 2025):
    for month in (1, 4, 7, 10):
        PLAN.append(("수질통계", year, month, "분기 수질통계", "통계", "csv"))

VERSION_SUFFIXES = ["", "_수정", "_최종", "_최종2", "_진짜최종", "_부장수정", "_송부"]


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[0]) if argv else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    made = 0
    for index, (task, year, month, step, doctype, ext) in enumerate(PLAN):
        # 폴더 구조도 일부러 어지럽힌다 — 연도별/기타/참고가 섞이도록
        folder = out / _messy_folder(task, year, index)
        folder.mkdir(parents=True, exist_ok=True)

        suffix = VERSION_SUFFIXES[index % len(VERSION_SUFFIXES)]
        name = f"{year}_{task}_{step.replace(' ', '')}{suffix}"
        body = _body(task, year, month, step, doctype)

        made += _write(folder / f"{name}.{ext}", ext, name, body)

        # 3개마다 완전 중복본을 하나 더 둔다 (해시 중복 검증)
        if index % 7 == 0:
            dup = out / "기타" / f"{name}_복사본.{ext}"
            dup.parent.mkdir(parents=True, exist_ok=True)
            made += _write(dup, ext, name, body)

    # 시점 단서가 파일시스템 수정일뿐인 문서 (⑤로만 판정되는 케이스)
    noclue = out / "참고"
    noclue.mkdir(parents=True, exist_ok=True)
    made += _write(noclue / "회의자료.txt", "txt", "회의자료",
                   "부서 내부 논의 내용 정리. 연도 표기 없음.")
    # HWPX 표본
    made += _write_hwpx(out / "2024" / "2024_행정사무감사_제출자료_최종.hwpx",
                        _body("행정사무감사", 2024, 10, "제출자료", "보고서"))

    print(f"표본 {made}건 생성: {out.resolve()}")
    print("\n다음으로:")
    print(f"  python -m app.tools.parse_report {out} --limit 200")
    print("\n⚠ .hwp(HWP 5.0)는 합성할 수 없습니다. 실제 문서로 별도 검증이 필요합니다.")
    return 0


def _messy_folder(task: str, year: int, index: int) -> str:
    if index % 11 == 0:
        return "기타"
    if index % 13 == 0:
        return f"참고/{task}"
    if index % 3 == 0:
        return f"{year}"
    return f"{task}/{year}"


def _body(task: str, year: int, month: int, step: str, doctype: str) -> str:
    return "\n\n".join([
        f"{year}년 {task} {step}",
        f"문서유형: {doctype}",
        f"작성일: {year}. {month}. 12.",
        f"본 문서는 {year}년 {month}월 {task} 업무 중 '{step}' 단계에서 작성되었다.",
        "담당부서는 관련 자료를 취합하여 기한 내 제출하였다.",
        f"전년도({year - 1}년) 자료를 참고하여 동일한 절차로 진행하였다.",
        "붙임: 관련 근거자료 1부.",
    ])


def _write(path: Path, ext: str, title: str, body: str) -> int:
    try:
        if ext == "docx":
            _write_docx(path, title, body)
        elif ext == "xlsx":
            _write_xlsx(path, title, body)
        elif ext == "pptx":
            _write_pptx(path, title, body)
        elif ext == "pdf":
            _write_pdf(path, title, body)
        elif ext in ("txt", "csv"):
            path.write_text(body, encoding="utf-8")
        else:
            return 0
    except Exception as exc:
        print(f"  생성 실패 {path.name}: {exc}", file=sys.stderr)
        return 0
    return 1


def _write_docx(path: Path, title: str, body: str) -> None:
    import docx

    doc = docx.Document()
    doc.core_properties.author = "홍길동"
    doc.core_properties.title = title
    doc.add_heading(title, level=1)
    for para in body.split("\n\n"):
        doc.add_paragraph(para)
    table = doc.add_table(rows=3, cols=3)
    for r, row in enumerate([["항목", "수량", "비고"], ["요구자료", "12", "제출완료"], ["추가질의", "3", "답변완료"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    doc.save(str(path))


def _write_xlsx(path: Path, title: str, body: str) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "본문"
    ws["A1"] = title
    for i, line in enumerate(body.splitlines(), start=3):
        ws.cell(row=i, column=1, value=line)
    data = wb.create_sheet("집계")
    data.append(["구분", "1분기", "2분기", "3분기", "4분기"])
    for name, *nums in [("수질검사", 120, 133, 128, 141), ("민원", 12, 9, 15, 7)]:
        data.append([name, *nums])
    wb.properties.creator = "홍길동"
    wb.save(str(path))


def _write_pptx(path: Path, title: str, body: str) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = title
    slide.placeholders[1].text = "\n".join(body.splitlines()[:5])
    slide.notes_slide.notes_text_frame.text = "발표 시 예산 증감 사유를 강조할 것."
    second = deck.slides.add_slide(deck.slide_layouts[5])
    second.shapes.title.text = "추진 일정"
    box = second.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(3))
    box.text_frame.text = body
    deck.core_properties.author = "홍길동"
    deck.save(str(path))


def _write_pdf(path: Path, title: str, body: str) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), title, fontsize=14, fontname="helv")
    page.insert_text((72, 130), body.encode("ascii", "replace").decode(), fontsize=10, fontname="helv")
    doc.save(str(path))
    doc.close()


def _write_hwpx(path: Path, body: str) -> int:
    """최소 구조의 HWPX를 만든다 (Contents/section0.xml + 필수 항목)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ns = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    paras = "".join(
        f'<hp:p><hp:run><hp:t>{_esc(line)}</hp:t></hp:run></hp:p>'
        for line in body.splitlines() if line.strip()
    )
    section = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<hs:sec xmlns:hs="{ns}" xmlns:hp="{ns}">{paras}</hs:sec>'
    )
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", "application/hwp+zip")
            zf.writestr("version.xml", '<?xml version="1.0"?><hv:HCFVersion xmlns:hv="x"/>')
            zf.writestr("Contents/section0.xml", section)
    except Exception as exc:
        print(f"  HWPX 생성 실패: {exc}", file=sys.stderr)
        return 0
    return 1


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
