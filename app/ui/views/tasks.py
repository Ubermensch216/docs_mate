"""업무 화면 — What + How. 홈을 겸한다.

첫 화면이 곧 정체성의 답이다. `파일 12,842건`이 아니라
`당신이 인수받은 업무는 7개입니다`가 여기에 온다.

먼저 읽을 문서에는 반드시 이유를 붙인다. 이유 없는 별점은 신뢰를 만들지 못한다.
When(주기)은 Step 7에서, How(처리 순서)는 Step 8에서 이 화면에 이어 붙는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import timeline
from ...db import Database
from .. import theme
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    TimelineGrid,
    UnknownBlock,
    clear_layout,
    muted_label,
    section_title,
    view_title,
)
from .cycle_format import cycle_headline, cycle_note, next_occurrence_text

STAGES = [
    ("파일 찾기", "total", None),
    ("내용 읽기", "parsed", "documents"),
    ("의미 색인", "embedded", "documents"),
    ("업무 파악하기", "in_task", "documents"),
]

CONFIDENCE_NOTE = {
    "high": ("● 묶음이 단단합니다", "ok"),
    "medium": ("◐ 확인이 필요합니다", "neutral"),
    "low": ("○ 느슨하게 묶였습니다", "attention"),
}


class TasksView(QWidget):
    go_documents = Signal()
    go_calendar = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._task_id: int | None = None   # None이면 목록, 값이 있으면 상세
        self._how_year: dict[int, int] = {}   # task_id -> 사용자가 고른 연도

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("Content")
        body = QWidget()
        body.setObjectName("Content")
        body.setAutoFillBackground(True)
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        self.column.setSpacing(theme.SP_LG)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.refresh()

    # ── 진입점 ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        clear_layout(self.column)
        if self._task_id is not None and self.db.task(self._task_id):
            self._render_detail(self._task_id)
        else:
            self._task_id = None
            self._render_list()

    def open_task(self, task_id: int) -> None:
        self._task_id = task_id
        self.refresh()

    def back(self) -> None:
        self._task_id = None
        self.refresh()

    # ── 목록 ────────────────────────────────────────────────────────
    def _render_list(self) -> None:
        counts = self.db.counts()
        tasks = self.db.tasks()

        if not tasks:
            self._render_progress(counts)
            return

        self.column.addWidget(
            view_title(f"당신이 인수받은 업무는 {len(tasks)}개로 추정됩니다")
        )
        self.column.addWidget(
            muted_label(
                f"전임자 자료 {counts['documents']:,}건을 살펴본 결과입니다. "
                "AI가 제안한 것이므로 확인하고 고칠 수 있습니다."
            )
        )

        for row in tasks:
            self.column.addWidget(self._task_card(row))

        self._render_leftovers(counts)

    def _task_card(self, row) -> QWidget:
        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(section_title(row["name"]))
        head.addStretch(1)
        head.addWidget(muted_label(_span(row), small=True, wrap=False))
        card.body.addLayout(head)

        if row["description"]:
            card.body.addWidget(muted_label(row["description"]))

        cycle = self.db.task_cycle(row["id"])
        if cycle:
            card.body.addWidget(
                muted_label(f"🔁 {cycle_headline(cycle)} 반복", small=True)
            )

        foot = QHBoxLayout()
        foot.setSpacing(theme.SP_MD)
        note, kind = CONFIDENCE_NOTE.get(row["confidence"], CONFIDENCE_NOTE["low"])
        foot.addWidget(Badge(note, kind))

        reading = len(self.db.task_reading(row["id"]))
        if reading:
            foot.addWidget(
                muted_label(f"📄 먼저 읽을 문서 {reading}건", small=True, wrap=False)
            )
        if row["status"] == "proposed":
            foot.addWidget(Badge("확인 필요", "attention"))
        foot.addStretch(1)

        enter = QPushButton("열기  →")
        enter.setObjectName("Link")
        enter.clicked.connect(lambda _=False, i=row["id"]: self.open_task(i))
        foot.addWidget(enter)
        card.body.addLayout(foot)
        return card

    def _render_leftovers(self, counts: dict) -> None:
        unclassified = self.db.unclassified_count()
        parts = []
        if unclassified:
            parts.append(f"미분류 {unclassified:,}건")
        if counts["duplicate_extra"]:
            parts.append(f"중복 {counts['duplicate_extra']:,}건")
        if counts["parse_failed"]:
            parts.append(f"읽지 못함 {counts['parse_failed']:,}건")
        if not parts:
            return

        self.column.addWidget(_divider())
        self.column.addWidget(
            muted_label(
                "⚠ " + " · ".join(parts)
                + "\n미분류가 남는 것은 정상입니다. 확신이 없는 문서를 억지로 "
                "업무에 밀어 넣지 않습니다."
            )
        )
        link = QPushButton("문서에서 보기")
        link.setObjectName("Link")
        link.clicked.connect(self.go_documents.emit)
        self.column.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)

    def _render_progress(self, counts: dict) -> None:
        if counts["total"] == 0:
            self.column.addWidget(view_title("업무"))
            self.column.addWidget(
                EmptyState(
                    "아직 살펴본 자료가 없습니다",
                    "자료원을 등록하면 파일을 먼저 찾고, 내용을 읽은 뒤 업무를 파악합니다.",
                )
            )
            return

        self.column.addWidget(view_title("자료를 살펴보고 있습니다"))
        card = Card()
        card.setMaximumWidth(theme.CONTENT_MAX_W)
        for label, done_key, total_key in STAGES:
            done = counts.get(done_key, 0)
            limit = counts.get(total_key, 0) if total_key else done
            card.body.addWidget(muted_label(_stage_line(label, done, limit, total_key)))
        self.column.addWidget(card)

        self.column.addWidget(
            EmptyState(
                "업무는 아직 파악하지 못했습니다",
                "내용을 읽고 의미 색인을 만들어야 업무를 나눌 수 있습니다. "
                "그동안 먼저 찾은 파일부터 [문서]에서 볼 수 있습니다.",
                "문서 보기",
                self.go_documents.emit,
            )
        )

    # ── 상세 ────────────────────────────────────────────────────────
    def _render_detail(self, task_id: int) -> None:
        row = self.db.task(task_id)

        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        back = QPushButton("←  업무 목록")
        back.setObjectName("Link")
        back.clicked.connect(self.back)
        head.addWidget(back)
        head.addStretch(1)

        rename = QPushButton("업무명 수정")
        rename.clicked.connect(lambda: self._rename(task_id))
        head.addWidget(rename)
        if row["status"] != "approved":
            approve = QPushButton("이 업무 확인함")
            approve.setObjectName("Primary")
            approve.clicked.connect(lambda: self._approve(task_id))
            head.addWidget(approve)
        self.column.addLayout(head)

        self.column.addWidget(view_title(row["name"]))
        if row["description"]:
            self.column.addWidget(muted_label(row["description"]))

        note, kind = CONFIDENCE_NOTE.get(row["confidence"], CONFIDENCE_NOTE["low"])
        self.column.addWidget(Badge(note, kind))

        self._render_reading(task_id)
        self._render_when(task_id)
        self._render_how(task_id)
        self._render_by_year(task_id)

    def _render_reading(self, task_id: int) -> None:
        picks = self.db.task_reading(task_id)
        self.column.addWidget(_divider())
        self.column.addWidget(section_title("먼저 읽을 문서"))

        if not picks:
            self.column.addWidget(muted_label("추천할 문서를 고르지 못했습니다."))
            return

        for index, pick in enumerate(picks, start=1):
            card = Card()
            card.setMaximumWidth(theme.CONTENT_MAX_W)

            top = QHBoxLayout()
            top.setSpacing(theme.SP_SM)
            top.addWidget(muted_label(f"{_circled(index)}", small=True))
            name = QPushButton(pick["filename"])
            name.setObjectName("Link")
            name.setToolTip(f"{pick['path']}\n클릭하면 원본을 엽니다")
            name.clicked.connect(lambda _=False, p=pick["path"]: self._open(p))
            top.addWidget(name)
            top.addStretch(1)
            top.addWidget(muted_label(_when(pick), small=True))
            card.body.addLayout(top)

            # 이유 없는 추천은 만들지 않는다.
            card.body.addWidget(muted_label(pick["reason"], small=True))
            self.column.addWidget(card)

    def _render_when(self, task_id: int) -> None:
        """When — 격자를 세로로 읽어 반복 주기를 보여준다.

        같은 격자를 Step 8에서 가로로 읽어 처리 순서(How)를 만든다.
        """
        rows = self.db.task_grid_documents(task_id)
        docs = [
            timeline.DatedDoc(
                doc_id=r["id"], year=r["year"], month=r["month"],
                day=int(r["eff_date"][8:10]) if r["eff_precision"] == "day" else None,
                trustworthy=r["eff_date_kind"] != "fs",
            )
            for r in rows
        ]
        grid = timeline.build_grid(docs)

        self.column.addWidget(_divider())
        self.column.addWidget(section_title("When"))

        if not grid.years:
            self.column.addWidget(
                UnknownBlock("이 업무 문서의 시점을 확인할 수 없어 반복 여부를 판단하지 못했습니다.")
            )
            return
        if len(grid.years) < 2:
            self.column.addWidget(
                UnknownBlock(
                    f"반복 여부를 판단할 자료가 부족합니다. 현재 확인된 연도가 "
                    f"{len(grid.years)}개뿐입니다. 최소 2개 연도가 있어야 반복을 제시합니다."
                )
            )

        cycle = self.db.task_cycle(task_id)
        if cycle:
            headline = QHBoxLayout()
            headline.setSpacing(theme.SP_SM)
            headline.addWidget(muted_label(f"🔁 {cycle_headline(cycle)} 반복"))
            headline.addWidget(muted_label(f"· {cycle_note(cycle)}", small=True))
            headline.addStretch(1)
            self.column.addLayout(headline)

        years = grid.years[-6:]   # 화면 폭을 넘지 않도록 최근 6개 연도만
        cells = {(y, m): bool(grid.doc_ids(y, m)) for y in years for m in range(1, 13)}
        today = date.today()
        current = (today.year, today.month) if today.year in years else None
        confidence = cycle["confidence"] if cycle else "low"
        self.column.addWidget(TimelineGrid(years, cells, confidence, current))

        if cycle:
            next_text = next_occurrence_text(cycle, today)
            if next_text:
                foot = QHBoxLayout()
                foot.setSpacing(theme.SP_SM)
                foot.addWidget(muted_label(f"다음 예상 시점: {next_text}", small=True))
                foot.addStretch(1)
                go = QPushButton("연간 일정에서 보기 →")
                go.setObjectName("Link")
                go.clicked.connect(self.go_calendar.emit)
                foot.addWidget(go)
                self.column.addLayout(foot)

    def _render_how(self, task_id: int) -> None:
        """How — 같은 격자를 가로로 읽어 처리 순서를 재현한다.

        인과관계 추론이 아니라 시간 근접 + 문서 유형 순서에 기반한
        재구성이므로 "~로 보입니다" 톤을 유지한다. 모든 단계는 문서에
        앵커링되고, 근거 없는 공백은 지어내지 않고 그렇다고 밝힌다.
        """
        years = self.db.task_years(task_id)
        self.column.addWidget(_divider())
        self.column.addWidget(section_title("How"))

        if not years:
            self.column.addWidget(
                UnknownBlock("처리 순서를 재구성할 자료가 없습니다.")
            )
            return

        default_year = timeline.default_how_year(years)
        selected = self._how_year.get(task_id, default_year)
        if selected not in years:
            selected = default_year

        header = QHBoxLayout()
        header.setSpacing(theme.SP_SM)
        header.addWidget(muted_label(f"{selected}년엔 이렇게 처리한 것으로 보입니다"))
        header.addStretch(1)

        ordered_years = sorted(years, reverse=True)
        combo = QComboBox()
        for y in ordered_years:
            combo.addItem(f"{y}년으로 보기", y)
        combo.setCurrentIndex(ordered_years.index(selected))
        combo.currentIndexChanged.connect(
            lambda _index, t=task_id, c=combo: self._change_how_year(t, c.currentData())
        )
        header.addWidget(combo)
        self.column.addLayout(header)

        steps = self.db.task_steps(task_id, selected)
        if not steps:
            self.column.addWidget(
                UnknownBlock(f"{selected}년에는 처리 순서를 재구성할 자료가 없습니다.")
            )
            return

        for step in steps:
            row = QHBoxLayout()
            row.setSpacing(theme.SP_SM)
            row.addWidget(muted_label(_circled(step["ordinal"]), small=True, wrap=False))
            row.addWidget(muted_label(step["day_hint"], small=True, wrap=False))
            row.addWidget(muted_label(step["label"], wrap=False))
            if step["filename"]:
                doc_btn = QPushButton(f"📄 {step['filename']}")
                doc_btn.setObjectName("Link")
                doc_btn.clicked.connect(lambda _=False, p=step["path"]: self._open(p))
                row.addWidget(doc_btn)
            row.addStretch(1)
            self.column.addLayout(row)

            # 근거 없는 공백은 지어내지 않고 그렇다고 밝힌다.
            if step["gap_note"]:
                self.column.addWidget(UnknownBlock(step["gap_note"]))

    def _change_how_year(self, task_id: int, year: int) -> None:
        self._how_year[task_id] = year
        self.refresh()

    def _render_by_year(self, task_id: int) -> None:
        rows = self.db.task_documents(task_id)
        self.column.addWidget(_divider())
        self.column.addWidget(section_title(f"이 업무의 문서 {len(rows):,}건"))

        if not rows:
            self.column.addWidget(muted_label("배정된 문서가 없습니다."))
            return

        by_year: dict[str, int] = {}
        for row in rows:
            key = str(row["eff_year"]) if row["eff_year"] else "연도 미상"
            by_year[key] = by_year.get(key, 0) + 1

        line = "   ".join(
            f"{year} ({count})"
            for year, count in sorted(by_year.items(), key=lambda kv: kv[0], reverse=True)
        )
        self.column.addWidget(muted_label(line))

        for row in rows[:30]:
            item = QHBoxLayout()
            item.setSpacing(theme.SP_SM)
            name = QPushButton(row["filename"])
            name.setObjectName("Link")
            name.clicked.connect(lambda _=False, p=row["path"]: self._open(p))
            item.addWidget(name)
            item.addStretch(1)
            item.addWidget(muted_label(_when(row), small=True))
            drop = QPushButton("이 업무 아님")
            drop.setObjectName("Link")
            drop.clicked.connect(
                lambda _=False, t=task_id, d=row["id"]: self._detach(t, d)
            )
            item.addWidget(drop)
            self.column.addLayout(item)

        if len(rows) > 30:
            self.column.addWidget(
                muted_label(f"… 외 {len(rows) - 30:,}건은 [문서]에서 볼 수 있습니다", small=True)
            )

    # ── 교정 ────────────────────────────────────────────────────────
    def _rename(self, task_id: int) -> None:
        row = self.db.task(task_id)
        name, ok = QInputDialog.getText(self, "업무명 수정", "업무 이름", text=row["name"])
        name = name.strip()
        if not ok or not name or name == row["name"]:
            return
        if not self.db.rename_task(task_id, name):
            QMessageBox.information(
                self, "업무명 수정", f"'{name}' 업무가 이미 있습니다. 다른 이름을 쓰세요."
            )
            return
        self.refresh()

    def _approve(self, task_id: int) -> None:
        self.db.approve_task(task_id)
        self.refresh()

    def _detach(self, task_id: int, doc_id: int) -> None:
        self.db.detach_document(task_id, doc_id)
        self.refresh()

    def _open(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            QMessageBox.information(self, "원본 열기", f"원본을 찾을 수 없습니다:\n{path}")
            return
        self.db.audit("document.open", path)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — 사용자가 명시적으로 연 원본
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])


def _stage_line(label: str, done: int, limit: int, total_key: str | None) -> str:
    if total_key is None:
        return f"✓ {label}   {done:,}건 완료"
    if limit == 0:
        return f"○ {label}   대기 중"
    if done >= limit:
        return f"✓ {label}   {done:,}건 완료"
    if done == 0:
        return f"○ {label}   대기 중"
    return f"⣾ {label}   {done:,} / {limit:,}"


def _span(row) -> str:
    count = f"{row['doc_count']:,}건"
    first, last = row["first_year"], row["last_year"]
    if first and last:
        return f"{count} · {first}~{last}" if first != last else f"{count} · {first}"
    return count


def _when(row) -> str:
    if not row["eff_date"]:
        return "시점 미상"
    precision = row["eff_precision"] or "day"
    year, month, *_ = (row["eff_date"] or "").split("-") + ["", ""]
    if precision == "year":
        text = f"{year}년"
    elif precision == "month":
        text = f"{year}.{int(month):02d}"
    else:
        text = row["eff_date"]
    if row["eff_date_kind"] == "fs":
        text += " (파일 날짜)"
    return text


def _circled(index: int) -> str:
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    return circled[index - 1] if 1 <= index <= len(circled) else f"{index}."


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
