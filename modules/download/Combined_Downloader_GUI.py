"""
Combined_Downloader_GUI.py
Połączone pobieranie NMT (GUGiK) + NEWA (NC) w jednym oknie.
Po kliknięciu „Pobierz” oba źródła startują równolegle (dwa wątki).

Uruchom w edytorze Python QGIS.
Wymaga w tym samym katalogu:
  - NMT_Downloader_GUI.py  (logika NMT)
  - NC_Downloader.py       (worker NEWA)
"""

import os
import re
import sys
import json
import tempfile
import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
from WindLab.core import remote_exec as rx
from pathlib import Path

from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsPointXY,
)
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QLineEdit, QFileDialog, QMessageBox, QWidget,
    QRadioButton, QButtonGroup, QDateEdit, QInputDialog,
    QScrollArea, QApplication,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject, QDate
from qgis.PyQt.QtGui import QFont

from WindLab.core.paths import find_external_python
from WindLab.core.ui import (
    browse_button, layer_icon, hint_label, log_view, set_role, style_module_dialog,
)
import contextlib

# katalog z NC_Downloader.py / NMT_Downloader_GUI.py (launcher WindLab nadpisuje)
SCRIPT_DIR = ""

# Interpreter z pakietami (aiohttp, xarray, geopandas, …)
PYTHON_EXE = find_external_python()

# Jeśli uruchamiasz jako plik z dysku (nie wklejasz do edytora), spróbuj __file__
with contextlib.suppress(NameError):
    _here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(_here, "NC_Downloader.py")):
        SCRIPT_DIR = _here

sys.path.insert(0, SCRIPT_DIR)

# NMT – import logiki z istniejącego skryptu
from NMT_Downloader_GUI import (
    DownloadWorker as NMTDownloadWorker,
)

NC_DOWNLOADER = os.path.join(SCRIPT_DIR, "NC_Downloader.py")
NC_DEFAULT_OUTPUT = os.environ.get("WINDLAB_NC_DIR") or os.path.join(SCRIPT_DIR, "dane_turbin")
NMT_DEFAULT_OUTPUT = os.environ.get("WINDLAB_NMT_DIR") or os.path.join(SCRIPT_DIR, "dane_nmt")
if os.environ.get("AEP_PYTHON") and os.path.isfile(os.environ["AEP_PYTHON"]):
    PYTHON_EXE = os.environ["AEP_PYTHON"]

DEFAULT_VARIABLES = ["RHO", "WD", "WS", "PD", "TKE"]
HEIGHT_OPTIONS = [50, 100, 150, 200, 250]
MICRO_HEIGHTS_ALLOWED = {50, 100, 200}



