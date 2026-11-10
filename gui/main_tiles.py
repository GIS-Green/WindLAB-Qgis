# -*- coding: utf-8 -*-
"""
WindLab – GUI kafelkowe v3.
FIX białego tła kafli: nadpisanie paintEvent z QPainterPath (rounded rect).
W Qt QWidget ignoruje background-color z QSS jeśli nie ma natywnego renderowania.
Jedyna niezawodna metoda = ręczny paintEvent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QMessageBox,
    QFileDialog, QLineEdit, QComboBox,
    QScrollArea, QGraphicsDropShadowEffect,
    QApplication,
)
from qgis.PyQt.QtCore import Qt, QSize, QByteArray, QRectF
from qgis.PyQt.QtGui import (
    QFont, QColor, QCursor, QPixmap, QPalette,
    QPainter, QPainterPath, QBrush, QPen,
)
from qgis.PyQt.QtSvg import QSvgRenderer


# ── Paleta, ikony – wspólne z oknami modułów (core/ui/theme.py) ─────────────
from WindLab.core.ui.theme import (  # noqa: E402
    C_INK, C_MUTED, C_FAINT, C_PRIMARY, C_PRIMARY_DK, C_SURFACE, C_MAIN,
    C_SIDEBAR, C_LINE, ICON_GRAD,
    svg_pixmap as _svg_pixmap,
    icon_grad_qss as _icon_grad_qss,
)
from WindLab.core.ui.widgets import open_path

ANALYSIS_TILE_W = 160
ANALYSIS_TILE_H = 176
TOOL_TILE_W     = 260
TOOL_TILE_H     = 108
SIDEBAR_W       = 268
TILE_RADIUS     = 22

F_TITLE    = QFont("Segoe UI", 22, QFont.Weight.Bold)
F_SUBTITLE = QFont("Segoe UI", 11)
F_SECTION  = QFont("Segoe UI", 8,  QFont.Weight.DemiBold)
F_TILE_T   = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
F_TILE_S   = QFont("Segoe UI", 9)
F_TILE_T_B = QFont("Segoe UI", 16, QFont.Weight.Bold)
F_SIDEBAR  = QFont("Segoe UI", 11, QFont.Weight.Medium)
F_LOGO_T   = QFont("Segoe UI", 13, QFont.Weight.Bold)
F_LOGO_S   = QFont("Segoe UI", 9)
F_FOOTER   = QFont("Segoe UI", 9)


# ── Klasa bazowa kafla – rysuje własne tło przez paintEvent ──────────────────
class _RoundedCard(QWidget):
    """
    QWidget który sam maluje zaokrąglone białe tło.
    To jedyna niezawodna metoda w Qt – QSS background-color na QWidget
    jest ignorowane gdy widget nie ma natywnego handle'a (np. wewnątrz QDialog).
    """
    def __init__(self, radius: int = TILE_RADIUS,
                 bg_color: QColor | None = None, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._bg = bg_color or QColor(C_SURFACE)
        # Wyłącz tło systemowe – my rysujemy sami
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        p.fillPath(path, QBrush(self._bg))
        p.end()


# ── Kafel Analizy ─────────────────────────────────────────────────────────────
class AnalysisTile(_RoundedCard):

    def __init__(self, title: str, subtitle: str, key: str, slot, parent=None):
        super().__init__(radius=TILE_RADIUS, parent=parent)
        self.setFixedSize(ANALYSIS_TILE_W, ANALYSIS_TILE_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._slot = slot

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(28, 29, 46, 25))
        self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(0)

        c1, c2 = ICON_GRAD.get(key, ("#818cf8", "#6d5aef"))
        icon_box = QLabel()
        icon_box.setFixedSize(56, 56)
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet(_icon_grad_qss(c1, c2, 18))
        pm = _svg_pixmap(key, 28)
        if not pm.isNull():
            icon_box.setPixmap(pm)
        outer.addWidget(icon_box, 0, Qt.AlignmentFlag.AlignLeft)

        outer.addStretch()

        lbl_t = QLabel(title)
        lbl_t.setFont(F_TILE_T)
        lbl_t.setWordWrap(True)  # długie nazwy (np. „Kalkulacja produktywności”) w dwóch liniach
        lbl_t.setStyleSheet(f"color: {C_INK}; background: transparent;")
        outer.addWidget(lbl_t)

        if subtitle:
            lbl_s = QLabel(subtitle)
            lbl_s.setFont(F_TILE_S)
            lbl_s.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
            outer.addWidget(lbl_s)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._slot:
            self._slot()

    def enterEvent(self, e):
        s = self.graphicsEffect()
        if s:
            s.setBlurRadius(32); s.setOffset(0, 10)
            s.setColor(QColor(28, 29, 46, 40))
        super().enterEvent(e)

    def leaveEvent(self, e):
        s = self.graphicsEffect()
        if s:
            s.setBlurRadius(24); s.setOffset(0, 8)
            s.setColor(QColor(28, 29, 46, 25))
        super().leaveEvent(e)


# ── Kafel Narzędzia ───────────────────────────────────────────────────────────
class ToolTile(_RoundedCard):

    def __init__(self, title: str, subtitle: str, key: str, slot,
                 bold_title: bool = False, parent=None):
        super().__init__(radius=TILE_RADIUS, parent=parent)
        self.setFixedSize(TOOL_TILE_W, TOOL_TILE_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._slot = slot

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(28, 29, 46, 25))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)

        c1, c2 = ICON_GRAD.get(key, ("#818cf8", "#6d5aef"))
        icon_box = QLabel()
        icon_box.setFixedSize(56, 56)
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet(_icon_grad_qss(c1, c2, 16))
        pm = _svg_pixmap(key, 28)
        if not pm.isNull():
            icon_box.setPixmap(pm)
        layout.addWidget(icon_box)

        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        lbl_t = QLabel(title)
        lbl_t.setFont(F_TILE_T_B if bold_title else F_TILE_T)
        lbl_t.setStyleSheet(f"color: {C_INK}; background: transparent;")
        txt.addWidget(lbl_t)

        if subtitle:
            lbl_s = QLabel(subtitle)
            lbl_s.setFont(F_TILE_S)
            lbl_s.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
            txt.addWidget(lbl_s)

        layout.addLayout(txt)
        layout.addStretch()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._slot:
            self._slot()

    def enterEvent(self, e):
        s = self.graphicsEffect()
        if s:
            s.setBlurRadius(32); s.setOffset(0, 10)
            s.setColor(QColor(28, 29, 46, 40))
        super().enterEvent(e)

    def leaveEvent(self, e):
        s = self.graphicsEffect()
        if s:
            s.setBlurRadius(24); s.setOffset(0, 8)
            s.setColor(QColor(28, 29, 46, 25))
        super().leaveEvent(e)


# ── QSS helpers ───────────────────────────────────────────────────────────────
def _accent_btn_qss() -> str:
    return f"""
        QPushButton {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {C_PRIMARY},stop:1 {C_PRIMARY_DK});
            color: #fff; border: none; border-radius: 10px;
            padding: 10px 24px; font-size: 13px; font-weight: 700;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #8b7bff,stop:1 {C_PRIMARY});
        }}
        QPushButton:pressed {{ background: {C_PRIMARY_DK}; }}
    """

def _cancel_btn_qss() -> str:
    return f"""
        QPushButton {{
            background: transparent; color: {C_INK};
            border: 1.5px solid {C_LINE}; border-radius: 10px;
            padding: 10px 24px; font-size: 13px; font-weight: 600;
        }}
        QPushButton:hover {{ background: #f0f2ff; border-color: {C_PRIMARY}; }}
    """

def _msg_qss() -> str:
    return f"""
        QMessageBox {{ background-color: {C_MAIN}; color: {C_INK}; }}
        QMessageBox QLabel {{ color: {C_INK}; background: transparent; font-size: 13px; }}
        QMessageBox QPushButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 {C_PRIMARY},stop:1 {C_PRIMARY_DK});
            color: #fff; border: none; border-radius: 8px;
            padding: 7px 20px; min-width: 72px; font-weight: 600;
        }}
        QMessageBox QPushButton:hover {{ background: {C_PRIMARY}; }}
    """

def _input_qss() -> str:
    return f"""
        QLineEdit, QComboBox {{
            background-color: {C_MAIN}; color: {C_INK};
            border: 1.5px solid {C_LINE}; border-radius: 10px;
            padding: 9px 12px; font-size: 13px; min-height: 20px;
        }}
        QLineEdit:focus, QComboBox:focus {{
            border: 1.5px solid {C_PRIMARY}; background-color: {C_SURFACE};
        }}
        QComboBox::drop-down {{ border: none; width: 28px; }}
        QComboBox QAbstractItemView {{
            background-color: {C_SURFACE}; color: {C_INK};
            selection-background-color: {C_PRIMARY}; selection-color: #fff;
            border: 1px solid {C_LINE}; padding: 4px;
        }}
    """


# ── Dialog „Utwórz projekt" ───────────────────────────────────────────────────
class CreateProjectDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Utwórz projekt")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(720)
        self.result_data = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("ModalCard")
        card.setStyleSheet(f"QWidget#ModalCard {{ background-color: {C_SURFACE}; border-radius: 20px; }}")

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(60); shadow.setOffset(0, 12)
        shadow.setColor(QColor(28, 29, 46, 90))
        card.setGraphicsEffect(shadow)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(36, 32, 36, 28)
        cl.setSpacing(0)

        hrow = QHBoxLayout()
        htx = QVBoxLayout(); htx.setSpacing(4)

        t = QLabel("Utwórz projekt")
        t.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C_INK}; background: transparent;")
        htx.addWidget(t)

        s = QLabel("Plik projektu: NazwaProjektu.json · SHP → folder WTG")
        s.setFont(QFont("Segoe UI", 10))
        s.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
        htx.addWidget(s)

        hrow.addLayout(htx); hrow.addStretch()

        btn_x = QPushButton("✕")
        btn_x.setFixedSize(32, 32)
        btn_x.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_x.setStyleSheet(f"""
            QPushButton {{ background: {C_MAIN}; color: {C_MUTED}; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; }}
            QPushButton:hover {{ background: {C_LINE}; color: {C_INK}; }}
        """)
        btn_x.clicked.connect(self.reject)
        hrow.addWidget(btn_x, 0, Qt.AlignmentFlag.AlignTop)

        cl.addLayout(hrow)
        cl.addSpacing(24)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20); grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)

        def _lbl(text):
            l = QLabel(text)
            l.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            l.setStyleSheet(f"color: {C_INK}; background: transparent;")
            return l

        def _btn_browse():
            b = QPushButton("…")
            b.setFixedSize(36, 38)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet(_cancel_btn_qss())
            return b

        iq = _input_qss()

        grid.addWidget(_lbl("Nazwa projektu"), 0, 0)
        grid.addWidget(_lbl("Katalog projektu"), 0, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("np. Farma_Wiatrowa_2026")
        self.name_edit.setStyleSheet(iq)
        grid.addWidget(self.name_edit, 1, 0)

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("C:/Projekty/...")
        self.dir_edit.setStyleSheet(iq)
        dr = QHBoxLayout(); dr.setSpacing(6)
        dr.addWidget(self.dir_edit)
        b_dir = _btn_browse(); b_dir.clicked.connect(self._browse_dir)
        dr.addWidget(b_dir)
        grid.addLayout(dr, 1, 1)

        grid.addWidget(_lbl("Warstwa turbin (SHP)"), 2, 0)
        grid.addWidget(_lbl("Kolumna ID / nazwa turbiny"), 2, 1)

        self.shp_combo = QComboBox()
        self.shp_combo.setEditable(True)
        self.shp_combo.setPlaceholderText("Farma demo — 3× Vestas V172-7.2")
        self.shp_combo.setStyleSheet(iq)
        self._populate_shp_combo()
        sr = QHBoxLayout(); sr.setSpacing(6)
        sr.addWidget(self.shp_combo)
        b_shp = _btn_browse(); b_shp.clicked.connect(self._browse_shp)
        sr.addWidget(b_shp)
        grid.addLayout(sr, 3, 0)

        self.id_combo = QComboBox()
        self.id_combo.addItems(["userlabel", "name", "id", "label"])
        self.id_combo.setCurrentText("userlabel")
        self.id_combo.setStyleSheet(iq)
        grid.addWidget(self.id_combo, 3, 1)

        grid.addWidget(_lbl("Katalog wyników (Output)"), 4, 0)
        grid.addWidget(_lbl("Katalog danych NC"), 4, 1)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("(domyślnie: <projekt>/Output)")
        self.out_edit.setStyleSheet(iq)
        or_ = QHBoxLayout(); or_.setSpacing(6)
        or_.addWidget(self.out_edit)
        b_out = _btn_browse()
        b_out.clicked.connect(lambda: self._browse_folder(self.out_edit))
        or_.addWidget(b_out)
        grid.addLayout(or_, 5, 0)

        self.nc_edit = QLineEdit()
        self.nc_edit.setPlaceholderText("(domyślnie: <projekt>/Data/NC)")
        self.nc_edit.setStyleSheet(iq)
        nr = QHBoxLayout(); nr.setSpacing(6)
        nr.addWidget(self.nc_edit)
        b_nc = _btn_browse()
        b_nc.clicked.connect(lambda: self._browse_folder(self.nc_edit))
        nr.addWidget(b_nc)
        grid.addLayout(nr, 5, 1)

        grid.addWidget(_lbl("Katalog danych NMT"), 6, 0, 1, 2)
        self.nmt_edit = QLineEdit()
        self.nmt_edit.setPlaceholderText("(domyślnie: <projekt>/Data/NMT)")
        self.nmt_edit.setStyleSheet(iq)
        nmtr = QHBoxLayout(); nmtr.setSpacing(6)
        nmtr.addWidget(self.nmt_edit)
        b_nmt = _btn_browse()
        b_nmt.clicked.connect(lambda: self._browse_folder(self.nmt_edit))
        nmtr.addWidget(b_nmt)
        grid.addLayout(nmtr, 7, 0, 1, 2)

        cl.addLayout(grid)
        cl.addSpacing(18)

        preset = QLabel(
            "Preset: Farma demo — 3× Vestas V172-7.2 · kolumna userlabel. "
            "Output, NC i NMT nie mogą się pokrywać."
        )
        preset.setFont(QFont("Segoe UI", 9))
        preset.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
        preset.setWordWrap(True)
        cl.addWidget(preset)
        cl.addSpacing(22)

        brow = QHBoxLayout(); brow.addStretch()
        b_cancel = QPushButton("Anuluj")
        b_cancel.setFixedHeight(42); b_cancel.setMinimumWidth(100)
        b_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        b_cancel.setStyleSheet(_cancel_btn_qss())
        b_cancel.clicked.connect(self.reject)
        brow.addWidget(b_cancel); brow.addSpacing(10)

        b_ok = QPushButton("Utwórz projekt")
        b_ok.setFixedHeight(42); b_ok.setMinimumWidth(150)
        b_ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        b_ok.setStyleSheet(_accent_btn_qss())
        b_ok.clicked.connect(self._accept)
        brow.addWidget(b_ok)

        cl.addLayout(brow)
        outer.addWidget(card)

        self.name_edit.textChanged.connect(self._auto_paths)
        self.shp_combo.currentTextChanged.connect(self._on_shp_changed)

    def _populate_shp_combo(self):
        try:
            from qgis.core import QgsProject
            for l in QgsProject.instance().mapLayers().values():
                if hasattr(l, "geometryType"):
                    self.shp_combo.addItem(l.name())
            if self.shp_combo.count() == 0:
                self.shp_combo.addItem("Farma demo — 3× Vestas V172-7.2")
        except Exception:
            self.shp_combo.addItem("Farma demo — 3× Vestas V172-7.2")

    def _auto_paths(self, name: str):
        pass

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz katalog projektu")
        if d:
            self.dir_edit.setText(d)
            base = Path(d)
            # katalog wybrany = katalog projektu (bez zagnieżdżania nazwy)
            if not self.out_edit.text():
                self.out_edit.setText(str(base / "Output"))
            if not self.nc_edit.text():
                self.nc_edit.setText(str(base / "Data" / "NC"))
            if not self.nmt_edit.text():
                self.nmt_edit.setText(str(base / "Data" / "NMT"))

    def _browse_shp(self):
        p, _ = QFileDialog.getOpenFileName(self, "Wybierz SHP turbin", "", "Shapefile (*.shp)")
        if p:
            self.shp_combo.setCurrentText(p)

    def _browse_folder(self, line: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Wybierz katalog")
        if d:
            line.setText(d)

    def _on_shp_changed(self, text: str):
        self.id_combo.clear()
        if not text or not Path(text).is_file():
            self.id_combo.addItems(["userlabel", "name", "id", "label"])
            self.id_combo.setCurrentText("userlabel")
            return
        try:
            from WindLab.core.project import list_fields, find_id_column
            fields = list_fields(text)
            self.id_combo.addItems(fields)
            pref = find_id_column(text, "userlabel")
            if pref and pref in fields:
                self.id_combo.setCurrentText(pref)
        except Exception as e:
            print(f"[CreateProject] {e}")
            self.id_combo.addItems(["userlabel", "name", "id", "label"])

    def _accept(self):
        name   = self.name_edit.text().strip()
        root   = self.dir_edit.text().strip()
        shp    = self.shp_combo.currentText().strip()
        id_col = self.id_combo.currentText().strip()

        if not name:   return self._warn("Podaj nazwę projektu.")
        if not root:   return self._warn("Wybierz katalog projektu.")
        if not shp:    return self._warn("Wybierz warstwę turbin (SHP).")
        if not id_col: return self._warn("Wybierz kolumnę identyfikującą turbiny.")

        out = self.out_edit.text().strip() or None
        nc  = self.nc_edit.text().strip()  or None
        nmt = self.nmt_edit.text().strip() or None

        if out and nc and Path(out).resolve() == Path(nc).resolve():
            return self._warn("Output i Data/NC nie mogą być tym samym folderem.")
        if out and nmt and Path(out).resolve() == Path(nmt).resolve():
            return self._warn("Output i Data/NMT nie mogą być tym samym folderem.")

        # Katalog wybrany przez użytkownika = katalog projektu (bez podkatalogu o nazwie projektu)
        proj_root = Path(root)

        # Rozwiąż SHP: ścieżka pliku lub nazwa warstwy QGIS → source()
        shp_path = None
        if Path(shp).is_file():
            shp_path = Path(shp)
        else:
            try:
                from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
                for lyr in QgsProject.instance().mapLayers().values():
                    if lyr.name() == shp and isinstance(lyr, QgsVectorLayer):
                        src = lyr.source().split("|")[0]
                        if Path(src).is_file():
                            shp_path = Path(src)
                        else:
                            # warstwa w pamięci – zapisz tymczasowy SHP
                            import tempfile
                            tmp = Path(tempfile.mkdtemp()) / "turbines_src.shp"
                            from qgis.core import QgsVectorFileWriter
                            QgsVectorFileWriter.writeAsVectorFormat(
                                lyr, str(tmp), "UTF-8", lyr.crs(), "ESRI Shapefile")
                            if tmp.is_file():
                                shp_path = tmp
                        break
            except Exception as e:
                print(f"[CreateProject] resolve layer: {e}")
        if shp_path is None:
            return self._warn(
                "Nie znaleziono pliku SHP turbin.\n"
                "Wybierz warstwę z projektu QGIS albo wskaż plik .shp z dysku.")

        self.result_data = {
            "project_name": name, "project_root": proj_root,
            "turbine_shp":  shp_path,
            "id_column":    id_col,
            "output_dir":   Path(out)  if out  else None,
            "data_nc_dir":  Path(nc)   if nc   else None,
            "data_nmt_dir": Path(nmt)  if nmt  else None,
        }
        self.accept()

    def _warn(self, text: str):
        b = QMessageBox(self)
        b.setIcon(QMessageBox.Icon.Warning); b.setWindowTitle("Błąd")
        b.setText(text); b.setStyleSheet(_msg_qss()); b.exec()


# ── Główne okno ───────────────────────────────────────────────────────────────
class WindLabMainWindow(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        from WindLab.core.ui.server_dialog import apply_server_settings
        apply_server_settings()  # adres serwera obliczeń i klucz API → środowisko
        self.setWindowTitle("WindLab")
        self.setFixedSize(1200, 760)
        self.current_project = None

        self.setStyleSheet(f"""
            QDialog {{ background-color: {C_MAIN}; }}
            QLabel {{ background: transparent; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C_MAIN}; width: 8px; }}
            QScrollBar::handle:vertical {{
                background: {C_LINE}; min-height: 28px; border-radius: 4px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setObjectName("SidebarWidget")
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(f"""
            QWidget#SidebarWidget {{
                background-color: {C_SIDEBAR};
                border-right: 1px solid {C_LINE};
            }}
        """)

        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(20, 24, 20, 24)
        sl.setSpacing(0)

        logo_row = QHBoxLayout(); logo_row.setSpacing(12)

        logo_box = QLabel("W")
        logo_box.setFixedSize(44, 44)
        logo_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_box.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        logo_box.setStyleSheet(f"""
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {C_PRIMARY},stop:1 {C_PRIMARY_DK});
            border-radius: 14px; color: white;
        """)

        logo_txt = QVBoxLayout(); logo_txt.setSpacing(1)
        logo_name = QLabel("WindLab")
        logo_name.setFont(F_LOGO_T)
        logo_name.setStyleSheet(f"color: {C_INK}; background: transparent;")
        logo_ver = QLabel("QGIS Plugin v1.0")
        logo_ver.setFont(F_LOGO_S)
        logo_ver.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
        logo_txt.addWidget(logo_name); logo_txt.addWidget(logo_ver)

        logo_row.addWidget(logo_box); logo_row.addLayout(logo_txt); logo_row.addStretch()
        sl.addLayout(logo_row)
        sl.addSpacing(36)

        menu_kicker = QLabel("MENU")
        menu_kicker.setFont(F_SECTION)
        menu_kicker.setStyleSheet(f"color: {C_FAINT}; letter-spacing: 2px; background: transparent;")
        sl.addWidget(menu_kicker)
        sl.addSpacing(8)

        self._btn_create = self._sidebar_btn("Utwórz projekt", "create", self._on_create)
        self._btn_open   = self._sidebar_btn("Otwórz projekt",  "open",   self._on_open)
        self._btn_change = self._sidebar_btn("Zmień projekt",   "change", self._on_close_project)

        sl.addWidget(self._btn_create); sl.addSpacing(2)
        sl.addWidget(self._btn_open);   sl.addSpacing(2)
        sl.addWidget(self._btn_change)
        sl.addStretch()
        self._btn_server = self._sidebar_btn("Aktywacja WindLab", "server", self._on_server)
        sl.addWidget(self._btn_server)
        sl.addSpacing(12)

        footer = QLabel("Analizy farm wiatrowych\nAEP · wake · flicker · hałas · opt")
        footer.setFont(F_FOOTER)
        footer.setStyleSheet(f"color: {C_FAINT}; background: transparent;")
        footer.setWordWrap(True)
        sl.addWidget(footer)

        root.addWidget(sidebar)

        # ── Obszar główny ─────────────────────────────────────────────────────
        content_outer = QWidget()
        content_outer.setObjectName("ContentOuter")
        content_outer.setStyleSheet(f"QWidget#ContentOuter {{ background-color: {C_MAIN}; }}")
        cv = QVBoxLayout(content_outer)
        cv.setContentsMargins(40, 36, 40, 36)
        cv.setSpacing(0)

        hdr = QHBoxLayout()
        title_col = QVBoxLayout(); title_col.setSpacing(4)

        self.title_lbl = QLabel("WindLab")
        self.title_lbl.setFont(F_TITLE)
        self.title_lbl.setStyleSheet(f"color: {C_INK}; background: transparent;")
        title_col.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel("Wybierz akcję")
        self.subtitle_lbl.setFont(F_SUBTITLE)
        self.subtitle_lbl.setStyleSheet(f"color: {C_MUTED}; background: transparent;")
        title_col.addWidget(self.subtitle_lbl)

        hdr.addLayout(title_col); hdr.addStretch()
        cv.addLayout(hdr)
        cv.addSpacing(28)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.tiles_host = QWidget()
        self.tiles_host.setStyleSheet("background: transparent;")
        self.tiles_layout = QVBoxLayout(self.tiles_host)
        self.tiles_layout.setContentsMargins(0, 0, 0, 0)
        self.tiles_layout.setSpacing(0)
        self.tiles_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self.tiles_host)
        cv.addWidget(scroll, 1)
        root.addWidget(content_outer, 1)

        self._build_start_view()

    def _on_server(self):
        from WindLab.core.ui.server_dialog import ServerDialog
        ServerDialog(self).exec()

    def showEvent(self, e):
        super().showEvent(e)
        # pierwsze uruchomienie bez klucza → okno aktywacji
        from WindLab.core.ui.server_dialog import has_key
        if not getattr(self, "_activation_asked", False) and not has_key():
            self._activation_asked = True
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._on_server)

    # ── Sidebar button ────────────────────────────────────────────────────────
    def _sidebar_btn(self, text: str, key: str, slot) -> QPushButton:
        btn = QPushButton()
        btn.setFixedHeight(44)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 14px; text-align: left; padding: 0 12px;
            }}
            QPushButton:hover {{ background-color: #eeebfd; }}
            QPushButton:pressed {{ background-color: #e0dbf8; }}
        """)

        layout = QHBoxLayout(btn)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(20, 20)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        pm = _svg_pixmap(key, 20, color_override=C_PRIMARY)
        if not pm.isNull():
            icon_lbl.setPixmap(pm)

        txt_lbl = QLabel(text)
        txt_lbl.setFont(F_SIDEBAR)
        txt_lbl.setStyleSheet(f"color: {C_INK}; background: transparent; border: none;")

        layout.addWidget(icon_lbl); layout.addWidget(txt_lbl); layout.addStretch()
        btn.clicked.connect(slot)
        return btn

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _clear_tiles(self):
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None); w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None); w.deleteLater()

    def _section_kicker(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(F_SECTION)
        lbl.setStyleSheet(f"color: {C_FAINT}; letter-spacing: 2px; background: transparent;")
        return lbl

    def _analysis_row(self, tiles_data) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(12); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for (title, sub, key, slot) in tiles_data:
            row.addWidget(AnalysisTile(title, sub, key, slot))
        row.addStretch()
        return row

    def _tools_row(self, tiles_data) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(12); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for item in tiles_data:
            title, sub, key, slot = item[:4]
            bold = len(item) > 4 and item[4]
            row.addWidget(ToolTile(title, sub, key, slot, bold_title=bold))
        row.addStretch()
        return row

    # ── Widoki ────────────────────────────────────────────────────────────────
    def _build_start_view(self):
        self._clear_tiles()
        self.title_lbl.setText("WindLab")
        self.subtitle_lbl.setText("Utwórz lub otwórz projekt, aby rozpocząć")
        self.subtitle_lbl.show()

        self.tiles_layout.addWidget(self._section_kicker("PROJEKT"))
        self.tiles_layout.addSpacing(12)
        self.tiles_layout.addLayout(self._tools_row([
            ("Utwórz projekt", "Nowy projekt WindLab", "create", self._on_create),
            ("Otwórz projekt",  "Wczytaj istniejący",   "open",   self._on_open),
        ]))
        self.tiles_layout.addStretch()

    def _build_project_view(self):
        self._clear_tiles()
        proj = self.current_project
        name = proj.name if proj else "?"

        self.title_lbl.setText(name)
        self.subtitle_lbl.hide()  # FIX: brak "1 turbiny · Vestas..."

        self.tiles_layout.addWidget(self._section_kicker("ANALIZA"))
        self.tiles_layout.addSpacing(12)
        self.tiles_layout.addLayout(self._analysis_row([
            ("Dane wiatrowe", "NC · Rose",     "wind",    self._on_wind_data),
            ("Kalkulacja produktywności", "AEP", "aep", self._on_aep),
            ("Analiza wake",  "Turbulencje",    "wake",    self._on_wake),
            ("Analiza cienia", "Shadow flicker", "flicker", self._on_shadow),
            ("Hałas",         "ISO 9613",       "noise",   self._on_noise),
        ]))
        self.tiles_layout.addSpacing(12)
        self.tiles_layout.addLayout(self._analysis_row([
            ("Optymalizacja", "Straty AEP · cień+hałas", "opt",    self._on_optimization),
            ("Trasy kablowe", "Optymalizacja tras",     "cables", self._on_cables),
        ]))
        self.tiles_layout.addSpacing(32)
        self.tiles_layout.addWidget(self._section_kicker("NARZĘDZIA"))
        self.tiles_layout.addSpacing(12)
        self.tiles_layout.addLayout(self._tools_row([
            ("Transport",    "Swept path",    "transport", self._on_transport),
            ("Pobierz dane", "NMT · NC",      "download",  self._on_download),
            ("Wyniki",       "Folder Output", "results",   self._on_results, True),
        ]))
        self.tiles_layout.addStretch()

    # ── Akcje ─────────────────────────────────────────────────────────────────
    def _msg(self, title: str, text: str, icon=QMessageBox.Icon.Information):
        b = QMessageBox(self); b.setIcon(icon); b.setWindowTitle(title)
        b.setText(text); b.setStyleSheet(_msg_qss()); b.exec()

    def _on_create(self):
        dlg = CreateProjectDialog(self)
        dlg.adjustSize()
        dlg.move(self.x() + (self.width() - dlg.width()) // 2,
                 self.y() + (self.height() - dlg.height()) // 2)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_data:
            return
        d = dlg.result_data
        try:
            from WindLab.core.project import create_project
            proj = create_project(**{k: d[k] for k in d})
            self.current_project = proj
            self._msg("Projekt utworzony",
                      f"Projekt '{proj.name}' został zapisany.\n"
                      f"Plik: {proj.project_file.name}\nKatalog: {proj.project_dir}")
            self._build_project_view()
            box = QMessageBox(self); box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Pobieranie danych")
            box.setText("Czy pobrać teraz dane NC oraz NMT?")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.Yes); box.setStyleSheet(_msg_qss())
            if box.exec() == QMessageBox.StandardButton.Yes:
                self._on_download()
        except Exception as e:
            self._msg("Błąd tworzenia projektu", str(e), QMessageBox.Icon.Critical)

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Otwórz projekt WindLab", "", "Projekt WindLab (*.json);;Wszystkie pliki (*)")
        if not path:
            d = QFileDialog.getExistingDirectory(self, "Wybierz katalog projektu")
            if not d: return
            path = d
        try:
            from WindLab.core.project import open_project
            self.current_project = open_project(Path(path))
            self._build_project_view()
        except Exception as e:
            self._msg("Błąd otwierania projektu", str(e), QMessageBox.Icon.Critical)

    def _on_close_project(self):
        self.current_project = None
        self._build_start_view()

    def _require_project(self) -> bool:
        if self.current_project is None:
            self._msg("Brak projektu", "Najpierw utwórz lub otwórz projekt.", QMessageBox.Icon.Warning)
            return False
        return True

    def _on_wind_data(self):
        if self._require_project(): self._launch("wind_analysis")
    def _on_aep(self):
        if self._require_project(): self._launch("aep")
    def _on_wake(self):
        if self._require_project(): self._launch("wake")
    def _on_shadow(self):
        if self._require_project(): self._launch("shadow")
    def _on_noise(self):
        if self._require_project(): self._launch("noise")
    def _on_optimization(self):
        if self._require_project(): self._launch("optimization")
    def _on_transport(self):
        if self._require_project(): self._launch("transport")
    def _on_cables(self):
        if self._require_project(): self._launch("cables")
    def _on_download(self):
        if self._require_project(): self._launch("download")

    def _on_results(self):
        if not self._require_project(): return
        output_dir = self.current_project.output_dir
        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self._msg("Błąd", f"Nie można utworzyć folderu Output:\n{e}", QMessageBox.Icon.Critical)
                return
        try:
            open_path(str(output_dir))
        except Exception as e:
            self._msg("Błąd", f"Nie można otworzyć folderu:\n{output_dir}\n\n{e}", QMessageBox.Icon.Critical)

    def _launch(self, name: str):
        from WindLab.gui.launchers import launch
        launch(name, self, self.current_project)


# ── Punkt wejścia ─────────────────────────────────────────────────────────────
def main():
    here = Path(__file__).resolve().parent.parent
    root = here.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    win = WindLabMainWindow()
    win.show()
    return win


if __name__ == "__main__":
    main()
