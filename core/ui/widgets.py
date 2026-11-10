# -*- coding: utf-8 -*-
"""
WindLab – standardowe elementy okien modułów.

Typowe użycie w dialogu modułu (po zbudowaniu własnego layoutu):

    style_module_dialog(self, "aep", "Produktywność farmy (AEP)",
                        "NEWA time-series · microscale")
    set_role(self.btn_run, "primary")
    set_role(self.btn_stop, "danger")
"""
from __future__ import annotations

import os
import sys

from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtGui import QFont, QIcon
from qgis.PyQt.QtWidgets import (
    QApplication, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTableView, QTextEdit, QVBoxLayout, QWidget,
)

from .theme import (
    C_INK, C_MUTED, FONT_FAMILY, accent_for, module_qss, svg_pixmap,
)
import contextlib


def set_role(widget: QWidget, role: str) -> QWidget:
    """Nadaj rolę (primary / danger / browse / hint / log) – wygląd z module_qss()."""
    widget.setProperty("role", role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def browse_button(tooltip: str = "Wybierz…") -> QPushButton:
    btn = QPushButton("…")
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return set_role(btn, "browse")


def layer_icon(layer) -> QIcon:
    """Ikona warstwy jak w panelu warstw QGIS (punkt / linia / poligon / raster)."""
    with contextlib.suppress(Exception):
        from qgis.core import QgsIconUtils
        return QgsIconUtils.iconForLayer(layer)
    try:  # starsze QGIS (< 3.20)
        from qgis.core import QgsApplication, QgsMapLayerType, QgsWkbTypes
        if layer.type() == QgsMapLayerType.RasterLayer:
            return QgsApplication.getThemeIcon("/mIconRaster.svg")
        name = {
            QgsWkbTypes.GeometryType.PointGeometry: "/mIconPointLayer.svg",
            QgsWkbTypes.GeometryType.LineGeometry: "/mIconLineLayer.svg",
            QgsWkbTypes.GeometryType.PolygonGeometry: "/mIconPolygonLayer.svg",
        }.get(layer.geometryType(), "/mIconTableLayer.svg")
        return QgsApplication.getThemeIcon(name)
    except Exception:
        return QIcon()


def open_path(path) -> bool:
    """Otwórz wygenerowany plik (PDF, PNG) albo folder wyników w domyślnym programie systemu.

    Windows: os.startfile – QDesktopServices.openUrl potrafi zamknąć QGIS na Windows przy otwieraniu PDF.
    Inne systemy: QDesktopServices (Qt), bez uruchamiania poleceń powłoki.
    """
    if not path:
        return False
    if sys.platform == "win32":
        os.startfile(str(path))  # nosec B606 – plik/folder utworzony przez WindLab, domyślna aplikacja Windows
        return True
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def hint_label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return set_role(lbl, "hint")


class ElevationSourceCombo(QComboBox):
    """Źródło wysokości: lokalny NMT / GUGiK online – wybór wspólny dla modułów (QSettings).

    `local_widgets` – pola lokalnego NMT, wyłączane gdy wybrano GUGiK.
    """
    _KEY = "WindLab/elevation_source"

    def __init__(self, local_widgets=(), parent=None):
        super().__init__(parent)
        from ..elevation import SOURCE_LABELS, SOURCE_LOCAL
        for key, label in SOURCE_LABELS.items():
            self.addItem(label, key)
        self.setToolTip(
            "GUGiK: wysokości pobierane z usługi services.gugik.gov.pl (wymaga internetu, "
            "tylko Polska, dokładność 0,1 m) – nie trzeba pobierać arkuszy NMT.")
        self._local = list(local_widgets)
        i = self.findData(QSettings().value(self._KEY, SOURCE_LOCAL))
        self.setCurrentIndex(max(i, 0))
        from .. import demo
        if demo.DEMO:
            # wersja demo: bez pobierania arkuszy NMT – tylko GUGiK online
            from ..elevation import SOURCE_GUGIK
            self.setCurrentIndex(self.findData(SOURCE_GUGIK))
            self.setEnabled(False)
            self.setToolTip(f"Lokalny NMT {demo.NOTE} – wysokości z usługi GUGiK online (tylko Polska).")
        self.currentIndexChanged.connect(self._changed)
        self._changed()

    def source(self) -> str:
        return self.currentData()

    def is_gugik(self) -> bool:
        from ..elevation import SOURCE_GUGIK
        return self.source() == SOURCE_GUGIK

    def _changed(self, *_):
        from .. import demo
        if not demo.DEMO:  # demo nie nadpisuje wyboru zapisanego przez pełną wersję
            QSettings().setValue(self._KEY, self.source())
        for w in self._local:
            w.setEnabled(not self.is_gugik())


def log_view(min_height: int = 110, max_height: int | None = 160) -> QTextEdit:
    box = QTextEdit()
    box.setReadOnly(True)
    box.setMinimumHeight(min_height)
    if max_height:
        box.setMaximumHeight(max_height)
    return set_role(box, "log")


class ModuleHeader(QWidget):
    """Nagłówek okna modułu: ikona w kolorze kafelka + tytuł + podtytuł."""

    def __init__(self, key: str, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 4)
        row.setSpacing(12)

        accent, _dark, soft = accent_for(key)
        icon = QLabel()
        icon.setFixedSize(44, 44)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"background-color: {soft}; border-radius: 22px;")
        pm = svg_pixmap(key, 24, color_override=accent)
        if not pm.isNull():
            icon.setPixmap(pm)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(QFont(FONT_FAMILY, 14, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet(f"color: {C_INK}; font-size: 14pt; font-weight: 700;")
        col.addWidget(self.title_lbl)
        self.subtitle_lbl = QLabel(subtitle)
        self.subtitle_lbl.setStyleSheet(f"color: {C_MUTED}; font-size: 9pt;")
        self.subtitle_lbl.setVisible(bool(subtitle))
        col.addWidget(self.subtitle_lbl)
        row.addLayout(col, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_lbl.setText(text)
        self.subtitle_lbl.setVisible(bool(text))


def _wrap_in_scroll(lay, footer_items: int) -> None:
    """Przenieś elementy layoutu (poza `footer_items` ostatnimi) do QScrollArea,
    tak by stopka (log, postęp, przyciski) była zawsze widoczna."""
    n_body = lay.count() - footer_items
    if n_body <= 0:
        return
    body = QWidget()
    body_lay = QVBoxLayout(body)
    body.setObjectName("WLCard")
    body_lay.setContentsMargins(14, 6, 14, 6)
    body_lay.setSpacing(lay.spacing())
    for _ in range(n_body):
        item = lay.takeAt(0)
        if item.widget() is not None:
            body_lay.addWidget(item.widget())
        elif item.layout() is not None:
            sub = item.layout()
            sub.setParent(None)
            body_lay.addLayout(sub)
        else:
            body_lay.addItem(item)
    body_lay.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    lay.insertWidget(0, scroll, 1)


def _polish_widgets(dlg) -> None:
    """Ujednolicenie elementów, których nie da się ustawić samym QSS."""
    # obszary przewijania w modułach: bez szarego tła (QScrollArea.setWidget
    # włącza autoFillBackground) – poza kartą WLCard, która ma własne białe tło
    for area in dlg.findChildren(QScrollArea):
        inner = area.widget()
        if inner is not None and inner.objectName() != "WLCard":
            inner.setAutoFillBackground(False)
    # tabele: bez kolumny numerów wierszy (szary narożnik), wiersze naprzemienne
    for table in dlg.findChildren(QTableView):
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setHighlightSections(False)
    # sekcje (QGroupBox) – linia pod każdą sekcją poza ostatnią na danej
    # karcie / zakładce; sekcje zagnieżdżone w innych sekcjach bez linii
    siblings: dict = {}
    for g in dlg.findChildren(QGroupBox):
        parent = g.parentWidget()
        if isinstance(parent, QGroupBox):
            continue
        siblings.setdefault(id(parent), []).append(g)
    for groups in siblings.values():
        visible = [g for g in groups if not g.isHidden()] or groups
        for g in groups:
            set_role(g, "section")
        set_role(visible[-1], "section-last")


def _fit_to_screen(dlg) -> None:
    """Wysokość okna = potrzebna, ale nie więcej niż 90 % ekranu."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    hint = dlg.sizeHint()
    h = min(max(hint.height(), dlg.minimumHeight()), int(avail.height() * 0.9))
    w = min(max(hint.width(), dlg.minimumWidth()), int(avail.width() * 0.9))
    if dlg.minimumHeight() > h:
        dlg.setMinimumHeight(h)
    dlg.resize(w, h)


def style_module_dialog(dlg, key: str, title: str, subtitle: str = "",
                        footer_items: int = 0) -> ModuleHeader:
    """Nałóż wspólny wygląd na gotowy dialog modułu i dodaj nagłówek.

    Tytuł okna: "<tytuł> – <projekt>" (projekt z WINDLAB_PROJECT_NAME),
    podtytuł nagłówka: "Projekt: <projekt> · <subtitle>".
    footer_items > 0: wszystko poza tyloma ostatnimi elementami layoutu
    (np. log + pasek postępu + przyciski) trafia do przewijanego obszaru.
    Zwraca nagłówek (np. do zmiany podtytułu).
    """
    dlg.setStyleSheet(module_qss(key))
    dlg.setFont(QFont(FONT_FAMILY, 9))

    project = os.environ.get("WINDLAB_PROJECT_NAME", "").strip()
    dlg.setWindowTitle(f"{title} – {project}" if project else title)

    parts = ([f"Projekt: {project}"] if project else []) + ([subtitle] if subtitle else [])
    header = ModuleHeader(key, title, "  ·  ".join(parts))
    lay = dlg.layout()
    if lay is not None:
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        if footer_items > 0:
            _wrap_in_scroll(lay, footer_items)
        lay.insertWidget(0, header)
    dlg._windlab_header = header
    _polish_widgets(dlg)
    _fit_to_screen(dlg)
    return header