# ── Mozaika NMT (GDAL) ────────────────────────────────────────────────────────
def mosaic_nmt_gdal(asc_paths, out_tif, remove_sources=True, log_cb=None):
    """
    Mozaika arkuszy NMT jak polecenie:
        gdalbuildvrt -overwrite -resolution average -a_srs EPSG:2180 -r nearest -input_file_list lista.txt mozaika.vrt
    a potem zapis rastra wirtualnego do GeoTIFF (kompresja, kafle, piramidy).
    Po udanym zapisie (remove_sources) usuwa pobrane arkusze i pliki pośrednie.
    Zwraca (ok: bool, message: str).
    """
    def log(m):
        if log_cb:
            log_cb(m)

    if not asc_paths:
        return False, "Brak arkuszy do połączenia"
    try:
        from osgeo import gdal
    except Exception as e:  # noqa: BLE001
        return False, f"Brak GDAL w Pythonie QGIS: {e}"
    gdal.UseExceptions()

    base = os.path.splitext(out_tif)[0]
    list_path = base + "_lista.txt"
    vrt_path = base + ".vrt"
    tmp_tif = base + "_tmp.tif"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(asc_paths) + "\n")
    log(f"  gdalbuildvrt -overwrite -resolution average -a_srs EPSG:2180 -r nearest "
        f"-input_file_list {os.path.basename(list_path)} {os.path.basename(vrt_path)}  ({len(asc_paths)} arkuszy)")
    try:
        if os.path.exists(vrt_path):
            os.remove(vrt_path)
        vrt = gdal.BuildVRT(vrt_path, asc_paths, options=gdal.BuildVRTOptions(
            resolution="average", outputSRS="EPSG:2180", resampleAlg="nearest"))
        if vrt is None:
            return False, "gdalbuildvrt nie utworzył rastra wirtualnego"
        vrt = None
        log("  zapis GeoTIFF…")
        ds = gdal.Translate(tmp_tif, vrt_path, options=gdal.TranslateOptions(
            format="GTiff", creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES",
                                             "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"]))
        if ds is None:
            return False, "Zapis GeoTIFF nie powiódł się"
        nx, ny = ds.RasterXSize, ds.RasterYSize
        ds.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])
        ds = None
        if os.path.exists(out_tif):
            os.remove(out_tif)
        os.replace(tmp_tif, out_tif)
    except Exception as e:  # noqa: BLE001
        for p in (tmp_tif,):
            with contextlib.suppress(OSError):
                if os.path.exists(p):
                    os.remove(p)
        return False, f"GDAL: {e}"

    removed = 0
    if remove_sources:
        for p in list(asc_paths) + [vrt_path, list_path]:
            for q in (p, p + ".aux.xml", os.path.splitext(p)[0] + ".prj"):
                try:
                    if os.path.exists(q) and os.path.abspath(q) != os.path.abspath(out_tif):
                        os.remove(q)
                        removed += 1
                except OSError as e:
                    log(f"  nie usunięto {os.path.basename(q)}: {e}")
    size_mb = os.path.getsize(out_tif) / (1024 * 1024)
    return True, (f"Zapisano: {out_tif}  ({size_mb:.1f} MB, {nx}×{ny})"
                  + (f"; usunięto {removed} plików pobranych/pośrednich" if remove_sources else ""))


