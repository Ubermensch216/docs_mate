"""상태 상세 — 상단 바를 누르면 뜬다.

단계별 진행 상황과 실패 목록을 보여준다. "일시중지"는 안전 지점에서
멈추고, "이어서 실행"은 문서 상태로 남은 일을 다시 찾아 이어간다 —
별도 재개 큐가 아니라 데이터 자체가 진행 상태이므로 멈췄다 다시 눌러도
같은 결과를 낸다(ING-006).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import stages, theme
from .widgets import Card, UnknownBlock, clear_layout, muted_label, section_title

STAGE_ORDER = [
    "파일 찾기", "중복 확인", "내용 읽기", "시점 확인", "의미 색인",
    "업무 파악", "일정 파악", "처리 순서 파악", "질문 준비",
]


class StatusDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None):
        super().__init__(parent)
        self._window = window
        self.setWindowTitle("분석 상태")
        self.setMinimumSize(520, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_LG, theme.SP_LG, theme.SP_LG, theme.SP_LG)
        outer.setSpacing(theme.SP_MD)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.column = QVBoxLayout(body)
        self.column.setSpacing(theme.SP_MD)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.toggle = QPushButton()
        self.toggle.setObjectName("Primary")
        self.toggle.clicked.connect(self._toggle_pipeline)
        buttons.addWidget(self.toggle)
        buttons.addStretch(1)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.column)
        reports = self._window.stage_reports

        stages = Card()
        stages.body.addWidget(section_title("단계별 진행"))
        seen = set()
        for name in STAGE_ORDER:
            report = reports.get(name)
            seen.add(name)
            stages.body.addWidget(muted_label(_stage_line(name, report)))
        for name, report in reports.items():
            if name not in seen:
                stages.body.addWidget(muted_label(_stage_line(name, report)))
        self.column.addWidget(stages)

        errors = [
            (stage, message)
            for stage, report in reports.items()
            for message in report.errors
        ]
        if errors:
            failures = Card()
            failures.body.addWidget(section_title(f"실패 목록 ({len(errors)}건)"))
            for stage, message in errors[:50]:
                failures.body.addWidget(UnknownBlock(f"[{stage}] {message}"))
            if len(errors) > 50:
                failures.body.addWidget(muted_label(f"… 외 {len(errors) - 50:,}건", small=True))
            self.column.addWidget(failures)

        running = self._window.runner.running
        self.toggle.setText("일시중지" if running else "이어서 실행")
        self.toggle.setEnabled(True)

    def _toggle_pipeline(self) -> None:
        if self._window.runner.running:
            self._window.runner.stop()
        else:
            self._window._start_pipeline()
        self.refresh()


def _stage_line(name: str, report) -> str:
    """상세 화면에서만 기술 이름을 함께 적는다 (계획서 §24).

    문제가 났을 때 무엇을 뒤져야 하는지는 결국 기술 이름으로 물어야 한다.
    다만 그 이름이 필요한 자리는 여기 하나뿐이다.
    """
    label = stages.technical(name)
    if report is None:
        return f"○ {label}   아직 시작하지 않았습니다"
    if report.total <= 0:
        return f"· {label}   {report.note or '해당 없음'}"
    if report.done >= report.total:
        text = f"✓ {label}   {stages.done(name)} ({report.total:,}건)"
    else:
        text = f"⣾ {label}   {stages.running(name)} · {report.done:,} / {report.total:,}"
    if report.note:
        text += f"   · {report.note}"
    return text
