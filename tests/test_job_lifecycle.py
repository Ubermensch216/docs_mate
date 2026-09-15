import os
import time
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

from app.db import Database
from app.ai.client import Health
from app.jobs import ask_task
from app.search.rag import Answer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def spin(qapp, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_cancelled_question_is_not_saved_or_delivered(tmp_path, qapp, monkeypatch):
    db = Database(tmp_path / "p.db")
    db.init()
    entered = threading.Event()
    def answer(db, question, client=None, scope=None):
        entered.set()
        deadline = time.monotonic() + 3
        while not client.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        return Answer(question, "late answer", False)
    monkeypatch.setattr(ask_task, "ask", answer)
    runner = ask_task.AskRunner(db.path)
    received, cancelled = [], []
    runner.done.connect(received.append)
    runner.cancelled.connect(lambda: cancelled.append(True))
    try:
        assert runner.start("synthetic")
        spin(qapp, entered.is_set)
        runner.cancel()
        spin(qapp, lambda: bool(cancelled))
        assert received == []
        assert db.con.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    finally:
        runner.stop()
        db.close()


def test_warmup_worker_runs_and_releases_its_thread(qapp, monkeypatch):
    calls = []
    class Client:
        def __init__(self, **kwargs): self.cancelled = False
        def cancel(self): self.cancelled = True
        def health(self): return Health(True, "ready", generation_ready=True)
        def generate_json(self, *args, **kwargs):
            calls.append(True)
            return {}, None, ""
    monkeypatch.setattr(ask_task, "OllamaClient", Client)
    runner = ask_task.WarmupRunner()
    try:
        assert runner.start()
        spin(qapp, lambda: runner._thread is None)
        assert calls == [True]
        assert not runner.start()
        runner.configure("other:tag", "embedding:latest")
        assert runner.start()
        spin(qapp, lambda: runner._thread is None)
        assert calls == [True, True]
        runner.stop()
    finally:
        runner.stop()


def test_summary_is_discarded_if_original_changes_during_generation(tmp_path, qapp, monkeypatch):
    from types import SimpleNamespace
    from app.jobs import ai_tasks
    db = Database(tmp_path / "p.db")
    db.init()
    sid = db.add_source(tmp_path / "source")
    doc = db.upsert_document(sid, {"path": "sample.txt", "filename": "sample.txt",
                                  "ext": ".txt", "parse_status": "ok", "hash": "old"})
    db.index_document(doc, "sample.txt", "sample.txt", "", "old body")
    class Client:
        def health(self): return Health(True, "ready", generation_ready=True)
    monkeypatch.setattr(ai_tasks, "OllamaClient", lambda **kwargs: Client())
    def summarize(*args):
        db.update_document(doc, hash="new")
        return SimpleNamespace(ok=True, summary="old summary", model="test", prompt_version="1")
    monkeypatch.setattr(ai_tasks.analyze, "summarize", summarize)
    worker = ai_tasks.SummaryWorker(db.path, doc)
    results = []
    worker.done.connect(lambda *args: results.append(args))
    worker.run()
    assert db.analysis(doc) is None
    assert results and "갱신" in results[0][2]
    db.close()
