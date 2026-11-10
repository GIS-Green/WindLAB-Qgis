# -*- coding: utf-8 -*-
"""
WindLab – motyw graficzny wspólny dla okna głównego i okien modułów.

Jedno źródło kolorów, ikon i arkuszy stylów (QSS). Moduły nie definiują
własnych kolorów przycisków – oznaczają je rolą (set_role), a wygląd
wynika z module_qss().
"""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import Qt, QByteArray
from qgis.PyQt.QtGui import QPixmap, QPainter
from qgis.PyQt.QtSvg import QSvgRenderer


# ── Paleta ────────────────────────────────────────────────────────────────────
C_INK        = "#1c1d2e"
C_MUTED      = "#8b8fa8"
C_FAINT      = "#b0b4cc"
C_PRIMARY    = "#6d5aef"
C_PRIMARY_DK = "#4f3fd0"
C_SURFACE    = "#ffffff"
C_MAIN       = "#eef0f8"
C_SIDEBAR    = "#f7f8fd"
C_LINE       = "#e4e6f0"
C_DANGER     = "#e11d48"
C_LOG_BG     = "#1c1d2e"
C_LOG_FG     = "#e4e6f0"

FONT_FAMILY = "Segoe UI"

# kolory modułów (gradient ikony) – te same na kafelkach i w nagłówkach okien
ICON_GRAD = {
    "wind":      ("#818cf8", "#6d5aef"),
    "aep":       ("#34d399", "#10b981"),
    "wake":      ("#38bdf8", "#0ea5e9"),
    "flicker":   ("#f472b6", "#ec4899"),
    "noise":     ("#fb923c", "#f97316"),
    "opt":       ("#c084fc", "#7c3aed"),
    "transport": ("#4ade80", "#22c55e"),
    "cables":    ("#f43f5e", "#e11d48"),
    "download":  ("#60a5fa", "#3b82f6"),
    "results":   ("#fbbf24", "#f59e0b"),
    "create":    ("#818cf8", "#6d5aef"),
    "open":      ("#38bdf8", "#0ea5e9"),
    "change":    ("#94a3b8", "#64748b"),
    "server":    ("#818cf8", "#6d5aef"),
}


# ── Ikony SVG ─────────────────────────────────────────────────────────────────
SVG_ICONS: dict[str, str] = {
    "wind": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M4 9.5h9.5a2.5 2.5 0 1 0-2.5-2.5" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M4 13h12.5a2.5 2.5 0 1 1-2.2 3.6" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M4 16.5h6" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
</svg>""",
    "aep": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M13 3 6 13h6l-1 8 8-11h-6l0-7Z" fill="white"/>
</svg>""",
    "wake": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M3 8c2.2-2 4.4-2 6.6 0s4.4 2 6.6 0 4.4-2 6.6 0" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M3 12.5c2.2-2 4.4-2 6.6 0s4.4 2 6.6 0 4.4-2 6.6 0" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M3 17c2.2-2 4.4-2 6.6 0s4.4 2 6.6 0 4.4-2 6.6 0" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
</svg>""",
    "flicker": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="12" cy="12" r="4" fill="white"/>
  <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M5.6 18.4l1.6-1.6M16.8 7.2l1.6-1.6"
        stroke="white" stroke-width="1.7" stroke-linecap="round"/>
</svg>""",
    "noise": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M4 10v4a1.5 1.5 0 0 0 1.5 1.5H8l5 3.5V5.5L8 9H5.5A1.5 1.5 0 0 0 4 10Z" fill="white"/>
  <path d="M16.2 9.2a4 4 0 0 1 0 5.6M18.6 7a7 7 0 0 1 0 10" stroke="white" stroke-width="1.7" stroke-linecap="round"/>
</svg>""",
    "transport": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M3 15V9.5A1.5 1.5 0 0 1 4.5 8H14v7H3Z" fill="white"/>
  <path d="M14 11h3.2L20 14.2V15h-6v-4Z" fill="white"/>
  <circle cx="7" cy="16.5" r="1.8" fill="white" fill-opacity="0.9"/>
  <circle cx="17" cy="16.5" r="1.8" fill="white" fill-opacity="0.9"/>
</svg>""",
    "download": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M7 16a5 5 0 0 1 .6-9.9 6 6 0 0 1 11.3 1.8A4 4 0 0 1 18 16" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M12 11v7m0 0-2.5-2.5M12 18l2.5-2.5" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "results": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M3.5 8.5 10 5.8l4.2 1.6H20a.8.8 0 0 1 .8.8V18a.8.8 0 0 1-.8.8H4.2A.8.8 0 0 1 3.4 18V8.5Z" fill="white"/>
  <path d="M3.5 10.2h17.3" stroke="white" stroke-opacity="0.35" stroke-width="1.4"/>
</svg>""",
    "create": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "open": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2Z" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "server": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="3" y="4" width="18" height="7" rx="2" stroke="white" stroke-width="1.9"/>
  <rect x="3" y="13" width="18" height="7" rx="2" stroke="white" stroke-width="1.9"/>
  <path d="M7 7.5h.01M7 16.5h.01" stroke="white" stroke-width="2.6" stroke-linecap="round"/>
</svg>""",
    "change": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M17 1 21 5 17 9" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M3 11V9a4 4 0 0 1 4-4h14" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M7 23l-4-4 4-4" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M21 13v2a4 4 0 0 1-4 4H3" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "opt": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M4 12h4l2-6 4 12 2-6h4" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="12" cy="12" r="9" stroke="white" stroke-width="1.5" stroke-opacity="0.35"/>
</svg>""",
    "cables": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="5" cy="7" r="2.2" fill="white"/>
  <circle cx="19" cy="7" r="2.2" fill="white"/>
  <circle cx="12" cy="17" r="2.2" fill="white"/>
  <path d="M7 7.5h10M6.2 8.8l4.5 6.4M17.8 8.8l-4.5 6.4" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
</svg>""",
}


