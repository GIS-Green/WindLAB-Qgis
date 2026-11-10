# -*- coding: utf-8 -*-
"""
AEP dialog (QGIS) – NEWA meso time-series + microscale ratio.
Run from QGIS Python editor or plugin action:
    from AEP.gui.aep_dialog import main; main()
"""

from __future__ import annotations

import os
import re
import sys
import json
from WindLab.core import remote_exec as rx
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QDoubleSpinBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QSizePolicy, QButtonGroup, QRadioButton,
    QLineEdit, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
)

from WindLab.core.paths import report_filename
from WindLab.core.ui import (
    TurbinePicker, browse_button, layer_icon, hint_label, log_view, set_role, style_module_dialog,
)
from WindLab.core.ui.widgets import open_path
import contextlib

# ── Paths relative to this package ────────────────────────────────────────────
_AEP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_WORKERS = os.path.join(_AEP_ROOT, "workers")
WORKER_AEP = os.path.join(_WORKERS, "worker_aep.py")
REPORT_GEN = os.path.join(_WORKERS, "worker_aep_report.py")


def _resolve_data_root() -> str:
    """Find folder that contains InputNC / WTG / Output – prefer WindLab project."""
    for key in ("AEP_DATA_ROOT", "WINDLAB_OUTPUT_DIR"):
        env = os.environ.get(key, "").strip()
        if key == "WINDLAB_OUTPUT_DIR" and env:
            # parent of Output is project dir
            parent = os.path.dirname(env)
            if parent and os.path.isdir(parent):
                return parent
        if env and os.path.isdir(env):
            return env
    candidates = [
        os.path.abspath(os.path.join(_AEP_ROOT, os.pardir, os.pardir)),
        os.path.abspath(os.path.join(_AEP_ROOT, os.pardir)),
        _AEP_ROOT,
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "InputNC")) or os.path.isdir(os.path.join(c, "Data", "NC")) or os.path.isdir(os.path.join(c, "WTG")):
            return c
    return candidates[0]


_DATA_ROOT = _resolve_data_root()
DEFAULT_WTG_DIR = os.environ.get("WINDLAB_WTG_DIR", os.path.join(_DATA_ROOT, "WTG"))
DEFAULT_NC_DIR = os.environ.get("WINDLAB_NC_DIR", os.path.join(_DATA_ROOT, "Data", "NC") if os.path.isdir(os.path.join(_DATA_ROOT, "Data", "NC")) else os.path.join(_DATA_ROOT, "InputNC"))
DEFAULT_OUTPUT_DIR = os.environ.get("WINDLAB_OUTPUT_DIR", os.path.join(_DATA_ROOT, "Output"))

def _find_python_exe() -> str:
    """Python for workers: external env with calculation libraries (not QGIS python).

    Priority:
      1. env AEP_PYTHON
      2. common standalone paths (original: C:\\Python3\\python.exe)
      3. QGIS apps/Python* only as last resort
    """
    env = (os.environ.get("AEP_PYTHON") or "").strip().strip('"')
    if env and os.path.isfile(env):
        return env

    # Original project convention + frequent installs
    standalone = [
        r"C:\Python3\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
        r"C:\Python39\python.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python311\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python310\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python39\python.exe"),
    ]
    for cand in standalone:
        if os.path.isfile(cand):
            return cand

    # Optional: py launcher
    for cand in (r"C:\Windows\py.exe",):
        if os.path.isfile(cand):
            return cand

    # Last resort: QGIS bundled Python (usually without calculation libraries)
    prefixes = []
    for key in ("QGIS_PREFIX_PATH", "OSGEO4W_ROOT"):
        v = os.environ.get(key)
        if v:
            prefixes.append(v)
    exe = sys.executable or ""
    if exe:
        cur = os.path.dirname(os.path.abspath(exe))
        for _ in range(6):
            prefixes.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    for root in prefixes:
        apps = os.path.join(root, "apps")
        if not os.path.isdir(apps):
            continue
        for name in sorted(os.listdir(apps), reverse=True):
            if name.lower().startswith("python"):
                for cand in (
                    os.path.join(apps, name, "python.exe"),
                    os.path.join(apps, name, "python3.exe"),
                ):
                    if os.path.isfile(cand):
                        return cand

    return exe or "python"


