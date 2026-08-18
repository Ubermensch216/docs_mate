"""디자인 토큰과 전역 스타일시트.

대상은 공직 사무 환경이다. 화려함은 신뢰를 깎으므로 무채색 기반에 강조색
하나만 쓴다. AI 결과에 특별한 색을 칠하지 않는 것도 의도다 — 색으로 튀게
하면 "기계가 한 말"로 분리되어 오히려 안 읽힌다. 구분은 근거 칩으로 한다.

상태는 색만으로 구분하지 않는다. 항상 아이콘이나 텍스트를 동반한다(PRD §18.4).

색은 두 벌(밝게·어둡게)이다. 모듈 전역 이름(TEXT, PRIMARY …)은 그대로 두고
apply_mode()가 그 이름이 가리키는 값을 바꾼다. 화면 코드는 예전처럼
theme.TEXT라고만 쓰면 되고, 어느 벌을 쓰는지는 신경 쓰지 않는다.

한 가지 규칙만 지키면 된다: **색을 모듈 수준 상수에 미리 담아 두지 않는다.**
import 시점에 값을 복사해 두면 테마를 바꿔도 그 사본은 옛 색으로 남는다.
색이 필요한 표는 값 대신 이름을 담고 theme.color("TEXT")로 꺼낸다.
"""

from __future__ import annotations

# ── 색 두 벌 ────────────────────────────────────────────────────────
# 이름은 역할이지 색이 아니다. 어두운 벌에서 BG가 검어지듯, 같은 이름이
# 같은 역할을 계속 맡는다.
LIGHT = {
    "BG": "#FFFFFF",
    "SURFACE": "#F7F8FA",
    "SURFACE_ALT": "#EEF1F5",
    "BORDER": "#E3E6EB",
    "BORDER_STRONG": "#CBD2DA",

    # 본문 바탕. 흰 종이 위에 흰 카드를 올리면 카드가 카드로 안 보인다 —
    # 바탕을 한 단계 낮춰야 카드·표·목록 줄이 각각 덩어리로 읽힌다.
    "CANVAS": "#F1F4F8",
    "BAND": "#F6F9FC",           # 표의 줄무늬. 가로줄을 눈으로 따라갈 수 있게 한다.

    # 사이드바는 본문보다 한 단계 더 짙게. 길과 내용이 같은 색이면 화면이
    # 한 덩어리로 보이고, 메뉴가 "그냥 왼쪽에 있는 글자"가 된다.
    "NAV_BG": "#E7EDF4",
    "NAV_HOVER": "#DDE5EF",

    "TEXT": "#1A1D21",
    "TEXT_MUTED": "#6B7280",
    "TEXT_DISABLED": "#9CA3AF",
    # 부기(副記) 전용 회색. 본문 회색(TEXT_MUTED)과 같은 색을 쓰면 메뉴 이름
    # 아래 설명이 "작은 본문"으로 읽혀 위계가 서지 않는다. 한 단계 더 물린다.
    "TEXT_SUBTLE": "#8B95A3",
    "TEXT_ON_PRIMARY": "#FFFFFF",

    "PRIMARY": "#1B5FA8",        # 채도를 낮춘 청색 — 관공서 문서에서 익숙한 계열
    "PRIMARY_HOVER": "#17518F",
    "PRIMARY_SOFT": "#E8F0F8",

    "ATTENTION": "#B45309",      # 확인 필요
    "ATTENTION_SOFT": "#FEF3E2",
    "DANGER": "#B91C1C",         # 오류
    "DANGER_SOFT": "#FDECEC",
    "CONFIRMED": "#15803D",      # 확인됨
    "CONFIRMED_SOFT": "#E9F6EE",

    # 연간 패턴 격자의 막대. 상태를 색만으로 구분하지 않도록 막대 안에 기호를
    # 함께 찍는다(PRD §18.4). 색은 "얼마나 단단한 근거인가"의 농도를 나타낸다.
    "CYCLE_STRONG": "#1B5FA8",   # 담당자가 확인함
    "CYCLE_SOFT": "#A7C1DB",     # 자료에서 추정
    "CYCLE_WEAK": "#DFE6EE",     # 자료가 부족

    # 상단 띠. 길(메뉴)과 내용(본문)을 가르는 검은 띠 하나 — 밝은 벌에서도
    # 어두운 벌에서도 같은 자리에 같은 무게로 선다. 색을 늘리는 것이 아니라
    # 구조를 만드는 것이라 두 벌이 같은 값을 쓴다.
    "NAVBAR_BG": "#15181D",
    "NAVBAR_TEXT": "#FFFFFF",
    "NAVBAR_MUTED": "#98A1AD",
    "NAVBAR_DISABLED": "#5A626D",
    "NAVBAR_HOVER": "#20242B",
    "NAVBAR_ACTIVE": "#2C323B",
    "NAVBAR_LINE": "#333941",
}

