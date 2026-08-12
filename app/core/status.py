"""신뢰 상태 모델 — 제품 전체가 쓰는 단 하나의 어휘.

개선 계획서 §9의 요구는 간단하다. "업무 분류·날짜·반복 주기·처리 단계·
최신본 후보·AI 답변에 **같은** 상태 모델을 쓴다. 사용자가 제품 전체에서
새로운 신뢰 체계를 다시 배우게 하지 않는다."

그런데 DB는 표마다 다른 말을 쓴다.
  tasks.status        proposed | approved | edited
  *.confidence        high | medium | low
  *.decided_by        ai | user
  ai_document.status  proposed | approved | edited

이 세 어휘를 화면마다 각자 번역하면 같은 뜻이 화면마다 다르게 보인다.
번역은 여기서 한 번만 한다. 화면은 raw 값을 해석하지 않는다.

가장 중요한 규칙 하나: **사람이 만진 값은 무조건 CONFIRMED다.**
이 판정을 UI에 맡기면 어느 화면에선가 반드시 빠뜨린다.
"""

from __future__ import annotations

from typing import Any

CONFIRMED = "confirmed"   # ✓ 담당자가 직접 확인했다
INFERRED = "inferred"     # ◐ 근거는 충분하나 사람이 아직 검증하지 않았다
WEAK = "weak"             # △ 근거가 약하거나 후보가 충돌한다
UNKNOWN = "unknown"       # ○ 자료에서 판단할 수 없다

ORDER = (CONFIRMED, INFERRED, WEAK, UNKNOWN)

# (기호, 문구, 뱃지 스타일). 색만으로 구분하지 않도록 기호와 글자를 함께 쓴다.
_LABELS: dict[str, tuple[str, str, str]] = {
    CONFIRMED: ("✓", "담당자 확인", "BadgeOk"),
    INFERRED: ("◐", "자료에서 추정", "BadgeNeutral"),
    WEAK: ("△", "자료가 불충분", "BadgeAttention"),
    UNKNOWN: ("○", "확인할 수 없음", "BadgeDanger"),
}

# 사람이 확정했음을 뜻하는 값들. 표마다 열 이름이 달라 값으로 판정한다.
_HUMAN_STATUS = frozenset({"approved", "edited", "confirmed"})
_HUMAN_ORIGIN = frozenset({"user"})

_FROM_CONFIDENCE = {
    "high": INFERRED,
    "medium": INFERRED,
    "low": WEAK,
    "unknown": UNKNOWN,
    "": UNKNOWN,
}


def symbol(state: str) -> str:
    return _LABELS.get(state, _LABELS[UNKNOWN])[0]


def label(state: str) -> str:
    """'◐ 자료에서 추정' — 뱃지와 목록에 그대로 쓰는 완성 문구."""
    mark, text, _style = _LABELS.get(state, _LABELS[UNKNOWN])
    return f"{mark} {text}"


def badge_style(state: str) -> str:
    return _LABELS.get(state, _LABELS[UNKNOWN])[2]


def from_confidence(confidence: str | None) -> str:
    """AI가 매긴 confidence만 있는 경우의 상태."""
    return _FROM_CONFIDENCE.get((confidence or "").lower(), UNKNOWN)


def resolve(
    confidence: str | None = None,
    status: str | None = None,
    decided_by: str | None = None,
    origin: str | None = None,
    has_value: bool = True,
) -> str:
    """DB 행의 여러 열을 하나의 상태로 접는다.

    사람 흔적(status/decided_by/origin)이 하나라도 있으면 confidence가
    무엇이든 CONFIRMED다. AI가 낮은 확신으로 만든 값을 사람이 확인했다면
    그것은 이제 확인된 지식이다.
    """
    if not has_value:
        return UNKNOWN
    if (status or "").lower() in _HUMAN_STATUS:
        return CONFIRMED
    if (decided_by or "").lower() in _HUMAN_ORIGIN:
        return CONFIRMED
    if (origin or "").lower() in _HUMAN_ORIGIN:
        return CONFIRMED
    return from_confidence(confidence)


