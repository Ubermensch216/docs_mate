"""강제종료 재개 시험 (NFR-SAF-003).

실제 프로세스를 스캔 도중 강제 종료해, DB가 손상되지 않고 다음 실행에서
중단 지점부터 이어지는지 확인한다. 재개는 별도 큐가 아니라 문서 상태
자체로 판단하므로(ING-006), 프로세스가 통째로 죽어도 다음 실행이 같은
질의로 남은 일을 그대로 찾아낸다.

WAL 저널 모드(db/repo.py)가 이 시험의 전제다 — 쓰기 도중 죽어도 마지막
커밋된 지점까지는 보존된다.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.db import Database

FIXTURES = Path(__file__).parent / "fixtures" / "sample_tree"
REPO_ROOT = Path(__file__).parent.parent

needs_fixtures = pytest.mark.skipif(
    not FIXTURES.is_dir(), reason="python -m app.tools.make_fixtures 로 표본을 먼저 생성하세요"
)


@needs_fixtures
def test_force_killed_scan_leaves_a_valid_database_and_resumes(tmp_path: Path, monkeypatch):
    tree = tmp_path / "자료"
    shutil.copytree(FIXTURES, tree)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = project_dir / "project.db"

    # 1) 실제 자식 프로세스로 스캔을 시작하고, 끝나기 전에 강제 종료한다.
    #    콘솔 인코딩 문제를 피하려고 최소한의 스크립트를 직접 만든다.
    script = tmp_path / "run_scan.py"
    # AI 단계는 이 시험의 관심사가 아니다 — Ollama 유무와 무관하게 빠르고
    # 결정적으로 만들려고 연결 불가 주소로 고정한다(다른 파이프라인 시험과
    # 같은 패턴).
    script.write_text(
        "import sys, time; sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "from app.db import Database\n"
        "from app.ai.client import OllamaClient\n"
        "import app.jobs.pipeline as pipeline_module\n"
        "pipeline_module.OllamaClient = lambda *a, **k: OllamaClient(base_url='http://127.0.0.1:1')\n"
        # 문서마다 잠깐 쉬게 해서 **자식이 부모보다 먼저 끝나는 일을 없앤다.**
        # 이 시험은 '스캔 도중 강제 종료'를 확인하는 것이라 자식이 살아 있는
        # 동안 죽여야 하는데, 표본이 작아 부하가 걸린 기계에서는 부모가 다음
        # 폴링을 하기 전에 자식이 파이프라인을 다 끝내 버렸다(실제로 전체
        # 시험을 돌릴 때 간헐 실패했다). 파일을 늘려 시간을 버는 방법도 있지만
        # 그러면 재개 단계까지 함께 느려진다. 자식은 첫 문서가 들어온 직후
        # 죽으므로 이 지연이 시험 시간에 더해지지는 않는다.
        "_parse = pipeline_module.parse\n"
        "pipeline_module.parse = lambda *a, **k: (time.sleep(0.05), _parse(*a, **k))[1]\n"
        "from app.jobs.pipeline import Pipeline\n"
        "from PySide6.QtCore import QCoreApplication\n"
        "QCoreApplication([])\n"
        "db = Database(r'" + str(db_path) + "')\n"
        "db.init()\n"
        "db.add_source(r'" + str(tree) + "')\n"
        "db.close()\n"
        "Pipeline(r'" + str(db_path) + "').run()\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
    )
    # 파일이 생긴 것만 보고 죽이면 안 된다 — sqlite는 스키마를 붓기 **전에**
    # 빈 파일을 먼저 만든다. 그 찰나에 죽이면 표가 하나도 없는 DB가 남아,
    # 이 시험이 확인하려는 '중단된 진행'이 아니라 '시작도 못 한 상태'를 본다
    # (실제로 이 시험이 간헐적으로 실패하던 원인이다). 문서가 실제로 들어가기
    # 시작한 것을 확인하고 죽인다.
    deadline = time.monotonic() + 30
    while _scanned(db_path) == 0:
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("30초 안에 스캔이 시작되지 않았습니다")
        if proc.poll() is not None:
            pytest.fail(f"스캔이 시작되기 전에 프로세스가 끝났습니다 (exit={proc.returncode})")
        time.sleep(0.1)

    assert proc.poll() is None, "프로세스가 확인 전에 이미 끝났습니다 — 표본을 늘리세요"
    proc.kill()
    proc.wait(timeout=10)

    # 2) DB 파일 자체가 열리고, 정합성 검사를 통과해야 한다.
    assert db_path.exists(), "강제 종료 시점까지 DB 파일이 생성되지 않았습니다"
    db = _open_after_kill(db_path)
    integrity = db.con.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok", f"DB 정합성 검사 실패: {integrity}"

    partial_total = db.counts()["total"]
    db.close()

    # 3) 원본은 전혀 건드리지 않았어야 한다 — 원본 보존은 예외 없는 원칙이다.
    remaining = {p.name for p in tree.rglob("*") if p.is_file()}
    original = {p.name for p in FIXTURES.rglob("*") if p.is_file()}
    assert remaining == original, "강제 종료 중 원본 파일이 변경되었습니다"

    # 4) 다시 실행하면 중단 지점부터 이어져 끝까지 완료되어야 한다.
    from PySide6.QtCore import QCoreApplication

    from app.ai.client import OllamaClient
    import app.jobs.pipeline as pipeline_module
    from app.jobs.pipeline import Pipeline

    monkeypatch.setattr(
        pipeline_module, "OllamaClient",
        lambda *a, **k: OllamaClient(base_url="http://127.0.0.1:1"),
    )
    QCoreApplication.instance() or QCoreApplication([])
    Pipeline(db_path).run()

    db = Database(db_path)
    try:
        counts = db.counts()
        assert counts["total"] >= partial_total, "재개 후 문서 수가 줄었습니다 — 데이터 유실"

        # 실패했다면 어떤 파일이 왜 실패했는지 남긴다. 간헐적으로만 재현되는
        # 문제는 실패 순간의 상태를 못 잡으면 원인을 영영 모른다.
        failures = db.con.execute(
            "SELECT filename, parse_status, parse_error, size FROM documents "
            "WHERE parse_status NOT IN ('ok', 'partial', 'skipped')"
        ).fetchall()
        detail = [
            (r["filename"], r["parse_status"], r["parse_error"], r["size"])
            for r in failures
        ]
        assert counts["parse_failed"] == 0, f"재개 후 읽지 못한 문서: {detail}"
        assert counts["parsed"] == counts["documents"], f"미완료가 남음: {detail}"

        unhashed = db.con.execute(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE hash IS NULL AND parse_status != 'skipped'"
        ).fetchone()["n"]
        assert unhashed == 0, "재개 후에도 해시가 빠진 문서가 있습니다"
    finally:
        db.close()


def _scanned(db_path: Path) -> int:
    """자식이 지금까지 넣은 문서 수. 아직 못 읽을 상태면 0.

    쓰는 쪽이 살아 있는 동안 읽는다. WAL이라 읽기는 막히지 않지만, 파일이
    막 만들어진 순간에는 표가 없거나 잠겨 있을 수 있어 오류를 진행 없음으로
    본다 — 여기서 예외를 던지면 시험이 경합에 걸려 무너진다.
    """
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return 0
    try:
        return con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _open_after_kill(db_path: Path, attempts: int = 10) -> Database:
    """강제 종료 직후에는 잠깐 열리지 않을 수 있다.

    윈도우는 죽은 프로세스의 파일 핸들을 곧바로 놓지 않아서, 그 사이의 열기
    시도가 'disk I/O error'로 떨어진다. 제품의 결함이 아니라 종료 직후의
    한때이므로 짧게 기다렸다 다시 연다.
    """
    for attempt in range(attempts):
        db = Database(db_path)
        try:
            db.con.execute("SELECT 1")
            return db
        except sqlite3.Error:
            db.close()
            if attempt == attempts - 1:
                raise
            time.sleep(0.3)
    raise AssertionError("도달할 수 없음")
