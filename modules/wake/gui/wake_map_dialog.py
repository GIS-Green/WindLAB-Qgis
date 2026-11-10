# -*- coding: utf-8 -*-
"""
Wake map dialog (QGIS) – NEWA meso/micro, no GWA.
  from WakeMap.gui.wake_map_dialog import main; main()
"""

from __future__ import annotations

import os
import sys
import json
import re
from WindLab.core import remote_exec as rx
import tempfile

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QSizePolicy, QButtonGroup, QRadioButton,
    QLineEdit, QFileDialog, QMessageBox, QWidget,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, QgsRasterLayer,
)

from WindLab.core.paths import report_filename
from WindLab.core.ui import (
    TurbinePicker, browse_button, layer_icon, hint_label, log_view, set_role, style_module_dialog,
    ElevationSourceCombo,
)
from WindLab.core.ui.widgets import open_path
import contextlib

_WM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_WORKERS = os.path.join(_WM_ROOT, "workers")
WORKER = os.path.join(_WORKERS, "worker_wake_map.py")


def _resolve_data_root() -> str:
    env = os.environ.get("AEP_DATA_ROOT", "").strip()
    if env and os.path.isdir(env):
        return env
    candidates = [
        os.path.abspath(os.path.join(_WM_ROOT, os.pardir, os.pardir)),
        os.path.abspath(os.path.join(_WM_ROOT, os.pardir)),
        _WM_ROOT,
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "InputNC")) or os.path.isdir(os.path.join(c, "WTG")):
            return c
    return candidates[0]


def _find_python_exe() -> str:
    env = (os.environ.get("AEP_PYTHON") or "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    for cand in (
        r"C:\Python3\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
        r"C:\Python39\python.exe",
    ):
        if os.path.isfile(cand):
            return cand
    return sys.executable


_DATA_ROOT = _resolve_data_root()
DEFAULT_WTG_DIR = os.environ.get("WINDLAB_WTG_DIR") or os.path.join(_DATA_ROOT, "WTG")
DEFAULT_NC_DIR = os.environ.get("WINDLAB_NC_DIR") or (
    os.path.join(_DATA_ROOT, "Data", "NC") if os.path.isdir(os.path.join(_DATA_ROOT, "Data", "NC"))
    else os.path.join(_DATA_ROOT, "InputNC"))
DEFAULT_OUTPUT_DIR = os.environ.get("WINDLAB_OUTPUT_DIR") or os.path.join(_DATA_ROOT, "Output")
PYTHON_EXE = _find_python_exe()

WAKE_MODELS = [
    ("BastankhahGaussian", "Bastankhah Gaussian [default]"),
    ("NiayifarGaussian", "Niayifar Gaussian"),
    ("ZongGaussian", "Zong Gaussian"),
    ("NOJ", "NOJ – Jensen"),
    ("GCL", "GCL – Larsen"),
    ("TurboGaussian", "TurboGaussian"),
]

DIR_PRESETS = [
    ("Jeden kierunek", None),
    ("Co 30° (12 sekt.)", list(range(0, 360, 30))),
    ("Co 10° (36 sekt.)", list(range(0, 360, 10))),
    ("Zakres W: 240–300°", list(range(240, 301, 10))),
    ("Zakres SW: 180–270°", list(range(180, 271, 15))),
    ("Pełne 360° co 5°", list(range(0, 360, 5))),
]


class SimWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, params, python_exe=None):
        super().__init__()
        self.params = params
        self.python_exe = python_exe or PYTHON_EXE

    def run(self):
        try:
            self.log.emit("Uruchamiam wake map (NEWA)...")
            result = self._run_sub(WORKER, self.params, timeout=3600)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _run_sub(self, script, params, timeout=600):
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)

        py = self.python_exe
        if "qgis" in os.path.basename(py).lower() and "python" not in os.path.basename(py).lower():
            raise RuntimeError(
                f"Błędny interpreter: {py}\nUstaw AEP_PYTHON=C:\\Python3\\python.exe"
            )

        fd, path = tempfile.mkstemp(suffix=".json", prefix="wake_params_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(params, f, ensure_ascii=False)
            self.log.emit(f"  Python: {py}")
            self.log.emit(f"  Worker: {script}")
            r = rx.run(
                [py, script, "--params-file", path],
                capture_output=True, env=env, timeout=timeout,
            )
        finally:
            with contextlib.suppress(Exception):
                os.unlink(path)

        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        stderr = r.stderr.decode("utf-8", errors="replace").strip()
        if stderr:
            for line in stderr.splitlines():
                if line.strip():
                    self.log.emit(f"  [log] {line}")
        if not stdout:
            raise RuntimeError(f"Brak outputu (code={r.returncode})\n{stderr[:600]}")
        lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
        data = json.loads(lines[-1] if lines else stdout)
        if data.get("status") == "error":
            raise RuntimeError(f"{data.get('message')}\n{data.get('trace', '')}")
        return data


class WakeMapDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(760)
        self.setMinimumHeight(600)
        self._thread = None
        self._worker = None
        self._last_result = None
        self._build_ui()
        self._populate_layers()
        self._refresh_nc_preview()


    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Input
        grp = QGroupBox("Dane wejściowe")
        gl = QGridLayout(grp)
        gl.addWidget(QLabel("Warstwa turbin:"), 0, 0)
        self.layer_combo = QComboBox()
        gl.addWidget(self.layer_combo, 0, 1, 1, 2)


        gl.addWidget(QLabel("Katalog NC:"), 2, 0)
        self.nc_edit = QLineEdit(DEFAULT_NC_DIR if os.path.isdir(DEFAULT_NC_DIR) else "")
        gl.addWidget(self.nc_edit, 2, 1)
        btn_nc = browse_button("Wybierz katalog NC")
        btn_nc.clicked.connect(lambda: self._browse_dir(self.nc_edit, self._refresh_nc_preview))
        gl.addWidget(btn_nc, 2, 2)

        gl.addWidget(QLabel("Katalog wyjściowy:"), 3, 0)
        self.out_edit = QLineEdit(DEFAULT_OUTPUT_DIR)
        gl.addWidget(self.out_edit, 3, 1)
        btn_o = browse_button("Wybierz katalog wyjściowy")
        btn_o.clicked.connect(lambda: self._browse_dir(self.out_edit))
        gl.addWidget(btn_o, 3, 2)

        gl.addWidget(QLabel("NMT (DEM):"), 4, 0)
        self.nmt_edit = QLineEdit("")
        self.nmt_edit.setPlaceholderText("opcjonalnie – raster elevacji (GeoTIFF / .asc)")
        gl.addWidget(self.nmt_edit, 4, 1)
        btn_nmt = browse_button("Wybierz raster NMT")
        btn_nmt.clicked.connect(self._browse_nmt)
        gl.addWidget(btn_nmt, 4, 2)
        self.elev_combo = ElevationSourceCombo(local_widgets=[self.nmt_edit, btn_nmt])
        gl.addWidget(QLabel("Źródło wysokości:"), 5, 0)
        gl.addWidget(self.elev_combo, 5, 1, 1, 2)

        self.nc_preview = hint_label()
        gl.addWidget(self.nc_preview, 6, 0, 1, 3)
        root.addWidget(grp)

        # Turbina
        grp_t = QGroupBox("Turbina")
        tl = QVBoxLayout(grp_t)
        self.turbine = TurbinePicker("power", DEFAULT_WTG_DIR)
        tl.addWidget(self.turbine)
        root.addWidget(grp_t)

        # Hub
        grp_h = QGroupBox("Wysokość piasty")
        gl_h = QGridLayout(grp_h)
        self.rb_hh_layer = QRadioButton("Z warstwy (hub_height)")
        self.rb_hh_layer.setChecked(True)
        self.rb_hh_manual = QRadioButton("Ręcznie")
        self.hub_spin = QDoubleSpinBox()
        self.hub_spin.setRange(50, 250)
        self.hub_spin.setValue(120)
        self.hub_spin.setEnabled(False)
        self.rb_hh_manual.toggled.connect(self.hub_spin.setEnabled)
        gl_h.addWidget(self.rb_hh_layer, 0, 0, 1, 2)
        gl_h.addWidget(self.rb_hh_manual, 1, 0)
        gl_h.addWidget(self.hub_spin, 1, 1)
        root.addWidget(grp_h)

        # Model + NEWA options
        grp_m = QGroupBox("Model / NEWA")
        gl_m = QGridLayout(grp_m)
        self.model_combo = QComboBox()
        for k, lab in WAKE_MODELS:
            self.model_combo.addItem(lab, userData=k)
        gl_m.addWidget(self.model_combo, 0, 0, 1, 2)

        gl_m.addWidget(QLabel("TI:"), 1, 0)
        self.spin_ti = QDoubleSpinBox()
        self.spin_ti.setRange(0.01, 0.30)
        self.spin_ti.setDecimals(3)
        self.spin_ti.setSingleStep(0.01)
        self.spin_ti.setValue(0.06)
        gl_m.addWidget(self.spin_ti, 1, 1)

        self.cb_micro = QCheckBox("Korekta microscale (scale = micro/meso mean)")
        self.cb_micro.setChecked(False)
        gl_m.addWidget(self.cb_micro, 2, 0, 1, 2)

        self.rb_farm = QRadioButton("Inflow: średnia farmy [domyślnie]")
        self.rb_farm.setChecked(True)
        self.rb_per = QRadioButton("Inflow: per turbina")
        gl_m.addWidget(self.rb_farm, 3, 0, 1, 2)
        gl_m.addWidget(self.rb_per, 4, 0, 1, 2)

        self.cb_omni = QCheckBox("Policz mapę omnidirectional (róża z NEWA meso)")
        self.cb_omni.setChecked(False)
        self.cb_omni.setToolTip(
            "Dostępne tylko przy Preset / Zakres.\n"
            "Uśrednia deficyt z wagami z róży wiatrów NEWA meso "
            "(nie równymi wagami jak zwykła średnia presetu)."
        )
        gl_m.addWidget(self.cb_omni, 5, 0, 1, 2)
        root.addWidget(grp_m)

        # Directions
        grp_d = QGroupBox("Kierunki wiatru")
        gl_d = QGridLayout(grp_d)
        self.rb_single = QRadioButton("Pojedynczy")
        self.rb_preset = QRadioButton("Preset")
        self.rb_range = QRadioButton("Zakres")
        self.rb_single.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.rb_single)
        bg.addButton(self.rb_preset)
        bg.addButton(self.rb_range)
        bg.buttonClicked.connect(self._on_dir_mode)
        gl_d.addWidget(self.rb_single, 0, 0)
        gl_d.addWidget(self.rb_preset, 0, 1)
        gl_d.addWidget(self.rb_range, 0, 2)

        self.spin_dir = QDoubleSpinBox()
        self.spin_dir.setRange(0, 360)
        self.spin_dir.setValue(270)
        self.spin_dir.setDecimals(1)
        gl_d.addWidget(QLabel("Kierunek [°]:"), 1, 0)
        gl_d.addWidget(self.spin_dir, 1, 1)

        compass = QHBoxLayout()
        for lab, deg in [("N", 0), ("NE", 45), ("E", 90), ("SE", 135),
                         ("S", 180), ("SW", 225), ("W", 270), ("NW", 315)]:
            b = set_role(QPushButton(lab), "compact")
            b.setFixedWidth(36)
            b.clicked.connect(lambda _, d=deg: self.spin_dir.setValue(d))
            compass.addWidget(b)
        cw = QWidget()
        cw.setLayout(compass)
        gl_d.addWidget(cw, 1, 2)
        self._single_w = [self.spin_dir, cw]

        self.preset_combo = QComboBox()
        for lab, _ in DIR_PRESETS[1:]:
            self.preset_combo.addItem(lab)
        gl_d.addWidget(QLabel("Preset:"), 2, 0)
        gl_d.addWidget(self.preset_combo, 2, 1, 1, 2)
        self._preset_w = [self.preset_combo]

        self.spin_from = QDoubleSpinBox()
        self.spin_to = QDoubleSpinBox()
        self.spin_step = QDoubleSpinBox()
        for s in (self.spin_from, self.spin_to, self.spin_step):
            s.setRange(0, 360)
            s.setDecimals(0)
        self.spin_from.setValue(240)
        self.spin_to.setValue(300)
        self.spin_step.setValue(10)
        rl = QHBoxLayout()
        rl.addWidget(self.spin_from)
        rl.addWidget(QLabel("–"))
        rl.addWidget(self.spin_to)
        rl.addWidget(QLabel("co"))
        rl.addWidget(self.spin_step)
        rw = QWidget()
        rw.setLayout(rl)
        gl_d.addWidget(QLabel("Od/Do/Krok:"), 3, 0)
        gl_d.addWidget(rw, 3, 1, 1, 2)
        self._range_w = [rw]

        self.lbl_dirs = hint_label()
        gl_d.addWidget(self.lbl_dirs, 4, 0, 1, 3)
        for w in (self.spin_dir, self.preset_combo, self.spin_from, self.spin_to, self.spin_step):
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self._update_dirs_preview)
            if hasattr(w, "currentIndexChanged"):
                w.currentIndexChanged.connect(self._update_dirs_preview)
        root.addWidget(grp_d)
        self._on_dir_mode()

        # Resolution
        grp_r = QGroupBox("Rozdzielczość")
        gl_r = QGridLayout(grp_r)
        self.spin_res_tif = QSpinBox()
        self.spin_res_tif.setRange(1, 500)
        self.spin_res_tif.setValue(5)
        self.spin_res_tif.setSuffix(" m")
        self.spin_res_png = QSpinBox()
        self.spin_res_png.setRange(5, 500)
        self.spin_res_png.setValue(25)
        self.spin_res_png.setSuffix(" m")
        gl_r.addWidget(QLabel("GeoTIFF:"), 0, 0)
        gl_r.addWidget(self.spin_res_tif, 0, 1)
        gl_r.addWidget(QLabel("PNG:"), 1, 0)
        gl_r.addWidget(self.spin_res_png, 1, 1)
        root.addWidget(grp_r)

        # Post
        grp_p = QGroupBox("Po obliczeniu")
        hl = QHBoxLayout(grp_p)
        self.chk_tif = QCheckBox("Wczytaj GeoTIFF do QGIS")
        self.chk_tif.setChecked(True)
        self.chk_shp = QCheckBox("Wczytaj SHP do QGIS")
        self.chk_shp.setChecked(True)
        self.chk_attrs = QCheckBox("Zapisz deficit do atrybutów warstwy")
        self.chk_attrs.setChecked(False)
        hl.addWidget(self.chk_tif)
        hl.addWidget(self.chk_shp)
        hl.addWidget(self.chk_attrs)
        root.addWidget(grp_p)

        self.log_box = log_view(max_height=130)
        root.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        bl = QHBoxLayout()
        self.btn_run = set_role(QPushButton("Oblicz wake"), "primary")
        self.btn_run.clicked.connect(self._on_run)
        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.setEnabled(False)
        self.btn_report.setToolTip(
            "Generuje raport PDF z ostatniego obliczenia wake "
            "(tabele, mapa deficytu, elevacja z NMT)."
        )
        self.btn_report.clicked.connect(self._on_generate_report)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        bl.addStretch()
        bl.addWidget(btn_close)
        bl.addWidget(self.btn_report)
        bl.addWidget(self.btn_run)
        root.addLayout(bl)
        self._update_dirs_preview()

        style_module_dialog(self, "wake", "Analiza wake",
                            "NEWA meso/micro · mapa deficytu prędkości",
                            footer_items=3)  # log, postęp, przyciski

    def _populate_layers(self):
        self.layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 0:
                self.layer_combo.addItem(layer_icon(layer), layer.name(), layer)
        # Auto-suggest first DEM-like raster as NMT if field empty
        if not self.nmt_edit.text().strip():
            for layer in QgsProject.instance().mapLayers().values():
                if isinstance(layer, QgsRasterLayer) and layer.isValid():
                    name_l = layer.name().lower()
                    src = layer.source()
                    if any(k in name_l for k in ("nmt", "dem", "dtm", "elev", "terrain", "hipsometria")) \
                            or src.lower().endswith((".tif", ".tiff", ".asc")):
                        self.nmt_edit.setText(src)
                        break

    def _browse_dir(self, edit, after=None):
        path = QFileDialog.getExistingDirectory(self, "Katalog", edit.text())
        if path:
            edit.setText(path)
            if after:
                after()

    def _browse_nmt(self):
        start = self.nmt_edit.text().strip() or DEFAULT_OUTPUT_DIR
        path, _ = QFileDialog.getOpenFileName(
            self, "NMT / DEM", start if os.path.isdir(os.path.dirname(start) or start) else "",
            "Raster (*.tif *.tiff *.asc *.img *.vrt);;Wszystkie (*.*)",
        )
        if path:
            self.nmt_edit.setText(path)

    def _refresh_nc_preview(self):
        nc = self.nc_edit.text().strip()
        if not os.path.isdir(nc):
            self.nc_preview.setText("NC: (brak)")
            return
        meso, micro = set(), set()
        for f in os.listdir(nc):
            m = re.match(r"^(.+)_meso\.nc$", f, re.I)
            if m:
                meso.add(m.group(1))
            m = re.match(r"^(.+)_micro\.nc$", f, re.I)
            if m:
                micro.add(m.group(1))
        paired = sorted(meso & micro)
        self.nc_preview.setText(
            f"Pary NC: {len(paired)} ({', '.join(paired[:10])}{'…' if len(paired) > 10 else ''})"
        )

    def _on_dir_mode(self, *_):
        s, p, r = self.rb_single.isChecked(), self.rb_preset.isChecked(), self.rb_range.isChecked()
        for w in self._single_w:
            w.setVisible(s)
        for w in self._preset_w:
            w.setVisible(p)
        for w in self._range_w:
            w.setVisible(r)
        # Omni only for multi-direction modes (preset / range)
        multi = p or r
        self.cb_omni.setEnabled(multi)
        if not multi:
            self.cb_omni.setChecked(False)
        self._update_dirs_preview()

    def _get_wind_dirs(self):
        if self.rb_single.isChecked():
            return [round(self.spin_dir.value(), 1)]
        if self.rb_preset.isChecked():
            _, dirs = DIR_PRESETS[self.preset_combo.currentIndex() + 1]
            return [float(d) for d in dirs]
        d0, d1, st = self.spin_from.value(), self.spin_to.value(), max(1.0, self.spin_step.value())
        if d0 <= d1:
            dirs = list(range(int(d0), int(d1) + 1, int(st)))
        else:
            dirs = list(range(int(d0), 361, int(st))) + list(range(0, int(d1) + 1, int(st)))
        return [float(d) for d in dirs] or [d0]

    def _update_dirs_preview(self):
        dirs = self._get_wind_dirs()
        if len(dirs) == 1:
            self.lbl_dirs.setText(f"Kierunek: {dirs[0]:.0f}°")
        elif len(dirs) <= 12:
            self.lbl_dirs.setText(f"{len(dirs)} kierunków: " + ", ".join(f"{d:.0f}°" for d in dirs))
        else:
            self.lbl_dirs.setText(f"{len(dirs)} kierunków: {dirs[0]:.0f}° … {dirs[-1]:.0f}°")

    @staticmethod
    def _label_aliases(raw: str) -> list:
        s = str(raw).strip()
        if not s:
            return []
        aliases = {s, s.upper(), s.lower()}
        for prefix in ("WTG_", "wtg_"):
            if s.startswith(prefix) and len(s) > len(prefix):
                rest = s[len(prefix):]
                aliases.update({rest, rest.upper(), rest.lower(), "WTG_" + rest})
        if not s.upper().startswith("WTG_"):
            aliases.update({"WTG_" + s, "WTG_" + s.upper(), "WTG_" + s.lower()})
        return list(aliases)

    def _discover_nc_names(self, nc_dir):
        meso, micro = set(), set()
        for f in os.listdir(nc_dir):
            m = re.match(r"^(.+)_meso\.nc$", f, re.I)
            if m:
                meso.add(m.group(1))
            m = re.match(r"^(.+)_micro\.nc$", f, re.I)
            if m:
                micro.add(m.group(1))
        if self.cb_micro.isChecked():
            return sorted(meso & micro)
        return sorted(meso)

    def _get_turbines(self, required_names):
        layer = self.layer_combo.currentData()
        if not layer:
            return None, "Wybierz warstwę punktową"

        crs_src = layer.crs()
        t_wgs = QgsCoordinateTransform(
            crs_src, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        t_m = QgsCoordinateTransform(
            crs_src, QgsCoordinateReferenceSystem("EPSG:2180"), QgsProject.instance())

        field_names = [f.name() for f in layer.fields()]
        ul_field = next(
            (n for n in field_names if n.lower() in ("userlabel", "name", "nazwa", "label", "wtg")),
            None,
        )
        if ul_field is None:
            return None, "Brak atrybutu USERLABEL/name"

        by_alias = {}
        for feat in layer.getFeatures():
            val = feat[ul_field]
            label = str(val).strip() if val not in (None, "") else ""
            if not label:
                continue
            pt = feat.geometry().asPoint()
            p_wgs, p_m = t_wgs.transform(pt), t_m.transform(pt)
            if self.rb_hh_layer.isChecked():
                try:
                    h = float(feat["hub_height"])
                except Exception:
                    return None, f"Brak hub_height dla {label}"
            else:
                h = self.hub_spin.value()
            rec = {
                "lon": round(p_wgs.x(), 6), "lat": round(p_wgs.y(), 6),
                "x": round(p_m.x(), 2), "y": round(p_m.y(), 2),
                "h": h, "id": feat.id(), "feat": feat, "raw": label,
            }
            for a in self._label_aliases(label):
                by_alias[a] = rec
                by_alias[a.upper()] = rec
                by_alias[a.lower()] = rec

        missing, matched = [], {}
        for name in required_names:
            rec = by_alias.get(name) or by_alias.get(name.upper()) or by_alias.get(name.lower())
            if rec is None:
                stem = name[4:] if name.upper().startswith("WTG_") else name
                rec = by_alias.get(stem) or by_alias.get(stem.upper())
            if rec is None:
                missing.append(name)
            else:
                matched[name] = rec
        if missing:
            return None, "Brak punktów dla: " + ", ".join(missing)

        turbines = {
            "lons": [], "lats": [], "hub_heights": [], "ids": [],
            "x_2180": [], "y_2180": [], "userlabel": [], "names": [],
        }
        feats = []
        for name in required_names:
            r = matched[name]
            turbines["lons"].append(r["lon"])
            turbines["lats"].append(r["lat"])
            turbines["hub_heights"].append(r["h"])
            turbines["ids"].append(r["id"])
            turbines["x_2180"].append(r["x"])
            turbines["y_2180"].append(r["y"])
            turbines["userlabel"].append(name)
            turbines["names"].append(name)
            feats.append(r["feat"])
        return (turbines, feats, layer), None

    def _on_run(self):
        wtg = self.turbine.path()
        from WindLab.core.wtg.remote import is_remote_wtg
        if not wtg or not (is_remote_wtg(wtg) or os.path.isfile(wtg)):
            QMessageBox.warning(self, "Błąd", "Wybierz plik .wtg")
            return
        curve = self.turbine.curve()
        if curve is None:
            QMessageBox.warning(self, "Błąd", "Wybrany plik .wtg nie zawiera krzywej mocy")
            return
        nc_dir = self.nc_edit.text().strip()
        if not os.path.isdir(nc_dir):
            QMessageBox.warning(self, "Błąd", "Katalog NC nie istnieje")
            return
        names = self._discover_nc_names(nc_dir)
        if not names:
            QMessageBox.warning(self, "Błąd", "Brak plików *_meso.nc w InputNC")
            return
        pack, err = self._get_turbines(names)
        if err:
            QMessageBox.warning(self, "Błąd", err)
            return
        turbines, feats, layer = pack
        out_dir = self.out_edit.text().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        layer_name = self.layer_combo.currentText() or "WakeMap"
        params = {
            "turbines": turbines,
            "wtg_path": wtg,
            "power_curve": curve.label,
            "nc_dir": nc_dir,
            "output_dir": out_dir,
            "wake_model": self.model_combo.currentData(),
            "wind_dirs": self._get_wind_dirs(),
            "ti": self.spin_ti.value(),
            "resolution": self.spin_res_tif.value(),
            "resolution_png": self.spin_res_png.value(),
            "inflow_mode": "per_turbine" if self.rb_per.isChecked() else "farm_mean",
            "apply_micro_scale": self.cb_micro.isChecked(),
            "compute_omni": self.cb_omni.isChecked() and not self.rb_single.isChecked(),
            "nmt_path": "" if self.elev_combo.is_gugik() else self.nmt_edit.text().strip(),
            "elevation_source": self.elev_combo.source(),
            "generate_report": False,
            "project_name": layer_name,
        }
        self._pending_layer = layer
        self._pending_feats = {f.id(): f for f in feats}

        self.btn_run.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.progress.setVisible(True)
        self.log_box.clear()
        self.log_box.append(f"Turbiny: {', '.join(names)}")
        self.log_box.append(
            f"Inflow={params['inflow_mode']} micro={params['apply_micro_scale']} "
            f"omni={params['compute_omni']}"
        )

        self._thread = QThread()
        self._worker = SimWorker(params)
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
        self.log_box.append("─" * 40)
        for w in result.get("position_warnings") or []:
            self.log_box.append(f"⚠ {w}")
        self.log_box.append(f"✓ PNG: {result.get('png_path')}")
        if result.get("png_omni_path"):
            self.log_box.append(f"✓ PNG omni: {result.get('png_omni_path')}")
        self.log_box.append(f"✓ TIF: {result.get('tif_path')}")
        if result.get("tif_omni_path"):
            self.log_box.append(f"✓ TIF omni: {result.get('tif_omni_path')}")
        self.log_box.append(f"✓ SHP: {result.get('shp_path')}")
        if result.get("nmt_used"):
            self.log_box.append(f"✓ NMT: elevacje odczytane z {result.get('nmt_path')}")
        self.log_box.append(
            f"WS farm: {result.get('ws_farm_mean')} m/s | "
            f"Max deficit: {result.get('max_deficit_pct')} %"
        )
        for t in result.get("turbines_result") or []:
            elev_s = ""
            if t.get("elevation_m") is not None:
                elev_s = f" elev={t['elevation_m']:.0f}m"
            self.log_box.append(
                f"  {t.get('userlabel')}: free={t['ws_free_ms']:.2f} "
                f"eff={t['ws_eff_ms']:.2f} def={t['deficit_pct']:.2f}%{elev_s}"
            )
        self.log_box.append("→ Możesz wygenerować raport PDF przyciskiem „Generuj raport”")

        png = result.get("png_path")
        if not self.rb_single.isChecked() and result.get("png_omni_path"):
            png = result["png_omni_path"]
        if png:
            open_path(png)

        model = result.get("wake_model", "")
        if self.chk_tif.isChecked() and result.get("tif_path"):
            self._load_raster(result["tif_path"], f"Wake deficit – {model}")
        if self.chk_tif.isChecked() and result.get("tif_omni_path"):
            self._load_raster(result["tif_omni_path"], f"Wake omni – {model}")
        if self.chk_shp.isChecked():
            shp = result.get("shp_path")
            if not self.rb_single.isChecked() and result.get("shp_omni_path"):
                shp = result["shp_omni_path"]
            if shp:
                self._load_vector(shp, f"Wake contours – {model}")

        if self.chk_attrs.isChecked():
            self._write_attrs(result)

    def _on_generate_report(self):
        """Generuje PDF z ostatniego wyniku wake (bez ponownego obliczania)."""
        result = getattr(self, "_last_result", None)
        if not result or result.get("status") != "ok":
            QMessageBox.warning(
                self, "Raport",
                "Brak wyników obliczenia.\nNajpierw uruchom „Oblicz wake”.",
            )
            return

        out_dir = (
            result.get("output_dir")
            or self.out_edit.text().strip()
            or DEFAULT_OUTPUT_DIR
        )
        os.makedirs(out_dir, exist_ok=True)

        model = result.get("wake_model") or "wake"
        wind_dirs = result.get("wind_dirs") or []
        if len(wind_dirs) == 1:
            dirs_label = f"{wind_dirs[0]:.0f}deg"
        elif wind_dirs:
            dirs_label = f"{min(wind_dirs):.0f}-{max(wind_dirs):.0f}deg_{len(wind_dirs)}dirs"
        else:
            dirs_label = "dirs"
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d")
        proj = (
            getattr(self, "_windlab_project_name", None)
            or os.environ.get("WINDLAB_PROJECT_NAME")
            or (self._last_result.get("project_name") if self._last_result else None)
            or "projekt"
        )
        # nie używaj nazwy warstwy/shp jako nazwy projektu
        if str(proj).lower() in ("turbines", "wtg", "turbiny", ""):
            proj = os.environ.get("WINDLAB_PROJECT_NAME") or "projekt"
        self._last_result["project_name"] = proj
        pdf_path = os.path.join(out_dir, report_filename("WAKE", proj, out_dir=out_dir))
        from WindLab.core.paths import stamp_of as _stamp_of
        plots_dir = os.path.join(out_dir, "wykresy_wake_" + (_stamp_of(pdf_path) or "raport"))

        # Nazwa projektu z WindLab (nie z warstwy turbin!)
        data = dict(result)
        data["project_name"] = proj
        nmt_gui = self.nmt_edit.text().strip()
        if nmt_gui and not self.elev_combo.is_gugik():
            data["nmt_path"] = nmt_gui

        self.btn_report.setEnabled(False)
        self.log_box.append("Generuję raport PDF…")
        try:
            report_script = os.path.join(_WORKERS, "worker_wake_report.py")
            fd, tmp_json = tempfile.mkstemp(suffix=".json", prefix="wake_report_", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                env = os.environ.copy()
                env.pop("PYTHONHOME", None)
                env.pop("PYTHONPATH", None)
                r = rx.run(
                    [PYTHON_EXE, report_script, tmp_json, pdf_path, plots_dir],
                    capture_output=True, env=env, timeout=300,
                )
            finally:
                with contextlib.suppress(Exception):
                    os.unlink(tmp_json)

            stdout = r.stdout.decode("utf-8", errors="replace").strip()
            stderr = r.stderr.decode("utf-8", errors="replace").strip()
            if stderr:
                for line in stderr.splitlines():
                    if line.strip():
                        self.log_box.append(f"  [log] {line}")

            ok = False
            if stdout:
                lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
                try:
                    payload = json.loads(lines[-1] if lines else stdout)
                    ok = payload.get("status") == "ok"
                    if not ok:
                        raise RuntimeError(payload.get("message") or stdout[:400])
                    pdf_path = payload.get("pdf_path") or pdf_path
                except json.JSONDecodeError:
                    if r.returncode == 0 and os.path.isfile(pdf_path):
                        ok = True
                    else:
                        raise RuntimeError(stdout[:600] or f"code={r.returncode}")

            if not ok and r.returncode != 0:
                raise RuntimeError(stderr[:600] or f"code={r.returncode}")

            self._last_result["pdf_path"] = pdf_path
            self.log_box.append(f"✓ PDF: {pdf_path}")
            if os.path.isfile(pdf_path):
                with contextlib.suppress(Exception):
                    open_path(pdf_path)
            QMessageBox.information(self, "Raport", f"Zapisano:\n{pdf_path}")
        except Exception as e:
            self.log_box.append(f"✗ Raport: {e}")
            QMessageBox.critical(self, "Błąd raportu", str(e)[:800])
        finally:
            self.btn_report.setEnabled(bool(self._last_result))

    def _write_attrs(self, result):
        """P3: write deficit_pct / ws_eff back to layer attributes."""
        layer = getattr(self, "_pending_layer", None)
        if not layer or not layer.isEditable() and not layer.startEditing():
            # try start editing
            if layer and not layer.isEditable():
                layer.startEditing()
        if not layer:
            return
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QVariant
        prov = layer.dataProvider()
        existing = {f.name().lower() for f in layer.fields()}
        to_add = []
        for name, typ in (
            ("wake_ws_free", QVariant.Double),
            ("wake_ws_eff", QVariant.Double),
            ("wake_deficit", QVariant.Double),
        ):
            if name.lower() not in existing:
                to_add.append(QgsField(name, typ))
        if to_add:
            prov.addAttributes(to_add)
            layer.updateFields()
        idx = {f.name(): i for i, f in enumerate(layer.fields())}
        by_id = {t["id"]: t for t in result.get("turbines_result") or []}
        for fid, t in by_id.items():
            attrs = {}
            if "wake_ws_free" in idx:
                attrs[idx["wake_ws_free"]] = t["ws_free_ms"]
            if "wake_ws_eff" in idx:
                attrs[idx["wake_ws_eff"]] = t["ws_eff_ms"]
            if "wake_deficit" in idx:
                attrs[idx["wake_deficit"]] = t["deficit_pct"]
            if attrs:
                prov.changeAttributeValues({int(fid): attrs})
        layer.commitChanges()
        self.log_box.append("→ Atrybuty wake_* zapisane do warstwy")

    def _load_raster(self, path, name):
        try:
            rl = QgsRasterLayer(path, name)
            if rl.isValid():
                QgsProject.instance().addMapLayer(rl)
                self.log_box.append(f"→ Raster: {name}")
        except Exception as e:
            self.log_box.append(f"⚠ raster: {e}")

    def _load_vector(self, path, name):
        try:
            vl = QgsVectorLayer(path, name, "ogr")
            if vl.isValid():
                QgsProject.instance().addMapLayer(vl)
                self.log_box.append(f"→ Vector: {name}")
        except Exception as e:
            self.log_box.append(f"⚠ vector: {e}")

    def _on_error(self, msg):
        self.log_box.append(f"✗ {msg}")
        QMessageBox.critical(self, "Błąd", msg[:800])


def main():
    global _wake_dlg
    try:
        parent = iface.mainWindow()  # noqa: F821
    except NameError:
        parent = None
    _wake_dlg = WakeMapDialog(parent=parent)
    _wake_dlg.show()
    _wake_dlg.raise_()
    _wake_dlg.activateWindow()