# 어두운 벌. 밝은 벌을 그대로 뒤집지 않는다 — 순수한 검정 바탕에 순백 글자는
# 대비가 지나쳐 잔상이 남는다. 바탕은 짙은 회청색, 글자는 살짝 낮춘 흰색으로
# 두고, 강조색은 어두운 바탕 위에서 대비가 서도록 한 단계 밝힌다.
DARK = {
    "BG": "#1B1E24",
    "SURFACE": "#22262D",
    "SURFACE_ALT": "#2B3038",
    "BORDER": "#2F343C",
    "BORDER_STRONG": "#434A54",

    "CANVAS": "#14171C",
    "BAND": "#1F232A",

    "NAV_BG": "#171A20",
    "NAV_HOVER": "#252A32",

    "TEXT": "#E6E9EE",
    "TEXT_MUTED": "#A2ABB6",
    "TEXT_DISABLED": "#6C7681",
    "TEXT_SUBTLE": "#8A94A0",
    "TEXT_ON_PRIMARY": "#0F1216",

    "PRIMARY": "#6BA6E4",
    "PRIMARY_HOVER": "#8CBCEE",
    "PRIMARY_SOFT": "#1E2C3B",

    "ATTENTION": "#E0A45C",
    "ATTENTION_SOFT": "#33291B",
    "DANGER": "#E97C7C",
    "DANGER_SOFT": "#331E1E",
    "CONFIRMED": "#69BE83",
    "CONFIRMED_SOFT": "#1B2E22",

    "CYCLE_STRONG": "#3E7FBE",
    "CYCLE_SOFT": "#31506F",
    "CYCLE_WEAK": "#2B3038",
    # 상단 띠. 길(메뉴)과 내용(본문)을 가르는 검은 띠 하나 — 밝은 벌에서도
    # 어두운 벌에서도 같은 자리에 같은 무게로 선다. 색을 늘리는 것이 아니라
    # 구조를 만드는 것이라 두 벌이 같은 값을 쓴다.
    "NAVBAR_BG": "#15181D",
    "NAVBAR_TEXT": "#FFFFFF",
    "NAVBAR_MUTED": "#98A1AD",
    "NAVBAR_DISABLED": "#5A626D",
    "NAVBAR_HOVER": "#20242B",
    "NAVBAR_ACTIVE": "#2C323B",
    "NAVBAR_LINE": "#333941",
}

# 정적 분석기와 예전 호출부를 위해 밝은 벌의 값을 모듈 전역으로 펼쳐 둔다.
# apply_mode()가 여기를 덮어쓴다.
globals().update(LIGHT)

BG: str
SURFACE: str
SURFACE_ALT: str
BORDER: str
BORDER_STRONG: str
CANVAS: str
BAND: str
NAV_BG: str
NAV_HOVER: str
TEXT: str
TEXT_MUTED: str
TEXT_DISABLED: str
TEXT_SUBTLE: str
TEXT_ON_PRIMARY: str
PRIMARY: str
PRIMARY_HOVER: str
PRIMARY_SOFT: str
ATTENTION: str
ATTENTION_SOFT: str
DANGER: str
DANGER_SOFT: str
CONFIRMED: str
CONFIRMED_SOFT: str
CYCLE_STRONG: str
CYCLE_SOFT: str
CYCLE_WEAK: str
NAVBAR_BG: str
NAVBAR_TEXT: str
NAVBAR_MUTED: str
NAVBAR_DISABLED: str
NAVBAR_HOVER: str
NAVBAR_ACTIVE: str
NAVBAR_LINE: str

_mode = "light"          # 지금 칠해져 있는 벌 (light | dark)


# ── 테마 모드 ───────────────────────────────────────────────────────
# 저장값은 셋뿐이다. system은 "지금 OS가 뭘 쓰는가"를 따라간다 — 사용자가
# 저녁에 OS를 어둡게 바꾸면 이 앱도 같이 어두워져야 한다.
THEME_MODES = ("system", "light", "dark")
THEME_LABELS = {"system": "시스템", "light": "밝게", "dark": "어둡게"}
THEME_ICONS = {"system": "theme-system", "light": "theme-light", "dark": "theme-dark"}
THEME_HINTS = {
    "system": "윈도우 설정을 따라갑니다",
    "light": "밝은 배경에 어두운 글자",
    "dark": "어두운 배경에 밝은 글자",
}


def normalize_mode(value: str | None) -> str:
    return value if value in THEME_MODES else "system"


def resolve_mode(mode: str) -> str:
    """system을 실제로 칠할 벌(light|dark)로 바꾼다.

    Qt 6.5부터 styleHints().colorScheme()이 OS 설정을 알려준다. 그보다 낮은
    환경에서는 알 길이 없으므로 밝은 벌로 물러선다 — 어둡게가 필요하면
    사용자가 직접 고르면 된다.
    """
    mode = normalize_mode(mode)
    if mode != "system":
        return mode
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
    except (ImportError, AttributeError):  # pragma: no cover — 옛 Qt 방어
        pass
    return "light"


def apply_mode(mode: str) -> str:
    """색 이름이 가리키는 값을 갈아 끼운다. 실제로 칠한 벌을 돌려준다."""
    global _mode
    resolved = resolve_mode(mode)
    globals().update(DARK if resolved == "dark" else LIGHT)
    _mode = resolved
    return resolved


def current_mode() -> str:
    return _mode


