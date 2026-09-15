"""화면에서 부르는 단발 AI 작업.

문서 1만 건을 미리 요약하면 27시간이 걸린다(실측). 그래서 요약은 사용자가
문서를 열었을 때 그 문서 하나만 만든다. 9~10초는 기다릴 만하고, 무엇보다
쓰지도 않을 요약에 27시간을 쓰지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ..ai import OllamaClient, analyze
from ..ai.settings import project_client
from ..db import Database
from ..core import diagnostics


class SummaryWorker(QObject):
    """문서 하나를 요약해 저장한다. 워커 스레드에서 돈다."""

    done = Signal(int, str, str)   # doc_id, summary, error

    def __init__(self, db_path: Path | str, doc_id: int):
        super().__init__()
        self._db_path = Path(db_path)
        self._doc_id = doc_id
        self._client = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._client is not None:
            self._client.cancel()

    def run(self) -> None:
        db = Database(self._db_path)
        try:
            row = db.con.execute(
                "SELECT i.body, d.hash FROM document_index i JOIN documents d ON d.id=i.doc_id WHERE doc_id = ?", (self._doc_id,)
            ).fetchone()
            if row is None or not row["body"]:
                self.done.emit(self._doc_id, "", "읽어 둔 본문이 없습니다")
                return

            client = self._client = project_client(db, OllamaClient)
            if self._cancelled:
                client.cancel()
            health = client.health()
            if not health.generation_ready:
                self.done.emit(self._doc_id, "", health.message)
                return
            result = analyze.summarize(client, self._doc_id, row["body"])
            if self._cancelled:
                self.done.emit(self._doc_id, "", "요약을 취소했습니다.")
                return
            if not result.ok:
                self.done.emit(self._doc_id, "", result.error or "요약 실패")
                return
            current = db.document(self._doc_id)
            if current is None or current["hash"] != row["hash"]:
                self.done.emit(self._doc_id, "", "요약 중 자료가 갱신되었습니다. 다시 시도해 주세요.")
                return

            db.save_analysis(
                self._doc_id,
                summary=result.summary,
                model=result.model,
                prompt_version=result.prompt_version,
                status="proposed",
            )
            db.audit("ai.summarize", str(self._doc_id), result.prompt_version, "ok")
            self.done.emit(self._doc_id, result.summary or "", "")
        except Exception as exc:  # 워커가 조용히 죽으면 버튼이 영원히 돈다
            diagnostics.record("summary.error", exc, doc_id=self._doc_id)
            self.done.emit(self._doc_id, "", f"{type(exc).__name__}: {exc}")
        finally:
            db.close()


class SummaryRunner(QObject):
    """UI 쪽 손잡이. 한 번에 하나만 돌린다."""

    done = Signal(int, str, str)

    def __init__(self, db_path: Path | str, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._thread: QThread | None = None
        self._worker: SummaryWorker | None = None

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, doc_id: int) -> bool:
        if self._thread is not None:
            return False
        self._thread = QThread()
        self._worker = SummaryWorker(self._db_path, doc_id)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._finish)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        return True

    def stop(self, wait_ms: int = 5000) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)

    def _finish(self, doc_id: int, summary: str, error: str) -> None:
        thread, worker = self._thread, self._worker
        self._thread, self._worker = None, None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self.done.emit(doc_id, summary, error)
