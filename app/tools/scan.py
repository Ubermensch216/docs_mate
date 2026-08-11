"""폴더 하나를 끝까지 처리하고 결과를 요약한다 (GUI 없이).

    python -m app.tools.scan <폴더> [--project 이름] [--fresh]

실제 전임자 자료로 파이프라인을 검증할 때 쓴다. 원본은 읽기만 한다.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ..db import Database, default_project_dir
from ..jobs.pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    root = Path(args.folder)
    if not root.is_dir():
        print(f"폴더를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    # Pipeline은 QObject다. 시그널을 쓰려면 애플리케이션 객체가 있어야 한다.
    QCoreApplication.instance() or QCoreApplication([])

    if args.fresh:
        directory = Path(tempfile.mkdtemp(prefix="workmemory-"))
    else:
        directory = default_project_dir() / args.project
        directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "project.db"
    print(f"프로젝트: {db_path}\n")

    db = Database(db_path)
    db.init()
    db.add_source(root)
    db.close()

    pipeline = Pipeline(db_path)
    pipeline.stage_done.connect(_print_stage)
    pipeline.failed.connect(lambda m: print(f"\n⚠ 오류: {m}", file=sys.stderr))
    pipeline.run()

    db = Database(db_path)
    try:
        _summary(db)
    finally:
        db.close()
    return 0


def _print_stage(report) -> None:
    print(f"■ {report.stage}   {report.done:,} / {report.total:,}")
    if report.note:
        print(f"    {report.note}")
    for error in report.errors[:5]:
        print(f"    ⚠ {error}")
    if len(report.errors) > 5:
        print(f"    ⚠ 외 {len(report.errors) - 5}건")
    print()


def _summary(db: Database) -> None:
    counts = db.counts()
    print("■ 결과")
    print(f"    전체 파일       {counts['total']:,}")
    print(f"    분석 대상 문서  {counts['documents']:,}")
    print(f"    본문 읽음       {counts['parsed']:,}")
    print(f"    읽지 못함       {counts['parse_failed']:,}")
    print(f"    완전 중복       {counts['duplicate_extra']:,}")
    print()

    rows = db.con.execute(
        "SELECT ext, parse_status, COUNT(*) AS n FROM documents "
        "WHERE parse_status != 'skipped' GROUP BY ext, parse_status ORDER BY ext, n DESC"
    ).fetchall()
    if rows:
        print("■ 확장자별 상태")
        current = None
        for row in rows:
            prefix = f"    {row['ext']:<8}" if row["ext"] != current else " " * 12
            current = row["ext"]
            print(f"{prefix}{row['parse_status']:<12}{row['n']:>7,}")
        print()

    failures = db.con.execute(
        "SELECT filename, parse_status, parse_error FROM documents "
        "WHERE parse_status NOT IN ('ok','partial','pending','skipped') LIMIT 10"
    ).fetchall()
    if failures:
        print("■ 읽지 못한 문서 (일부)")
        for row in failures:
            print(f"    {row['filename']}  [{row['parse_status']}] {row['parse_error'] or ''}")
        print()

    print("다음으로:")
    print("    python -m app.main        # 화면에서 확인")


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scan", description="폴더를 조사·해시·파싱하고 결과를 요약한다 (원본 읽기 전용)."
    )
    parser.add_argument("folder", help="조사할 폴더")
    parser.add_argument("--project", default="default", help="프로젝트 이름")
    parser.add_argument("--fresh", action="store_true", help="임시 프로젝트에 새로 담는다")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
