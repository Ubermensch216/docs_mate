"""잠긴 파일 처리 시험 (PRD §19 — 파일 잠김은 재시도 대기열로).

사용자가 한글·엑셀에서 문서를 열어 둔 채 스캔하는 것은 실제 업무에서
흔하다. 이걸 영구 실패로 굳히면 파일을 닫은 뒤에도 영영 안 읽힌다.

이 결함은 강제종료 재개 시험이 간헐적으로 실패하면서 드러났다 — 죽은
프로세스가 쥐고 있던 핸들을 OS가 회수하기 전에 재개 프로세스가 같은
파일을 열려다 Permission denied를 받았다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.ingest.parsers import parse  # noqa: E402
from app.ingest.parsers.base import FAILED, LOCKED, is_lock_error  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    yield QApplication.instance() or QApplication([])


# ── 잠김 판별 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("code", ["EACCES", "EBUSY", "EPERM"])
def test_permission_style_errors_count_as_locked(code):
    import errno

    exc = OSError(getattr(errno, code), "denied")
    assert is_lock_error(exc)


@pytest.mark.parametrize("code", ["ENOENT", "EISDIR"])
def test_other_errors_are_not_locked(code):
    import errno

    exc = OSError(getattr(errno, code), "nope")
    assert not is_lock_error(exc)


def test_parser_reports_locked_when_file_cannot_be_opened(tmp_path: Path, monkeypatch):
    """열 수 없는 파일은 FAILED가 아니라 LOCKED로 분류돼야 한다."""
    import errno

    target = tmp_path / "열려있는문서.xlsx"
    target.write_bytes(b"dummy")

    import app.ingest.parsers as parsers

    def deny(_path):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setitem(parsers._REGISTRY, ".xlsx", deny)
    assert parse(target).status == LOCKED


def test_real_failures_stay_failed(tmp_path: Path, monkeypatch):
    """잠김이 아닌 진짜 오류까지 LOCKED로 뭉뚱그리면 안 된다."""
    target = tmp_path / "깨진문서.xlsx"
    target.write_bytes(b"dummy")

    import app.ingest.parsers as parsers

    def broken(_path):
        raise ValueError("파일 구조가 손상됨")

    monkeypatch.setitem(parsers._REGISTRY, ".xlsx", broken)
    assert parse(target).status == FAILED


# ── 파이프라인 처리 ─────────────────────────────────────────────────

def test_locked_document_returns_to_pending_and_is_not_counted_as_failure(
    tmp_path: Path, monkeypatch
):
    """잠긴 문서는 다음 실행에서 다시 시도되어야 한다."""
    import errno

    tree = tmp_path / "자료"
    tree.mkdir()
    (tree / "열린문서.xlsx").write_bytes(b"dummy")

    db_path = tmp_path / "p.db"
    db = Database(db_path)
    db.init()
    db.add_source(tree)
    db.close()

    import app.ingest.parsers as parsers
    import app.jobs.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "OllamaClient",
        lambda *a, **k: __import__("app.ai.client", fromlist=["OllamaClient"])
        .OllamaClient(base_url="http://127.0.0.1:1"),
    )
    # 재시도를 기다리느라 시험이 느려지지 않게 지연을 없앤다.
    monkeypatch.setattr(pipeline_module, "LOCK_RETRY_DELAY", 0)

    def deny(_path):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setitem(parsers._REGISTRY, ".xlsx", deny)
    Pipeline(db_path).run()

    db = Database(db_path)
    try:
        row = db.con.execute(
            "SELECT parse_status FROM documents WHERE filename = '열린문서.xlsx'"
        ).fetchone()
        assert row["parse_status"] == "pending", "잠긴 파일이 실패로 굳었습니다"
        assert db.counts()["parse_failed"] == 0

        # 재시도 횟수를 소진시키면 안 된다 — 닫기만 하면 읽혀야 한다.
        jobs = db.con.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE kind = 'parse'"
        ).fetchone()
        assert jobs["n"] == 0, "잠김이 재시도 상한을 깎았습니다"
    finally:
        db.close()


def test_locked_file_is_read_once_the_lock_clears(tmp_path: Path, monkeypatch):
    """파일을 닫으면 다음 실행에서 정상적으로 읽혀야 한다."""
    import errno

    tree = tmp_path / "자료"
    tree.mkdir()
    (tree / "문서.txt").write_text("실제 내용입니다", encoding="utf-8")

    db_path = tmp_path / "p.db"
    db = Database(db_path)
    db.init()
    db.add_source(tree)
    db.close()

    import app.ingest.parsers as parsers
    import app.jobs.pipeline as pipeline_module
    from app.ai.client import OllamaClient

    monkeypatch.setattr(
        pipeline_module, "OllamaClient",
        lambda *a, **k: OllamaClient(base_url="http://127.0.0.1:1"),
    )
    monkeypatch.setattr(pipeline_module, "LOCK_RETRY_DELAY", 0)

    original = parsers._REGISTRY[".txt"]

    def deny(_path):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setitem(parsers._REGISTRY, ".txt", deny)
    Pipeline(db_path).run()

    # 잠금이 풀린 상태로 재실행
    monkeypatch.setitem(parsers._REGISTRY, ".txt", original)
    Pipeline(db_path).run()

    db = Database(db_path)
    try:
        row = db.con.execute(
            "SELECT parse_status FROM documents WHERE filename = '문서.txt'"
        ).fetchone()
        assert row["parse_status"] == "ok", "잠금이 풀렸는데도 읽지 못했습니다"
    finally:
        db.close()
