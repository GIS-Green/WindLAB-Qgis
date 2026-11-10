# -*- coding: utf-8 -*-
"""
Okno modułu kabli SN (QGIS) – analogiczne do okna AEP.

Zakładka 1: MST (las rozpinający) z opcjonalnymi strefami no-go i wieloma GPO.
Zakładka 2: optymalizacja topologii i przekrojów kabli, straty, spadek napięcia, raport PDF.

Uruchomienie z konsoli Pythona QGIS lub akcji wtyczki:
    from Cable.gui.cable_dialog import main; main()

Katalog wyników i nazwa projektu są takie jak w module AEP (projekt wczytany w QGIS) – nie ma ich w oknie.
Obliczenia idą do zewnętrznego Pythona (ten sam co AEP lub zmienna CABLE_PYTHON) – wymagane moduły:
numpy, scipy, shapely>=2.0, matplotlib, reportlab, openpyxl.
"""

from __future__ import annotations

import json
import os
import re
from WindLab.core import remote_exec as rx
import sys
import tempfile
from datetime import datetime

from qgis.PyQt.QtCore import QObject, Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject, QgsVectorLayer, QgsWkbTypes,
)

from WindLab.core.paths import report_filename
from WindLab.core.ui import TurbinePicker, browse_button, hint_label, layer_icon, set_role, style_module_dialog
from WindLab.core.ui.widgets import open_path
import contextlib

# ── Ścieżki względem pakietu ──────────────────────────────────────────────────
_CABLE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_WORKERS = os.path.join(_CABLE_ROOT, "workers")
WORKER_MST = os.path.join(_WORKERS, "worker_cable_mst.py")
WORKER_OPT = os.path.join(_WORKERS, "worker_cable_opt.py")
WORKER_REPORT = os.path.join(_WORKERS, "worker_cable_report.py")

# Domyślne ceny i parametry kabli (zapis strony windfarmoptimizer.com); ta sama lista co w cable_common.py
DEFAULT_CABLES = [
    {"type": "3x1x95 mm2", "section": 95, "price_eur_m_core": 4.5, "r_dc": 0.32, "r_ac": 0.411, "x": 0.15, "i_max": 231.0, "active": True},
    {"type": "3x1x150 mm2", "section": 150, "price_eur_m_core": 5.2, "r_dc": 0.206, "r_ac": 0.26, "x": 0.13, "i_max": 294.882, "active": True},
    {"type": "3x1x240 mm2", "section": 240, "price_eur_m_core": 6.4, "r_dc": 0.125, "r_ac": 0.161, "x": 0.12, "i_max": 381.0, "active": True},
    {"type": "3x1x400 mm2", "section": 400, "price_eur_m_core": 7.9, "r_dc": 0.0778, "r_ac": 0.098, "x": 0.11, "i_max": 493.592, "active": True},
    {"type": "3x1x500 mm2", "section": 500, "price_eur_m_core": 8.9, "r_dc": 0.0605, "r_ac": 0.08, "x": 0.10, "i_max": 554.0, "active": True},
    {"type": "3x1x630 mm2", "section": 630, "price_eur_m_core": 10.4, "r_dc": 0.0469, "r_ac": 0.063, "x": 0.10, "i_max": 625.0, "active": True},
    {"type": "3x1x800 mm2", "section": 800, "price_eur_m_core": 12.0, "r_dc": 0.0369, "r_ac": 0.04, "x": 0.10, "i_max": 800.0, "active": True},
]

# Katalogi domyślne – takie same jak w oknie AEP (wyniki trafiają do tego samego katalogu Output)
try:  # najpierw wartości z modułu AEP, jeśli jest dostępny w tym samym środowisku
    from AEP.gui.aep_dialog import (  # type: ignore
        DEFAULT_OUTPUT_DIR as _AEP_OUT, DEFAULT_WTG_DIR as _AEP_WTG, PYTHON_EXE as _AEP_PY,
    )
except Exception:  # noqa: BLE001
    _AEP_OUT = _AEP_WTG = _AEP_PY = None


def _resolve_data_root() -> str:
    for key in ("AEP_DATA_ROOT", "WINDLAB_OUTPUT_DIR"):
        env = os.environ.get(key, "").strip()
        if key == "WINDLAB_OUTPUT_DIR" and env:
            parent = os.path.dirname(env)
            if parent and os.path.isdir(parent):
                return parent
        if env and os.path.isdir(env):
            return env
    candidates = [
        os.path.abspath(os.path.join(_CABLE_ROOT, os.pardir, os.pardir)),
        os.path.abspath(os.path.join(_CABLE_ROOT, os.pardir)),
        _CABLE_ROOT,
    ]
    for c in candidates:
        if (os.path.isdir(os.path.join(c, "InputNC")) or os.path.isdir(os.path.join(c, "Data", "NC"))
                or os.path.isdir(os.path.join(c, "WTG"))):
            return c
    return candidates[0]


_DATA_ROOT = _resolve_data_root()
DEFAULT_OUTPUT_DIR = _AEP_OUT or os.environ.get("WINDLAB_OUTPUT_DIR", os.path.join(_DATA_ROOT, "Output"))
DEFAULT_WTG_DIR = _AEP_WTG or os.environ.get("WINDLAB_WTG_DIR", os.path.join(_DATA_ROOT, "WTG"))


def _find_python_exe() -> str:
    """Zewnętrzny Python z modułami obliczeniowymi (nie Python QGIS)."""
    for key in ("CABLE_PYTHON", "AEP_PYTHON"):
        env = (os.environ.get(key) or "").strip().strip('"')
        if env and os.path.isfile(env):
            return env
    if _AEP_PY and os.path.isfile(str(_AEP_PY)):
        return str(_AEP_PY)
    home = os.path.expanduser("~")
    standalone = [r"C:\Python3\python.exe", r"C:\Python311\python.exe", r"C:\Python310\python.exe", r"C:\Python39\python.exe"]
    for ver in ("Python311", "Python310", "Python39", "Python312"):
        standalone.append(os.path.join(home, "AppData", "Local", "Programs", "Python", ver, "python.exe"))
    for cand in standalone:
        if os.path.isfile(cand):
            return cand
    return sys.executable or "python"


PYTHON_EXE = _find_python_exe()


def _aep_attr(name, fallback):
    """Wartość z modułu AEP odczytana w chwili użycia (obsługa zmiany projektu), inaczej fallback."""
    try:
        import importlib
        mod = importlib.import_module("AEP.gui.aep_dialog")
        val = getattr(mod, name, None)
        return val or fallback
    except Exception:  # noqa: BLE001
        return fallback


