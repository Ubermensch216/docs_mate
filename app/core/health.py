"""업무기억 건강도 — "다음 담당자에게 넘기기 전에 무엇이 부족한가" (계획서 §19).

이 제품의 쓰임을 **발령 직후 한 번**에서 **업무를 하는 내내, 그리고 다음
인수인계를 준비할 때까지**로 넓히는 기능이다. 후임자가 묻는 질문(What·When·
How)을 그대로 뒤집어, 지금 이 업무기억이 그 질문에 답할 수 있는지 점검한다.

**규칙으로만 판정한다. AI를 쓰지 않는다.** 건강도는 "자료가 있는가/사람이
확인했는가"를 세는 일이고, 그건 DB가 이미 아는 사실이다. 여기에 모델을
들이면 점검 결과 자체가 또 검증해야 할 주장이 된다.

점수는 항목의 가중 합이다. 등급(양호·확인 필요·자료 부족)은 점수가 아니라
**무엇이 빠졌는지**로 가른다 — 60점이라는 말은 사용자에게 아무것도 알려
주지 않지만 "반복 주기를 아직 아무도 확인하지 않았다"는 다음 행동이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

# (키, 화면 이름, 없을 때 할 말, 무게)
#
# 무게는 "이게 없으면 후임자가 얼마나 막히는가"다. 업무 설명이 없으면 그
# 업무가 무엇인지부터 모르고, 대표 문서가 없으면 조금 불편하다.
CHECKS: tuple[tuple[str, str, str, int], ...] = (
    ("description", "업무 설명", "이 업무가 무엇인지 적혀 있지 않습니다", 2),
    ("documents", "자료", "묶인 문서가 없습니다", 3),
    ("recent", "최근 자료", "최근 2년 안에 만든 자료가 없습니다", 2),
    ("reading", "먼저 읽을 문서", "무엇부터 읽어야 할지 정해지지 않았습니다", 2),
    ("cycle", "반복 주기", "언제 하는 일인지 아직 확인되지 않았습니다", 2),
    ("steps", "처리 순서", "어떤 순서로 처리하는지 재구성되지 않았습니다", 2),
    ("primary", "대표 문서", "이 업무를 대표하는 문서가 지정되지 않았습니다", 1),
    ("reviewed", "담당자 확인", "담당자가 아직 확인하지 않았습니다", 2),
)

# 등급. 색이 아니라 말이 먼저다(PRD §18.4).
GOOD, ATTENTION, THIN = "good", "attention", "thin"
GRADE_LABEL = {GOOD: "양호", ATTENTION: "확인 필요", THIN: "자료 부족"}
GRADE_BADGE = {GOOD: "ok", ATTENTION: "attention", THIN: "neutral"}

# 자료 부족과 확인 필요를 가르는 기준. **자료가 없는 것**과 **사람이 아직
# 안 본 것**은 다른 문제이고 할 일도 다르다 — 앞은 원본을 더 찾아야 하고,
# 뒤는 지금 화면에서 확인하면 된다.
MATERIAL = ("documents", "recent", "reading", "steps")
RECENT_YEARS = 2


@dataclass(slots=True)
class Check:
    key: str
    label: str
    lack: str
    weight: int
    ok: bool


@dataclass(slots=True)
class Health:
    task_id: int
    name: str
    checks: list[Check]

    @property
    def missing(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    @property
    def score(self) -> int:
        """0~100. 저장·정렬용이고 화면의 주인공은 아니다."""
        total = sum(check.weight for check in self.checks)
        got = sum(check.weight for check in self.checks if check.ok)
        return round(100 * got / total) if total else 0

    @property
    def grade(self) -> str:
        missing = {check.key for check in self.missing}
        if not missing:
            return GOOD
        # 자료 자체가 비어 있으면 확인할 것도 없다. 그때 '확인 필요'라고
        # 하면 사용자는 화면을 뒤지다가 할 수 있는 일이 없다는 걸 알게 된다.
        return THIN if missing & set(MATERIAL) else ATTENTION

    def grade_label(self) -> str:
        return GRADE_LABEL[self.grade]

    def headline(self) -> str:
        """'양호' / '반복 주기를 확인해야 합니다' — 등급 옆에 붙는 한 줄."""
        missing = self.missing
        if not missing:
            return "빠진 것이 없습니다"
        first = max(missing, key=lambda check: check.weight)
        more = f" 외 {len(missing) - 1}가지" if len(missing) > 1 else ""
        return f"{first.lack}{more}"


def evaluate(row) -> Health:
    """`Database.task_health_inputs()`의 한 줄을 건강도로 바꾼다."""
    def value(key: str, default=0):
        keys = row.keys() if hasattr(row, "keys") else row
        return row[key] if key in keys else default

    latest_year = value("latest_year") or 0
    this_year = value("this_year") or 0
    results = {
        "description": bool(value("description")),
        "documents": value("doc_count") > 0,
        # '최근'은 오늘이 아니라 **자료 전체의 최신 연도**를 기준으로 잰다.
        # 2026년에 2025년 자료를 보고 "오래됐다"고 하면 성실히 정리한
        # 사람에게 틀린 지적을 하는 셈이다.
        "recent": bool(latest_year) and latest_year >= this_year - RECENT_YEARS,
        "reading": value("reading_count") > 0,
        "cycle": bool(value("has_cycle")),
        "steps": value("step_count") > 0,
        "primary": bool(value("has_primary")),
        "reviewed": bool(value("reviewed")),
    }
    return Health(
        task_id=value("task_id"),
        name=value("name", "") or "",
        checks=[
            Check(key=key, label=label, lack=lack, weight=weight, ok=results[key])
            for key, label, lack, weight in CHECKS
        ],
    )


def summarize(rows) -> list[Health]:
    """업무 목록 전체의 건강도. 나쁜 것부터 앞에 둔다 — 손댈 곳이 먼저 보여야 한다."""
    out = [evaluate(row) for row in rows]
    order = {THIN: 0, ATTENTION: 1, GOOD: 2}
    out.sort(key=lambda item: (order[item.grade], item.score, item.name))
    return out
