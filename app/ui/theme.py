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

# 본문 바탕. 흰 종이 위에 흰 카드를 올리면 카드가 카드로 안 보인다 —
# 바탕을 한 단계 낮춰야 카드·표·목록 줄이 각각 덩어리로 읽힌다.
CANVAS = "#F1F4F8"
BAND = "#F6F9FC"             # 표의 줄무늬. 가로줄을 눈으로 따라갈 수 있게 한다.

TEXT = "#1A1D21"
TEXT_MUTED = "#6B7280"
TEXT_DISABLED = "#9CA3AF"
TEXT_ON_PRIMARY = "#FFFFFF"

PRIMARY = "#1B5FA8"          # 채도를 낮춘 청색 — 관공서 문서에서 익숙한 계열
PRIMARY_HOVER = "#17518F"
PRIMARY_SOFT = "#E8F0F8"

ATTENTION = "#B45309"        # 확인 필요
ATTENTION_SOFT = "#FEF3E2"
DANGER = "#B91C1C"           # 오류
CONFIRMED = "#15803D"        # 확인됨

# 연간 패턴 격자의 막대. 상태를 색만으로 구분하지 않도록 막대 안에 기호를
# 함께 찍는다(PRD §18.4). 색은 "얼마나 단단한 근거인가"의 농도를 나타낸다.
CYCLE_STRONG = PRIMARY       # 담당자가 확인함
CYCLE_SOFT = "#A7C1DB"       # 자료에서 추정
CYCLE_WEAK = "#DFE6EE"       # 자료가 부족

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

# 연간 패턴 격자 (일정 화면)
GRID_NAME_W = 224            # 업무 이름 칸. 이보다 좁으면 이름이 자주 잘린다.
GRID_CELL_W = 46
GRID_ROW_H = 34
GRID_BAR_H = 22              # 막대 높이. 줄 높이보다 낮아야 행이 분리되어 보인다.