def svg_pixmap(key: str, size: int, color_override: str | None = None) -> QPixmap:
    svg_str = SVG_ICONS.get(key, "")
    if not svg_str:
        return QPixmap()
    if color_override:
        svg_str = (svg_str
                   .replace('stroke="white"', f'stroke="{color_override}"')
                   .replace('fill="white"', f'fill="{color_override}"'))
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def icon_grad_qss(c1: str, c2: str, radius: int = 18) -> str:
    return (
        f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {c1},stop:1 {c2}); border-radius: {radius}px;"
    )


# ── Strzałki do pól (QSS wymaga plików graficznych) ──────────────────────────
_ICON_DIR = Path(__file__).resolve().parent / "icons"


def _icon_url(name: str) -> str:
    return (_ICON_DIR / name).as_posix()


# ── Kolor akcentu modułu ─────────────────────────────────────────────────────
def _mix(hex_a: str, hex_b: str, t: float) -> str:
    """Kolor pośredni: t=0 → a, t=1 → b."""
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def accent_for(key: str) -> tuple[str, str, str]:
    """(akcent, akcent ciemny, akcent jasny) – z koloru modułu na kafelku.

    Akcent jest lekko przyciemniony, by biały tekst na przycisku był czytelny.
    """
    base = ICON_GRAD.get(key, (C_PRIMARY, C_PRIMARY))[1]
    accent = _mix(base, "#000000", 0.18)
    return accent, _mix(base, "#000000", 0.32), _mix(base, "#ffffff", 0.86)


# ── Arkusz stylów okna modułu ─────────────────────────────────────────────────
C_BORDER = "#d5d9e2"   # obwódki pól
C_HEAD   = "#f5f6f8"   # nagłówek tabeli, pola nieaktywne
C_ZEBRA  = "#fafbfc"   # wiersze naprzemienne


