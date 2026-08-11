"""로컬 AI 계층.

원칙
  1. AI가 없어도 조사·검색·중복은 그대로 동작한다. 이 계층은 단일 장애점이 아니다.
  2. 작업을 쪼개서 부른다. 경량 모델에 여러 항목을 한 번에 시키면 품질이 무너진다.
  3. 전수 처리는 임베딩으로, 생성 모델은 대표에만. 1만 건 요약은 27시간이 걸린다.
  4. 결과는 제안이다. 사람이 고친 값을 덮어쓰지 않는다.
"""

from .analyze import Analysis, TaskNaming, classify, keywords, name_task, summarize
from .client import Health, ModelProfile, OllamaClient

__all__ = [
    "Analysis",
    "Health",
    "ModelProfile",
    "OllamaClient",
    "TaskNaming",
    "classify",
    "keywords",
    "name_task",
    "summarize",
]
