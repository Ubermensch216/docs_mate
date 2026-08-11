"""설정 — 메뉴가 아니라 우상단 아이콘에서 연다.

AI 상태를 여기서 정직하게 보여준다. Ollama가 없어도 조사·검색·중복은 그대로
동작하므로, 연결 실패를 오류가 아니라 '기능 일부 사용 불가'로 알린다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
        self._accessibility()
        self._sources()
        self._ai()
        self._storage()
        self._audit_log()

    def _accessibility(self) -> None:
        card = Card()
        card.body.addWidget(section_title("화면"))
        checkbox = QCheckBox("글자 크게 보기")
        checkbox.setChecked(self.db.get_meta("large_text") == "1")
        checkbox.toggled.connect(self._toggle_large_text)
        card.body.addWidget(checkbox)
        self.body.addWidget(card)

    def _toggle_large_text(self, checked: bool) -> None:
        from .. import theme

        self.db.set_meta("large_text", "1" if checked else "0")
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.stylesheet(large_text=checked))

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

    def _audit_log(self) -> None:
        """감사 로그는 앱에서 임의로 편집할 수 없고, 최근 내역을 확인하고
        승인된 형식(CSV)으로 내보낼 수 있어야 한다 (PRD §20.2)."""
        card = Card()
        head = QHBoxLayout()
        head.addWidget(section_title("최근 활동"))
        head.addStretch(1)
        export = QPushButton("내보내기")
        export.clicked.connect(self._export_audit_log)
        head.addWidget(export)
        card.body.addLayout(head)

        rows = self.db.recent_audit(20)
        if not rows:
            card.body.addWidget(muted_label("기록이 없습니다."))
        for row in rows:
            text = f"{row['at']}   {row['action']}"
            if row["target"]:
                text += f"   {row['target']}"
            if row["result"]:
                text += f"   [{row['result']}]"
            card.body.addWidget(muted_label(text, small=True))
        self.body.addWidget(card)

    def _export_audit_log(self) -> None:
        """경로는 다이얼로그로 묻고, 실제 쓰기는 _write_audit_csv에 맡긴다.

        둘을 나눈 이유: 네이티브 파일 다이얼로그는 자동화 시험에서 몽키패치가
        기대대로 가로채지지 않을 수 있어(Qt 정적 메서드), 시험이 실제
        모달 창을 띄우며 멈추는 사고가 났었다. 쓰기 로직만 따로 두면 다이얼로그
        없이 바로 검증할 수 있다.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path, _filter = QFileDialog.getSaveFileName(
            self, "감사 로그 내보내기", "audit_log.csv", "CSV (*.csv)"
        )
        if not path:
            return
        count = self._write_audit_csv(path)
        QMessageBox.information(self, "내보내기 완료", f"{count:,}건을 저장했습니다.\n{path}")

    def _write_audit_csv(self, path: str) -> int:
        import csv

        rows = self.db.con.execute(
            "SELECT at, action, target, detail, result FROM audit_logs ORDER BY id"
        ).fetchall()
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["시각", "동작", "대상", "상세", "결과"])
            for row in rows:
                writer.writerow([row["at"], row["action"], row["target"], row["detail"], row["result"]])
        self.db.audit("audit_log.export", path)
        return len(rows)

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
