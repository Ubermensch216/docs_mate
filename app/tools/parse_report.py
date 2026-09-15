"""파서 실측 리포트.

    python -m app.tools.parse_report <폴더> [--limit 100] [--verbose]

Step 1의 게이트다. 파싱 품질이 확보되지 않으면 AI 성능과 무관하게 제품 가치가
없으므로(제품 원칙 3), UI를 만들기 전에 포맷별 추출 성공률을 실측한다.

원본을 절대 열어 쓰지 않는다 — 읽기 전용으로만 접근한다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.ingest.parsers import parse, supported_extensions
from app.ingest.parsers.base import (
    EMPTY,
    ENCRYPTED,
    FAILED,
    OK,
    PARTIAL,
    TOO_LARGE,
    UNSUPPORTED,
)

# 스캔에서 제외할 것들 (PRJ-003)
SKIP_DIRS = {
    "$RECYCLE.BIN", "System Volume Information", "node_modules",
    ".git", ".svn", "__pycache__", ".venv", "venv",
}
SKIP_PREFIXES = ("~$", ".~")

STATUS_ORDER = [OK, PARTIAL, EMPTY, FAILED, UNSUPPORTED, ENCRYPTED, TOO_LARGE]
STATUS_LABEL = {
    OK: "정상",
    PARTIAL: "부분",
    EMPTY: "빈문서",
    FAILED: "실패",
    UNSUPPORTED: "미지원",
    ENCRYPTED: "암호",
    TOO_LARGE: "초과",
}


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    root = Path(args.folder)
    if not root.is_dir():
        print(f"폴더를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    files = _collect(root, args.ext)
    if not files:
        print(f"대상 파일이 없습니다: {root}")
        print(f"지원 확장자: {', '.join(supported_extensions())}")
        return 1

    sampled = _sample(files, args.limit)
    total = sum(len(v) for v in sampled.values())

    print(f"\n파서 실측 리포트")
    print(f"대상   : {root}")
    print(f"파일   : 전체 {sum(len(v) for v in files.values()):,}건 · 표본 {total:,}건")
    if args.limit:
        print(f"표본화 : 확장자별 최대 {args.limit}건")
    print()

    stats: dict[str, Counter] = defaultdict(Counter)
    timings: dict[str, list[int]] = defaultdict(list)
    charcounts: dict[str, list[int]] = defaultdict(list)
    structured: dict[str, int] = defaultdict(int)
    reasons: dict[str, Counter] = defaultdict(Counter)
    notes: dict[str, Counter] = defaultdict(Counter)

    for ext, paths in sorted(sampled.items()):
        for path in paths:
            result = parse(path)
            stats[ext][result.status] += 1
            timings[ext].append(result.elapsed_ms)
            if result.ok:
                charcounts[ext].append(result.char_count)
                if any(s.kind in ("table", "sheet") for s in result.sections):
                    structured[ext] += 1
            if result.error:
                reasons[ext][_short(result.error)] += 1
            if result.note:
                notes[ext][_short(result.note)] += 1
            if args.verbose:
                mark = "OK " if result.ok else "!! "
                print(
                    f"  {mark}{STATUS_LABEL.get(result.status, result.status):4} "
                    f"{result.char_count:>7,}자 {result.elapsed_ms:>5}ms  {path.name}"
                    + (f"  ← {result.error or result.note}" if result.error or result.note else "")
                )

    _print_table(sampled, stats, timings, charcounts, structured)
    _print_details("실패·거부 사유", reasons)
    _print_details("경고", notes)
    _print_gate(stats)
    if args.strict and any(status not in (OK, PARTIAL) and count
                           for counts in stats.values() for status, count in counts.items()):
        return 1
    return 0


def _print_table(sampled, stats, timings, charcounts, structured) -> None:
    header = (
        f"{'확장자':<8}{'표본':>6}"
        + "".join(f"{STATUS_LABEL[s]:>7}" for s in STATUS_ORDER)
        + f"{'추출률':>8}{'평균ms':>8}{'평균글자':>10}{'표보존':>8}"
    )
    print(header)
    print("-" * 96)

    for ext in sorted(sampled):
        n = len(sampled[ext])
        counts = stats[ext]
        extracted = counts[OK] + counts[PARTIAL]
        rate = extracted / n * 100 if n else 0
        avg_ms = sum(timings[ext]) / len(timings[ext]) if timings[ext] else 0
        avg_chars = sum(charcounts[ext]) / len(charcounts[ext]) if charcounts[ext] else 0
        tables = f"{structured[ext]:>7}" if structured[ext] else f"{'-':>7}"
        print(
            f"{ext:<8}{n:>6}"
            + "".join(f"{counts[s] or '':>7}" for s in STATUS_ORDER)
            + f"{rate:>7.0f}%{avg_ms:>8.0f}{avg_chars:>10,.0f}{tables} "
        )
    print()


def _print_details(title: str, table: dict[str, Counter]) -> None:
    rows = [(ext, reason, n) for ext, counter in table.items() for reason, n in counter.most_common(5)]
    if not rows:
        return
    print(f"■ {title}")
    for ext, reason, n in sorted(rows, key=lambda r: -r[2]):
        print(f"  {ext:<8}{n:>4}건  {reason}")
    print()


def _print_gate(stats: dict[str, Counter]) -> None:
    """Step 1 게이트 판정. 핵심 4포맷의 추출률을 본다."""
    print("■ 게이트 (핵심 포맷 본문 추출률)")
    any_core = False
    for ext in (".hwp", ".hwpx", ".pdf", ".xlsx"):
        counts = stats.get(ext)
        if not counts:
            print(f"  {ext:<8}  표본 없음 — 실자료로 별도 검증 필요")
            continue
        any_core = True
        n = sum(counts.values())
        extracted = counts[OK] + counts[PARTIAL]
        rate = extracted / n * 100
        verdict = "통과" if rate >= 95 else ("주의" if rate >= 80 else "미달")
        print(f"  {ext:<8}{rate:>6.1f}%  ({extracted}/{n})  {verdict}")
    if not any_core:
        print("\n  ⚠ 핵심 포맷 표본이 없어 게이트를 판정할 수 없습니다.")
        print("    실제 업무 문서가 담긴 폴더로 다시 실행해야 합니다.")
    print()


def _collect(root: Path, only: set[str] | None) -> dict[str, list[Path]]:
    supported = set(supported_extensions())
    found: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name.startswith(SKIP_PREFIXES):
            continue
        ext = path.suffix.lower()
        if ext not in supported:
            continue
        if only and ext not in only:
            continue
        found[ext].append(path)
    return found


def _sample(files: dict[str, list[Path]], limit: int | None) -> dict[str, list[Path]]:
    if not limit:
        return files
    return {ext: paths[:limit] for ext, paths in files.items()}


def _short(text: str, width: int = 72) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="parse_report",
        description="포맷별 본문 추출 성공률·소요시간을 실측한다 (원본 읽기 전용).",
    )
    p.add_argument("folder", help="조사할 폴더")
    p.add_argument("--limit", type=int, default=None, help="확장자별 표본 상한")
    p.add_argument("--ext", nargs="*", default=None, help="특정 확장자만 (예: .hwp .pdf)")
    p.add_argument("--verbose", action="store_true", help="파일별 결과 출력")
    p.add_argument("--strict", action="store_true", help="본문 추출 실패가 있으면 오류 코드로 종료")
    args = p.parse_args(argv)
    if args.ext:
        args.ext = {e if e.startswith(".") else f".{e}" for e in (x.lower() for x in args.ext)}
    return args


if __name__ == "__main__":
    raise SystemExit(main())
