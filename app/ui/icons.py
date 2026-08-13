"""단색 선 아이콘.

이모지를 쓰지 않는 이유
    🗂📅📄💬는 OS가 제 색으로 칠해 버린다. 넷이 각각 다른 색으로 튀면
    메뉴가 아니라 스티커가 되고, 고른 메뉴를 강조색으로 물들여도 아이콘만
    혼자 원래 색으로 남아 "지금 여기"가 흐려진다. 화면 전체를 무채색 +
    강조색 하나로 묶기로 한 원칙(theme.py)과도 어긋난다.

    그래서 24×24 격자 위에 굵기 1.6의 선으로 직접 그린다. 색은 그릴 때
    주입하므로 상태(보통·선택·비활성)에 따라 글자와 같은 색으로 움직인다.

QtSvg가 없는 환경에서는 None을 돌려준다 — 호출부가 글자로 물러선다.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover — 패키징 환경 방어
    QSvgRenderer = None  # type: ignore[assignment]

# 선으로만 그린다(fill="none"). 채운 조각이 필요한 곳만 fill="{c}"를 준다.
_SHAPES = {
    # 업무 — 집게 달린 서류판에 체크. "해야 할 일 묶음"의 관용 기호.
    "tasks": (
        '<rect x="4.5" y="5" width="15" height="16" rx="2.5"/>'
        '<rect x="9" y="2.9" width="6" height="4.2" rx="1.3" fill="{c}" stroke="none"/>'
        '<path d="M8.6 13.4 l2.4 2.4 L15.8 11"/>'
    ),
    # 일정 — 달력. 위 고리 두 개와 머리 줄로 "달력"임을 한눈에 알린다.
    "calendar": (
        '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/>'
        '<path d="M3.5 9.6 H20.5"/>'
        '<path d="M8 3.2 V6.6"/><path d="M16 3.2 V6.6"/>'
        '<rect x="7" y="12.4" width="2" height="2" rx="0.6" fill="{c}" stroke="none"/>'
        '<rect x="11" y="12.4" width="2" height="2" rx="0.6" fill="{c}" stroke="none"/>'
        '<rect x="15" y="12.4" width="2" height="2" rx="0.6" fill="{c}" stroke="none"/>'
        '<rect x="7" y="16.2" width="2" height="2" rx="0.6" fill="{c}" stroke="none"/>'
        '<rect x="11" y="16.2" width="2" height="2" rx="0.6" fill="{c}" stroke="none"/>'
    ),
    # 문서 — 모서리를 접은 낱장. 접힌 귀가 있어야 '파일'이 아니라 '문서'다.
    "documents": (
        '<path d="M6.5 3.25 H13.25 L18.75 8.75 V20 '
        'a1.5 1.5 0 0 1 -1.5 1.5 H6.5 A1.5 1.5 0 0 1 5 20 V4.75 '
        'A1.5 1.5 0 0 1 6.5 3.25 Z"/>'
        '<path d="M13 3.5 V8.4 a0.6 0.6 0 0 0 0.6 0.6 H18.5"/>'
        '<path d="M8.2 13.4 H15.4"/><path d="M8.2 16.8 H13"/>'
    ),
    # 질문 — 말풍선. 꼬리까지 한 획으로 그려야 이어 붙인 티가 나지 않는다.
    "ask": (
        '<path d="M6.75 4.25 H17.25 A3 3 0 0 1 20.25 7.25 V13.75 '
        'A3 3 0 0 1 17.25 16.75 H12.4 L8.4 20.05 V16.75 H6.75 '
        'A3 3 0 0 1 3.75 13.75 V7.25 A3 3 0 0 1 6.75 4.25 Z"/>'
        '<path d="M7.9 8.9 H16.1"/><path d="M7.9 12.1 H13"/>'
    ),
    # 읽기 전용 — 자물쇠. 약속을 말하는 자리라 글자 옆에 작게 붙는다.
    "lock": (
        '<rect x="5" y="10.4" width="14" height="9.6" rx="2.2"/>'
        '<path d="M8.3 10.4 V7.7 a3.7 3.7 0 0 1 7.4 0 V10.4"/>'
        '<circle cx="12" cy="15.2" r="1.15" fill="{c}" stroke="none"/>'
    ),
    # 자료 폴더 — 탭 달린 폴더. 설정의 '자료 폴더' 갈래 표지.
    "folder": (
        '<path d="M3.5 6.75 a1.75 1.75 0 0 1 1.75 -1.75 H9.4 l2 2.4 H18.75 '
        'A1.75 1.75 0 0 1 20.5 9.15 V17.75 A1.75 1.75 0 0 1 18.75 19.5 '
        'H5.25 A1.75 1.75 0 0 1 3.5 17.75 Z"/>'
    ),
    # 시스템 — 톱니. AI 현황·저장 위치·최근 활동이 모이는 갈래 표지.
    "system": (
        '<circle cx="12" cy="12" r="3.1"/>'
        '<path d="M12 2.9 V5.3"/><path d="M12 18.7 V21.1"/>'
        '<path d="M2.9 12 H5.3"/><path d="M18.7 12 H21.1"/>'
        '<path d="M5.6 5.6 L7.3 7.3"/><path d="M16.7 16.7 L18.4 18.4"/>'
        '<path d="M18.4 5.6 L16.7 7.3"/><path d="M7.3 16.7 L5.6 18.4"/>'
    ),

    # ── 테마 3종 ──
    # 셋이 한자리에 놓이므로 실루엣이 서로 겹치면 안 된다. 해(둥근 방사),
    # 달(초승달), 모니터(네모+받침) — 축소해도 윤곽만으로 구별된다.
    "theme-light": (
        '<circle cx="12" cy="12" r="4.2"/>'
        '<path d="M12 2.6 V5"/><path d="M12 19 V21.4"/>'
        '<path d="M2.6 12 H5"/><path d="M19 12 H21.4"/>'
        '<path d="M5.4 5.4 L7.1 7.1"/><path d="M16.9 16.9 L18.6 18.6"/>'
        '<path d="M18.6 5.4 L16.9 7.1"/><path d="M7.1 16.9 L5.4 18.6"/>'
    ),
    "theme-dark": (
        '<path d="M20.2 14.3 A8.6 8.6 0 0 1 9.7 3.8 '
        'A8.6 8.6 0 1 0 20.2 14.3 Z"/>'
    ),
    "theme-system": (
        '<rect x="3" y="4.6" width="18" height="12.2" rx="2"/>'
        '<path d="M9 20.2 H15"/><path d="M12 16.8 V20.2"/>'
        # 화면 절반만 칠한다 — "밝기를 OS에 맡긴다"를 그림 하나로 말한다.
        '<path d="M12 6.6 H19 V14.8 H12 Z" fill="{c}" stroke="none"/>'
    ),

    # ── 글자 크기 3종 ──
    # 같은 'A'를 크기만 달리 그린다. 그림이 곧 결과라서 아래 글자를 읽지
    # 않아도 무엇이 커지는지 안다. viewBox 안에서 아래쪽 기준선을 맞춰
    # 셋이 한 줄에 앉은 것처럼 보이게 한다.
    "text-small": (
        '<path d="M8.4 16.4 L12 8.9 L15.6 16.4"/>'
        '<path d="M9.9 13.8 H14.1"/>'
    ),
    "text-medium": (
        '<path d="M7.2 17.6 L12 7.2 L16.8 17.6"/>'
        '<path d="M9.2 14 H14.8"/>'
    ),
    "text-large": (
        '<path d="M5.6 19.2 L12 4.8 L18.4 19.2"/>'
        '<path d="M8.3 13.4 H15.7"/>'
    ),
}

_cache: dict[tuple[str, str, int], QPixmap] = {}


def pixmap(name: str, color: str, size: int = 20) -> QPixmap | None:
    """단색 아이콘 픽스맵. 없으면 None — 호출부는 글자로 물러선다.

    2배로 그린 뒤 devicePixelRatio를 올린다. 고해상도 화면에서 선 아이콘은
    등배로 그리면 획이 뭉개져 오히려 이모지보다 지저분해 보인다.
    """
    if QSvgRenderer is None or name not in _SHAPES:
        return None
    key = (name, color, size)
    if key in _cache:
        return _cache[key]

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" fill="none" stroke="{color}" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        f'{_SHAPES[name].format(c=color)}</svg>'
    )
    ratio = 2
    canvas = QPixmap(size * ratio, size * ratio)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    canvas.setDevicePixelRatio(ratio)
    _cache[key] = canvas
    return canvas
