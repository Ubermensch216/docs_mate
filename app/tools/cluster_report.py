"""업무 분류 실측 리포트 — 사람이 만든 정답과 대조한다 (계획서 §32, A-5).

    python -m app.tools.cluster_report [--project 이름] [--data 경로]
                                       [--truth 정답.csv] [--json 경로]

왜 필요한가
  "업무가 7개로 추정됩니다"가 이 제품의 첫 문장이다. 그 숫자가 얼마나
  맞는지 재는 자가 없으면, 군집 임계값을 바꿀 때마다 좋아졌는지 나빠졌는지
  느낌으로 판단하게 된다. RAG를 rag_report 없이 고치지 않기로 한 것과 같은
  이유다.

무엇을 재는가 — 짝 기준(pairwise)
  묶음 이름은 비교할 수 없다. AI가 지은 '행정사무감사 요구자료'와 사람이
  적은 '행정사무감사'가 같은 것인지 기계가 알 수 없기 때문이다. 그래서
  **두 문서가 같은 묶음에 있는가**만 본다. 이름이 달라도 묶음이 같으면 맞다.

    정밀도  한 묶음에 넣은 짝 중 실제로 같은 업무였던 비율
            (낮으면 서로 다른 업무를 뭉갠 것 — 사용자가 가장 싫어한다)
    재현율  같은 업무인 짝 중 실제로 한 묶음이 된 비율
            (낮으면 한 업무가 여러 묶음으로 쪼개진 것)

  어느 업무에도 못 붙은 문서는 '혼자 있는 묶음'으로 센다. 미분류를 빼고
  재면 확신 없는 문서를 전부 버릴수록 점수가 오르는 자가 된다.

정답은 어디서 오는가
  `--truth`  파일: `파일명,업무` 두 칸짜리 CSV. 실제 자료를 쓸 때의 정식 경로다.
  기본값     파일명 둘째 토막(`2025_행정사무감사_제출자료.docx` → 행정사무감사).
             합성 표본(make_fixtures.py)에서만 성립하는 규칙이라 그 사실을
             화면에 함께 적는다.

프로젝트 DB는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

from ..db import Database, default_project_dir, open_project

UNCLASSIFIED = "(미분류)"


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)

    db = open_project(args.project, Path(args.data) if args.data else None)
    try:
        rows = documents(db)
        if not rows:
            print("분석된 문서가 없습니다. 먼저 자료를 등록하고 분석을 끝내세요.")
            return 2

        truth_map = _load_truth(Path(args.truth)) if args.truth else None
        pairs = [
            (row, _truth_of(row, truth_map))
            for row in rows
        ]
        graded = [(row, label) for row, label in pairs if label]
        skipped = len(pairs) - len(graded)

        print(f"프로젝트  {db.path}")
        print(f"정답      {'CSV ' + args.truth if args.truth else '파일명 규칙 (합성 표본 전용)'}")
        print(f"대상      문서 {len(graded):,}건"
              + (f" · 정답 없어 제외 {skipped:,}건" if skipped else ""))
        if not args.truth:
            print("          ⚠ 실제 자료를 잴 때는 --truth로 사람이 만든 정답을 주세요")
        print()

        if not graded:
            print("정답을 붙일 수 있는 문서가 없습니다.")
            return 2

        result = evaluate(
            {row["id"]: label for row, label in graded},
            {row["id"]: row["task"] or UNCLASSIFIED for row, label in graded},
        )
        _print_report(result, graded)
    finally:
        db.close()

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n결과를 저장했습니다: {args.json}")

    return 0


# ── 자료 ────────────────────────────────────────────────────────────
def documents(db: Database) -> list[dict]:
    """분석된 문서와 그 문서가 붙은 업무.

    한 문서가 여러 업무에 속할 수 있으므로(task_docs는 다대다) 이름이 앞선
    것 하나를 대표로 삼는다. 짝 기준 계산은 '한 문서 = 한 묶음'을 전제한다.
    복수 배정은 따로 세어 보고한다 — 흔해지면 이 전제부터 다시 봐야 한다.
    """
    rows = db.con.execute(
        """
        SELECT d.id, d.filename, d.path,
               (SELECT t.name FROM task_docs td JOIN tasks t ON t.id = td.task_id
                 WHERE td.doc_id = d.id AND t.not_a_task = 0 AND t.merged_into IS NULL
                 ORDER BY t.name LIMIT 1) AS task,
               (SELECT COUNT(*) FROM task_docs td WHERE td.doc_id = d.id) AS task_count
        FROM documents d
        WHERE d.missing_since IS NULL AND d.parse_status IN ('ok', 'partial')
        ORDER BY d.filename
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _truth_of(row: dict, truth_map: dict[str, str] | None) -> str:
    if truth_map is not None:
        return truth_map.get(row["filename"]) or truth_map.get(row["path"]) or ""
    return truth_from_filename(row["filename"])


