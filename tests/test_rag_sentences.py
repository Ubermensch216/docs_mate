"""문장 조립·JSON 잘림 복구 시험 (RAG 개선 R5).

두 가지를 확인한다.
  · _compose_answer: 검증에서 살아남은 문장만으로 답변과 근거 번호를
    다시 매기는가 — 원래 컨텍스트 번호에 구멍이 생겨도 [1][2][3]으로
    깔끔하게 이어지는가.
  · _salvage_ask_response: 실측(R1)에서 확인된 실패 — num_predict 한도에
    걸려 JSON이 문장 중간에서 잘리면 답변 전체가 사라졌다. 완성된
    문장까지는 건지는가.
"""

from __future__ import annotations

from app.search.rag import GEN_TOKEN_BUDGET, _compose_answer, _salvage_ask_response
from app.search.verify import VerifiedSentence


def row(chunk_id: int, doc_id: int, filename: str, locator: str = "1문단") -> dict:
    return {"chunk_id": chunk_id, "doc_id": doc_id, "filename": filename,
            "locator": locator, "path": f"/자료/{filename}"}


# ── _compose_answer ─────────────────────────────────────────────────

def test_citation_numbers_are_renumbered_without_gaps():
    """검증에서 2번이 버려졌다고 [1][3]으로 듬성듬성 보여주면 §1 측정 1의 변형이 된다."""
    rows = [row(1, 10, "a.hwp"), row(2, 20, "b.hwp"), row(3, 30, "c.hwp")]
    sentences = [
        VerifiedSentence(text="첫 문장", sources=[1]),
        VerifiedSentence(text="셋째 문장", sources=[3]),   # 2번은 검증에서 버려졌다고 가정
    ]
    text, citations = _compose_answer(sentences, rows)

    assert text == "첫 문장[1] 셋째 문장[2]"
    assert [c.filename for c in citations] == ["a.hwp", "c.hwp"]
    assert [c.index for c in citations] == [1, 2]


def test_repeated_source_across_sentences_gets_one_citation():
    rows = [row(1, 10, "a.hwp")]
    sentences = [
        VerifiedSentence(text="첫 문장", sources=[1]),
        VerifiedSentence(text="둘째 문장도 같은 근거", sources=[1]),
    ]
    text, citations = _compose_answer(sentences, rows)

    assert text == "첫 문장[1] 둘째 문장도 같은 근거[1]"
    assert len(citations) == 1


def test_sentence_citing_multiple_sources_gets_multiple_marks():
    rows = [row(1, 10, "a.hwp"), row(2, 20, "b.hwp")]
    sentences = [VerifiedSentence(text="두 근거를 합쳤다", sources=[1, 2])]
    text, citations = _compose_answer(sentences, rows)

    assert text == "두 근거를 합쳤다[1][2]"
    assert len(citations) == 2


def test_no_surviving_sentences_yields_empty_answer():
    text, citations = _compose_answer([], [row(1, 10, "a.hwp")])
    assert text == ""
    assert citations == []


# ── _salvage_ask_response ───────────────────────────────────────────

def test_salvage_recovers_complete_sentences_from_truncated_json():
    """실측(R1): num_predict 한도에 걸려 문자열 중간에서 JSON이 잘렸다."""
    truncated = (
        '{"answered": true, "sentences": ['
        '{"text": "9월에 접수되었다", "sources": [1]}, '
        '{"text": "이어서 취합했다", "sources": [1, 2]}, '
        '{"text": "그리고 마지막으로 제출'   # 여기서 잘림 — 닫는 따옴표조차 없다
    )
    result = _salvage_ask_response(truncated)

    assert result is not None
    assert result["answered"] is True
    texts = [s["text"] for s in result["sentences"]]
    assert texts == ["9월에 접수되었다", "이어서 취합했다"]
    assert "그리고 마지막으로 제출" not in " ".join(texts)


def test_salvage_preserves_source_numbers():
    truncated = '{"answered": true, "sentences": [{"text": "합쳤다", "sources": [1, 2, 3]}'
    result = _salvage_ask_response(truncated)
    assert result["sentences"][0]["sources"] == [1, 2, 3]


def test_salvage_returns_none_without_even_the_answered_field():
    """복구할 뼈대조차 없으면 포기한다 — 억지로 만들어 내지 않는다."""
    assert _salvage_ask_response('{"ans') is None


def test_salvage_returns_none_when_no_sentence_completed_before_the_cut():
    truncated = '{"answered": true, "sentences": [{"text": "완성되지 못'
    assert _salvage_ask_response(truncated) is None


def test_salvage_handles_answered_false():
    truncated = '{"answered": false, "sentences": ['
    result = _salvage_ask_response(truncated)
    assert result is None   # 문장이 하나도 없으면 어차피 건질 게 없다


def test_salvage_unescapes_the_recovered_text():
    truncated = r'{"answered": true, "sentences": [{"text": "\"인용\" 표시", "sources": [1]}'
    result = _salvage_ask_response(truncated)
    assert result["sentences"][0]["text"] == '"인용" 표시'


# ── 토큰 한도 ────────────────────────────────────────────────────────

def test_token_budget_was_raised_past_the_measured_failure_point():
    """실측: num_predict=500이 문장 배열 스키마에서 잘림을 냈다. 늘렸는지 못 박는다."""
    assert GEN_TOKEN_BUDGET > 500
