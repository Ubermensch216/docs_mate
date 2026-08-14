"""프로젝트 레지스트리 — 이 PC에 어떤 인수인계가 있는지 아는 유일한 자리.

프로젝트 하나 = 폴더 하나 = DB 파일 하나(repo.py)라는 규칙은 그대로 둔다.
레지스트리는 그 폴더들 **위에** 얹는 얇은 목록일 뿐이고, 사람이 읽는 이름과
마지막으로 연 날짜만 따로 안다. 그래서 레지스트리 파일이 사라져도 프로젝트는
멀쩡하다 — 폴더를 훑어 다시 찾아낸다(`list_projects`의 discovery).

파일은 JSON이다. SQLite를 하나 더 두면 스키마 마이그레이션 부담이 두 배가
되는데, 여기 담기는 것은 몇 줄짜리 목록이라 그 값을 하지 못한다.

폴더 이름은 ASCII로만 만든다. 표시 이름은 한글이어도 되지만 경로에 한글이
섞이면 start.bat·PyInstaller 같은 바깥 도구에서 인코딩 사고가 난다
(APP_DIR_NAME을 ASCII로 못 박아 둔 것과 같은 이유).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .repo import default_project_dir

REGISTRY_NAME = "registry.json"
DB_NAME = "project.db"


@dataclass(frozen=True)
class ProjectEntry:
    """목록에 보이는 프로젝트 하나."""

    name: str            # 사람이 읽는 이름 (한글 가능)
    path: Path           # 프로젝트 폴더
    created_at: str = ""
    opened_at: str = ""
    external: bool = False   # 기본 저장 위치 바깥에 있는 폴더인가

    @property
    def db_path(self) -> Path:
        return self.path / DB_NAME

    @property
    def exists(self) -> bool:
        """지금 이 프로젝트에 닿을 수 있는가.

        DB 파일이 아니라 폴더를 본다 — 막 만든 프로젝트는 아직 DB가 없고
        (처음 열 때 만들어진다), 그 사이에 목록에서 사라지면 안 된다.
        """
        return self.path.exists()

    @property
    def analyzed(self) -> bool:
        """이미 분석 결과가 담긴 프로젝트인가."""
        return self.db_path.exists()

    def opened_text(self) -> str:
        return _date_text(self.opened_at) or "연 적 없음"


def registry_path(root: Path | None = None) -> Path:
    """projects 폴더 **옆**이 아니라 안에 둔다 — 폴더째 옮겨도 목록이 따라간다."""
    return (root or default_project_dir()) / REGISTRY_NAME


def list_projects(root: Path | None = None) -> list[ProjectEntry]:
    """최근에 연 순서로 프로젝트 목록.

    레지스트리에 적힌 것 + 저장 폴더에서 발견한 것을 합친다. 발견을 함께
    하는 이유: 이 기능이 생기기 전에 만든 프로젝트(대개 `default`)와, 폴더만
    복사해 온 프로젝트가 목록에서 통째로 빠지면 사용자는 자기 분석 결과가
    사라진 것으로 본다.
    """
    base = root or default_project_dir()
    entries: dict[Path, ProjectEntry] = {}

    for item in _load(base):
        entries[item.path] = item

    for folder in _discover(base):
        if folder not in entries:
            entries[folder] = ProjectEntry(name=folder.name, path=folder)

    # 저장 위치 안에 있던 프로젝트가 사라졌다면 사용자가 폴더째 지운 것이니
    # 목록에서도 뺀다. 바깥(USB·공유 폴더)은 반대다 — 지금 안 보이는 것과
    # 없어진 것이 다르므로 '닿을 수 없음'으로 남겨 둔다.
    alive = [entry for entry in entries.values() if entry.exists or entry.external]
    alive.sort(key=lambda e: (e.opened_at or "", e.name), reverse=True)
    return alive


def create_project(name: str, root: Path | None = None) -> ProjectEntry:
    """새 인수인계를 만든다. 폴더까지 만들고 레지스트리에 적는다."""
    base = root or default_project_dir()
    label = name.strip() or "새 인수인계"
    folder = base / _unique_slug(label, base)
    folder.mkdir(parents=True, exist_ok=True)

    now = _now()
    entry = ProjectEntry(name=label, path=folder, created_at=now, opened_at=now)
    _save(base, _replace_entry(_load(base), entry))
    return entry


def register(path: Path, name: str = "", root: Path | None = None) -> ProjectEntry:
    """이미 있는 폴더를 목록에 넣는다 (기존 프로젝트 열기).

    저장 위치 바깥의 폴더도 받는다. USB나 공유 폴더에 있는 프로젝트를
    그대로 여는 것이 인수인계 현장에서는 흔한 일이다.
    """
    base = root or default_project_dir()
    folder = Path(path)
    known = {entry.path: entry for entry in _load(base)}
    previous = known.get(folder)

    entry = ProjectEntry(
        name=(name.strip() or (previous.name if previous else folder.name)),
        path=folder,
        created_at=(previous.created_at if previous else _now()),
        opened_at=_now(),
        external=not _is_inside(folder, base),
    )
    _save(base, _replace_entry(_load(base), entry))
    return entry


def touch(path: Path, root: Path | None = None) -> None:
    """이 프로젝트를 방금 열었다고 적는다 — 목록의 정렬 기준이다."""
    base = root or default_project_dir()
    folder = Path(path)
    known = {entry.path: entry for entry in _load(base)}
    entry = known.get(folder) or ProjectEntry(
        name=folder.name, path=folder, created_at=_now(),
        external=not _is_inside(folder, base),
    )
    _save(base, _replace_entry(_load(base), replace(entry, opened_at=_now())))


def rename(path: Path, name: str, root: Path | None = None) -> None:
    """표시 이름만 바꾼다. 폴더는 건드리지 않는다 — 경로가 바뀌면 열려 있는
    DB, 백업, 사용자가 적어 둔 경로가 전부 어긋난다."""
    base = root or default_project_dir()
    folder = Path(path)
    known = {entry.path: entry for entry in _load(base)}
    entry = known.get(folder) or ProjectEntry(name=folder.name, path=folder)
    _save(base, _replace_entry(_load(base), replace(entry, name=name.strip() or entry.name)))


def forget(path: Path, root: Path | None = None) -> None:
    """목록에서만 뺀다. 폴더와 DB는 그대로 둔다.

    분석 결과를 지우는 일과 목록에서 감추는 일은 다르다. 지우기는 WAL·백업
    파일까지 함께 다뤄야 하는 별도 작업이다.
    """
    base = root or default_project_dir()
    folder = Path(path)
    remaining = [entry for entry in _load(base) if entry.path != folder]
    _save(base, remaining)


# ── 내부 ────────────────────────────────────────────────────────────
def _load(base: Path) -> list[ProjectEntry]:
    path = base / REGISTRY_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 목록 파일이 깨졌다고 앱이 못 뜨면 안 된다. 발견으로 복구된다.
        return []
    items = raw.get("projects", []) if isinstance(raw, dict) else []
    out: list[ProjectEntry] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        folder = Path(item["path"])
        out.append(
            ProjectEntry(
                name=str(item.get("name") or folder.name),
                path=folder,
                created_at=str(item.get("created_at") or ""),
                opened_at=str(item.get("opened_at") or ""),
                external=bool(item.get("external")) or not _is_inside(folder, base),
            )
        )
    return out


def _save(base: Path, entries: list[ProjectEntry]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "projects": [
            {
                "name": entry.name,
                "path": str(entry.path),
                "created_at": entry.created_at,
                "opened_at": entry.opened_at,
                "external": entry.external,
            }
            for entry in entries
        ],
    }
    (base / REGISTRY_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _replace_entry(entries: list[ProjectEntry], entry: ProjectEntry) -> list[ProjectEntry]:
    kept = [item for item in entries if item.path != entry.path]
    return [*kept, entry]


def _discover(base: Path) -> list[Path]:
    if not base.exists():
        return []
    try:
        children = sorted(base.iterdir())
    except OSError:
        return []
    return [child for child in children if (child / DB_NAME).exists()]


def _unique_slug(name: str, base: Path) -> str:
    stem = _slug(name)
    candidate = stem
    index = 2
    while (base / candidate).exists():
        candidate = f"{stem}-{index}"
        index += 1
    return candidate


def _slug(name: str) -> str:
    """한글 이름에서 ASCII 폴더 이름을 만든다.

    한글은 음역하지 않는다 — 음역 규칙을 하나 더 들이는 값이 없고, 결과가
    사용자가 적은 이름과 어차피 다르다. 남는 글자가 없으면 만든 날짜를 쓴다.
    """
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return ascii_part[:40] or datetime.now().strftime("project-%Y%m%d-%H%M%S")


def _is_inside(folder: Path, base: Path) -> bool:
    try:
        folder.relative_to(base)
    except ValueError:
        return False
    return True


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _date_text(stamp: str) -> str:
    if not stamp:
        return ""
    return stamp.split("T")[0]
