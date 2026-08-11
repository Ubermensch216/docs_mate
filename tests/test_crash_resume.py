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
        "import sys; sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "from app.db import Database\n"
        "from app.ai.client import OllamaClient\n"
        "import app.jobs.pipeline as pipeline_module\n"
        "pipeline_module.OllamaClient = lambda *a, **k: OllamaClient(base_url='http://127.0.0.1:1')\n"
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
    # 고정 sleep 대신 DB 파일이 실제로 생기는 것을 기다린다 — Python·PySide6
    # 콜드 스타트 시간이 환경마다 달라 고정 대기는 타이밍이 어긋나기 쉽다.
    deadline = time.monotonic() + 20
    while not db_path.exists():
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("20초 안에 DB 파일이 생기지 않았습니다")
        if proc.poll() is not None:
            pytest.fail(f"DB 파일이 생기기 전에 프로세스가 끝났습니다 (exit={proc.returncode})")
        time.sleep(0.1)

    time.sleep(0.4)   # 스캔·해시가 한창 돌고 있을 시점까지 조금 더 기다린다
    assert proc.poll() is None, "프로세스가 확인 전에 이미 끝났습니다 — 대기 시간을 줄이세요"
    proc.kill()
    proc.wait(timeout=10)

    # 2) DB 파일 자체가 열리고, 정합성 검사를 통과해야 한다.
    assert db_path.exists(), "강제 종료 시점까지 DB 파일이 생성되지 않았습니다"
    db = Database(db_path)
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
        assert counts["parse_failed"] == 0
        assert counts["parsed"] == counts["documents"], "재개 후에도 못 읽은 문서가 남았습니다"

        unhashed = db.con.execute(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE hash IS NULL AND parse_status != 'skipped'"
        ).fetchone()["n"]
        assert unhashed == 0, "재개 후에도 해시가 빠진 문서가 있습니다"
    finally:
        db.close()
