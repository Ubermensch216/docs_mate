"""구조화 응답 스키마와 정규화.

Ollama의 format 파라미터에 JSON Schema를 그대로 넘겨 문법을 강제한다.
그래도 모델은 "2024년"처럼 한국어 표기를 섞어 보내므로, 받은 뒤 한 번 더
정규화한다.

작업 하나에 스키마 하나다. 실측에서 여러 항목을 한 번에 요구하면 분류가
전부 '기타'로 몰렸다 — 경량 모델에는 작업을 쪼개 주어야 한다.
"""

from __future__ import annotations

import re
from typing import Any

# AI가 매번 새 이름을 지어내면 업무 분류가 분산된다. 목록에서만 고르게 한다.
DEFAULT_CATEGORIES = (
    "예산", "계약", "의회", "감사", "통계", "인사", "민원", "회의", "계획", "실적",
)
CONFIDENCE = ("high", "medium", "low")

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_area": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "evidence": {"type": "string"},
    },
    "required": ["business_area", "confidence"],
}

SUMMARIZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

KEYWORDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
        }
    },
    "required": ["keywords"],
}

ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "answer": {"type": "string"},
    },
    "required": ["answered", "answer"],
}

NAME_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "coherent": {"type": "boolean"},
    },
    "required": ["name", "description", "coherent"],
}


def classify_schema(categories: tuple[str, ...] | list[str]) -> dict[str, Any]:
    schema = {k: dict(v) if isinstance(v, dict) else v for k, v in CLASSIFY_SCHEMA.items()}
    schema["properties"] = dict(CLASSIFY_SCHEMA["properties"])
    schema["properties"]["business_area"] = {"type": "string", "enum": list(categories)}
    return schema


# ── 정규화 ──────────────────────────────────────────────────────────

def clean_category(value: Any, categories: tuple[str, ...] | list[str]) -> str | None:
    """목록에 없는 값은 버린다. 모델이 새 이름을 만들어도 받아주지 않는다."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text in categories else None


def clean_confidence(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in CONFIDENCE:
        return value.strip().lower()
    return "low"


def clean_summary(value: Any, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clean_keywords(value: Any, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        word = " ".join(item.split()).strip("#·,.")
        if word and word not in out and len(word) <= 30:
            out.append(word)
        if len(out) >= limit:
            break
    return out


def clean_task_name(value: Any) -> str | None:
    """연도·확장자가 섞인 이름을 걸러 업무 분류가 흩어지지 않게 한다."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip("[]()<>「」“”\"' ")
    text = re.sub(r"\b(?:19|20)\d{2}\s*(?:년도?|회계연도)?\b", "", text).strip()
    text = re.sub(r"\.(?:hwp|hwpx|docx?|xlsx?|pptx?|pdf|csv|txt)\b", "", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text).strip(" -_·")
    if not (2 <= len(text) <= 20):
        return None
    return text
