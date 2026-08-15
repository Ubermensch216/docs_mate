"""품질 계측 도구 시험 — 업무 분류 채점기와 파서 회귀 시험대 (계획서 A-5).

두 도구 다 "고치기 전에 재라"를 위한 자다. 자가 틀리면 개선이 아니라 운을
측정하게 되므로(rag_report에서 겪었다), 자 자체를 먼저 시험한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.db import Database  # noqa: E402
from app.tools import cluster_report, parse_bench  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "sample_tree"

needs_fixtures = pytest.mark.skipif(
    not FIXTURES.is_dir(), reason="python -m app.tools.make_fixtures 로 표본을 먼저 생성하세요"
)


# ── 업무 분류 채점 ──────────────────────────────────────────────────

def test_perfect_clustering_scores_one():
    truth = {1: "감사", 2: "감사", 3: "예산", 4: "예산"}
    predicted = {1: "A", 2: "A", 3: "B", 4: "B"}      # 이름이 달라도 묶음이 같으면 맞다

    result = cluster_report.evaluate(truth, predicted)

    assert (result["precision"], result["recall"], result["f1"]) == (1.0, 1.0, 1.0)
    assert result["pairs"] == {"tp": 2, "fp": 0, "fn": 0}


def test_merging_two_tasks_costs_precision_and_names_the_pair():
    truth = {1: "감사", 2: "감사", 3: "예산", 4: "예산"}
    predicted = {1: "A", 2: "A", 3: "A", 4: "A"}      # 넷을 한 묶음으로

    result = cluster_report.evaluate(truth, predicted)

    assert result["recall"] == 1.0
    assert result["precision"] < 1.0
    assert result["merged"][0]["tasks"] == ["감사", "예산"]
    assert result["merged"][0]["pairs"] == 4


def test_splitting_one_task_costs_recall_and_names_it():
    truth = {1: "감사", 2: "감사", 3: "감사"}
    predicted = {1: "A", 2: "B", 3: "B"}

    result = cluster_report.evaluate(truth, predicted)

    assert result["precision"] == 1.0
    assert result["recall"] < 1.0
    assert result["split"][0]["task"] == "감사"


def test_unclassified_documents_count_as_lonely_clusters():
    """미분류를 빼고 재면 '확신 없는 문서를 다 버릴수록 점수가 오르는 자'가 된다."""
    truth = {1: "감사", 2: "감사"}
    all_dropped = cluster_report.evaluate(truth, {1: cluster_report.UNCLASSIFIED,
                                                 2: cluster_report.UNCLASSIFIED})

    assert all_dropped["recall"] == 0.0
    assert all_dropped["unclassified"] == 2
    assert all_dropped["found_tasks"] == 0


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("2025_행정사무감사_제출자료_최종.docx", "행정사무감사"),
        ("2022_예산관리_예산요구자료.xlsx", "예산관리"),
        ("메모.txt", ""),                     # 규칙에 맞지 않으면 채점에서 빠진다
        ("2025_행정사무감사.docx", ""),        # 토막이 모자라면 추측하지 않는다
        ("사업_계획_2025.hwp", ""),
    ],
)
def test_truth_from_filename_only_trusts_the_fixture_rule(filename, expected):
    assert cluster_report.truth_from_filename(filename) == expected


def test_documents_query_ignores_missing_and_unreadable_files(tmp_path: Path):
    db = Database(tmp_path / "p.db")
    db.init()
    source_id = db.add_source(tmp_path / "자료")
    db.upsert_document(source_id, {
        "path": str(tmp_path / "정상.docx"), "filename": "정상.docx",
        "ext": ".docx", "parse_status": "ok",
    })
    db.upsert_document(source_id, {
        "path": str(tmp_path / "실패.docx"), "filename": "실패.docx",
        "ext": ".docx", "parse_status": "failed",
    })
    db.upsert_document(source_id, {
        "path": str(tmp_path / "사라짐.docx"), "filename": "사라짐.docx",
        "ext": ".docx", "parse_status": "ok", "missing_since": "2026-01-01",
    })
    db.con.execute("INSERT INTO tasks(id, name) VALUES (1, '행정사무감사')")
    db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")

    rows = cluster_report.documents(db)
    db.close()

    assert [row["filename"] for row in rows] == ["정상.docx"]
    assert rows[0]["task"] == "행정사무감사"


def test_tasks_marked_not_a_task_do_not_count_as_clusters(tmp_path: Path):
    db = Database(tmp_path / "p.db")
    db.init()
    source_id = db.add_source(tmp_path / "자료")
    db.upsert_document(source_id, {
        "path": str(tmp_path / "문서.docx"), "filename": "문서.docx",
        "ext": ".docx", "parse_status": "ok",
    })
    db.con.execute("INSERT INTO tasks(id, name, not_a_task) VALUES (1, '참고자료', 1)")
    db.con.execute("INSERT INTO task_docs(task_id, doc_id) VALUES (1, 1)")

    rows = cluster_report.documents(db)
    db.close()

    assert rows[0]["task"] is None       # 사람이 '업무 아님'이라 한 것은 묶음이 아니다


# ── 파서 회귀 ───────────────────────────────────────────────────────

@needs_fixtures
def test_baseline_matches_itself(tmp_path: Path):
    measured = parse_bench.measure(FIXTURES)
    assert measured, "표본에서 아무것도 재지 못했다"
    assert not parse_bench.compare(measured, measured)


@needs_fixtures
def test_baseline_holds_no_document_text(tmp_path: Path):
    """기준 파일은 공유될 수 있다. 본문이 들어가면 그 자체가 유출 경로다."""
    sample = next(iter(parse_bench.measure(FIXTURES).values()))
    text = json.dumps(sample, ensure_ascii=False)

    assert "text_sha256" in sample
    assert sample["chars"] > 0
    assert "행정사무감사 요구자료" not in text
    assert not any(isinstance(value, str) and len(value) > 100 for value in sample.values())


def test_status_change_is_reported_first():
    before = {"a.hwp": _entry(status="ok", chars=100)}
    after = {"a.hwp": _entry(status="failed", chars=0, sha="0" * 64)}

    changes = parse_bench.compare(before, after)
    assert changes == ["! a.hwp  상태 ok → failed"]


def test_changed_body_is_reported_with_the_size_delta():
    before = {"a.hwp": _entry(chars=1000)}
    after = {"a.hwp": _entry(chars=400, sha="b" * 64)}

    line = parse_bench.compare(before, after)[0]
    assert line.startswith("! ")            # 2%를 넘는 변화는 경고로
    assert "1,000 → 400자" in line and "-600" in line


def test_small_body_change_is_marked_but_not_alarming():
    before = {"a.hwp": _entry(chars=1000)}
    after = {"a.hwp": _entry(chars=1005, sha="b" * 64)}

    assert parse_bench.compare(before, after)[0].startswith("~ ")


def test_locator_count_change_is_reported_even_when_text_is_identical():
    """조각이 갈라지면 본문은 같아도 '3쪽' 같은 근거 위치가 어긋난다."""
    before = {"a.hwp": _entry(sections=5)}
    after = {"a.hwp": _entry(sections=9)}

    assert "조각 수 5 → 9" in parse_bench.compare(before, after)[0]


def test_new_and_missing_samples_are_reported():
    changes = parse_bench.compare({"old.hwp": _entry()}, {"new.hwp": _entry()})
    assert any(line.startswith("+ new.hwp") for line in changes)
    assert any(line.startswith("- old.hwp") for line in changes)


@needs_fixtures
def test_check_exits_nonzero_when_a_sample_changed(tmp_path: Path):
    """도구를 실제로 돌려 확인한다 — 종료 코드가 CI의 신호다."""
    import shutil

    folder = tmp_path / "표본"
    folder.mkdir()
    source = next(FIXTURES.rglob("*.csv"))
    shutil.copy2(source, folder / source.name)
    baseline = tmp_path / "base.json"

    record = parse_bench.main(["--record", "--folder", str(folder),
                               "--baseline", str(baseline)])
    assert record == 0

    (folder / source.name).write_text("바뀐 내용,1,2\n", encoding="utf-8")
    assert parse_bench.main(["--check", "--folder", str(folder),
                             "--baseline", str(baseline)]) == 1


def test_check_without_a_sample_folder_says_so(monkeypatch, capsys):
    monkeypatch.delenv(parse_bench.ENV_VAR, raising=False)
    assert parse_bench.main(["--check"]) == 2
    assert parse_bench.ENV_VAR in capsys.readouterr().err


@pytest.mark.skipif(
    not os.environ.get(parse_bench.ENV_VAR),
    reason=f"실문서 표본이 없습니다 — {parse_bench.ENV_VAR} 를 설정하면 함께 검사합니다",
)
def test_real_document_samples_have_not_regressed():
    """실제 공문서(HWP 포함) 회귀 — 표본을 가진 기계에서만 돈다.

    합성 표본으로는 HWP 5.0을 만들 수 없어(바이너리 OLE) 이 경로가 유일한
    방어선이다. 표본은 공직 자료라 저장소에 넣을 수 없으므로 경로를 환경
    변수로 받고, 없는 기계에서는 조용히 건너뛴다.
    """
    folder = Path(os.environ[parse_bench.ENV_VAR])
    baseline_path = folder / parse_bench.BASELINE_NAME
    if not baseline_path.exists():
        pytest.skip(f"기준 파일이 없습니다 — python -m app.tools.parse_bench --record")

    changes = parse_bench.compare(
        json.loads(baseline_path.read_text(encoding="utf-8")), parse_bench.measure(folder)
    )
    assert not changes, "파서 결과가 기준과 달라졌습니다:\n" + "\n".join(changes)


def _entry(status: str = "ok", chars: int = 100, sha: str = "a" * 64,
           sections: int = 5) -> dict:
    return {
        "status": status, "parser": "docx", "sections": sections, "chars": chars,
        "text_sha256": sha, "locators": ["1쪽"], "author": True, "created": True,
    }
