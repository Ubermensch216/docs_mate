"""질문 화면에서 부르는 단발 AI 작업.

RAG는 임베딩 + 생성 호출을 순차로 하므로 몇 초가 걸린다. UI를 막지 않도록
워커 스레드에서 돈다. SummaryRunner(ai_tasks.py)와 같은 모양이다 — 한 번에
하나만 돌리고, 창이 닫혀도 스레드가 살아남지 않게 한다.
"""

from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Qt

from ..ai.client import OllamaClient
from ..ai.settings import project_client
from ..db import Database
from ..core import diagnostics
from ..search.rag import Answer, ask


class AskWorker(QObject):
    done = Signal(object)   # Answer

    def __init__(self, db_path: Path | str, question: str, scope=None):
        super().__init__()
        self._db_path = Path(db_path)
        self._question = question
        self._scope = scope
        self.cancelled = False
        self._client = None
        self._state_lock = threading.Lock()
        self._completed = False

    def cancel(self):
        with self._state_lock:
            if self._completed:
                return
            self.cancelled = True
        if self._client is not None:
            self._client.cancel()

    def run(self) -> None:
        db = Database(self._db_path)
        try:
            self._client = project_client(db, OllamaClient)
            if self.cancelled:
                self._client.cancel()
            answer = ask(db, self._question, client=self._client, scope=self._scope)
            with self._state_lock:
                if self.cancelled:
                    self.done.emit(Answer(self._question, "", True, error="질문을 취소했습니다."))
                    return
                db.save_question(
                    answer.question, answer.text, _citations_json(answer),
                    answer.withheld, answer.model, filters=_filters_json(answer),
                )
                db.audit("question.ask", detail=f"{len(self._question)}자",
                         result="withheld" if answer.withheld else "ok")
                self._completed = True
        except Exception as exc:   # 워커가 조용히 죽으면 화면이 영원히 돈다
            diagnostics.record("question.error", exc)
            answer = Answer(
                question=self._question, text="", withheld=True,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            db.close()
        self.done.emit(answer)


class AskRunner(QObject):
    done = Signal(object)
    cancelled = Signal()

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

    def start(self, question: str, scope=None) -> bool:
        if self._thread is not None:
            return False
        self._thread = QThread()
        self._worker = AskWorker(self._db_path, question, scope)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._finish)
        self._worker.done.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        self._worker.done.connect(self._worker.deleteLater)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        return True

    def stop(self, wait_ms: int = 5000) -> None:
        self.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _finish(self, answer: Answer) -> None:
        thread, worker = self._thread, self._worker
        self._thread, self._worker = None, None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None and worker.cancelled:
            self.cancelled.emit()
        else:
            self.done.emit(answer)


class WarmupRunner(QObject):
    """질문 화면에 들어올 때 생성 모델을 미리 올려 둔다.

    실측: 첫 질문만 40.8초, 이후 11~16초였다. 차이는 거의 전부 모델 적재다
    — 임베딩 모델과 생성 모델이 메모리에서 서로를 밀어내기 때문에(ai/client.py의
    KEEP_ALIVE 주석 참고) 질문 화면에서 처음 부를 때 다시 올라온다.

    사용자가 질문을 타이핑하는 몇 초 동안 미리 올려 두면 그 지연이 보이지
    않는다. 실패해도 조용히 넘어간다 — 예열은 편의일 뿐이고, 안 되면
    평소처럼 첫 질문이 조금 느릴 뿐이다.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker = None
        self._done = False
        self._models = None
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    def configure(self, generation: str, embedding: str) -> None:
        if self._models != (generation, embedding):
            self.stop()
            self._models = (generation, embedding)
            self._done = False

    def start(self) -> bool:
        """한 번만 돈다. 화면을 오갈 때마다 모델을 다시 부르지 않는다."""
        if self._done or (self._thread is not None and self._thread.isRunning()):
            return False
        self._done = True

        thread = QThread()
        worker = _WarmupWorker(self._models)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._complete)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def stop(self, wait_ms: int = 5000) -> None:
        if self._worker is not None:
            self._worker.client.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)

    def _complete(self):
        if self.sender() is not None and self.sender() is not self._thread:
            return
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    @property
    def running(self):
        return self._thread is not None and self._thread.isRunning()


class _WarmupWorker(QObject):
    finished = Signal()

    def __init__(self, models=None):
        super().__init__()
        self.client = OllamaClient(gen_model=models[0], embed_model=models[1]) if models else OllamaClient()

    def run(self) -> None:
        try:
            client = self.client
            if client.health().generation_ready:
                # 가장 짧은 호출로 모델만 올린다. 결과는 쓰지 않는다.
                client.generate_json("준비", _WARMUP_SCHEMA, num_predict=1)
        except Exception:
            pass   # 예열 실패는 사용자에게 알릴 일이 아니다
        self.finished.emit()


_WARMUP_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


def _citations_json(answer: Answer) -> str:
    import json

    return json.dumps(
        [
            {"doc_id": c.doc_id, "filename": c.filename, "locator": c.locator}
            for c in answer.citations
        ],
        ensure_ascii=False,
    )


def _filters_json(answer: Answer) -> str | None:
    """질문에서 규칙으로 뽑아낸 범위. 나중에 '왜 이렇게 좁혔는지' 확인용이다."""
    import json

    if not answer.inferred_years:
        return None
    return json.dumps({"years": answer.inferred_years}, ensure_ascii=False)