def color(name: str) -> str:
    """지금 칠해져 있는 벌에서 색 하나를 꺼낸다.

    표(어떤 상태 → 어떤 색)를 모듈 수준에 두어야 할 때 값 대신 이 함수로
    미룬다. 값을 미리 담아 두면 테마를 바꿔도 그 표만 옛 색으로 남는다.
    """
    return globals().get(name, globals()["TEXT"])


# ── 타이포 ──────────────────────────────────────────────────────────
# 글꼴 후보. **설치된 것 하나만 골라 쓴다** — 목록을 그대로 스타일시트에 넣으면
# 없는 글꼴(Pretendard)이 QFont의 이름이 되고, 재는 것은 그 이름의 대체 글꼴,
# 그리는 것은 한글 대체 글꼴이 되어 **줄 높이가 어긋난다.** 실제로 안내 문구의
# 둘째 줄이 판 밖으로 잘려 나갔다(문서 화면 오른쪽 패널).
FONT_CANDIDATES = ("Pretendard", "Malgun Gothic", "맑은 고딕", "Segoe UI")
_family: str | None = None


def font_family() -> str:
    """이 PC에 실제로 설치된 첫 글꼴. 없으면 시스템 기본에 맡긴다.

    **앱이 서기 전에는 묻지 않는다.** QFontDatabase를 QGuiApplication 없이
    건드리면 Qt가 예외가 아니라 프로세스를 죽인다 — try/except로 못 막는다.
    그리고 못 찾은 답은 캐시하지 않는다. 한 번 빈 값을 담아 두면 앱이 선
    뒤에도 계속 빈 값을 돌려주어, 글꼴이 영영 안 맞는다(시험에서 잡혔다).
    """
    global _family
    if _family:
        return _family
    try:
        from PySide6.QtGui import QFontDatabase, QGuiApplication

        if QGuiApplication.instance() is None:
            return ""
        installed = set(QFontDatabase.families())
    except Exception:                      # pragma: no cover — Qt 없이 부를 때
        return ""
    _family = next((name for name in FONT_CANDIDATES if name in installed), "")
    return _family


FONT_FAMILY = '"Pretendard", "Malgun Gothic", "맑은 고딕", sans-serif'
FS_TITLE = 20
FS_SECTION = 16
FS_BODY = 14
FS_SMALL = 12
LINE_HEIGHT = 1.6            # 한글은 1.5 이하면 답답하다

# 글자 크기 3단계. 소·중·대이며 저장값은 이 키다.
# 큰 쪽을 1.2로 둔 근거는 PRD §18.4(저시력 지원)이고, 작은 쪽은 그 대칭이
# 아니라 0.9까지만 내린다 — 그 아래는 한글 자소가 뭉개져 오히려 안 읽힌다.
TEXT_SIZES = {"small": 0.9, "medium": 1.0, "large": 1.2}
TEXT_SIZE_ORDER = ("small", "medium", "large")
TEXT_SIZE_LABELS = {"small": "작게", "medium": "보통", "large": "크게"}
TEXT_SIZE_ICONS = {"small": "text-small", "medium": "text-medium", "large": "text-large"}


# 한글 글자는 자소 셋이 한 칸에 들어간다. 이 아래로 내려가면 획이 서로
# 붙어 뭉개진다 — 알파벳은 10px에서도 읽히지만 한글은 아니다. '작게'를
# 골랐을 때 부기 글자가 11px이 되어 실제로 뭉개졌다.
MIN_FONT_PX = 12


def _qss_family() -> str:
    """스타일시트에 넣을 글꼴 이름. 설치된 것이 없으면 항목 자체를 비운다."""
    name = font_family()
    return f'"{name}"' if name else "sans-serif"


def _scaled(base: int, scale: float) -> int:
    """글자 크기를 비율로 줄이되 읽을 수 있는 하한 아래로는 내리지 않는다.

    '작게'가 모든 글자를 똑같이 줄이지는 못한다는 뜻이다 — 본문·제목은
    줄어들고 부기는 그대로다. 안 읽히는 글자를 만드는 것보다 낫다.
    """
    return max(MIN_FONT_PX, round(base * scale))


def normalize_text_size(value: str | None) -> str:
    return value if value in TEXT_SIZES else "medium"


def body_px(text_size: str) -> int:
    """설정 화면이 '(13px)'처럼 보여줄 실제 본문 크기. 하한을 함께 반영한다."""
    return _scaled(FS_BODY, TEXT_SIZES[normalize_text_size(text_size)])


# ── 간격·크기 (4px 그리드) ──────────────────────────────────────────
SP_XS, SP_SM, SP_MD, SP_LG, SP_XL = 4, 8, 12, 16, 24
RADIUS = 8
RADIUS_SM = 4

# 상단 띠. 메뉴가 여기 산다(세로 사이드바는 걷어 냈다 — shell.TopBar 참고).
TOPBAR_H = 52
ROW_H = 36
# 입력칸·드롭다운·버튼의 공통 높이. 한 줄에 서면 같은 키여야 한 벌로 보인다.
CONTROL_H = 36
DETAIL_PANEL_W = 360
# 근거 서랍(계획서 §28: 360~420px). 인용문이 서너 줄로 접히는 최소 폭이다.
DRAWER_W = 380
CONTENT_MAX_W = 1040
WINDOW_MIN = (1120, 720)

