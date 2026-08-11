"""파서 공통 계약.

모든 파서는 예외를 밖으로 던지지 않고 ParseResult의 status/error로 결과를
표현한다. 파일 하나의 실패가 전체 조사를 멈추면 안 되기 때문이다(PRD ING-007).

Section의 locator는 나중에 근거 표시(RAG 출처)에 그대로 쓰인다. 파싱 단계에서
좌표를 만들어 두지 않으면 나중에 끼워 넣을 수 없으므로 처음부터 채운다(PAR-002).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PARSER_VERSION = "1"

# 파싱 상태
OK = "ok"                    # 본문 정상 추출
PARTIAL = "partial"          # 일부만 추출 (미리보기 폴백, 텍스트 레이어 없음 등)
EMPTY = "empty"              # 읽었으나 텍스트가 없음
FAILED = "failed"            # 추출 실패
UNSUPPORTED = "unsupported"  # 지원하지 않는 형식·버전
ENCRYPTED = "encrypted"      # 암호 문서 — 해제를 시도하지 않는다 (PAR-006)
TOO_LARGE = "too_large"      # 크기 상한 초과 (PAR-007)

# 안전 한도 (PAR-007)
MAX_BYTES = 200 * 1024 * 1024
MAX_CHARS = 2_000_000
MAX_CELLS_PER_SHEET = 20_000


@dataclass(slots=True)
class Section:
    """원문 위치를 유지한 텍스트 조각."""

    kind: str      # paragraph | table | page | sheet | slide | note | preview
    ordinal: int
    locator: str   # "3쪽" | "'검사결과' B4:F28" | "슬라이드 7" | "12문단"
    text: str


@dataclass(slots=True)
class DocMeta:
    """문서 내부 메타데이터. 시점 추정(core/dating.py)에서 쓰인다."""

    title: str | None = None
    author: str | None = None
    created: str | None = None
    modified: str | None = None


@dataclass(slots=True)
class ParseResult:
    status: str
    parser: str
    sections: list[Section] = field(default_factory=list)
    meta: DocMeta = field(default_factory=DocMeta)
    error: str | None = None
    note: str | None = None
    elapsed_ms: int = 0
    parser_version: str = PARSER_VERSION

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.sections if s.text)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.sections)

    @property
    def ok(self) -> bool:
        return self.status in (OK, PARTIAL)


def clip(text: str, budget: list[int]) -> str:
    """문서 전체 추출량이 MAX_CHARS를 넘지 않도록 자른다.

    budget은 남은 글자 수를 담은 1칸 리스트로, 호출할 때마다 줄어든다.
    """
    if budget[0] <= 0:
        return ""
    if len(text) > budget[0]:
        text = text[: budget[0]]
    budget[0] -= len(text)
    return text


def failed(parser: str, error: str, status: str = FAILED) -> ParseResult:
    return ParseResult(status=status, parser=parser, error=error)
