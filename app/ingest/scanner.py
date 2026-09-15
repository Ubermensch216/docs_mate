"""파일 조사 — 자료원을 재귀적으로 훑어 메타데이터를 모은다.

여기서는 파일을 열지 않는다. 이름·크기·시각만 읽는다. 그래야 수만 건도 빨리
훑어 목록을 먼저 보여줄 수 있다(본문 추출은 뒤 단계에서 백그라운드로 한다).

모든 파일을 목록화하되(ING-001) 분석 대상은 지원 확장자로 한정한다. 전체
건수와 분석 대상 건수를 함께 보여주는 편이 정직하다 — 사용자는 "18,237개 중
14,820건이 문서"라는 사실 자체를 알아야 한다.

원본은 읽기만 한다. 이 모듈에는 쓰기 연산이 존재하지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..db import Database
from .parsers import supported_extensions

# 훑지 않을 폴더·파일 (PRJ-003)
EXCLUDE_DIRS = {
    "$RECYCLE.BIN", "System Volume Information", "Windows", "Program Files",
    "Program Files (x86)", "AppData", "node_modules", "__pycache__",
    ".git", ".svn", ".venv", "venv", ".idea", ".vscode",
}
EXCLUDE_PREFIXES = ("~$", ".~", "._")
EXCLUDE_SUFFIXES = (".tmp", ".temp", ".bak", ".lnk", ".url", ".ini", ".db-wal", ".db-shm")

NEW, CHANGED, UNCHANGED = "new", "changed", "unchanged"


@dataclass
class ScanStats:
    found: int = 0          # 훑은 전체 파일
    documents: int = 0      # 지원 확장자 (분석 대상)
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0        # 이전 스캔에 있었으나 지금 없는 것
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "ScanStats") -> None:
        self.found += other.found
        self.documents += other.documents
        self.new += other.new
        self.changed += other.changed
        self.unchanged += other.unchanged
        self.missing += other.missing
        self.skipped += other.skipped
        self.errors.extend(other.errors)


def scan_source(
    db: Database,
    source_id: int,
    root: Path | str,
    excludes: str = "",
    on_progress: Callable[[int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanStats:
    """자료원 하나를 훑어 documents 테이블을 갱신한다.

    이미 아는 파일은 크기·수정시각으로 걸러 다시 처리하지 않는다(ING-005).
    """
    root = Path(root)
    stats = ScanStats()
    supported = set(supported_extensions())
    known = db.paths_for_source(source_id)
    seen: set[str] = set()
    user_excludes = _parse_excludes(excludes)

    for path, entry_stat, error in _walk(root, user_excludes):
        if should_stop and should_stop():
            break
        if error:
            stats.errors.append(error)
            continue

        stats.found += 1
        text_path = str(path)
        seen.add(text_path)

        ext = path.suffix.lower()
        is_document = ext in supported
        if is_document:
            stats.documents += 1

        mtime = _iso(entry_stat.st_mtime)
        previous = known.get(text_path)
        if previous and previous["size"] == entry_stat.st_size and previous["fs_mtime"] == mtime:
            db.mark_seen(previous["id"])
            stats.unchanged += 1
            if on_progress and stats.found % 200 == 0:
                on_progress(stats.found, path.name)
            continue

        fields = {
            "path": text_path,
            "filename": path.name,
            "ext": ext,
            "size": entry_stat.st_size,
            "fs_mtime": mtime,
            "fs_ctime": _iso(entry_stat.st_ctime),
            # 'skipped'는 분석 대상이 아닌 확장자(그림·실행파일 등)다.
            # 파서가 돌려주는 'unsupported'(구형 DOC/XLS 등)와 구분해야
            # "왜 이 파일은 안 읽혔나"에 정확히 답할 수 있다.
            "parse_status": "pending" if is_document else "skipped",
        }
        if previous:
            # 내용이 바뀌었으니 해시와 본문을 다시 뜬다.
            fields["hash"] = None
            stats.changed += 1
        else:
            stats.new += 1

        with db.transaction():
            if previous:
                db.invalidate_document_analysis(previous["id"])
            db.upsert_document(source_id, fields)

        if on_progress and stats.found % 100 == 0:
            on_progress(stats.found, path.name)

    # 사라진 원본은 지우지 않고 표시만 한다 (ING-008).
    gone = [row["id"] for text_path, row in known.items() if text_path not in seen]
    # 탐색에 실패한 범위는 '삭제'로 판정할 수 없다. 접근을 복구한 뒤 다시 판단한다.
    if gone and not stats.errors and not (should_stop and should_stop()):
        stats.missing = db.mark_missing(gone)

    db.audit(
        "scan.source",
        str(root),
        f"found={stats.found} new={stats.new} changed={stats.changed} missing={stats.missing}",
        "ok" if not stats.errors else f"{len(stats.errors)}건 오류",
    )
    if on_progress:
        on_progress(stats.found, "")
    return stats


def _walk(root: Path, user_excludes: list[str]) -> Iterator[tuple[Path, os.stat_result, str]]:
    """디렉터리를 훑는다. 접근 거부 같은 개별 오류는 격리한다 (ING-007)."""
    walk_errors: list[OSError] = []
    for current, dirs, files in os.walk(root, onerror=walk_errors.append):
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
            and not d.startswith(".")
            and not _matches(d, user_excludes)
        ]
        base = Path(current)
        for name in files:
            if name.startswith(EXCLUDE_PREFIXES) or name.lower().endswith(EXCLUDE_SUFFIXES):
                continue
            if _matches(name, user_excludes):
                continue
            path = base / name
            try:
                yield path, path.stat(), ""
            except OSError as exc:
                yield path, None, f"{path}: {exc.strerror or exc}"  # type: ignore[misc]
    for exc in walk_errors:
        yield Path(exc.filename or root), None, "폴더에 접근할 수 없습니다"  # type: ignore[misc]


def _matches(name: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    lowered = name.lower()
    return any(fnmatch(lowered, pattern) for pattern in patterns)


def _parse_excludes(excludes: str) -> list[str]:
    return [line.strip().lower() for line in excludes.splitlines() if line.strip()]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(timespec="microseconds")
