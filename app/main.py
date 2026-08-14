"""진입점.

    python -m app.main [--project 이름] [--data 경로]

인자 없이 실행하면 **시작 화면**이 먼저 뜬다 — 이 PC에 있는 인수인계 목록에서
하나를 고르거나 새로 만든다(계획서 §22). `--project`는 개발·시험용으로
남겨 둔 지름길이고, 주면 그 프로젝트를 곧장 연다.

완전 로컬로 동작한다. 외부 네트워크를 호출하지 않으며, AI(Ollama)도
127.0.0.1로만 연결한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import default_project_dir, open_project_at, registry
from .db.registry import ProjectEntry
from .ui import theme
from .ui.shell import MainWindow
from .ui.views.launcher import LauncherDialog

_LOGO_PATH = Path(__file__).parent / "ui" / "assets" / "logo.png"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("눈치코치")
    app.setOrganizationName("NunchiCoach")
    if _LOGO_PATH.exists():
        app.setWindowIcon(QIcon(str(_LOGO_PATH)))

    root = Path(args.data) if args.data else None
    theme.apply(app, "medium", "system")   # 시작 화면에도 같은 옷을 입힌다
    target = _from_args(args, root)

    # 프로젝트를 바꾸면 창을 새로 만든다. MainWindow가 db를 생성자에서 받아
    # 뷰마다 들고 있어(shell.py), 열린 창의 db만 갈아 끼우는 것은 안전하지
    # 않다 — 백그라운드 스레드까지 옛 DB를 가리키고 있다.
    while True:
        if target is None:
            target = _choose(root)
            if target is None:
                return 0
        target = _run(app, target, root)
        if target is None:
            return 0


def _run(app: QApplication, entry: ProjectEntry, root: Path | None) -> ProjectEntry | None:
    """프로젝트 하나를 연다. 돌려주는 값이 있으면 그 프로젝트로 갈아탄다."""
    db = open_project_at(entry.path)
    db.set_meta("project_name", entry.name)
    registry.touch(entry.path, root)
    db.audit("app.start", detail=f"project={entry.name}")
    theme.apply(app, _text_size(db), theme.normalize_mode(db.get_meta("theme_mode")))

    window = MainWindow(db, project_name=entry.name)
    switch_to: list[ProjectEntry] = []

    def request_switch() -> None:
        chosen = _choose(root, parent=window, current=entry.path)
        if chosen is None or chosen.path == entry.path:
            return
        switch_to.append(chosen)
        window.close()

    window.switch_requested.connect(request_switch)
    disconnect = _follow_system_theme(app, db, window)
    window.show()
    try:
        app.exec()
    finally:
        disconnect()
        db.audit("app.stop")
        db.close()
    return switch_to[0] if switch_to else None


def _from_args(args, root: Path | None) -> ProjectEntry | None:
    """`--project 이름`으로 곧장 열기. 목록에도 올려 다음부터 화면에서 보인다."""
    if not args.project:
        return None
    return registry.register(
        (root or default_project_dir()) / args.project, name=args.project, root=root
    )


def _choose(root: Path | None, parent=None, current: Path | None = None) -> ProjectEntry | None:
    dialog = LauncherDialog(root, parent, current=current)
    dialog.exec()
    return dialog.chosen


def _follow_system_theme(app, db, window):
    """테마가 '시스템'이면 윈도우 설정이 바뀔 때 같이 바뀐다.

    시작할 때 한 번만 읽으면 '시스템'은 사실상 '그때의 시스템'이다. 야근하다
    윈도우를 어둡게 바꿨는데 이 창만 하얗게 남아 있으면 설정이 거짓말을 한 게
    된다. Qt 6.5부터 알려주는 신호가 있으니 그것에 붙인다.

    끊는 함수를 돌려준다. 프로젝트를 갈아탈 때마다 붙이기만 하면, 닫힌 창과
    이미 닫은 DB를 가리키는 연결이 앱이 사는 내내 쌓인다.
    """
    hints = app.styleHints()
    if not hasattr(hints, "colorSchemeChanged"):   # pragma: no cover — 옛 Qt 방어
        return lambda: None

    def repaint(_scheme=None) -> None:
        if theme.normalize_mode(db.get_meta("theme_mode")) != "system":
            return
        theme.apply(app, _text_size(db), "system")
        window.restyle()

    hints.colorSchemeChanged.connect(repaint)
    return lambda: hints.colorSchemeChanged.disconnect(repaint)


def _text_size(db) -> str:
    """저장된 글자 크기. 옛 설정(large_text)을 3단계로 옮겨 읽는다.

    쓰던 사람이 켜 둔 '글자 크게 보기'가 갱신 후 조용히 꺼져 있으면, 그
    사람에게는 기능이 사라진 것이 아니라 앱이 망가진 것으로 보인다.
    """
    stored = db.get_meta("text_size")
    if stored in theme.TEXT_SIZES:
        return stored
    return "large" if db.get_meta("large_text") == "1" else "medium"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="눈치코치")
    parser.add_argument(
        "--project",
        default=None,
        help="프로젝트 이름 (생략하면 시작 화면에서 고릅니다)",
    )
    parser.add_argument(
        "--data",
        default=None,
        help=f"프로젝트 저장 위치 (기본: {default_project_dir()})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
