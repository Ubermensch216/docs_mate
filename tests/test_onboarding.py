"""시작 마법사 사전 점검 시험 (계획서 §23).

분석을 시작하기 전에 규모를 보여주는 화면이다. 여기서 중요한 것은 숫자
자체가 아니라 **못 읽는 파일을 숫자로 말하는 것**이다. 조용히 건너뛰면
사용자는 분석이 끝난 뒤에야 "왜 이 문서가 없지"를 겪고, 그때는 원인을
찾을 단서가 없다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.ui.views.onboarding import OnboardingView  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp, tmp_path: Path):
    db = Database(tmp_path / "p.db")
    db.init()
    widget = OnboardingView(db)
    yield widget
    widget.deleteLater()
    db.close()


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "자료"
    folder.mkdir()
    (folder / "계획.docx").write_text("x", encoding="utf-8")
    (folder / "사진.jpg").write_text("y", encoding="utf-8")   # 파서 없음
    return folder


def test_precheck_counts_files_and_analysable_documents(view, tmp_path: Path):
    view._folders = [_folder(tmp_path)]
    view._precheck()

    text = view.summary.text()
    assert "파일 2개" in text
    assert "분석 대상 문서 1건" in text     # .docx만 파서가 있다
    assert "접근 불가 0개" in text
    assert view.start.isEnabled()


def test_unreadable_files_are_counted_not_silently_skipped(view, tmp_path: Path, monkeypatch):
    folder = _folder(tmp_path)
    (folder / "잠긴보고서.docx").write_text("z", encoding="utf-8")

    real_stat = Path.stat

    def guarded(self, *args, **kwargs):
        if self.name == "잠긴보고서.docx":
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded)

    view._folders = [folder]
    view._precheck()

    text = view.summary.text()
    assert "접근 불가 1개" in text
    assert "권한이나 잠금" in text
    assert view.start.isEnabled()          # 나머지는 그대로 분석한다


def test_no_analysable_document_blocks_the_start_button(view, tmp_path: Path):
    folder = tmp_path / "빈자료"
    folder.mkdir()
    (folder / "사진.jpg").write_text("y", encoding="utf-8")

    view._folders = [folder]
    view._precheck()

    assert "분석할 수 있는 문서를 찾지 못했습니다" in view.summary.text()
    assert not view.start.isEnabled()
