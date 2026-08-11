"""설정 — 메뉴가 아니라 우상단 아이콘에서 연다.

AI 상태를 여기서 정직하게 보여준다. Ollama가 없어도 조사·검색·중복은 그대로
동작하므로, 연결 실패를 오류가 아니라 '기능 일부 사용 불가'로 알린다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...ai import OllamaClient
from ...db import Database
from .. import theme
from ..widgets import Card, UnknownBlock, clear_layout, muted_label, section_title


class SettingsDialog(QDialog):
    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("설정")
        self.setMinimumWidth(560)

        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_LG)
        self.column.setSpacing(theme.SP_LG)

        self.body = QVBoxLayout()
        self.body.setSpacing(theme.SP_LG)
        self.column.addLayout(self.body)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        recheck = QPushButton("다시 확인")
        recheck.clicked.connect(self.refresh)
        buttons.addWidget(recheck)
        close = QPushButton("닫기")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        self.column.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.body)
        self._sources()
        self._ai()
        self._storage()

    def _sources(self) -> None:
        card = Card()
        card.body.addWidget(section_title("자료원"))
        rows = self.db.sources()
        if not rows:
            card.body.addWidget(muted_label("등록된 자료원이 없습니다."))
        for row in rows:
            card.body.addWidget(muted_label(f"{row['path']}   ({row['kind']})", small=True))
        self.body.addWidget(card)

    def _ai(self) -> None:
        card = Card()
        card.body.addWidget(section_title("로컬 AI"))

        client = OllamaClient()
        health = client.health()
        card.body.addWidget(
            muted_label(f"{client.base_url}   ·   {health.message}")
        )

        if not health.ok:
            card.body.addWidget(
                UnknownBlock(
                    "AI 기능(요약·업무 분류·질문)은 쓸 수 없습니다. "
                    "파일 조사·검색·중복 확인은 그대로 동작합니다."
                )
            )
        else:
            for label, model, ready in (
                ("생성", client.gen_model, health.generation_ready),
                ("임베딩", client.embed_model, health.embedding_ready),
            ):
                mark = "●" if ready else "○"
                card.body.addWidget(
                    muted_label(f"{mark} {label}   {model}" + ("" if ready else "   (없음)"))
                )
            profile = client.profile()
            if profile:
                card.body.addWidget(muted_label(profile.describe(), small=True))

        card.body.addWidget(
            muted_label(
                "로컬 주소로만 연결합니다. 문서가 외부로 나가지 않습니다.", small=True
            )
        )
        self.body.addWidget(card)

    def _storage(self) -> None:
        card = Card()
        card.body.addWidget(section_title("저장 위치"))
        card.body.addWidget(muted_label(str(self.db.path), small=True))
        counts = self.db.counts()
        card.body.addWidget(
            muted_label(
                f"문서 {counts['documents']:,}건 · 의미 색인 {counts['embedded']:,}건 · "
                f"요약 {counts['summarized']:,}건",
                small=True,
            )
        )
        self.body.addWidget(card)
