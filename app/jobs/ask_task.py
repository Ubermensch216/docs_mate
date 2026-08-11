"""질문 화면에서 부르는 단발 AI 작업.

RAG는 임베딩 + 생성 호출을 순차로 하므로 몇 초가 걸린다. UI를 막지 않도록
워커 스레드에서 돈다. SummaryRunner(ai_tasks.py)와 같은 모양이다 — 한 번에
하나만 돌리고, 창이 닫혀도 스레드가 살아남지 않게 한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ..db import Database
from ..search.rag import Answer, ask


class AskWorker(QObject):
    done = Signal(object)   # Answer

    def __init__(self, db_path: Path | str, question: str):
        super().__init__()
        self._db_path = Path(db_path)
        self._question = question

    def run(self) -> None:
        db = Database(self._db_path)
        try:
            answer = ask(db, self._question)
            db.save_question(
                answer.question,
                answer.text,
                _citations_json(answer),
                answer.withheld,
                answer.model,
            )
            db.audit(
                "question.ask", detail=f"{len(self._question)}자",
                result="withheld" if answer.withheld else "ok",
            )
        except Exception as exc:   # 워커가 조용히 죽으면 화면이 영원히 돈다
            answer = Answer(
                question=self._question, text="", withheld=True,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            db.close()
        self.done.emit(answer)


class AskRunner(QObject):
    done = Signal(object)

    def __init__(self, db_path: Path | str, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._thread: QThread | None = None
        self._worker: AskWorker | None = None

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, question: str) -> bool:
        if self.running:
            return False
        self._thread = QThread()
        self._worker = AskWorker(self._db_path, question)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._finish)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        return True

    def stop(self, wait_ms: int = 5000) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)

    def _finish(self, answer: Answer) -> None:
        thread, worker = self._thread, self._worker
        self._thread, self._worker = None, None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self.done.emit(answer)


def _citations_json(answer: Answer) -> str:
    import json

    return json.dumps(
        [
            {"doc_id": c.doc_id, "filename": c.filename, "locator": c.locator}
            for c in answer.citations
        ],
        ensure_ascii=False,
    )
