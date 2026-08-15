"""파서 회귀 시험대 — 실제 문서로만 확인할 수 있는 것을 지킨다 (계획서 A-5).

    python -m app.tools.parse_bench --record            # 지금 결과를 기준으로 저장
    python -m app.tools.parse_bench --check             # 기준과 달라졌는지 검사
    python -m app.tools.parse_bench --check --folder D:\\표본

표본은 저장소 밖에 둔다. HWP 5.0은 합성이 불가능해 실제 공문서로만 검증할 수
있는데(app/core/hwp.py 주석 참고), 그 문서는 공직 자료라 저장소에 넣을 수
없다. 그래서 경로를 환경 변수로 받는다.

    setx NUNCHICOACH_SAMPLES D:\\검증표본

기준 파일에는 **본문을 담지 않는다.** 글자 수와 본문의 sha256만 적는다 —
본문이 바뀌면 해시가 달라지므로 회귀는 그대로 잡히고, 기준 파일 자체는
공유해도 안전하다. 감사 로그에 본문을 남기지 않는 것과 같은 규칙이다(SEC-005).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from ..ingest.parsers import parse, supported_extensions

ENV_VAR = "NUNCHICOACH_SAMPLES"
BASELINE_NAME = "parse_baseline.json"
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "__pycache__", ".git"}
SKIP_PREFIXES = ("~$", ".~")

# 글자 수는 파서 판본이 조금 달라도 흔들린다(공백 처리 등). 본문 해시가 같으면
# 어차피 같은 글이므로, 해시가 달라졌을 때만 이 값으로 크기를 함께 본다.
CHAR_TOLERANCE = 0.02


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)

    folder = _folder(args)
    if folder is None:
        print(
            "표본 폴더를 찾지 못했습니다.\n"
            f"  --folder 로 지정하거나 환경 변수 {ENV_VAR} 를 설정하세요.",
            file=sys.stderr,
        )
        return 2

    baseline_path = Path(args.baseline) if args.baseline else folder / BASELINE_NAME
    current = measure(folder)
    print(f"표본      {folder}")
    print(f"대상      {len(current):,}건")

    if args.record:
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"기준을 저장했습니다: {baseline_path}")
        return 0

    if not baseline_path.exists():
        print(f"기준 파일이 없습니다: {baseline_path}\n  먼저 --record 로 만드세요.",
              file=sys.stderr)
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    changes = compare(baseline, current)
    _print_report(baseline, current, changes)
    return 1 if changes else 0


# ── 측정 ────────────────────────────────────────────────────────────
def measure(folder: Path) -> dict[str, dict]:
    """표본 하나하나를 실제로 파싱한다. 원본은 읽기만 한다."""
    supported = set(supported_extensions())
    out: dict[str, dict] = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name == BASELINE_NAME:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name.startswith(SKIP_PREFIXES) or path.suffix.lower() not in supported:
            continue
        out[str(path.relative_to(folder)).replace("\\", "/")] = _measure_one(path)
    return out


def _measure_one(path: Path) -> dict:
    result = parse(path)
    text = result.text
    return {
        "status": result.status,
        "parser": result.parser,
        "sections": len(result.sections),
        "chars": len(text),
        # 본문은 담지 않는다. 해시만으로 "달라졌다"를 판정할 수 있다.
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "locators": [section.locator for section in result.sections[:3]],
        "author": bool(result.meta.author),
        "created": bool(result.meta.created),
    }


# ── 대조 ────────────────────────────────────────────────────────────
def compare(baseline: dict[str, dict], current: dict[str, dict]) -> list[str]:
    """사람이 읽는 변화 목록. 빈 목록이면 회귀가 없다."""
    changes: list[str] = []
    for name in sorted(set(baseline) | set(current)):
        before, after = baseline.get(name), current.get(name)
        if before is None:
            changes.append(f"+ {name}  새 표본 (기준에 없음, --record로 반영하세요)")
            continue
        if after is None:
            changes.append(f"- {name}  표본이 사라졌습니다")
            continue
        if before["status"] != after["status"]:
            changes.append(f"! {name}  상태 {before['status']} → {after['status']}")
            continue
        if before["text_sha256"] != after["text_sha256"]:
            delta = after["chars"] - before["chars"]
            ratio = abs(delta) / max(before["chars"], 1)
            mark = "!" if ratio > CHAR_TOLERANCE else "~"
            changes.append(
                f"{mark} {name}  본문이 달라졌습니다 "
                f"({before['chars']:,} → {after['chars']:,}자, {delta:+,})"
            )
            continue
        if before["sections"] != after["sections"]:
            changes.append(
                f"~ {name}  조각 수 {before['sections']} → {after['sections']} "
                f"(본문은 같음 — 위치 표시가 바뀌면 근거 표시가 어긋난다)"
            )
    return changes


def _print_report(baseline: dict, current: dict, changes: list[str]) -> None:
    statuses: dict[str, int] = {}
    for item in current.values():
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
    print("상태      " + " · ".join(f"{k} {v:,}" for k, v in sorted(statuses.items())))
    print(f"기준      {len(baseline):,}건")

    if not changes:
        print("\n기준과 같습니다. 회귀 없음.")
        return
    print(f"\n달라진 것 {len(changes)}건")
    for line in changes:
        print("  " + line)
    print("\n의도한 변화라면 --record 로 기준을 다시 저장하고, 무엇이 왜 바뀌었는지 "
          "커밋에 남기세요.")


# ── 부속 ────────────────────────────────────────────────────────────
def _folder(args) -> Path | None:
    raw = args.folder or os.environ.get(ENV_VAR)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="parse_bench",
        description="실제 문서 표본으로 파서 회귀를 검사한다 (원본 읽기 전용).",
    )
    parser.add_argument("--folder", default=None, help=f"표본 폴더 (기본: 환경 변수 {ENV_VAR})")
    parser.add_argument("--baseline", default=None, help=f"기준 파일 (기본: 표본폴더/{BASELINE_NAME})")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="지금 결과를 기준으로 저장")
    group.add_argument("--check", action="store_true", help="기준과 대조 (다르면 exit 1)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