# ── Worker NC (subprocess) ───────────────────────────────────────────────────
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

            self.log.emit(f"[NC] Uruchamiam: {os.path.basename(self.script_path)}")

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
                cwd=SCRIPT_DIR,
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
                    self.log.emit(f"[NC] {line}")

            if self._stopped:
                with contextlib.suppress(Exception):
                    proc.kill()
                self.log.emit("[NC] ⏹  Przerwano przez użytkownika.")
                self.finished.emit({"status": "cancelled"})
                return

            ret = proc.wait(timeout=self.timeout)
            if ret != 0 and not json_lines:
                raise RuntimeError(
                    f"Skrypt NC zakończył się kodem {ret}. "
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
            self.error.emit(f"[NC] Timeout po {self.timeout}s")
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


# ── Główne okno ──────────────────────────────────────────────────────────────
class CombinedDownloaderDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        # punkty wspólne
        self.points_wgs = []          # [(lon, lat), ...]
        self.points_2180 = []         # [QgsPointXY, ...]
        self.turbine_ids = []
        self.turbine_labels = []
        self._last_shp = None
        self._label_field = None
        self._label_field_is_fid = False

        # wątki
        self._nmt_thread = None
        self._nmt_worker = None
        self._nc_thread = None
        self._nc_worker = None
        self._nmt_done = True
        self._nc_done = True
        self._nmt_result = None
        self._nc_result = None

        self.setMinimumWidth(700)
        self.setMinimumHeight(600)
        self._build_ui()
        self._refresh_layers()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Warstwa / SHP (wspólne) ───────────────────────────────────────────
        grp_layer = QGroupBox("Punkty (turbiny / lokalizacje) – wspólne dla NMT i NEWA")
        gl = QVBoxLayout(grp_layer)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Warstwa:"))
        self.combo_layer = QComboBox()
        self.combo_layer.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo_layer.currentIndexChanged.connect(self._on_layer_changed)
        row1.addWidget(self.combo_layer, 1)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh_layers)
        row1.addWidget(btn_refresh)
        gl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("SHP:"))
        self.edit_shp = QLineEdit("")
        self.edit_shp.setPlaceholderText(r"opcjonalnie: plik .shp z dysku")
        row2.addWidget(self.edit_shp, 1)
        btn_shp = browse_button("Wskaż plik .shp")
        btn_shp.clicked.connect(self._browse_shp)
        row2.addWidget(btn_shp)
        gl.addLayout(row2)

        root.addWidget(grp_layer)

        self.lbl_header = hint_label("Punktów: 0")
        gl.addWidget(self.lbl_header)

        # ── Opcje NMT + NC (przewijanie zapewnia wspólny motyw) ───────────────
        scroll_w = QWidget()
        scroll_lay = QVBoxLayout(scroll_w)
        scroll_lay.setContentsMargins(0, 0, 0, 0)
        scroll_lay.setSpacing(8)

        # ===== NMT =====
        self.cb_enable_nmt = QCheckBox("Pobieraj NMT (GUGiK)")
        self.cb_enable_nmt.setChecked(True)
        self.cb_enable_nmt.setStyleSheet("font-weight:600;")
        scroll_lay.addWidget(self.cb_enable_nmt)
        from WindLab.core import demo
        if demo.DEMO:
            self.cb_enable_nmt.setChecked(False)
            self.cb_enable_nmt.setEnabled(False)
            self.cb_enable_nmt.setText(f"Pobieraj NMT (GUGiK) – {demo.NOTE}")
            scroll_lay.addWidget(hint_label(
                "Wysokości terenu do hałasu, migotania cienia i wake pobierane są na bieżąco "
                "z usługi GUGiK online – arkusze NMT nie są potrzebne."))

        grp_nmt = QGroupBox("Opcje NMT")
        gn = QVBoxLayout(grp_nmt)

        row_out_nmt = QHBoxLayout()
        row_out_nmt.addWidget(QLabel("Katalog NMT:"))
        self.edit_nmt_outdir = QLineEdit(NMT_DEFAULT_OUTPUT)
        row_out_nmt.addWidget(self.edit_nmt_outdir)
        btn_nmt_out = browse_button("Wybierz katalog NMT")
        btn_nmt_out.clicked.connect(lambda: self._browse_dir(self.edit_nmt_outdir))
        row_out_nmt.addWidget(btn_nmt_out)
        gn.addLayout(row_out_nmt)

        row_h = QHBoxLayout()
        row_h.addWidget(QLabel("Układ:"))
        self.rb_evrf = QRadioButton("EVRF2007 (1 m + 5 m)")
        self.rb_kron = QRadioButton("KRON86-NH")
        self.rb_evrf.setChecked(True)
        bg_h = QButtonGroup(self)
        bg_h.addButton(self.rb_evrf)
        bg_h.addButton(self.rb_kron)
        row_h.addWidget(self.rb_evrf)
        row_h.addWidget(self.rb_kron)
        row_h.addStretch()
        gn.addLayout(row_h)

        row_f = QHBoxLayout()
        self.cb_arc = QCheckBox("ARC/INFO ASCII GRID")
        self.cb_xyz = QCheckBox("ASCII XYZ GRID")
        self.cb_arc.setChecked(True)
        self.cb_xyz.setChecked(False)
        row_f.addWidget(self.cb_arc)
        row_f.addWidget(self.cb_xyz)
        row_f.addWidget(QLabel("(odznacz oba = wszystkie)"))
        row_f.addStretch()
        gn.addLayout(row_f)

        self.cb_newest = QCheckBox("Tylko najnowszy arkusz")
        self.cb_newest.setChecked(True)
        gn.addWidget(self.cb_newest)

        self.cb_merge_asc = QCheckBox("Połącz arkusze w jeden GeoTIFF (GDAL) i usuń pobrane arkusze ASC")
        self.cb_merge_asc.setChecked(True)
        self.cb_merge_asc.setToolTip(
            "Po pobraniu: gdalbuildvrt (-resolution average, EPSG:2180, nearest) ze wszystkich .asc\n"
            "z katalogu NMT, zapis do <projekt>_NMT.tif w miejsce pobranych arkuszy."
        )
        gn.addWidget(self.cb_merge_asc)

        scroll_lay.addWidget(grp_nmt)
        grp_nmt.setVisible(not demo.DEMO)

        # ===== NC / NEWA =====
        self.cb_enable_nc = QCheckBox("Pobieraj NEWA (mesoscale + microscale)")
        self.cb_enable_nc.setChecked(True)
        self.cb_enable_nc.setStyleSheet("font-weight:600;")
        scroll_lay.addWidget(self.cb_enable_nc)

        grp_nc = QGroupBox("Opcje NEWA")
        gnc = QVBoxLayout(grp_nc)

        row_out_nc = QHBoxLayout()
        row_out_nc.addWidget(QLabel("Katalog NC:"))
        self.edit_nc_outdir = QLineEdit(NC_DEFAULT_OUTPUT)
        row_out_nc.addWidget(self.edit_nc_outdir)
        btn_nc_out = browse_button("Wybierz katalog NC")
        btn_nc_out.clicked.connect(lambda: self._browse_dir(self.edit_nc_outdir))
        row_out_nc.addWidget(btn_nc_out)
        gnc.addLayout(row_out_nc)

        row_src = QHBoxLayout()
        self.cb_meso = QCheckBox("Mesoscale – time series")
        self.cb_meso.setChecked(True)
        self.cb_micro = QCheckBox("Microscale – WAsP")
        self.cb_micro.setChecked(True)
        row_src.addWidget(self.cb_meso)
        row_src.addWidget(self.cb_micro)
        row_src.addStretch()
        gnc.addLayout(row_src)

        row_ht = QHBoxLayout()
        row_ht.addWidget(QLabel("Wysokości [m]:"))
        self.height_checks = {}
        for h in HEIGHT_OPTIONS:
            cb = QCheckBox(str(h))
            cb.setChecked(h in (100, 150, 200))
            self.height_checks[h] = cb
            row_ht.addWidget(cb)
        row_ht.addStretch()
        gnc.addLayout(row_ht)

        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("Okres meso:"))
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setDate(QDate(2000, 1, 1))
        self.date_start.setMinimumDate(QDate(1979, 1, 1))
        self.date_start.setMaximumDate(QDate(2030, 12, 31))
        row_t.addWidget(self.date_start)
        row_t.addWidget(QLabel("–"))
        self.date_stop = QDateEdit()
        self.date_stop.setCalendarPopup(True)
        self.date_stop.setDisplayFormat("yyyy-MM-dd")
        self.date_stop.setDate(QDate(2022, 12, 31))
        self.date_stop.setMinimumDate(QDate(1979, 1, 1))
        self.date_stop.setMaximumDate(QDate(2030, 12, 31))
        row_t.addWidget(self.date_stop)
        row_t.addStretch()
        gnc.addLayout(row_t)

        scroll_lay.addWidget(grp_nc)
        root.addWidget(scroll_w)

        # ── Przycisk pobierania ───────────────────────────────────────────────
        self.btn_download = set_role(QPushButton("Pobierz dane NEWA" if demo.DEMO else "Pobierz dane (NMT + NEWA)"), "primary")
        self.btn_download.clicked.connect(self._on_download)

        # Log
        self.log_box = log_view(min_height=130, max_height=160)
        root.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Dolne przyciski
        btn_row = QHBoxLayout()
        self.btn_stop = set_role(QPushButton("Przerwij wszystko"), "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_download)
        root.addLayout(btn_row)

        style_module_dialog(self, "download", "Pobieranie danych",
                            "NEWA (mesoscale + microscale)" if demo.DEMO else "NMT (GUGiK) + NEWA",
                            footer_items=3)  # log, postęp, przyciski

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
            self.combo_layer.addItem(layer_icon(layer), layer.name(), userData=layer.id())

        idx = 0
        if current_id:
            for i in range(self.combo_layer.count()):
                if self.combo_layer.itemData(i) == current_id:
                    idx = i
                    break
        self.combo_layer.setCurrentIndex(idx)
        self.combo_layer.blockSignals(False)

    def _find_userlabel_field(self, layer):
        if layer is None:
            return None
        for f in layer.fields():
            if f.name().upper() == "USERLABEL":
                return f.name()
        return None

    def _attribute_field_names(self, layer):
        if layer is None:
            return []
        return [f.name() for f in layer.fields()]

    def _ask_label_field(self, layer):
        if layer is None:
            return None
        preset = getattr(self, "_label_field", None)
        if preset and not getattr(self, "_label_field_is_fid", False):
            fields = [f.name() for f in layer.fields()]
            lower = {f.lower(): f for f in fields}
            if preset in fields:
                return preset
            if preset.lower() in lower:
                self._label_field = lower[preset.lower()]
                return self._label_field
        auto = self._find_userlabel_field(layer)
        if auto:
            self._label_field = auto
            self._label_field_is_fid = False
            return auto

        fields = self._attribute_field_names(layer)
        choices = list(fields) + ["[FID – numer obiektu]"]
        default = getattr(self, "_label_field", None)
        if default and default in choices:
            start = choices.index(default)
        else:
            start = 0

        chosen, ok = QInputDialog.getItem(
            self,
            "Kolumna z nazwami turbin",
            "Wybierz kolumnę z etykietami turbin (dla plików NEWA):",
            choices,
            start,
            False,
        )
        if not ok:
            self._label_field = None
            self._label_field_is_fid = True
            return None
        if chosen == "[FID – numer obiektu]":
            self._label_field = None
            self._label_field_is_fid = True
            return None
        self._label_field = chosen
        self._label_field_is_fid = False
        return chosen

    def _read_points(self, layer, label_field=None):
        """Zwraca (points_wgs, points_2180, ids, labels)."""
        if layer is None or not layer.isValid():
            return [], [], [], []

        crs_src = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_2180 = QgsCoordinateReferenceSystem("EPSG:2180")
        xform_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
        xform_2180 = QgsCoordinateTransform(crs_src, crs_2180, QgsProject.instance())

        points_wgs = []
        points_2180 = []
        ids = []
        labels = []

        use_fid = label_field is None
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.GeometryType.PointGeometry:
                continue
            pt = geom.asPoint()
            pt_wgs = xform_wgs.transform(pt)
            pt_2180 = xform_2180.transform(pt)
            points_wgs.append((pt_wgs.x(), pt_wgs.y()))
            points_2180.append(QgsPointXY(pt_2180))
            fid = feat.id()
            ids.append(fid)
            if use_fid:
                labels.append(str(fid))
            else:
                val = feat.attribute(label_field)
                labels.append(str(val) if val is not None else str(fid))

        return points_wgs, points_2180, ids, labels

    def _on_layer_changed(self):
        layer_id = self.combo_layer.currentData()
        if not layer_id:
            self.points_wgs = []
            self.points_2180 = []
            self.turbine_ids = []
            self.turbine_labels = []
            self.lbl_header.setText("Punktów: 0")
            return

        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return

        label_field = self._ask_label_field(layer)
        self.points_wgs, self.points_2180, self.turbine_ids, self.turbine_labels = \
            self._read_points(layer, label_field)

        self.edit_shp.clear()
        self._last_shp = None
        n = len(self.points_wgs)
        lbl_info = f" (etykieta: {label_field or 'FID'})" if n else ""
        self.lbl_header.setText(f"Punktów: {n}{lbl_info}")

    def _browse_shp(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Wybierz plik SHP", "", "Shapefile (*.shp)"
        )
        if path:
            self.edit_shp.setText(path)
            self._load_shp(path)

    def _load_shp(self, path):
        layer = QgsVectorLayer(path, "tmp_shp", "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Błąd", f"Nie można wczytać: {path}")
            return
        if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:
            QMessageBox.warning(self, "Błąd", "Plik musi zawierać geometrię punktową.")
            return

        label_field = self._ask_label_field(layer)
        self.points_wgs, self.points_2180, self.turbine_ids, self.turbine_labels = \
            self._read_points(layer, label_field)
        self._last_shp = path
        self.combo_layer.setCurrentIndex(0)
        n = len(self.points_wgs)
        lbl_info = f" (etykieta: {label_field or 'FID'})" if n else ""
        self.lbl_header.setText(f"Punktów: {n}{lbl_info}  [SHP]")

    def _browse_dir(self, line_edit):
        path = QFileDialog.getExistingDirectory(
            self, "Wybierz katalog", line_edit.text()
        )
        if path:
            line_edit.setText(path)

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _date_range_iso(self):
        d0 = self.date_start.date()
        d1 = self.date_stop.date()
        if d1 < d0:
            d0, d1 = d1, d0
        start = f"{d0.toString('yyyy-MM-dd')}T00:00:00"
        stop = f"{d1.toString('yyyy-MM-dd')}T23:30:00"
        return start, stop

    # ── Pobieranie (równoległe) ───────────────────────────────────────────────
    def _on_download(self):
        do_nmt = self.cb_enable_nmt.isChecked()
        do_nc = self.cb_enable_nc.isChecked()

        if not do_nmt and not do_nc:
            QMessageBox.warning(
                self, "Błąd",
                "Zaznacz co najmniej jedno źródło: NMT lub NEWA."
            )
            return

        # odśwież SHP jeśli podano ręcznie
        shp = self.edit_shp.text().strip()
        if shp and (not self.points_wgs or shp != getattr(self, "_last_shp", None)):
            self._load_shp(shp)
            self._last_shp = shp

        if not self.points_wgs:
            QMessageBox.warning(
                self, "Błąd",
                "Wskaż warstwę punktową lub plik SHP z punktami."
            )
            return

        if do_nc:
            if not rx.is_remote() and not os.path.isfile(PYTHON_EXE):
                QMessageBox.critical(
                    self, "Błąd",
                    f"Nie znaleziono interpretera Python:\n{PYTHON_EXE}\n\n"
                    f"Ustaw PYTHON_EXE na początku Combined_Downloader_GUI.py."
                )
                return
            if not rx.is_remote() and not os.path.isfile(NC_DOWNLOADER):
                QMessageBox.critical(
                    self, "Błąd",
                    f"Nie znaleziono NC_Downloader.py w:\n{SCRIPT_DIR}"
                )
                return

            do_meso = self.cb_meso.isChecked()
            do_micro = self.cb_micro.isChecked()
            if not do_meso and not do_micro:
                QMessageBox.warning(
                    self, "Błąd",
                    "Dla NEWA zaznacz co najmniej Mesoscale lub Microscale."
                )
                return

            heights = [h for h, cb in self.height_checks.items() if cb.isChecked()]
            if not heights:
                QMessageBox.warning(self, "Błąd", "Wybierz co najmniej jedną wysokość NEWA.")
                return

        # reset stanu
        self._nmt_done = not do_nmt
        self._nc_done = not do_nc
        self._nmt_result = None
        self._nc_result = None

        self.btn_download.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        self._log("═" * 55)
        self._log(f"Start pobierania  |  punktów: {len(self.points_wgs)}")
        self._log(f"  NMT: {'TAK' if do_nmt else 'NIE'}   |   NEWA: {'TAK' if do_nc else 'NIE'}")
        self._log("═" * 55)

        if do_nmt:
            self._start_nmt()
        if do_nc:
            self._start_nc()

    def _start_nmt(self):
        out_dir = self.edit_nmt_outdir.text().strip() or NMT_DEFAULT_OUTPUT
        os.makedirs(out_dir, exist_ok=True)

        formats = set()
        if self.cb_arc.isChecked():
            formats.add("ARC/INFO ASCII GRID")
        if self.cb_xyz.isChecked():
            formats.add("ASCII XYZ GRID")
        formats = formats or None

        is_evrf = self.rb_evrf.isChecked()
        only_newest = self.cb_newest.isChecked()

        self._log(f"[NMT] Katalog: {out_dir}")
        self._log(f"[NMT] Układ: {'EVRF2007' if is_evrf else 'KRON86'}  |  najnowszy: {only_newest}")

        self._nmt_thread = QThread()
        self._nmt_worker = NMTDownloadWorker(
            points_2180=self.points_2180,
            out_dir=out_dir,
            is_evrf=is_evrf,
            formats=formats,
            only_newest=only_newest,
        )
        self._nmt_worker.moveToThread(self._nmt_thread)

        self._nmt_thread.started.connect(self._nmt_worker.run)
        self._nmt_worker.log.connect(lambda m: self._log(f"[NMT] {m}"))
        self._nmt_worker.progress.connect(self._on_nmt_progress)
        self._nmt_worker.finished.connect(self._on_nmt_finished)
        self._nmt_worker.error.connect(self._on_nmt_error)
        self._nmt_worker.finished.connect(self._nmt_thread.quit)
        self._nmt_worker.error.connect(self._nmt_thread.quit)
        self._nmt_thread.finished.connect(self._maybe_cleanup)

        self._nmt_thread.start()

    def _start_nc(self):
        out_dir = self.edit_nc_outdir.text().strip() or NC_DEFAULT_OUTPUT
        os.makedirs(out_dir, exist_ok=True)

        heights = [h for h, cb in self.height_checks.items() if cb.isChecked()]
        do_meso = self.cb_meso.isChecked()
        do_micro = self.cb_micro.isChecked()
        dt_start, dt_stop = self._date_range_iso()

        points = []
        for i, (lon, lat) in enumerate(self.points_wgs):
            pid = self.turbine_ids[i] if i < len(self.turbine_ids) else i
            label = self.turbine_labels[i] if i < len(self.turbine_labels) else str(pid)
            points.append({
                "id": pid,
                "label": label,
                "longitude": lon,
                "latitude": lat,
            })

        params = {
            "output_dir": out_dir,
            "heights": heights,
            "variables": list(DEFAULT_VARIABLES),
            "dt_start": dt_start,
            "dt_stop": dt_stop,
            "points": points,
            "download_meso": do_meso,
            "download_micro": do_micro,
        }

        self._log(f"[NC] Katalog: {out_dir}")
        self._log(f"[NC] Źródła: meso={do_meso}, micro={do_micro}  |  wysokości: {heights}")
        self._log(f"[NC] Okres meso: {dt_start} → {dt_stop}")

        self._nc_thread = QThread()
        self._nc_worker = ScriptWorker(NC_DOWNLOADER, params)
        self._nc_worker.moveToThread(self._nc_thread)

        self._nc_thread.started.connect(self._nc_worker.run)
        self._nc_worker.log.connect(self._log)
        self._nc_worker.finished.connect(self._on_nc_finished)
        self._nc_worker.error.connect(self._on_nc_error)
        self._nc_worker.finished.connect(self._nc_thread.quit)
        self._nc_worker.error.connect(self._nc_thread.quit)
        self._nc_thread.finished.connect(self._maybe_cleanup)

        self._nc_thread.start()

    def _on_nmt_progress(self, cur, total):
        # opcjonalnie można pokazać osobny pasek; na razie tylko log
        pass

    def _on_nmt_finished(self, result):
        self._nmt_result = result
        self._nmt_done = True
        self._log("─" * 40)
        if result.get("status") == "cancelled":
            self._log("[NMT] ⏹  Przerwano.")
        elif result.get("status") == "ok":
            self._log(
                f"[NMT] ✓ Gotowe. Pobrano: {result.get('downloaded', 0)}, "
                f"pominięto: {result.get('skipped', 0)}, "
                f"błędy: {result.get('errors', 0)}"
            )
            # scalanie ASC
            if getattr(self, "cb_merge_asc", None) is not None and self.cb_merge_asc.isChecked():
                self._merge_nmt_asc(result)
        else:
            self._log(f"[NMT] Wynik: {result}")
        self._check_all_done()

    def _merge_nmt_asc(self, result):
        """Po pobraniu NMT – mozaika GDAL wszystkich .asc z katalogu wynikowego → <projekt>_NMT.tif."""
        out_dir = result.get("output_dir") or self.edit_nmt_outdir.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            self._log("[NMT] Mozaika: brak katalogu wynikowego.")
            return
        asc_files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.lower().endswith(".asc"))
        if not asc_files:
            self._log("[NMT] Mozaika: nie znaleziono arkuszy .asc.")
            return
        proj_name = os.environ.get("WINDLAB_PROJECT_NAME", "").strip()
        safe = re.sub(r'[^\w\-]+', "_", proj_name).strip("_") if proj_name else ""
        out_tif = os.path.join(out_dir, f"{safe or 'merged'}_NMT.tif")
        self._log(f"[NMT] Mozaika {len(asc_files)} arkuszy → {os.path.basename(out_tif)} …")
        QApplication.processEvents()
        ok, msg = mosaic_nmt_gdal(asc_files, out_tif, remove_sources=True,
                                  log_cb=lambda m: self._log(f"[NMT] {m}"))
        self._log(f"[NMT] ✓ {msg}" if ok else f"[NMT] ✗ Mozaika nieudana: {msg}")

    def _on_nmt_error(self, msg):
        self._nmt_done = True
        self._log(f"[NMT] ✗ BŁĄD: {msg}")
        self._check_all_done()

    def _on_nc_finished(self, result):
        self._nc_result = result
        self._nc_done = True
        self._log("─" * 40)
        if result.get("status") == "cancelled":
            self._log("[NC] ⏹  Przerwano.")
        elif result.get("status") == "ok":
            self._log(
                f"[NC] ✓ Gotowe. Sukces: {result.get('success', '?')}, "
                f"istniały: {result.get('exists', '?')}, "
                f"błędy: {result.get('errors', '?')}"
            )
            if "meso_ok" in result or "micro_ok" in result:
                self._log(
                    f"[NC]   Meso OK: {result.get('meso_ok', '?')}  |  "
                    f"Micro OK: {result.get('micro_ok', '?')}"
                )
            for w in result.get("position_warnings") or []:
                self._log(f"[NC] ⚠ {w}")
        else:
            self._log(f"[NC] Wynik: {result}")
        self._check_all_done()

    def _on_nc_error(self, msg):
        self._nc_done = True
        self._log(f"[NC] ✗ BŁĄD: {msg}")
        QMessageBox.critical(self, "Błąd NEWA", msg[:600])
        self._check_all_done()

    def _check_all_done(self):
        if self._nmt_done and self._nc_done:
            self._log("═" * 55)
            self._log("Wszystkie zadania zakończone.")
            self._log("═" * 55)
            self.btn_download.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.progress.setVisible(False)

    def _maybe_cleanup(self):
        # wywoływane po zakończeniu wątku – nic więcej nie trzeba
        pass

    def _on_stop(self):
        self._log("⏹  Żądanie przerwania wszystkich zadań…")
        if self._nmt_worker is not None:
            self._nmt_worker.stop()
        if self._nc_worker is not None:
            self._nc_worker.stop()
        self.btn_stop.setEnabled(False)


def main():
    dlg = CombinedDownloaderDialog()
    dlg.show()
    return dlg



if __name__ == '__main__':
    try:
        _dlg = main()
    except Exception as e:
        from qgis.PyQt.QtWidgets import QMessageBox
        QMessageBox.critical(None, 'Błąd Combined Downloader', str(e))
