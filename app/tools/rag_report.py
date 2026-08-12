"""질문 화면 실측 리포트 — RAG 개선의 자.

    python -m app.tools.rag_report [--project 이름] [--data 경로]
                                   [--cases 경로] [--case id] [--json 경로]

왜 필요한가
  RAG는 고쳐도 좋아졌는지 눈으로 알 수 없다. 답이 그럴듯해 보이는 것과
  근거가 맞는 것은 다른 문제다. 실제로 이 도구를 만들기 전 실측에서,
  네 질문 모두 "그럴듯한 답 + 인용 8건"을 내놨지만 인용은 전부 컨텍스트
  전량 나열이었고 한 질문은 무관한 자료로 표를 지어냈다.

읽는 법
  유보 정확도      가장 중요하다. 자료에 없는 것을 답으로 만들면 실패다.
  근거 적중률      기대한 문서가 근거에 들어왔는가.
  오염             들어오면 안 되는 업무의 문서가 섞였는가.
  인용 전량 나열   인용 수가 늘 TOP_K와 같으면 인용이 근거가 아니라는 뜻이다.
  문서 다양성      중복본이 근거 자리를 독점하지 않는가.

원본과 프로젝트 DB는 읽기만 한다. 질문 이력도 남기지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..db import Database, default_project_dir, open_project
from ..search import rag

DEFAULT_CASES = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "rag_eval" / "cases.json"
)


@dataclass(slots=True)
class Outcome:
    case_id: str
    question: str
    expect: str
    withheld: bool
    seconds: float
    citations: list[str] = field(default_factory=list)
    answer: str = ""
    error: str | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def distinct_docs(self) -> int:
        return len(set(self.citations))


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)

    cases = _load_cases(Path(args.cases))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"그런 사례가 없습니다: {args.case}")
            return 2

    db = open_project(args.project, Path(args.data) if args.data else None)
    try:
        print(f"프로젝트  {db.path}")
        counts = db.counts()
        left = db.unembedded_chunk_count()
        print(f"자료      문서 {counts['documents']:,}건 · "
              f"조각 {counts['chunks']:,}개 · 임베딩 {counts['chunks_embedded']:,}개")
        if left:
            # 색인이 덜 된 상태로 잰 점수는 성능이 아니라 색인 상태를 재는 것이다.
            print(f"⚠ 임베딩이 빠진 조각 {left:,}개 — 이 상태의 점수는 신뢰할 수 없습니다")
        print()

        outcomes = [_run(db, case) for case in cases]
    finally:
        db.close()

    _print_details(outcomes)
    _print_summary(outcomes)

    if args.json:
        Path(args.json).write_text(
            json.dumps([_as_dict(o) for o in outcomes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n결과를 저장했습니다: {args.json}")

    return 0 if all(o.passed for o in outcomes) else 1


# ── 실행 ────────────────────────────────────────────────────────────

def _run(db: Database, case: dict) -> Outcome:
    print(f"  · {case['id']} … ", end="", flush=True)
    started = time.time()
    answer = rag.ask(db, case["question"])
    seconds = time.time() - started

    outcome = Outcome(
        case_id=case["id"],
        question=case["question"],
        expect=case["expect"],
        withheld=answer.withheld,
        seconds=seconds,
        citations=[c.filename for c in answer.citations],
        answer=answer.text,
        error=answer.error,
    )
    outcome.failures = _judge(case, outcome)
    print(f"{'통과' if outcome.passed else '실패'} ({seconds:.1f}초)")
    return outcome


def _judge(case: dict, outcome: Outcome) -> list[str]:
    """사례 하나의 판정. 실패 사유를 사람이 읽는 문장으로 남긴다."""
    if outcome.error:
        return [f"오류: {outcome.error}"]

    failures: list[str] = []
    joined = " ".join(outcome.citations)

    if case["expect"] == "withhold":
        if not outcome.withheld:
            failures.append("유보해야 하는데 답을 만들었다 — 지어낸 답이다")
    else:
        if outcome.withheld:
            failures.append("답할 수 있어야 하는데 유보했다")
        elif not outcome.citations:
            failures.append("답을 냈는데 근거가 없다")

    for wanted in case.get("must_cite_any") or []:
        if any(wanted in name for name in outcome.citations):
            break
    else:
        if case.get("must_cite_any") and not outcome.withheld:
            failures.append(f"기대한 근거가 없다: {' / '.join(case['must_cite_any'])}")

    for banned in case.get("must_not_cite") or []:
        if banned in joined:
            failures.append(f"엉뚱한 자료가 근거에 섞였다: {banned}")

    minimum = case.get("min_distinct_docs")
    if minimum and not outcome.withheld and outcome.distinct_docs < minimum:
        failures.append(
            f"근거가 {outcome.distinct_docs}개 문서에 몰렸다 (최소 {minimum}) "
            f"— 중복본이 자리를 독점했을 수 있다"
        )
    return failures


# ── 출력 ────────────────────────────────────────────────────────────

def _print_details(outcomes: list[Outcome]) -> None:
    print()
    print("─" * 72)
    for outcome in outcomes:
        mark = "○" if outcome.passed else "✕"
        print(f"{mark} [{outcome.case_id}] {outcome.question}")
        print(f"   기대 {outcome.expect} · 실제 {'유보' if outcome.withheld else '답변'}"
              f" · {outcome.seconds:.1f}초 · 근거 {len(outcome.citations)}건"
              f"(문서 {outcome.distinct_docs}종)")
        if outcome.answer:
            print(f"   답: {_clip(outcome.answer, 100)}")
        for name in outcome.citations[:4]:
            print(f"     - {name}")
        if len(outcome.citations) > 4:
            print(f"     … 외 {len(outcome.citations) - 4}건")
        for failure in outcome.failures:
            print(f"   ✕ {failure}")
        print()


def _print_summary(outcomes: list[Outcome]) -> None:
    total = len(outcomes)
    if not total:
        return
    passed = sum(1 for o in outcomes if o.passed)

    should_hold = [o for o in outcomes if o.expect == "withhold"]
    held = sum(1 for o in should_hold if o.withheld)
    should_answer = [o for o in outcomes if o.expect == "answer"]
    answered = sum(1 for o in should_answer if not o.withheld)

    answered_ones = [o for o in outcomes if not o.withheld and o.citations]
    all_k = [o for o in answered_ones if len(o.citations) == rag.TOP_K]
    diversity = (
        sum(o.distinct_docs / len(o.citations) for o in answered_ones) / len(answered_ones)
        if answered_ones else 0.0
    )
    times = sorted(o.seconds for o in outcomes)

    print("─" * 72)
    print(f"통과            {passed}/{total}")
    if should_hold:
        print(f"유보 정확도     {held}/{len(should_hold)}   "
              f"(자료에 없는 질문에 답을 만들지 않았는가 — 가장 중요)")
    if should_answer:
        print(f"응답 정확도     {answered}/{len(should_answer)}   (답할 수 있는 질문에 답했는가)")
    if answered_ones:
        print(f"인용 전량 나열  {len(all_k)}/{len(answered_ones)}   "
              f"(인용 수가 TOP_K={rag.TOP_K}와 같음 — 0이어야 한다)")
        print(f"근거 다양성     {diversity:.2f}   (1.0이면 인용마다 다른 문서)")
    if times:
        middle = times[len(times) // 2]
        print(f"응답 시간       중앙값 {middle:.1f}초 · 최대 {times[-1]:.1f}초")


def _as_dict(outcome: Outcome) -> dict:
    return {
        "id": outcome.case_id,
        "question": outcome.question,
        "expect": outcome.expect,
        "withheld": outcome.withheld,
        "seconds": round(outcome.seconds, 2),
        "citations": outcome.citations,
        "distinct_docs": outcome.distinct_docs,
        "answer": outcome.answer,
        "failures": outcome.failures,
        "passed": outcome.passed,
    }


def _load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rag_report",
        description="질문 화면의 정확도·유보·근거 품질을 실제 프로젝트로 잰다",
    )
    parser.add_argument("--project", default="default", help="프로젝트 이름")
    parser.add_argument("--data", default=None,
                        help=f"프로젝트 저장 위치 (기본: {default_project_dir()})")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="평가셋 경로")
    parser.add_argument("--case", default=None, help="사례 하나만 실행")
    parser.add_argument("--json", default=None, help="결과를 JSON으로 저장할 경로")
    return parser.parse_args(argv)


def _force_utf8() -> None:
    """윈도우 콘솔이 cp949면 한글 출력에서 죽는다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
