"""신뢰 상태 모델 시험.

계획서 §9의 요구는 "제품 전체가 같은 상태 모델을 쓴다"이고, §11의 요구는
"사람이 수정한 값은 자동 재분석이 덮지 않는다"이다. 두 요구가 만나는 지점이
'사람이 만진 값은 무조건 CONFIRMED'라는 규칙이다. 여기서 못 박는다.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.core import status


def row(**fields):
    """sqlite3.Row를 흉내 낸다 — 실제 코드가 받는 것과 같은 모양으로 시험한다."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    keys = ", ".join(f'? AS "{k}"' for k in fields)
    return con.execute(f"SELECT {keys}", tuple(fields.values())).fetchone()


# ── 사람이 만진 값 ──────────────────────────────────────────────────

@pytest.mark.parametrize("fields", [
    {"status": "approved", "confidence": "low"},
    {"status": "edited", "confidence": "low"},
    {"decided_by": "user", "confidence": "low"},
    {"origin": "user", "confidence": "low"},
])
def test_human_touch_always_wins(fields):
    """AI가 아무리 낮은 확신으로 만들었어도 사람이 확인했다면 확정 지식이다."""
    assert status.resolve(**fields) == status.CONFIRMED


def test_ai_confidence_maps_to_four_states():
    assert status.from_confidence("high") == status.INFERRED
    assert status.from_confidence("medium") == status.INFERRED
    assert status.from_confidence("low") == status.WEAK
    assert status.from_confidence(None) == status.UNKNOWN
    assert status.from_confidence("낯선값") == status.UNKNOWN


def test_missing_value_is_unknown_not_weak():
    """'근거가 약함'과 '판단할 수 없음'은 다른 말이다."""
    assert status.resolve(confidence="high", has_value=False) == status.UNKNOWN


# ── 행 단위 판정 ────────────────────────────────────────────────────

def test_task_review_state_is_authoritative():
    """v2 review_state가 있으면 옛 열들의 조합보다 우선한다."""
    task = row(review_state="confirmed", status="proposed", confidence="low", origin="ai")
    assert status.of_task(task) == status.CONFIRMED


def test_human_trace_in_legacy_columns_still_wins():
    """review_state를 갱신하지 않는 쓰기 경로가 사용자의 확인을 강등시키면 안 된다."""
    task = row(review_state="inferred", status="edited", confidence="high", origin="user")
    assert status.of_task(task) == status.CONFIRMED


def test_task_falls_back_to_legacy_columns():
    """review_state가 아직 없는 행(마이그레이션 전 코드 경로)도 판정된다."""
    assert status.of_task(row(status="proposed", confidence="high", origin="ai")) == status.INFERRED
    assert status.of_task(row(status="proposed", confidence="low", origin="ai")) == status.WEAK
    assert status.of_task(row(status="approved", confidence="low", origin="ai")) == status.CONFIRMED


def test_missing_cycle_is_unknown():
    """주기를 못 찾았으면 빈칸이 아니라 '확인할 수 없음'이다."""
    assert status.of_cycle(None) == status.UNKNOWN
    assert status.of_cycle(row(confidence="high", decided_by="ai")) == status.INFERRED
    assert status.of_cycle(row(confidence="low", decided_by="user")) == status.CONFIRMED


def test_step_without_evidence_is_never_inferred():
    """근거 문서가 없는 단계를 '자료에서 추정'이라고 말하면 거짓말이다."""
    assert status.of_step(row(decided_by="ai", is_inferred=0, doc_id=None)) == status.WEAK
    assert status.of_step(row(decided_by="ai", is_inferred=1, doc_id=5)) == status.WEAK
    assert status.of_step(row(decided_by="ai", is_inferred=0, doc_id=5)) == status.INFERRED
    assert status.of_step(row(decided_by="user", is_inferred=1, doc_id=None)) == status.CONFIRMED


def test_filesystem_date_is_weak():
    """파일 수정일은 복사만 해도 바뀐다 — When·How가 이미 배제하는 기준이다."""
    assert status.of_document_date(
        row(eff_date="2025-09-01", eff_date_kind="fs", date_decided_by="ai")
    ) == status.WEAK
    assert status.of_document_date(
        row(eff_date="2025-09-01", eff_date_kind="body", date_decided_by="ai")
    ) == status.INFERRED
    assert status.of_document_date(
        row(eff_date="2025-09-01", eff_date_kind="fs", date_decided_by="user")
    ) == status.CONFIRMED
    assert status.of_document_date(
        row(eff_date=None, eff_date_kind=None, date_decided_by="ai")
    ) == status.UNKNOWN


# ── 표시 ────────────────────────────────────────────────────────────

def test_every_state_has_a_label_and_a_symbol():
    """색만으로 구분하지 않는다 — 기호와 글자가 항상 함께 간다."""
    for state in status.ORDER:
        text = status.label(state)
        assert text.startswith(status.symbol(state))
        assert len(text) > 2
        assert status.badge_style(state).startswith("Badge")


def test_unknown_state_degrades_gracefully():
    """낯선 값이 들어와도 화면이 비지 않는다."""
    assert status.label("낯선상태") == status.label(status.UNKNOWN)


def test_cluster_note_keeps_human_wording():
    """계획서 §7.2 — 추상적인 숫자 대신 사람이 이해할 수 있는 표현을 유지한다."""
    text, kind = status.cluster_note("high")
    assert "단단" in text and kind == "ok"
    assert status.cluster_note(None) == status.cluster_note("low")
