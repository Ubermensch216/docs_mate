"""프롬프트 템플릿 로더.

프롬프트는 코드에 흩어두지 않고 버전 있는 파일로 관리한다. 어떤 결과가 어느
프롬프트로 나왔는지 추적해야 모델·프롬프트를 바꾼 뒤 재분석 대상을 고를 수 있다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).with_name("prompts")
_VERSION = re.compile(r"<!--\s*version:\s*(\S+)\s*-->")
_COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)


@lru_cache(maxsize=32)
def load(name: str) -> tuple[str, str]:
    """(본문, 버전)을 돌려준다."""
    path = PROMPT_DIR / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    match = _VERSION.search(raw)
    version = match.group(1) if match else "0"
    body = _COMMENT.sub("", raw).strip()
    return body, version


def render(name: str, **values: str) -> tuple[str, str]:
    body, version = load(name)
    return body.format(**values), f"{name}@{version}"