# 연간 패턴 격자 (일정 화면)
GRID_NAME_W = 224            # 업무 이름 칸. 이보다 좁으면 이름이 자주 잘린다.
GRID_CELL_W = 46
GRID_ROW_H = 34
GRID_BAR_H = 22              # 막대 높이. 줄 높이보다 낮아야 행이 분리되어 보인다.

# 설정 창 왼쪽 갈래 목록
SETTINGS_NAV_W = 168


def apply(app, text_size: str = "medium", mode: str = "system") -> str:
    """앱 전체에 테마를 적용한다. 실제로 칠한 벌(light|dark)을 돌려준다.

    색 교체와 스타일시트 재적용은 항상 붙어 다녀야 한다 — 한쪽만 하면
    QSS는 어두운데 위젯이 직접 칠한 색은 밝은, 반쪽짜리 화면이 나온다.
    """
    resolved = apply_mode(mode)
    # 앱 기본 글꼴도 같은 이름으로 맞춘다. 스타일시트만 바꾸면 위젯이 들고
    # 있는 QFont는 옛 이름 그대로라, **재는 글꼴과 그리는 글꼴이 갈린다.**
    name = font_family()
    if name:
        from PySide6.QtGui import QFont

        font = QFont(app.font())
        font.setFamily(name)
        app.setFont(font)
    app.setStyleSheet(stylesheet(text_size))
    return resolved


