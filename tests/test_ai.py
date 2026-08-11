"""AI 계층 시험.

가장 중요한 계약: **AI가 없어도 나머지가 동작한다.** 그래서 Ollama가 꺼진
상황을 정상 경로로 다룬다. 예외가 밖으로 새어 나오면 실패다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai import OllamaClient, schemas  # noqa: E402
from app.ai.prompts_loader import load, render  # noqa: E402
from app.db import Database  # noqa: E402
from app.jobs.pipeline import Pipeline  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "sample_tree"
DEAD = "http://127.0.0.1:1"       # 닫힌 포트


@pytest.fixture(scope="session", autouse=True)
def qapp():
    yield QApplication.instance() or QCoreApplication.instance() or QApplication([])


# ── 로컬 강제 ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.5:11434", "https://ollama.example.com", "http://192.168.0.2:11434"],
)
def test_client_refuses_non_loopback_addresses(url):
    """업무 자료가 외부로 나가는 경로를 코드 수준에서 막는다 (SEC-002)."""
    with pytest.raises(ValueError):
        OllamaClient(base_url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434"])
def test_client_accepts_loopback(url):
    assert OllamaClient(base_url=url).base_url.startswith("http")


# ── Ollama가 꺼져 있을 때 ───────────────────────────────────────────

def test_health_reports_failure_without_raising():
    health = OllamaClient(base_url=DEAD).health()
    assert health.ok is False
    assert health.message
    assert health.generation_ready is False
    assert health.embedding_ready is False


def test_generate_returns_error_value_not_exception():
    data, error = OllamaClient(base_url=DEAD).generate_json(
        "안녕", schemas.SUMMARIZE_SCHEMA
    )
    assert data is None
    assert error


def test_embed_returns_error_value_not_exception():
    vectors, error = OllamaClient(base_url=DEAD).embed(["문서"])
    assert vectors is None
    assert error


def test_profile_returns_none_when_unreachable():
    assert OllamaClient(base_url=DEAD).profile() is None


def test_embed_of_empty_list_is_a_no_op():
    vectors, error = OllamaClient(base_url=DEAD).embed([])
    assert vectors == []
    assert error is None


# ── 스키마 정규화 ───────────────────────────────────────────────────

def test_category_outside_the_list_is_rejected():
    """모델이 새 이름을 지어내면 업무 분류가 흩어진다. 받아주지 않는다."""
    assert schemas.clean_category("예산", schemas.DEFAULT_CATEGORIES) == "예산"
    assert schemas.clean_category("수질 및 환경 관리", schemas.DEFAULT_CATEGORIES) is None
    assert schemas.clean_category(None, schemas.DEFAULT_CATEGORIES) is None


def test_classify_schema_pins_the_enum():
    schema = schemas.classify_schema(["예산", "계약"])
    assert schema["properties"]["business_area"]["enum"] == ["예산", "계약"]
    # 원본을 오염시키면 다음 호출이 망가진다
    assert "enum" not in schemas.CLASSIFY_SCHEMA["properties"]["business_area"]


def test_confidence_defaults_to_low_when_unusable():
    assert schemas.clean_confidence("HIGH") == "high"
    assert schemas.clean_confidence("아주 높음") == "low"
    assert schemas.clean_confidence(None) == "low"


def test_summary_is_collapsed_and_capped():
    assert schemas.clean_summary("  여러   공백 \n 줄바꿈 ") == "여러 공백 줄바꿈"
    assert schemas.clean_summary("") is None
    assert len(schemas.clean_summary("가" * 500)) == 300


def test_keywords_drop_duplicates_and_junk():
    got = schemas.clean_keywords(["예산", "예산", 42, "  수질  ", "", "가" * 40])
    assert got == ["예산", "수질"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("행정사무감사", "행정사무감사"),
        ("2024년 예산관리", "예산관리"),
        ("  예산관리.hwp ", "예산관리"),
        ("「수질통계」", "수질통계"),
    ],
)
def test_task_name_is_stripped_of_years_and_extensions(raw, expected):
    assert schemas.clean_task_name(raw) == expected


@pytest.mark.parametrize("raw", ["가", "2024", "", None, "가" * 30])
def test_unusable_task_names_are_rejected(raw):
    assert schemas.clean_task_name(raw) is None


# ── 프롬프트 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["classify", "summarize", "keywords", "name_task"])
def test_prompts_load_with_a_version(name):
    body, version = load(name)
    assert body and "<!--" not in body
    assert version != "0", f"{name}.md 에 version 주석이 없습니다"


def test_render_reports_which_prompt_version_was_used():
    prompt, version = render("summarize", document="본문")
    assert "본문" in prompt
    assert version.startswith("summarize@")


def test_each_prompt_asks_for_a_single_task():
    """경량 모델에 여러 항목을 한 번에 시키면 분류가 전부 '기타'로 몰린다(실측)."""
    classify_body, _ = load("classify")
    assert "요약" not in classify_body
    summarize_body, _ = load("summarize")
    assert "분류" not in summarize_body


# ── 파이프라인은 AI 없이도 끝난다 ───────────────────────────────────

@pytest.mark.skipif(not FIXTURES.is_dir(), reason="표본을 먼저 생성하세요")
def test_pipeline_completes_when_ollama_is_unavailable(tmp_path: Path, monkeypatch):
    """이것이 Step 5의 게이트다. AI는 단일 장애점이 아니다."""
    tree = tmp_path / "자료"
    shutil.copytree(FIXTURES, tree)

    import app.jobs.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "OllamaClient", lambda *a, **k: OllamaClient(base_url=DEAD)
    )

    db = Database(tmp_path / "p.db")
    db.init()
    db.add_source(tree)
    db.close()

    Pipeline(tmp_path / "p.db").run()

    db = Database(tmp_path / "p.db")
    try:
        counts = db.counts()
        assert counts["parsed"] > 0, "AI가 없다고 본문 읽기가 멈췄습니다"
        assert counts["dated"] > 0, "AI가 없다고 시점 추정이 멈췄습니다"
        assert counts["duplicate_extra"] > 0, "AI가 없다고 중복 확인이 멈췄습니다"
        assert counts["embedded"] == 0, "연결도 안 되는데 임베딩이 생겼습니다"
    finally:
        db.close()


# ── 실제 Ollama가 있을 때만 (선택) ──────────────────────────────────

def _ollama_ready() -> bool:
    if os.environ.get("WORKMEMORY_LIVE_AI") != "1":
        return False
    return OllamaClient().health().embedding_ready


# 기본 실행에서는 건너뛴다. 실제 모델을 부르면 생성·임베딩 모델이 메모리에서
# 서로를 밀어내 재적재가 일어나고, 시험 시간이 60초에서 250초로 뛴다.
#     WORKMEMORY_LIVE_AI=1 python -m pytest tests/test_ai.py
live = pytest.mark.skipif(
    not _ollama_ready(), reason="WORKMEMORY_LIVE_AI=1 과 Ollama·bge-m3가 필요합니다"
)


@live
def test_live_embeddings_have_expected_shape():
    client = OllamaClient()
    vectors, error = client.embed(["2024년 행정사무감사 제출자료", "2025년 예산 요구서"])
    assert error is None
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0


@live
def test_live_embeddings_separate_different_business_areas():
    """같은 업무끼리 가깝고 다른 업무와 멀어야 업무 발견(군집화)이 성립한다."""
    import numpy as np

    from app.search import normalize

    texts = [
        "2024년 행정사무감사 의회 요구자료 제출",
        "2023년 행정사무감사 의원질의 답변자료",
        "2025년도 예산 요구서 사업별 편성 내역",
    ]
    vectors, error = OllamaClient().embed(texts)
    assert error is None

    matrix = normalize(np.asarray(vectors, dtype=np.float32))
    same = float(matrix[0] @ matrix[1])
    different = float(matrix[0] @ matrix[2])
    assert same > different, f"같은 업무({same:.3f})가 다른 업무({different:.3f})보다 멀었습니다"
