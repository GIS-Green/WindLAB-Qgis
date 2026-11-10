# -*- coding: utf-8 -*-
"""
worker_shadow_GUI.py
Okno dialogowe Shadow Flicker – uruchom w edytorze Python QGIS.
"""

import os
import re
import sys
import json
from WindLab.core import remote_exec as rx
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QSizePolicy, QRadioButton, QButtonGroup, QLineEdit,
    QFileDialog, QMessageBox, QTabWidget, QWidget,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsCoordinateTransform, QgsCoordinateReferenceSystem,
)

from WindLab.core.paths import report_filename
from WindLab.core.ui import (
    TurbinePicker, browse_button, layer_icon, log_view, set_role, style_module_dialog,
    ElevationSourceCombo,
)
from WindLab.core.ui.widgets import open_path
import contextlib

# ── Konfiguracja ścieżek (dostosuj do swojej instalacji) ──────────────────────
PYTHON_EXE = os.environ.get("AEP_PYTHON", os.environ.get("WINDLAB_PYTHON", r"C:\Python3\python.exe"))
try:
    WORKERS_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    WORKERS_DIR = os.path.dirname(os.path.abspath("."))
WORKER_SHADOW = os.path.join(WORKERS_DIR, "worker_shadow.py")
REPORT_GEN = os.path.join(WORKERS_DIR, "worker_shadow_report.py")
WTG_DIR = os.environ.get("WINDLAB_WTG_DIR", os.path.join(os.path.dirname(os.path.dirname(WORKERS_DIR)), "WTG"))
NC_DIR = os.environ.get("WINDLAB_NC_DIR", os.path.join(os.path.dirname(WORKERS_DIR), "InputNC"))
OUTPUT_DIR = os.environ.get("WINDLAB_OUTPUT_DIR", os.path.join(WORKERS_DIR, "Output"))
# NMT jako folder (domyślnie z projektu)
NMT_DIR = os.environ.get("WINDLAB_NMT_DIR", "")


# ── Worker thread ─────────────────────────────────────────────────────────────
class ShadowWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            self.log.emit("Uruchamiam obliczenia Shadow Flicker...")
            result = self._run_sub(WORKER_SHADOW, self.params, timeout=7200)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _run_sub(self, script, params, timeout=600):
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        r = rx.run(
            [PYTHON_EXE, script, json.dumps(params)],
            capture_output=True, env=env, timeout=timeout,
        )
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        stderr = r.stderr.decode("utf-8", errors="replace").strip()
        if stderr:
            for line in stderr.splitlines():
                if line.strip():
                    self.log.emit(f"  [log] {line}")
        if not stdout:
            raise RuntimeError(f"Brak outputu z {os.path.basename(script)}\n{stderr[:500]}")
        # ostatnia linia JSON
        last = stdout.splitlines()[-1] if stdout else ""
        data = json.loads(last)
        if data.get("status") == "error":
            raise RuntimeError(f"Błąd workera: {data.get('message', '?')}\n{data.get('trace', '')[:800]}")
        return data


