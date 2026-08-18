"""연도별 업무 흐름 비교 — "작년과 무엇이 달라졌나" (계획서 §20).

단순 검색 제품이 못 하는 자리다. 파일을 아무리 잘 찾아 줘도 "2025년부터
사전검토가 들어갔다"는 말은 나오지 않는다. 그 말은 **같은 업무의 두 해를
나란히 놓아야** 나온다.

무엇을 비교하는가
    이미 재구성해 둔 처리 순서(task_steps)를 비교한다. 새로 추론하지
    않는다 — 비교가 원본 자료를 다시 해석하기 시작하면, 화면의 순서와
    비교 결과가 어긋나는 순간 둘 다 못 믿게 된다.

어떻게 짝짓는가
    두 해의 단계 이름을 **차집합이 아니라 순서를 지키는 대응(LCS)**으로
    맞춘다. 집합으로 빼면 "검토가 취합 앞으로 옮겨졌다"가 '검토 삭제 +
    검토 추가'로 보인다. 순서가 이 업무의 내용인데 그것을 버리는 셈이다.

같은 이름이 여러 번 나오는 업무
    분기 통계, 월간 실적처럼 **한 해에 같은 단계가 반복되는 업무**가 실제
    자료에 흔하다(실측: 수질통계 분기 4회, 실적취합 월 12회). 여기서 짝을
    못 지은 것을 '새 단계'라고 말하면 "2025년부터 '통계 작성' 단계가
    추가되었습니다"가 두 번 나온다 — 그 단계는 작년에도 있었다. 이름이 두
    해에 다 있으면 그것은 새 단계가 아니라 **횟수**가 달라진 것이고, 그렇게
    말해야 한다. 같은 이유로 반복되는 단계에는 시점 변화도 말하지 않는다.
    몇 번째 것과 몇 번째 것이 대응하는지 우리는 모른다.

가장 중요한 절제
    **없어진 단계를 '없앴다'고 말하지 않는다.** 자료가 남지 않은 것과
    실제로 그 단계를 그만둔 것을 우리는 구별할 수 없다. 문서 더미에서
    복원한 흐름이므로 "보이지 않습니다"까지가 우리가 아는 전부다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# 짝지음 결과. 화면의 두 칸이 이 상태로 칠해진다.
SAME, ADDED, GONE, MOVED, REPEAT = "same", "added", "gone", "moved", "repeat"
SHIFT = "shift"

# 시점이 이만큼 움직여야 '달라졌다'고 말한다. 한 달 차이는 그 해 문서
# 하나가 월말에 걸렸는지 월초에 걸렸는지로도 생기므로 변화가 아니다.
MONTH_SHIFT = 2


@dataclass(slots=True)
class Item:
    """비교에 쓰는 단계 하나. DB 행에서 필요한 것만 뽑아 둔다."""

    label: str
    month: int | None
    day_hint: str
    ordinal: int
    row: object = None          # 근거 서랍에 그대로 넘길 원래 행

    @property
    def key(self) -> str:
        """짝지음의 기준. 띄어쓰기만 무시한다 — '사전 검토'와 '사전검토'는
        같은 말이지만, '검토'와 '사전검토'는 다른 단계다(그것이 §20의 예다)."""
        return self.label.replace(" ", "")


@dataclass(slots=True)
class Pair:
    """화면 한 줄. 한쪽이 비면 그 해에는 없는 단계다."""

    left: Item | None
    right: Item | None
    state: str


@dataclass(slots=True)
class Change:
    """사람이 읽을 변화점 하나. 근거로 쓸 단계 행을 함께 들고 다닌다."""

    kind: str
    label: str
    sentence: str
    row: object = None


@dataclass(slots=True)
class Comparison:
    earlier: int
    later: int
    pairs: list[Pair] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return not self.changes

    def headline(self) -> str:
        if not self.pairs:
            return f"{self.earlier}년과 {self.later}년을 견줄 자료가 없습니다"
        if self.same:
            return f"{self.earlier}년과 {self.later}년의 처리 흐름은 같아 보입니다"
        return f"{self.later}년에 달라진 것으로 보이는 곳이 {len(self.changes)}군데 있습니다"


def _read(row) -> Item:
    def value(key, default=None):
        keys = row.keys() if hasattr(row, "keys") else row
        return row[key] if key in keys else default

    return Item(
        label=value("label", "") or "",
        month=value("month"),
        day_hint=value("day_hint", "") or "",
        ordinal=value("ordinal", 0) or 0,
        row=row,
    )


def _match(left: list[Item], right: list[Item]) -> dict[int, int]:
    """순서를 지키면서 가장 많이 겹치는 대응을 찾는다 (LCS).

    단계 수는 한 해에 많아야 열 몇 개다. O(n·m) 표로 충분하고, 여기서
    영리해질 이유가 없다.
    """
    n, m = len(left), len(right)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if left[i].key == right[j].key:
                table[i][j] = table[i + 1][j + 1] + 1
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])

    matched: dict[int, int] = {}
    i = j = 0
    while i < n and j < m:
        if left[i].key == right[j].key:
            matched[i] = j
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return matched


def _moved(
    left: list[Item], right: list[Item], matched: dict[int, int]
) -> tuple[set[int], set[int]]:
    """짝을 못 찾았지만 **양쪽에 다 있는** 이름. 없어진 것이 아니라 옮겨진 것이다.

    이것을 구분하지 않으면 순서만 바꾼 업무가 '단계 하나 없애고 하나 새로
    만든' 것으로 보고된다. 실제로 그해에 바뀐 것은 순서 하나뿐인데.
    """
    taken_right = set(matched.values())
    free_left = [i for i in range(len(left)) if i not in matched]
    free_right = [j for j in range(len(right)) if j not in taken_right]

    moved_left: set[int] = set()
    moved_right: set[int] = set()
    for i in free_left:
        for j in free_right:
            if j in moved_right:
                continue
            if left[i].key == right[j].key:
                moved_left.add(i)
                moved_right.add(j)
                break
    return moved_left, moved_right


def _shift_sentence(left: Item, right: Item) -> str | None:
    if left.month is None or right.month is None:
        return None
    delta = right.month - left.month
    if abs(delta) < MONTH_SHIFT:
        return None
    way = "앞당겨진" if delta < 0 else "늦춰진"
    return (
        f"'{right.label}'이 {left.month}월에서 {right.month}월로 "
        f"{abs(delta)}달 {way} 것으로 보입니다"
    )


def compare(earlier: int, earlier_steps, later: int, later_steps) -> Comparison:
    """두 해의 처리 순서를 나란히 놓는다. 이른 해가 왼쪽이다."""
    left = [_read(row) for row in earlier_steps]
    right = [_read(row) for row in later_steps]
    out = Comparison(earlier=earlier, later=later)
    if not left or not right:
        return out

    matched = _match(left, right)
    moved_left, moved_right = _moved(left, right, matched)
    taken_right = set(matched.values())

    tally_left = Counter(item.key for item in left)
    tally_right = Counter(item.key for item in right)
    # 두 해에 다 있는 이름은 새 단계도 없어진 단계도 아니다. 여러 번 나오는
    # 이름은 몇 번째끼리 대응하는지 알 수 없으므로 시점 변화도 말하지 않는다.
    both = {key for key in tally_left if key in tally_right}
    repeated = {key for key in both if tally_left[key] > 1 or tally_right[key] > 1}

    i = j = 0
    while i < len(left) or j < len(right):
        if i in matched and matched[i] == j:
            out.pairs.append(Pair(left[i], right[j], SAME))
            sentence = (
                None if right[j].key in repeated
                else _shift_sentence(left[i], right[j])
            )
            if sentence:
                out.changes.append(
                    Change(kind=SHIFT, label=right[j].label,
                           sentence=sentence, row=right[j].row)
                )
            i += 1
            j += 1
        elif j < len(right) and j not in taken_right and (
            i >= len(left) or i in matched
        ):
            item = right[j]
            state = MOVED if j in moved_right else (
                REPEAT if item.key in both else ADDED
            )
            out.pairs.append(Pair(None, item, state))
            if state == ADDED:
                out.changes.append(Change(
                    kind=ADDED, label=item.label,
                    sentence=f"{later}년부터 '{item.label}' 단계가 "
                             f"추가된 것으로 보입니다",
                    row=item.row,
                ))
            j += 1
        elif i < len(left):
            item = left[i]
            state = MOVED if i in moved_left else (
                REPEAT if item.key in both else GONE
            )
            out.pairs.append(Pair(item, None, state))
            if state == GONE:
                out.changes.append(Change(
                    kind=GONE, label=item.label,
                    # '없앴다'가 아니라 '보이지 않는다'. 자료가 안 남은 것과
                    # 실제로 그만둔 것을 우리는 구별할 수 없다.
                    sentence=f"{earlier}년에 있던 '{item.label}' 단계가 "
                             f"{later}년 자료에서는 보이지 않습니다",
                    row=item.row,
                ))
            i += 1
        else:
            j += 1

    _add_move_sentences(out, left, right, moved_left, moved_right, earlier, later)
    _add_repeat_sentences(out, left, right, tally_left, tally_right, earlier, later)
    return out


def _add_repeat_sentences(
    out: Comparison,
    left: list[Item],
    right: list[Item],
    tally_left: Counter,
    tally_right: Counter,
    earlier: int,
    later: int,
) -> None:
    """같은 단계를 몇 번 했는가. **이름마다 한 문장**이다.

    반복 횟수는 그 자체로 업무의 성격이다 — 분기 보고가 월간이 되었다면
    그것이 그해의 변화다. 다만 이것도 자료에서 읽은 것이라, 늘어난 것은
    "더 자주 한 것으로 보입니다"까지만 말한다.
    """
    for key in sorted(set(tally_left) & set(tally_right)):
        before, after = tally_left[key], tally_right[key]
        if before == after:
            continue
        item = next(one for one in right if one.key == key)
        way = "늘었" if after > before else "줄었"
        out.changes.append(Change(
            kind=REPEAT, label=item.label,
            sentence=f"'{item.label}'이 {earlier}년 {before}번에서 "
                     f"{later}년 {after}번으로 {way}습니다",
            row=item.row,
        ))


def _add_move_sentences(
    out: Comparison,
    left: list[Item],
    right: list[Item],
    moved_left: set[int],
    moved_right: set[int],
    earlier: int,
    later: int,
) -> None:
    """옮겨진 단계는 한 번만 말한다 — 왼쪽·오른쪽 두 번 말하면 두 가지 변화로 읽힌다."""
    for j in sorted(moved_right):
        item = right[j]
        was = next((left[i].ordinal for i in sorted(moved_left)
                    if left[i].key == item.key), None)
        if was is None:
            continue
        out.changes.append(Change(
            kind=MOVED, label=item.label,
            sentence=f"'{item.label}'이 {earlier}년 {was}번째에서 "
                     f"{later}년 {item.ordinal}번째로 옮겨진 것으로 보입니다",
            row=item.row,
        ))
