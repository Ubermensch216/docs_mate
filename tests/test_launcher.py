"""시작 화면(프로젝트 선택) 시험.

두 번째 인수인계를 시작할 방법이 없던 것을 푸는 화면이다. 여기서 보는 것:
목록이 실제 프로젝트를 담는가, 고르면 그 프로젝트가 돌아오는가, 창에서
'전환'을 누르면 갈아타자는 신호가 나가는가.

네이티브 다이얼로그(QInputDialog/QFileDialog)는 몽키패치가 어긋나면 시험이
실제 모달 창을 띄우며 멈춘다 — settings.py에서 겪었다. 그래서 클래스가 아니라
**모듈에 붙은 이름**을 갈아 끼운다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database, open_project_at, registry  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.shell import MainWindow  # noqa: E402
from app.ui.views import launcher as launcher_view  # noqa: E402
from app.ui.views.launcher import LauncherDialog, _ProjectRow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


@pytest.fixture
def make_dialog(qapp):
    made: list[LauncherDialog] = []

    def build(root: Path, current: Path | None = None) -> LauncherDialog:
        dialog = LauncherDialog(root, current=current)
        made.append(dialog)
        return dialog

    yield build
    for dialog in made:
        dialog.deleteLater()


def _rows(dialog: LauncherDialog) -> list[_ProjectRow]:
    return dialog.findChildren(_ProjectRow)


def test_existing_projects_are_listed(make_dialog, tmp_path: Path):
    registry.create_project("총무팀 인수인계", tmp_path)
    registry.create_project("회계팀 인수인계", tmp_path)

    dialog = make_dialog(tmp_path)

    assert len(_rows(dialog)) == 2


def test_empty_state_when_nothing_has_been_created(make_dialog, tmp_path: Path):
    dialog = make_dialog(tmp_path)
    assert not _rows(dialog)


def test_choosing_a_project_returns_it_and_marks_it_opened(make_dialog, tmp_path: Path):
    entry = registry.create_project("총무팀 인수인계", tmp_path)

    dialog = make_dialog(tmp_path)
    dialog._open(entry)

    assert dialog.chosen is not None and dialog.chosen.path == entry.path
    assert registry.list_projects(tmp_path)[0].opened_at


def test_creating_a_project_from_the_dialog(make_dialog, tmp_path: Path, monkeypatch):
    class FakeInput:
        @staticmethod
        def getText(*_args, **_kwargs):
            return "2026년 인수인계", True

    monkeypatch.setattr(launcher_view, "QInputDialog", FakeInput)
    dialog = make_dialog(tmp_path)
    dialog._create()

    assert dialog.chosen is not None
    assert dialog.chosen.name == "2026년 인수인계"
    assert dialog.chosen.path.exists()


def test_cancelling_the_name_prompt_creates_nothing(make_dialog, tmp_path: Path, monkeypatch):
    class FakeInput:
        @staticmethod
        def getText(*_args, **_kwargs):
            return "", False

    monkeypatch.setattr(launcher_view, "QInputDialog", FakeInput)
    dialog = make_dialog(tmp_path)
    dialog._create()

    assert dialog.chosen is None
    assert not registry.list_projects(tmp_path)


def test_unreachable_project_is_not_opened(make_dialog, tmp_path: Path, monkeypatch):
    """USB가 빠진 프로젝트를 열면 빈 DB가 새로 생겨 '자료가 사라진' 것처럼 보인다."""
    base = tmp_path / "projects"
    outside = tmp_path / "usb" / "handover"
    open_project_at(outside).close()
    entry = registry.register(outside, name="USB 인수인계", root=base)

    import shutil

    shutil.rmtree(outside)
    warned: list[str] = []
    monkeypatch.setattr(
        launcher_view.QMessageBox, "warning",
        lambda *args, **kwargs: warned.append(args[2] if len(args) > 2 else "")
    )

    dialog = make_dialog(base)
    dialog._open(entry)

    assert dialog.chosen is None
    assert warned


def test_forgetting_keeps_the_data(make_dialog, tmp_path: Path, monkeypatch):
    entry = registry.create_project("총무팀 인수인계", tmp_path)
    monkeypatch.setattr(
        launcher_view.QMessageBox, "question",
        lambda *args, **kwargs: launcher_view.QMessageBox.StandardButton.Yes,
    )

    dialog = make_dialog(tmp_path)
    dialog._forget(entry)

    assert entry.path.exists()
    assert not _rows(dialog)


def test_deleting_a_project_removes_its_files(make_dialog, tmp_path: Path, monkeypatch):
    entry = registry.create_project("총무팀 인수인계", tmp_path)
    open_project_at(entry.path).close()
    monkeypatch.setattr(
        launcher_view.QMessageBox, "warning",
        lambda *args, **kwargs: launcher_view.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(launcher_view.QMessageBox, "information", lambda *a, **k: None)

    dialog = make_dialog(tmp_path)
    dialog._delete(entry)

    assert not entry.db_path.exists()
    assert not _rows(dialog)


def test_declining_the_delete_warning_keeps_everything(make_dialog, tmp_path: Path, monkeypatch):
    entry = registry.create_project("총무팀 인수인계", tmp_path)
    open_project_at(entry.path).close()
    monkeypatch.setattr(
        launcher_view.QMessageBox, "warning",
        lambda *args, **kwargs: launcher_view.QMessageBox.StandardButton.No,
    )

    dialog = make_dialog(tmp_path)
    dialog._delete(entry)

    assert entry.db_path.exists()
    assert len(_rows(dialog)) == 1


def test_the_open_project_cannot_be_deleted(make_dialog, tmp_path: Path):
    """열려 있는 DB를 지우려 하면 윈도우가 막는다. 누르기 전에 막는 편이 낫다."""
    entry = registry.create_project("총무팀 인수인계", tmp_path)
    open_project_at(entry.path).close()

    dialog = make_dialog(tmp_path, current=entry.path)
    row = _rows(dialog)[0]

    delete = next(
        b for b in row.findChildren(launcher_view.QPushButton) if b.text() == "완전 삭제"
    )
    assert not delete.isEnabled()


def test_window_asks_to_switch_when_the_project_button_is_pressed(qapp, tmp_path: Path,
                                                                 monkeypatch):
    monkeypatch.setattr(MainWindow, "_resume_if_pending", lambda self: None)
    db = Database(tmp_path / "p.db")
    db.init()
    window = MainWindow(db, project_name="총무팀 인수인계")
    asked: list[int] = []
    window.switch_requested.connect(lambda: asked.append(1))
    try:
        assert "총무팀 인수인계" in window.topbar.project.text()
        assert "총무팀 인수인계" in window.windowTitle()
        window.topbar.project.click()
        assert asked == [1]
    finally:
        window.shutdown()
        window.deleteLater()
        db.close()
