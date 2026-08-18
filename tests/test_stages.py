"""분석 상태 표현 시험 (계획서 §24, Phase B).

요구는 한 줄이다 — **상시 화면에는 결과 중심 표현, 기술 이름은 상세에만.**
그래서 확인할 것도 둘이다.

  ① 사용자가 늘 보는 자리에 일감 이름("의미 색인")이 새어 나오지 않는가
  ② 파이프라인 단계가 늘어났을 때 문구가 빠진 채로 지나가지 않는가
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.jobs import pipeline  # noqa: E402
from app.ui import stages  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.status_dialog import _stage_line as dialog_line  # noqa: E402
from app.ui.views.tasks import STAGES, _stage_line as home_line  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    yield app


class FakeReport:
    def __init__(self, done: int, total: int, note: str = ""):
        self.done, self.total, self.note = done, total, note
        self.errors: list[str] = []


PIPELINE_STAGES = [
    value for name, value in vars(pipeline).items()
    if name.startswith("STAGE_") and isinstance(value, str)
]


# ── 문구가 모든 단계에 있는가 ───────────────────────────────────────

def test_every_pipeline_stage_has_words_for_a_person():
    """단계를 새로 만들면서 문구를 안 붙이면, 사용자는 그 단계에서만 갑자기
    일감 이름을 보게 된다."""
    assert PIPELINE_STAGES
    missing = [s for s in PIPELINE_STAGES if s not in stages.RUNNING or s not in stages.DONE]
    assert missing == []


def test_the_home_screen_stages_are_pipeline_stages():
    """홈 카드가 제 이름을 따로 쓰면 문구 표가 그 단계를 못 찾는다."""
    assert all(stage in PIPELINE_STAGES for stage, _done, _total in STAGES)


# ── 상시 화면 ───────────────────────────────────────────────────────

def test_the_home_card_says_what_is_happening_not_the_job_name():
    assert home_line("내용 읽기", 120, 800, "documents") == "⣾ 문서를 읽는 중   120 / 800"
    assert home_line("의미 색인", 0, 800, "documents") == (
        "○ 비슷한 문서끼리 견줄 수 있게 만드는 중 — 아직 시작하지 않았습니다"
    )


def test_a_finished_stage_says_what_it_produced():
    """사용자가 알고 싶은 것은 단계가 지나갔다는 사실이 아니라 결과다."""
    assert home_line("파일 찾기", 12842, 0, None) == "✓ 파일을 찾았습니다 (12,842건)"
    assert home_line("업무 파악", 800, 800, "documents") == "✓ 업무를 나눴습니다 (800건)"


def test_no_technical_stage_name_leaks_into_the_always_on_screen():
    for stage in PIPELINE_STAGES:
        for line in (home_line(stage, 1, 10, "documents"),
                     home_line(stage, 10, 10, "documents")):
            assert stage not in line, f"{stage}가 상시 화면에 그대로 나왔습니다"


# ── 상태 상세 ───────────────────────────────────────────────────────

def test_the_detail_dialog_keeps_the_technical_name():
    """문제가 났을 때 무엇을 뒤져야 하는지는 결국 기술 이름으로 물어야 한다."""
    line = dialog_line("의미 색인", FakeReport(300, 800))
    assert "의미 색인 (embedding · bge-m3)" in line
    assert "비슷한 문서끼리 견줄 수 있게 만드는 중" in line
    assert "300 / 800" in line


def test_a_stage_that_has_not_started_says_so_in_plain_words():
    assert dialog_line("파일 찾기", None) == (
        "○ 파일 찾기 (scan)   아직 시작하지 않았습니다"
    )


def test_the_detail_dialog_keeps_the_stage_note(qapp):
    line = dialog_line("내용 읽기", FakeReport(10, 10, note="잠긴 파일 2건 건너뜀"))
    assert "문서를 읽었습니다 (10건)" in line
    assert "잠긴 파일 2건 건너뜀" in line
