"""인수인계 보고서 — 확인한 업무지식을 문서 하나로 내보낸다 (계획서 §33, PRD §10.9).

왜 필요한가
    이 도구를 쓰지 못하는 사람에게도 인수인계는 넘어가야 한다. 후임자가
    프로그램을 설치하지 않을 수도 있고, 결재 라인에 종이로 올려야 할 수도
    있다. 화면 안에만 있는 지식은 인수인계를 **끝내지 못한다**.

AI를 쓰지 않는다
    보고서는 **이미 확인한 것을 옮겨 적는 일**이지 새로 쓰는 일이 아니다.
    여기서 모델에게 문장을 짓게 하면, 사람이 확정해 둔 사실 옆에 아무도
    검증하지 않은 문장이 같은 활자로 앉는다. 그 순간 보고서 전체가
    "확인된 것"인지 "그럴듯한 것"인지 알 수 없는 문서가 된다.

무엇을 구별해서 적는가 (PRD RPT-003)
    문장마다 어디서 온 것인지 표시한다 — ✓ 담당자가 확인한 것, ◐ 자료에서
    추정한 것, △ 근거가 약한 것. 표시를 지우면 읽는 사람은 전부 사실로
    읽는다. 그래서 **근거 문서 이름을 함께 적는다**. 확인할 수 없는 주장은
    보고서에서 가장 위험한 것이고, 인수인계 문서에서는 특히 그렇다.

형식은 Markdown 하나다
    DOCX·PDF는 PRD에서 P2다. 지금 필요한 것은 **어디서나 열리고 그대로
    붙여 넣을 수 있는 글**이고, 한글이 깨지지 않는 것이 그다음이다.
"""

from __future__ import annotations

from datetime import date

from . import guide, handover, health, status

# 문장 앞에 붙는 표시. 화면에서 쓰는 기호를 그대로 쓴다 — 보고서에서만
# 다른 어휘를 쓰면 두 벌을 배워야 한다(§9).
MARK = {
    status.CONFIRMED: "✓",
    status.INFERRED: "◐",
    status.WEAK: "△",
    status.UNKNOWN: "○",
}
LEGEND = "✓ 담당자 확인 · ◐ 자료에서 추정 · △ 근거 부족 · ○ 확인할 수 없음"

MONTHS = ("1월", "2월", "3월", "4월", "5월", "6월",
          "7월", "8월", "9월", "10월", "11월", "12월")


def _mark(state: str) -> str:
    return MARK.get(state, MARK[status.UNKNOWN])


def _cycle_line(row) -> str:
    """'매년 9~11월' — 화면의 cycle_headline과 같은 말을 해야 한다.

    ui.views.cycle_format을 core가 가져다 쓸 수는 없으므로(방향이 거꾸로다)
    보고서에 필요한 만큼만 여기서 만든다. 규칙이 갈라지지 않도록 표현은
    최소로 줄인다 — 월 목록과 '매월'뿐이다.
    """
    if row is None:
        return ""
    if row["kind"] == "none":
        return "반복하지 않는 업무입니다"
    if row["kind"] == "monthly":
        return "매월" + (f" {row['day_hint']}" if row["day_hint"] else "")
    months = [int(m) for m in (row["months"] or "").split(",") if m.strip()]
    if not months:
        return "매년"
    return "매년 " + ", ".join(MONTHS[m - 1] for m in sorted(months))


