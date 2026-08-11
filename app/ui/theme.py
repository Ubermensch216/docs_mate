"""디자인 토큰과 전역 스타일시트.

대상은 공직 사무 환경이다. 화려함은 신뢰를 깎으므로 무채색 기반에 강조색
하나만 쓴다. AI 결과에 특별한 색을 칠하지 않는 것도 의도다 — 색으로 튀게
하면 "기계가 한 말"로 분리되어 오히려 안 읽힌다. 구분은 근거 칩으로 한다.

상태는 색만으로 구분하지 않는다. 항상 아이콘이나 텍스트를 동반한다(PRD §18.4).
"""

from __future__ import annotations

# ── 색 ──────────────────────────────────────────────────────────────
BG = "#FFFFFF"
SURFACE = "#F7F8FA"
SURFACE_ALT = "#EEF1F5"
BORDER = "#E3E6EB"
BORDER_STRONG = "#CBD2DA"

TEXT = "#1A1D21"
TEXT_MUTED = "#6B7280"
TEXT_DISABLED = "#9CA3AF"
TEXT_ON_PRIMARY = "#FFFFFF"

PRIMARY = "#1B5FA8"          # 채도를 낮춘 청색 — 관공서 문서에서 익숙한 계열
PRIMARY_HOVER = "#17518F"
PRIMARY_SOFT = "#E8F0F8"

ATTENTION = "#B45309"        # 확인 필요
DANGER = "#B91C1C"           # 오류
CONFIRMED = "#15803D"        # 확인됨

# ── 타이포 ──────────────────────────────────────────────────────────
FONT_FAMILY = '"Pretendard", "Malgun Gothic", "맑은 고딕", sans-serif'
FS_TITLE = 20
FS_SECTION = 16
FS_BODY = 14
FS_SMALL = 12
LINE_HEIGHT = 1.6            # 한글은 1.5 이하면 답답하다

# ── 간격·크기 (4px 그리드) ──────────────────────────────────────────
SP_XS, SP_SM, SP_MD, SP_LG, SP_XL = 4, 8, 12, 16, 24
RADIUS = 8
RADIUS_SM = 4

SIDEBAR_W = 200
TOPBAR_H = 48
ROW_H = 36
DETAIL_PANEL_W = 360
CONTENT_MAX_W = 1040
WINDOW_MIN = (1120, 720)


def stylesheet() -> str:
    return f"""
    * {{
        font-family: {FONT_FAMILY};
        font-size: {FS_BODY}px;
        color: {TEXT};
    }}
    QMainWindow, QWidget#Content {{ background: {BG}; }}

    /* ── 상단 바 ── */
    QWidget#TopBar {{
        background: {BG};
        border-bottom: 1px solid {BORDER};
    }}
    QLabel#AppName {{
        font-size: {FS_SECTION}px;
        font-weight: 600;
        color: {TEXT};
    }}
    QLabel#StatusText {{
        color: {TEXT_MUTED};
        font-size: {FS_SMALL}px;
    }}
    QProgressBar#TopProgress {{
        background: {SURFACE_ALT};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar#TopProgress::chunk {{
        background: {PRIMARY};
        border-radius: 3px;
    }}

    /* ── 사이드바 ── */
    QWidget#Sidebar {{
        background: {SURFACE};
        border-right: 1px solid {BORDER};
    }}
    QPushButton#NavItem {{
        background: transparent;
        border: none;
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
        text-align: left;
        color: {TEXT_MUTED};
        min-height: 24px;
    }}
    QPushButton#NavItem:hover {{
        background: {SURFACE_ALT};
        color: {TEXT};
    }}
    QPushButton#NavItem:checked {{
        background: {PRIMARY_SOFT};
        color: {PRIMARY};
        font-weight: 600;
    }}
    QLabel#NavSection {{
        color: {TEXT_DISABLED};
        font-size: {FS_SMALL}px;
        padding: {SP_SM}px {SP_MD}px {SP_XS}px {SP_MD}px;
    }}

    /* ── 공통 ── */
    QLabel#ViewTitle {{
        font-size: {FS_TITLE}px;
        font-weight: 600;
    }}
    QLabel#ViewLead {{
        color: {TEXT_MUTED};
        font-size: {FS_BODY}px;
    }}
    QLabel#SectionTitle {{
        font-size: {FS_SECTION}px;
        font-weight: 600;
    }}
    QLabel#Muted {{ color: {TEXT_MUTED}; }}
    QLabel#Small {{ color: {TEXT_MUTED}; font-size: {FS_SMALL}px; }}

    QFrame#Card {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}
    QFrame#Card:hover {{ border-color: {BORDER_STRONG}; }}

    QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}

    QPushButton {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_LG}px;
    }}
    QPushButton:hover {{ background: {SURFACE}; }}
    QPushButton:disabled {{ color: {TEXT_DISABLED}; border-color: {BORDER}; }}
    QPushButton#Primary {{
        background: {PRIMARY};
        border: 1px solid {PRIMARY};
        color: {TEXT_ON_PRIMARY};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {PRIMARY_HOVER}; }}
    QPushButton#Link {{
        background: transparent;
        border: none;
        color: {PRIMARY};
        padding: {SP_XS}px 0;
        text-align: left;
    }}
    QPushButton#Link:hover {{ text-decoration: underline; }}

    QLineEdit, QComboBox {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {TEXT};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}

    QTreeView, QTableView, QListView {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
        alternate-background-color: {SURFACE};
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {TEXT};
        outline: none;
    }}
    QHeaderView::section {{
        background: {SURFACE};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: {SP_SM}px {SP_MD}px;
        color: {TEXT_MUTED};
        font-size: {FS_SMALL}px;
        font-weight: 600;
    }}

    /* 스크롤 영역은 뷰포트와 내부 위젯까지 배경을 지정해야 한다.
       transparent로 두면 칠해지지 않은 영역이 검게 남는다. */
    QScrollArea {{ border: none; background: {BG}; }}
    QScrollArea > QWidget > QWidget {{ background: {BG}; }}
    QAbstractScrollArea::viewport {{ background: {BG}; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_STRONG}; border-radius: 5px; min-height: 32px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    /* ── 상태 뱃지 (색만으로 구분하지 않는다) ── */
    QLabel#BadgeNeutral, QLabel#BadgeAttention,
    QLabel#BadgeDanger, QLabel#BadgeOk {{
        border-radius: {RADIUS_SM}px;
        padding: 2px {SP_SM}px;
        font-size: {FS_SMALL}px;
    }}
    QLabel#BadgeNeutral   {{ background: {SURFACE_ALT}; color: {TEXT_MUTED}; }}
    QLabel#BadgeAttention {{ background: #FEF3E2; color: {ATTENTION}; }}
    QLabel#BadgeDanger    {{ background: #FDECEC; color: {DANGER}; }}
    QLabel#BadgeOk        {{ background: #E9F6EE; color: {CONFIRMED}; }}

    /* ── 확인되지 않은 구간 (UnknownBlock) ── */
    QFrame#UnknownBlock {{
        background: {SURFACE};
        border: 1px dashed {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
    }}
    QLabel#UnknownText {{ color: {ATTENTION}; font-size: {FS_SMALL}px; }}
    """
