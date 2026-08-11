"""진입점.

    python -m app.main [--project 이름] [--data 경로]

완전 로컬로 동작한다. 외부 네트워크를 호출하지 않으며, AI(Ollama)도
127.0.0.1로만 연결한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .db import default_project_dir, open_project
from .ui import theme
from .ui.shell import MainWindow


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("업무기억관")
    app.setOrganizationName("WorkMemory")

    db = open_project(args.project, Path(args.data) if args.data else None)
    db.audit("app.start", detail=f"project={args.project}")
    app.setStyleSheet(theme.stylesheet(large_text=db.get_meta("large_text") == "1"))

    window = MainWindow(db)
    window.show()
    try:
        return app.exec()
    finally:
        db.audit("app.stop")
        db.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="업무기억관")
    parser.add_argument("--project", default="default", help="프로젝트 이름")
    parser.add_argument(
        "--data",
        default=None,
        help=f"프로젝트 저장 위치 (기본: {default_project_dir()})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
