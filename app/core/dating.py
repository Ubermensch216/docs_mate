"""문서의 '시점' 추정 — 이 제품의 급소.

When(언제 하는 일인가)과 How(작년엔 어떤 순서였나)가 전부 이 하나에 걸려 있다.
그런데 파일시스템 수정일은 믿을 수 없다. 2024년 9월 행감 자료를 2025년 3월에
폴더째 복사하면 수정일이 전부 2025-03이 되고, 타임라인·일정·처리순서가 통째로
무너진다.

그래서 근거를 다섯 갈래로 나눠 모으고, 우선순위대로 하나를 채택하되 **후보를
버리지 않는다.** 화면에서 "무엇을 근거로 2024년이라 했는지" 보여줄 수 있어야
하고, 추정 규칙을 고쳐도 재파싱 없이 다시 계산할 수 있어야 하기 때문이다.

    ① body      본문 내 날짜        "2024. 9. 12."      가장 신뢰
    ② filename  파일명 내 날짜      2024_행감_요구자료
    ③ meta      문서 속성 작성일
    ④ folder    경로 내 연도        \2024\
    ⑤ fs        파일 수정일                              최후 수단

⑤로만 판정된 문서는 주기 계산에서 제외한다. 오염된 근거로 일정을 만드느니
만들지 않는 편이 낫다.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# 근거 갈래 (우선순위 순)
BODY, FILENAME, META, FOLDER, FS = "body", "filename", "meta", "folder", "fs"
PRIORITY = (BODY, FILENAME, META, FOLDER, FS)

# 정밀도
DAY, MONTH, YEAR = "day", "month", "year"
PRECISION_RANK = {DAY: 3, MONTH: 2, YEAR: 1}

MIN_YEAR = 1990
BODY_SCAN_CHARS = 4000   # 본문 앞부분에 문서 자신의 날짜가 있을 가능성이 높다

# 작성일을 명시한 곳은 다른 어떤 날짜보다 강한 근거다.
_LABEL = r"(?:작성일자?|작성|기준일자?|시행일자?|발행일자?|보고일자?|접수일자?|제출일자?|일자)\s*[:：]?\s*"

# 구분자에 밑줄을 넣는다. 공직 파일명은 2024_09_월간실적처럼 밑줄을 쓴다.
_SEP = r"[._\-/]"
_FULL = (
    r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*(?:년\s*|" + _SEP + r"\s*)"
    r"(?P<m>1[0-2]|0?[1-9])\s*(?:월\s*|" + _SEP + r"\s*)"
    r"(?P<d>3[01]|[12]\d|0?[1-9])\s*일?"
)
# 월 뒤에 숫자가 더 오면 연-월-일이므로 여기서 잡지 않는다.
_YM = (
    r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*(?:년\s*|" + _SEP + r"\s*)"
    r"(?P<m>1[0-2]|0?[1-9])\s*(?:월|\.)?(?![\s._\-/]*\d)"
)
_YEAR = r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*(?:회계연도|년도|년|학년도)?(?!\d)"
_COMPACT = r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])(?!\d)"

_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(_COMPACT), DAY),
    (re.compile(_FULL), DAY),
    (re.compile(_YM), MONTH),
    (re.compile(_YEAR), YEAR),
)
_LABELED = tuple(
    (re.compile(_LABEL + pattern.pattern), precision) for pattern, precision in _PATTERNS
)

# 파일명·경로에서 연도로 오인하기 쉬운 것들
_NOISE = re.compile(r"(?:제\s*|호|번|쪽|p\.?)\s*$", re.IGNORECASE)


@dataclass(slots=True)
class DateCandidate:
    kind: str
    value: str        # YYYY-MM-DD (미상 부분은 01로 채우고 precision으로 구분)
    precision: str
    raw: str          # 판정 근거가 된 원문
    locator: str = "" # 어느 조각에서 나왔는지
    labeled: bool = False

    @property
    def year(self) -> int:
        return int(self.value[:4])

    @property
    def month(self) -> int:
        return int(self.value[5:7])

    def as_row(self) -> tuple[str, str, str, str, str]:
        return (self.kind, self.value, self.precision, self.raw, self.locator)


@dataclass(slots=True)
class Resolution:
    """채택 결과와 그 근거."""

    value: str | None
    kind: str | None
    precision: str | None
    candidates: list[DateCandidate]

    @property
    def year(self) -> int | None:
        return int(self.value[:4]) if self.value else None

    @property
    def month(self) -> int | None:
        return int(self.value[5:7]) if self.value else None

    @property
    def trustworthy(self) -> bool:
        """주기 계산에 써도 되는가. 파일 수정일뿐이면 쓰지 않는다."""
        return self.kind is not None and self.kind != FS


# ── 갈래별 추출 ─────────────────────────────────────────────────────

def from_text(text: str, locator: str = "", kind: str = BODY) -> list[DateCandidate]:
    """본문에서 날짜 후보를 모은다.

    작성일을 명시한 표현이 있으면 그것만 쓴다. 없으면 앞부분에서 가장 자주
    나오는 연도를 문서의 연도로 보고, 그 연도의 가장 정밀한 날짜를 고른다.
    (2024년 문서는 본문에서 2024를 여러 번 말하고 2023은 참고로 한두 번 말한다.)
    """
    if not text:
        return []
    head = text[:BODY_SCAN_CHARS]

    labeled = _scan(head, _LABELED, kind, locator, labeled=True)
    if labeled:
        best = max(labeled, key=lambda c: (PRECISION_RANK[c.precision], -_position(head, c.raw)))
        return [best]

    found = _scan(head, _PATTERNS, kind, locator)
    if not found:
        return []

    votes = Counter(c.year for c in found)
    top_year, _count = votes.most_common(1)[0]
    same_year = [c for c in found if c.year == top_year]
    best = max(same_year, key=lambda c: (PRECISION_RANK[c.precision], -_position(head, c.raw)))
    return [best]


def from_filename(name: str) -> list[DateCandidate]:
    """파일명에서 날짜를 찾는다. 확장자는 떼고 본다."""
    stem = Path(name).stem
    found = _scan(stem, _PATTERNS, FILENAME, "파일명")
    if not found:
        return []
    return [max(found, key=lambda c: PRECISION_RANK[c.precision])]


def from_meta(created: str | None, modified: str | None = None) -> list[DateCandidate]:
    """문서 속성의 작성일. 형식이 제각각이라 파싱에 실패하면 조용히 버린다."""
    for raw, _label in ((created, "작성일"), (modified, "수정일")):
        if not raw:
            continue
        parsed = _parse_meta_datetime(raw)
        if parsed:
            return [DateCandidate(META, parsed, DAY, str(raw), "문서 속성")]
    return []


def from_path(path: str | Path) -> list[DateCandidate]:
    """경로 폴더명에서 연도를 찾는다. 가장 깊은(구체적인) 폴더를 우선한다."""
    parts = Path(path).parent.parts
    for part in reversed(parts):
        found = _scan(part, _PATTERNS, FOLDER, f"폴더 '{part}'")
        if found:
            return [max(found, key=lambda c: PRECISION_RANK[c.precision])]
    return []


def from_filesystem(mtime: str | float | None) -> list[DateCandidate]:
    """최후 수단. 복사 한 번에 오염되므로 신뢰도가 가장 낮다."""
    if mtime is None:
        return []
    try:
        if isinstance(mtime, (int, float)):
            stamp = datetime.fromtimestamp(mtime)
        else:
            stamp = datetime.fromisoformat(str(mtime))
    except (ValueError, OSError, OverflowError):
        return []
    if not _valid_year(stamp.year):
        return []
    return [DateCandidate(FS, stamp.date().isoformat(), DAY, str(mtime), "파일 수정일")]


# ── 채택 ────────────────────────────────────────────────────────────

def resolve(candidates: list[DateCandidate]) -> Resolution:
    """우선순위대로 하나를 채택한다. 후보는 전부 남긴다."""
    by_kind: dict[str, list[DateCandidate]] = {}
    for candidate in candidates:
        by_kind.setdefault(candidate.kind, []).append(candidate)

    for kind in PRIORITY:
        group = by_kind.get(kind)
        if not group:
            continue
        chosen = max(group, key=lambda c: (c.labeled, PRECISION_RANK[c.precision]))
        return Resolution(chosen.value, chosen.kind, chosen.precision, candidates)
    return Resolution(None, None, None, candidates)


def collect(
    *,
    text: str = "",
    sections: list[tuple[str, str]] | None = None,
    filename: str = "",
    path: str | Path = "",
    meta_created: str | None = None,
    meta_modified: str | None = None,
    fs_mtime: str | float | None = None,
) -> Resolution:
    """문서 하나의 모든 갈래를 모아 채택까지 한 번에 처리한다.

    sections를 주면 (locator, text) 순서대로 훑어 어느 조각에서 나온
    날짜인지 근거 위치까지 남긴다.
    """
    candidates: list[DateCandidate] = []

    if sections:
        # 첫 조각에서 찾자마자 멈추면 안 된다. 표제 줄에 연도만 적혀 있고
        # 바로 다음 줄에 "작성일: 2024. 9. 12."가 오는 문서가 흔하기 때문이다.
        # 앞쪽 조각을 모두 훑은 뒤 명시 표기와 정밀도로 고른다.
        ranked: list[tuple[bool, int, int, DateCandidate]] = []
        for order, (locator, chunk) in enumerate(sections):
            for candidate in from_text(chunk, locator):
                ranked.append(
                    (candidate.labeled, PRECISION_RANK[candidate.precision], -order, candidate)
                )
        if ranked:
            candidates.append(max(ranked, key=lambda item: item[:3])[3])
    elif text:
        candidates.extend(from_text(text))

    if filename:
        candidates.extend(from_filename(filename))
    candidates.extend(from_meta(meta_created, meta_modified))
    if path:
        candidates.extend(from_path(path))
    candidates.extend(from_filesystem(fs_mtime))

    return resolve(candidates)


# ── 내부 ────────────────────────────────────────────────────────────

def _scan(
    text: str,
    patterns: tuple[tuple[re.Pattern, str], ...],
    kind: str,
    locator: str,
    labeled: bool = False,
) -> list[DateCandidate]:
    """정밀한 패턴부터 훑고, 잡힌 자리는 가려서 중복 검출을 막는다.

    가리지 않으면 "2024. 9. 12."가 연-월-일로도, 연-월로도, 연도로도 잡혀
    같은 날짜가 세 번 세어진다.

    다만 **유효한 후보를 만든 자리만** 가린다. "2024-02-30"처럼 모양은 맞지만
    존재하지 않는 날짜는 자리를 열어 둬야 뒤 패턴이 연도라도 건진다.
    """
    out: list[DateCandidate] = []
    working = text
    for pattern, precision in patterns:
        spans: list[tuple[int, int]] = []
        for match in pattern.finditer(working):
            candidate = _build(match, precision, kind, locator, labeled)
            if candidate:
                out.append(candidate)
                spans.append(match.span())
        if spans:
            working = _mask(working, spans)
    return out


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def _build(
    match: re.Match, precision: str, kind: str, locator: str, labeled: bool
) -> DateCandidate | None:
    groups = match.groupdict()
    year = int(groups["y"])
    if not _valid_year(year):
        return None

    raw = match.group(0).strip()
    if _NOISE.search(text_before(match)):
        return None

    month = int(groups.get("m") or 1)
    day = int(groups.get("d") or 1)
    if not 1 <= month <= 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    if not 1 <= day <= last_day:
        if precision == DAY:
            return None
        day = 1

    value = f"{year:04d}-{month:02d}-{day:02d}"
    return DateCandidate(kind, value, precision, raw, locator, labeled)


def text_before(match: re.Match, width: int = 3) -> str:
    start = max(0, match.start() - width)
    return match.string[start : match.start()]


def _position(text: str, raw: str) -> int:
    index = text.find(raw)
    return index if index >= 0 else len(text)


def _valid_year(year: int) -> bool:
    return MIN_YEAR <= year <= date.today().year + 1


def _parse_meta_datetime(raw: str) -> str | None:
    text = str(raw).strip()
    # PDF는 "D:20240912153000+09'00'" 형태를 쓴다.
    if text.startswith("D:"):
        text = text[2:]
    if len(text) >= 8 and text[:8].isdigit():
        try:
            stamp = datetime.strptime(text[:8], "%Y%m%d")
            return stamp.date().isoformat() if _valid_year(stamp.year) else None
        except ValueError:
            return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.date().isoformat() if _valid_year(stamp.year) else None
