"""조사 파이프라인 통합 시험.

Step 3의 게이트는 하나다 — **원본이 단 한 바이트도 바뀌지 않는다.**
파이프라인 전체를 실제 파일 위에서 돌린 뒤 해시와 수정시각을 대조한다.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.ingest import scanner  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "sample_tree"

needs_fixtures = pytest.mark.skipif(
    not FIXTURES.is_dir(),
    reason="python -m app.tools.make_fixtures 로 표본을 먼저 생성하세요",
)


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QCoreApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    """조사 파이프라인 시험은 AI를 쓰지 않는다.

    실제 Ollama에 붙으면 시험이 느려지고(표본 77건 임베딩) 기계마다 결과가
    달라진다. 여기서 확인할 것은 스캔·해시·파싱·시점이지 AI가 아니다.
    AI 없이도 끝난다는 것 자체가 계약이므로 닫힌 포트를 물린다.
    """
    from app.ai import OllamaClient
    import app.jobs.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "OllamaClient",
        lambda *a, **k: OllamaClient(base_url="http://127.0.0.1:1"),
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """표본에서 추린 작은 트리를 쓴다.

    스캔·해시·시점의 의미를 확인하는 데 77건이 필요하지 않다. 확장자별로
    두어 건씩만 복사하면 시험이 몇 배 빨라진다. 전체 트리는 별도 시험에서
    한 번만 쓴다.
    """
    return _copy_subset(tmp_path / "자료", per_extension=2)


@pytest.fixture
def full_tree(tmp_path: Path) -> Path:
    target = tmp_path / "전체자료"
    shutil.copytree(FIXTURES, target)
    return target


def _copy_subset(target: Path, per_extension: int) -> Path:
    """확장자별로 앞의 몇 건만 복사한다. 중복본은 반드시 함께 가져온다."""
    taken: dict[str, int] = {}
    duplicates: list[Path] = []
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file():
            continue
        if "복사본" in path.name:
            duplicates.append(path)
            continue
        ext = path.suffix.lower()
        if taken.get(ext, 0) >= per_extension:
            continue
        taken[ext] = taken.get(ext, 0) + 1
        _place(path, target)

    # 완전 중복 판정을 시험하려면 같은 내용의 파일 쌍이 있어야 한다.
    for path in duplicates[:2]:
        _place(path, target)
        origin = path.name.replace("_복사본", "")
        source = next((p for p in FIXTURES.rglob(origin)), None)
        if source is not None:
            _place(source, target)
    return target


def _place(source: Path, target: Path) -> None:
    destination = target / source.relative_to(FIXTURES)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)


@pytest.fixture
def project(tmp_path: Path):
    db = Database(tmp_path / "project.db")
    db.init()
    yield db
    db.close()


def _fingerprint(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[str(path.relative_to(root))] = (
                hashlib.sha256(data).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return out


def _run(project: Database, tree: Path) -> Database:
    project.add_source(tree)
    project.close()
    Pipeline(project.path).run()
    fresh = Database(project.path)
    return fresh


# ── 게이트: 원본 무변경 ──────────────────────────────────────────────

@needs_fixtures
def test_pipeline_does_not_touch_originals(project, tree):
    before = _fingerprint(tree)
    assert before, "표본이 비어 있습니다"

    db = _run(project, tree)
    try:
        after = _fingerprint(tree)
    finally:
        db.close()

    assert set(before) == set(after), "파일이 생기거나 사라졌습니다"
    changed = {name for name in before if before[name] != after[name]}
    assert not changed, f"원본이 변경되었습니다: {sorted(changed)[:10]}"


@needs_fixtures
def test_pipeline_creates_no_files_in_source_tree(project, tree):
    before = {str(p.relative_to(tree)) for p in tree.rglob("*")}
    db = _run(project, tree)
    db.close()
    after = {str(p.relative_to(tree)) for p in tree.rglob("*")}
    assert before == after


@needs_fixtures
def test_full_tree_processes_without_failure(project, full_tree):
    """전체 표본을 한 번은 끝까지 돌려 본다."""
    db = _run(project, full_tree)
    try:
        counts = db.counts()
        assert counts["documents"] >= 70
        assert counts["parse_failed"] == 0, "읽지 못한 문서가 있습니다"
        assert counts["parsed"] == counts["documents"]
    finally:
        db.close()


# ── 결과 ────────────────────────────────────────────────────────────

@needs_fixtures
def test_pipeline_finds_hashes_and_reads_documents(project, tree):
    db = _run(project, tree)
    try:
        counts = db.counts()
        assert counts["total"] > 0
        assert counts["documents"] > 0
        assert counts["parsed"] > 0, "본문을 하나도 읽지 못했습니다"
        # 표본에는 일부러 완전 중복본을 넣어 두었다.
        assert counts["duplicate_extra"] > 0, "중복을 찾지 못했습니다"

        unhashed = db.con.execute(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE hash IS NULL AND parse_status != 'skipped'"
        ).fetchone()["n"]
        assert unhashed == 0, "해시가 빠진 문서가 있습니다"

        sections = db.con.execute("SELECT COUNT(*) AS n FROM document_sections").fetchone()["n"]
        assert sections > 0
        indexed = db.con.execute("SELECT COUNT(*) AS n FROM document_index").fetchone()["n"]
        assert indexed > 0
    finally:
        db.close()


@needs_fixtures
def test_second_run_is_idempotent(project, tree):
    """재실행이 같은 일을 다시 하지 않아야 재개가 의미를 갖는다 (ING-005)."""
    db = _run(project, tree)
    first = db.counts()
    db.close()

    Pipeline(project.path).run()

    db = Database(project.path)
    try:
        assert db.counts() == first
        remaining = db.con.execute(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE missing_since IS NULL AND (hash IS NULL OR parse_status = 'pending')"
        ).fetchone()["n"]
        assert remaining == 0, "재실행 후에도 남은 일이 있습니다"
    finally:
        db.close()


@needs_fixtures
def test_missing_original_is_flagged_not_deleted(project, tree):
    """원본이 사라져도 분석 기록을 지우지 않는다 (ING-008)."""
    db = _run(project, tree)
    total_before = db.counts()["total"]
    victim = next(tree.rglob("*.xlsx"))
    victim_path = str(victim)
    db.close()

    victim.unlink()
    Pipeline(project.path).run()

    db = Database(project.path)
    try:
        row = db.con.execute(
            "SELECT missing_since FROM documents WHERE path = ?", (victim_path,)
        ).fetchone()
        assert row is not None, "기록이 삭제되었습니다"
        assert row["missing_since"], "원본 없음 표시가 되지 않았습니다"
        assert db.counts()["total"] == total_before
    finally:
        db.close()


@needs_fixtures
def test_changed_file_is_reprocessed(project, tree):
    db = _run(project, tree)
    target = next(tree.rglob("*.txt"))
    target_path = str(target)
    old_hash = db.con.execute(
        "SELECT hash FROM documents WHERE path = ?", (target_path,)
    ).fetchone()["hash"]
    db.close()

    target.write_text("완전히 다른 내용입니다. 2025년 예산 요구.", encoding="utf-8")
    Pipeline(project.path).run()

    db = Database(project.path)
    try:
        row = db.con.execute(
            "SELECT hash, parse_status FROM documents WHERE path = ?", (target_path,)
        ).fetchone()
        assert row["hash"] != old_hash, "변경된 파일의 해시가 갱신되지 않았습니다"
        assert row["parse_status"] == "ok"
    finally:
        db.close()


# ── 스캐너 단위 ─────────────────────────────────────────────────────

def test_scanner_skips_temp_and_system_entries(project, tmp_path: Path):
    root = tmp_path / "원본"
    (root / "정상").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "정상" / "보고서.txt").write_text("내용", encoding="utf-8")
    (root / "~$보고서.docx").write_bytes(b"lock")
    (root / "임시.tmp").write_bytes(b"tmp")
    (root / ".git" / "config").write_text("x", encoding="utf-8")

    source_id = project.add_source(root)
    stats = scanner.scan_source(project, source_id, root)

    names = {
        r["filename"]
        for r in project.con.execute("SELECT filename FROM documents").fetchall()
    }
    assert names == {"보고서.txt"}
    assert stats.found == 1
    assert stats.documents == 1


def test_scanner_honours_user_excludes(project, tmp_path: Path):
    root = tmp_path / "원본"
    root.mkdir()
    (root / "보고서.txt").write_text("a", encoding="utf-8")
    (root / "제외대상.txt").write_text("b", encoding="utf-8")

    source_id = project.add_source(root, excludes="제외*")
    scanner.scan_source(project, source_id, root, excludes="제외*")

    names = {
        r["filename"]
        for r in project.con.execute("SELECT filename FROM documents").fetchall()
    }
    assert names == {"보고서.txt"}


def test_scanner_marks_non_document_extensions_as_skipped(project, tmp_path: Path):
    root = tmp_path / "원본"
    root.mkdir()
    (root / "보고서.txt").write_text("a", encoding="utf-8")
    (root / "사진.jpg").write_bytes(b"\xff\xd8\xff")

    source_id = project.add_source(root)
    stats = scanner.scan_source(project, source_id, root)

    assert stats.found == 2
    assert stats.documents == 1
    status = dict(
        project.con.execute("SELECT filename, parse_status FROM documents").fetchall()
    )
    assert status["사진.jpg"] == "skipped"
    assert status["보고서.txt"] == "pending"
