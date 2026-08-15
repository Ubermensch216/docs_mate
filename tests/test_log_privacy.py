"""로그에 원문이 새지 않는지 검사한다 (계획서 §30-4, SEC-005).

`Database.audit()`은 "식별자와 결과만 기록한다"고 선언해 두었다. 선언은
지켜지는 동안에만 참이고, 새 기능이 붙을 때 detail에 본문 한 조각을 담는
것은 아주 쉽다 — 그러면 감사 로그를 CSV로 내보내는 순간(설정 화면) 공직
자료 본문이 통째로 파일로 빠져나간다.

원칙을 검사로 바꾼다. 알아볼 수 있는 표식을 문서 본문에 심고 파이프라인을
실제로 돌린 뒤, 그 표식이 로그 어디에도 없는지 본다.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import Database  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402

# 본문에만 심는 표식. 파일명·경로에는 절대 넣지 않는다 — 경로는 식별자라
# 로그에 남는 것이 정상이고, 그것까지 금지하면 시험이 거짓 경보를 낸다.
SECRET = "대외비주민등록번호9901011234567"
AUDIT_DETAIL_LIMIT = 200


@pytest.fixture(scope="session", autouse=True)
def qapp():
    yield QApplication.instance() or QCoreApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    """AI는 닫힌 포트를 물린다. 로그 규칙은 AI가 없어도 지켜져야 한다."""
    from app.ai import OllamaClient
    import app.jobs.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "OllamaClient",
        lambda *a, **k: OllamaClient(base_url="http://127.0.0.1:1"),
    )


@pytest.fixture
def analysed(tmp_path: Path):
    """표식이 든 문서를 실제로 훑고 읽은 프로젝트."""
    source = tmp_path / "자료"
    source.mkdir()
    (source / "2025_행정사무감사_요구자료.txt").write_text(
        f"2025년 행정사무감사 요구자료\n담당자 정보: {SECRET}\n제출 기한 2025-09-30",
        encoding="utf-8",
    )

    db = Database(tmp_path / "project.db")
    db.init()
    db.add_source(source)
    db.close()
    Pipeline(tmp_path / "project.db").run()

    fresh = Database(tmp_path / "project.db")
    yield fresh
    fresh.close()


def _audit_text(db: Database) -> str:
    rows = db.con.execute(
        "SELECT at, action, target, detail, result FROM audit_logs"
    ).fetchall()
    return "\n".join(
        " ".join(str(row[key] or "") for key in row.keys()) for row in rows
    )


def test_the_marker_really_made_it_into_the_analysis(analysed: Database):
    """이 시험이 무엇도 확인하지 못하는 상태(본문을 못 읽음)를 먼저 배제한다."""
    body = analysed.con.execute(
        "SELECT text FROM document_sections"
    ).fetchall()
    assert any(SECRET in row["text"] for row in body), "표식이 본문에 없다 — 시험이 무의미하다"
    assert analysed.con.execute("SELECT COUNT(*) AS n FROM audit_logs").fetchone()["n"] > 0


def test_document_body_never_reaches_the_audit_log(analysed: Database):
    assert SECRET not in _audit_text(analysed)


def test_document_body_never_reaches_the_meta_table(analysed: Database):
    rows = analysed.con.execute("SELECT key, value FROM meta").fetchall()
    assert not any(SECRET in str(row["value"] or "") for row in rows)


def test_audit_details_stay_short_enough_to_be_identifiers(analysed: Database):
    """길이 자체가 방어선이다. 본문 한 조각이 들어오면 반드시 길어진다."""
    long_ones = [
        (row["action"], len(row["detail"] or ""))
        for row in analysed.con.execute("SELECT action, detail FROM audit_logs").fetchall()
        if len(row["detail"] or "") > AUDIT_DETAIL_LIMIT
    ]
    assert not long_ones, f"detail이 너무 길다(본문 유출 의심): {long_ones}"


def test_exported_audit_csv_carries_no_body(analysed: Database, tmp_path: Path):
    """내보내기가 실제 유출 경로다 — 파일로 나간 뒤에는 거둘 수 없다."""
    from app.ui.views.settings import SettingsDialog

    out = tmp_path / "audit.csv"
    count = SettingsDialog._write_audit_csv(_Fake(analysed), str(out))

    assert count > 0
    text = out.read_text(encoding="utf-8-sig")
    assert SECRET not in text
    with open(out, encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == ["시각", "동작", "대상", "상세", "결과"]


def test_corrections_keep_the_values_a_person_typed_not_the_body(analysed: Database):
    """교정 기록에는 사람이 정한 값만 남는다. 되돌리기에 필요한 최소한이다."""
    analysed.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    analysed.rename_task(1, "행정사무감사 요구자료")
    analysed.edit_task_description(1, "해마다 9월에 준비합니다")

    rows = analysed.con.execute(
        "SELECT before_val, after_val FROM corrections"
    ).fetchall()
    joined = " ".join(f"{row['before_val']} {row['after_val']}" for row in rows)
    assert "행정사무감사 요구자료" in joined      # 되돌릴 수 있어야 한다
    assert SECRET not in joined
    assert SECRET not in _audit_text(analysed)


def test_storage_outside_the_user_account_is_flagged(tmp_path: Path, monkeypatch):
    """`--data`로 공유 폴더를 지정하면 계정 권한 보호가 통째로 사라진다."""
    from app.ui.views.settings import storage_warning

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "사용자"))
    (tmp_path / "사용자").mkdir()

    assert not storage_warning(tmp_path / "사용자" / "projects" / "project.db")
    assert "계정 폴더 밖" in storage_warning(tmp_path / "공용" / "project.db")
    assert "네트워크 공유" in storage_warning(r"\\부서서버\공유\project.db")


class _Fake:
    """`_write_audit_csv`는 db와 audit만 쓴다. 창을 띄우지 않고 그 부분만 부른다."""

    def __init__(self, db: Database):
        self.db = db
