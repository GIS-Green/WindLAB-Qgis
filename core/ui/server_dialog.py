# -*- coding: utf-8 -*-
"""
Okno „Aktywacja WindLab”: klucz aktywacyjny z rejestracji na stronie WindLab i adres serwera
(QSettings → zmienne środowiskowe WINDLAB_SERVER_URL / WINDLAB_API_KEY dla core/remote_exec.py).
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSettings, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .. import demo
from .widgets import set_role, style_module_dialog

_KEY_URL = "WindLab/server_url"
_KEY_API = "WindLab/api_key"


def has_key() -> bool:
    return bool(str(QSettings().value(_KEY_API, "") or "").strip())


def apply_server_settings() -> None:
    """Przepisz ustawienia z QSettings do środowiska (wywoływane przy starcie WindLab)."""
    s = QSettings()
    # adres wpisany na stałe przy wdrożeniu ma pierwszeństwo; własny adres tylko na etapie testów
    url = demo.DEFAULT_SERVER_URL or str(s.value(_KEY_URL, "") or "").strip()
    os.environ["WINDLAB_SERVER_URL"] = url
    os.environ["WINDLAB_API_KEY"] = str(s.value(_KEY_API, "") or "").strip()


class ServerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        s = QSettings()
        self.ed_url = QLineEdit(str(s.value(_KEY_URL, "") or demo.DEFAULT_SERVER_URL))
        self.ed_url.setPlaceholderText("np. https://api.twojadomena.pl")
        self.ed_key = QLineEdit(str(s.value(_KEY_API, "") or ""))
        self.ed_key.setPlaceholderText("wl_…  (z e-maila po rejestracji)")
        form.addRow("Klucz aktywacyjny:", self.ed_key)
        if not demo.DEFAULT_SERVER_URL:              # po wdrożeniu adres jest stały – pole ukryte
            form.addRow("Adres serwera:", self.ed_url)
        else:
            self.ed_url.setText(demo.DEFAULT_SERVER_URL)
            self.ed_url.setVisible(False)
        lay.addLayout(form)
        reg = QLabel("Nie masz klucza? <a href='#'>Zarejestruj się na stronie WindLab</a> – "
                     "klucz przyjdzie e-mailem.")
        reg.linkActivated.connect(self._register)
        lay.addWidget(reg)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Testuj połączenie")
        self.btn_test.clicked.connect(self._test)
        self.btn_save = set_role(QPushButton("Zapisz"), "primary")
        self.btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_test)
        row.addStretch()
        row.addWidget(btn_close)
        row.addWidget(self.btn_save)
        lay.addLayout(row)
        style_module_dialog(self, "server", "Aktywacja WindLab", "klucz aktywacyjny")
        self.setMinimumWidth(520)

    def _store(self) -> None:
        s = QSettings()
        s.setValue(_KEY_URL, self.ed_url.text().strip())
        s.setValue(_KEY_API, self.ed_key.text().strip())
        apply_server_settings()

    def _test(self) -> None:
        from .. import remote_exec as rx
        self._store()
        try:
            info = rx.health()
            if not info.get("key_ok"):
                self.lbl_status.setText("✗ Serwer odpowiada, ale klucz API jest nieprawidłowy")
                return
            limit = f" · limit {info.get('max_turbines')} turbin" if info.get("demo") else ""
            self.lbl_status.setText(f"✓ Połączono z serwerem, klucz poprawny{limit}")
        except Exception as e:  # noqa: BLE001
            self.lbl_status.setText(f"✗ {e}")

    def _register(self, *_):
        url = (self.ed_url.text().strip() or demo.DEFAULT_SERVER_URL).rstrip("/")
        if url:
            QDesktopServices.openUrl(QUrl(url + "/register"))
        else:
            self.lbl_status.setText("✗ Najpierw podaj adres serwera")

    def _save(self) -> None:
        self._store()
        self.accept()
