"""
NC_Data_Q_GUI.py
GUI launcher dla pobierania i analizy danych NEWA (NC_Downloader + NC_Analizer).
Uruchom w edytorze Python QGIS.

Pobieranie: mesoscale time series + microscale atlas.
Nazwy plików: WTG_{USERLABEL}_meso.nc / WTG_{USERLABEL}_micro.nc
"""

import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
from WindLab.core import remote_exec as rx
import json
import os
import sys
import tempfile
from pathlib import Path

from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsMapLayer,
)
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QLineEdit, QFileDialog, QMessageBox, QWidget, QTabWidget,
    QRadioButton, QButtonGroup, QDateEdit, QCalendarWidget,
    QInputDialog,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject, QDate
from qgis.PyQt.QtGui import QFont
from WindLab.core.ui.widgets import open_path
import contextlib

# ── KONFIGURACJA ──────────────────────────────────────────────────────────────
PYTHON_EXE = os.environ.get("AEP_PYTHON", os.environ.get("WINDLAB_PYTHON", r"C:\Python3\python.exe"))
# Katalog skryptów – w edytorze QGIS __file__ bywa niedostępne
try:
    WORKERS_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    WORKERS_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.environ.get("WINDLAB_NC_DIR", os.path.join(WORKERS_DIR, "dane_turbin"))

DOWNLOADER     = os.environ.get("WINDLAB_NC_DOWNLOADER", os.path.join(os.path.dirname(WORKERS_DIR), "download", "NC_Downloader.py"))
ANALYZER       = os.path.join(WORKERS_DIR, "NC_Analizer.py")
DEFAULT_OUTPUT = os.environ.get("WINDLAB_NC_DIR", OUTPUT_DIR)

# Jak w oryginalnym NC_Downloader – nie edytowane z GUI
DEFAULT_VARIABLES = ["RHO", "WD", "WS", "PD", "TKE"]
HEIGHT_OPTIONS = [50, 100, 150, 200, 250]
# Microscale API obsługuje tylko te wysokości
MICRO_HEIGHTS_ALLOWED = {50, 100, 200}


# ── Worker thread ─────────────────────────────────────────────────────────────
class ScriptWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, script_path, params, timeout=7200):
        super().__init__()
        self.script_path = str(script_path)
        self.params = params
        self.timeout = timeout
        self._proc = None
        self._stopped = False

    def stop(self):
        """Przerwij działający proces."""
        self._stopped = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.kill()

    def run(self):
        params_file = None
        try:
            if not rx.is_remote() and not os.path.isfile(PYTHON_EXE):
                raise RuntimeError(f"Brak PYTHON_EXE: {PYTHON_EXE}")
            if not rx.is_remote() and not os.path.isfile(self.script_path):
                raise RuntimeError(f"Brak skryptu: {self.script_path}")

            self.log.emit(f"Uruchamiam: {os.path.basename(self.script_path)}")
            self.log.emit(f"  python: {PYTHON_EXE}")
            self.log.emit(f"  script: {self.script_path}")

            fd, params_file = tempfile.mkstemp(suffix=".json", prefix="nc_params_")
            os.close(fd)
            with open(params_file, "w", encoding="utf-8") as f:
                json.dump(self.params, f, ensure_ascii=False)

            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)

            cmd = [PYTHON_EXE, "-u", self.script_path, params_file]

            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=WORKERS_DIR,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                )

            self._proc = rx.Popen(cmd, **popen_kwargs)
            proc = self._proc

            result_json = None
            collecting_json = False
            json_lines = []

            for line in proc.stdout:
                if self._stopped:
                    break
                line = line.rstrip("\n\r")
                if line.strip() == "__RESULT_JSON__":
                    collecting_json = True
                    continue
                if collecting_json:
                    json_lines.append(line)
                    continue
                if line.strip():
                    self.log.emit(line)

            if self._stopped:
                with contextlib.suppress(Exception):
                    proc.kill()
                self.log.emit("⏹  Przerwano przez użytkownika.")
                self.finished.emit({"status": "cancelled"})
                return

            ret = proc.wait(timeout=self.timeout)
            if ret != 0 and not json_lines:
                raise RuntimeError(
                    f"Skrypt zakończył się kodem {ret}. "
                    f"Sprawdź czy {PYTHON_EXE} ma xarray/aiohttp/geopandas."
                )

            if json_lines:
                try:
                    result_json = json.loads("\n".join(json_lines))
                except json.JSONDecodeError:
                    result_json = {"status": "ok", "raw": "\n".join(json_lines)}
            else:
                result_json = {"status": "ok"}

            self.finished.emit(result_json)

        except subprocess.TimeoutExpired:
            self.error.emit(f"Timeout po {self.timeout}s")
        except Exception as e:
            if self._stopped:
                self.finished.emit({"status": "cancelled"})
            else:
                self.error.emit(str(e))
        finally:
            self._proc = None
            if params_file and os.path.isfile(params_file):
                with contextlib.suppress(OSError):
                    os.remove(params_file)


