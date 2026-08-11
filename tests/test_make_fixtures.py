"""합성 표본 생성기 시험.

실측으로 잡은 회귀: 모든 xlsx에 "수질검사" 집계 시트를 하드코딩했더니
RAG 검색이 무관한 예산관리·월간실적보고 문서를 근거로 잡았다. 업무마다
다른 집계 항목을 쓰는지, 서로 다른 업무의 어휘가 섞이지 않는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.tools.make_fixtures import _TALLY_BY_TASK, _write_xlsx


def _tally_words(path: Path) -> set[str]:
    wb = openpyxl.load_workbook(path)
    ws = wb["집계"]
    return {str(row[0].value) for row in ws.iter_rows(min_row=2) if row[0].value}


def test_different_tasks_get_different_tally_vocabulary(tmp_path: Path):
    """서로 다른 업무의 xlsx가 같은 낱말을 공유하면 그 낱말이 검색을 오염시킨다."""
    written = {}
    for task in ("수질통계", "예산관리", "월간실적보고"):
        path = tmp_path / f"{task}.xlsx"
        _write_xlsx(path, task, "본문", task)
        written[task] = _tally_words(path)

    all_words = set().union(*written.values())
    # 각 업무의 집계 어휘가 서로 겹치지 않아야 한다 — 겹치면 그 낱말로
    # 다른 업무 문서가 함께 검색된다.
    for task, words in written.items():
        others = set().union(*(w for t, w in written.items() if t != task))
        assert not (words & others), f"{task}의 집계 어휘가 다른 업무와 겹칩니다: {words & others}"


def test_water_quality_keyword_is_confined_to_its_own_task(tmp_path: Path):
    """'수질검사'는 수질통계 xlsx에만 있어야 한다."""
    for task in ("수질통계", "예산관리", "월간실적보고", "계약관리"):
        path = tmp_path / f"{task}.xlsx"
        _write_xlsx(path, task, "본문", task)
        words = _tally_words(path)
        if task == "수질통계":
            assert "수질검사" in words
        else:
            assert "수질검사" not in words


def test_unknown_task_falls_back_to_a_neutral_tally(tmp_path: Path):
    path = tmp_path / "미확인.xlsx"
    _write_xlsx(path, "제목", "본문", "처음보는업무")
    words = _tally_words(path)
    known_task_words = {name for rows in _TALLY_BY_TASK.values() for name, *_ in rows}
    assert not (words & known_task_words)
