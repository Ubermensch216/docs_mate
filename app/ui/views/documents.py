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
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import status
from ...db import Database, representative_predicate
from ...jobs import SummaryRunner
from ...search import fts
from .. import theme
from ..widgets import (
    Badge,
    EmptyState,
    InfoDot,
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
# 색은 값이 아니라 이름으로 담는다 — 테마를 바꾸면 theme의 색이 갈리는데,
# 값을 import 시점에 복사해 두면 이 표만 옛 색으로 남는다.
PARSE_LABEL = {
    "ok": ("● 정상", "CONFIRMED"),
    "partial": ("◐ 부분", "ATTENTION"),
    "empty": ("○ 빈 문서", "TEXT_MUTED"),
    "failed": ("✕ 읽기 실패", "DANGER"),
    "unsupported": ("— 미지원 형식", "TEXT_MUTED"),
    "encrypted": ("🔒 암호", "ATTENTION"),
    "too_large": ("△ 크기 초과", "ATTENTION"),
    "locked": ("🔓 열려 있음", "ATTENTION"),
    "pending": ("… 대기", "TEXT_MUTED"),
    "skipped": ("· 대상 아님", "TEXT_DISABLED"),
}
MISSING_LABEL = ("⚠ 원본 없음", "DANGER")

# 시점 판정 근거. 번호는 우선순위다 (doc/00 §8.1).
KIND_LABEL = {
    "body": "① 본문",
    "filename": "② 파일명",
    "meta": "③ 문서 속성",
    "folder": "④ 폴더 경로",
    "fs": "⑤ 파일 수정일",
}
PRECISION_LABEL = {"day": "일 단위", "month": "월 단위", "year": "연 단위"}

# 대표 문서만 골라내는 조건. When·How·RAG와 같은 기준을 쓴다(db/repo.py).
REPRESENTATIVE = representative_predicate("d")
DUP_COUNT = (
    "(SELECT COUNT(*) FROM documents y "
    " WHERE y.hash = d.hash AND d.hash IS NOT NULL AND y.missing_since IS NULL)"
)
# 미분류 = 어느 업무에도 붙지 않은 문서. repo.unclassified_count()와 같은 조건을
# 써야 한다 — 상단 집계와 목록이 다른 수를 말하면 둘 다 못 믿는다.
UNCLASSIFIED = "NOT EXISTS(SELECT 1 FROM task_docs td WHERE td.doc_id = d.id)"


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

        # 다른 화면과 같은 자리에 같은 ⓘ를 둔다. 설명이 어디 있는지 매번
        # 찾게 하지 않는 것이 도움말의 절반이다.
        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP_SM)
        title_row.addWidget(view_title("문서"))
        title_row.addWidget(
            InfoDot(
                "찾은 파일을 그대로 보여 줍니다. 줄을 고르면 오른쪽에 그 파일의 "
                "시점을 무엇으로 판정했는지와 근거가 나옵니다. 원본은 열어서 "
                "읽기만 하고 고치지 않습니다."
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        title_row.addStretch(1)
        outer.addLayout(title_row)
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

        # 미분류는 실패가 아니라 정상이다(어느 업무에도 안 붙는 문서는 늘 있다).
        # 다만 "내 업무 문서인데 안 붙은 것"을 찾아 배정하려면 그것만 모아
        # 볼 수 있어야 한다 — 500행 목록에서 눈으로 고를 수는 없다.
        self.unclassified_only = QCheckBox("미분류만 보기")
        self.unclassified_only.setToolTip("어느 업무에도 배정되지 않은 문서만 봅니다")
        self.unclassified_only.stateChanged.connect(self.refresh)
        row.addWidget(self.unclassified_only)
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

        # 건수를 거르개에 붙여 둔다. 누르기 전에 규모를 알아야 누를지 말지
        # 정할 수 있다(일정 화면 탭에서 쓴 것과 같은 규칙).
        self.unclassified_only.setText(f"미분류만 보기 ({self.db.unclassified_count():,})")

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
        if self.unclassified_only.isChecked():
            where.append(UNCLASSIFIED)

        if term:
            # trigram FTS는 3글자 이상만 처리한다. 짧은 질의는 LIKE로 폴백한다.
            # 이 판단·이스케이프 규칙은 질문 화면(RAG)과 공유한다(search/fts.py)
            # — 갈라지면 같은 검색어에 화면마다 다른 결과가 나온다.
            if fts.usable_for_trigram(term):
                try:
                    return self.db.con.execute(
                        f"SELECT d.*, {DUP_COUNT} AS dup_n FROM document_fts f "
                        f"JOIN documents d ON d.id = f.rowid "
                        f"WHERE document_fts MATCH ? AND {' AND '.join(where)} "
                        f"ORDER BY rank LIMIT 500",
                        (fts.escape_match(term),),
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
            label, token = MISSING_LABEL
        else:
            label, token = PARSE_LABEL.get(
                row["parse_status"], (row["parse_status"], "TEXT_MUTED")
            )
        status = _cell(label)
        status.setForeground(QColor(theme.color(token)))
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
        self._add_tasks(row)
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
        head = QHBoxLayout()
        head.setSpacing(theme.SP_SM)
        head.addWidget(section_title("시점"))
        head.addWidget(Badge.state(status.of_document_date(row)))
        head.addStretch(1)
        self.detail.addLayout(head)

        if not row["eff_date"]:
            if row["date_decided_by"] == "user":
                # 사람이 '모름'으로 확정한 것과 시스템이 못 찾은 것은 다르다.
                self.detail.addWidget(
                    muted_label(
                        "담당자가 ‘날짜 모름’으로 확정했습니다. 다시 분석해도 "
                        "이 판단은 바뀌지 않습니다."
                    )
                )
            else:
                self.detail.addWidget(
                    UnknownBlock(
                        "이 문서가 언제 작성됐는지 확인할 단서를 찾지 못했습니다. "
                        "본문·파일명·문서 속성·폴더 어디에도 날짜가 없습니다."
                    )
                )
            self._add_date_actions(row)
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

        if row["date_decided_by"] == "user":
            self.detail.addWidget(
                muted_label(
                    f"{status.symbol(status.CONFIRMED)} 담당자가 확정한 시점입니다. "
                    f"다시 분석해도 바뀌지 않습니다.",
                    small=True,
                )
            )
        self._add_date_actions(row)

    def _add_date_actions(self, row) -> None:
        """추정 옆에는 늘 교정 수단이 있어야 한다 (계획서 §11).

        후보를 보여 주기만 하고 고를 수 없으면, 사용자는 틀린 것을 발견하고도
        할 수 있는 일이 없다 — 그 순간 화면 전체가 '읽을거리'가 된다.
        """
        button = QPushButton("시점 고치기" if row["eff_date"] else "시점 직접 지정")
        button.setObjectName("Quiet")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, i=row["id"]: self._edit_date(i))
        self.detail.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)

    def _edit_date(self, doc_id: int) -> None:
        row = self.db.document(doc_id)
        if row is None:
            return
        picked = _DateDialog.run(self, row, self.db.dates(doc_id))
        if picked is None:
            return
        value, precision, kind = picked
        self.db.set_document_date(doc_id, value, precision=precision, kind=kind)
        self.refresh()

    # ── 상세 (업무 배정) ────────────────────────────────────────────
    def _add_tasks(self, row) -> None:
        """이 문서가 어느 업무에 속하는가, 그리고 그것을 여기서 고칠 수 있는가.

        미분류 문서를 배정하는 자리를 업무 화면에 두면 순서가 거꾸로다 —
        사용자는 '이 파일이 어느 업무지?'를 문서를 보면서 판단한다.
        """
        if row["parse_status"] == "skipped":
            return

        self.detail.addWidget(_divider())
        self.detail.addWidget(section_title("업무"))

        links = self.db.document_tasks(row["id"])
        if not links:
            self.detail.addWidget(
                UnknownBlock(
                    "어느 업무에도 배정되지 않았습니다. 업무와 무관한 문서라면 "
                    "그대로 두어도 됩니다."
                )
            )
        for link in links:
            line = QHBoxLayout()
            line.setSpacing(theme.SP_SM)
            name = muted_label(link["name"])
            name.setToolTip(link["name"])
            line.addWidget(name, 1)
            if link["is_primary"]:
                line.addWidget(Badge("대표", "ok"))
            drop = QPushButton("배정 해제")
            drop.setObjectName("Quiet")
            drop.setCursor(Qt.CursorShape.PointingHandCursor)
            drop.setToolTip("이 업무에서만 뗍니다. 문서와 분석 결과는 그대로입니다.")
            drop.clicked.connect(
                lambda _=False, t=link["id"], d=row["id"]: self._detach_task(t, d)
            )
            line.addWidget(drop)
            self.detail.addLayout(line)

        assign = QPushButton("＋ 업무에 배정")
        assign.setObjectName("Quiet")
        assign.setCursor(Qt.CursorShape.PointingHandCursor)
        assign.setToolTip("한 문서가 여러 업무에 속할 수 있습니다")
        assign.clicked.connect(lambda _=False, i=row["id"]: self._assign_task(i))
        self.detail.addWidget(assign, alignment=Qt.AlignmentFlag.AlignLeft)

    def _assign_task(self, doc_id: int) -> None:
        taken = {link["id"] for link in self.db.document_tasks(doc_id)}
        choices = [task for task in self.db.tasks() if task["id"] not in taken]
        if not choices:
            self.summary.setText(
                "⚠ 배정할 업무가 없습니다. 업무 화면에서 업무를 먼저 만들거나, "
                "이미 이 문서가 모든 업무에 배정돼 있습니다."
            )
            return
        task_id = _TaskPicker.run(self, choices)
        if task_id is None:
            return
        self.db.assign_document(task_id, doc_id)
        self.refresh()

    def _detach_task(self, task_id: int, doc_id: int) -> None:
        self.db.detach_document(task_id, doc_id)
        self.refresh()

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


class _DateDialog(QDialog):
    """시점 교정 — 후보 중에서 고르거나, 직접 적거나, ‘모름’으로 확정한다.

    후보를 라디오로 내놓는 이유: 날짜를 손으로 다시 치게 하면 오타가 들어오고,
    무엇보다 **근거와의 연결이 끊긴다.** 후보를 고르면 그 후보의 출처(본문·
    파일명·문서 속성…)를 그대로 유지하므로, 확정한 뒤에도 "무엇을 보고
    정했는지"가 화면에 남는다.

    ‘날짜 모름’도 하나의 판단이다. 빈칸으로 두는 것과 달리 다음 재분석이
    다시 추정해 덮어쓰지 않는다(repo.set_document_date 참고).
    """

    def __init__(self, parent: QWidget, row, candidates) -> None:
        super().__init__(parent)
        self.setWindowTitle("시점 고치기")
        self.setMinimumWidth(460)

        column = QVBoxLayout(self)
        column.setSpacing(theme.SP_MD)
        column.addWidget(muted_label(row["filename"]))

        self._group = QButtonGroup(self)
        self._choices: dict[QRadioButton, tuple[str | None, str, str]] = {}

        ordered = sorted(candidates, key=lambda c: _kind_order(c["kind"]))
        for candidate in ordered:
            label = KIND_LABEL.get(candidate["kind"], candidate["kind"])
            precision = PRECISION_LABEL.get(candidate["precision"], "")
            text = f"{label}   {_value_text(candidate['value'], candidate['precision'])}"
            if precision:
                text += f" ({precision})"
            if candidate["raw"]:
                text += f"   “{candidate['raw'][:24]}”"
            if candidate["locator"]:
                text += f"   · {candidate['locator']}"
            button = QRadioButton(text)
            if candidate["kind"] == row["eff_date_kind"]:
                button.setChecked(True)
            self._group.addButton(button)
            self._choices[button] = (
                candidate["value"], candidate["precision"], candidate["kind"]
            )
            column.addWidget(button)

        if not ordered:
            column.addWidget(
                muted_label("자료에서 찾은 날짜 후보가 없습니다. 직접 적어 주세요.",
                            small=True)
            )

        self.manual = QRadioButton("직접 입력")
        self._group.addButton(self.manual)
        column.addWidget(self.manual)

        self.text = QLineEdit()
        self.text.setPlaceholderText("2024-03-11  ·  2024-03  ·  2024")
        self.text.textEdited.connect(lambda _t: self.manual.setChecked(True))
        column.addWidget(self.text)

        self.unknown = QRadioButton("날짜 모름으로 확정")
        self.unknown.setToolTip("다시 분석해도 날짜를 추정하지 않습니다")
        self._group.addButton(self.unknown)
        column.addWidget(self.unknown)

        if not ordered and not row["eff_date"]:
            self.manual.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._group.buttonToggled.connect(lambda *_: self._revalidate())
        self.text.textChanged.connect(self._revalidate)
        self._revalidate()

    def choice(self) -> tuple[str | None, str, str] | None:
        """(value, precision, kind). 고른 것이 없거나 입력이 틀리면 None."""
        if self.unknown.isChecked():
            return (None, "day", "user")
        if self.manual.isChecked():
            parsed = _parse_date_text(self.text.text())
            if parsed is None:
                return None
            return (parsed[0], parsed[1], "user")
        for button, value in self._choices.items():
            if button.isChecked():
                return value
        return None

    def _revalidate(self) -> None:
        self._ok.setEnabled(self.choice() is not None)

    @classmethod
    def run(cls, parent: QWidget, row, candidates) -> tuple[str | None, str, str] | None:
        dialog = cls(parent, row, candidates)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.choice()


class _TaskPicker(QDialog):
    """이 문서를 어느 업무에 붙일지 고른다.

    업무가 수십 개가 되면 목록만으로는 못 찾으므로 이름 거르기를 함께 둔다.
    """

    def __init__(self, parent: QWidget, tasks) -> None:
        super().__init__(parent)
        self.setWindowTitle("업무에 배정")
        self.setMinimumWidth(460)

        column = QVBoxLayout(self)
        column.setSpacing(theme.SP_MD)
        column.addWidget(
            muted_label("이 문서를 어느 업무의 자료로 볼지 고르세요. "
                        "한 문서가 여러 업무에 속해도 됩니다.")
        )

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("업무 이름으로 거르기")
        self.filter.textChanged.connect(self._apply_filter)
        column.addWidget(self.filter)

        self.list = QListWidget()
        for task in tasks:
            item = QListWidgetItem(f"{task['name']}   ({task['doc_count']:,}건)")
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, task["name"])
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        column.addWidget(self.list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setEnabled(False)
        self.list.itemSelectionChanged.connect(
            lambda: self._ok.setEnabled(bool(self.list.selectedItems()))
        )

    def _apply_filter(self, term: str) -> None:
        needle = term.strip()
        for index in range(self.list.count()):
            item = self.list.item(index)
            name = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            item.setHidden(bool(needle) and needle not in name)

    @classmethod
    def run(cls, parent: QWidget, tasks) -> int | None:
        dialog = cls(parent, tasks)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        items = dialog.list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None


def _parse_date_text(text: str) -> tuple[str, str] | None:
    """사람이 적은 날짜를 (YYYY-MM-DD, precision)으로 바꾼다.

    적는 방식을 하나로 강요하지 않는다 — 2024.3.11 / 2024-03-11 / 2024년 3월을
    모두 받는다. 못 알아들을 때는 지어내지 않고 None을 돌려 확인 버튼을 잠근다.

    빠진 자리는 01로 채우고 무엇이 확실한지는 precision이 말한다. 자료에서
    뽑은 후보와 같은 형식이어야 한다(core/dating.py).
    """
    parts = [p for p in re.split(r"[^0-9]+", text.strip()) if p]
    if not parts or len(parts) > 3:
        return None
    try:
        numbers = [int(p) for p in parts]
    except ValueError:                       # pragma: no cover — 숫자만 남겨 두었다
        return None

    year = numbers[0]
    if not 1900 <= year <= 2200:
        return None
    if len(numbers) == 1:
        return f"{year:04d}-01-01", "year"

    month = numbers[1]
    if not 1 <= month <= 12:
        return None
    if len(numbers) == 2:
        return f"{year:04d}-{month:02d}-01", "month"

    day = numbers[2]
    try:
        return date(year, month, day).isoformat(), "day"
    except ValueError:
        return None


def _value_text(value: str, precision: str) -> str:
    if precision == "year":
        return f"{value[:4]}년"
    if precision == "month":
        return f"{value[:4]}.{value[5:7]}"
    return value


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