# ── Dialog ────────────────────────────────────────────────────────────────────
class ShadowDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(820)
        self.setMinimumHeight(600)
        self._thread = None
        self._worker = None
        self._last_result = None
        self._build_ui()
        self._populate_layers()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab 1: Dane ───────────────────────────────────────────────────
        tab_data = QWidget()
        tabs.addTab(tab_data, "Dane wejściowe")
        gl = QGridLayout(tab_data)

        gl.addWidget(QLabel("Warstwa turbin:"), 0, 0)
        self.turb_combo = QComboBox()
        gl.addWidget(self.turb_combo, 0, 1, 1, 2)

        gl.addWidget(QLabel("Warstwa receptorów:"), 1, 0)
        self.rec_combo = QComboBox()
        gl.addWidget(self.rec_combo, 1, 1, 1, 2)

        self.turbine = TurbinePicker("file", WTG_DIR)
        gl.addWidget(self.turbine, 2, 0, 1, 3)

        gl.addWidget(QLabel("DEM/DSM (opcjonalny):"), 3, 0)
        self.dem_combo = QComboBox()
        self.dem_combo.addItem("— brak —", userData=None)
        self.elev_combo = ElevationSourceCombo(local_widgets=[self.dem_combo])
        dem_row = QHBoxLayout()
        dem_row.addWidget(self.elev_combo)
        dem_row.addWidget(self.dem_combo, 1)
        gl.addLayout(dem_row, 3, 1, 1, 2)

        gl.addWidget(QLabel("Katalog NC (real_case):"), 4, 0)
        self.nc_edit = QLineEdit(NC_DIR)
        gl.addWidget(self.nc_edit, 4, 1)
        btn_nc = browse_button("Wybierz katalog NC")
        btn_nc.clicked.connect(lambda: self._browse_dir(self.nc_edit))
        gl.addWidget(btn_nc, 4, 2)

        gl.addWidget(QLabel("Katalog wyjściowy:"), 5, 0)
        self.out_edit = QLineEdit(OUTPUT_DIR)
        gl.addWidget(self.out_edit, 5, 1)
        btn_out = browse_button("Wybierz katalog wyjściowy")
        btn_out.clicked.connect(lambda: self._browse_dir(self.out_edit))
        gl.addWidget(btn_out, 5, 2)
        gl.setRowStretch(6, 1)

        # ── Tab 2: Tryb i parametry ────────────────────────────────────────
        tab_par = QWidget()
        tabs.addTab(tab_par, "Parametry")
        vp = QVBoxLayout(tab_par)

        grp_mode = QGroupBox("Tryb obliczeń")
        gm = QVBoxLayout(grp_mode)
        self.rb_worst = QRadioButton("Worst-case – wirnik jako pełny dysk (konserwatywny screening)")
        self.rb_worst.setChecked(True)
        self.rb_real = QRadioButton("Real-case – orientacja wirnika z kierunku wiatru NEWA")
        gm.addWidget(self.rb_worst)
        gm.addWidget(self.rb_real)
        vp.addWidget(grp_mode)

        grp_hub = QGroupBox("Wysokość piasty")
        gh = QGridLayout(grp_hub)
        self.rb_hub_layer = QRadioButton("Z warstwy (atrybut hub_height)")
        self.rb_hub_layer.setChecked(True)
        self.rb_hub_manual = QRadioButton("Ręcznie (jedna wartość globalna)")
        gh.addWidget(self.rb_hub_layer, 0, 0, 1, 2)
        gh.addWidget(self.rb_hub_manual, 1, 0)
        self.hub_spin = QDoubleSpinBox()
        self.hub_spin.setRange(30, 250)
        self.hub_spin.setValue(120)
        self.hub_spin.setSuffix(" m")
        self.hub_spin.setEnabled(False)
        gh.addWidget(self.hub_spin, 1, 1)
        self.rb_hub_manual.toggled.connect(self.hub_spin.setEnabled)
        vp.addWidget(grp_hub)

        grp_geo = QGroupBox("Geometria / czas")
        gg = QGridLayout(grp_geo)

        gg.addWidget(QLabel("Rok analizy:"), 0, 0)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2000, 2100)
        self.year_spin.setValue(datetime.now().year)
        gg.addWidget(self.year_spin, 0, 1)

        gg.addWidget(QLabel("Krok czasowy [min]:"), 0, 2)
        self.tstep_spin = QSpinBox()
        self.tstep_spin.setRange(1, 30)
        self.tstep_spin.setValue(2)
        gg.addWidget(self.tstep_spin, 0, 3)

        gg.addWidget(QLabel("Wys. obserwatora [m]:"), 1, 0)
        self.obs_spin = QDoubleSpinBox()
        self.obs_spin.setRange(0.5, 20)
        self.obs_spin.setValue(2.0)
        gg.addWidget(self.obs_spin, 1, 1)

        gg.addWidget(QLabel("Max. dystans cienia [m]:"), 1, 2)
        self.dist_spin = QDoubleSpinBox()
        self.dist_spin.setRange(100, 10000)
        self.dist_spin.setValue(2500)
        self.dist_spin.setSingleStep(100)
        gg.addWidget(self.dist_spin, 1, 3)

        gg.addWidget(QLabel("Min. elev. Słońca [°]:"), 2, 0)
        self.minsun_spin = QDoubleSpinBox()
        self.minsun_spin.setRange(0, 20)
        self.minsun_spin.setValue(3.0)
        gg.addWidget(self.minsun_spin, 2, 1)

        gg.addWidget(QLabel("Dostępność turbiny [-]:"), 2, 2)
        self.avail_spin = QDoubleSpinBox()
        self.avail_spin.setRange(0, 1)
        self.avail_spin.setSingleStep(0.01)
        self.avail_spin.setValue(1.0)
        gg.addWidget(self.avail_spin, 2, 3)
        vp.addWidget(grp_geo)

        grp_tz = QGroupBox("Strefa czasowa")
        gt = QGridLayout(grp_tz)
        self.rb_tz_fixed = QRadioButton("Stały offset UTC")
        self.rb_tz_fixed.setChecked(True)
        self.rb_tz_iana = QRadioButton("IANA (z DST)")
        gt.addWidget(self.rb_tz_fixed, 0, 0)
        self.tz_offset_spin = QDoubleSpinBox()
        self.tz_offset_spin.setRange(-12, 14)
        self.tz_offset_spin.setValue(1.0)
        gt.addWidget(self.tz_offset_spin, 0, 1)
        gt.addWidget(self.rb_tz_iana, 1, 0)
        self.tz_name_edit = QLineEdit("Europe/Warsaw")
        self.tz_name_edit.setEnabled(False)
        gt.addWidget(self.tz_name_edit, 1, 1)
        self.rb_tz_iana.toggled.connect(self.tz_name_edit.setEnabled)
        self.rb_tz_iana.toggled.connect(lambda c: self.tz_offset_spin.setEnabled(not c))
        vp.addWidget(grp_tz)

        grp_opt = QGroupBox("Optymalizacja / straty AEP")
        go_opt = QVBoxLayout(grp_opt)
        self.chk_opt_calendars = QCheckBox(
            "Eksport kalendarzy per turbina (model dokładny pod optymalizację)"
        )
        self.chk_opt_calendars.setChecked(True)
        self.chk_opt_calendars.setToolTip(
            "Zapisuje shadow_optimization_calendars.json z kalendarzami "
            "cień×turbina×receptor (wymagane przez moduł optymalizacji strat)."
        )
        go_opt.addWidget(self.chk_opt_calendars)
        self.chk_opt_exceed_only = QCheckBox(
            "Tylko receptory z przekroczeniem limitu godzin/rok"
        )
        self.chk_opt_exceed_only.setChecked(True)
        go_opt.addWidget(self.chk_opt_exceed_only)
        row_lim = QHBoxLayout()
        row_lim.addWidget(QLabel("Limit godzin/rok:"))
        self.limit_hours_spin = QDoubleSpinBox()
        self.limit_hours_spin.setRange(1, 200)
        self.limit_hours_spin.setValue(30.0)
        self.limit_hours_spin.setSuffix(" h")
        row_lim.addWidget(self.limit_hours_spin)
        row_lim.addStretch()
        go_opt.addLayout(row_lim)
        vp.addWidget(grp_opt)

        grp_out = QGroupBox("Wyniki")
        go = QGridLayout(grp_out)

        # nagłówki kolumn
        hdr_font = QFont()
        hdr_font.setBold(True)
        for col, title in enumerate(["Wielkość", "SHP", "Raster"]):
            lab = QLabel(title)
            lab.setFont(hdr_font)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter if col > 0 else Qt.AlignmentFlag.AlignLeft)
            go.addWidget(lab, 0, col)

        # Wiersze metryk
        self.chk_shp_hours = QCheckBox()
        self.chk_shp_hours.setChecked(True)
        self.chk_ras_hours = QCheckBox()
        self.chk_shp_days = QCheckBox()
        self.chk_shp_days.setChecked(True)
        self.chk_ras_days = QCheckBox()
        self.chk_shp_maxmin = QCheckBox()
        self.chk_shp_maxmin.setChecked(True)
        self.chk_ras_maxmin = QCheckBox()

        rows = [
            ("Godziny migotania w roku", self.chk_shp_hours, self.chk_ras_hours),
            ("Dni z migotaniem w roku", self.chk_shp_days, self.chk_ras_days),
            ("Maks. minut migotania na dzień", self.chk_shp_maxmin, self.chk_ras_maxmin),
        ]
        for i, (name, chk_shp, chk_ras) in enumerate(rows, start=1):
            go.addWidget(QLabel(name), i, 0)
            go.addWidget(chk_shp, i, 1, alignment=Qt.AlignmentFlag.AlignCenter)
            go.addWidget(chk_ras, i, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        go.addWidget(QLabel("Rozdzielczość siatki [m]:"), 4, 0)
        self.ras_res_spin = QSpinBox()
        self.ras_res_spin.setRange(5, 500)
        self.ras_res_spin.setValue(20)
        self.ras_res_spin.setToolTip("Rozdzielczość siatki – wspólna dla SHP i rastrów")
        go.addWidget(self.ras_res_spin, 4, 1, 1, 2)
        vp.addWidget(grp_out)
        vp.addStretch()

        # ── Log + przyciski ────────────────────────────────────────────────
        self.log_box = log_view(max_height=150)
        layout.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.btn_run = set_role(QPushButton("Oblicz migotanie cienia"), "primary")
        self.btn_run.clicked.connect(self._on_run)
        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self._on_report)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(self.btn_report)
        btn_row.addWidget(self.btn_run)
        layout.addLayout(btn_row)

        style_module_dialog(self, "flicker", "Migotanie cienia (shadow flicker)",
                            "worst-case / real-case · NEWA",
                            footer_items=3)  # log, postęp, przyciski

    # ── Helpers ────────────────────────────────────────────────────────────
    def _populate_layers(self):
        self.turb_combo.clear()
        self.rec_combo.clear()
        self.dem_combo.clear()
        self.dem_combo.addItem("— brak —", userData=None)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 0:
                self.turb_combo.addItem(layer_icon(layer), layer.name(), layer)
                self.rec_combo.addItem(layer_icon(layer), layer.name(), layer)
            elif isinstance(layer, QgsRasterLayer):
                self.dem_combo.addItem(layer_icon(layer), layer.name(), layer)

    def _browse_dir(self, edit):
        path = QFileDialog.getExistingDirectory(self, "Katalog", edit.text())
        if path:
            edit.setText(path)

    def _points_from_layer(self, layer, is_turbine=True):
        if not layer:
            return None
        crs_src = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        # używamy CRS warstwy jeśli metryczny, inaczej 2180
        if crs_src.mapUnits() == 0:  # meters
            crs_m = crs_src
        else:
            crs_m = QgsCoordinateReferenceSystem("EPSG:2180")
        t_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
        t_m = QgsCoordinateTransform(crs_src, crs_m, QgsProject.instance())

        data = {"ids": [], "x": [], "y": [], "names": [], "hub_heights": [],
                "lons": [], "lats": []}
        fields = [f.name().lower() for f in layer.fields()]
        name_field = None
        if is_turbine:
            for cand in ("userlabel", "user_label", "name", "nazwa", "id", "label", "turbine"):
                if cand in fields:
                    name_field = layer.fields()[fields.index(cand)].name()
                    break
        else:
            for cand in ("name", "nazwa", "id", "label", "receptor"):
                if cand in fields:
                    name_field = layer.fields()[fields.index(cand)].name()
                    break
        hub_field = None
        for cand in ("hub_height", "hubheight", "h_hub", "hh"):
            if cand in fields:
                hub_field = layer.fields()[fields.index(cand)].name()
                break

        for feat in layer.getFeatures():
            pt = feat.geometry().asPoint()
            p_wgs = t_wgs.transform(pt)
            p_m = t_m.transform(pt)
            data["ids"].append(feat.id())
            data["x"].append(round(p_m.x(), 2))
            data["y"].append(round(p_m.y(), 2))
            data["lons"].append(round(p_wgs.x(), 6))
            data["lats"].append(round(p_wgs.y(), 6))
            if name_field:
                data["names"].append(str(feat[name_field]))
            else:
                data["names"].append(f"{'T' if is_turbine else 'R'}{feat.id()}")
            if is_turbine:
                if self.rb_hub_layer.isChecked() and hub_field:
                    try:
                        data["hub_heights"].append(float(feat[hub_field]))
                    except Exception:
                        data["hub_heights"].append(self.hub_spin.value())
                else:
                    data["hub_heights"].append(self.hub_spin.value())
        data["crs"] = crs_m.authid()
        return data

    def _on_run(self):
        turb_layer = self.turb_combo.currentData()
        rec_layer = self.rec_combo.currentData()
        if not turb_layer or not rec_layer:
            QMessageBox.warning(self, "Błąd", "Wybierz warstwę turbin i receptorów")
            return
        wtg = self.turbine.path()
        from WindLab.core.wtg.remote import is_remote_wtg
        if not wtg or not (is_remote_wtg(wtg) or os.path.isfile(wtg)):
            QMessageBox.warning(self, "Błąd", "Wybierz prawidłowy plik .wtg")
            return

        turbines = self._points_from_layer(turb_layer, is_turbine=True)
        receptors = self._points_from_layer(rec_layer, is_turbine=False)
        if not turbines or not receptors:
            QMessageBox.warning(self, "Błąd", "Brak punktów w warstwach")
            return

        # lat/lon – automatyczny centroid turbin (bez UI)
        lat = sum(turbines["lats"]) / len(turbines["lats"])
        lon = sum(turbines["lons"]) / len(turbines["lons"])

        dem_layer = self.dem_combo.currentData()
        dem_path = dem_layer.source() if dem_layer else None
        if self.elev_combo.is_gugik():
            dem_path = None
        elif not dem_path:
            # prefer scalony plik {projekt}_NMT.asc, potem folder NMT
            dem_path = (
                os.environ.get("WINDLAB_NMT_FILE")
                or os.environ.get("WINDLAB_NMT_DIR")
                or globals().get("NMT_DIR")
                or None
            )
            if dem_path and not os.path.isdir(dem_path) and not os.path.isfile(dem_path):
                dem_path = None

        mode = "real_case" if self.rb_real.isChecked() else "worst_case"
        tz_mode = "iana" if self.rb_tz_iana.isChecked() else "fixed"

        out_dir = self.out_edit.text().strip() or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        params = {
            "turbines": turbines,
            "receptors": receptors,
            "wtg_path": wtg,
            "mode": mode,
            "nc_dir": self.nc_edit.text().strip(),
            "dem_path": dem_path,
            "elevation_source": self.elev_combo.source(),
            "latitude": lat,
            "longitude": lon,
            "year": self.year_spin.value(),
            "timezone_mode": tz_mode,
            "timezone_name": self.tz_name_edit.text().strip(),
            "timezone_offset": self.tz_offset_spin.value(),
            "observer_height_m": self.obs_spin.value(),
            "max_shadow_distance_m": self.dist_spin.value(),
            "time_step_minutes": self.tstep_spin.value(),
            "min_sun_elevation_deg": self.minsun_spin.value(),
            "max_sun_elevation_deg": 90.0,
            "turbine_availability": self.avail_spin.value(),
            "hub_height_manual": self.hub_spin.value() if self.rb_hub_manual.isChecked() else None,
            "isolines_hours": self.chk_shp_hours.isChecked(),
            "isolines_days": self.chk_shp_days.isChecked(),
            "isolines_maxmin": self.chk_shp_maxmin.isChecked(),
            "raster_hours": self.chk_ras_hours.isChecked(),
            "raster_days": self.chk_ras_days.isChecked(),
            "raster_maxmin": self.chk_ras_maxmin.isChecked(),
            "raster_resolution_m": self.ras_res_spin.value(),
            "output_dir": out_dir,
            "crs": turbines.get("crs", "EPSG:2180"),
            "export_detailed_calendars": self.chk_opt_calendars.isChecked(),
            "export_calendars_exceedances_only": self.chk_opt_exceed_only.isChecked(),
            "limit_hours_year": self.limit_hours_spin.value(),
        }

        self.btn_run.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.progress.setVisible(True)
        self.log_box.clear()

        self._thread = QThread()
        self._worker = ShadowWorker(params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log_box.append)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _cleanup(self):
        self.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None

    def _on_finished(self, result):
        if isinstance(result, dict):
            result["project_name"] = (
                result.get("project_name")
                or getattr(self, "_windlab_project_name", None)
                or os.environ.get("WINDLAB_PROJECT_NAME")
                or "projekt"
            )
        self._last_result = result
        self.btn_report.setEnabled(True)
        self.log_box.append("=" * 50)
        self.log_box.append("✅ Obliczenia zakończone.")
        self.log_box.append(f"Max h/rok (astro): {result.get('max_hours_astro', 0):.1f}")
        self.log_box.append(f"Max h/rok (real):  {result.get('max_hours_real', 0):.1f}")
        self.log_box.append(f"Najgorszy receptor: {result.get('worst_receptor')}")
        self.log_box.append(f">30 h: {result.get('n_exceed_30h')}   >8 h: {result.get('n_exceed_8h')}")
        self.log_box.append(f"CSV receptorów: {result.get('csv_path', '')}")

        # Punkty receptorów (zawsze, jeśli worker zapisał)
        p = result.get("shp_receptors_path") or result.get("shp_path")
        if p and os.path.isfile(p):
            self.log_box.append(f"SHP receptory: {p}")
            with contextlib.suppress(Exception):
                vl = QgsVectorLayer(p, "SF receptory", "ogr")
                if vl.isValid():
                    QgsProject.instance().addMapLayer(vl)

        # Izolinie zasięgu (SHP)
        for key, title in (
            ("isolines_hours_path", "Hours per year"),
            ("isolines_days_path", "Shadow days per year"),
            ("isolines_maxmin_path", "Max shadow minutes per day"),
        ):
            p = result.get(key)
            if p and os.path.isfile(p):
                self.log_box.append(f"Izolinie: {p}")
                with contextlib.suppress(Exception):
                    vl = QgsVectorLayer(p, title, "ogr")
                    if vl.isValid():
                        QgsProject.instance().addMapLayer(vl)

        # Rastry
        for key, title in (
            ("raster_hours_path", "SF Hours/year"),
            ("raster_days_path", "SF Days/year"),
            ("raster_maxmin_path", "SF Max min/day"),
        ):
            rpath = result.get(key)
            if rpath and os.path.isfile(rpath):
                self.log_box.append(f"Raster: {rpath}")
                with contextlib.suppress(Exception):
                    rl = QgsRasterLayer(rpath, title)
                    if rl.isValid():
                        QgsProject.instance().addMapLayer(rl)


    def _write_shp_from_result(self, result):
        """Tworzy SHP receptorów z pól wyniku (QGIS API)."""
        from qgis.core import (
            QgsVectorFileWriter, QgsFeature, QgsGeometry, QgsPointXY,
            QgsFields, QgsField, QgsWkbTypes, QgsCoordinateReferenceSystem,
        )
        from qgis.PyQt.QtCore import QVariant

        receptors = result.get("receptors_result") or []
        if not receptors:
            return None
        out_dir = result.get("output_dir") or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        mode = result.get("mode", "worst_case")
        suffix = "real" if mode == "real_case" else "worst"
        shp_path = os.path.join(out_dir, f"shadow_flicker_receptors_{suffix}.shp")

        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String))
        fields.append(QgsField("hours_yr", QVariant.Double))
        fields.append(QgsField("days_yr", QVariant.Int))
        fields.append(QgsField("max_min_d", QVariant.Int))
        fields.append(QgsField("hours_real", QVariant.Double))
        fields.append(QgsField("dom_turb", QVariant.String))
        fields.append(QgsField("severity", QVariant.String))
        fields.append(QgsField("mode", QVariant.String))

        crs = QgsCoordinateReferenceSystem(result.get("crs") or "EPSG:2180")
        writer = QgsVectorFileWriter(
            shp_path, "UTF-8", fields, QgsWkbTypes.Type.Point, crs, "ESRI Shapefile"
        )
        if writer.hasError() != QgsVectorFileWriter.WriterError.NoError:
            raise RuntimeError(writer.errorMessage())

        for r in receptors:
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(r["receptor_x"]), float(r["receptor_y"]))
            ))
            # hours: dla real_case preferuj hours_real jeśli availability < 1, ale
            # użytkownik chce hours zależne od trybu kalkulacji (astro z danego trybu)
            feat.setAttributes([
                str(r.get("receptor_name", "")),
                float(r.get("hours_astro", 0)),
                int(r.get("days_affected", 0)),
                int(r.get("max_minutes_day", 0)),
                float(r.get("hours_real", 0)),
                str(r.get("dominant_turbine") or ""),
                str(r.get("severity_label") or r.get("severity", "")),
                mode,
            ])
            writer.addFeature(feat)
        del writer

        # załaduj
        vl = QgsVectorLayer(shp_path, f"Shadow Flicker – receptory ({suffix})", "ogr")
        if vl.isValid():
            QgsProject.instance().addMapLayer(vl)
        return shp_path

    def _on_error(self, msg):
        self.log_box.append(f"❌ BŁĄD: {msg}")
        QMessageBox.critical(self, "Błąd obliczeń", msg[:600])

    def _on_report(self):
        if not self._last_result:
            return
        import tempfile
        out_dir = self._last_result.get("output_dir") or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        proj = (
            getattr(self, "_windlab_project_name", None)
            or os.environ.get("WINDLAB_PROJECT_NAME")
            or self._last_result.get("project_name")
            or "projekt"
        )
        self._last_result["project_name"] = proj
        fd, json_path = tempfile.mkstemp(suffix=".json", prefix="shadow_", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._last_result, f, ensure_ascii=False, indent=2)
        pdf_path = os.path.join(out_dir, report_filename("MIGOTANIE_CIENIA", proj))
        self.log_box.append("Generuję raport PDF...")
        self.btn_report.setEnabled(False)
        try:
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            proc = rx.run(
                [PYTHON_EXE, REPORT_GEN, json_path, pdf_path, out_dir],
                capture_output=True, text=True, env=env, timeout=300,
            )
            if proc.stdout:
                self.log_box.append(proc.stdout.strip())
            if proc.stderr:
                for line in proc.stderr.strip().splitlines():
                    self.log_box.append(f"  {line}")
            if proc.returncode != 0:
                raise RuntimeError(f"Kod {proc.returncode}")
            if os.path.exists(pdf_path):
                self.log_box.append(f"✅ Raport: {pdf_path}")
                with contextlib.suppress(Exception):
                    open_path(pdf_path)
            else:
                raise FileNotFoundError(pdf_path)
        except Exception as e:
            self.log_box.append(f"❌ Raport: {e}")
            QMessageBox.critical(self, "Błąd", str(e))
        finally:
            self.btn_report.setEnabled(True)
            with contextlib.suppress(Exception):
                os.unlink(json_path)


def main():
    global _shadow_dlg
    _shadow_dlg = ShadowDialog(parent=iface.mainWindow())
    _shadow_dlg.show()
    _shadow_dlg.raise_()
    _shadow_dlg.activateWindow()



if __name__ == '__main__':
    main()