def module_qss(key: str = "") -> str:
    """QSS nakładany na każde okno modułu (QDialog) – dziedziczą go wszystkie
    elementy okna, także QMessageBox otwierane z tym oknem jako rodzicem.
    Kolor akcentu (zaznaczenia, przycisk główny, postęp) = kolor modułu."""
    accent, accent_dk, accent_soft = accent_for(key)
    down = _icon_url("chevron-down.svg")
    up = _icon_url("chevron-up.svg")
    check = _icon_url("check.svg")
    dot = _icon_url("dot.svg")
    return f"""
    QDialog {{ background-color: {C_SURFACE}; color: {C_INK}; }}
    QDialog QWidget {{ font-size: 9pt; }}
    QLabel {{ background: transparent; color: {C_INK}; }}
    QLabel[role="hint"] {{ color: {C_MUTED}; font-size: 8pt; }}

    /* ── Karta z sekcjami ───────────────────────────────────── */
    QWidget#WLCard {{
        background-color: {C_SURFACE};
        border: 1px solid {C_LINE};
        border-radius: 12px;
    }}
    QGroupBox {{
        background: transparent;
        border: none;
        margin-top: 22px;
        padding: 4px 0px 8px 0px;
        font-weight: 700;
        font-size: 10pt;
    }}
    QGroupBox[role="section"] {{ border-bottom: 1px solid {C_LINE}; padding-bottom: 12px; }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 0px;
        padding: 0px;
        color: {C_INK};
        background: transparent;
    }}
    QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{
        font-weight: 400; font-size: 9pt;
    }}

    /* ── Pola ───────────────────────────────────────────────── */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{
        background-color: {C_SURFACE};
        color: {C_INK};
        border: 1px solid {C_BORDER};
        border-radius: 7px;
        padding: 3px 8px;
        min-height: 18px;
        selection-background-color: {accent};
        selection-color: #ffffff;
        font-weight: 400;
        font-size: 9pt;
    }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {C_MUTED}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QDateEdit:focus, QDateTimeEdit:focus {{ border: 1px solid {accent}; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {C_FAINT}; background-color: {C_HEAD}; border-color: {C_LINE};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{
        background-color: {C_SURFACE};
        color: {C_INK};
        border: 1px solid {C_BORDER};
        selection-background-color: {accent_soft};
        selection-color: {C_INK};
        padding: 2px;
    }}
    QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{ padding-right: 22px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button, QDateTimeEdit::up-button {{
        subcontrol-origin: border; subcontrol-position: top right;
        width: 20px; border: none; border-left: 1px solid {C_LINE}; background: transparent;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button, QDateTimeEdit::down-button {{
        subcontrol-origin: border; subcontrol-position: bottom right;
        width: 20px; border: none; border-left: 1px solid {C_LINE}; background: transparent;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow, QDateTimeEdit::up-arrow {{
        image: url("{up}"); width: 9px; height: 9px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{
        image: url("{down}"); width: 9px; height: 9px;
    }}

    /* ── Pola wyboru ────────────────────────────────────────── */
    QCheckBox, QRadioButton {{ background: transparent; color: {C_INK}; spacing: 7px; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {C_FAINT}; }}
    QCheckBox::indicator, QRadioButton::indicator, QTableView::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {C_BORDER};
        background-color: {C_SURFACE};
    }}
    QCheckBox::indicator, QTableView::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{
        background-color: {accent}; border-color: {accent}; image: url("{check}");
    }}
    QRadioButton::indicator:checked {{
        background-color: {accent}; border-color: {accent}; image: url("{dot}");
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        background-color: {C_HEAD}; border-color: {C_LINE};
    }}
    QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
        background-color: {C_FAINT}; border-color: {C_FAINT};
    }}

    /* ── Przyciski ──────────────────────────────────────────── */
    QPushButton {{
        background-color: {C_SURFACE};
        color: {C_INK};
        border: 1px solid {C_BORDER};
        border-radius: 8px;
        padding: 6px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background-color: {C_HEAD}; border-color: {C_MUTED}; }}
    QPushButton:pressed {{ background-color: {C_LINE}; }}
    QPushButton:disabled {{ color: {C_FAINT}; border-color: {C_LINE}; background-color: {C_SURFACE}; }}

    QPushButton[role="primary"] {{
        background-color: {accent}; color: #ffffff; border: none; padding: 7px 22px;
    }}
    QPushButton[role="primary"]:hover {{ background-color: {accent_dk}; }}
    QPushButton[role="primary"]:pressed {{ background-color: {accent_dk}; }}
    QPushButton[role="primary"]:disabled {{ background-color: {C_LINE}; color: {C_FAINT}; }}

    QPushButton[role="danger"] {{ color: {C_DANGER}; border-color: #f5c2cd; }}
    QPushButton[role="danger"]:hover {{ background-color: #fff1f3; border-color: {C_DANGER}; }}

    QPushButton[role="browse"] {{ padding: 0px; min-width: 30px; max-width: 30px; }}
    QPushButton[role="compact"] {{ padding: 4px 2px; font-weight: 600; }}

    /* ── Zakładki ───────────────────────────────────────────── */
    QTabWidget::pane {{ border: none; background: transparent; top: -1px; }}
    QTabWidget[role="card"]::pane {{
        border: 1px solid {C_LINE}; border-radius: 12px; background: {C_SURFACE}; padding: 6px;
    }}
    QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
    QTabBar::tab {{
        background: transparent;
        color: {C_MUTED};
        padding: 7px 14px;
        border: none;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
    QTabBar::tab:hover:!selected {{ color: {C_INK}; }}

    /* ── Tabele ─────────────────────────────────────────────── */
    QTableWidget, QTableView, QListWidget, QTreeWidget {{
        background-color: {C_SURFACE};
        alternate-background-color: {C_ZEBRA};
        border: 1px solid {C_LINE};
        border-radius: 8px;
        gridline-color: {C_LINE};
        selection-background-color: {accent_soft};
        selection-color: {C_INK};
    }}
    QHeaderView {{ background-color: transparent; border: none; }}
    QHeaderView::section {{
        background-color: {C_HEAD};
        color: {C_INK};
        border: none;
        border-bottom: 1px solid {C_LINE};
        border-right: 1px solid {C_LINE};
        padding: 4px 6px;
        font-weight: 600;
    }}
    QHeaderView::section:last {{ border-right: none; }}
    QTableCornerButton::section {{ background-color: {C_HEAD}; border: none; }}

    /* ── Log i postęp ───────────────────────────────────────── */
    QTextEdit[role="log"], QPlainTextEdit[role="log"] {{
        background-color: {C_LOG_BG};
        color: {C_LOG_FG};
        border: none;
        border-radius: 10px;
        padding: 6px;
        font-family: "Consolas", monospace;
        font-size: 8pt;
    }}
    QProgressBar {{
        background-color: {C_LINE};
        border: none;
        border-radius: 4px;
        max-height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ border-radius: 4px; background-color: {accent}; }}

    /* ── Przewijanie ────────────────────────────────────────── */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget#WLCard {{ background-color: {C_SURFACE}; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {C_FAINT}; min-height: 28px; border-radius: 4px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {C_FAINT}; min-width: 28px; border-radius: 4px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ── Komunikaty ─────────────────────────────────────────── */
    QMessageBox {{ background-color: {C_SURFACE}; }}
    QMessageBox QLabel {{ color: {C_INK}; font-size: 9pt; }}
    """
