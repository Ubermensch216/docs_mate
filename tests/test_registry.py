"""프로젝트 레지스트리 시험.

이 목록이 틀리면 사용자는 "내 인수인계가 사라졌다"고 본다. 그래서 발견
(레지스트리 없이도 폴더에서 찾아내기)과 보존(빼기가 지우기가 아님)을
못 박는다.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.db import open_project_at, registry


def _make(root: Path, folder: str) -> Path:
    path = root / folder
    db = open_project_at(path)
    db.close()
    return path


def test_create_makes_folder_and_lists_it(tmp_path: Path):
    entry = registry.create_project("2026년 총무팀 인수인계", tmp_path)

    assert entry.path.exists()
    assert entry.name == "2026년 총무팀 인수인계"
    assert [item.name for item in registry.list_projects(tmp_path)] == [entry.name]


def test_folder_name_is_ascii_even_for_korean_project_names(tmp_path: Path):
    """경로에 한글이 섞이면 바깥 도구(start.bat·PyInstaller)에서 사고가 난다."""
    entry = registry.create_project("총무팀 인수인계", tmp_path)
    assert entry.path.name.isascii()


def test_same_name_twice_does_not_collide(tmp_path: Path):
    first = registry.create_project("인수인계", tmp_path)
    second = registry.create_project("인수인계", tmp_path)

    assert first.path != second.path
    assert len(registry.list_projects(tmp_path)) == 2


def test_existing_project_folders_are_discovered_without_a_registry(tmp_path: Path):
    """이 기능이 생기기 전에 만든 프로젝트(대개 default)가 목록에서 빠지면 안 된다."""
    _make(tmp_path, "default")

    listed = registry.list_projects(tmp_path)

    assert [item.path.name for item in listed] == ["default"]
    assert not registry.registry_path(tmp_path).exists()


def test_broken_registry_file_does_not_hide_projects(tmp_path: Path):
    _make(tmp_path, "default")
    registry.registry_path(tmp_path).write_text("{ 깨진 파일", encoding="utf-8")

    assert [item.path.name for item in registry.list_projects(tmp_path)] == ["default"]


def test_recently_opened_comes_first(tmp_path: Path):
    old = registry.create_project("먼저", tmp_path)
    registry.create_project("나중", tmp_path)
    registry.touch(old.path, tmp_path)

    assert registry.list_projects(tmp_path)[0].path == old.path


def test_rename_changes_the_label_but_not_the_folder(tmp_path: Path):
    entry = registry.create_project("옛 이름", tmp_path)
    registry.rename(entry.path, "새 이름", tmp_path)

    listed = registry.list_projects(tmp_path)[0]
    assert listed.name == "새 이름"
    assert listed.path == entry.path
    assert entry.path.exists()


def test_forget_removes_from_the_list_but_keeps_the_data(tmp_path: Path):
    """빼기는 지우기가 아니다 — 분석 결과 폴더는 그대로 남아야 한다."""
    outside = tmp_path / "usb" / "handover"
    open_project_at(outside).close()
    entry = registry.register(outside, name="USB 인수인계", root=tmp_path)

    registry.forget(entry.path, tmp_path)

    assert entry.db_path.exists()
    assert entry.path not in [item.path for item in registry.list_projects(tmp_path)]


def test_external_folder_is_marked_and_survives_reload(tmp_path: Path):
    base = tmp_path / "projects"
    outside = tmp_path / "usb" / "handover"
    open_project_at(outside).close()
    registry.register(outside, name="USB 인수인계", root=base)

    listed = registry.list_projects(base)[0]
    assert listed.external
    assert listed.name == "USB 인수인계"

    payload = json.loads(registry.registry_path(base).read_text(encoding="utf-8"))
    assert payload["projects"][0]["path"] == str(outside)


def test_missing_external_folder_stays_listed_as_unreachable(tmp_path: Path):
    """USB를 뽑았다고 목록에서 조용히 사라지면 사용자는 잃은 줄 안다."""
    base = tmp_path / "projects"
    outside = tmp_path / "usb" / "handover"
    open_project_at(outside).close()
    registry.register(outside, name="USB 인수인계", root=base)

    shutil.rmtree(outside)          # USB를 뽑았다

    listed = [item for item in registry.list_projects(base) if item.path == outside]
    assert listed and not listed[0].exists
