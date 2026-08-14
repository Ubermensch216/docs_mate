"""평가 도구 자체의 시험 (RAG 개선 R1).

이 도구가 잘못 채점하면 이후 모든 개선 판단이 틀어진다. 도구는 Ollama가
있어야 돌지만, 채점 규칙은 순수 함수라 여기서 오프라인으로 검사한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.tools.rag_report import DEFAULT_CASES, Outcome, _judge, _load_cases


def outcome(**fields) -> Outcome:
    base = dict(
        case_id="t", question="질문", expect="answer", withheld=False,
        seconds=1.0, citations=["2025_행정사무감사_제출자료_부장수정.docx"],
        answer="답", error=None,
    )
    base.update(fields)
    return Outcome(**base)


# ── 유보 판정 — 가장 중요한 규칙 ────────────────────────────────────

def test_answering_an_unanswerable_question_fails():
    """자료에 없는 것을 답으로 만들면 실패다. 지어낸 답이기 때문이다."""
    case = {"id": "t", "question": "q", "expect": "withhold"}
    failures = _judge(case, outcome(withheld=False))
    assert any("지어낸 답" in f for f in failures)


def test_withholding_an_unanswerable_question_passes():
    case = {"id": "t", "question": "q", "expect": "withhold"}
    assert _judge(case, outcome(withheld=True, citations=[], answer="")) == []


def test_withholding_an_answerable_question_fails():
    case = {"id": "t", "question": "q", "expect": "answer"}
    failures = _judge(case, outcome(withheld=True, citations=[]))
    assert any("유보했다" in f for f in failures)


def test_answer_without_citations_fails():
    """근거 없는 답은 이 제품에서 답이 아니다."""
    case = {"id": "t", "question": "q", "expect": "answer"}
    failures = _judge(case, outcome(citations=[]))
    assert any("근거가 없다" in f for f in failures)


# ── 근거 판정 ───────────────────────────────────────────────────────

def test_missing_expected_evidence_fails():
    case = {"id": "t", "question": "q", "expect": "answer",
            "must_cite_any": ["수질통계"]}
    failures = _judge(case, outcome())
    assert any("기대한 근거가 없다" in f for f in failures)


def test_any_one_of_the_expected_documents_is_enough():
    case = {"id": "t", "question": "q", "expect": "answer",
            "must_cite_any": ["수질통계", "행정사무감사_제출자료"]}
    assert _judge(case, outcome()) == []


def test_contamination_from_another_task_fails():
    case = {"id": "t", "question": "q", "expect": "answer",
            "must_not_cite": ["월간실적보고"]}
    failures = _judge(case, outcome(
        citations=["2024_월간실적보고_실적취합.xlsx"]
    ))
    assert any("엉뚱한 자료" in f for f in failures)


def test_duplicate_flood_is_caught():
    """근거 8건이 전부 같은 문서의 사본이면 근거 예산을 낭비한 것이다."""
    case = {"id": "t", "question": "q", "expect": "answer", "min_distinct_docs": 3}
    failures = _judge(case, outcome(citations=["같은문서.csv"] * 8))
    assert any("몰렸다" in f for f in failures)


def test_diverse_evidence_passes_the_diversity_rule():
    case = {"id": "t", "question": "q", "expect": "answer", "min_distinct_docs": 3}
    assert _judge(case, outcome(citations=["a.csv", "b.csv", "c.csv"])) == []


def test_expected_evidence_is_not_demanded_when_withholding_is_correct():
    """유보한 사례에 '근거가 없다'고 또 감점하면 이중 처벌이다."""
    case = {"id": "t", "question": "q", "expect": "withhold",
            "must_cite_any": ["수질통계"]}
    assert _judge(case, outcome(withheld=True, citations=[])) == []


# ── 오류 처리 ───────────────────────────────────────────────────────

def test_error_is_reported_as_a_single_failure():
    """모델 응답이 깨진 것과 답을 틀린 것은 다른 문제다. 섞어 세지 않는다."""
    case = {"id": "t", "question": "q", "expect": "answer",
            "must_cite_any": ["수질통계"]}
    failures = _judge(case, outcome(error="JSON 형식 오류", withheld=True))
    assert failures == ["오류: JSON 형식 오류"]


# ── 평가셋 파일 ─────────────────────────────────────────────────────

def test_shipped_cases_are_wellformed():
    cases = _load_cases(Path(DEFAULT_CASES))
    assert cases, "평가셋이 비어 있다"
    for case in cases:
        assert case["id"] and case["question"]
        assert case["expect"] in ("answer", "withhold")
        assert case.get("why"), f"{case['id']}: 이 사례를 왜 넣었는지 적어야 한다"


def test_shipped_cases_include_questions_that_must_be_withheld():
    """유보해야 하는 질문이 없는 평가셋은 지어내기를 못 잡는다."""
    cases = _load_cases(Path(DEFAULT_CASES))
    assert sum(1 for c in cases if c["expect"] == "withhold") >= 2


def test_case_ids_are_unique():
    cases = _load_cases(Path(DEFAULT_CASES))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_cases_file_is_valid_json():
    json.loads(Path(DEFAULT_CASES).read_text(encoding="utf-8"))


# ── 반복 실행 (편차 측정) ───────────────────────────────────────────

def test_repeat_option_exists_and_defaults_to_one():
    """생성 모델 출력은 실행마다 다르다 — 프롬프트 판단에는 반복이 필요하다."""
    from app.tools.rag_report import _parse_args

    assert _parse_args([]).repeat == 1
    assert _parse_args(["--repeat", "3"]).repeat == 3


def test_stability_report_marks_flaky_cases(capsys):
    """일부만 통과한 사례를 '통과'로 뭉뚱그리면 운을 개선으로 착각한다."""
    from app.tools.rag_report import _print_stability

    steady = outcome(case_id="steady")
    flaky_pass = outcome(case_id="flaky")
    flaky_fail = outcome(case_id="flaky")
    flaky_fail.failures = ["실패"]

    _print_stability([[steady, flaky_pass], [steady, flaky_fail]])
    printed = capsys.readouterr().out

    assert "steady   2/2 통과" in printed
    assert "flaky   1/2 통과" in printed
    assert "실행마다 결과가 갈리는 사례: flaky" in printed


def test_stability_report_is_quiet_when_everything_is_consistent(capsys):
    from app.tools.rag_report import _print_stability

    _print_stability([[outcome(case_id="a")], [outcome(case_id="a")]])
    assert "실행마다 결과가 갈리는" not in capsys.readouterr().out
