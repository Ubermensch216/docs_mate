"""설정 화면의 연결 확인을 UI 스레드에서 분리한다."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..ai.client import Health


class _Signals(QObject):
    done = Signal(object)


class StatusTask(QRunnable):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.signals = _Signals()

    def run(self):
        try:
            health = self.client.health()
        except Exception:
            health = Health(False, "AI 상태를 확인하지 못했습니다. 다시 확인해 주세요.")
        self.signals.done.emit(health)


def check_status(client, receiver):
    task = StatusTask(client)
    task.signals.done.connect(receiver)
    QThreadPool.globalInstance().start(task)
    return task
