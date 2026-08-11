"""시점 추정 실측 리포트 — Step 4의 게이트.

    python -m app.tools.dating_report <폴더> [--limit 200] [--samples 15]

When(언제 하는 일인가)과 How(작년엔 어떤 순서였나)가 전부 시점 추정에 걸려
있다. 그래서 화면을 만들기 전에 **어느 갈래로 판정되는지의 분포**를 먼저 잰다.

읽는 법
  본문·파일명·문서속성·폴더로 판정된 비율이 높을수록 좋다.
  '파일날짜'로만 판정된 비율이 높으면 주기 탐지를 신뢰할 수 없다 —
  복사 한 번에 오염되는 근거이기 때문이다. 이 경우 When 범위를 재조정한다.

원본은 읽기만 한다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..core import dating
from ..ingest.parsers import parse, supported_extensions
from ..ingest.scanner import EXCLUDE_DIRS, EXCLUDE_PREFIXES

KIND_LABEL = {
    dating.BODY: "① 본문",
    dating.FILENAME: "② 파일명",
    dating.META: "③ 문서속성",
    dating.FOLDER: "④ 폴더",
    dating.FS: "⑤ 파일날짜",
    None: "— 단서 없음",
}
# ①~④로 판정되면 주기 계산에 쓸 수 있다. ⑤는 못 쓴다.
TRUSTED = (dating.BODY, dating.FILENAME, dating.META, dating.FOLDER)


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    root = Path(args.folder)
    if not root.is_dir():
        print(f"폴더를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    files = _collect(root, args.limit)
    if not files:
        print(f"분석 대상 문서가 없습니다: {root}")
        return 1

    print(f"\n시점 추정 실측 리포트\n대상 : {root}\n표본 : {len(files):,}건\n")

    kinds: Counter = Counter()
    precisions: Counter = Counter()
    years: Counter = Counter()
    disagreements: list[tuple[str, int, int]] = []
    samples: list[tuple[str, str, str, str]] = []

    for path in files:
        result = parse(path)
        sections = [(s.locator, s.text) for s in result.sections[:6]]
        resolution = dating.collect(
            sections=sections,
            filename=path.name,
            path=path,
            meta_created=result.meta.created,
            meta_modified=result.meta.modified,
            fs_mtime=path.stat().st_mtime,
        )

        kinds[resolution.kind] += 1
        if resolution.value:
            precisions[resolution.precision] += 1
            years[resolution.year] += 1

        # 본문과 파일명이 다른 해를 가리키면 사람이 봐야 한다.
        by_kind = {c.kind: c for c in resolution.candidates}
        body, name = by_kind.get(dating.BODY), by_kind.get(dating.FILENAME)
        if body and name and body.year != name.year:
            disagreements.append((path.name, body.year, name.year))

        if len(samples) < args.samples:
            raw = next(
                (c.raw for c in resolution.candidates if c.kind == resolution.kind), ""
            )
            samples.append((
                path.name,
                resolution.value or "—",
                KIND_LABEL[resolution.kind],
                raw,
            ))

    _print_kinds(kinds, len(files))
    _print_counter("정밀도", precisions, {"day": "일", "month": "월", "year": "연"})
    _print_years(years)
    _print_samples(samples)
    _print_disagreements(disagreements)
    _print_gate(kinds, len(files))
    return 0


def _print_kinds(kinds: Counter, total: int) -> None:
    print("■ 판정 근거 분포")
    for kind in (*TRUSTED, dating.FS, None):
        n = kinds.get(kind, 0)
        if not n:
            continue
        bar = "█" * round(n / total * 40)
        print(f"    {KIND_LABEL[kind]:<12}{n:>6,}  {n / total * 100:>5.1f}%  {bar}")
    print()


def _print_counter(title: str, counter: Counter, labels: dict[str, str]) -> None:
    if not counter:
        return
    print(f"■ {title}")
    for key, n in counter.most_common():
        print(f"    {labels.get(key, key):<12}{n:>6,}")
    print()


def _print_years(years: Counter) -> None:
    if not years:
        return
    print("■ 연도 분포")
    for year in sorted(years):
        bar = "█" * round(years[year] / max(years.values()) * 30)
        print(f"    {year}  {years[year]:>6,}  {bar}")
    print()


def _print_samples(samples: list[tuple[str, str, str, str]]) -> None:
    if not samples:
        return
    print("■ 표본 (파일명 → 판정 시점 · 근거)")
    for name, value, kind, raw in samples:
        clipped = name if len(name) <= 44 else name[:43] + "…"
        print(f"    {clipped:<45}{value:<12}{kind:<12}{raw}")
    print()


def _print_disagreements(rows: list[tuple[str, int, int]]) -> None:
    if not rows:
        return
    print(f"■ 본문과 파일명이 다른 해를 가리킴 ({len(rows):,}건)")
    for name, body_year, name_year in rows[:10]:
        print(f"    {name:<45}본문 {body_year} vs 파일명 {name_year}")
    if len(rows) > 10:
        print(f"    외 {len(rows) - 10:,}건")
    print()


def _print_gate(kinds: Counter, total: int) -> None:
    trusted = sum(kinds.get(k, 0) for k in TRUSTED)
    ratio = trusted / total * 100 if total else 0
    print("■ 게이트 (주기 계산에 쓸 수 있는 시점의 비율)")
    print(f"    ①~④ 근거 {trusted:,}/{total:,} = {ratio:.1f}%")
    if ratio >= 80:
        print("    통과 — 타임라인·일정을 신뢰할 수 있습니다.")
    elif ratio >= 60:
        print("    주의 — 반복 주기는 ①~④ 문서만으로 계산하고, 나머지는 흐리게 표시하세요.")
    else:
        print("    미달 — 파일 날짜에 의존하는 문서가 너무 많습니다.")
        print("           doc/00 §8.2에 따라 When 범위를 재조정해야 합니다.")
    print()


def _collect(root: Path, limit: int | None) -> list[Path]:
    supported = set(supported_extensions())
    found: list[Path] = []
    for path in root.rglob("*"):
        if limit and len(found) >= limit:
            break
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.name.startswith(EXCLUDE_PREFIXES):
            continue
        if path.is_file() and path.suffix.lower() in supported:
            found.append(path)
    return found


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dating_report",
        description="문서 시점이 어느 근거로 판정되는지 실측한다 (원본 읽기 전용).",
    )
    parser.add_argument("folder")
    parser.add_argument("--limit", type=int, default=None, help="표본 상한")
    parser.add_argument("--samples", type=int, default=15, help="예시로 출력할 건수")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
