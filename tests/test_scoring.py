"""먼저 읽을 문서 추천 시험.

두 가지가 계약이다.
  1. 모델 없이도 추천이 나온다 (규칙 기반)
  2. 추천마다 사람이 읽을 수 있는 이유가 붙는다 — 이유 없는 별점은 신뢰를 못 만든다
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.scoring import DocFacts, recommend

TODAY = date(2026, 8, 11)


def _facts(**overrides) -> DocFacts:
    base = dict(doc_id=1, filename="문서.hwp", eff_year=2025, char_count=1000)
    base.update(overrides)
    return DocFacts(**base)


def test_empty_input_yields_nothing():
    assert recommend([]) == []


def test_every_recommendation_carries_a_reason():
    picks = recommend(
        [_facts(doc_id=i, filename=f"문서{i}.hwp") for i in range(1, 6)], today=TODAY
    )
    assert picks
    assert all(pick.reason for pick in picks)


def test_limit_is_respected():
    facts = [_facts(doc_id=i, filename=f"문서{i}.hwp") for i in range(1, 20)]
    assert len(recommend(facts, limit=3, today=TODAY)) == 3


def test_recent_documents_outrank_old_ones():
    picks = recommend(
        [
            _facts(doc_id=1, filename="보고.hwp", eff_year=2021),
            _facts(doc_id=2, filename="보고.hwp", eff_year=2025),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2


def test_final_markers_lift_a_document():
    picks = recommend(
        [
            _facts(doc_id=1, filename="제출자료_초안.docx"),
            _facts(doc_id=2, filename="제출자료_송부.docx"),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2
    assert "송부" in picks[0].reason


def test_handover_document_ranks_highest_by_type():
    picks = recommend(
        [
            _facts(doc_id=1, filename="회의록.hwp"),
            _facts(doc_id=2, filename="2025 업무 인수인계서.hwp"),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2
    assert "인수인계" in picks[0].reason


def test_widely_copied_documents_are_treated_as_important():
    picks = recommend(
        [
            _facts(doc_id=1, filename="자료.hwp", duplicate_count=1),
            _facts(doc_id=2, filename="자료.hwp", duplicate_count=4),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2
    assert "복사" in picks[0].reason


def test_empty_documents_are_pushed_down():
    picks = recommend(
        [
            _facts(doc_id=1, filename="자료.hwp", char_count=0),
            _facts(doc_id=2, filename="자료.hwp", char_count=2000),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2


def test_filesystem_only_dates_are_discounted():
    """복사 한 번에 오염되는 시점을 근거로 '최신'이라고 말하면 안 된다."""
    picks = recommend(
        [
            _facts(doc_id=1, filename="자료.hwp", eff_year=2025, date_kind="fs"),
            _facts(doc_id=2, filename="자료.hwp", eff_year=2025, date_kind="body"),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2
    assert picks[0].components.get("시점 불확실") is None


def test_user_pinned_document_wins_over_everything():
    picks = recommend(
        [
            _facts(doc_id=1, filename="2025 업무 인수인계서_송부.hwp"),
            _facts(doc_id=2, filename="잡다한자료.hwp", eff_year=2019, user_pinned=True),
        ],
        today=TODAY,
    )
    assert picks[0].doc_id == 2
    assert "사용자가 대표 문서로 지정" in picks[0].reason


def test_documents_without_a_date_still_get_ranked():
    picks = recommend([_facts(doc_id=1, filename="자료.hwp", eff_year=None)], today=TODAY)
    assert len(picks) == 1
    assert picks[0].reason


def test_components_explain_the_score():
    """점수를 어떻게 냈는지 사람이 볼 수 있어야 한다."""
    picks = recommend([_facts(doc_id=1, filename="제출자료_송부.docx")], today=TODAY)
    parts = picks[0].components
    assert "최종본 표기" in parts
    assert "문서 유형" in parts
    assert sum(parts.values()) == pytest.approx(picks[0].score, abs=0.01)


def test_ranking_is_stable_for_equal_scores():
    facts = [
        _facts(doc_id=2, filename="나.hwp"),
        _facts(doc_id=1, filename="가.hwp"),
    ]
    assert [p.filename for p in recommend(facts, today=TODAY)] == ["가.hwp", "나.hwp"]
