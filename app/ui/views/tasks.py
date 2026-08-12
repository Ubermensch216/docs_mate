"""업무 화면 — What + How. 홈을 겸한다.

첫 화면이 곧 정체성의 답이다. `파일 12,842건`이 아니라
`당신이 인수받은 업무는 7개입니다`가 여기에 온다.

먼저 읽을 문서에는 반드시 이유를 붙인다. 이유 없는 별점은 신뢰를 만들지 못한다.
When(주기)은 Step 7에서, How(처리 순서)는 Step 8에서 이 화면에 이어 붙는다.
"""

from __future__ import annotations

from datetime import date
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import status, timeline
from ...db import Database
from .. import theme
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    FlowGrid,
    TaskCard,
    TimelineGrid,
    UnknownBlock,
    clear_layout,
    muted_label,
    open_original,
    section_title,
    view_title,
)
from .cycle_format import cycle_headline, cycle_note, next_occurrence_text, parse_months

STAGES = [
    ("파일 찾기", "total", None),
    ("내용 읽기", "parsed", "documents"),
    ("의미 색인", "embedded", "documents"),
    ("업무 파악하기", "in_task", "documents"),
]



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
        # 카드 그리드는 세로로만 늘어나야 한다. 가로 스크롤을 허용하면
        # 좁은 창에서 열 수가 줄지 않고 카드가 잘린 채 옆으로 밀린다.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

        # 카드 그리드로 한 화면 조망. 창을 넓히면 열이 늘어난다.
        grid = FlowGrid()
        for row in tasks:
            grid.add_card(self._task_card(row))
        self.column.addWidget(grid)

        self._render_leftovers(counts)

    def _task_card(self, row) -> QWidget:
        cycle = self.db.task_cycle(row["id"])
        card = TaskCard(
            task_id=row["id"],
            name=row["name"],
            description=row["description"],
            span_text=_span(row),
            months=_active_months(cycle),
            cycle_text=cycle_headline(cycle) if cycle else None,
            confidence=row["confidence"],
            review_state=status.of_task(row),
            reading_count=len(self.db.task_reading(row["id"])),
        )
        card.opened.connect(self.open_task)
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

        edit = QPushButton("수정 ▾")
        edit.setToolTip("업무명·설명을 고치거나 다른 업무와 합치고 나눕니다")
        edit.setMenu(self._edit_menu(task_id, edit))
        head.addWidget(edit)
        if status.of_task(row) != status.CONFIRMED:
            approve = QPushButton("이 업무 확인함")
            approve.setObjectName("Primary")
            approve.clicked.connect(lambda: self._approve(task_id))
            head.addWidget(approve)
        self.column.addLayout(head)

        self.column.addWidget(view_title(row["name"]))
        if row["description"]:
            self.column.addWidget(muted_label(row["description"]))

        # 두 축을 함께 보여준다. 왼쪽은 "사람이 확인했는가"(§9의 4단계),
        # 오른쪽은 "묶음이 얼마나 단단한가"(§7.2). 둘은 다른 질문이다.
        marks = QHBoxLayout()
        marks.setSpacing(theme.SP_SM)
        marks.addWidget(Badge.state(status.of_task(row)))
        note, kind = status.cluster_note(row["confidence"])
        marks.addWidget(Badge(note, kind))
        marks.addStretch(1)
        self.column.addLayout(marks)

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
        if cycle and cycle["kind"] == "none":
            # 사람이 '반복 아님'으로 확정한 업무. 빈칸으로 두면 시스템이
            # 못 찾은 것인지 사람이 아니라고 한 것인지 구분되지 않는다.
            self.column.addWidget(Badge(status.label(status.CONFIRMED), "ok"))
            self.column.addWidget(muted_label("반복하지 않는 업무로 확인했습니다."))
            self.column.addLayout(self._cycle_actions(task_id, cycle))
            return
        if cycle:
            headline = QHBoxLayout()
            headline.setSpacing(theme.SP_SM)
            headline.addWidget(Badge.state(status.of_cycle(cycle)))
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

        self.column.addLayout(self._cycle_actions(task_id, cycle))

    def _cycle_actions(self, task_id: int, cycle) -> QHBoxLayout:
        """추정을 사실로 바꾸는 자리. AI 결과 옆에는 항상 교정 수단이 있어야 한다."""
        row = QHBoxLayout()
        row.setSpacing(theme.SP_SM)
        if cycle is not None and status.of_cycle(cycle) != status.CONFIRMED:
            confirm = QPushButton("이 주기가 맞습니다")
            confirm.clicked.connect(lambda: self._confirm_cycle(task_id))
            row.addWidget(confirm)

        edit = QPushButton("주기 수정")
        edit.clicked.connect(lambda: self._edit_cycle(task_id))
        row.addWidget(edit)

        if cycle is None or cycle["kind"] != "none":
            none = QPushButton("반복 아님")
            none.setObjectName("Link")
            none.clicked.connect(lambda: self._no_cycle(task_id))
            row.addWidget(none)
        row.addStretch(1)
        return row

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
            add = QPushButton("＋ 단계 직접 추가")
            add.setObjectName("Link")
            add.clicked.connect(lambda: self._add_step(task_id, selected, 0))
            self.column.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)
            return

        if any(s["decided_by"] == "user" for s in steps):
            self.column.addWidget(
                muted_label(
                    f"{status.symbol(status.CONFIRMED)} 이 연도의 순서는 담당자가 "
                    f"확인했습니다. 다시 분석해도 바뀌지 않습니다.",
                    small=True,
                )
            )

        last = len(steps)
        for position, step in enumerate(steps, start=1):
            row = QHBoxLayout()
            row.setSpacing(theme.SP_SM)
            row.addWidget(muted_label(_circled(step["ordinal"]), small=True, wrap=False))
            row.addWidget(muted_label(step["day_hint"] or "", small=True, wrap=False))

            name = muted_label(step["label"], wrap=False)
            if step["is_inferred"]:
                # 사람이 채운 칸도 근거가 없으면 없다고 계속 드러낸다.
                name.setToolTip("근거 문서가 없는 단계입니다")
                name.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-style: italic;")
            row.addWidget(name)

            if step["filename"]:
                doc_btn = QPushButton(f"📄 {step['filename']}")
                doc_btn.setObjectName("Link")
                doc_btn.clicked.connect(lambda _=False, p=step["path"]: self._open(p))
                row.addWidget(doc_btn)
            row.addStretch(1)

            for text, tip, slot, enabled in (
                ("▲", "위로", lambda _=False, s=step["id"]: self._move_step(s, -1),
                 position > 1),
                ("▼", "아래로", lambda _=False, s=step["id"]: self._move_step(s, 1),
                 position < last),
                ("✎", "단계 이름 수정",
                 lambda _=False, s=step["id"], l=step["label"]: self._rename_step(s, l),
                 True),
                ("＋", "이 아래에 단계 추가",
                 lambda _=False, t=task_id, y=selected, o=step["ordinal"]:
                     self._add_step(t, y, o), True),
                ("✕", "이 단계 빼기",
                 lambda _=False, s=step["id"], l=step["label"]: self._delete_step(s, l),
                 True),
            ):
                button = QPushButton(text)
                button.setObjectName("Link")
                button.setToolTip(tip)
                button.setFixedWidth(24)
                button.setEnabled(enabled)
                button.clicked.connect(slot)
                row.addWidget(button)

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

        primary = self.db.primary_document(task_id)
        primary_id = primary["id"] if primary else None

        for row in rows[:30]:
            item = QHBoxLayout()
            item.setSpacing(theme.SP_SM)
            if row["id"] == primary_id:
                item.addWidget(muted_label("★", small=True, wrap=False))
            name = QPushButton(row["filename"])
            name.setObjectName("Link")
            name.setToolTip(
                f"{row['path']}\n클릭하면 원본을 엽니다"
                + ("\n이 업무의 대표 문서입니다" if row["id"] == primary_id else "")
            )
            name.clicked.connect(lambda _=False, p=row["path"]: self._open(p))
            item.addWidget(name)
            item.addStretch(1)
            item.addWidget(muted_label(_when(row), small=True))

            more = QPushButton("⋯")
            more.setObjectName("Link")
            more.setFixedWidth(24)
            more.setToolTip("이 문서를 다른 업무로 옮기거나 대표로 지정합니다")
            more.setMenu(
                self._document_menu(task_id, row["id"], row["id"] == primary_id, more)
            )
            item.addWidget(more)
            self.column.addLayout(item)

        if len(rows) > 30:
            self.column.addWidget(
                muted_label(f"… 외 {len(rows) - 30:,}건은 [문서]에서 볼 수 있습니다", small=True)
            )

    # ── 교정 ────────────────────────────────────────────────────────
    # AI가 지식을 만들고, 사람이 지식을 확정한다. 여기 있는 조작이 그
    # '확정' 쪽 절반이다 (계획서 §11). 모든 조작은 db의 교정 함수를 지나며,
    # 그 함수들이 재분석 보호와 기록을 함께 처리한다.
    #
    # 메뉴의 부모는 항상 그 메뉴를 여는 버튼이다. 뷰에 붙이면 두 가지가
    # 어긋난다. ① 분석 중에는 1.5초마다 다시 그리므로 뷰에 메뉴가 계속
    # 쌓인다. ② 부모 없이 만든 하위 메뉴는 파이썬 GC가 먼저 수거해 버려서
    # 눌렀을 때 빈 메뉴가 뜬다. 버튼에 붙이면 화면이 지워질 때 함께 사라진다.

    def _edit_menu(self, task_id: int, owner: QWidget) -> QMenu:
        menu = QMenu(owner)
        menu.addAction("업무명 수정", lambda: self._rename(task_id))
        menu.addAction("업무 설명 수정", lambda: self._edit_description(task_id))
        menu.addSeparator()
        merge = menu.addAction("다른 업무와 합치기", lambda: self._merge(task_id))
        merge.setEnabled(len(self.db.tasks()) > 1)
        menu.addAction("두 업무로 나누기", lambda: self._split(task_id))
        menu.addSeparator()
        menu.addAction("이건 업무가 아닙니다", lambda: self._not_a_task(task_id))
        return menu

    def _edit_description(self, task_id: int) -> None:
        row = self.db.task(task_id)
        text, ok = QInputDialog.getMultiLineText(
            self, "업무 설명 수정",
            "이 업무가 무엇인지 한두 문장으로 적어 주세요",
            row["description"] or "",
        )
        if not ok:
            return
        self.db.edit_task_description(task_id, text.strip())
        self.refresh()

    def _merge(self, task_id: int) -> None:
        others = [t for t in self.db.tasks() if t["id"] != task_id]
        if not others:
            return
        name = self.db.task(task_id)["name"]
        choice, ok = QInputDialog.getItem(
            self, "업무 합치기",
            f"'{name}'을(를) 어느 업무에 합칠까요?\n"
            f"문서가 모두 그쪽으로 옮겨지고, 이 업무는 목록에서 사라집니다.",
            [t["name"] for t in others], 0, False,
        )
        if not ok:
            return
        target = next(t for t in others if t["name"] == choice)
        if self.db.merge_tasks(task_id, target["id"]):
            self.open_task(target["id"])

    def _split(self, task_id: int) -> None:
        docs = self.db.task_documents(task_id)
        if len(docs) < 2:
            QMessageBox.information(
                self, "업무 나누기", "나누려면 문서가 2건 이상이어야 합니다."
            )
            return
        picked, name = SplitDialog.run(self, self.db.task(task_id)["name"], docs)
        if not picked:
            return
        new_id = self.db.split_task(task_id, picked, name)
        if new_id is None:
            QMessageBox.information(
                self, "업무 나누기", f"'{name}' 업무가 이미 있습니다. 다른 이름을 쓰세요."
            )
            return
        self.open_task(new_id)

    def _not_a_task(self, task_id: int) -> None:
        name = self.db.task(task_id)["name"]
        answer = QMessageBox.question(
            self, "업무 아님",
            f"'{name}'을(를) 업무 목록에서 내릴까요?\n\n"
            f"문서는 그대로 남고 삭제되지 않습니다. 다시 분석해도 이 판단은 "
            f"유지되므로 같은 묶음을 또 묻지 않습니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.mark_not_a_task(task_id)
        self.back()

    # ── 문서 교정 ───────────────────────────────────────────────────
    def _document_menu(self, task_id: int, doc_id: int, is_primary: bool,
                       owner: QWidget) -> QMenu:
        menu = QMenu(owner)
        if not is_primary:
            menu.addAction("대표 문서로 지정", lambda: self._set_primary(task_id, doc_id))
        others = [t for t in self.db.tasks() if t["id"] != task_id]
        if others:
            # addMenu(str)이 돌려주는 하위 메뉴는 주인이 없어 GC 대상이 된다.
            # 부모를 주고 만든 뒤에 붙인다.
            move = QMenu("다른 업무로 옮기기", menu)
            menu.addMenu(move)
            for other in others:
                move.addAction(
                    other["name"],
                    lambda _=False, t=other["id"]: self._move_document(task_id, t, doc_id),
                )
        menu.addSeparator()
        menu.addAction("이 업무 아님", lambda: self._detach(task_id, doc_id))
        return menu

    def _set_primary(self, task_id: int, doc_id: int) -> None:
        self.db.set_primary_document(task_id, doc_id)
        self.refresh()

    def _move_document(self, from_task: int, to_task: int, doc_id: int) -> None:
        self.db.move_document(from_task, to_task, doc_id)
        self.refresh()

    # ── When 교정 ───────────────────────────────────────────────────
    def _confirm_cycle(self, task_id: int) -> None:
        self.db.confirm_task_cycle(task_id)
        self.refresh()

    def _edit_cycle(self, task_id: int) -> None:
        cycle = self.db.task_cycle(task_id)
        current = cycle["months"] if cycle and cycle["kind"] == "yearly" else ""
        text, ok = QInputDialog.getText(
            self, "반복 주기 수정",
            "이 업무를 하는 달을 쉼표로 적어 주세요.  예: 9,10,11\n"
            "매달 하는 업무라면 '매월'이라고 적으세요.",
            text=current,
        )
        if not ok:
            return
        text = text.strip()
        if text in ("매월", "매달"):
            self.db.set_task_cycle(task_id, "monthly", "")
        else:
            months = sorted({
                int(p) for p in text.replace("월", "").split(",")
                if p.strip().isdigit() and 1 <= int(p) <= 12
            })
            if not months:
                QMessageBox.information(
                    self, "반복 주기 수정", "1~12 사이의 달을 쉼표로 구분해 적어 주세요."
                )
                return
            self.db.set_task_cycle(task_id, "yearly", ",".join(str(m) for m in months))
        self.refresh()

    def _no_cycle(self, task_id: int) -> None:
        self.db.mark_no_cycle(task_id)
        self.refresh()

    # ── How 교정 ────────────────────────────────────────────────────
    def _rename_step(self, step_id: int, current: str) -> None:
        text, ok = QInputDialog.getText(self, "단계 이름 수정", "단계 이름", text=current)
        if ok and text.strip():
            self.db.edit_step_label(step_id, text.strip())
            self.refresh()

    def _move_step(self, step_id: int, offset: int) -> None:
        self.db.move_step(step_id, offset)
        self.refresh()

    def _delete_step(self, step_id: int, label: str) -> None:
        answer = QMessageBox.question(self, "단계 삭제", f"'{label}' 단계를 뺄까요?")
        if answer == QMessageBox.StandardButton.Yes:
            self.db.delete_step(step_id)
            self.refresh()

    def _add_step(self, task_id: int, year: int, after_ordinal: int) -> None:
        text, ok = QInputDialog.getText(
            self, "단계 추가",
            "자료에는 없지만 실제로 있었던 단계를 적어 주세요.\n"
            "근거 문서가 없는 단계는 점선으로 표시됩니다.",
        )
        if ok and text.strip():
            self.db.add_step(task_id, year, text.strip(), after_ordinal)
            self.refresh()

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
        self.db.confirm_task(task_id)
        self.refresh()

    def _detach(self, task_id: int, doc_id: int) -> None:
        self.db.detach_document(task_id, doc_id)
        self.refresh()

    def _open(self, path: str) -> None:
        open_original(self, self.db, path)


class SplitDialog(QDialog):
    """업무 나누기 — 떼어 낼 문서를 고르고 새 업무 이름을 짓는다.

    AI가 두 업무를 하나로 묶는 일은 흔하다(같은 부서, 비슷한 어휘). 그때
    사용자가 할 수 있는 일이 '삭제'뿐이면 자료를 잃는다. 그래서 나누기가
    교정 UX의 핵심 조작 중 하나다.
    """

    def __init__(self, parent: QWidget, task_name: str, docs) -> None:
        super().__init__(parent)
        self.setWindowTitle("업무 나누기")
        self.setMinimumWidth(520)

        column = QVBoxLayout(self)
        column.setSpacing(theme.SP_MD)
        column.addWidget(
            muted_label(f"'{task_name}'에서 떼어 낼 문서를 고르세요. "
                        f"고르지 않은 문서는 그대로 남습니다.")
        )

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for doc in docs:
            item = QListWidgetItem(f"{doc['filename']}   ({_when(doc)})")
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self.list.addItem(item)
        column.addWidget(self.list)

        column.addWidget(muted_label("새 업무 이름", small=True))
        self.name = QLineEdit()
        self.name.setPlaceholderText("예: 행감 질의대응")
        column.addWidget(self.name)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setEnabled(False)
        self.list.itemSelectionChanged.connect(self._revalidate)
        self.name.textChanged.connect(self._revalidate)

    def _revalidate(self) -> None:
        self._ok.setEnabled(
            bool(self.list.selectedItems()) and bool(self.name.text().strip())
        )

    @classmethod
    def run(cls, parent: QWidget, task_name: str, docs) -> tuple[list[int], str]:
        dialog = cls(parent, task_name, docs)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return [], ""
        picked = [
            item.data(Qt.ItemDataRole.UserRole) for item in dialog.list.selectedItems()
        ]
        return picked, dialog.name.text().strip()


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


def _active_months(cycle) -> list[int] | None:
    """카드 월 스트립에 채울 달. 주기를 못 찾았으면 None(빈 칸)."""
    if cycle is None:
        return None
    if cycle["kind"] == "monthly":
        return list(range(1, 13))
    return parse_months(cycle["months"]) or None


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
