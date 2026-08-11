"""문서 화면 — 검증과 안전망.

AI가 틀렸을 때 돌아올 수 있는 곳. 이 화면이 없으면 사용자는 AI를 검증할
방법이 없다. 그래서 AI가 하나도 동작하지 않아도 이 화면은 온전해야 한다.

중복·버전은 개별 행이 아니라 묶음 한 줄로 접는다. 목록의 체감 분량이 즉시
줄어드는 것이 '정리'의 실감이다.

우측 패널은 시점 판정 근거를 보여준다. "무엇을 근거로 2024년이라 했는지"를
사람이 확인하고 고칠 수 있어야 When과 How를 믿을 수 있다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...db import Database
from ...jobs import SummaryRunner
from .. import theme
from ..widgets import (
    EmptyState,
    UnknownBlock,
    clear_layout,
    muted_label,
    section_title,
    view_title,
)

COLUMNS = ["파일명", "업무", "시점", "형식", "크기", "상태"]

# 상태는 색만으로 구분하지 않는다. 기호와 글자를 함께 쓴다 (PRD §18.4).
# 셀 위젯 대신 아이템으로 그린다 — 위젯 500개는 저사양 PC에서 무겁고,
# 열 너비가 바뀔 때 위젯 위치가 어긋난다.
PARSE_LABEL = {
    "ok": ("● 정상", theme.CONFIRMED),
    "partial": ("◐ 부분", theme.ATTENTION),
    "empty": ("○ 빈 문서", theme.TEXT_MUTED),
    "failed": ("✕ 읽기 실패", theme.DANGER),
    "unsupported": ("— 미지원 형식", theme.TEXT_MUTED),
    "encrypted": ("🔒 암호", theme.ATTENTION),
    "too_large": ("△ 크기 초과", theme.ATTENTION),
    "pending": ("… 대기", theme.TEXT_MUTED),
    "skipped": ("· 대상 아님", theme.TEXT_DISABLED),
}
MISSING_LABEL = ("⚠ 원본 없음", theme.DANGER)

# 시점 판정 근거. 번호는 우선순위다 (doc/00 §8.1).
KIND_LABEL = {
    "body": "① 본문",
    "filename": "② 파일명",
    "meta": "③ 문서 속성",
    "folder": "④ 폴더 경로",
    "fs": "⑤ 파일 수정일",
}
PRECISION_LABEL = {"day": "일 단위", "month": "월 단위", "year": "연 단위"}

# 대표 문서만 골라내는 조건.
REPRESENTATIVE = (
    "(d.hash IS NULL OR d.id = ("
    "  SELECT MIN(x.id) FROM documents x "
    "  WHERE x.hash = d.hash AND x.missing_since IS NULL))"
)
DUP_COUNT = (
    "(SELECT COUNT(*) FROM documents y "
    " WHERE y.hash = d.hash AND d.hash IS NOT NULL AND y.missing_since IS NULL)"
)


class DocumentsView(QWidget):
    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._current_id: int | None = None
        self._summary_doc_id: int | None = None      # 요약 중인 문서
        self._summary_error: tuple[int, str] | None = None
        self._summaries = SummaryRunner(db.path, self)
        self._summaries.done.connect(self._on_summary_done)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SP_XL, theme.SP_XL, theme.SP_XL, theme.SP_XL)
        outer.setSpacing(theme.SP_MD)

        outer.addWidget(view_title("문서"))
        outer.addLayout(self._build_search())
        outer.addLayout(self._build_filters())

        self.summary = muted_label("")
        outer.addWidget(self.summary)

        body = QHBoxLayout()
        body.setSpacing(theme.SP_LG)
        body.addLayout(self._build_table(), 1)
        body.addWidget(self._build_detail())
        outer.addLayout(body, 1)

        self.empty = EmptyState(
            "아직 찾은 문서가 없습니다",
            "자료원을 등록하고 분석을 시작하면 먼저 찾은 파일부터 여기에 나타납니다.",
        )
        outer.addWidget(self.empty)

        self.refresh()

    # ── 구성 ────────────────────────────────────────────────────────
    def _build_search(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        self.search = QLineEdit()
        self.search.setPlaceholderText("파일명·본문·작성자 검색")
        self.search.returnPressed.connect(self.refresh)
        row.addWidget(self.search, 1)
        find = QPushButton("검색")
        find.clicked.connect(self.refresh)
        row.addWidget(find)
        return row

    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.SP_LG)
        self.collapse_dups = QCheckBox("중복 묶기")
        self.collapse_dups.setChecked(True)
        self.collapse_dups.setToolTip("내용이 완전히 같은 파일을 한 줄로 접습니다")
        self.collapse_dups.stateChanged.connect(self.refresh)
        row.addWidget(self.collapse_dups)

        self.documents_only = QCheckBox("문서만 보기")
        self.documents_only.setChecked(True)
        self.documents_only.setToolTip("그림·실행파일 등 분석 대상이 아닌 파일을 숨깁니다")
        self.documents_only.stateChanged.connect(self.refresh)
        row.addWidget(self.documents_only)
        row.addStretch(1)
        return row

    def _build_table(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(theme.SP_SM)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_H)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(COLUMNS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(self._open_selected)
        column.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(theme.SP_SM)
        open_file = QPushButton("원본 열기")
        open_file.clicked.connect(self._open_selected)
        actions.addWidget(open_file)
        open_folder = QPushButton("폴더 열기")
        open_folder.clicked.connect(self._reveal_selected)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        column.addLayout(actions)
        return column

    def _build_detail(self) -> QWidget:
        panel = QScrollArea()
        panel.setObjectName("Content")
        panel.setWidgetResizable(True)
        panel.setFixedWidth(theme.DETAIL_PANEL_W)
        # 긴 경로 때문에 가로 스크롤이 생기면 패널이 지저분해진다. 경로는
        # 가운데를 줄여 보여주고 전체는 툴팁으로 준다.
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setObjectName("Content")
        inner.setAutoFillBackground(True)
        self.detail = QVBoxLayout(inner)
        self.detail.setContentsMargins(theme.SP_LG, 0, theme.SP_SM, theme.SP_LG)
        self.detail.setSpacing(theme.SP_MD)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        panel.setWidget(inner)

        self.detail_panel = panel
        return panel

    # ── 목록 ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        rows = self._query(self.search.text().strip())
        has_rows = bool(rows)
        self.table.setVisible(has_rows)
        self.detail_panel.setVisible(has_rows)
        self.empty.setVisible(not has_rows)

        counts = self.db.counts()
        parts = [f"파일 {counts['total']:,}건", f"문서 {counts['documents']:,}건"]
        if counts["duplicate_extra"]:
            parts.append(f"중복 {counts['duplicate_extra']:,}건")
        if counts["parse_pending"]:
            parts.append(f"읽는 중 {counts['parse_pending']:,}건")
        if counts["parse_failed"]:
            parts.append(f"읽지 못함 {counts['parse_failed']:,}건")
        if counts["missing"]:
            parts.append(f"원본 없음 {counts['missing']:,}건")
        self.summary.setText(" · ".join(parts) + f"   (표시 {len(rows):,}행)")

        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._fill(r, row)

        # 검색으로 목록이 바뀌면 이전 선택이 목록 밖으로 나갈 수 있다. 그때
        # 패널이 보이지 않는 문서를 계속 보여주면 사용자가 혼란스럽다.
        visible = [row["id"] for row in rows]
        if self._current_id not in visible:
            self._current_id = visible[0] if visible else None
            if visible:
                self.table.blockSignals(True)
                self.table.selectRow(0)
                self.table.blockSignals(False)
        self._show_detail(self._current_id)

    def _query(self, term: str) -> list:
        where = ["1=1"]
        params: list = []

        if self.documents_only.isChecked():
            where.append("d.parse_status != 'skipped'")
        if self.collapse_dups.isChecked():
            where.append(REPRESENTATIVE)

        if term:
            # trigram FTS는 3글자 이상만 처리한다. 짧은 질의는 LIKE로 폴백한다.
            if len(term) >= 3:
                try:
                    return self.db.con.execute(
                        f"SELECT d.*, {DUP_COUNT} AS dup_n FROM document_fts f "
                        f"JOIN documents d ON d.id = f.rowid "
                        f"WHERE document_fts MATCH ? AND {' AND '.join(where)} "
                        f"ORDER BY rank LIMIT 500",
                        (_fts_query(term),),
                    ).fetchall()
                except Exception:
                    pass
            like = f"%{term}%"
            where.append("(d.filename LIKE ? OR d.path LIKE ?)")
            params += [like, like]

        return self.db.con.execute(
            f"SELECT d.*, {DUP_COUNT} AS dup_n FROM documents d "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY d.eff_date DESC NULLS LAST, d.filename LIMIT 500",
            params,
        ).fetchall()

    def _fill(self, r: int, row) -> None:
        name = row["filename"]
        dup_n = row["dup_n"] if "dup_n" in row.keys() else 0
        if dup_n and dup_n > 1 and self.collapse_dups.isChecked():
            name = f"▸ {name}   ({dup_n}개 묶음)"
        self.table.setItem(r, 0, _cell(name, row["id"], row["path"]))

        task = self.db.con.execute(
            "SELECT t.name FROM task_docs td JOIN tasks t ON t.id = td.task_id "
            "WHERE td.doc_id = ? LIMIT 1",
            (row["id"],),
        ).fetchone()
        self.table.setItem(r, 1, _cell(task["name"] if task else "—"))
        self.table.setItem(r, 2, _cell(_when(row)))
        self.table.setItem(r, 3, _cell((row["ext"] or "").lstrip(".").upper()))
        self.table.setItem(r, 4, _cell(_human(row["size"] or 0)))

        if row["missing_since"]:
            label, color = MISSING_LABEL
        else:
            label, color = PARSE_LABEL.get(
                row["parse_status"], (row["parse_status"], theme.TEXT_MUTED)
            )
        status = _cell(label)
        status.setForeground(QColor(color))
        if row["parse_error"]:
            status.setToolTip(row["parse_error"])
        self.table.setItem(r, 5, status)

    # ── 상세 (시점 근거) ────────────────────────────────────────────
    def _on_selection(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        doc_id = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self._current_id = doc_id
        self._show_detail(doc_id)

    def _show_detail(self, doc_id: int | None) -> None:
        clear_layout(self.detail)
        if doc_id is None:
            return
        row = self.db.document(doc_id)
        if row is None:
            return

        title = section_title(row["filename"])
        title.setWordWrap(True)
        title.setToolTip(row["path"])
        self.detail.addWidget(title)

        folder = str(Path(row["path"]).parent)
        location = muted_label(_elide_middle(folder), small=True)
        location.setToolTip(folder)
        self.detail.addWidget(location)

        self._add_summary(row)
        self._add_when(row)
        self._add_facts(row)
        self._add_trouble(row)

    def _add_summary(self, row) -> None:
        """AI 요약은 미리 만들어 두지 않는다.

        문서 1만 건을 미리 요약하면 27시간이 걸린다(실측). 쓰지도 않을 요약에
        그 시간을 쓰는 대신, 사용자가 문서를 열었을 때 그 하나만 만든다.
        """
        if row["parse_status"] not in ("ok", "partial"):
            return

        self.detail.addWidget(_divider())
        self.detail.addWidget(section_title("요약"))

        analysis = self.db.analysis(row["id"])
        if analysis and analysis["summary"]:
            body = muted_label(analysis["summary"])
            self.detail.addWidget(body)
            self.detail.addWidget(
                muted_label(
                    f"AI 제안 · {analysis['model'] or ''} · 원문에서 직접 확인하세요",
                    small=True,
                )
            )
            return

        if self._summary_doc_id == row["id"]:
            self.detail.addWidget(muted_label("요약하는 중… (10초쯤 걸립니다)"))
            return

        if self._summary_error and self._summary_error[0] == row["id"]:
            self.detail.addWidget(UnknownBlock(self._summary_error[1]))

        button = QPushButton("AI 요약 만들기")
        button.setEnabled(not self._summaries.running)
        button.clicked.connect(lambda _=False, i=row["id"]: self._start_summary(i))
        self.detail.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)

    def _start_summary(self, doc_id: int) -> None:
        if not self._summaries.start(doc_id):
            return
        self._summary_doc_id = doc_id
        self._summary_error = None
        self._show_detail(doc_id)

    def _on_summary_done(self, doc_id: int, summary: str, error: str) -> None:
        self._summary_doc_id = None
        self._summary_error = (doc_id, error) if error else None
        if self._current_id == doc_id:
            self._show_detail(doc_id)

    def _add_when(self, row) -> None:
        """시점과 그 근거. 이 제품에서 가장 중요한 검증 지점이다."""
        self.detail.addWidget(section_title("시점"))

        if not row["eff_date"]:
            self.detail.addWidget(
                UnknownBlock(
                    "이 문서가 언제 작성됐는지 확인할 단서를 찾지 못했습니다. "
                    "본문·파일명·문서 속성·폴더 어디에도 날짜가 없습니다."
                )
            )
            return

        kind = row["eff_date_kind"]
        self.detail.addWidget(
            muted_label(
                f"{_when(row)}   ·   {PRECISION_LABEL.get(row['eff_precision'], '')}\n"
                f"{KIND_LABEL.get(kind, kind)}에서 판정"
            )
        )
        if kind == "fs":
            self.detail.addWidget(
                UnknownBlock(
                    "파일 수정일로만 판정했습니다. 폴더째 복사하면 바뀌는 값이라 "
                    "반복 주기 계산에서는 제외됩니다."
                )
            )

        candidates = self.db.dates(row["id"])
        chosen = [c for c in candidates if c["kind"] == kind]
        if chosen and chosen[0]["raw"]:
            evidence = chosen[0]
            text = f"근거:  “{evidence['raw']}”"
            if evidence["locator"]:
                text += f"   ·   {evidence['locator']}"
            self.detail.addWidget(muted_label(text, small=True))

        others = [c for c in candidates if c["kind"] != kind]
        if others:
            self.detail.addWidget(muted_label("다른 후보", small=True))
            for candidate in sorted(others, key=lambda c: _kind_order(c["kind"])):
                label = KIND_LABEL.get(candidate["kind"], candidate["kind"])
                self.detail.addWidget(
                    muted_label(
                        f"   {label}   {candidate['value'][:7]}   “{candidate['raw'][:24]}”",
                        small=True,
                    )
                )

    def _add_facts(self, row) -> None:
        self.detail.addWidget(_divider())
        facts = [
            ("형식", (row["ext"] or "").lstrip(".").upper() or "—"),
            ("크기", _human(row["size"] or 0)),
            ("작성자", row["author"] or "—"),
            ("글자 수", f"{row['char_count']:,}" if row["char_count"] else "—"),
            ("파일 수정일", (row["fs_mtime"] or "—")[:10]),
        ]
        for label, value in facts:
            self.detail.addWidget(muted_label(f"{label}   {value}", small=True))

    def _add_trouble(self, row) -> None:
        if row["missing_since"]:
            self.detail.addWidget(
                UnknownBlock("원본을 찾을 수 없습니다. 분석 기록은 그대로 보존합니다.")
            )
        if row["parse_error"]:
            self.detail.addWidget(UnknownBlock(f"읽지 못했습니다 — {row['parse_error']}"))
        if row["parse_note"]:
            self.detail.addWidget(muted_label(f"ⓘ {row['parse_note']}", small=True))

    # ── 원본 열기 ───────────────────────────────────────────────────
    def _selected_path(self) -> Path | None:
        items = self.table.selectedItems()
        if not items:
            return None
        path = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole + 1)
        return Path(path) if path else None

    def _open_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        if not path.exists():
            self.summary.setText(f"⚠ 원본을 찾을 수 없습니다: {path}")
            return
        self.db.audit("document.open", str(path))
        _launch(path)

    def _reveal_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        target = path.parent
        if not target.exists():
            self.summary.setText(f"⚠ 폴더를 찾을 수 없습니다: {target}")
            return
        if sys.platform == "win32" and path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            _launch(target)


def _elide_middle(text: str, limit: int = 46) -> str:
    if len(text) <= limit:
        return text
    head = (limit - 3) // 2
    return f"{text[:head]}…{text[-(limit - 3 - head):]}"


def _kind_order(kind: str) -> int:
    order = ["body", "filename", "meta", "folder", "fs"]
    return order.index(kind) if kind in order else len(order)


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def _fts_query(term: str) -> str:
    """공백으로 나눈 조각을 AND로 묶는다. trigram은 구절 전체를 통으로 찾는다."""
    parts = [p for p in term.split() if len(p) >= 3]
    if not parts:
        return f'"{term}"'
    return " AND ".join(f'"{p}"' for p in parts)


def _cell(text: str, doc_id: int | None = None, path: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if doc_id is not None:
        item.setData(Qt.ItemDataRole.UserRole, doc_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, path)
    return item


def _when(row) -> str:
    """시점과 그 판정 출처를 함께 보여준다. 파일 수정일뿐이면 그렇다고 밝힌다."""
    if not row["eff_date"]:
        return "—"
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


def _launch(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 — 사용자가 명시적으로 연 원본
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:,.1f}{unit}"
        value /= 1024
    return f"{value:,.1f}GB"
