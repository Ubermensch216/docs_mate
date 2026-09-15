import json
from pathlib import Path

from app.core import diagnostics
from app.tools import parse_report


def test_diagnostic_log_does_not_contain_document_or_exception_content(tmp_path):
    assert diagnostics.configure(tmp_path / "logs")
    secret = "PRIVATE-BODY-QUESTION-PATH"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        diagnostics.record("question.error", exc, doc_id=12, message=secret, path=secret)
    content = (tmp_path / "logs/diagnostic.log").read_text(encoding="utf-8")
    assert secret not in content
    assert "ValueError" in content and '"doc_id": 12' in content
    for handler in list(diagnostics._logger.handlers):
        handler.close()
        diagnostics._logger.removeHandler(handler)


def test_strict_selftest_fails_on_unreadable_document(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not a PDF")
    assert parse_report.main([str(tmp_path), "--strict"]) == 1


def test_strict_selftest_accepts_readable_document(tmp_path):
    (tmp_path / "sample.txt").write_text("본문 검증용 합성 문서", encoding="utf-8")
    assert parse_report.main([str(tmp_path), "--strict"]) == 0
