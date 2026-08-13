"""Ollama 어댑터.

완전 로컬로만 통신한다. 루프백이 아닌 주소는 거부한다 — 업무 자료가 외부로
나가는 경로를 코드 수준에서 막는다(SEC-002).

이 계층은 예외를 밖으로 던지지 않는다. Ollama가 꺼져 있어도 스캔·검색·중복은
그대로 동작해야 하므로, 실패는 값으로 표현한다.

실측(2026-08, gemma4:e2b Q4_K_M 5.1B / bge-m3):
    통합 분석(제목+분류+연도+요약+키워드 한 번에)   9.8초/문서, 분류 정확도 낮음
    분류만 단독                                     2.5초/문서, 정확도 개선
    임베딩 배치 32                                  67ms/건
따라서 작업을 쪼개고, 전수 처리는 임베딩으로, 생성 모델은 대표 문서에만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_GEN_MODEL = "gemma4:e2b"
DEFAULT_EMBED_MODEL = "bge-m3"

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

CONNECT_TIMEOUT = 3.0
GENERATE_TIMEOUT = 300.0
EMBED_TIMEOUT = 300.0
EMBED_BATCH = 32

# 생성 모델과 임베딩 모델을 번갈아 부르면 메모리에서 서로를 밀어내 매번 다시
# 올라온다. 실측에서 임베딩 직후의 첫 요약이 10초가 아니라 46초 걸렸다.
# 모델을 잠시 붙잡아 두어 연속 작업에서 재적재를 줄인다.
KEEP_ALIVE = "10m"


@dataclass(slots=True)
class ModelProfile:
    name: str
    parameter_size: str = ""
    quantization: str = ""
    context_length: int = 0
    capabilities: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [self.name]
        if self.parameter_size:
            parts.append(self.parameter_size)
        if self.quantization:
            parts.append(self.quantization)
        if self.context_length:
            parts.append(f"컨텍스트 {self.context_length:,}")
        return " · ".join(parts)


@dataclass(slots=True)
class Health:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)
    generation_ready: bool = False
    embedding_ready: bool = False


class OllamaError(Exception):
    """이 모듈 밖으로 나가지 않는다. 호출자에게는 값으로 알린다."""


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        gen_model: str = DEFAULT_GEN_MODEL,
        embed_model: str = DEFAULT_EMBED_MODEL,
    ):
        self.base_url = _require_loopback(base_url)
        self.gen_model = gen_model
        self.embed_model = embed_model

    # ── 상태 ────────────────────────────────────────────────────────
    def health(self) -> Health:
        """연결과 모델 설치 여부를 확인한다. 절대 예외를 던지지 않는다."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=CONNECT_TIMEOUT)
            response.raise_for_status()
            names = [m["name"] for m in response.json().get("models", [])]
        except httpx.ConnectError:
            return Health(False, "Ollama에 연결할 수 없습니다. 실행 중인지 확인하세요.")
        except httpx.HTTPError as exc:
            return Health(False, f"Ollama 응답 오류: {exc}")
        except (ValueError, KeyError) as exc:
            return Health(False, f"Ollama 응답을 이해할 수 없습니다: {exc}")

        generation = _has_model(names, self.gen_model)
        embedding = _has_model(names, self.embed_model)
        if generation and embedding:
            message = "연결됨"
        else:
            missing = [
                name for name, ok in
                ((self.gen_model, generation), (self.embed_model, embedding)) if not ok
            ]
            message = f"모델이 없습니다: {', '.join(missing)}"
        return Health(
            ok=generation or embedding,
            message=message,
            models=names,
            generation_ready=generation,
            embedding_ready=embedding,
        )

    def profile(self, model: str | None = None) -> ModelProfile | None:
        target = model or self.gen_model
        try:
            response = httpx.post(
                f"{self.base_url}/api/show", json={"model": target}, timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        details = data.get("details", {})
        info = data.get("model_info", {})
        context = next(
            (v for k, v in info.items() if k.endswith("context_length")), 0
        )
        return ModelProfile(
            name=target,
            parameter_size=details.get("parameter_size", ""),
            quantization=details.get("quantization_level", ""),
            context_length=int(context or 0),
            capabilities=list(data.get("capabilities", [])),
        )

    # ── 생성 ────────────────────────────────────────────────────────
    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int = 400,
        temperature: float = 0.0,
    ) -> tuple[dict | None, str | None, str]:
        """JSON Schema를 강제해 구조화 응답을 받는다.

        Ollama의 format 파라미터가 문법을 강제하므로 파싱 실패는 드물다.
        그래도 실패하면 (None, 사유, 원문)으로 돌려준다. 원문을 함께 주는
        이유: 토큰 한도에 걸려 중간에 잘린 응답도 완성된 부분은 건질 수
        있다 — 그 복구는 스키마마다 뜻이 다르므로 호출자(예: search/rag.py)
        가 판단한다. 이 계층은 스키마를 모른다.
        """
        payload = {
            "model": self.gen_model,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate", json=payload, timeout=GENERATE_TIMEOUT
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException:
            return None, "모델 응답 시간 초과", ""
        except httpx.ConnectError:
            return None, "Ollama 연결 끊김", ""
        except httpx.HTTPError as exc:
            return None, f"모델 호출 실패: {exc}", ""
        except ValueError as exc:
            return None, f"응답을 읽을 수 없음: {exc}", ""

        import json

        raw = body.get("response", "")
        try:
            return json.loads(raw), None, raw
        except json.JSONDecodeError as exc:
            return None, f"JSON 형식 오류: {exc}", raw

    # ── 임베딩 ──────────────────────────────────────────────────────
    def embed(self, texts: list[str]) -> tuple[list[list[float]] | None, str | None]:
        """배치로 임베딩한다. 배치 32에서 건당 67ms로 가장 효율적이었다."""
        if not texts:
            return [], None
        try:
            response = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": texts},
                timeout=EMBED_TIMEOUT,
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings")
        except httpx.TimeoutException:
            return None, "임베딩 시간 초과"
        except httpx.ConnectError:
            return None, "Ollama 연결 끊김"
        except httpx.HTTPError as exc:
            return None, f"임베딩 호출 실패: {exc}"
        except ValueError as exc:
            return None, f"응답을 읽을 수 없음: {exc}"

        if not isinstance(vectors, list) or len(vectors) != len(texts):
            return None, "임베딩 개수가 입력과 다릅니다"
        return vectors, None


def _has_model(installed: list[str], wanted: str) -> bool:
    """'gemma4:e2b'와 'gemma4:e2b-instruct' 같은 태그 차이를 흡수한다."""
    base = wanted.split(":")[0]
    return any(name == wanted or name.split(":")[0] == base for name in installed)


def _require_loopback(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host not in LOOPBACK:
        raise ValueError(
            f"로컬 주소만 허용합니다 (요청: {base_url}). "
            "업무 자료가 외부로 나가지 않도록 강제하는 제약입니다."
        )
    return base_url.rstrip("/")
