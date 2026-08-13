"""진입점.

    python -m app.main [--project 이름] [--data 경로]

완전 로컬로 동작한다. 외부 네트워크를 호출하지 않으며, AI(Ollama)도
127.0.0.1로만 연결한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import default_project_dir, open_project
from .ui import theme
from .ui.shell import MainWindow

_LOGO_PATH = Path(__file__).parent / "ui" / "assets" / "logo.png"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("눈치코치")
    app.setOrganizationName("NunchiCoach")
    if _LOGO_PATH.exists():
        app.setWindowIcon(QIcon(str(_LOGO_PATH)))

    db = open_project(args.project, Path(args.data) if args.data else None)
    db.audit("app.start", detail=f"project={args.project}")
    theme.apply(app, _text_size(db), theme.normalize_mode(db.get_meta("theme_mode")))

    window = MainWindow(db)
    _follow_system_theme(app, db, window)
    window.show()
    try:
        return app.exec()
    finally:
        db.audit("app.stop")
        db.close()


def _follow_system_theme(app, db, window) -> None:
    """테마가 '시스템'이면 윈도우 설정이 바뀔 때 같이 바뀐다.

    시작할 때 한 번만 읽으면 '시스템'은 사실상 '그때의 시스템'이다. 야근하다
    윈도우를 어둡게 바꿨는데 이 창만 하얗게 남아 있으면 설정이 거짓말을 한 게
    된다. Qt 6.5부터 알려주는 신호가 있으니 그것에 붙인다.
    """
    hints = app.styleHints()
    if not hasattr(hints, "colorSchemeChanged"):   # pragma: no cover — 옛 Qt 방어
        return

    def repaint(_scheme=None) -> None:
        if theme.normalize_mode(db.get_meta("theme_mode")) != "system":
            return
        theme.apply(app, _text_size(db), "system")
        window.restyle()

    hints.colorSchemeChanged.connect(repaint)


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
    parser.add_argument("--project", default="default", help="프로젝트 이름")
    parser.add_argument(
        "--data",
        default=None,
        help=f"프로젝트 저장 위치 (기본: {default_project_dir()})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
