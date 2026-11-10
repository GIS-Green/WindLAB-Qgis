# -*- coding: utf-8 -*-
"""
Wybór turbiny w oknach modułów: plik .wtg → wersja danych → tryb pracy.

    picker = TurbinePicker(kind="power", wtg_dir=WTG_DIR)
    grid.addWidget(picker, row, 0, 1, 3)
    ...
    picker.path(), picker.curve_label(), picker.model(), picker.curve()

kind:
  "power" – tryb = krzywa mocy (AEP, wake, kable),
  "noise" – tryb = krzywa hałasu (hałas),
  "file"  – tylko plik (cień: geometria wirnika).

Tryb serwerowy (core/remote_exec): lista turbin z biblioteki serwera obliczeń, ścieżka
„wtg://<plik>”, model = sam opis (pliki .wtg nie opuszczają serwera).
"""
from __future__ import annotations

import os
from typing import List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGridLayout, QLabel, QSizePolicy, QWidget,
)

from WindLab.core import remote_exec as rx
from WindLab.core.wtg import list_turbine_files, load_turbine
from WindLab.core.wtg.remote import SCHEME, is_remote_wtg
from .widgets import browse_button, hint_label


class TurbinePicker(QWidget):
    changed = pyqtSignal()

    def __init__(self, kind: str = "power", wtg_dir: str = "", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.wtg_dir = wtg_dir
        self._model = None
        self._curves: List = []

        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        g.setColumnStretch(1, 1)

        g.addWidget(QLabel("Turbina (.wtg):"), 0, 0)
        self.file_combo = QComboBox()
        self.file_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.file_combo.currentIndexChanged.connect(self._on_file)
        g.addWidget(self.file_combo, 0, 1)
        btn = browse_button("Wskaż plik .wtg")
        btn.clicked.connect(self._browse)
        g.addWidget(btn, 0, 2)
        btn.setVisible(not rx.is_remote())   # serwer: tylko turbiny z biblioteki serwera

        self.variant_combo = QComboBox()
        self.curve_combo = QComboBox()
        self.curve_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.info = hint_label()
        if kind != "file":
            g.addWidget(QLabel("Wersja danych:"), 1, 0)
            g.addWidget(self.variant_combo, 1, 1, 1, 2)
            g.addWidget(QLabel("Tryb pracy:"), 2, 0)
            g.addWidget(self.curve_combo, 2, 1, 1, 2)
            self.variant_combo.currentIndexChanged.connect(self._on_variant)
            self.curve_combo.currentIndexChanged.connect(lambda *_: self._emit())
        g.addWidget(self.info, 3, 0, 1, 3)

        self.refresh()

    # ── API ──────────────────────────────────────────────────────────────────
    def path(self) -> str:
        return self.file_combo.currentData() or ""

    def model(self):
        return self._model

    def curve_label(self) -> str:
        return self.curve_combo.currentData() or ""

    def curve(self):
        if self._model is None:
            return None
        if self.kind == "noise":
            return self._model.find_noise(self.curve_label() or None)
        return self._model.find_power(self.curve_label() or None)

    def set_path(self, path: str, curve_label: str = "") -> None:
        """Wybierz plik (dodaj do listy, jeśli spoza katalogu) i opcjonalnie tryb."""
        if not path:
            return
        if rx.is_remote() and not is_remote_wtg(path):
            # lokalna ścieżka (np. ze starszych wyników) → ta sama turbina z biblioteki serwera
            cand = SCHEME + os.path.basename(path)
            if any(self.file_combo.itemData(i) == cand for i in range(self.file_combo.count())):
                path = cand
        idx = next((i for i in range(self.file_combo.count())
                    if os.path.normcase(self.file_combo.itemData(i) or "") == os.path.normcase(path)), -1)
        if idx < 0:
            self.file_combo.insertItem(0, os.path.basename(path), path)
            idx = 0
        self.file_combo.setCurrentIndex(idx)
        if curve_label:
            self.select_curve(curve_label)

    def select_curve(self, label: str) -> None:
        if self._model is None:
            return
        curve = next((c for c in self._all_curves() if c.label == label), None)
        if curve is None:
            return
        vi = self.variant_combo.findData(curve.variant)
        if vi >= 0:
            self.variant_combo.setCurrentIndex(vi)
        ci = self.curve_combo.findData(label)
        if ci >= 0:
            self.curve_combo.setCurrentIndex(ci)

    def refresh(self) -> None:
        current = self.path()
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        if rx.is_remote():
            try:
                for t in rx.wtg_list():
                    self.file_combo.addItem(t["name"], SCHEME + t["file"])
                empty = "(brak turbin w bibliotece serwera)"
            except Exception as e:  # noqa: BLE001
                empty = f"(lista turbin niedostępna: {e})"
        else:
            for p in list_turbine_files(self.wtg_dir):
                self.file_combo.addItem(os.path.splitext(os.path.basename(p))[0], p)
            empty = "(brak plików .wtg w katalogu WTG)"
        if self.file_combo.count() == 0:
            self.file_combo.addItem(empty, "")
        self.file_combo.blockSignals(False)
        if current:
            self.set_path(current)
        else:
            self._on_file()

    # ── wewnętrzne ───────────────────────────────────────────────────────────
    def _all_curves(self):
        if self._model is None:
            return []
        return self._model.noise_curves if self.kind == "noise" else self._model.power_curves

    def _browse(self):
        start = self.wtg_dir if os.path.isdir(self.wtg_dir) else ""
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik turbiny", start, "Turbiny (*.wtg)")
        if path:
            self.set_path(path)

    def _on_file(self, *_):
        path = self.path()
        self._model = None
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        self.variant_combo.blockSignals(False)
        self.curve_combo.clear()
        if not path or not (is_remote_wtg(path) or os.path.isfile(path)):
            self.info.setText("")
            self._emit()
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._model = load_turbine(path)
        except Exception as e:
            self._model = None
            self.info.setText(f"Nie udało się odczytać turbiny: {e}")
            self._emit()
            return
        finally:
            QApplication.restoreOverrideCursor()

        tm = self._model
        txt = f"{tm.manufacturer} {tm.type_variant} · D = {tm.rotor_diameter:.0f} m · {tm.rated_kw:.0f} kW"
        if tm.hub_heights:
            txt += " · piasty: " + ", ".join(f"{h:.0f}" for h in tm.hub_heights[:8])
        self.info.setText(txt)
        if self.kind == "file":
            self._emit()
            return

        curves = self._all_curves()
        if not curves:
            what = "hałasu" if self.kind == "noise" else "mocy"
            self.info.setText(txt + f"\nPlik nie zawiera krzywych {what}.")
            self._emit()
            return
        variants = sorted({c.variant for c in curves}, reverse=True)
        default = (tm.default_noise_curve() if self.kind == "noise" else tm.default_power_curve())
        self.variant_combo.blockSignals(True)
        for v in variants:
            n = sum(1 for c in curves if c.variant == v)
            self.variant_combo.addItem(f"{v or '(bez oznaczenia)'}  [{n}]", v)
        self.variant_combo.blockSignals(False)
        vi = self.variant_combo.findData(default.variant) if default else 0
        self.variant_combo.setCurrentIndex(max(vi, 0))
        self._on_variant()
        if default is not None:
            ci = self.curve_combo.findData(default.label)
            if ci >= 0:
                self.curve_combo.setCurrentIndex(ci)

    def _on_variant(self, *_):
        variant = self.variant_combo.currentData()
        self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        for c in self._all_curves():
            if c.variant == variant:
                self.curve_combo.addItem(c.display, c.label)
        self.curve_combo.blockSignals(False)
        self._emit()

    def _emit(self):
        self.changed.emit()