def current_output_dir() -> str:
    """Katalog wyników projektu – tak jak w AEP: WINDLAB_OUTPUT_DIR, potem stała z modułu AEP, potem <root>/Output."""
    env = (os.environ.get("WINDLAB_OUTPUT_DIR") or "").strip()
    if env:
        return env
    return _aep_attr("DEFAULT_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)


def current_wtg_dir() -> str:
    env = (os.environ.get("WINDLAB_WTG_DIR") or "").strip()
    if env:
        return env
    return _aep_attr("DEFAULT_WTG_DIR", DEFAULT_WTG_DIR)


def _norm_label(s) -> str:
    """WTG_11_A, 11a, WTG11A → '11A' (jak cable_common.norm_label; bez importu numpy w QGIS)."""
    txt = str(s or "").strip().upper()
    if txt.startswith("WTG"):
        txt = txt[3:]
    return re.sub(r"[^0-9A-Z]", "", txt)

class ProcWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, script, params, label, timeout=3600, python_exe=None):
        super().__init__()
        self.script = script
        self.params = params
        self.label = label
        self.timeout = timeout
        self.python_exe = python_exe or PYTHON_EXE

    def run(self):
        try:
            self.log.emit("Uruchamiam: %s ..." % self.label)
            self.finished.emit(self._run_sub())
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    def _run_sub(self):
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["QT_QPA_PLATFORM"] = env.get("AEP_QT_PLATFORM", "offscreen")
        py = self.python_exe
        base = os.path.basename(py).lower()
        if not rx.is_remote() and "qgis" in base and "python" not in base:
            raise RuntimeError("Błędny interpreter: %s\nUstaw zmienną CABLE_PYTHON na python.exe ze środowiska z numpy/scipy/shapely." % py)
        fd, params_path = tempfile.mkstemp(suffix=".json", prefix="cable_params_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.params, f, ensure_ascii=False)
            self.log.emit("  Python: %s" % py)
            self.log.emit("  Worker: %s" % self.script)
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            r = rx.run([py, self.script, "--params-file", params_path], capture_output=True,
                               env=env, timeout=self.timeout, **kwargs)
        finally:
            with contextlib.suppress(Exception):
                os.unlink(params_path)
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        stderr = r.stderr.decode("utf-8", errors="replace").strip()
        if stderr:
            for line in stderr.splitlines():
                if line.strip():
                    self.log.emit("  [log] %s" % line)
        if not stdout:
            raise RuntimeError("Brak outputu z %s (returncode=%s)\n%s" % (os.path.basename(self.script), r.returncode, stderr[:600]))
        lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
        raw = lines[-1] if lines else stdout
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Niepoprawny JSON z workera:\n%s" % stdout[:800])
        if data.get("status") == "error":
            raise RuntimeError("Błąd workera: %s\n%s" % (data.get("message", "?"), data.get("trace", "")))
        return data


def _to_float(text, what):
    try:
        return float(str(text).strip().replace(" ", "").replace(",", "."))
    except ValueError:
        raise ValueError("Niepoprawna liczba w polu: %s (%r)" % (what, text))


class CableDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        self._thread = None
        self._worker = None
        self._mst_result = None
        self._opt_result = None
        self._build_ui()
        self._populate_layers()
        self._refresh_aep_status()
        self._populate_wtg_combo()
        self.log_box.appendPlainText("Katalog wyników: %s\nProjekt: %s" % (self._out_dir(), self._project_name()))

    # ══ UI ════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        self.tabs = set_role(QTabWidget(), "card")
        self.tabs.addTab(self._scrolled(self._build_tab_mst()), "1. Układ i MST")
        self.tabs.addTab(self._scrolled(self._build_tab_opt()), "2. Optymalizacja")
        root.addWidget(self.tabs, 1)

        self.log_box = set_role(QPlainTextEdit(), "log")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(150)
        root.addWidget(self.log_box)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.clicked.connect(self._on_report)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(btn_close)
        row.addWidget(self.btn_report)
        root.addLayout(row)

        # zakładki mają własne przewijanie – bez dodatkowego obszaru przewijania
        style_module_dialog(self, "cables", "Trasy kablowe SN",
                            "MST i optymalizacja układu kabli")

    @staticmethod
    def _scrolled(widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(widget)
        return area

    # ── Zakładka 1 ────────────────────────────────────────────────────────────
    def _build_tab_mst(self):
        w = QWidget()
        v = QVBoxLayout(w)

        g = QGroupBox("Warstwy wejściowe")
        gl = QGridLayout(g)
        gl.setColumnStretch(1, 1)
        gl.addWidget(QLabel("Warstwa turbin (etykieta z atrybutu USERLABEL):"), 0, 0)
        self.t_layer = QComboBox()
        gl.addWidget(self.t_layer, 0, 1)
        self.t_selected = QCheckBox("Tylko zaznaczone obiekty")
        gl.addWidget(self.t_selected, 1, 1)

        gl.addWidget(QLabel("Warstwa GPO (punkty):"), 2, 0)
        self.g_layer = QComboBox()
        self.g_layer.currentIndexChanged.connect(
            lambda *_: self._fill_fields(self.g_layer, self.g_bays, ("bays", "pola", "liczba_pol"), True))
        gl.addWidget(self.g_layer, 2, 1)
        gl.addWidget(QLabel("Atrybut z liczbą pól SN w GPO (opcjonalnie):"), 3, 0)
        self.g_bays = QComboBox()
        gl.addWidget(self.g_bays, 3, 1)

        gl.addWidget(QLabel("Warstwa stref no-go (poligony, opcjonalnie):"), 4, 0)
        self.n_layer = QComboBox()
        gl.addWidget(self.n_layer, 4, 1)
        gl.addWidget(QLabel("Bufor wokół stref no-go [m]:"), 5, 0)
        self.n_buffer = QDoubleSpinBox()
        self.n_buffer.setRange(0, 1000)
        self.n_buffer.setDecimals(1)
        self.n_buffer.setValue(0.0)
        self.n_buffer.setToolTip("Odsunięcie tras od granicy strefy. 0 = trasy mogą biec po granicy.")
        gl.addWidget(self.n_buffer, 5, 1)
        v.addWidget(g)

        info = hint_label(
            "MST jest odniesieniem długości: łączy wszystkie turbiny z GPO najkrótszą siecią, nie uwzględnia obciążalności kabli, "
            "liczby pól GPO ani przecięć. Przy kilku GPO powstaje las rozpinający. Obliczenia w EPSG:2180.")
        v.addWidget(info)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Odśwież listy warstw")
        self.btn_refresh.clicked.connect(self._populate_layers)
        row.addWidget(self.btn_refresh)
        self.btn_mst = set_role(QPushButton("Oblicz MST i graf tras"), "primary")
        self.btn_mst.clicked.connect(self._on_run_mst)
        self.btn_add_mst = QPushButton("Dodaj warstwy MST do projektu")
        self.btn_add_mst.setEnabled(False)
        self.btn_add_mst.clicked.connect(self._on_add_mst_layers)
        row.addStretch()
        row.addWidget(self.btn_add_mst)
        row.addWidget(self.btn_mst)
        v.addLayout(row)

        self.mst_summary = QLabel("")
        self.mst_summary.setWordWrap(True)
        v.addWidget(self.mst_summary)
        v.addStretch()
        return w

    # ── Zakładka 2 ────────────────────────────────────────────────────────────
    def _build_tab_opt(self):
        w = QWidget()
        v = QVBoxLayout(w)

        # moc turbiny
        g = QGroupBox("Turbina: moc znamionowa i straty roczne")
        gl = QGridLayout(g)
        self.rb_aep = QRadioButton("Z wyników AEP (aep_results.json + macierz G)")
        self.rb_manual = QRadioButton("Ręcznie: moc znamionowa + LLF")
        self.rb_aep.setChecked(True)
        gl.addWidget(self.rb_aep, 0, 0, 1, 3)
        self.aep_edit = QLineEdit("")
        gl.addWidget(self.aep_edit, 1, 0, 1, 2)
        btn = browse_button("Wskaż wynik AEP (JSON)")
        btn.clicked.connect(self._browse_aep)
        gl.addWidget(btn, 1, 2)
        self.aep_status = QLabel("")
        self.aep_status.setWordWrap(True)
        gl.addWidget(self.aep_status, 2, 0, 1, 3)

        gl.addWidget(self.rb_manual, 3, 0, 1, 3)
        self.turbine = TurbinePicker("power", current_wtg_dir())
        self.turbine.changed.connect(self._on_wtg_selected)
        gl.addWidget(self.turbine, 4, 0, 1, 3)
        gl.addWidget(QLabel("Moc znamionowa [MW] (z wybranego trybu pracy):"), 5, 0)
        self.p_spin = QDoubleSpinBox()
        self.p_spin.setRange(0.0, 30)
        self.p_spin.setDecimals(3)
        self.p_spin.setReadOnly(True)
        self.p_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.p_spin.setToolTip("Maksimum krzywej mocy wybranego trybu pracy z pliku .wtg.")
        gl.addWidget(self.p_spin, 5, 1)
        self.type_name = ""
        gl.addWidget(QLabel("LLF (współczynnik strat obciążeniowych):"), 6, 0)
        self.llf_spin = QDoubleSpinBox()
        self.llf_spin.setRange(0.01, 1.0)
        self.llf_spin.setDecimals(3)
        self.llf_spin.setSingleStep(0.01)
        self.llf_spin.setValue(0.30)
        self.llf_spin.setToolTip("LLF = średnia z (P/Pn)² w roku. Wartość 0,30 wynika z jednego przebiegu AEP "
                                 "(9 × SG 7.0-170, CF 36,6 %; w tym przebiegu LLF ≈ 1,8·CF²) – zastąp własną, dla innej farmy może być inna.")
        gl.addWidget(self.llf_spin, 6, 1)
        gl.addWidget(QLabel("CF [%] (opcjonalnie, do udziału strat w energii; 0 = brak):"), 7, 0)
        self.cf_spin = QDoubleSpinBox()
        self.cf_spin.setRange(0, 100)
        self.cf_spin.setDecimals(1)
        gl.addWidget(self.cf_spin, 7, 1)
        v.addWidget(g)
        self.rb_aep.toggled.connect(self._on_power_mode)
        self._on_power_mode()

        # elektryka
        g = QGroupBox("Parametry elektryczne i ograniczenia")
        gl = QGridLayout(g)
        self.u_spin = self._dspin(1, 150, 33.0, 1)
        self.cos_spin = self._dspin(0.5, 1.0, 0.95, 3)
        self.cos_spin.setSingleStep(0.01)
        self.cos_spin.setToolTip("Założenie użytkownika (domyślnie 0,95), nie dana z karty turbiny. Wartość z warunków przyłączenia "
                                 "lub z dokumentacji producenta (zakres regulacji mocy biernej) wpisz tutaj.")
        self.dv_spin = self._dspin(0.1, 20, 3.0, 2)
        self.loss_spin = self._dspin(0.1, 20, 3.5, 2)
        self.icap_spin = self._dspin(0, 5000, 0, 0)
        self.icap_spin.setToolTip("Limit prądu przepływającego przez rozdzielnicę turbiny / feeder [A]. 0 = brak limitu. "
                                  "Np. rozdzielnica 630 A w platformie Vestas 4 MW (General Description) ogranicza prąd łańcucha.")
        self.indeg_spin = QSpinBox()
        self.indeg_spin.setRange(1, 6)
        self.indeg_spin.setValue(2)
        self.indeg_spin.setToolTip("Maks. liczba kabli dopływających do jednej turbiny. 1 = tylko łańcuchy (bez rozgałęzień).")
        self.bays_spin = QSpinBox()
        self.bays_spin.setRange(0, 200)
        self.bays_spin.setValue(0)
        self.bays_spin.setToolTip("Domyślna liczba pól SN w GPO, gdy warstwa GPO nie ma atrybutu z liczbą pól. 0 = bez limitu.")
        rows = [("Napięcie znamionowe [kV]:", self.u_spin), ("cosφ (założenie):", self.cos_spin),
                ("Maks. spadek napięcia [%]:", self.dv_spin), ("Limit strat przy Pn [%] (kontrola):", self.loss_spin),
                ("Limit prądu feedera/rozdzielnicy [A] (0 = brak):", self.icap_spin),
                ("Maks. kabli dopływających do turbiny:", self.indeg_spin),
                ("Domyślna liczba pól GPO (0 = bez limitu):", self.bays_spin)]
        for i, (lab, wd) in enumerate(rows):
            gl.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            gl.addWidget(wd, i // 2, (i % 2) * 2 + 1)
        v.addWidget(g)

        # biblioteka kabli
        g = QGroupBox("Biblioteka kabli (domyślnie wartości ze strony windfarmoptimizer.com)")
        gl = QVBoxLayout(g)
        note = QLabel("Cena w €/mb JEDNEJ żyły (kabel 3×1× liczony ×3). I_max wpisz po uwzględnieniu warunków ułożenia – domyślne "
                      "wartości zostały przepisane ze strony i wymagają weryfikacji z kartą katalogową.")
        note.setWordWrap(True)
        gl.addWidget(note)
        self.cab_table = QTableWidget()
        self.cab_table.setColumnCount(8)
        self.cab_table.setHorizontalHeaderLabels(["Aktywny", "Typ", "Przekrój [mm²]", "Cena żyły [€/mb]", "R_dc [Ω/km]",
                                                  "R_ac [Ω/km]", "X [Ω/km]", "I_max [A]"])
        self.cab_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cab_table.setMinimumHeight(220)
        gl.addWidget(self.cab_table)
        hl = QHBoxLayout()
        b1 = QPushButton("Dodaj kabel")
        b1.clicked.connect(lambda: self._add_cable_row({"type": "", "section": 0, "price_eur_m_core": 0, "r_dc": 0,
                                                       "r_ac": 0, "x": 0, "i_max": 0, "active": True}))
        b2 = QPushButton("Usuń wybrany")
        b2.clicked.connect(self._remove_cable_row)
        b3 = QPushButton("Przywróć domyślne (windfarmoptimizer)")
        b3.clicked.connect(self._reset_cables)
        hl.addWidget(b1)
        hl.addWidget(b2)
        hl.addStretch()
        hl.addWidget(b3)
        gl.addLayout(hl)
        v.addWidget(g)
        self._reset_cables()

        # przekroje poza biblioteką
        g = QGroupBox("Przekroje poza biblioteką")
        gl = QGridLayout(g)
        self.chk_beyond = QCheckBox("Dopuszczaj przekroje większe niż w bibliotece (wirtualne – wyznaczane z wymaganego prądu odcinka)")
        self.chk_beyond.setToolTip("Gdy największy kabel biblioteki nie przenosi prądu odcinka, model używa przekroju wirtualnego o obciążalności "
                                   "równej wymaganemu prądowi. Przekrój równoważny, cena żyły i rezystancja są ekstrapolacją z biblioteki "
                                   "(oznaczone w raporcie). Limit prądu feedera, jeśli ustawiony, nadal obowiązuje.")
        gl.addWidget(self.chk_beyond, 0, 0, 1, 4)
        gl.addWidget(QLabel("Cena żyły = a [€/mb] + b [€/mb na mm²] · przekrój (b = 0: z regresji biblioteki):"), 1, 0, 1, 2)
        self.bp_a = self._dspin(0, 1000, 0, 3)
        self.bp_b = self._dspin(0, 10, 0, 5)
        self.bp_a.setToolTip("Wyraz wolny ceny żyły przekroju wirtualnego (używany tylko przy b > 0).")
        self.bp_b.setToolTip("0 = cena z regresji liniowej ceny żyły względem przekroju w aktywnej bibliotece. "
                             "Wpisz b > 0, aby podać własną zależność.")
        gl.addWidget(QLabel("a:"), 1, 2)
        gl.addWidget(self.bp_a, 1, 3)
        gl.addWidget(QLabel("b:"), 2, 2)
        gl.addWidget(self.bp_b, 2, 3)
        note_b = QLabel("Ceny i parametry przekrojów wirtualnych to szacunek, nie dane katalogowe. Rzetelniejsza wycena: dodaj do biblioteki "
                        "własny większy przekrój z ceną od dostawcy. Bez limitu prądu feedera liczba możliwych przekrojów rośnie z liczbą turbin "
                        "(czas obliczeń).")
        note_b.setWordWrap(True)
        gl.addWidget(note_b, 3, 0, 1, 4)
        gl.setColumnStretch(0, 1)
        v.addWidget(g)
        self.chk_beyond.toggled.connect(lambda on: (self.bp_a.setEnabled(on), self.bp_b.setEnabled(on)))
        self.bp_a.setEnabled(False)
        self.bp_b.setEnabled(False)

        # koszty
        g = QGroupBox("Koszty dodatkowe (opcjonalne – puste/0 = nie wliczane)")
        gl = QGridLayout(g)
        self.branch_spin = self._dspin(0, 1e7, 0, 0)
        self.branch_spin.setToolTip("Koszt jednego dodatkowego kabla dopływającego do turbiny (rozgałęzienie). Wartość podajesz Ty.")
        self.price_spin = self._dspin(0, 10, 0, 4)
        self.price_spin.setToolTip("Gdy > 0, straty roczne są kapitalizowane i wchodzą do doboru przekrojów.")
        self.horizon_spin = self._dspin(1, 60, 20, 0)
        self.disc_spin = self._dspin(0, 30, 0, 1)
        self.reserve_spin = self._dspin(0, 50, 0, 1)
        self.reserve_spin.setToolTip("Rezerwa długości kabla względem długości trasy (zwisy, wprowadzenia do turbin). 0 = brak.")
        rows = [("Koszt rozgałęzienia [€ za dodatkowy kabel]:", self.branch_spin),
                ("Cena energii [€/kWh] (0 = straty nie wliczane do kosztu):", self.price_spin),
                ("Okres kapitalizacji strat [lata]:", self.horizon_spin), ("Stopa dyskonta [%]:", self.disc_spin),
                ("Rezerwa długości kabla [%]:", self.reserve_spin)]
        for i, (lab, wd) in enumerate(rows):
            gl.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            gl.addWidget(wd, i // 2, (i % 2) * 2 + 1)
        gl.addWidget(QLabel("Wykopy, montaż i mufowanie nie są wliczane do kosztu. Kara za rozgałęzienie i straty wpływają "
                            "tylko na tryb „min. koszt kabli”."), 3, 0, 1, 4)
        v.addWidget(g)

        # funkcja celu
        g = QGroupBox("Funkcja celu")
        gl = QVBoxLayout(g)
        self.rb_obj_cost = QRadioButton("Min. koszt kabli – zakup kabli (opcjonalnie straty i kara za rozgałęzienie)")
        self.rb_obj_len = QRadioButton("Min. długość tras – te same ograniczenia, przekroje dobierane po wyznaczeniu topologii")
        self.rb_obj_cost.setChecked(True)
        self.chk_compare = QCheckBox("Policz też drugi tryb i dodaj porównanie do raportu (czas obliczeń rośnie ok. dwukrotnie)")
        self.chk_compare.setChecked(True)
        gl.addWidget(self.rb_obj_cost)
        gl.addWidget(self.rb_obj_len)
        gl.addWidget(self.chk_compare)
        v.addWidget(g)

        # solver
        g = QGroupBox("Metoda")
        gl = QGridLayout(g)
        self.rb_milp = QRadioButton("MILP (HiGHS) – dokładniejsza, startuje od heurystyki")
        self.rb_ew = QRadioButton("Tylko heurystyka Esau–Williams (szybka)")
        self.rb_milp.setChecked(True)
        gl.addWidget(self.rb_milp, 0, 0, 1, 2)
        gl.addWidget(self.rb_ew, 1, 0, 1, 2)
        gl.addWidget(QLabel("Limit czasu MILP [s]:"), 0, 2)
        self.tl_spin = self._dspin(5, 7200, 120, 0)
        gl.addWidget(self.tl_spin, 0, 3)
        gl.addWidget(QLabel("Dopuszczalna luka optymalności [%]:"), 1, 2)
        self.gap_spin = self._dspin(0, 20, 1.0, 2)
        gl.addWidget(self.gap_spin, 1, 3)
        v.addWidget(g)

        row = QHBoxLayout()
        self.btn_opt = set_role(QPushButton("Oblicz optymalizację"), "primary")
        self.btn_opt.clicked.connect(self._on_run_opt)
        self.btn_add_opt = QPushButton("Dodaj warstwy układu do projektu")
        self.btn_add_opt.setEnabled(False)
        self.btn_add_opt.clicked.connect(self._on_add_opt_layers)
        row.addStretch()
        row.addWidget(self.btn_add_opt)
        row.addWidget(self.btn_opt)
        v.addLayout(row)
        self.opt_summary = QLabel("")
        self.opt_summary.setWordWrap(True)
        v.addWidget(self.opt_summary)
        v.addStretch()
        return w

    @staticmethod
    def _dspin(lo, hi, val, dec):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setValue(val)
        return s

    # ══ Warstwy i pola ════════════════════════════════════════════════════════
    @staticmethod
    def _userlabel_field(layer):
        """Nazwa atrybutu USERLABEL w warstwie (bez rozróżniania wielkości liter) lub None."""
        if layer is None:
            return None
        for f in layer.fields():
            if f.name().lower() == "userlabel":
                return f.name()
        return None

    def _populate_layers(self):
        for cb in (self.t_layer, self.g_layer, self.n_layer):
            cb.blockSignals(True)
            cb.clear()
        self.n_layer.addItem("— brak —", None)
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.geometryType() == QgsWkbTypes.GeometryType.PointGeometry:
                self.t_layer.addItem(layer_icon(layer), layer.name(), layer)
                self.g_layer.addItem(layer_icon(layer), layer.name(), layer)
            elif layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry:
                self.n_layer.addItem(layer_icon(layer), layer.name(), layer)
        # warstwa turbin: pierwsza z atrybutem USERLABEL
        for i in range(self.t_layer.count()):
            if self._userlabel_field(self.t_layer.itemData(i)):
                self.t_layer.setCurrentIndex(i)
                break
        for cb in (self.t_layer, self.g_layer, self.n_layer):
            cb.blockSignals(False)
        self._fill_fields(self.g_layer, self.g_bays, ("bays", "pola", "liczba_pol"), True)

    @staticmethod
    def _fill_fields(layer_combo, field_combo, prefer, allow_none):
        field_combo.clear()
        if allow_none:
            field_combo.addItem("(brak)", "")
        layer = layer_combo.currentData()
        if not layer:
            return
        names = [f.name() for f in layer.fields()]
        for n in names:
            field_combo.addItem(n, n)
        for pref in prefer:
            for i, n in enumerate(names):
                if n.lower() == pref:
                    field_combo.setCurrentIndex(i + (1 if allow_none else 0))
                    return

    # ── Katalog wyników i nazwa projektu (jak w AEP – bez pól w oknie) ───────
    def _out_dir(self):
        return current_output_dir()

    def _aep_json_default(self):
        return os.path.join(self._out_dir(), "aep_results.json")

    def _project_name(self):
        env = (os.environ.get("WINDLAB_PROJECT_NAME") or "").strip()
        if env:
            return env
        with contextlib.suppress(Exception):
            with open(self._aep_json_default(), "r", encoding="utf-8") as f:
                name = str((json.load(f) or {}).get("project_name") or "").strip()
            if name:
                return name
        with contextlib.suppress(Exception):
            prj = QgsProject.instance()
            name = (prj.title() or "").strip() or os.path.splitext(os.path.basename(prj.fileName() or ""))[0]
            if name:
                return name
        return "projekt"

    def _browse_aep(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wyniki AEP", self._out_dir(), "aep_results.json (*.json)")
        if path:
            self.aep_edit.setText(path)
            self._refresh_aep_status(keep_path=True)

    def _on_power_mode(self, *_):
        manual = self.rb_manual.isChecked()
        for wd in (self.turbine, self.p_spin, self.cf_spin):
            wd.setEnabled(manual)
        self.llf_spin.setEnabled(manual)
        self.aep_edit.setEnabled(not manual)

    # ── Turbina (.wtg) – jak w AEP ───────────────────────────────────────────
    def _populate_wtg_combo(self):
        """Ustawia turbinę i tryb pracy użyte w AEP (z aep_results.json), jeśli są dostępne."""
        with contextlib.suppress(Exception):
            with open(self._aep_json_default(), "r", encoding="utf-8") as f:
                d = json.load(f) or {}
            path = str(d.get("wtg_path") or "")
            from WindLab.core.wtg.remote import is_remote_wtg
            if path and (is_remote_wtg(path) or os.path.isfile(path)):
                self.turbine.set_path(path, str(d.get("power_curve") or ""))
        self._on_wtg_selected()

    def _on_wtg_selected(self, *_):
        self.type_name = ""
        self.p_spin.setValue(0.0)
        tm, curve = self.turbine.model(), self.turbine.curve()
        if tm is None or curve is None:
            return
        self.p_spin.setValue(curve.rated_kw / 1000.0)
        self.type_name = "%s (%s)" % (tm.name, curve.label)

    def _refresh_aep_status(self, keep_path=False):
        if not keep_path or not self.aep_edit.text().strip():
            self.aep_edit.setText(self._aep_json_default())
        path = self.aep_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.aep_status.setText("Brak pliku wyników AEP – użyj trybu ręcznego albo najpierw policz AEP.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            has_g = False
            npz = os.path.join(os.path.dirname(path), "aep_results.npz")
            if os.path.isfile(npz):
                try:
                    import numpy as np
                    has_g = "gram_w2h_yr" in np.load(npz, allow_pickle=True).files
                except Exception:  # noqa: BLE001
                    has_g = False
            self.aep_status.setText("%s, %.2f MW, %s turbin, macierz G: %s" % (
                d.get("turbine_type", "?"), float(d.get("rated_power_mw") or 0), d.get("n_turbines", "?"),
                "tak (straty dokładne)" if has_g else "NIE (zaktualizuj worker_aep.py lub użyj trybu ręcznego)"))
        except Exception as e:  # noqa: BLE001
            self.aep_status.setText("Nie można odczytać pliku: %s" % e)

    def _check_labels_vs_aep(self, labels):
        """Porównuje USERLABEL z warstwy z listą turbin w aep_results.json (jeśli istnieje) – tylko ostrzeżenie w logu."""
        try:
            with open(self._aep_json_default(), "r", encoding="utf-8") as f:
                aep = json.load(f)
            aep_labels = list(((aep.get("turbines") or {}).get("userlabel")) or [])
        except Exception:  # noqa: BLE001
            return
        if not aep_labels:
            return
        a = {_norm_label(x): x for x in aep_labels}
        b = {_norm_label(x): x for x in labels}
        only_layer = [b[k] for k in b if k not in a]
        only_aep = [a[k] for k in a if k not in b]
        if only_layer or only_aep:
            self.log_box.appendPlainText(
                "⚠ USERLABEL z warstwy różni się od aep_results.json. Tylko w warstwie: %s. Tylko w wynikach AEP: %s." % (
                    ", ".join(only_layer) or "—", ", ".join(only_aep) or "—"))
        else:
            self.log_box.appendPlainText("USERLABEL turbin zgadza się z aep_results.json (%d turbin)." % len(labels))

    # ── Biblioteka kabli ─────────────────────────────────────────────────────
    def _reset_cables(self):
        self.cab_table.setRowCount(0)
        for c in DEFAULT_CABLES:
            self._add_cable_row(c)

    def _add_cable_row(self, c):
        row = self.cab_table.rowCount()
        self.cab_table.insertRow(row)
        chk = QCheckBox()
        chk.setChecked(bool(c.get("active", True)))
        self.cab_table.setCellWidget(row, 0, chk)
        vals = [c["type"], c["section"], c["price_eur_m_core"], c["r_dc"], c["r_ac"], c["x"], c["i_max"]]
        for j, val in enumerate(vals):
            self.cab_table.setItem(row, 1 + j, QTableWidgetItem(str(val)))

    def _remove_cable_row(self):
        row = self.cab_table.currentRow()
        if row >= 0:
            self.cab_table.removeRow(row)

    def _get_cables(self):
        cables = []
        for r in range(self.cab_table.rowCount()):
            def cell(j):
                it = self.cab_table.item(r, j)
                return it.text() if it else ""
            chk = self.cab_table.cellWidget(r, 0)
            name = cell(1).strip() or ("wiersz %d" % (r + 1))
            cables.append({
                "active": bool(chk.isChecked()) if chk else True, "type": cell(1).strip(),
                "section": _to_float(cell(2), "przekrój (%s)" % name),
                "price_eur_m_core": _to_float(cell(3), "cena (%s)" % name),
                "r_dc": _to_float(cell(4) or "0", "R_dc (%s)" % name),
                "r_ac": _to_float(cell(5), "R_ac (%s)" % name),
                "x": _to_float(cell(6), "X (%s)" % name),
                "i_max": _to_float(cell(7), "I_max (%s)" % name),
            })
        return cables

    # ══ Dane z warstw ═════════════════════════════════════════════════════════
    @staticmethod
    def _points(geom):
        if geom is None or geom.isNull() or geom.isEmpty():
            return []
        if QgsWkbTypes.isMultiType(geom.wkbType()):
            return list(geom.asMultiPoint())
        return [geom.asPoint()]

    def _collect_mst_params(self):
        out_dir = self._out_dir()
        if not out_dir:
            raise ValueError("Nie ustalono katalogu wyników projektu (ustaw WINDLAB_OUTPUT_DIR lub uruchom moduł AEP).")
        t_layer = self.t_layer.currentData()
        g_layer = self.g_layer.currentData()
        if t_layer is None:
            raise ValueError("Wybierz warstwę punktową z turbinami.")
        if g_layer is None:
            raise ValueError("Wybierz warstwę punktową GPO.")
        t_field = self._userlabel_field(t_layer)
        if not t_field:
            raise ValueError("Warstwa turbin „%s” nie ma atrybutu USERLABEL." % t_layer.name())
        crs = QgsCoordinateReferenceSystem("EPSG:2180")

        ct = QgsCoordinateTransform(t_layer.crs(), crs, QgsProject.instance())
        feats = t_layer.selectedFeatures() if self.t_selected.isChecked() else list(t_layer.getFeatures())
        if self.t_selected.isChecked() and not feats:
            raise ValueError("Zaznaczono opcję „Tylko zaznaczone”, ale nic nie jest zaznaczone w warstwie turbin.")
        labels, xs, ys = [], [], []
        for f in feats:
            lab = f[t_field]
            lab = "" if lab in (None, "") else str(lab).strip()
            if not lab:
                raise ValueError("Turbina bez etykiety w atrybucie %s (id obiektu %s)." % (t_field, f.id()))
            for p in self._points(f.geometry()):
                q = ct.transform(p)
                labels.append(lab)
                xs.append(q.x())
                ys.append(q.y())
        if not labels:
            raise ValueError("Warstwa turbin nie zawiera punktów.")
        self._check_labels_vs_aep(labels)

        ctg = QgsCoordinateTransform(g_layer.crs(), crs, QgsProject.instance())
        g_field = None   # nazwa GPO z atrybutu, jeśli warstwa ma pole o typowej nazwie; inaczej GPO_1, GPO_2, …
        _names = {f.name().lower(): f.name() for f in g_layer.fields()}
        for cand in ("name", "nazwa", "label", "gpo", "userlabel"):
            if cand in _names:
                g_field = _names[cand]
                break
        b_field = self.g_bays.currentData()
        g_labels, gx, gy, bays = [], [], [], []
        for k, f in enumerate(g_layer.getFeatures()):
            pts = self._points(f.geometry())
            for j, p in enumerate(pts):
                q = ctg.transform(p)
                name = ""
                if g_field:
                    val = f[g_field]
                    name = "" if val in (None, "") else str(val).strip()
                if not name:
                    name = "GPO_%d" % (len(g_labels) + 1)
                elif len(pts) > 1:
                    name = "%s_%d" % (name, j + 1)
                g_labels.append(name)
                gx.append(q.x())
                gy.append(q.y())
                b = None
                if b_field:
                    try:
                        b = int(f[b_field]) if f[b_field] not in (None, "") else None
                    except (TypeError, ValueError):
                        raise ValueError("Atrybut %s (liczba pól) musi być liczbą całkowitą (GPO %s)." % (b_field, name))
                bays.append(b)
        if not g_labels:
            raise ValueError("Warstwa GPO nie zawiera punktów.")

        nogo = []
        n_layer = self.n_layer.currentData()
        if n_layer is not None:
            ctn = QgsCoordinateTransform(n_layer.crs(), crs, QgsProject.instance())
            for f in n_layer.getFeatures():
                geom = QgsGeometry(f.geometry())
                if geom.isNull() or geom.isEmpty():
                    continue
                geom.transform(ctn)
                nogo.append(geom.asWkt(3))
        return {
            "turbines": {"labels": labels, "x": xs, "y": ys},
            "gpo": {"labels": g_labels, "x": gx, "y": gy, "bays": bays},
            "nogo_wkt": nogo, "nogo_buffer_m": self.n_buffer.value(),
            "output_dir": out_dir, "project_name": self._project_name(),
        }

    def _collect_opt_params(self):
        out_dir = self._out_dir()
        if not os.path.isfile(os.path.join(out_dir, "cable_graph.json")):
            raise ValueError("Brak pliku cable_graph.json w katalogu wyjściowym – najpierw policz MST (zakładka 1).")
        cables = self._get_cables()
        p = {
            "output_dir": out_dir, "project_name": self._project_name(),
            "electrical": {"u_kv": self.u_spin.value(), "cos_phi": self.cos_spin.value(), "max_dv_pct": self.dv_spin.value(),
                           "max_loss_pct": self.loss_spin.value(),
                           "max_feeder_current_a": self.icap_spin.value() or None, "max_in_degree": self.indeg_spin.value()},
            "gpo_bays_default": self.bays_spin.value() or None,
            "cables": cables,
            "costs": {"branch_penalty_eur": self.branch_spin.value(), "energy_price_eur_kwh": self.price_spin.value() or None,
                      "horizon_years": self.horizon_spin.value(), "discount_pct": self.disc_spin.value(),
                      "length_reserve_pct": self.reserve_spin.value()},
            "solver": {"method": "milp" if self.rb_milp.isChecked() else "ew", "time_limit_s": self.tl_spin.value(),
                       "mip_gap_pct": self.gap_spin.value()},
            "beyond_library": {"enabled": self.chk_beyond.isChecked(),
                               "price_a_eur_m_core": self.bp_a.value() or None,
                               "price_b_eur_m_core_per_mm2": self.bp_b.value() or None},
            "objective": "length" if self.rb_obj_len.isChecked() else "cost",
            "compare_modes": self.chk_compare.isChecked(),
        }
        if self.rb_aep.isChecked():
            path = self.aep_edit.text().strip()
            if not os.path.isfile(path):
                raise ValueError("Nie znaleziono pliku wyników AEP: %s\nWybierz tryb ręczny albo wskaż plik." % path)
            p["power_source"] = "aep"
            p["aep_json_path"] = path
            p["aep_npz_path"] = os.path.join(os.path.dirname(path), "aep_results.npz")
            p["llf"] = self.llf_spin.value()   # używane tylko, gdy w npz nie ma macierzy G
        else:
            p["power_source"] = "manual"
            if not self.turbine.path() or self.p_spin.value() <= 0:
                raise ValueError("Tryb ręczny: wybierz plik .wtg z listy (moc znamionowa jest z niego odczytywana).")
            p["rated_power_mw"] = self.p_spin.value()
            p["turbine_type"] = self.type_name
            p["llf"] = self.llf_spin.value()
            p["cf_pct"] = self.cf_spin.value() or None
        return p

    # ══ Uruchamianie workerów ═════════════════════════════════════════════════
    def _start(self, script, params, label, on_done, timeout=3600):
        if self._thread is not None:
            QMessageBox.information(self, "Moduł kabli", "Poprzednie obliczenia jeszcze trwają.")
            return
        for b in (self.btn_mst, self.btn_opt, self.btn_report):
            b.setEnabled(False)
        self.progress.setVisible(True)
        self._thread = QThread()
        self._worker = ProcWorker(script, params, label, timeout=timeout)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log_box.appendPlainText)
        self._worker.finished.connect(on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self):
        for b in (self.btn_mst, self.btn_opt, self.btn_report):
            b.setEnabled(True)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None

    def _on_error(self, msg):
        self.log_box.appendPlainText("❌ BŁĄD: %s" % msg)
        QMessageBox.critical(self, "Błąd obliczeń", msg[:1200])

    # ── MST ───────────────────────────────────────────────────────────────────
    def _on_run_mst(self):
        try:
            params = self._collect_mst_params()
        except ValueError as e:
            QMessageBox.warning(self, "Błąd", str(e))
            return
        self.log_box.clear()
        self.log_box.appendPlainText("Turbiny: %d, GPO: %d, strefy no-go: %d" % (
            len(params["turbines"]["labels"]), len(params["gpo"]["labels"]), len(params["nogo_wkt"])))
        self._start(WORKER_MST, params, "MST i graf tras", self._on_mst_done, timeout=1800)

    def _on_mst_done(self, res):
        self._mst_result = res
        self.btn_add_mst.setEnabled(True)
        self.log_box.appendPlainText("=" * 50)
        self.log_box.appendPlainText("✅ MST gotowe: %.0f mb (gwiazda: %.0f mb), najdłuższa ścieżka %.0f mb" % (
            res["mst_total_length_m"], res["star_total_length_m"], res["mst_longest_path_m"]))
        for w in res.get("warnings") or []:
            self.log_box.appendPlainText("  ⚠ %s" % w)
        self.log_box.appendPlainText("JSON: %s" % res.get("json_path", ""))
        self.mst_summary.setText("MST: %.0f mb  |  gwiazda: %.0f mb  |  krawędzi kandydujących: %d (przecięć: %d)" % (
            res["mst_total_length_m"], res["star_total_length_m"], res["n_candidate_edges"], res["n_crossing_pairs"]))

    # ── Optymalizacja ─────────────────────────────────────────────────────────
    def _on_run_opt(self):
        try:
            params = self._collect_opt_params()
        except ValueError as e:
            QMessageBox.warning(self, "Błąd", str(e))
            return
        self.log_box.clear()
        self.log_box.appendPlainText("Źródło mocy: %s | cosφ = %.2f (założenie) | metoda: %s | funkcja celu: %s%s" % (
            params["power_source"], params["electrical"]["cos_phi"], params["solver"]["method"],
            "min. długość tras" if params["objective"] == "length" else "min. koszt kabli",
            " (+ porównanie z drugim trybem)" if params["compare_modes"] else ""))
        self._start(WORKER_OPT, params, "optymalizacja układu kabli", self._on_opt_done,
                    timeout=int(self.tl_spin.value()) + 1800)

    def _on_opt_done(self, res):
        self._opt_result = res
        self.btn_add_opt.setEnabled(True)
        s = res["summary"]
        self.log_box.appendPlainText("=" * 50)
        self.log_box.appendPlainText("✅ Optymalizacja zakończona – tryb: %s." % res.get("objective_label", ""))
        self.log_box.appendPlainText("Feederów: %d | trasy: %.0f mb | koszt kabli: %.0f € (bez wykopów)" % (
            s["n_feeders"], s["route_length_m"], s["cable_cost_eur"]))
        self.log_box.appendPlainText("Straty: %.1f MWh/rok (%s)%s | maks. ΔU: %.2f %% (%s)" % (
            s["loss_mwh_yr"], s["loss_method"],
            "" if s["loss_energy_pct"] is None else ", %.2f %% energii" % s["loss_energy_pct"],
            s["max_dv_pct"], s["max_dv_turbine"]))
        if s.get("n_virtual_segments"):
            self.log_box.appendPlainText("Poza biblioteką: %d odcinków, maks. równoważny przekrój ≈ %.0f mm² (ekstrapolacja; koszt tych odcinków %.0f €)." % (
                s["n_virtual_segments"], s["max_virtual_section_mm2"], s["virtual_cost_eur"]))
        alt = res.get("alt")
        if alt:
            sa = alt["summary"]
            self.log_box.appendPlainText("Tryb porównawczy (%s): feederów %d | trasy %.0f mb | koszt kabli %.0f € | straty %.1f MWh/rok | maks. ΔU %.2f %%%s" % (
                alt["objective_label"], sa["n_feeders"], sa["route_length_m"], sa["cable_cost_eur"], sa["loss_mwh_yr"], sa["max_dv_pct"],
                (" | poza biblioteką: %d odc. (maks. ≈ %.0f mm²)" % (sa["n_virtual_segments"], sa["max_virtual_section_mm2"]))
                if sa.get("n_virtual_segments") else ""))
        for w in res.get("warnings") or []:
            self.log_box.appendPlainText("  ⚠ %s" % w)
        for k in ("json_path", "xlsx_path", "svg_path", "geojson_path", "csv_path"):
            if res.get(k):
                self.log_box.appendPlainText("%s: %s" % (k.replace("_path", "").upper(), res[k]))
        txt = "%s: koszt kabli %.0f €  |  trasy %.0f mb  |  straty %.1f MWh/rok  |  maks. ΔU %.2f %%" % (
            (res.get("objective_label") or "").capitalize(), s["cable_cost_eur"], s["route_length_m"], s["loss_mwh_yr"], s["max_dv_pct"])
        if alt:
            txt += "\n%s: koszt kabli %.0f €  |  trasy %.0f mb  |  straty %.1f MWh/rok  |  maks. ΔU %.2f %%" % (
                alt["objective_label"].capitalize(), sa["cable_cost_eur"], sa["route_length_m"], sa["loss_mwh_yr"], sa["max_dv_pct"])
        self.opt_summary.setText(txt)

    # ── Warstwy w projekcie ───────────────────────────────────────────────────
    def _add_geojson(self, path, name):
        if not path or not os.path.isfile(path):
            self.log_box.appendPlainText("Brak pliku warstwy: %s" % path)
            return None
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            self.log_box.appendPlainText("Nie udało się wczytać warstwy: %s" % path)
            return None
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:2180"))
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _on_add_mst_layers(self):
        if not self._mst_result:
            return
        self._add_geojson(self._mst_result.get("mst_geojson"), "Kable – MST")
        self._add_geojson(self._mst_result.get("terminals_geojson"), "Kable – turbiny i GPO")

    def _on_add_opt_layers(self):
        if not self._opt_result:
            return
        layer = self._add_geojson(self._opt_result.get("geojson_path"), "Kable – układ zoptymalizowany")
        if layer is None:
            return
        try:  # styl kategoryzowany: kolor i grubość wg przekroju (ta sama paleta co w raporcie i SVG)
            from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
            palette = ["#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026", "#800026"]
            all_cables = self._opt_result["inputs"]["cables"]
            used_types = {s_["cable"] for s_ in self._opt_result.get("segments") or []}
            cables = [c for c in all_cables if (not c.get("virtual")) or c["type"] in used_types]
            n = len(cables)
            cats = []
            for rank, c in enumerate(cables):
                idx = 3 if n <= 1 else int(round(rank * (len(palette) - 1) / float(n - 1)))
                sym = QgsSymbol.defaultSymbol(layer.geometryType())
                sym.setColor(QColor(palette[idx]))
                sym.setWidth(0.5 + 1.2 * (rank / float(max(1, n - 1))))
                cats.append(QgsRendererCategory(c["type"], sym, c["type"]))
            layer.setRenderer(QgsCategorizedSymbolRenderer("cable", cats))
            layer.triggerRepaint()
        except Exception as e:  # noqa: BLE001
            self.log_box.appendPlainText("Nie udało się nadać stylu warstwie (warstwa dodana bez stylu): %s" % e)
        if self._mst_result:
            self._add_geojson(self._mst_result.get("terminals_geojson"), "Kable – turbiny i GPO")

    # ── Raport PDF ────────────────────────────────────────────────────────────
    def _on_report(self):
        out_dir = self._out_dir()
        if not os.path.isfile(os.path.join(out_dir, "cable_graph.json")):
            QMessageBox.warning(self, "Błąd", "Brak wyników w katalogu wyjściowym – najpierw policz MST (zakładka 1).")
            return
        proj = self._project_name()
        pdf = os.path.join(out_dir, report_filename("KABLE_SN", proj))
        self._pdf_path = pdf
        self.log_box.appendPlainText("Generuję raport PDF...")
        self._start(WORKER_REPORT, {"output_dir": out_dir, "output_pdf": pdf, "project_name": proj},
                    "raport PDF", self._on_report_done, timeout=600)

    def _on_report_done(self, res):
        pdf = res.get("pdf_path") or getattr(self, "_pdf_path", "")
        self.log_box.appendPlainText("✅ Raport PDF: %s" % pdf)
        try:
            open_path(pdf)
        except Exception as e:  # noqa: BLE001
            self.log_box.appendPlainText("Nie udało się otworzyć PDF: %s" % e)


def main():
    global _cable_dlg
    try:
        parent = iface.mainWindow()  # noqa: F821 – globalna QGIS
    except NameError:
        parent = None
    _cable_dlg = CableDialog(parent=parent)
    _cable_dlg.show()
    _cable_dlg.raise_()
    _cable_dlg.activateWindow()


if __name__ == "__main__":
    main()