def stylesheet(text_size: str = "medium") -> str:
    """지금 칠해져 있는 색 벌로 전역 스타일시트를 짓는다.

    글자 크기는 소·중·대 3단계다(PRD §18.4 접근성). 별도 '고대비 테마'를
    두지 않는 이유: 이미 상태를 색상 하나로 구분하지 않고 기호+글자를 함께
    쓰도록 설계했다(Badge, EvidenceChip 등). 그래서 저시력 사용자에게 가장
    먼저 도움이 되는 것은 대비 반전보다 글자 크기다.
    """
    scale = TEXT_SIZES[normalize_text_size(text_size)]
    body = _scaled(FS_BODY, scale)
    small = _scaled(FS_SMALL, scale)
    section = _scaled(FS_SECTION, scale)
    title = _scaled(FS_TITLE, scale)
    return f"""
    * {{
        font-family: {_qss_family()};
        font-size: {body}px;
        color: {TEXT};
    }}
    QMainWindow, QWidget#Content, QDialog {{ background: {BG}; }}

    /* ── 상단 바 ── */
    /* ── 상단 띠 (길 + 상태) ──
       메뉴가 여기 산다. 세로 사이드바를 걷어 낸 자리라 본문은 폭을 다 받는다.
       띠를 어둡게 두는 것은 장식이 아니라 구획이다 — 길과 내용이 같은 색이면
       화면이 한 덩어리로 보이고 메뉴가 "위에 있는 글자"가 된다. */
    QWidget#TopBar {{
        background: {NAVBAR_BG};
    }}
    QLabel#AppName {{
        font-size: {round(body * 1.05)}px;
        font-weight: 700;
        color: {NAVBAR_TEXT};
    }}
    QFrame#TopDivider {{ background: {NAVBAR_LINE}; border: none; }}
    /* 메뉴 한 칸. 고른 것은 글자를 희게 세우고 판을 한 단계 밝혀 둔다 —
       색 하나가 아니라 무게와 바탕이 함께 움직여야 눈이 바로 찾는다. */
    QPushButton#NavTab {{
        background: transparent;
        border: none;
        border-radius: {RADIUS_SM}px;
        color: {NAVBAR_MUTED};
        font-size: {body}px;
        padding: {SP_SM}px {SP_MD}px;
    }}
    QPushButton#NavTab:hover {{ background: {NAVBAR_HOVER}; color: {NAVBAR_TEXT}; }}
    QPushButton#NavTab:checked {{
        background: {NAVBAR_ACTIVE};
        color: {NAVBAR_TEXT};
        font-weight: 700;
    }}
    QPushButton#NavTab:disabled {{ color: {NAVBAR_DISABLED}; }}
    QPushButton#TopLink {{
        background: transparent;
        border: none;
        color: {NAVBAR_MUTED};
        font-size: {small}px;
        padding: {SP_XS}px {SP_SM}px;
    }}
    QPushButton#TopLink:hover {{ color: {NAVBAR_TEXT}; }}
    /* 원본을 건드리지 않는다는 약속. 사이드바 아래에 두던 것을 띠로 옮겼다 —
       확인 문구는 늘 보여야 하고, 초록 점 하나로도 눈에 걸린다. */
    QLabel#TopPromise {{
        color: {CONFIRMED};
        font-size: {small}px;
        font-weight: 700;
    }}
    /* 인수인계 진행도. 사이드바가 없어진 자리를 대신한다. */
    QPushButton#TopHandover {{
        background: {NAVBAR_ACTIVE};
        border: none;
        border-radius: 10px;
        color: {NAVBAR_TEXT};
        font-size: {small}px;
        padding: {SP_XS}px {SP_MD}px;
    }}
    QPushButton#TopHandover:hover {{ background: {NAVBAR_HOVER}; }}
    QLabel#StatusText {{
        color: {NAVBAR_MUTED};
        font-size: {small}px;
    }}
    QProgressBar#TopProgress {{
        background: {NAVBAR_ACTIVE};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar#TopProgress::chunk {{
        background: {PRIMARY};
        border-radius: 3px;
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

    /* ── 근거 서랍 ──
       본문에서 떼어 낸 판이라는 것이 보여야 한다. 왼쪽 테두리로 경계를
       긋고 바탕을 한 단계 낮춘다. */
    QFrame#EvidenceDrawer {{
        background: {SURFACE};
        border-left: 1px solid {BORDER_STRONG};
    }}
    /* 근거 한 건. [n]을 누르면 그 항목만 짚어 준다 — 패널에 근거가
       여럿일 때 눈으로 찾는 수고를 없앤다. */
    QFrame#EvidenceEntry {{
        border: 1px solid transparent;
        border-radius: {RADIUS_SM}px;
    }}
    QFrame#EvidenceEntry[picked="true"] {{
        background: {PRIMARY_SOFT};
        border: 1px solid {PRIMARY};
    }}

    /* 질문 가이드 칩. 누르면 그 질문이 입력창에 들어간다. */
    QPushButton#Chip {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: 14px;
        padding: {SP_XS}px {SP_MD}px;
        color: {TEXT_MUTED};
        text-align: left;
    }}
    QPushButton#Chip:hover {{
        border-color: {PRIMARY};
        color: {PRIMARY};
        background: {PRIMARY_SOFT};
    }}
    /* 칩 글자는 버튼 텍스트가 아니라 안에 넣은 라벨이다 — 그래야 줄바꿈이
       되어 질문이 잘리지 않는다. 색은 버튼을 따라가야 한다. */
    QLabel#ChipText {{ color: {TEXT_MUTED}; font-size: {small}px; }}
    QPushButton#Chip:hover QLabel#ChipText {{ color: {PRIMARY}; }}

    /* ── 질문 화면: 근거 대조 ──
       왼쪽에서 근거를 고르면 오른쪽에 그 문서의 원문이 펴진다. 고른 것과
       안 고른 것의 차이가 한눈에 보여야 "지금 무엇을 대조하고 있는가"를
       잃지 않는다. 색만으로 구분하지 않도록 테두리 두께도 함께 바꾼다. */
    QLabel#SourceMark {{
        background: {SURFACE_ALT}; color: {TEXT_MUTED};
        border-radius: {RADIUS_SM}px; font-weight: 700;
    }}
    QLabel#SourceMarkOn {{
        background: {PRIMARY}; color: {TEXT_ON_PRIMARY};
        border-radius: {RADIUS_SM}px; font-weight: 700;
    }}
    QFrame#EvidencePick {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}
    QFrame#EvidencePick:hover {{ border-color: {BORDER_STRONG}; }}
    QFrame#EvidencePick[picked="true"] {{
        border: 2px solid {PRIMARY};
        background: {PRIMARY_SOFT};
    }}
    QLabel#EvidenceName {{ color: {TEXT}; font-weight: 600; }}
    QFrame#SubPanel[picked="true"] {{ border: 2px solid {CONFIRMED}; }}

    /* 원문 칸. 답변 카드와 나란히 서는 흰 판이라 같은 테두리·모서리를 쓴다 —
       둘이 다른 물건처럼 보이면 '답과 그 근거'라는 관계가 끊긴다. */
    QWidget#ReaderPane {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}
    QWidget#PaneBody {{ background: transparent; }}
    /* 문서 화면 오른쪽 상세. 표와 나란히 서는 흰 판이라 같은 테두리를 쓴다. */
    QScrollArea#DetailPane {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}
    QLabel#ParaNumber {{ color: {TEXT_DISABLED}; font-size: {small}px; }}
    QLabel#ParaText {{ color: {TEXT_MUTED}; line-height: 180%; }}
    /* 인용된 대목. 답에서 이 문장을 가져왔다는 뜻이라 본문 색으로 세우고
       바탕을 칠한다 — 원문에서 눈이 이 대목을 바로 찾는 것이 이 칸의 목적이다. */
    QLabel#ParaCited {{
        color: {TEXT};
        background: {PRIMARY_SOFT};
        border-radius: {RADIUS_SM}px;
        padding: 2px 6px;
        line-height: 180%;
    }}
    QPushButton#Fold {{
        background: transparent;
        border: 1px dashed {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        color: {TEXT_SUBTLE};
        font-size: {small}px;
        padding: {SP_XS}px {SP_SM}px;
    }}
    QPushButton#Fold:hover {{ color: {PRIMARY}; border-color: {PRIMARY}; }}

    /* 답변 카드 — 이 화면의 주인공이라 테두리를 한 단계 세운다. */
    QFrame#AnswerCard {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
    }}
    /* 답이 이 화면에서 가장 큰 글자다. 본문·칩·인용문이 다 같은 크기면
       가장 중요한 것이 시각적으로 가장 크지 않다 — 눈이 어디부터 읽을지
       스스로 정해야 한다. 보조 정보는 small로 물러선다. */
    QLabel#AnswerBody {{
        color: {TEXT};
        font-size: {section}px;
        line-height: 175%;
    }}

    /* 생성 중 표시. 가느다란 막대 하나면 충분하다 — 회전하는 그림이나
       말풍선은 이 제품의 성격에 맞지 않는다. */
    QProgressBar#Thinking {{
        background: {SURFACE_ALT};
        border: none;
        border-radius: 2px;
        height: 3px;
        text-align: center;
    }}
    QProgressBar#Thinking::chunk {{
        background: {PRIMARY};
        border-radius: 2px;
    }}

    /* 인용문은 '이 앱이 쓴 말'이 아니라 '문서에 있던 말'이다 — 따옴표만으로는
       약해서 옅은 판에 얹고 왼쪽에 띠를 둔다. */
    QLabel#EvidenceQuote {{
        background: {BG};
        border-left: 3px solid {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
        color: {TEXT};
    }}

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
        font-weight: 700;
    }}
    /* When·How 같은 개념 이름은 꼬리표로 남기고 큰 글자는 우리말 질문으로. */
    QLabel#SectionTag {{
        background: {PRIMARY_SOFT};
        color: {PRIMARY};
        border-radius: {RADIUS_SM}px;
        padding: 2px {SP_SM}px;
        font-size: {small}px;
        font-weight: 700;
    }}
    /* 화면이 내놓는 답 한 줄. 근거·부연과 같은 크기로 두면 답이 묻힌다. */
    QLabel#Answer {{ font-size: {section}px; font-weight: 700; color: {TEXT}; }}
    QLabel#Muted {{ color: {TEXT_MUTED}; }}
    QLabel#Small {{ color: {TEXT_MUTED}; font-size: {small}px; }}
    QLabel#Mono {{ font-family: "Consolas", "D2Coding", monospace; color: {TEXT_MUTED}; }}

    /* 덩어리 하나에 붙이는 작은 머리말("이렇게 판단한 근거"). 제목만큼
       크면 구획이 또 갈라지고, 본문과 같으면 머리말인 줄 모른다. */
    QLabel#BlockLabel {{
        color: {TEXT_MUTED};
        font-size: {small}px;
        font-weight: 700;
        padding-top: {SP_XS}px;
    }}
    /* 규모·시점처럼 "몇 건, 몇 년치"를 말하는 중립 조각. */
    QLabel#MetaChip {{
        background: {SURFACE_ALT};
        color: {TEXT_MUTED};
        border-radius: {RADIUS_SM}px;
        padding: 2px {SP_SM}px;
        font-size: {small}px;
        font-weight: 600;
    }}
    /* 조작 구역. 본문에 그냥 섞어 두면 버튼이 내용처럼 보인다. */
    QFrame#ActionBar {{
        background: transparent;
        border: none;
        border-top: 1px solid {BORDER};
    }}

    /* 설명 문구는 내용이 아니다. 같은 회색 글씨로 흘려 두면 목록 항목과
       섞여 읽히므로, 옅은 판에 얹어 "이건 안내"라고 표시한다. */
    QLabel#PageNote {{
        background: {SURFACE_ALT};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
        color: {TEXT_MUTED};
        font-size: {small}px;
    }}

    /* ── 접어 둔 설명 ──
       ⓘ는 눈에 걸리되 내용을 가리지 않아야 한다. 평소엔 옅은 회색 원,
       마우스를 올리면 강조색으로 켜져 "이건 눌러/올려 볼 것"임을 말한다. */
    QLabel#InfoDot {{
        background: {SURFACE_ALT};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_STRONG};
        border-radius: 8px;
        font-size: {small}px;
        font-weight: 700;
        font-style: italic;
    }}
    QLabel#InfoDot:hover {{
        background: {PRIMARY_SOFT};
        border-color: {PRIMARY};
        color: {PRIMARY};
    }}
    /* 팝업 레이어. 기본 툴팁은 얇은 시스템 상자라 본문 위에 떠도 읽히지
       않는다. 카드와 같은 언어(흰 판·테두리·여백)로 그린다. */
    QToolTip {{
        background: {BG};
        color: {TEXT};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        padding: {SP_SM}px {SP_MD}px;
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
    /* 설정 창의 카드는 목록이 아니라 설명 판이다. 마우스만 스쳐도 테두리가
       파래지면 "눌러야 하나?" 싶어진다 — 조용히 둔다. */
    QFrame#CardStatic {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
    }}

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
    QLabel#GridName {{ color: {TEXT_MUTED}; font-size: {small}px; font-weight: 600; }}

    /* ── 업무 화면 조각 ── */
    QPushButton#BackLink {{
        background: transparent;
        border: none;
        padding: 0;
        color: {TEXT_MUTED};
        font-weight: 600;
        text-align: left;
    }}
    QPushButton#BackLink:hover {{ color: {PRIMARY}; text-decoration: underline; }}
    /* 글자로 이미 ▾를 쓰고 있다. Qt가 그리는 화살표까지 나오면 둘이 된다. */
    QPushButton#MenuButton::menu-indicator {{ image: none; width: 0; }}

    /* 읽는 순서. 번호가 흐리면 순서가 아니라 장식이 된다. */
    QLabel#RankChip {{
        background: {PRIMARY};
        color: {TEXT_ON_PRIMARY};
        border-radius: {RADIUS_SM}px;
        font-weight: 700;
    }}
    QLabel#StepMark {{
        color: {PRIMARY};
        font-weight: 700;
        font-size: {section}px;
    }}
    QLabel#StepWhen {{
        background: {SURFACE_ALT};
        color: {TEXT_MUTED};
        border-radius: {RADIUS_SM}px;
        padding: 2px {SP_XS}px;
        font-size: {small}px;
    }}
    QLabel#StepLabel {{ font-weight: 600; }}
    QLabel#StepLabelInferred {{ color: {TEXT_MUTED}; font-style: italic; }}
    /* 질문 화면의 흐름 카드에서 '지금 보는 단계'. 색과 무게를 함께 올린다 —
       색만으로 구분하지 않는다는 규칙(PRD §18.4)은 여기에도 적용된다. */
    QLabel#StepLabelHere {{ color: {PRIMARY}; font-weight: 700; }}
    /* 연도 비교에서 달라진 칸. 기호(＋ － ↕)가 이미 변화를 말하므로 여기서는
       무게만 올린다 — 색을 하나 더 늘리면 상태 어휘가 흐려진다. */
    QLabel#CompareChanged {{ color: {PRIMARY}; font-weight: 700; }}
    QLabel#YearChip {{
        background: {SURFACE_ALT};
        color: {TEXT_MUTED};
        border-radius: {RADIUS_SM}px;
        padding: 2px {SP_SM}px;
        font-size: {small}px;
        font-weight: 600;
    }}
    QLabel#PrimaryMark {{ color: {ATTENTION}; font-weight: 700; }}
    QLabel#LeftoverHead {{ color: {ATTENTION}; font-weight: 700; }}
    /* '지금 먼저 확인할 것'의 한 줄. 본문보다 조금 굵게만 — 여기서 색까지
       쓰면 경고처럼 보인다. 이건 경고가 아니라 다음에 할 일이다. */
    QLabel#StartHereHead {{ color: {TEXT}; font-weight: 600; }}
    QLabel#StageDone {{ color: {CONFIRMED}; font-weight: 600; }}

    /* 줄 끝에 붙는 작은 조작. 글자만 두면 눌리는 것인지 알 수 없다. */
    QPushButton#IconButton {{
        background: {BG};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 2px 0;
        color: {TEXT_MUTED};
    }}
    QPushButton#IconButton:hover {{
        background: {PRIMARY_SOFT};
        border-color: {PRIMARY};
        color: {PRIMARY};
    }}
    QPushButton#IconButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
    /* 화살표가 글자를 밀어내면 ⋯이 사라진다 — 무엇을 누르는지 알 수 없다. */
    QPushButton#IconButton::menu-indicator {{ image: none; width: 0; }}

    QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}

    /* 버튼은 입력칸과 같은 모서리를 쓴다. 한 줄에 나란히 설 때 모서리가
       다르면 두 부품을 붙여 놓은 것처럼 보인다. */
    QPushButton {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
        padding: {SP_SM}px {SP_LG}px;
        color: {TEXT};
    }}
    QPushButton:hover {{ background: {SURFACE}; border-color: {TEXT_SUBTLE}; }}
    QPushButton:pressed {{ background: {SURFACE_ALT}; }}
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
    /* 지우는 버튼. 파란 버튼과 같은 모양이면 손이 먼저 가고 눈이 나중에 온다. */
    QPushButton#Destructive {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        color: {DANGER};
    }}
    QPushButton#Destructive:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}

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

    /* ── 설정 창 ──
       왼쪽은 갈래(무엇을 설정하나), 오른쪽은 그 갈래의 내용. 예전처럼 한
       기둥에 다섯 덩어리를 쌓으면 "화면 설정"과 "감사 로그"가 같은 무게로
       보여, 매일 쓰는 것과 일 년에 한 번 보는 것이 구분되지 않는다. */
    QWidget#SettingsNav {{
        background: {NAV_BG};
        border-right: 1px solid {BORDER_STRONG};
    }}
    QPushButton#SettingsNavItem {{
        background: transparent;
        border: none;
        border-left: 3px solid transparent;
        border-radius: {RADIUS_SM}px;
        padding: {SP_MD}px {SP_MD}px;
        text-align: left;
        color: {TEXT_MUTED};
        font-weight: 600;
    }}
    QPushButton#SettingsNavItem:hover {{ background: {NAV_HOVER}; color: {TEXT}; }}
    QPushButton#SettingsNavItem:checked {{
        background: {BG};
        border-left: 3px solid {PRIMARY};
        color: {PRIMARY};
        font-weight: 700;
    }}
    QWidget#SettingsBody {{ background: {CANVAS}; }}
    QScrollArea#SettingsScroll {{ background: {CANVAS}; border: none; }}
    QScrollArea#SettingsScroll::viewport {{ background: {CANVAS}; }}
    QWidget#SettingsPane {{ background: {CANVAS}; }}
    QFrame#SettingsFooter {{
        background: {BG};
        border: none;
        border-top: 1px solid {BORDER};
    }}
    QLabel#SettingsHeading {{ font-size: {title}px; font-weight: 700; }}
    QLabel#SettingsLead {{ color: {TEXT_MUTED}; font-size: {small}px; }}
    /* 설정 항목 하나의 이름. 카드 제목(SectionTitle)보다 한 단계 작다 —
       카드 안에 항목이 둘 이상 들어가므로 위계가 한 층 더 필요하다. */
    QLabel#FieldLabel {{ font-weight: 700; }}
    QLabel#FieldHint {{ color: {TEXT_MUTED}; font-size: {small}px; }}

    /* ── 아이콘 선택 묶음 (글자 크기·테마) ──
       글로 "밝게/어둡게/시스템"이라고 늘어놓으면 셋 다 같은 회색 글자라
       고르기 전에 읽어야 한다. 그림이 먼저 뜻을 말하고 글자는 확인용으로
       아래에 작게 붙인다. 고른 칸은 강조색 테두리 + 옅은 판으로, 색을
       못 보는 사람에게도 테두리 굵기 차이로 남는다. */
    QWidget#ChoiceGroup {{ background: transparent; }}
    /* QPushButton은 제 글자를 기준으로 크기를 잡는다 — 안에 레이아웃을 넣어도
       그 사실은 변하지 않아, 최소 높이를 주지 않으면 그림과 글자가 겹친다.
       사이드바 NavItem이 같은 이유로 min-height를 갖고 있다. */
    QPushButton#ChoiceItem {{
        background: {SURFACE};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
        padding: 0;
        min-width: 88px;
        min-height: {round(72 * scale)}px;
    }}
    QPushButton#ChoiceItem:hover {{ background: {SURFACE_ALT}; border-color: {PRIMARY}; }}
    QPushButton#ChoiceItem:checked {{
        background: {PRIMARY_SOFT};
        border: 2px solid {PRIMARY};
    }}
    QLabel#ChoiceCaption {{ color: {TEXT_MUTED}; font-size: {small}px; font-weight: 600; }}
    QPushButton#ChoiceItem:checked QLabel#ChoiceCaption {{
        color: {PRIMARY};
        font-weight: 700;
    }}

    /* ── 입력 컨트롤 ──
       입력칸·드롭다운·버튼이 한 줄에 서면 높이와 모서리가 같아야 한 벌로
       보인다. 제각각이면 그 줄이 "붙여 놓은 부품"처럼 읽힌다. */
    QLineEdit, QComboBox {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS}px;
        padding: 0 {SP_MD}px;
        min-height: {CONTROL_H - 2}px;
        color: {TEXT};
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {TEXT};
    }}
    QLineEdit:hover, QComboBox:hover {{ border-color: {TEXT_SUBTLE}; }}
    /* 포커스는 테두리 색만 바꾸지 않는다 — 얇은 테를 한 겹 더 둘러야
       "지금 여기에 글자가 들어간다"가 멀리서도 보인다. */
    QLineEdit:focus, QComboBox:focus {{
        border: 2px solid {PRIMARY};
        padding: 0 {SP_MD - 1}px;
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        background: {SURFACE};
        color: {TEXT_DISABLED};
        border-color: {BORDER};
    }}
    /* 화살표는 손대지 않는다. QSS로 drop-down·down-arrow를 건드리는 순간
       Qt가 플랫폼 화살표 그리기를 멈추는데, 웹에서 쓰는 border 삼각형 기법은
       Qt에서 회색 네모로 나오고 image를 비우면 화살표가 아예 사라진다.
       실제로 둘 다 그려 보고 확인했다 — 기본 화살표가 가장 낫다. */
    QComboBox QAbstractItemView {{
        background: {BG};
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
        padding: {SP_XS}px;
        outline: none;
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {TEXT};
    }}
    QComboBox QAbstractItemView::item {{
        min-height: {ROW_H - 6}px;
        padding: 0 {SP_SM}px;
        border-radius: {RADIUS_SM}px;
    }}
    QCheckBox {{ spacing: {SP_SM}px; }}

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
    /* 본문 기둥은 판이 아니라 폭 제한용 그릇이다. 위의 일반 규칙
       (QScrollArea > QWidget > QWidget)이 흰색을 칠하지 못하게 막는다. */
    QWidget#PageColumn {{ background: transparent; }}
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
    QLabel#BadgeAttention {{ background: {ATTENTION_SOFT}; color: {ATTENTION}; }}
    QLabel#BadgeDanger    {{ background: {DANGER_SOFT}; color: {DANGER}; }}
    QLabel#BadgeOk        {{ background: {CONFIRMED_SOFT}; color: {CONFIRMED}; }}

    /* ── 확인되지 않은 구간 (UnknownBlock) ── */
    QFrame#UnknownBlock {{
        background: {SURFACE};
        border: 1px dashed {BORDER_STRONG};
        border-radius: {RADIUS_SM}px;
    }}
    QLabel#UnknownText {{ color: {ATTENTION}; font-size: {small}px; }}
    """