def _task_section(db, row, today: date) -> list[str]:
    task_id = row["id"]
    state = status.of_task(row)
    out = [f"### {row['name']}", ""]
    out.append(
        f"{_mark(state)} 문서 {row['doc_count']:,}건"
        + (f" · {row['first_year']}~{row['last_year']}년" if row["last_year"] else "")
    )
    out.append("")

    if row["description"]:
        out += [row["description"], ""]
    else:
        out += ["> 이 업무가 무엇인지는 아직 적혀 있지 않습니다.", ""]

    cycle = db.task_cycle(task_id)
    if cycle is not None:
        out += [
            "**언제 하는 일인가**",
            "",
            f"{_mark(status.of_cycle(cycle))} {_cycle_line(cycle)}",
            "",
        ]

    reading = db.task_reading(task_id)
    if reading:
        out += ["**먼저 읽을 문서**", ""]
        for pick in reading:
            # 이유 없는 순번은 신뢰를 만들지 못한다(§7). 화면에서 이유를
            # 붙였으면 보고서에도 같이 가야 한다.
            reason = f" — {pick['reason']}" if pick["reason"] else ""
            out.append(f"{pick['ordinal']}. `{pick['filename']}`{reason}")
        out.append("")

    years = db.task_years(task_id)
    confirmed_years = db.confirmed_step_years(task_id)
    year = _report_year(years, confirmed_years, today)
    if year is not None:
        steps = db.task_steps(task_id, year)
        if steps:
            settled = all(step["decided_by"] == "user" for step in steps)
            mark = _mark(status.CONFIRMED if settled else status.INFERRED)
            out += [f"**어떻게 처리했나** ({year}년)", "", f"{mark} 처리 순서", ""]
            if not any(step["doc_id"] for step in steps):
                # 화면과 같은 말을 해야 한다 — 가져오거나 손으로 적은 순서에
                # "자료에서 이렇게 보입니다"라고 하면 안 된다.
                out += ["이 해 순서는 자료가 아니라 사람이 적어 둔 것입니다.", ""]
            for step in steps:
                when = step["day_hint"] or "시점 미상"
                source = f" — `{step['filename']}`" if step["filename"] else (
                    " — 근거 문서 없음"
                )
                out.append(f"{step['ordinal']}. {step['label']} ({when}){source}")
            out.append("")

    primary = db.primary_document(task_id)
    if primary is not None:
        out += ["**대표 문서**", "", f"`{primary['path']}`", ""]

    where = _folders(db.task_documents(task_id))
    if where:
        out += ["**자료가 있는 곳**", ""]
        out += [f"- `{folder}` ({count:,}건)" for folder, count in where]
        out.append("")

    report = health.evaluate(_health_row(db, task_id))
    if report is not None and report.missing:
        lacks = " · ".join(check.label for check in report.missing)
        out += [f"> 아직 확인되지 않은 것 — {lacks}", ""]
    return out


def _report_year(years: list[int], confirmed: list[int], today: date) -> int | None:
    """보고서에 실을 연도. 사람이 확정한 해가 있으면 그 해다 (§21).

    보고서는 "지금 이 업무는 이렇게 돌아간다"를 넘기는 문서다. 확정한 해를
    두고 더 오래된 해를 싣는 것은 후임자에게 낡은 절차를 물려주는 일이다.
    """
    from .timeline import default_how_year
    return default_how_year(sorted(set(years) | set(confirmed)),
                            today=today, confirmed=confirmed)


def _health_row(db, task_id: int):
    for row in db.task_health_inputs():
        if row["task_id"] == task_id:
            return row
    return None