def stylesheet(large_text: bool = False) -> str:
    """large_text=True면 본문 글자를 약 20% 키운다 (PRD §18.4 접근성).

    별도 '고대비 테마'를 두지 않는 이유: 이미 상태를 색상 하나로 구분하지
    않고 기호+글자를 함께 쓰도록 설계했다(Badge, EvidenceChip 등). 그래서
    저시력 사용자에게 가장 먼저 도움이 되는 것은 대비 반전보다 글자 크기다.
    """
    scale = 1.2 if large_text else 1.0
    body = round(FS_BODY * scale)
    small = round(FS_SMALL * scale)
    section = round(FS_SECTION * scale)
    title = round(FS_TITLE * scale)
    return f"""
    * {{
        font-family: {FONT_FAMILY};
        font-size: {body}px;
        color: {TEXT};
    }}
    QMainWindow, QWidget#Content {{ background: {BG}; }}

    /* ── 상단 바 ── */
    QWidget#TopBar {{
        background: {BG};
        border-bottom: 1px solid {BORDER};
    }}
    QLabel#AppName {{
        font-size: {section}px;
        font-weight: 600;
        color: {TEXT};
    }}
    QLabel#StatusText {{
        color: {TEXT_MUTED};
        font-size: {small}px;
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
        font-size: {small}px;
        padding: {SP_SM}px {SP_MD}px {SP_XS}px {SP_MD}px;
    }}

    /* ── 화면 머리 ──
       제목·설명·탭을 흰 띠에 묶어 본문 바탕(회색)과 갈라 놓는다. 탭이
       어디에 붙은 물건인지 보이지 않으면 그냥 떠 있는 글자로 읽힌다. */
    QWidget#ViewHeader {{
        background: {BG};
        border-bottom: 1px solid {BORDER_STRONG};
    }}

    /* ── 화면 안 탭 ──
       사이드바(메뉴)와 확실히 달라 보여야 한다. 메뉴는 칠한 블록,
       탭은 머리 띠 아래를 무는 밑줄 — "지금 어느 메뉴 안에 있는가"를
       잃지 않으면서도, 본문 글자와는 확실히 구분되어야 한다. */
    QPushButton#Tab {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-bottom: 3px solid {BORDER};
        border-top-left-radius: {RADIUS_SM}px;
        border-top-right-radius: {RADIUS_SM}px;
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        padding: {SP_SM}px {SP_LG}px;
        color: {TEXT_MUTED};
        font-weight: 600;
        min-height: 26px;
    }}
    QPushButton#Tab:hover {{
        background: {SURFACE_ALT};
        color: {TEXT};
        border-bottom-color: {BORDER_STRONG};
    }}
    QPushButton#Tab:checked {{
        background: {BG};
        color: {PRIMARY};
        border-color: {BORDER_STRONG};
        border-bottom: 3px solid {PRIMARY};
    }}
    QWidget#TabBar {{ background: transparent; }}

    /* ── 공통 ── */
    QLabel#ViewTitle {{
        font-size: {title}px;
        font-weight: 600;
    }}
    QLabel#ViewLead {{
        color: {TEXT_MUTED};
        font-size: {body}px;
    }}
    QLabel#SectionTitle {{
        font-size: {section}px;
        font-weight: 600;
    }}
    QLabel#Muted {{ color: {TEXT_MUTED}; }}
    QLabel#Small {{ color: {TEXT_MUTED}; font-size: {small}px; }}

    /* 설명 문구는 내용이 아니다. 같은 회색 글씨로 흘려 두면 목록 항목과
       섞여 읽히므로, 옅은 판에 얹어 "이건 안내"라고 표시한다. */
    QLabel#PageNote {{
        background: {SURFACE_ALT};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
        color: {TEXT_MUTED};
        font-size: {small}px;
    }}

    QFrame#Card {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
    }}
    QFrame#Card:hover {{ border-color: {PRIMARY}; }}
    /* 왼쪽 굵은 띠로 급한 것을 표시한다 — 뱃지 하나보다 멀리서 보인다. */
    QFrame#CardAttention {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-left: 4px solid {ATTENTION};
        border-radius: {RADIUS}px;
    }}
    QFrame#CardAttention:hover {{ border-color: {ATTENTION}; }}
    QFrame#CardPrimary {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-left: 4px solid {PRIMARY};
        border-radius: {RADIUS}px;
    }}
    QFrame#CardPrimary:hover {{ border-color: {PRIMARY}; }}

    /* 카드 제목. 누를 수 있지만 파란 링크로 두면 본문과 위계가 뒤집힌다. */
    QPushButton#CardTitle {{
        background: transparent;
        border: none;
        padding: 0;
        text-align: left;
        font-size: {section}px;
        font-weight: 700;
        color: {TEXT};
    }}
    QPushButton#CardTitle:hover {{ color: {PRIMARY}; text-decoration: underline; }}

    /* 카드 안의 근거 묶음. 본문과 같은 흰 바탕에 두면 어디까지가 근거인지
       경계가 사라진다. */
    QFrame#SubPanel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px;
    }}

    /* ── 목록 한 줄 ──
       카드보다 가볍지만, 배경과 테두리로 "한 건"이라는 덩어리를 만든다. */
    QFrame#ListRow {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}
    QFrame#ListRow:hover {{ border-color: {PRIMARY}; background: {SURFACE}; }}
    QLabel#WhenChip {{
        background: {PRIMARY_SOFT};
        color: {PRIMARY};
        border-radius: {RADIUS_SM}px;
        padding: {SP_XS}px {SP_SM}px;
        font-weight: 700;
    }}
    QPushButton#RowTitle {{
        background: transparent;
        border: none;
        padding: 0;
        text-align: left;
        font-weight: 600;
        color: {TEXT};
    }}
    QPushButton#RowTitle:hover {{ color: {PRIMARY}; text-decoration: underline; }}

    /* ── 연간 패턴 격자 ── */
    QFrame#GridPanel {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
    }}
    QFrame#GridHeadRow {{
        background: {SURFACE_ALT};
        border-bottom: 1px solid {BORDER_STRONG};
        border-top-left-radius: {RADIUS}px;
        border-top-right-radius: {RADIUS}px;
    }}
    QLabel#GridHeadCell {{
        color: {TEXT_MUTED};
        font-size: {small}px;
        font-weight: 700;
    }}
    QLabel#GridHeadCellNow {{
        background: {PRIMARY};
        color: {TEXT_ON_PRIMARY};
        font-size: {small}px;
        font-weight: 700;
        border-top-left-radius: {RADIUS_SM}px;
        border-top-right-radius: {RADIUS_SM}px;
    }}
    QFrame#GridRow {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
    QFrame#GridRowAlt {{ background: {BAND}; border-bottom: 1px solid {BORDER}; }}
    /* 마지막 줄은 밑줄을 지운다 — 판 테두리와 겹쳐 두 줄로 보인다. */
    QFrame#GridRowLast {{ background: {BG}; border: none; }}
    QFrame#GridRowAltLast {{ background: {BAND}; border: none; }}
    QFrame#GridRow:hover, QFrame#GridRowAlt:hover,
    QFrame#GridRowLast:hover, QFrame#GridRowAltLast:hover {{
        background: {PRIMARY_SOFT};
    }}
    QPushButton#GridName {{
        background: transparent;
        border: none;
        padding: 0;
        text-align: left;
        font-weight: 600;
        color: {TEXT};
    }}
    QPushButton#GridName:hover {{ color: {PRIMARY}; text-decoration: underline; }}

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

    /* 확인 동작. 파란 덩어리를 여섯 줄에 늘어놓으면 화면이 버튼밭이 되므로
       테두리만 강조색으로 두고, 옆 동작은 조용한 버튼으로 내린다. */
    QPushButton#Confirm {{
        background: {PRIMARY_SOFT};
        border: 1px solid {PRIMARY};
        color: {PRIMARY};
        font-weight: 700;
        padding: {SP_XS}px {SP_MD}px;
    }}
    QPushButton#Confirm:hover {{ background: {PRIMARY}; color: {TEXT_ON_PRIMARY}; }}
    QPushButton#Quiet {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        color: {TEXT_MUTED};
        padding: {SP_XS}px {SP_MD}px;
    }}
    QPushButton#Quiet:hover {{ background: {SURFACE_ALT}; color: {TEXT}; }}

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
        font-size: {small}px;
        font-weight: 600;
    }}

    /* 스크롤 영역은 뷰포트와 내부 위젯까지 배경을 지정해야 한다.
       transparent로 두면 칠해지지 않은 영역이 검게 남는다. */
    QScrollArea {{ border: none; background: {BG}; }}
    QScrollArea > QWidget > QWidget {{ background: {BG}; }}
    QAbstractScrollArea::viewport {{ background: {BG}; }}

    /* 본문 바탕을 한 단계 낮춘 화면(일정). ID 선택자라 위의 일반 규칙을
       이긴다 — 뷰포트까지 지정하지 않으면 스크롤할 때 흰 띠가 남는다. */
    QWidget#Canvas, QWidget#PageBody {{ background: {CANVAS}; }}
    QScrollArea#PageScroll {{ background: {CANVAS}; }}
    QScrollArea#PageScroll::viewport {{ background: {CANVAS}; }}
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
        font-size: {small}px;
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
    QLabel#UnknownText {{ color: {ATTENTION}; font-size: {small}px; }}
    """
