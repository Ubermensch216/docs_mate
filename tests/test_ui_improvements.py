"""전체 자료 탐색과 비동기 설정의 사용자 계약."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QApplication

from app.db import Database
from app.search.documents import find_documents
from app.ui.views.documents import DocumentsView
from app.ui.views.settings import SettingsDialog
from app.ai.client import Health
from app.ai.settings import model_names, save_models, project_client


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "p.db")
    db.init()
    source = db.add_source(tmp_path / "source")
    with db.con:
        db.con.executemany(
            "INSERT INTO documents(source_id,path,filename,ext,parse_status) VALUES (?,?,?,?,?)",
            [(source, str(tmp_path / f"보고서{i:04}.txt"), f"보고서{i:04}.txt", ".txt", "ok")
             for i in range(603)],
        )
    yield db
    db.close()


def test_all_documents_and_search_results_can_be_paged(db):
    for term in ("", "보고서"):
        seen = []
        for page in range(7):
            result = find_documents(db, term, page=page)
            assert result.total == 603
            seen.extend(row["id"] for row in result.rows)
        assert len(seen) == len(set(seen)) == 603


def test_short_body_search_and_literal_fts_quotes(db):
    db.index_document(1, "보고서0000.txt", "", "", '금액 5%와 값_1, "예산"')
    assert find_documents(db, "5%").total == 1
    assert find_documents(db, '"예산"').total == 1
    assert find_documents(db, "없는표현").total == 0


def test_page_selection_and_empty_search_reset(db, qapp):
    view = DocumentsView(db)
    view.show()
    view._turn_page(6)
    assert view.table.rowCount() == 3
    assert "601" in view.summary.text()
    assert view._current_id == view.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    view.search.setText("존재하지않는문서")
    view.refresh()
    assert not view.no_results.isHidden()
    assert view.empty.isHidden()
    assert not view.open_file.isEnabled()
    view._reset_search()
    assert view._page == 0 and view.table.rowCount() == 100
    view.close()
    view.deleteLater()


def test_settings_does_not_call_ai_until_system_is_open(db, qapp, monkeypatch):
    calls = []
    class SlowClient:
        def __init__(self, **kwargs): pass
        def health(self):
            calls.append(True)
            time.sleep(0.2)
            return Health(True, "연결됨", ["local:small", "embed:latest"], True, True)
    monkeypatch.setattr("app.ui.views.settings.OllamaClient", SlowClient)
    dialog = SettingsDialog(db)
    assert calls == []
    dialog.go("system")
    # 반환 시 완료되지 않은 작업이 남아 있어도 다른 탭을 조작할 수 있다.
    assert dialog._ai_status_task is not None
    dialog.go("appearance")
    assert dialog.stack.currentWidget() is dialog.panes["appearance"]
    QThreadPool.globalInstance().waitForDone(2000)
    qapp.processEvents()
    assert calls == [True]
    assert dialog.gen_model.findText("local:small") >= 0
    dialog.gen_model.setCurrentText("local:small")
    dialog.embed_model.setCurrentText("embed:latest")
    dialog._save_models()
    assert model_names(db) == ("local:small", "embed:latest")
    assert project_client(db).gen_model == "local:small"
    QThreadPool.globalInstance().waitForDone(2000)
    qapp.processEvents()
    dialog.close()
    dialog.deleteLater()