# ── 행 단위 판정 ────────────────────────────────────────────────────
# sqlite3.Row는 dict가 아니라 .get()이 없다. 없는 열을 조용히 넘기려면
# keys() 검사가 필요해서 접근을 여기로 모은다.

def _get(row: Any, key: str) -> Any:
    if row is None:
        return None
    try:
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else None
        return row[key]
    except (KeyError, IndexError):
        return None


def of_task(row: Any) -> str:
    """업무 하나의 상태.

    review_state(v2)가 기본 권위지만, 사람 흔적이 옛 열에만 남은 경우에도
    CONFIRMED로 올린다. review_state를 갱신하지 않는 쓰기 경로가 하나라도
    있으면 사용자의 확인이 조용히 강등되기 때문이다 — 실제로 rename_task가
    그랬다. 어느 쪽이든 사람이 만졌으면 확정이다.
    """
    if row is None:
        return UNKNOWN
    legacy = resolve(
        confidence=_get(row, "confidence"),
        status=_get(row, "status"),
        origin=_get(row, "origin"),
    )
    explicit = _get(row, "review_state")
    if explicit not in _LABELS:
        return legacy
    return CONFIRMED if CONFIRMED in (explicit, legacy) else explicit


def of_cycle(row: Any) -> str:
    """반복 주기의 상태. 주기를 못 찾았으면 UNKNOWN(빈칸이 아니다)."""
    return resolve(
        confidence=_get(row, "confidence"),
        decided_by=_get(row, "decided_by"),
        has_value=row is not None,
    )


def of_step(row: Any) -> str:
    """처리 단계 하나의 상태.

    근거 문서가 없는 추정 단계(is_inferred)는 사람이 만들었더라도 WEAK보다
    나을 수 없다 — 다만 사람이 직접 넣은 단계는 그 사람이 근거다.
    """
    if row is None:
        return UNKNOWN
    if (_get(row, "decided_by") or "").lower() in _HUMAN_ORIGIN:
        return CONFIRMED
    if _get(row, "is_inferred"):
        return WEAK
    return INFERRED if _get(row, "doc_id") else WEAK


def of_document_date(row: Any) -> str:
    """문서 시점의 상태.

    파일 수정일(fs)로만 판정한 시점은 신뢰하지 않는다 — 복사만 해도 바뀐다.
    When·How 계산에서 이미 fs를 배제하고 있으므로 표시도 같은 기준을 쓴다.
    """
    if row is None or not _get(row, "eff_date"):
        return UNKNOWN
    if (_get(row, "date_decided_by") or "").lower() in _HUMAN_ORIGIN:
        return CONFIRMED
    kind = _get(row, "eff_date_kind")
    if kind == "fs":
        return WEAK
    return INFERRED if kind else UNKNOWN


# ── 업무 묶음의 단단함 (§7.2) ───────────────────────────────────────
# 4단계 신뢰 상태와는 다른 축이다. "사람이 확인했는가"가 아니라 "임베딩
# 군집이 얼마나 단단한가"를 말한다. 계획서가 이 표현을 유지하라고 했으므로
# 남기되, 출처는 여기 하나로 모은다.
_CLUSTER_NOTES = {
    "high": ("● 묶음이 단단합니다", "ok"),
    "medium": ("◐ 확인이 필요합니다", "neutral"),
    "low": ("○ 느슨하게 묶였습니다", "attention"),
}


def cluster_note(confidence: str | None) -> tuple[str, str]:
    """(문구, 뱃지 kind). 사용자가 이해할 수 있는 말로만 쓴다 — 확률 숫자 금지."""
    return _CLUSTER_NOTES.get((confidence or "").lower(), _CLUSTER_NOTES["low"])
