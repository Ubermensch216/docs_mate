"""SHA-256 해시.

완전 중복 판정에 AI를 쓰지 않는다. 해시가 같으면 같은 파일이고, 오탐이 없다
(DUP-001). 결정적으로 정할 수 있는 것을 모델에 맡기지 않는 것이 핵심 원칙이다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .parsers.base import MAX_BYTES

CHUNK = 1024 * 1024


def sha256(path: Path | str) -> tuple[str | None, str | None]:
    """(해시, 오류) 튜플을 돌려준다. 예외를 던지지 않는다."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"파일 접근 실패: {exc.strerror or exc}"

    if size > MAX_BYTES:
        return None, f"크기 상한 초과 ({size:,}바이트) — 해시를 계산하지 않음"

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
    except OSError as exc:
        return None, f"읽기 실패: {exc.strerror or exc}"
    return digest.hexdigest(), None
