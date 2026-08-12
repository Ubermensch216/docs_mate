"""프로젝트 저장 경로 시험.

제품 이름을 바꿀 때(업무기억관 → 눈치코치) 폴더 이름도 함께 바꿨다.
그런데 이름만 갈아 끼우면 이미 분석을 끝내 둔 사용자가 자기 프로젝트를
통째로 잃는다. 옛 폴더가 남아 있으면 그대로 이어 쓰는지 못 박는다.
"""

from __future__ import annotations

from pathlib import Path

from app.db.repo import APP_DIR_NAME, LEGACY_APP_DIR_NAME, default_project_dir


def test_new_user_gets_the_current_app_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_project_dir() == tmp_path / APP_DIR_NAME / "projects"


def test_existing_user_keeps_using_the_legacy_directory(tmp_path: Path, monkeypatch):
    """이름을 바꿨다고 기존 분석 결과가 사라지면 안 된다."""
    legacy = tmp_path / LEGACY_APP_DIR_NAME / "projects" / "default"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_project_dir() == tmp_path / LEGACY_APP_DIR_NAME / "projects"


def test_current_directory_wins_when_both_exist(tmp_path: Path, monkeypatch):
    """이미 새 폴더로 옮겨 간 사용자는 새 폴더를 쓴다."""
    (tmp_path / LEGACY_APP_DIR_NAME / "projects").mkdir(parents=True)
    (tmp_path / APP_DIR_NAME / "projects").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_project_dir() == tmp_path / APP_DIR_NAME / "projects"


def test_app_directory_name_is_ascii():
    """폴더 이름에 한글을 쓰면 인코딩·경로 문제를 부른다 — start.bat에서 겪었다."""
    assert APP_DIR_NAME.isascii()
