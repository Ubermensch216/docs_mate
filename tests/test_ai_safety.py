import asyncio
import threading
import time

import httpx
import pytest

from app.ai.client import OllamaClient, _has_model
from app.search.verify import verify_sentences


@pytest.mark.parametrize("answer,source", [
    ("예산 요구액은 820원이다.", "예산 요구액은 1820원이다."),
    ("제출 기한은 9일이다.", "제출 기한은 5일이다."),
    ("예산 요구액은 820원이다.", "예산 요구액은 820원이 아니다."),
])
def test_contradictory_claims_are_rejected(answer, source):
    assert not verify_sentences([{"text": answer, "sources": [1]}], {1: source}).kept


def test_grouped_and_ungrouped_numbers_are_equivalent():
    assert verify_sentences([{"text": "예산 요구액은 1,820원이다.", "sources": [1]}],
                            {1: "예산 요구액은 1820원이다."}).kept


def test_exact_model_tags_and_implicit_latest():
    assert not _has_model(["example:small"], "example:large")
    assert _has_model(["bge-m3:latest"], "bge-m3")


def test_cancellation_aborts_request_and_ignores_environment_proxies(monkeypatch):
    options, requests, ended = [], [], []
    class AsyncClient:
        def __init__(self, **kwargs): options.append(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            requests.append(args)
            try:
                await asyncio.sleep(60)
            finally:
                ended.append(True)
    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:8080")
    monkeypatch.setenv("NO_PROXY", "")
    client = OllamaClient()
    timer = threading.Timer(0.15, client.cancel)
    timer.start()
    started = time.monotonic()
    try:
        data, error, _ = client.generate_json("synthetic", {})
    finally:
        timer.join()
    assert data is None and error
    assert time.monotonic() - started < 2
    assert ended == [True]
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