def truth_from_filename(filename: str) -> str:
    """`2025_행정사무감사_제출자료_최종.docx` → `행정사무감사`.

    합성 표본의 이름 규칙(make_fixtures.py)에만 기댄다. 규칙에 맞지 않는
    파일은 빈 문자열을 돌려 채점에서 빠진다 — 억지로 이름을 붙이면 정답이
    아니라 잡음을 정답으로 삼는 것이 된다.
    """
    stem = Path(filename).stem
    parts = stem.split("_")
    if len(parts) < 3 or not parts[0].isdigit() or len(parts[0]) != 4:
        return ""
    return parts[1]


def _load_truth(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"정답 파일을 찾을 수 없습니다: {path}")
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2 or not row[0].strip():
                continue
            key, label = row[0].strip(), row[1].strip()
            if key in ("파일명", "filename", "path", "경로"):   # 머리글
                continue
            out[key] = label
    return out


# ── 채점 ────────────────────────────────────────────────────────────
def evaluate(truth: dict[int, str], predicted: dict[int, str]) -> dict:
    """짝 기준 정밀도·재현율. 미분류 문서는 각자 혼자 있는 묶음으로 센다."""
    ids = sorted(truth)
    unique = {doc_id: f"{UNCLASSIFIED}#{doc_id}" for doc_id in ids}
    cluster = {
        doc_id: (unique[doc_id] if predicted.get(doc_id, UNCLASSIFIED) == UNCLASSIFIED
                 else predicted[doc_id])
        for doc_id in ids
    }

    tp = fp = fn = 0
    merged: Counter[tuple[str, str]] = Counter()   # 서로 다른 업무를 한 묶음에
    split: Counter[str] = Counter()                # 한 업무가 여러 묶음으로
    for left, right in combinations(ids, 2):
        same_truth = truth[left] == truth[right]
        same_cluster = cluster[left] == cluster[right]
        if same_truth and same_cluster:
            tp += 1
        elif same_cluster:
            fp += 1
            merged[tuple(sorted((truth[left], truth[right])))] += 1
        elif same_truth:
            fn += 1
            split[truth[left]] += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "documents": len(ids),
        "unclassified": sum(
            1 for doc_id in ids if predicted.get(doc_id, UNCLASSIFIED) == UNCLASSIFIED
        ),
        "truth_tasks": len(set(truth.values())),
        "found_tasks": len({c for c in cluster.values() if not c.startswith(UNCLASSIFIED)}),
        "pairs": {"tp": tp, "fp": fp, "fn": fn},
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "merged": [{"tasks": list(key), "pairs": n} for key, n in merged.most_common(5)],
        "split": [{"task": key, "pairs": n} for key, n in split.most_common(5)],
    }


# ── 출력 ────────────────────────────────────────────────────────────
def _print_report(result: dict, graded: list[tuple[dict, str]]) -> None:
    print(f"정답 업무 {result['truth_tasks']}개  →  찾은 묶음 {result['found_tasks']}개")
    if result["unclassified"]:
        print(f"미분류    {result['unclassified']:,}건 (혼자 있는 묶음으로 셈)")
    multi = sum(1 for row, _label in graded if row["task_count"] > 1)
    if multi:
        print(f"복수 배정 {multi:,}건 (이름이 앞선 업무를 대표로 삼음)")
    print()
    print(f"정밀도    {result['precision']:.3f}   같은 묶음에 넣은 짝 중 실제로 같은 업무")
    print(f"재현율    {result['recall']:.3f}   같은 업무인 짝 중 실제로 한 묶음이 된 비율")
    print(f"F1        {result['f1']:.3f}")
    print(f"          맞게 묶음 {result['pairs']['tp']:,} · 잘못 묶음 {result['pairs']['fp']:,}"
          f" · 놓친 짝 {result['pairs']['fn']:,}")

    if result["merged"]:
        print("\n서로 다른 업무를 한 묶음에 넣은 사례")
        for item in result["merged"]:
            left, right = item["tasks"]
            print(f"  {left} × {right}   짝 {item['pairs']:,}건")
    if result["split"]:
        print("\n한 업무가 여러 묶음으로 갈린 사례")
        for item in result["split"]:
            print(f"  {item['task']}   놓친 짝 {item['pairs']:,}건")


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cluster_report",
        description="업무 분류 결과를 사람이 만든 정답과 대조한다 (읽기 전용).",
    )
    parser.add_argument("--project", default="default", help="프로젝트 이름")
    parser.add_argument("--data", default=None, help=f"저장 위치 (기본: {default_project_dir()})")
    parser.add_argument("--truth", default=None, help="정답 CSV (`파일명,업무`)")
    parser.add_argument("--json", default=None, help="결과를 JSON으로 저장할 경로")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
