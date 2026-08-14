"""한국어 낱말 대조 시험 (RAG 개선 R7에서 추출).

이 프로젝트에서 같은 실수를 세 번 했다 — 조사·어미가 붙은 한국어를 낱말
완전일치로 대조하면 자연어 문장이 거의 다 헛돈다. 그때마다 실제 질의가
실패했고, 세 번째에 이 모듈로 뽑았다. 여기서 규칙을 못 박아 네 번째를
막는다.
"""

from __future__ import annotations

from app.core import korean


# ── 조사·어미 ───────────────────────────────────────────────────────

def test_word_with_a_particle_matches_its_stem():
    """'계약관리는'과 '계약관리'는 사람에게 같은 말이다."""
    assert korean.contains("계약관리 처리 절차입니다", "계약관리는")


def test_verb_ending_is_tolerated():
    assert korean.contains("9월에 작성되었다", "작성되었어")


def test_exact_match_still_works():
    assert korean.contains("행정사무감사 자료", "행정사무감사")


def test_unrelated_word_does_not_match():
    assert not korean.contains("전혀 다른 주제입니다", "계약관리는")


def test_stem_is_not_shortened_below_the_floor():
    """어간을 무한정 깎으면 두 글자짜리가 아무 데나 걸린다."""
    assert not korean.contains("가나다라마바사", "계약관리는")


def test_single_character_words_never_match():
    """한 글자는 뜻을 가르지 못한다 — 대조에서 뺀다."""
    assert not korean.contains("아무 내용", "가")


# ── 문장부호 ────────────────────────────────────────────────────────

def test_trailing_punctuation_is_stripped():
    assert korean.strip_punctuation("820,") == "820"
    assert korean.strip_punctuation("210.") == "210"
    assert korean.strip_punctuation("끝!") == "끝"


def test_thousands_separator_inside_a_number_survives():
    """'1,234'의 쉼표는 숫자의 일부다 — 무조건 떼면 안 된다."""
    assert korean.strip_punctuation("1,234") == "1,234"


def test_word_with_trailing_comma_matches():
    assert korean.contains("예산요구액 820 집행액", "820,")


# ── 어간 ────────────────────────────────────────────────────────────

def test_stem_strips_the_particle():
    """낱말이 얼마나 흔한지 셀 때 쓰는 형태 — 대조가 가장 넓어지는 지점이다."""
    assert korean.stem("업무는") == "업무"


def test_stem_never_goes_below_the_floor():
    assert korean.stem("예산") == "예산"
    assert len(korean.stem("가나")) == 2


def test_stem_is_what_contains_would_accept():
    """어간으로 센 빈도가 실제 대조 결과와 어긋나면 변별력 판정이 틀어진다."""
    for word in ("업무는", "계약관리는", "작성되었다", "예산"):
        assert korean.contains(korean.stem(word), word)


# ── 낱말 쪼개기 ─────────────────────────────────────────────────────

def test_tokens_drop_single_characters():
    assert "가" not in korean.tokens("가 나다 라마바")


def test_tokens_strip_punctuation():
    assert "820" in korean.tokens("예산요구액은 820, 집행액은 210.")


def test_tokens_of_empty_text():
    assert korean.tokens("") == []
    assert korean.tokens(None) == []


# ── 중첩 비율 ───────────────────────────────────────────────────────

def test_overlap_counts_stems_not_exact_words():
    """실측으로 겪은 실패 그대로 — 조사 때문에 0%가 나왔다."""
    ratio = korean.overlap_ratio(
        "예산요구액은 820, 집행액은 210.", "예산요구액 820 집행액 210",
    )
    assert ratio == 1.0


def test_overlap_is_zero_for_unrelated_text():
    assert korean.overlap_ratio("오늘 날씨가 좋다", "예산 요구자료 접수") == 0.0


def test_overlap_of_text_without_usable_words_is_one():
    """판단할 낱말이 없으면 막지 않는다 — 근거 없다고 단정할 수 없다."""
    assert korean.overlap_ratio("가 나 다", "아무 내용") == 1.0
