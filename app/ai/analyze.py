"""문서 단위 분석 — 작업을 쪼개서 부른다.

실측에서 제목·분류·연도·요약·키워드를 한 번에 요구하니 분류가 전부 '기타'로
몰렸고 9.8초가 걸렸다. 분류만 따로 부르면 2.5초에 정확도도 올랐다. 경량 모델은
한 번에 하나씩 시켜야 한다.

전수 처리는 하지 않는다. 문서 1만 건을 요약하면 27시간이 걸린다. 대신
  · 임베딩(67ms/건)으로 전수 처리하고
  · 생성 모델은 사용자가 연 문서나 묶음 대표에만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import schemas
from .client import OllamaClient
from .prompts_loader import render

# 프롬프트에 넣을 본문 길이. 길수록 느려지고, 문서 앞부분에 정체가 다 드러난다.
DOCUMENT_BUDGET = 2400


@dataclass(slots=True)
class Analysis:
    doc_id: int
    summary: str | None = None
    business_area: str | None = None
    confidence: str = "low"
    evidence: str | None = None
    keywords: list[str] = field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def summarize(client: OllamaClient, doc_id: int, text: str) -> Analysis:
    """요약 하나만 요청한다."""
    result = Analysis(doc_id=doc_id, model=client.gen_model)
    body = _budget(text)
    if not body:
        result.error = "요약할 본문이 없습니다"
        return result

    prompt, version = render("summarize", document=body)
    result.prompt_version = version
    data, error, _raw = client.generate_json(prompt, schemas.SUMMARIZE_SCHEMA, num_predict=220)
    if error:
        result.error = error
        return result

    result.summary = schemas.clean_summary(data.get("summary"))
    if not result.summary:
        result.error = "요약이 비어 있습니다"
    return result


def classify(
    client: OllamaClient,
    doc_id: int,
    text: str,
    categories: tuple[str, ...] | list[str] = schemas.DEFAULT_CATEGORIES,
) -> Analysis:
    """업무 분류 하나만 요청한다. 목록에 없는 답은 버린다."""
    result = Analysis(doc_id=doc_id, model=client.gen_model)
    body = _budget(text)
    if not body:
        result.error = "분류할 본문이 없습니다"
        return result

    prompt, version = render(
        "classify", categories=" / ".join(categories), document=body
    )
    result.prompt_version = version
    data, error, _raw = client.generate_json(
        prompt, schemas.classify_schema(categories), num_predict=120
    )
    if error:
        result.error = error
        return result

    area = schemas.clean_category(data.get("business_area"), categories)
    if area is None:
        # 목록 밖 이름을 지어냈다. 받아주면 업무 분류가 흩어진다.
        result.error = f"목록에 없는 분류: {data.get('business_area')!r}"
        return result

    result.business_area = area
    result.confidence = schemas.clean_confidence(data.get("confidence"))
    result.evidence = schemas.clean_summary(data.get("evidence"), limit=200)
    return result


def keywords(client: OllamaClient, doc_id: int, text: str) -> Analysis:
    result = Analysis(doc_id=doc_id, model=client.gen_model)
    body = _budget(text)
    if not body:
        result.error = "본문이 없습니다"
        return result

    prompt, version = render("keywords", document=body)
    result.prompt_version = version
    data, error, _raw = client.generate_json(prompt, schemas.KEYWORDS_SCHEMA, num_predict=120)
    if error:
        result.error = error
        return result

    result.keywords = schemas.clean_keywords(data.get("keywords"))
    if not result.keywords:
        result.error = "키워드가 비어 있습니다"
    return result


@dataclass(slots=True)
class TaskNaming:
    name: str | None = None
    description: str | None = None
    coherent: bool = False
    prompt_version: str = ""
    error: str | None = None


def name_task(client: OllamaClient, titles: list[str]) -> TaskNaming:
    """묶음 하나에 이름을 붙인다. 문서마다 부르지 않고 묶음당 한 번만 부른다."""
    naming = TaskNaming()
    if not titles:
        naming.error = "제목이 없습니다"
        return naming

    listing = "\n".join(f"- {t}" for t in titles[:40])
    prompt, version = render("name_task", titles=listing)
    naming.prompt_version = version
    data, error, _raw = client.generate_json(prompt, schemas.NAME_TASK_SCHEMA, num_predict=200)
    if error:
        naming.error = error
        return naming

    naming.name = schemas.clean_task_name(data.get("name"))
    naming.description = schemas.clean_summary(data.get("description"), limit=200)
    naming.coherent = bool(data.get("coherent"))
    if not naming.name:
        naming.error = f"쓸 수 없는 업무 이름: {data.get('name')!r}"
    return naming


def _budget(text: str) -> str:
    """앞부분만 쓴다. 문서의 정체는 표지·머리말에 거의 다 드러난다."""
    trimmed = " ".join((text or "").split())
    return trimmed[:DOCUMENT_BUDGET]
