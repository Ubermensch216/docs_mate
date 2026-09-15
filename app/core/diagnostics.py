"""본문·질문·경로를 기록하지 않는 회전 진단 로그. 감사 기록과 별개다."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

_logger = logging.getLogger("nunchicoach.diagnostics")
_logger.propagate = False
_logger.setLevel(logging.INFO)
_FIELDS = {"stage", "doc_id", "count", "elapsed_ms", "version"}
_EVENTS = {"app.start", "app.stop", "app.unhandled", "pipeline.start", "pipeline.stop",
           "pipeline.error", "question.error", "summary.error"}


def configure(directory: Path) -> bool:
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        handler.close()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / "diagnostic.log", maxBytes=512_000,
                                      backupCount=3, encoding="utf-8")
    except OSError:
        _logger.addHandler(logging.NullHandler())
        return False
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    return True


def record(event: str, error: BaseException | None = None, **fields) -> None:
    if event not in _EVENTS:
        return
    item = {"event": event}
    # 자유문을 받지 않고 예외도 종류와 코드 위치만 남긴다.
    for key in _FIELDS & fields.keys():
        value = fields[key]
        if key in {"doc_id", "count", "elapsed_ms"} and isinstance(value, (int, float)):
            item[key] = value
        elif key == "version" and isinstance(value, str) and len(value) < 32:
            item[key] = value
        elif key == "stage" and value in {"scan", "parse", "embed", "ask", "summary"}:
            item[key] = value
    if error is not None:
        item["error_type"] = type(error).__name__
        frames = []
        tb = error.__traceback__
        while tb is not None:
            frames.append({"file": Path(tb.tb_frame.f_code.co_filename).name,
                           "line": tb.tb_lineno, "function": tb.tb_frame.f_code.co_name})
            tb = tb.tb_next
        item["frames"] = frames[-8:]
    _logger.info(json.dumps(item, ensure_ascii=False))


def exception_hook(kind, error, traceback):
    record("app.unhandled", error)
    sys.__excepthook__(kind, error, traceback)