# ── Główne okno ───────────────────────────────────────────────────────────────
class NCDataDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.turbines = {"lons": [], "lats": [], "ids": [], "labels": []}
        self._thread = None
        self._worker = None
        self._last_shp = None
        self._label_field = None          # wybrana kolumna z nazwami
        self._label_field_is_fid = False  # True = używamy FID

        self.setWindowTitle("NEWA – Pobieranie i analiza danych turbin")
        self.setMinimumWidth(640)
        self.setMinimumHeight(720)
        self._build_ui()
        self._refresh_layers()
        # Nie wczytuj warstwy automatycznie – użytkownik wybiera warstwę/SHP ręcznie

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Warstwa z projektu LUB plik SHP z dysku ───────────────────────────
        grp_layer = QGroupBox("WTGs")
        gl_layer = QVBoxLayout(grp_layer)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Warstwa:"))
        self.combo_layer = QComboBox()
        self.combo_layer.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo_layer.currentIndexChanged.connect(self._on_layer_changed)
        row1.addWidget(self.combo_layer, 1)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.setFixedWidth(70)
        btn_refresh.clicked.connect(self._refresh_layers)
        row1.addWidget(btn_refresh)
        gl_layer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("SHP:"))
        self.edit_shp = QLineEdit("")
        self.edit_shp.setPlaceholderText(r"opcjonalnie: plik .shp z dysku")
        row2.addWidget(self.edit_shp, 1)
        btn_shp = QPushButton("…")
        btn_shp.setFixedWidth(30)
        btn_shp.clicked.connect(self._browse_shp)
        row2.addWidget(btn_shp)
        gl_layer.addLayout(row2)

        root.addWidget(grp_layer)

        self.lbl_header = QLabel("Turbin: 0")
        self.lbl_header.setStyleSheet(
            "padding: 6px; background:#e3f2fd; border-radius:4px;"
        )
        root.addWidget(self.lbl_header)

        # Zakładki
        tabs = QTabWidget()
        tabs.addTab(self._build_download_tab(), "1. Pobieranie danych")
        tabs.addTab(self._build_analyze_tab(), "2. Analiza")
        root.addWidget(tabs)

        # Log
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(160)
        self.log_box.setFont(QFont("Consolas", 8))
        self.log_box.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        root.addWidget(self.log_box)

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Przyciski dolne
        btn_row = QHBoxLayout()
        self.btn_stop = QPushButton("⏹  Przerwij")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#c62828; color:white; font-weight:bold; "
            "padding:8px 16px; border-radius:4px; }"
            "QPushButton:hover { background:#e53935; }"
            "QPushButton:disabled { background:#aaa; color:#eee; }"
        )
        self.btn_stop.clicked.connect(self._on_stop)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    # ── Warstwy / SHP ─────────────────────────────────────────────────────────
    def _refresh_layers(self):
        current_id = self.combo_layer.currentData()
        self.combo_layer.blockSignals(True)
        self.combo_layer.clear()
        self.combo_layer.addItem("— wybierz warstwę punktową —", userData=None)

        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:
                continue
            self.combo_layer.addItem(layer.name(), userData=layer.id())

        # Przy odświeżeniu zachowaj poprzedni wybór (jeśli nadal na liście),
        # ale NIE przełączaj automatycznie na aktywną warstwę projektu.
        idx = 0
        if current_id:
            for i in range(self.combo_layer.count()):
                if self.combo_layer.itemData(i) == current_id:
                    idx = i
                    break
        self.combo_layer.setCurrentIndex(idx)
        self.combo_layer.blockSignals(False)

    def _find_userlabel_field(self, layer):
        """Zwraca nazwę pola USERLABEL (case-insensitive) lub None."""
        if layer is None:
            return None
        for f in layer.fields():
            if f.name().upper() == "USERLABEL":
                return f.name()
        return None

    def _attribute_field_names(self, layer):
        """Lista nazw atrybutów (bez geometrii)."""
        if layer is None:
            return []
        return [f.name() for f in layer.fields()]

    def _ask_label_field(self, layer):
        """
        Wybór kolumny z nazwami turbin.
        1) Jeśli jest USERLABEL – użyj jej.
        2) W przeciwnym razie zapytaj użytkownika (lista kolumn + opcja FID).
        Zwraca nazwę pola albo None (= użyj feature id / FID).
        """
        if layer is None:
            return None

        auto = self._find_userlabel_field(layer)
        if auto:
            self._label_field = auto
            self._label_field_is_fid = False
            return auto

        fields = self._attribute_field_names(layer)
        choices = list(fields) + ["[FID – numer obiektu]"]
        # zapamiętaj poprzedni wybór, jeśli nadal dostępny
        default = getattr(self, "_label_field", None)
        if default and default in choices:
            start = choices.index(default)
        else:
            start = 0

        chosen, ok = QInputDialog.getItem(
            self,
            "Kolumna z nazwami turbin",
            "Warstwa nie ma kolumny USERLABEL.\n"
            "Wybierz kolumnę, która zawiera nazwy / identyfikatory turbin\n"
            "(będą używane w nazwach plików i raportach):",
            choices,
            start,
            False,
        )
        if not ok or not chosen:
            # anulowano – fallback FID
            self._label_field = None
            self._label_field_is_fid = True
            return None

        if chosen.startswith("[FID"):
            self._label_field = None
            self._label_field_is_fid = True
            return None

        self._label_field = chosen
        self._label_field_is_fid = False
        return chosen

    def _read_turbines_from_layer(self, layer, label_field=None):
        """
        Odczyt punktów + etykiet.
        label_field: nazwa kolumny albo None (= feature id).
        """
        turbines = {"lons": [], "lats": [], "ids": [], "labels": []}
        if layer is None:
            return turbines
        crs_src = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        t_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())

        # jeśli nie podano – spróbuj USERLABEL, inaczej to co wybrał user
        if label_field is None:
            label_field = getattr(self, "_label_field", None)
            if label_field is None and not getattr(self, "_label_field_is_fid", False):
                label_field = self._find_userlabel_field(layer)

        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            pt = geom.asPoint()
            p_wgs = t_wgs.transform(pt)
            turbines["lons"].append(round(p_wgs.x(), 6))
            turbines["lats"].append(round(p_wgs.y(), 6))
            turbines["ids"].append(feat.id())

            if label_field:
                try:
                    val = feat[label_field]
                except Exception:
                    val = None
                if val is None or str(val).strip() == "":
                    label = str(feat.id())
                else:
                    label = str(val).strip()
            else:
                label = str(feat.id())
            turbines["labels"].append(label)
        return turbines

    def _on_layer_changed(self):
        # Wybór warstwy z projektu nadpisuje ścieżkę SHP
        lid = self.combo_layer.currentData()
        layer = QgsProject.instance().mapLayer(lid) if lid else None
        if layer is None:
            # jeśli jest ścieżka SHP – nie czyść punktów
            if self.edit_shp.text().strip() and self.turbines.get("lons"):
                return
            self.turbines = {"lons": [], "lats": [], "ids": [], "labels": []}
            self.lbl_header.setText("Turbin: 0")
            return
        self.edit_shp.blockSignals(True)
        self.edit_shp.setText("")
        self.edit_shp.blockSignals(False)

        label_field = self._ask_label_field(layer)
        self.turbines = self._read_turbines_from_layer(layer, label_field=label_field)
        n = len(self.turbines["lons"])
        if label_field:
            lbl_info = f"nazwy: <b>{label_field}</b>"
        else:
            lbl_info = "nazwy: <b>FID</b>"
        self.lbl_header.setText(
            f"<b>Warstwa:</b> {layer.name()}  ·  <b>Turbin:</b> {n}  ·  {lbl_info}"
        )
        sample = self.turbines["labels"][:5]
        self._log(
            f"📂 Warstwa „{layer.name()}”: {n} turbin, "
            f"kolumna nazw: {label_field or 'FID'}  "
            f"(przykł.: {sample}{'…' if n > 5 else ''})"
        )

    def _browse_shp(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik SHP z turbinami",
            self.edit_shp.text() or WORKERS_DIR,
            "Shapefile (*.shp);;Wszystkie pliki (*.*)",
        )
        if path:
            self.edit_shp.setText(path)
            self._load_shp(path)

    def _load_shp(self, path):
        """Wczytuje punkty z SHP z dysku (bez dodawania do projektu QGIS)."""
        self.turbines = {"lons": [], "lats": [], "ids": [], "labels": []}
        if not path or not os.path.isfile(path):
            self.lbl_header.setText("Turbin: 0")
            return

        layer = QgsVectorLayer(path, "nc_turbines_tmp", "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Błąd", f"Nie można wczytać SHP:\n{path}")
            self.lbl_header.setText("Turbin: 0 – błąd odczytu")
            return
        if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:
            QMessageBox.warning(self, "Błąd", "Plik SHP musi zawierać geometrię punktową.")
            self.lbl_header.setText("Turbin: 0 – zła geometria")
            return

        # Odznacz warstwę z projektu – źródłem jest SHP
        self.combo_layer.blockSignals(True)
        self.combo_layer.setCurrentIndex(0)
        self.combo_layer.blockSignals(False)

        label_field = self._ask_label_field(layer)
        self.turbines = self._read_turbines_from_layer(layer, label_field=label_field)
        n = len(self.turbines["lons"])
        if label_field:
            lbl_info = f"nazwy: <b>{label_field}</b>"
        else:
            lbl_info = "nazwy: <b>FID</b>"
        self.lbl_header.setText(
            f"<b>SHP:</b> {os.path.basename(path)}  ·  <b>Turbin:</b> {n}  ·  {lbl_info}"
        )
        sample = self.turbines["labels"][:5]
        self._log(
            f"📂 Wczytano {n} punktów z {path}, "
            f"kolumna nazw: {label_field or 'FID'}  "
            f"(przykł.: {sample}{'…' if n > 5 else ''})"
        )
        self._last_shp = path

    # ── Zakładka pobierania ───────────────────────────────────────────────────
    def _build_download_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        # Katalog wyjściowy
        grp_out = QGroupBox("Katalog wynikowy (.nc)")
        gl = QHBoxLayout(grp_out)
        self.edit_outdir = QLineEdit(DEFAULT_OUTPUT)
        gl.addWidget(self.edit_outdir)
        btn = QPushButton("…")
        btn.setFixedWidth(30)
        btn.clicked.connect(self._browse_outdir)
        gl.addWidget(btn)
        lay.addWidget(grp_out)

        # Źródło danych: meso / micro
        grp_src = QGroupBox("Źródło danych NEWA")
        gls = QHBoxLayout(grp_src)
        self.cb_meso = QCheckBox("Mesoscale - time series")
        self.cb_meso.setChecked(True)
        self.cb_micro = QCheckBox("Microscale -  WAsP")
        self.cb_micro.setChecked(True)
        gls.addWidget(self.cb_meso)
        gls.addWidget(self.cb_micro)
        gls.addStretch()
        info_src = QLabel(
            ""
        )
        info_src.setStyleSheet("color:#555; font-size:11px;")
        gls.addWidget(info_src)
        lay.addWidget(grp_src)

        # Wysokości
        grp_h = QGroupBox("Wysokości pomiaru [m]")
        glh = QHBoxLayout(grp_h)
        self.height_checks = {}
        for h in HEIGHT_OPTIONS:
            cb = QCheckBox(str(h))
            cb.setChecked(h in (100, 150, 200))
            self.height_checks[h] = cb
            glh.addWidget(cb)
        glh.addStretch()
        info_h = QLabel("")
        info_h.setStyleSheet("color:#555; font-size:11px;")
        glh.addWidget(info_h)
        lay.addWidget(grp_h)

        # Okres – tylko pełne dni, z kalendarzem (dotyczy meso)
        grp_t = QGroupBox("Okres danych meso")
        glt = QGridLayout(grp_t)
        glt.addWidget(QLabel("Od:"), 0, 0)
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setDate(QDate(2000, 1, 1))
        self.date_start.setMinimumDate(QDate(1979, 1, 1))
        self.date_start.setMaximumDate(QDate(2030, 12, 31))
        glt.addWidget(self.date_start, 0, 1)

        glt.addWidget(QLabel("Do:"), 0, 2)
        self.date_stop = QDateEdit()
        self.date_stop.setCalendarPopup(True)
        self.date_stop.setDisplayFormat("yyyy-MM-dd")
        self.date_stop.setDate(QDate(2022, 12, 31))
        self.date_stop.setMinimumDate(QDate(1979, 1, 1))
        self.date_stop.setMaximumDate(QDate(2030, 12, 31))
        glt.addWidget(self.date_stop, 0, 3)
        lay.addWidget(grp_t)

        # Przycisk pobierania
        self.btn_download = QPushButton("▶  Pobierz dane NEWA")
        self.btn_download.setStyleSheet(
            "QPushButton { background:#1565c0; color:white; font-weight:bold; "
            "padding:8px 20px; border-radius:4px; }"
            "QPushButton:hover { background:#1976d2; }"
            "QPushButton:disabled { background:#aaa; }"
        )
        self.btn_download.clicked.connect(self._on_download)
        lay.addWidget(self.btn_download)
        lay.addStretch()
        return w

    # ── Zakładka analizy ──────────────────────────────────────────────────────
    def _build_analyze_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        grp_d = QGroupBox("Katalog z plikami .nc")
        gld = QHBoxLayout(grp_d)
        self.edit_datadir = QLineEdit(DEFAULT_OUTPUT)
        gld.addWidget(self.edit_datadir)
        btn = QPushButton("…")
        btn.setFixedWidth(30)
        btn.clicked.connect(self._browse_datadir)
        gld.addWidget(btn)
        lay.addWidget(grp_d)

        grp_m = QGroupBox("Tryb analizy")
        glm = QVBoxLayout(grp_m)
        self.rb_single = QRadioButton("Każda turbina osobno (single)")
        self.rb_average = QRadioButton("Średnia z całej farmy (average)")
        self.rb_single.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.rb_single)
        bg.addButton(self.rb_average)
        glm.addWidget(self.rb_single)
        glm.addWidget(self.rb_average)
        info = QLabel(
            "• single – generuje wykresy dla każdej turbiny osobno\n"
            "• average – uśrednia wszystkie turbiny i generuje jeden zestaw wykresów\n"
            "Uwaga: analiza działa na plikach *_meso.nc time series."
        )
        info.setStyleSheet("color:#555; font-size:11px;")
        glm.addWidget(info)
        lay.addWidget(grp_m)

        grp_h = QGroupBox("Wysokość analizy [m]")
        glh = QHBoxLayout(grp_h)
        self.combo_height = QComboBox()
        for h in HEIGHT_OPTIONS:
            self.combo_height.addItem(str(h), userData=h)
        self.combo_height.setCurrentText("150")
        glh.addWidget(self.combo_height)
        glh.addWidget(QLabel(""))
        glh.addStretch()
        lay.addWidget(grp_h)

        grp_p = QGroupBox("Katalog wykresów (opcjonalnie)")
        glp = QHBoxLayout(grp_p)
        self.edit_plotsdir = QLineEdit("")
        self.edit_plotsdir.setPlaceholderText("domyślnie: <katalog danych>/wykresy")
        glp.addWidget(self.edit_plotsdir)
        btnp = QPushButton("…")
        btnp.setFixedWidth(30)
        btnp.clicked.connect(self._browse_plotsdir)
        glp.addWidget(btnp)
        lay.addWidget(grp_p)

        self.btn_analyze = QPushButton("▶  Uruchom analizę")
        self.btn_analyze.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold; "
            "padding:8px 20px; border-radius:4px; }"
            "QPushButton:hover { background:#388e3c; }"
            "QPushButton:disabled { background:#aaa; }"
        )
        self.btn_analyze.clicked.connect(self._on_analyze)
        lay.addWidget(self.btn_analyze)
        lay.addStretch()
        return w

    # ── Browse helpers ────────────────────────────────────────────────────────
    def _browse_outdir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Katalog wynikowy", self.edit_outdir.text()
        )
        if path:
            self.edit_outdir.setText(path)
            if not self.edit_datadir.text() or self.edit_datadir.text() == DEFAULT_OUTPUT:
                self.edit_datadir.setText(path)

    def _browse_datadir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Katalog z plikami .nc", self.edit_datadir.text()
        )
        if path:
            self.edit_datadir.setText(path)

    def _browse_plotsdir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Katalog wykresów",
            self.edit_plotsdir.text() or self.edit_datadir.text()
        )
        if path:
            self.edit_plotsdir.setText(path)

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _date_range_iso(self):
        """Pełne dni → API: start 00:00:00, stop 23:30:00."""
        d0 = self.date_start.date()
        d1 = self.date_stop.date()
        if d1 < d0:
            d0, d1 = d1, d0
        start = f"{d0.toString('yyyy-MM-dd')}T00:00:00"
        stop = f"{d1.toString('yyyy-MM-dd')}T23:30:00"
        return start, stop

    # ── Pobieranie ────────────────────────────────────────────────────────────
    def _on_download(self):
        if not rx.is_remote() and not os.path.isfile(PYTHON_EXE):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono interpretera Python:\n{PYTHON_EXE}\n\n"
                f"Ustaw PYTHON_EXE na początku NC_Data_Q_GUI.py."
            )
            return
        if not rx.is_remote() and not os.path.isfile(DOWNLOADER):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono NC_Downloader.py w:\n{WORKERS_DIR}"
            )
            return

        do_meso = self.cb_meso.isChecked()
        do_micro = self.cb_micro.isChecked()
        if not do_meso and not do_micro:
            QMessageBox.warning(
                self, "Błąd",
                "Zaznacz co najmniej jedno źródło: Mesoscale lub Microscale."
            )
            return

        # Odśwież z pola ścieżki (na wypadek ręcznej edycji)
        shp = self.edit_shp.text().strip()
        if shp and (not self.turbines["lons"] or shp != getattr(self, "_last_shp", None)):
            self._load_shp(shp)
            self._last_shp = shp

        if not self.turbines["lons"]:
            QMessageBox.warning(
                self, "Błąd",
                "Wskaż warstwę punktową lub plik SHP z turbinami."
            )
            return

        heights = [h for h, cb in self.height_checks.items() if cb.isChecked()]
        if not heights:
            QMessageBox.warning(self, "Błąd", "Wybierz co najmniej jedną wysokość.")
            return

        # ostrzeżenie jeśli micro włączone, a żadna z dozwolonych wysokości nie jest zaznaczona
        if do_micro:
            micro_h = [h for h in heights if h in MICRO_HEIGHTS_ALLOWED]
            if not micro_h:
                QMessageBox.warning(
                    self, "Uwaga",
                    "Microscale obsługuje tylko wysokości 50, 100 i 200 m.\n"
                    "Żadna z nich nie jest zaznaczona – micro nie zostanie pobrane "
                    "dla wybranych wysokości (użyte zostanie 100 m jako fallback)."
                )

        out_dir = self.edit_outdir.text().strip() or DEFAULT_OUTPUT
        os.makedirs(out_dir, exist_ok=True)

        points = []
        labels = self.turbines.get("labels") or []
        for i, (lon, lat) in enumerate(zip(self.turbines["lons"], self.turbines["lats"])):
            pid = self.turbines["ids"][i] if i < len(self.turbines["ids"]) else i
            label = labels[i] if i < len(labels) else str(pid)
            points.append({
                "id": pid,
                "label": label,
                "longitude": lon,
                "latitude": lat,
            })

        dt_start, dt_stop = self._date_range_iso()

        params = {
            "output_dir": out_dir,
            "heights": heights,
            "variables": list(DEFAULT_VARIABLES),  # meso – jak w oryginale
            "dt_start": dt_start,
            "dt_stop": dt_stop,
            "points": points,
            "download_meso": do_meso,
            "download_micro": do_micro,
        }

        self._log("─" * 50)
        self._log(f"Pobieranie {len(points)} punktów → {out_dir}")
        self._log(f"Źródła:  meso={do_meso}, micro={do_micro}")
        self._log(f"Wysokości: {heights}")
        if do_micro:
            mh = [h for h in heights if h in MICRO_HEIGHTS_ALLOWED]
            self._log(f"  → micro użyje: {mh or [100]}")
        self._log(f"Zmienne meso: {DEFAULT_VARIABLES}")
        self._log(f"Okres meso:  {dt_start} → {dt_stop}")
        sample_labels = [p["label"] for p in points[:5]]
        self._log(f"Przykł. etykiety: {sample_labels}{'…' if len(points) > 5 else ''}")

        self._run_script(DOWNLOADER, params, self._on_download_finished)

    def _on_download_finished(self, result):
        self._log("─" * 50)
        if result.get("status") == "cancelled":
            self._log("⏹  Pobieranie przerwane.")
            return
        if result.get("status") == "ok":
            self._log(
                f"✓  Gotowe. Sukces: {result.get('success', '?')}, "
                f"istniały: {result.get('exists', '?')}, "
                f"błędy: {result.get('errors', '?')}"
            )
            if "meso_ok" in result or "micro_ok" in result:
                self._log(
                    f"   Meso OK: {result.get('meso_ok', '?')}  |  "
                    f"Micro OK: {result.get('micro_ok', '?')}"
                )
            if result.get("output_dir"):
                self.edit_datadir.setText(result["output_dir"])
        else:
            self._log(f"⚠  Wynik: {result}")

    # ── Analiza ───────────────────────────────────────────────────────────────
    def _on_analyze(self):
        if not rx.is_remote() and not os.path.isfile(PYTHON_EXE):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono interpretera Python:\n{PYTHON_EXE}"
            )
            return
        if not rx.is_remote() and not os.path.isfile(ANALYZER):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono NC_Analizer.py w:\n{WORKERS_DIR}"
            )
            return

        data_dir = self.edit_datadir.text().strip()
        if not data_dir or not Path(data_dir).is_dir():
            QMessageBox.warning(self, "Błąd", "Podaj prawidłowy katalog z plikami .nc")
            return

        mode = "single" if self.rb_single.isChecked() else "average"
        height = self.combo_height.currentData()
        plots_dir = self.edit_plotsdir.text().strip() or None

        params = {
            "data_dir": data_dir,
            "mode": mode,
            "target_height": height,
        }
        if plots_dir:
            params["plots_dir"] = plots_dir

        self._log("─" * 50)
        self._log(f"Analiza: tryb={mode}, wysokość={height} m")
        self._log(f"Katalog: {data_dir}")

        self._run_script(ANALYZER, params, self._on_analyze_finished)

    def _on_analyze_finished(self, result):
        self._log("─" * 50)
        if result.get("status") == "cancelled":
            self._log("⏹  Analiza przerwana.")
            return
        if result.get("status") == "ok":
            self._log(f"✓  Analiza zakończona. Przetworzono: {result.get('processed', [])}")
            plots = result.get("plots_dir")
            if plots:
                self._log(f"  Wykresy: {plots}")
                with contextlib.suppress(Exception):
                    open_path(plots)
        else:
            self._log(f"✗  Błąd: {result.get('message', result)}")

    # ── Uruchomienie / stop ───────────────────────────────────────────────────
    def _run_script(self, script_path, params, on_finished):
        self.btn_download.setEnabled(False)
        self.btn_analyze.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)

        self._thread = QThread()
        self._worker = ScriptWorker(script_path, params)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)

        self._thread.start()

    def _on_stop(self):
        if self._worker is not None:
            self._log("⏹  Żądanie przerwania…")
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    def _on_error(self, msg):
        self._log(f"✗  BŁĄD: {msg}")
        QMessageBox.critical(self, "Błąd", msg[:600])

    def _cleanup(self):
        self.btn_download.setEnabled(True)
        self.btn_analyze.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    print(f"[OK] PYTHON_EXE  = {PYTHON_EXE}  (istnieje: {os.path.isfile(PYTHON_EXE)})")
    print(f"[OK] WORKERS_DIR = {WORKERS_DIR}")
    print(f"     NC_Downloader.py: {os.path.isfile(DOWNLOADER)}")
    print(f"     NC_Analizer.py:   {os.path.isfile(ANALYZER)}")

    global _nc_data_dlg
    _nc_data_dlg = NCDataDialog(parent=iface.mainWindow())
    _nc_data_dlg.show()
    _nc_data_dlg.raise_()
    _nc_data_dlg.activateWindow()



if __name__ == '__main__':
    main()
