"""설정 — 메뉴가 아니라 우상단 아이콘에서 연다.

갈래는 셋이다. 한 기둥에 다섯 덩어리를 쌓아 두면 매일 건드리는 것(글자
크기)과 일 년에 한 번 보는 것(감사 로그)이 같은 무게로 보인다.

  화면       글자 크기·테마. 지금 눈이 불편한가.
  자료 폴더  어디를 읽는가. 바꿀 수 있어야 한다.
  시스템     이 프로그램이 지금 어떤 상태인가 — 로컬 AI, 저장 위치, 최근 활동.

'자료 폴더'와 '저장 위치'는 방향이 반대다. 앞은 **읽는 곳**(원본, 건드리지
않음), 뒤는 **쓰는 곳**(분석 결과 DB). 예전에는 '자료원'과 '저장 위치'라는
비슷한 무게의 이름으로 나란히 놓여 있어 구분이 안 됐다. 이제 서로 다른
갈래에 두고, 각자 상대를 가리키는 한 줄을 달아 둔다.

AI 상태를 여기서 정직하게 보여준다. Ollama가 없어도 조사·검색·중복은 그대로
동작하므로, 연결 실패를 오류가 아니라 '기능 일부 사용 불가'로 알린다.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...ai import OllamaClient
from ...ai.settings import model_names, project_client, save_models
from ...jobs.ai_status import check_status
from ...db import Database
from .. import icons, theme
from ..widgets import (
    Badge,
    Card,
    IconChoice,
    UnknownBlock,
    clear_layout,
    muted_label,
    section_title,
)

# 갈래: (키, 아이콘, 이름, 이 갈래가 답하는 것)
SECTIONS = [
    ("appearance", "text-medium", "화면", "글자 크기와 밝기"),
    ("sources", "folder", "자료 폴더", "어디를 읽는가"),
    ("system", "system", "시스템", "AI·저장 위치·최근 활동"),
]

SOURCE_KIND_LABEL = {"local": "내 PC", "unc": "네트워크", "removable": "이동식"}
AUDIT_ROWS = 20


class SettingsDialog(QDialog):
    """설정 창.

    화면 설정은 누른 즉시 적용된다 — '확인'을 눌러야 반영되는 방식이면
    글자 크기를 고를 때마다 창을 닫았다 열어야 한다. 되돌리는 것도 다시
    누르면 그만이라 취소 버튼이 필요 없다.
    """

    sources_changed = Signal()      # 자료 폴더가 바뀌었다 — 다시 분석해야 한다
    models_changed = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._ai_checked = False
        self._ai_status_task = None
        self.setWindowTitle("설정")
        self.setMinimumSize(760, 620)

        self.text_size = theme.normalize_text_size(self._stored_text_size())
        self.theme_mode = theme.normalize_mode(db.get_meta("theme_mode"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        self.nav = _SettingsNav()
        self.nav.navigated.connect(self.go)
        split.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.panes: dict[str, _Pane] = {}
        for key, _icon, name, lead in SECTIONS:
            pane = _Pane(name, lead)
            self.panes[key] = pane
            self.stack.addWidget(pane)
        split.addWidget(self.stack, 1)
        outer.addLayout(split, 1)

        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(theme.SP_LG, theme.SP_MD, theme.SP_LG, theme.SP_MD)
        buttons.addStretch(1)
        close = QPushButton("닫기")
        close.setObjectName("Primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addWidget(footer)

        self.refresh()
        self.go("appearance")

    # ── 갈래 ────────────────────────────────────────────────────────
    def go(self, key: str) -> None:
        pane = self.panes.get(key)
        if pane is None:
            return
        self.stack.setCurrentWidget(pane)
        self.nav.select(key)
        if key == "system" and not self._ai_checked:
            self._recheck_ai()

    def refresh(self) -> None:
        """세 갈래를 모두 다시 그린다.

        보이지 않는 갈래까지 짓는 이유: 갈래를 옮길 때마다 조사하면 로컬 AI
        확인처럼 시간이 걸리는 일이 클릭마다 끼어든다. 한 번에 짓고, 바뀐
        일이 있을 때만 다시 부른다.
        """
        self._build_appearance(self.panes["appearance"].reset())
        self._build_sources(self.panes["sources"].reset())
        self._build_system(self.panes["system"].reset())

    # ── 화면 ────────────────────────────────────────────────────────
    def _build_appearance(self, body: QVBoxLayout) -> None:
        size_card = Card(tone="static")
        size_card.body.addWidget(section_title("글자 크기"))
        size_card.body.addWidget(
            muted_label(
                "화면 전체 글자가 함께 커지고 작아집니다. 고르면 바로 적용됩니다.",
                small=True,
            )
        )
        self.size_choice = IconChoice(
            [
                (
                    key,
                    theme.TEXT_SIZE_ICONS[key],
                    theme.TEXT_SIZE_LABELS[key],
                    f"본문 글자를 {theme.TEXT_SIZE_LABELS[key]} 봅니다"
                    f" ({theme.body_px(key)}px)",
                )
                for key in theme.TEXT_SIZE_ORDER
            ],
            current=self.text_size,
            icon_size=30,
        )
        self.size_choice.chosen.connect(self._choose_text_size)
        size_card.body.addWidget(self.size_choice)
        body.addWidget(size_card)

        theme_card = Card(tone="static")
        theme_card.body.addWidget(section_title("테마"))
        theme_card.body.addWidget(
            muted_label("화면 밝기입니다. 어두운 곳에서는 어둡게가 눈이 덜 부십니다.",
                        small=True)
        )
        self.theme_choice = IconChoice(
            [
                (key, theme.THEME_ICONS[key], theme.THEME_LABELS[key], theme.THEME_HINTS[key])
                for key in theme.THEME_MODES
            ],
            current=self.theme_mode,
            icon_size=30,
        )
        self.theme_choice.chosen.connect(self._choose_theme)
        theme_card.body.addWidget(self.theme_choice)

        # '시스템'을 골랐을 때 지금 실제로 무엇이 칠해져 있는지 말해 준다 —
        # 고른 것과 보이는 것이 다를 수 있는 유일한 항목이다.
        self.theme_state = muted_label(self._theme_state_text(), small=True)
        theme_card.body.addWidget(self.theme_state)
        body.addWidget(theme_card)

        note = Card(tone="static")
        note.body.addWidget(section_title("미리 보기"))
        note.body.addWidget(
            muted_label("이 창 자체가 미리 보기입니다. 아래 조각으로 확인하세요.",
                        small=True)
        )
        sample = QHBoxLayout()
        sample.setSpacing(theme.SP_SM)
        sample.addWidget(Badge("✓ 확인됨", "ok"))
        sample.addWidget(Badge("△ 확인 필요", "attention"))
        sample.addWidget(Badge("추정", "neutral"))
        sample.addStretch(1)
        note.body.addLayout(sample)
        note.body.addWidget(
            muted_label("본문은 이 크기로 보입니다. 작은 글씨는 아래와 같습니다.")
        )
        note.body.addWidget(muted_label("근거·부연은 이 크기입니다.", small=True))
        body.addWidget(note)

    def _stored_text_size(self) -> str:
        """옛 설정(large_text=1/0)을 3단계로 옮겨 읽는다.

        쓰던 사람이 켜 둔 '글자 크게 보기'가 갱신 후 조용히 꺼져 있으면,
        그 사람에게는 기능이 사라진 것이 아니라 앱이 망가진 것으로 보인다.
        """
        stored = self.db.get_meta("text_size")
        if stored in theme.TEXT_SIZES:
            return stored
        return "large" if self.db.get_meta("large_text") == "1" else "medium"

    def _choose_text_size(self, key: str) -> None:
        self.text_size = theme.normalize_text_size(key)
        self.db.set_meta("text_size", self.text_size)
        # 옛 키도 함께 맞춰 둔다 — 갱신 전 버전으로 되돌아가도 어긋나지 않는다.
        self.db.set_meta("large_text", "1" if self.text_size == "large" else "0")
        self._apply_appearance()

    def _choose_theme(self, key: str) -> None:
        self.theme_mode = theme.normalize_mode(key)
        self.db.set_meta("theme_mode", self.theme_mode)
        self._apply_appearance()
        self.theme_state.setText(self._theme_state_text())

    def _theme_state_text(self) -> str:
        resolved = theme.resolve_mode(self.theme_mode)
        shown = "밝게" if resolved == "light" else "어둡게"
        if self.theme_mode == "system":
            return f"윈도우 설정을 따릅니다 — 지금은 {shown} 표시 중입니다."
        return f"항상 {shown} 표시합니다."

    def _apply_appearance(self) -> None:
        """색과 글자 크기를 앱 전체에 즉시 반영한다.

        스타일시트만 갈면 반쪽이다 — 아이콘은 그릴 때 색을 넣으므로 다시
        그려야 하고, 본문 화면들은 카드 안에서 직접 칠한 색이 있어 다시
        조립해야 한다. 창(shell)이 그 일을 맡고, 여기서는 신호만 보낸다.
        """
        app = QApplication.instance()
        if app is None:                      # 오프스크린 시험 등
            theme.apply_mode(self.theme_mode)
        else:
            theme.apply(app, self.text_size, self.theme_mode)

        for choice in (self.size_choice, self.theme_choice):
            choice.repaint_icons()
        self.nav.repaint_icons()

        window = self.parent()
        if hasattr(window, "restyle"):
            window.restyle()

    # ── 자료 폴더 ───────────────────────────────────────────────────
    def _build_sources(self, body: QVBoxLayout) -> None:
        card = Card(tone="static")
        head = QHBoxLayout()
        head.addWidget(section_title("업무 자료가 있는 폴더"))
        head.addStretch(1)
        add = QPushButton("+ 폴더 추가")
        add.clicked.connect(self._add_source)
        head.addWidget(add)
        card.body.addLayout(head)
        card.body.addWidget(
            muted_label(
                "여기 있는 문서를 읽어 분석합니다. 원본은 수정·이동·삭제하지 않습니다.",
                small=True,
            )
        )

        rows = self.db.sources()
        counts = self.db.source_document_counts()
        if not rows:
            card.body.addWidget(
                UnknownBlock("등록된 폴더가 없습니다. 폴더를 추가하면 분석을 시작합니다.")
            )
        for row in rows:
            card.body.addWidget(
                _SourceRow(
                    source_id=row["id"],
                    path=row["path"],
                    kind=row["kind"],
                    documents=counts.get(row["id"], 0),
                    on_change=self._change_source,
                    on_remove=self._remove_source,
                )
            )
        body.addWidget(card)

        # 두 경로를 헷갈리게 두지 않는다. 여기서 상대를 명시적으로 가리킨다.
        compare = Card(tone="static")
        compare.body.addWidget(section_title("‘자료 폴더’와 ‘저장 위치’의 차이"))
        compare.body.addWidget(
            muted_label(
                "자료 폴더는 <b>읽는 곳</b>입니다. 전임자가 남긴 원본이 있는 자리이고, "
                "이 프로그램은 여기서 읽기만 합니다.<br>"
                "저장 위치는 <b>쓰는 곳</b>입니다. 읽어서 분석한 결과(요약·업무 분류·"
                "색인)를 담아 두는 이 프로그램의 파일 자리입니다."
            )
        )
        jump = QPushButton("저장 위치 보기 →")
        jump.setObjectName("Link")
        jump.setCursor(Qt.CursorShape.PointingHandCursor)
        jump.clicked.connect(lambda: self.go("system"))
        compare.body.addWidget(jump, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(compare)

    def _add_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "업무 자료 폴더 선택")
        if not chosen:
            return
        path = Path(chosen)
        if any(Path(row["path"]) == path for row in self.db.sources()):
            QMessageBox.information(self, "폴더 추가", "이미 등록된 폴더입니다.")
            return
        self.db.add_source(path, kind=_source_kind(path))
        self._after_sources_changed()

    def _change_source(self, source_id: int, old_path: str) -> None:
        """폴더 위치를 바꾼다 — 최초 지정이 곧 확정이던 것을 푼다.

        자료를 다른 드라이브로 옮기거나 USB에서 네트워크 폴더로 갈아타는 일은
        인수인계 현장에서 흔하다. 그때마다 프로젝트를 새로 만들면 손으로
        고쳐 둔 교정값이 전부 날아간다.
        """
        chosen = QFileDialog.getExistingDirectory(self, "바꿀 폴더 선택", old_path)
        if not chosen:
            return
        path = Path(chosen)
        if str(path) == str(Path(old_path)):
            return
        if not self.db.change_source_path(source_id, path, kind=_source_kind(path)):
            QMessageBox.information(
                self, "폴더 변경", "그 폴더는 이미 다른 자료 폴더로 등록돼 있습니다."
            )
            return
        QMessageBox.information(
            self,
            "폴더 변경",
            f"새 폴더를 다음 분석에서 훑습니다.\n{path}\n\n"
            "지금까지의 분석 결과와 직접 고친 내용은 그대로 남습니다. "
            "이전 폴더에만 있던 파일은 문서 화면에서 ‘원본 없음’으로 표시됩니다.",
        )
        self._after_sources_changed()

    def _remove_source(self, source_id: int, path: str, documents: int) -> None:
        """제거는 지우는 일이다. 무엇이 함께 사라지는지 먼저 말한다."""
        detail = (
            f"이 폴더에서 읽어 둔 문서 기록 {documents:,}건도 함께 지워집니다.\n"
            if documents
            else ""
        )
        answer = QMessageBox.question(
            self,
            "자료 폴더 제거",
            f"{path}\n\n{detail}원본 파일은 지우지 않습니다. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.remove_source(source_id)
        self._after_sources_changed()

    def _after_sources_changed(self) -> None:
        self.refresh()
        self.go("sources")
        self.sources_changed.emit()

    # ── 시스템 ──────────────────────────────────────────────────────
    def _build_system(self, body: QVBoxLayout) -> None:
        body.addWidget(self._ai_card())
        body.addWidget(self._storage_card())
        body.addWidget(self._audit_card())

    def _ai_card(self) -> Card:
        card = Card(tone="static")
        head = QHBoxLayout()
        head.addWidget(section_title("로컬 AI"))
        head.addStretch(1)
        self.ai_recheck = QPushButton("연결 확인")
        self.ai_recheck.clicked.connect(self._recheck_ai)
        head.addWidget(self.ai_recheck)
        card.body.addLayout(head)
        self.ai_state = muted_label("시스템 화면을 열면 연결 상태를 확인합니다.")
        self.ai_state.setAccessibleName("로컬 AI 연결 상태")
        card.body.addWidget(self.ai_state)
        self.gen_model = QComboBox()
        self.embed_model = QComboBox()
        for label, combo, value in zip(
            ("답변·요약 모델", "문서 검색 모델"),
            (self.gen_model, self.embed_model), model_names(self.db),
        ):
            combo.setEditable(True)
            combo.addItem(value)
            combo.setMinimumHeight(theme.CONTROL_H)
            combo.setAccessibleName(label)
            row = QVBoxLayout()
            row.addWidget(muted_label(label, small=True))
            row.addWidget(combo)
            card.body.addLayout(row)
        card.body.addWidget(muted_label(
            "이 인수인계에서 사용할 설치된 모델을 선택하세요. "
            "문서 검색 모델을 바꾸면 자료를 다시 준비하며, 그동안 질문을 사용할 수 없을 수 있습니다.", small=True))
        self.ai_save = QPushButton("모델 설정 저장")
        self.ai_save.setObjectName("Primary")
        self.ai_save.clicked.connect(self._save_models)
        card.body.addWidget(self.ai_save)
        self.ai_feedback = muted_label("")
        card.body.addWidget(self.ai_feedback)
        card.body.addWidget(
            muted_label(
                "로컬 주소로만 연결합니다. 문서가 외부로 나가지 않습니다.", small=True
            )
        )
        return card

    def _recheck_ai(self) -> None:
        if self._ai_status_task is not None:
            return
        self._ai_checked = True
        self._status_models = model_names(self.db)
        self.ai_state.setText("연결을 확인하는 중입니다… 화면 설정은 계속 사용할 수 있습니다.")
        self.ai_recheck.setEnabled(False)
        self._ai_status_task = check_status(project_client(self.db, OllamaClient), self._on_ai_status)

    def _on_ai_status(self, health) -> None:
        self._ai_status_task = None
        self.ai_recheck.setEnabled(True)
        if self._status_models != model_names(self.db):
            self._recheck_ai()
            return
        states = ("답변 준비됨" if health.generation_ready else "답변 모델 없음",
                  "검색 준비됨" if health.embedding_ready else "검색 모델 없음")
        self.ai_state.setText(" · ".join(states) if health.models else health.message)
        for combo in (self.gen_model, self.embed_model):
            chosen = combo.currentText()
            combo.clear()
            combo.addItems(list(dict.fromkeys([chosen, *health.models])))
            combo.setCurrentText(chosen)

    def _save_models(self) -> None:
        if (self.gen_model.currentText().strip(), self.embed_model.currentText().strip()) == model_names(self.db):
            self.ai_feedback.setText("현재 모델 설정이 이미 저장되어 있습니다.")
            return
        parent = self.parentWidget()
        runners = [getattr(parent, "runner", None)]
        for view in getattr(parent, "views", {}).values():
            runners.extend([getattr(view, "_runner", None), getattr(view, "_summaries", None)])
        if any(runner is not None and runner.running for runner in runners):
            self.ai_feedback.setText("분석이나 질문이 끝난 뒤 모델 설정을 저장해 주세요.")
            return
        try:
            rebuild = save_models(self.db, self.gen_model.currentText(), self.embed_model.currentText())
        except ValueError as exc:
            self.ai_feedback.setText(str(exc))
            return
        self.ai_feedback.setText("저장했습니다. 새 검색 모델로 자료를 다시 준비합니다." if rebuild else "모델 설정을 저장했습니다.")
        self.models_changed.emit()
        self._recheck_ai()

    def _storage_card(self) -> Card:
        card = Card(tone="static")
        head = QHBoxLayout()
        head.addWidget(section_title("저장 위치"))
        head.addStretch(1)
        open_folder = QPushButton("폴더 열기")
        open_folder.clicked.connect(self._open_storage_folder)
        head.addWidget(open_folder)
        card.body.addLayout(head)

        card.body.addWidget(
            muted_label(
                "분석 결과를 담아 두는 이 프로그램의 파일입니다. "
                "원본은 자료 폴더에 그대로 있습니다.",
                small=True,
            )
        )
        path_label = muted_label(str(self.db.path))
        path_label.setObjectName("Mono")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(path_label)

        warning = storage_warning(self.db.path)
        if warning:
            card.body.addWidget(UnknownBlock(warning))

        counts = self.db.counts()
        card.body.addWidget(
            muted_label(
                f"문서 {counts['documents']:,}건 · 의미 색인 {counts['embedded']:,}건 · "
                f"요약 {counts['summarized']:,}건",
                small=True,
            )
        )

        # 이 파일에는 문서 본문 조각과 요약이 들어 있다. 암호화는 아직 하지
        # 않으므로(오프라인 설치와 충돌한다) 그 사실을 감추지 않고 말한다.
        # 저장 위치를 어디로 둘지 판단할 수 있어야 사용자가 스스로 지킨다.
        card.body.addWidget(
            UnknownBlock(
                "이 파일도 민감정보입니다. 원본 자료의 본문 일부와 요약이 담겨 "
                "있으므로, 원본과 같은 수준으로 관리하세요. 지금은 파일 자체를 "
                "암호화하지 않으며, 접근은 이 PC 사용자 계정 권한으로 제한됩니다. "
                "더 이상 필요 없으면 시작 화면에서 ‘완전 삭제’로 지울 수 있습니다."
            )
        )
        return card

    def _open_storage_folder(self) -> None:
        from ..widgets import open_original

        open_original(self, self.db, str(Path(self.db.path).parent))

    def _audit_card(self) -> Card:
        """감사 로그는 앱에서 임의로 편집할 수 없고, 최근 내역을 확인하고
        승인된 형식(CSV)으로 내보낼 수 있어야 한다 (PRD §20.2)."""
        card = Card(tone="static")
        head = QHBoxLayout()
        head.addWidget(section_title("최근 활동"))
        head.addStretch(1)
        export = QPushButton("내보내기")
        export.clicked.connect(self._export_audit_log)
        head.addWidget(export)
        card.body.addLayout(head)
        card.body.addWidget(
            muted_label(
                f"이 프로그램이 한 일의 기록입니다. 최근 {AUDIT_ROWS}건만 보이며, "
                "전체는 CSV로 내보낼 수 있습니다.",
                small=True,
            )
        )

        rows = self.db.recent_audit(AUDIT_ROWS)
        if not rows:
            card.body.addWidget(muted_label("기록이 없습니다."))
        for row in rows:
            text = f"{row['at']}   {row['action']}"
            if row["target"]:
                text += f"   {row['target']}"
            if row["result"]:
                text += f"   [{row['result']}]"
            card.body.addWidget(muted_label(text, small=True))
        return card

    def _export_audit_log(self) -> None:
        """경로는 다이얼로그로 묻고, 실제 쓰기는 _write_audit_csv에 맡긴다.

        둘을 나눈 이유: 네이티브 파일 다이얼로그는 자동화 시험에서 몽키패치가
        기대대로 가로채지지 않을 수 있어(Qt 정적 메서드), 시험이 실제
        모달 창을 띄우며 멈추는 사고가 났었다. 쓰기 로직만 따로 두면 다이얼로그
        없이 바로 검증할 수 있다.
        """
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


class _SettingsNav(QWidget):
    """설정 창 왼쪽 갈래 목록. 본문 사이드바와 같은 언어로 그린다."""

    navigated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsNav")
        self.setFixedWidth(theme.SETTINGS_NAV_W)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_SM, theme.SP_MD, theme.SP_SM, theme.SP_MD)
        column.setSpacing(theme.SP_XS)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._items: dict[str, QPushButton] = {}

        for key, icon_name, name, lead in SECTIONS:
            button = QPushButton(f"  {name}")
            button.setObjectName("SettingsNavItem")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{name} — {lead}")
            button._icon_name = icon_name
            button.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            button.toggled.connect(lambda _checked: self.repaint_icons())
            self._group.addButton(button)
            column.addWidget(button)
            self._items[key] = button

        column.addStretch(1)
        self.repaint_icons()

    def select(self, key: str) -> None:
        button = self._items.get(key)
        if button:
            button.setChecked(True)

    def repaint_icons(self) -> None:
        from PySide6.QtGui import QIcon

        for button in self._items.values():
            tint = theme.PRIMARY if button.isChecked() else theme.TEXT_MUTED
            art = icons.pixmap(button._icon_name, tint, 18)
            if art is not None:
                button.setIcon(QIcon(art))


class _Pane(QWidget):
    """갈래 하나. 제목 + 한 줄 설명 + 스크롤되는 카드 기둥."""

    def __init__(self, title: str, lead: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setObjectName("ViewHeader")
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head_box = QVBoxLayout(head)
        head_box.setContentsMargins(theme.SP_XL, theme.SP_LG, theme.SP_XL, theme.SP_MD)
        head_box.setSpacing(theme.SP_XS)
        heading = muted_label(title)
        heading.setObjectName("SettingsHeading")
        head_box.addWidget(heading)
        subtitle = muted_label(lead, small=True)
        subtitle.setObjectName("SettingsLead")
        head_box.addWidget(subtitle)
        outer.addWidget(head)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        holder = QWidget()
        holder.setObjectName("SettingsBody")
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(theme.SP_XL, theme.SP_LG, theme.SP_XL, theme.SP_XL)
        self.body.setSpacing(theme.SP_LG)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

    def reset(self) -> QVBoxLayout:
        clear_layout(self.body)
        return self.body


class _SourceRow(QFrame):
    """자료 폴더 한 줄. 경로 + 종류 + 규모 + 조작(변경·제거).

    경로를 그냥 회색 한 줄로 흘리면 "표시"지 "다룰 수 있는 것"으로 보이지
    않는다. 조작 버튼을 같은 줄에 붙여 바꿀 수 있음을 먼저 말한다.
    """

    def __init__(
        self,
        source_id: int,
        path: str,
        kind: str,
        documents: int,
        on_change,
        on_remove,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("SubPanel")

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_MD, theme.SP_MD, theme.SP_MD, theme.SP_MD)
        column.setSpacing(theme.SP_SM)

        top = QHBoxLayout()
        top.setSpacing(theme.SP_SM)
        location = muted_label(path, wrap=True)
        location.setObjectName("Mono")
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(location, 1)
        top.addWidget(Badge(SOURCE_KIND_LABEL.get(kind, kind), "neutral"))
        column.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SP_SM)
        exists = Path(path).exists()
        summary = f"문서 {documents:,}건"
        if not exists:
            summary += "   ·   ⚠ 지금 이 폴더에 닿을 수 없습니다"
        bottom.addWidget(muted_label(summary, small=True, wrap=False))
        bottom.addStretch(1)

        change = QPushButton("위치 변경")
        change.setObjectName("Quiet")
        change.setToolTip("자료를 다른 폴더로 옮겼다면 여기서 새 위치를 지정합니다")
        change.clicked.connect(lambda: on_change(source_id, path))
        bottom.addWidget(change)

        remove = QPushButton("제거")
        remove.setObjectName("Destructive")
        remove.setToolTip("목록에서 뺍니다. 원본 파일은 지우지 않습니다.")
        remove.clicked.connect(lambda: on_remove(source_id, path, documents))
        bottom.addWidget(remove)
        column.addLayout(bottom)


def _source_kind(path: Path) -> str:
    return "unc" if str(path).startswith("\\\\") else "local"


def storage_warning(path: Path | str) -> str:
    """저장 위치가 내 계정 밖이면 알린다 (SEC-003, 계획서 §30-5).

    기본 위치(`%LOCALAPPDATA%`)는 윈도우가 계정 권한으로 막아 준다. 그런데
    `--data`로 공유 폴더나 USB를 지정하면 그 보호가 통째로 사라진다 — 부서
    공유 드라이브에 분석 결과를 두면 문서 본문 조각을 부서원 전체가 읽을 수
    있다. 권한 목록(ACL)을 해석하는 대신 **위치**로 판단한다. 정확도는 낮지만
    사용자가 확인해야 할 상황을 놓치지 않고, 어떤 윈도우에서도 같게 동작한다.
    """
    text = str(path)
    if text.startswith("\\\\"):
        return ("이 저장 위치는 네트워크 공유 폴더입니다. 분석 결과에는 문서 "
                "본문 조각이 들어 있으므로, 그 폴더에 접근할 수 있는 사람이 "
                "모두 읽을 수 있습니다.")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    try:
        Path(text).resolve().relative_to(Path(home).resolve())
    except (ValueError, OSError):
        return ("이 저장 위치는 내 계정 폴더 밖입니다. 같은 PC를 쓰는 다른 "
                "계정이 읽을 수 있는 자리인지 확인하세요.")
    return ""