def _folders(docs, limit: int = 5) -> list[tuple[str, int]]:
    """문서가 실제로 놓인 폴더. 후임자가 가장 먼저 묻는 것 중 하나다 —
    "그 파일들 어디 있어요?"."""
    tally: dict[str, int] = {}
    for doc in docs:
        path = str(doc["path"])
        folder = path.rsplit("\\", 1)[0] if "\\" in path else path.rsplit("/", 1)[0]
        tally[folder] = tally.get(folder, 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _calendar(db) -> list[str]:
    """연간 일정 — 달마다 어떤 업무가 걸리는가.

    업무별 주기를 열두 번 읽어 알아내게 하지 않는다. 발령 첫 달에 필요한
    것은 "이번 달에 뭐가 있나" 한 줄이다.
    """
    by_month: dict[int, list[str]] = {}
    for row in db.all_cycles():
        if row["kind"] == "monthly":
            continue
        for raw in (row["months"] or "").split(","):
            if raw.strip():
                by_month.setdefault(int(raw), []).append(row["task_name"])
    monthly = [row["task_name"] for row in db.all_cycles() if row["kind"] == "monthly"]

    if not by_month and not monthly:
        return []
    out = ["## 연간 일정", "", "| 달 | 업무 |", "|---|---|"]
    for month in range(1, 13):
        names = sorted(set(by_month.get(month, [])))
        out.append(f"| {MONTHS[month - 1]} | {', '.join(names) if names else '—'} |")
    out.append("")
    if monthly:
        out += [f"매월 하는 업무: {', '.join(sorted(set(monthly)))}", ""]
    return out


def _progress(db) -> list[str]:
    tally = dict(db.handover_counts())
    progress = handover.summarize(tally)
    out = ["## 확인 상태", "", progress.headline(), ""]
    for area in progress.areas:
        out.append(f"- {_mark(area.state)} {area.sentence()}")
    out.append("")

    weak = [item for item in health.summarize(db.task_health_inputs())
            if item.grade != health.GOOD]
    if weak:
        out += ["아직 손볼 곳이 있는 업무", ""]
        out += [f"- {item.name} — {item.grade_label()}: {item.headline()}"
                for item in weak]
        out.append("")
    return out


def _leftovers(db) -> list[str]:
    """업무에 넣지 못한 것. 빼면 보고서가 실제보다 깔끔해 보인다.

    미분류가 남는 것은 정상이지만(§7), 그 사실을 넘겨받는 사람도 알아야
    한다 — 자기가 찾아야 할 것이 남았다는 뜻이기 때문이다.
    """
    unclassified = db.unclassified_count()
    if not unclassified:
        return []
    strays = db.stray_counts()
    line = f"어느 업무에도 넣지 못한 문서가 {unclassified:,}건 있습니다."
    if strays["strays_total"]:
        line += (
            f" 그중 최근 자료는 {strays['strays_total']:,}건이고, "
            f"{strays['strays_done']:,}건은 확인했습니다."
        )
    return ["## 아직 정리되지 않은 자료", "", line, ""]


def build(db, project: str = "", today: date | None = None) -> str:
    """보고서 전문을 Markdown으로 만든다.

    `db`에 형(型)을 달지 않는 이유: core는 db 계층을 모른다. repo.py가
    core.health를 쓰고 있어 반대 방향 임포트는 순환이 된다.
    """
    today = today or date.today()
    tasks = db.tasks()
    counts = db.counts()

    title = f"# {project} 인수인계 보고서" if project else "# 인수인계 보고서"
    out = [
        title,
        "",
        f"{today.year}년 {today.month}월 {today.day}일 만듦 · "
        f"전임자 자료 {counts['documents']:,}건을 살펴본 결과입니다.",
        "",
        LEGEND,
        "",
        "> 이 보고서는 자료에서 읽어 낸 것과 담당자가 확인한 것을 옮겨 적은 "
        "것입니다. 표시 없는 문장은 없습니다 — ◐과 △는 아직 사람이 확인하지 "
        "않은 대목이니 넘겨받는 분이 반드시 함께 확인하시기 바랍니다.",
        "",
    ]

    if not tasks:
        out += ["아직 파악된 업무가 없습니다.", ""]
        return "\n".join(out)

    out += _progress(db)

    plan = guide.summarize(dict(db.handover_counts(), **db.stray_counts()))
    if plan.stage != guide.DONE and plan.measured:
        nxt = plan.next_item()
        if nxt is not None:
            out += [
                f"다음에 확인할 것: {nxt.sentence()} — {nxt.todo}", "",
            ]

    out += [f"## 인수받은 업무 {len(tasks)}개", ""]
    for row in tasks:
        out += _task_section(db, row, today)

    out += _calendar(db)
    out += _leftovers(db)
    out += [
        "## 이 보고서에 대하여",
        "",
        "- 원본 파일은 하나도 고치지 않았습니다. 경로만 읽었습니다.",
        "- 업무 구분·반복 주기·처리 순서는 문서의 이름과 시점에서 재구성한 "
        "것입니다. 담당자가 확인한 대목(✓)만 사실로 보시기 바랍니다.",
        "- 문서를 외부로 보내지 않았습니다. 분석은 이 PC 안에서만 했습니다.",
        "",
    ]
    return "\n".join(out)