PYTHON_EXE = _find_python_exe()

WAKE_MODELS = [
    ("BastankhahGaussian", "Bastankhah Gaussian [domyślny]"),
    ("NiayifarGaussian", "Niayifar Gaussian"),
    ("ZongGaussian", "Zong Gaussian [najdokładniejszy]"),
    ("NOJ", "NOJ – Jensen top-hat [najszybszy]"),
    ("GCL", "GCL – Larsen"),
    ("TurboGaussian", "TurboGaussian"),
]


class AepWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, params, python_exe=None):
        super().__init__()
        self.params = params
        self.python_exe = python_exe or PYTHON_EXE

    def run(self):
        try:
            self.log.emit("Uruchamiam obliczenia AEP...")
            result = self._run_sub(WORKER_AEP, self.params, timeout=3600)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _run_sub(self, script, params, timeout=600):
        import tempfile

        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        # Avoid QGIS plugin noise / accidental GUI
        env["QT_QPA_PLATFORM"] = env.get("AEP_QT_PLATFORM", "offscreen")

        py = self.python_exe
        base = os.path.basename(py).lower()
        if not rx.is_remote() and "qgis" in base and "python" not in base:
            raise RuntimeError(
                f"Błędny interpreter: {py}\n"
                "To wygląda na QGIS, nie na Python.\n"
                "Ustaw zmienną środowiskową AEP_PYTHON na python.exe, np.\n"
                r"  C:\Program Files\QGIS 3.34.0\apps\Python39\python.exe"
            )

        fd, params_path = tempfile.mkstemp(suffix=".json", prefix="aep_params_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(params, f, ensure_ascii=False)
            self.log.emit(f"  Python: {py}")
            self.log.emit(f"  Worker: {script}")
            r = rx.run(
                [py, script, "--params-file", params_path],
                capture_output=True, env=env, timeout=timeout,
            )
        finally:
            with contextlib.suppress(Exception):
                os.unlink(params_path)

        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        stderr = r.stderr.decode("utf-8", errors="replace").strip()

        if stderr:
            for line in stderr.splitlines():
                if line.strip():
                    self.log.emit(f"  [log] {line}")

        if not stdout:
            raise RuntimeError(
                f"Brak outputu z {os.path.basename(script)} "
                f"(returncode={r.returncode})\n{stderr[:600]}"
            )

        # last JSON object in stdout (ignore stray prints)
        lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
        raw = lines[-1] if lines else stdout
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Niepoprawny JSON z workera:\n{stdout[:800]}"
            )
        if data.get("status") == "error":
            raise RuntimeError(
                f"Błąd workera: {data.get('message', '?')}\n{data.get('trace', '')}"
            )
        return data


class AepDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(820)
        self.setMinimumHeight(600)

        self._thread = None
        self._worker = None
        self._last_result = None

        self._build_ui()
        self._populate_layers()
        self._refresh_nc_preview()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Input ─────────────────────────────────────────────────────────────
        grp_input = QGroupBox("Dane wejściowe")
        gl = QGridLayout(grp_input)

        gl.addWidget(QLabel("Warstwa turbin:"), 0, 0)
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        gl.addWidget(self.layer_combo, 0, 1, 1, 2)

        gl.addWidget(QLabel("Katalog NC (meso/micro):"), 2, 0)
        self.nc_edit = QLineEdit(DEFAULT_NC_DIR if os.path.isdir(DEFAULT_NC_DIR) else "")
        gl.addWidget(self.nc_edit, 2, 1)
        btn_nc = browse_button("Wybierz katalog NC")
        btn_nc.clicked.connect(lambda: self._browse_dir(self.nc_edit, self._refresh_nc_preview))
        gl.addWidget(btn_nc, 2, 2)

        gl.addWidget(QLabel("Katalog wyjściowy:"), 3, 0)
        self.out_edit = QLineEdit(
            DEFAULT_OUTPUT_DIR if os.path.isdir(os.path.dirname(DEFAULT_OUTPUT_DIR))
            else DEFAULT_OUTPUT_DIR
        )
        gl.addWidget(self.out_edit, 3, 1)
        btn_out = browse_button("Wybierz katalog wyjściowy")
        btn_out.clicked.connect(lambda: self._browse_dir(self.out_edit))
        gl.addWidget(btn_out, 3, 2)

        gl.addWidget(QLabel("Nazwa projektu:"), 4, 0)
        self.project_edit = QLineEdit(os.environ.get("WINDLAB_PROJECT_NAME") or "")
        gl.addWidget(self.project_edit, 4, 1, 1, 2)

        self.nc_preview = hint_label()
        gl.addWidget(self.nc_preview, 5, 0, 1, 3)

        layout.addWidget(grp_input)

        # ── Turbina ───────────────────────────────────────────────────────────
        grp_turb = QGroupBox("Turbina")
        tl = QVBoxLayout(grp_turb)
        self.turbine = TurbinePicker("power", DEFAULT_WTG_DIR)
        tl.addWidget(self.turbine)
        layout.addWidget(grp_turb)

        # ── Wake + inflow ─────────────────────────────────────────────────────
        grp_model = QGroupBox("Model wake / inflow")
        gl_m = QGridLayout(grp_model)
        self.model_combo = QComboBox()
        for key, label in WAKE_MODELS:
            self.model_combo.addItem(label, userData=key)
        gl_m.addWidget(self.model_combo, 0, 0, 1, 2)

        gl_m.addWidget(QLabel("TI:"), 1, 0)
        self.ti_spin = QDoubleSpinBox()
        self.ti_spin.setRange(0.01, 0.30)
        self.ti_spin.setSingleStep(0.01)
        self.ti_spin.setDecimals(3)
        self.ti_spin.setValue(0.06)
        gl_m.addWidget(self.ti_spin, 1, 1)

        self.cb_micro = QCheckBox("Korekta microscale (scale = micro/meso mean)")
        self.cb_micro.setChecked(True)
        gl_m.addWidget(self.cb_micro, 2, 0, 1, 2)

        self.rb_per_turbine = QRadioButton("Inflow: per turbina (lokalne WS)")
        self.rb_farm_mean = QRadioButton("Inflow: średnia farmy (szybsze)")
        self.rb_per_turbine.setChecked(True)
        gl_m.addWidget(self.rb_per_turbine, 3, 0, 1, 2)
        gl_m.addWidget(self.rb_farm_mean, 4, 0, 1, 2)
        layout.addWidget(grp_model)

        # ── Hub height ────────────────────────────────────────────────────────
        grp_hub = QGroupBox("Wysokość piasty")
        gl_h = QGridLayout(grp_hub)
        self.rb_from_layer = QRadioButton("Z warstwy (atrybut hub_height)")
        self.rb_from_layer.setChecked(True)
        self.rb_manual = QRadioButton("Ręcznie (jedna wartość)")
        gl_h.addWidget(self.rb_from_layer, 0, 0, 1, 2)
        gl_h.addWidget(self.rb_manual, 1, 0)
        self.hub_spin = QDoubleSpinBox()
        self.hub_spin.setRange(50, 250)
        self.hub_spin.setValue(120)
        self.hub_spin.setEnabled(False)
        gl_h.addWidget(self.hub_spin, 1, 1)
        self.rb_manual.toggled.connect(self.hub_spin.setEnabled)
        layout.addWidget(grp_hub)

        # ── Losses ────────────────────────────────────────────────────────────
        grp_loss = QGroupBox("Straty")
        gl_l = QGridLayout(grp_loss)
        self.rb_wake_only = QRadioButton("Tylko wake")
        self.rb_wake_only.setChecked(True)
        self.rb_global = QRadioButton("Globalna strata")
        self.rb_detailed = QRadioButton("Szczegółowa tabela")
        gl_l.addWidget(self.rb_wake_only, 0, 0)
        gl_l.addWidget(self.rb_global, 1, 0)
        gl_l.addWidget(self.rb_detailed, 2, 0)

        self.global_spin = QDoubleSpinBox()
        self.global_spin.setRange(0, 100)
        self.global_spin.setValue(10)
        self.global_spin.setEnabled(False)
        gl_l.addWidget(QLabel("Strata [%]:"), 1, 1)
        gl_l.addWidget(self.global_spin, 1, 2)
        self.rb_global.toggled.connect(self.global_spin.setEnabled)

        self.loss_table = QTableWidget()
        self.loss_table.setColumnCount(3)
        self.loss_table.setHorizontalHeaderLabels(["Nazwa", "Wartość [%]", "Aktywna"])
        self.loss_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.loss_table.setEnabled(False)
        gl_l.addWidget(self.loss_table, 3, 0, 1, 3)

        btn_add = QPushButton("Dodaj stratę")
        btn_add.clicked.connect(lambda: self._add_loss_row())
        btn_rm = QPushButton("Usuń wybraną")
        btn_rm.clicked.connect(self._remove_loss_row)
        hl = QHBoxLayout()
        hl.addWidget(btn_add)
        hl.addWidget(btn_rm)
        gl_l.addLayout(hl, 4, 0, 1, 3)

        self._add_loss_row("Dostępność", 3.0, True)
        self._add_loss_row("Straty elektryczne", 2.0, True)
        self._add_loss_row("Zablokowania", 1.0, True)
        self.rb_detailed.toggled.connect(self.loss_table.setEnabled)
        layout.addWidget(grp_loss)

        # ── Log / progress ────────────────────────────────────────────────────
        self.log_box = log_view(max_height=150)
        layout.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        self.btn_run = set_role(QPushButton("Oblicz AEP"), "primary")
        self.btn_run.clicked.connect(self._on_run)

        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self._on_report)

        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        btn_layout.addWidget(self.btn_report)
        btn_layout.addWidget(self.btn_run)
        layout.addLayout(btn_layout)

        style_module_dialog(self, "aep", "Produktywność farmy (AEP)",
                            "NEWA time-series · microscale",
                            footer_items=3)  # log, postęp, przyciski

    # ── Populate ──────────────────────────────────────────────────────────────
    def _populate_layers(self):
        self.layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 0:
                self.layer_combo.addItem(layer_icon(layer), layer.name(), layer)

    def _browse_dir(self, line_edit, after=None):
        path = QFileDialog.getExistingDirectory(self, "Wybierz katalog", line_edit.text())
        if path:
            line_edit.setText(path)
            if after:
                after()

    def _on_layer_changed(self, *_):
        self._refresh_nc_preview()

    def _refresh_nc_preview(self):
        nc_dir = self.nc_edit.text().strip()
        if not os.path.isdir(nc_dir):
            self.nc_preview.setText("Katalog NC: (brak)")
            return
        import re
        meso = set()
        micro = set()
        for f in os.listdir(nc_dir):
            m = re.match(r"^(.+)_meso\.nc$", f, re.I)
            if m:
                meso.add(m.group(1))
            m = re.match(r"^(.+)_micro\.nc$", f, re.I)
            if m:
                micro.add(m.group(1))
        paired = sorted(meso & micro)
        only_m = sorted(meso - micro)
        msg = f"Pary NC: {len(paired)}  ({', '.join(paired[:12])}{'…' if len(paired) > 12 else ''})"
        if only_m:
            msg += f"  |  bez micro: {', '.join(only_m)}"
        self.nc_preview.setText(msg)

    def _add_loss_row(self, name="", value=0.0, active=True):
        row = self.loss_table.rowCount()
        self.loss_table.insertRow(row)
        self.loss_table.setItem(row, 0, QTableWidgetItem(str(name)))
        self.loss_table.setItem(row, 1, QTableWidgetItem(str(value)))
        chk = QCheckBox()
        chk.setChecked(active)
        self.loss_table.setCellWidget(row, 2, chk)

    def _remove_loss_row(self):
        row = self.loss_table.currentRow()
        if row >= 0:
            self.loss_table.removeRow(row)

    def _get_loss_config(self):
        if self.rb_wake_only.isChecked():
            return {"loss_type": "wake_only"}
        if self.rb_global.isChecked():
            return {"loss_type": "global", "global_loss_pct": self.global_spin.value()}
        losses = []
        for row in range(self.loss_table.rowCount()):
            name = self.loss_table.item(row, 0).text()
            val = float(self.loss_table.item(row, 1).text())
            chk = self.loss_table.cellWidget(row, 2)
            active = chk.isChecked() if chk else False
            losses.append({"name": name, "value": val, "active": active})
        return {"loss_type": "detailed", "detailed_losses": losses}

    def _discover_nc_names(self, nc_dir):
        import re
        meso, micro = set(), set()
        for f in os.listdir(nc_dir):
            m = re.match(r"^(.+)_meso\.nc$", f, re.I)
            if m:
                meso.add(m.group(1))
            m = re.match(r"^(.+)_micro\.nc$", f, re.I)
            if m:
                micro.add(m.group(1))
        return sorted(meso & micro)

    @staticmethod
    def _label_aliases(raw: str) -> list:
        """Possible keys linking layer USERLABEL to NC stem (e.g. WTG_4a).

        Layer often has: 1, 4a, 7b
        NC files:        WTG_1_meso.nc, WTG_4a_micro.nc
        Sometimes layer already has WTG_1.
        """
        s = str(raw).strip()
        if not s:
            return []
        aliases = {s, s.upper(), s.lower()}
        # strip common prefixes
        for prefix in ("WTG_", "wtg_", "Wtg_", "T", "t"):
            if s.startswith(prefix) and len(s) > len(prefix):
                rest = s[len(prefix):]
                aliases.add(rest)
                aliases.add(rest.upper())
                aliases.add(rest.lower())
                aliases.add("WTG_" + rest)
                aliases.add("WTG_" + rest.upper() if rest.upper() != rest else "WTG_" + rest)
        # add WTG_ + value
        if not s.upper().startswith("WTG_"):
            aliases.add("WTG_" + s)
            aliases.add("WTG_" + s.upper())
            aliases.add("WTG_" + s.lower())
        return list(aliases)

    def _get_turbines_from_layer(self, required_names):
        """Match layer features to NC stems via USERLABEL.

        NC stem: WTG_1, WTG_4a
        Layer USERLABEL: 1, 4a  (or already WTG_1)
        """
        layer = self.layer_combo.currentData()
        if not layer:
            return None, "Wybierz warstwę punktową z turbinami"

        crs_src = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_2180 = QgsCoordinateReferenceSystem("EPSG:2180")
        t_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
        t_m = QgsCoordinateTransform(crs_src, crs_2180, QgsProject.instance())

        field_names = [f.name() for f in layer.fields()]
        userlabel_field = None
        for name in field_names:
            if name.lower() in ("userlabel", "name", "nazwa", "label", "wtg"):
                userlabel_field = name
                break

        if userlabel_field is None:
            return None, (
                "Warstwa musi mieć atrybut USERLABEL (lub name/nazwa).\n"
                "Wartość: numer turbiny jak w pliku NC, np. 1, 4a, 7b\n"
                "(pliki: WTG_1_meso.nc, WTG_4a_micro.nc)."
            )

        # Map every alias -> feature record
        by_alias = {}
        for feat in layer.getFeatures():
            val = feat[userlabel_field]
            label = str(val).strip() if val not in (None, "") else ""
            if not label:
                continue
            pt = feat.geometry().asPoint()
            p_wgs = t_wgs.transform(pt)
            p_m = t_m.transform(pt)
            if self.rb_from_layer.isChecked():
                try:
                    h = float(feat["hub_height"])
                except Exception:
                    return None, f"Brak lub błędny hub_height dla USERLABEL={label}"
            else:
                h = self.hub_spin.value()
            rec = {
                "lon": round(p_wgs.x(), 6),
                "lat": round(p_wgs.y(), 6),
                "x": round(p_m.x(), 2),
                "y": round(p_m.y(), 2),
                "h": h,
                "id": feat.id(),
                "raw_label": label,
            }
            for alias in self._label_aliases(label):
                by_alias[alias] = rec
                by_alias[alias.upper()] = rec
                by_alias[alias.lower()] = rec

        missing = []
        matched = {}
        for name in required_names:
            rec = (
                by_alias.get(name)
                or by_alias.get(name.upper())
                or by_alias.get(name.lower())
            )
            if rec is None:
                # try stem without WTG_
                stem = name[4:] if name.upper().startswith("WTG_") else name
                rec = (
                    by_alias.get(stem)
                    or by_alias.get(stem.upper())
                    or by_alias.get(stem.lower())
                )
            if rec is None:
                missing.append(name)
            else:
                matched[name] = rec

        if missing:
            examples = sorted({r.get("raw_label", "") for r in by_alias.values() if r.get("raw_label")})[:15]
            return None, (
                "Brak punktów w warstwie dla: " + ", ".join(missing)
                + f"\n(atrybut {userlabel_field})\n"
                "Oczekiwane: wartość z USERLABEL po dodaniu przedrostka WTG_ "
                "ma dać nazwę pliku NC (np. USERLABEL=4a → WTG_4a_meso.nc).\n"
                f"Przykładowe USERLABEL w warstwie: {', '.join(examples)}"
            )

        turbines = {
            "lons": [], "lats": [], "hub_heights": [],
            "ids": [], "x_2180": [], "y_2180": [],
            "userlabel": [], "names": [],
        }
        for name in required_names:
            rec = matched[name]
            turbines["lons"].append(rec["lon"])
            turbines["lats"].append(rec["lat"])
            turbines["hub_heights"].append(rec["h"])
            turbines["ids"].append(rec["id"])
            turbines["x_2180"].append(rec["x"])
            turbines["y_2180"].append(rec["y"])
            turbines["userlabel"].append(name)  # canonical NC stem for report
            turbines["names"].append(name)

        return turbines, None

    def _on_run(self):
        wtg_path = self.turbine.path()
        from WindLab.core.wtg.remote import is_remote_wtg
        if not wtg_path or not (is_remote_wtg(wtg_path) or os.path.isfile(wtg_path)):
            QMessageBox.warning(self, "Błąd", "Wybierz prawidłowy plik .wtg")
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
            QMessageBox.warning(
                self, "Błąd",
                "Brak par *_meso.nc / *_micro.nc w katalogu NC",
            )
            return

        turbines, err = self._get_turbines_from_layer(names)
        if err:
            QMessageBox.warning(self, "Błąd", err)
            return

        out_dir = self.out_edit.text().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        loss_config = self._get_loss_config()
        params = {
            "turbines": turbines,
            "wtg_path": wtg_path,
            "power_curve": curve.label,
            "nc_dir": nc_dir,
            "output_dir": out_dir,
            "wake_model": self.model_combo.currentData(),
            "ti": self.ti_spin.value(),
            "ws_bin_step": 0.5,
            "wd_bin_step": 5.0,
            "inflow_mode": "per_turbine" if self.rb_per_turbine.isChecked() else "farm_mean",
            "apply_micro_scale": self.cb_micro.isChecked(),
            "project_name": self.project_edit.text().strip() or os.environ.get("WINDLAB_PROJECT_NAME") or "projekt",
            **loss_config,
        }

        self.btn_run.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.progress.setVisible(True)
        self.log_box.clear()
        self.log_box.append(f"Turbiny: {', '.join(names)}")
        self.log_box.append(f"Inflow: {params['inflow_mode']}  |  microscale: {params['apply_micro_scale']}")

        self._thread = QThread()
        self._worker = AepWorker(params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log_box.append)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self):
        self.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None

    def _on_finished(self, result):
        self._last_result = result
        if isinstance(self._last_result, dict) and not self._last_result.get("project_name"):
            self._last_result["project_name"] = (
                self.project_edit.text().strip()
                if hasattr(self, "project_edit") else
                os.environ.get("WINDLAB_PROJECT_NAME") or "projekt"
            )
        self.btn_report.setEnabled(True)
        self.log_box.append("=" * 50)
        self.log_box.append("✅ Obliczenia zakończone.")
        self.log_box.append(f"AEP końcowe: {result['aep_final_mwh']:.1f} MWh/rok")
        self.log_box.append(f"CF: {result['capacity_factor_pct']:.1f} %")
        self.log_box.append(f"Wake loss: {result['wake_loss_pct']:.2f} %")
        self.log_box.append(f"CSV: {result.get('csv_path', '')}")
        # trwały JSON dla modułu optymalizacji (gdy worker nie zapisał)
        try:
            out_dir = result.get("output_dir") or self.out_edit.text().strip()
            if out_dir:
                import os, json
                os.makedirs(out_dir, exist_ok=True)
                jp = result.get("json_path") or os.path.join(out_dir, "aep_results.json")
                if not os.path.isfile(jp):
                    with open(jp, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    result["json_path"] = jp
                self.log_box.append(f"JSON: {jp}")
        except Exception as e:
            self.log_box.append(f"  WARN JSON: {e}")
        for w in (result.get("position_warnings") or []) + (result.get("scale_warnings") or []):
            self.log_box.append(f"  ⚠ {w}")

    def _on_error(self, msg):
        self.log_box.append(f"❌ BŁĄD: {msg}")
        QMessageBox.critical(self, "Błąd obliczeń", msg[:800])

    def _on_report(self):
        if not self._last_result:
            return
        import tempfile
        proj = (
            (self.project_edit.text().strip() if hasattr(self, "project_edit") else "")
            or os.environ.get("WINDLAB_PROJECT_NAME")
            or self._last_result.get("project_name")
            or "projekt"
        )
        if isinstance(self._last_result, dict):
            self._last_result["project_name"] = proj
        fd, json_path = tempfile.mkstemp(suffix=".json", prefix="aep_result_", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._last_result, f, ensure_ascii=False, indent=2)

        out_dir = self._last_result.get("output_dir") or self.out_edit.text().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        pdf_path = os.path.join(out_dir, report_filename("AEP", proj))
        nc_dir = self.nc_edit.text().strip()

        self.log_box.append("Generuję raport PDF...")
        self.btn_report.setEnabled(False)
        try:
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            proc = rx.run(
                [PYTHON_EXE, REPORT_GEN, json_path, nc_dir, pdf_path, out_dir],
                capture_output=True, text=True, env=env, timeout=300,
            )
            if proc.stdout:
                self.log_box.append(proc.stdout.strip())
            if proc.stderr:
                for line in proc.stderr.strip().splitlines():
                    self.log_box.append(f"  {line}")
            if proc.returncode != 0:
                raise RuntimeError(f"Generator raportu kod {proc.returncode}")
            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"Brak PDF: {pdf_path}")
            self.log_box.append(f"✅ Raport PDF: {pdf_path}")
            open_path(pdf_path)
        except Exception as e:
            self.log_box.append(f"❌ Błąd raportu: {e}")
            QMessageBox.critical(self, "Błąd", str(e))
        finally:
            self.btn_report.setEnabled(True)
            with contextlib.suppress(Exception):
                os.unlink(json_path)


def main():
    global _aep_dlg
    try:
        parent = iface.mainWindow()  # noqa: F821 – QGIS global
    except NameError:
        parent = None
    _aep_dlg = AepDialog(parent=parent)
    _aep_dlg.show()
    _aep_dlg.raise_()
    _aep_dlg.activateWindow()


if __name__ == "__main__":
    main()
