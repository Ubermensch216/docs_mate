"""시점 추정 시험 — 제품의 급소.

When(언제 하는 일인가)과 How(작년엔 어떤 순서였나)가 전부 여기에 걸려 있다.
잘못 추정하면 타임라인·일정·처리순서가 통째로 어긋나므로, 형식별 인식과
우선순위 채택을 촘촘히 못 박는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core import dating
from app.core.dating import BODY, DAY, FILENAME, FOLDER, FS, META, MONTH, YEAR


# ── 본문 형식 ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected, precision",
    [
        ("작성일: 2024. 9. 12.", "2024-09-12", DAY),
        ("2024년 9월 12일 제출", "2024-09-12", DAY),
        ("2024-09-12 기준", "2024-09-12", DAY),
        ("2024/09/12 회의", "2024-09-12", DAY),
        ("20240912 정기회의 결과", "2024-09-12", DAY),
        ("2024년 9월 실적", "2024-09-01", MONTH),
        ("2024. 9.", "2024-09-01", MONTH),
        ("2024년 주요업무계획", "2024-01-01", YEAR),
        ("2025회계연도 예산", "2025-01-01", YEAR),
    ],
)
def test_body_date_formats(text, expected, precision):
    found = dating.from_text(text)
    assert found, f"인식 실패: {text!r}"
    assert found[0].value == expected
    assert found[0].precision == precision


def test_labeled_date_beats_everything_else():
    """작성일을 명시한 곳은 다른 어떤 날짜보다 강한 근거다."""
    text = (
        "2019년부터 이어온 사업입니다. 2023년 자료를 참고했습니다.\n"
        "작성일: 2024. 9. 12.\n"
        "2023년 실적은 다음과 같습니다. 2023년 예산 집행."
    )
    found = dating.from_text(text)
    assert found[0].value == "2024-09-12"
    assert found[0].labeled is True


def test_unlabeled_body_votes_on_most_frequent_year():
    """2024년 문서는 2024를 여러 번 말하고 참고 연도는 한두 번 말한다."""
    text = (
        "2024년 행정사무감사 제출자료. 2024년 9월 요구자료를 접수하여 "
        "2024년 10월 제출하였다. 참고로 2021년 자료도 검토하였다."
    )
    found = dating.from_text(text)
    assert found[0].year == 2024


def test_same_date_is_not_counted_three_times():
    """'2024. 9. 12.'가 연-월-일·연-월·연도로 세 번 잡히면 표가 오염된다."""
    found = dating._scan("2024. 9. 12.", dating._PATTERNS, BODY, "")
    assert len(found) == 1
    assert found[0].precision == DAY


def test_impossible_dates_degrade_to_year_instead_of_producing_garbage():
    """13월 40일 같은 값을 그대로 저장하면 타임라인이 깨진다.

    확실한 부분(연도)만 남기고 정밀도를 낮춘다 — 지어내지 않는 원칙의 적용이다.
    """
    for text in ("2024년 13월 40일", "2024-02-30"):
        found = dating.from_text(text)
        assert found, text
        assert found[0].precision == YEAR
        assert found[0].value == "2024-01-01"


def test_document_numbers_are_not_mistaken_for_dates():
    """'문서번호 20240912'는 날짜가 아니라 접수번호다."""
    assert not dating.from_text("문서번호 20240912 접수")
    assert not dating.from_text("행정과-제2024호")


def test_rejects_years_outside_plausible_range():
    assert not dating.from_text("1887년 기록")
    assert not dating.from_text(f"{date.today().year + 5}년 계획")


def test_empty_text_yields_nothing():
    assert dating.from_text("") == []
    assert dating.from_text("연도가 전혀 없는 문서입니다.") == []


# ── 파일명 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name, expected, precision",
    [
        ("2024_행정사무감사_요구자료.hwp", "2024-01-01", YEAR),
        ("2024_09_월간실적.xlsx", "2024-09-01", MONTH),
        ("월간실적_2025_08.xlsx", "2025-08-01", MONTH),
        ("20240912_회의록.docx", "2024-09-12", DAY),
        ("2023년 예산요구서_최종.hwp", "2023-01-01", YEAR),
    ],
)
def test_filename_dates(name, expected, precision):
    found = dating.from_filename(name)
    assert found, f"인식 실패: {name}"
    assert found[0].value == expected
    assert found[0].precision == precision
    assert found[0].kind == FILENAME


def test_filename_without_date_yields_nothing():
    assert dating.from_filename("최종진짜마지막_부장수정.hwp") == []


# ── 경로 ────────────────────────────────────────────────────────────

def test_folder_year_uses_deepest_match():
    found = dating.from_path(r"D:\업무자료\2022\행정사무감사\2024\제출.hwp")
    assert found[0].year == 2024
    assert found[0].kind == FOLDER


def test_folder_without_year_yields_nothing():
    assert dating.from_path(r"D:\업무자료\기타\참고\문서.hwp") == []


# ── 문서 속성 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["2024-09-12T10:30:00", "D:20240912153000+09'00'", "20240912"])
def test_meta_datetime_formats(raw):
    found = dating.from_meta(raw)
    assert found[0].value == "2024-09-12"
    assert found[0].kind == META


def test_meta_garbage_is_discarded_quietly():
    assert dating.from_meta("알 수 없는 형식") == []
    assert dating.from_meta(None) == []


# ── 파일시스템 ──────────────────────────────────────────────────────

def test_filesystem_date_is_accepted_as_last_resort():
    found = dating.from_filesystem("2025-03-14T09:00:00")
    assert found[0].value == "2025-03-14"
    assert found[0].kind == FS


# ── 채택 우선순위 ───────────────────────────────────────────────────

def test_body_wins_over_everything():
    resolution = dating.collect(
        text="작성일: 2024. 9. 12.",
        filename="2019_옛날자료.hwp",
        path=r"D:\자료\2018\x.hwp",
        meta_created="2020-01-01T00:00:00",
        fs_mtime="2025-03-14T09:00:00",
    )
    assert resolution.value == "2024-09-12"
    assert resolution.kind == BODY
    assert resolution.trustworthy


def test_filename_wins_when_body_has_no_date():
    resolution = dating.collect(
        text="날짜가 없는 본문입니다.",
        filename="2024_행정사무감사.hwp",
        path=r"D:\자료\2018\x.hwp",
        fs_mtime="2025-03-14T09:00:00",
    )
    assert resolution.kind == FILENAME
    assert resolution.year == 2024


def test_folder_wins_over_filesystem():
    resolution = dating.collect(
        text="", filename="최종.hwp",
        path=r"D:\업무자료\2023\최종.hwp",
        fs_mtime="2025-03-14T09:00:00",
    )
    assert resolution.kind == FOLDER
    assert resolution.year == 2023


def test_filesystem_only_is_marked_untrustworthy():
    """복사 한 번에 오염되는 근거로 일정을 만들면 안 된다."""
    resolution = dating.collect(
        text="단서가 없는 본문", filename="회의자료.hwp",
        path=r"D:\기타\회의자료.hwp", fs_mtime="2025-03-14T09:00:00",
    )
    assert resolution.kind == FS
    assert resolution.trustworthy is False


def test_no_clue_at_all_resolves_to_nothing():
    resolution = dating.collect(text="", filename="문서.hwp", path=r"D:\기타\문서.hwp")
    assert resolution.value is None
    assert resolution.trustworthy is False


def test_all_candidates_are_preserved_for_the_user_to_inspect():
    """채택하지 않은 근거도 남겨야 '왜 2024로 봤는지' 보여줄 수 있다."""
    resolution = dating.collect(
        text="작성일: 2024. 9. 12.",
        filename="2019_옛날자료.hwp",
        path=r"D:\자료\2018\x.hwp",
        fs_mtime="2025-03-14T09:00:00",
    )
    kinds = {c.kind for c in resolution.candidates}
    assert kinds == {BODY, FILENAME, FOLDER, FS}
    rows = [c.as_row() for c in resolution.candidates]
    assert all(len(row) == 5 for row in rows)


def test_sections_record_where_the_date_came_from():
    resolution = dating.collect(
        sections=[("1문단", "표지"), ("2문단", "작성일: 2024. 9. 12.")],
        filename="문서.hwp",
    )
    chosen = [c for c in resolution.candidates if c.kind == BODY][0]
    assert chosen.locator == "2문단"


def test_title_line_does_not_shortcircuit_the_body_scan():
    """표제에 연도만 있고 다음 줄에 작성일이 오는 문서가 흔하다.

    첫 조각에서 찾자마자 멈추면 연도 정밀도로 끝나 월 단위 타임라인을
    못 그린다. 앞쪽 조각을 모두 훑은 뒤 골라야 한다.
    """
    resolution = dating.collect(
        sections=[
            ("1문단", "2022_행정사무감사_의원질의답변"),
            ("2문단", "작성일: 2022. 11. 12."),
            ("3문단", "본문 내용"),
        ],
        filename="문서.docx",
    )
    assert resolution.value == "2022-11-12"
    assert resolution.precision == DAY


def test_earlier_section_wins_when_precision_ties():
    resolution = dating.collect(
        sections=[("1문단", "2024. 3. 5. 회의"), ("2문단", "2024. 9. 9. 회의")],
        filename="문서.hwp",
    )
    assert resolution.value == "2024-03-05"


def test_the_copied_folder_scenario():
    """2024년 자료를 2025년에 폴더째 복사해 수정일이 오염된 상황.

    이 시나리오를 놓치면 타임라인이 통째로 무너진다.
    """
    resolution = dating.collect(
        text="2024년 행정사무감사 제출자료입니다. 2024년 10월 제출.",
        filename="행감_제출자료_최종.hwp",
        path=r"D:\전임자PC\백업\행감\제출자료.hwp",
        fs_mtime="2025-03-14T09:00:00",   # 복사 시각
    )
    assert resolution.year == 2024, "복사 시각에 속았습니다"
    assert resolution.kind == BODY
