"""문서 라벨링 시험 — How 단계 이름의 재료."""

from __future__ import annotations

import pytest

from app.core.labeling import FALLBACK_LABEL, label_document


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("2024_행정사무감사_요구자료접수_최종.docx", "접수"),
        ("2024_행정사무감사_부서별자료요청_수정.docx", "자료 요청"),
        ("2025_월간실적보고_실적취합_송부.xlsx", "취합"),
        ("계약서_검토중.hwp", "검토"),
        ("예산안_결재완료.hwp", "결재"),
        ("2024_행정사무감사_제출자료_최종.docx", "제출"),
        ("자료_송부.hwp", "송부"),
        ("2024_행정사무감사_의원질의답변_최종.docx", "질의응답 대응"),
        ("정기회의록.hwp", "회의"),
        ("2025_주요업무계획.pptx", "계획 수립"),
        ("분기수질통계.csv", "통계 작성"),
        ("정수장별수질현황.xlsx", "현황 정리"),
        ("용역계약서.pdf", "계약"),
        ("월간실적보고.xlsx", "보고"),
    ],
)
def test_label_matches_expected_stage(filename, expected):
    assert label_document(filename) == expected


def test_unrecognized_filename_falls_back():
    assert label_document("스캔0001.pdf") == FALLBACK_LABEL


def test_earlier_pattern_wins_when_multiple_keywords_present():
    """'요구자료접수'는 요청보다 접수가 실제 업무 단계에 더 가깝다."""
    assert label_document("요구자료접수_최종.hwp") == "접수"


def test_submission_wins_over_dispatch_marker():
    """'제출자료_송부'는 배송 표기보다 원래 업무(제출)로 라벨링한다."""
    assert label_document("2024_제출자료_송부.docx") == "제출"
