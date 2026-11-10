# -*- coding: utf-8 -*-
"""
GUI QGIS – styl NC_Data_Q_GUI:
  zbiera parametry, liczy silnikiem Python, generuje raport PDF (worker).
"""
from __future__ import annotations

import json
import os
import contextlib

# WindLab project overrides
_WL_OUT = os.environ.get("WINDLAB_OUTPUT_DIR", "")
_WL_NMT = os.environ.get("WINDLAB_NMT_DIR", "")
_WL_WTG = os.environ.get("WINDLAB_WTG_DIR", "")
_WL_NC = os.environ.get("WINDLAB_NC_DIR", "")
if _WL_OUT:
    OUTPUT_DIR = _WL_OUT
import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
from WindLab.core import remote_exec as rx
import sys
import tempfile
import traceback
from typing import Optional

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QDoubleSpinBox, QCheckBox,
    QPushButton, QLineEdit, QFileDialog, QMessageBox, QTextEdit,
    QProgressBar, QTabWidget, QWidget,
)
from qgis.core import QgsMapLayerProxyModel, QgsVectorLayer, QgsRasterLayer
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox

from ..engine import (
    RunConfig, GridConfig, RunResult, ReceiverResult, NoiseGrid, SourcePoint,
    AIR_ABSORPTION_LABELS, RULE_LABELS, REF_LABELS,
)
from .qgis_bridge import (
    build_sources, build_receivers, estimate_g_corine, apply_results,
    apply_noise_grid_and_isolines, fill_z_gugik,
)

from WindLab.core.paths import find_external_python, models_wtg_dir, report_filename, windlab_root
from WindLab.core.ui import (
    TurbinePicker, browse_button, hint_label, log_view, set_role, style_module_dialog,
    ElevationSourceCombo,
)
from WindLab.core.ui.widgets import open_path

PYTHON_EXE = find_external_python()


def _attenuation_table(cfg):
    from ..engine import attenuation_table  # silnik lokalny
    return attenuation_table(cfg)
WORKERS_DIR = str(windlab_root())
WORKER_NOISE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "worker_noise.py")
WTG_DIR = _WL_WTG or str(models_wtg_dir())
OUTPUT_DIR = _WL_OUT or os.path.join(WORKERS_DIR, "Output")

# Kategorie terenów chronionych – dopuszczalny poziom nocą (LAeq,N) dla „pozostałych obiektów
# i działalności” wg rozporządzenia w sprawie dopuszczalnych poziomów hałasu w środowisku.
# Wartości do potwierdzenia z aktualnym brzmieniem przepisów.
NOISE_AREAS = [
    ("Zabudowa jednorodzinna (noc)", 40.0),
    ("Zabudowa zagrodowa / wielorodzinna / mieszkaniowo-usługowa (noc)", 45.0),
    ("Strefa ochronna „A” uzdrowiska, szpitale poza miastem (noc)", 35.0),
    ("Własny limit", None),
]


class ReportWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, json_path: str, pdf_path: str, plots_dir: str):
        super().__init__()
        self.json_path = json_path
        self.pdf_path = pdf_path
        self.plots_dir = plots_dir
        self._proc = None
        self._stopped = False

    def stop(self):
        self._stopped = True
        if self._proc and self._proc.poll() is None:
            with contextlib.suppress(Exception):
                self._proc.terminate()

    def run(self):
        try:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            worker = os.path.join(here, "worker_noise_report.py")
            if not rx.is_remote() and not os.path.isfile(worker):
                raise RuntimeError(f"Brak worker_noise_report.py\n{worker}")
            py = PYTHON_EXE if os.path.isfile(PYTHON_EXE) else sys.executable
            self.log.emit(f"Raport: {py}")
            self.log.emit(f"Worker: {worker}")
            cmd = [py, "-u", worker, self.json_path, self.pdf_path, self.plots_dir]
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            kwargs = dict(
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            self._proc = rx.Popen(cmd, **kwargs)
            lines = []
            for line in self._proc.stdout:
                if self._stopped:
                    break
                line = line.rstrip("\n\r")
                if line.strip():
                    self.log.emit(line)
                    lines.append(line)
            try:
                ret = self._proc.wait(timeout=300)  # max 5 min na raport
            except subprocess.TimeoutExpired:
                self.log.emit("Timeout 5 min – przerywam workera")
                with contextlib.suppress(Exception):
                    self._proc.kill()
                raise RuntimeError(
                    "Worker raportu przekroczył 5 min. "
                    "Sprawdź PYTHON_EXE i czy reportlab/matplotlib są zainstalowane."
                )
            if self._stopped:
                self.finished.emit({"status": "cancelled"})
                return
            result = {"status": "ok" if ret == 0 else "error", "pdf": self.pdf_path}
            for line in reversed(lines):
                if line.strip().startswith("{"):
                    with contextlib.suppress(json.JSONDecodeError):
                        result = json.loads(line)
                        break
            if ret != 0 and result.get("status") != "ok":
                raise RuntimeError(result.get("message") or f"Worker exit {ret}")
            self.finished.emit(result)
        except Exception as e:
            if self._stopped:
                self.finished.emit({"status": "cancelled"})
            else:
                self.error.emit(str(e))
        finally:
            self._proc = None


class NoiseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(680)
        self.setMinimumHeight(600)
        self._root = None
        self._last_result = None
        self._last_sources = None
        self._last_receivers = None
        self._last_meta = None
        self._thread = None
        self._worker = None
        self._build()
        self._autodetect()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        tabs = QTabWidget()
        tabs.addTab(self._tab_setup(), "1. Warstwy i model")
        tabs.addTab(self._tab_params(), "2. Parametry")
        tabs.addTab(self._tab_map(), "3. Mapa / izolinie")
        root.addWidget(tabs)

        self.log_box = log_view(max_height=150)
        root.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_run = set_role(QPushButton("Oblicz hałas"), "primary")
        self.btn_run.clicked.connect(self._on_run)
        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self._on_report)
        self.btn_stop = set_role(QPushButton("Przerwij"), "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(btn_close)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_report)
        row.addWidget(self.btn_run)
        root.addLayout(row)

        style_module_dialog(self, "noise", "Hałas (ISO 9613-2)",
                            "analiza hałasu",
                            footer_items=3)  # log, postęp, przyciski

    def _tab_setup(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        gb = QGroupBox("Turbiny")
        fl = QFormLayout(gb)
        self.cmb_turb = QgsMapLayerComboBox()
        self.cmb_turb.setFilters(QgsMapLayerProxyModel.Filter.PointLayer)
        fl.addRow("Warstwa punktowa:", self.cmb_turb)
        self.cmb_hh = QgsFieldComboBox()
        self.cmb_hh.setAllowEmptyFieldName(True)
        self.cmb_turb.layerChanged.connect(self.cmb_hh.setLayer)
        self.cmb_hh.setLayer(self.cmb_turb.currentLayer())
        fl.addRow("Pole hub_height:", self.cmb_hh)
        self.chk_ghh = QCheckBox("Globalna wysokość piasty dla wszystkich turbin")
        self.spn_ghh = QDoubleSpinBox()
        self.spn_ghh.setRange(10, 300); self.spn_ghh.setValue(149); self.spn_ghh.setSuffix(" m")
        self.spn_ghh.setEnabled(False)
        self.chk_ghh.toggled.connect(self.spn_ghh.setEnabled)
        fl.addRow(self.chk_ghh); fl.addRow("Globalna HH:", self.spn_ghh)
        lay.addWidget(gb)

        gb = QGroupBox("Typ turbiny i tryb pracy")
        tl = QVBoxLayout(gb)
        self.turbine = TurbinePicker("noise", WTG_DIR)
        tl.addWidget(self.turbine)
        lay.addWidget(gb)

        gb = QGroupBox("Receptory (NSA)")
        fl = QFormLayout(gb)
        self.cmb_rec = QgsMapLayerComboBox()
        self.cmb_rec.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        fl.addRow("Warstwa (punkt / poligon):", self.cmb_rec)
        self.spn_rh = QDoubleSpinBox()
        self.spn_rh.setRange(0.5, 50); self.spn_rh.setValue(4.0); self.spn_rh.setSuffix(" m")
        fl.addRow("Wysokość receptora:", self.spn_rh)
        lay.addWidget(gb)

        gb = QGroupBox("Teren (opcjonalnie)")
        fl = QFormLayout(gb)
        self.cmb_dem = QgsMapLayerComboBox()
        self.cmb_dem.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)
        self.cmb_dem.setAllowEmptyLayer(True)
        self.cmb_elev = ElevationSourceCombo(local_widgets=[self.cmb_dem])
        fl.addRow("Źródło wysokości:", self.cmb_elev)
        fl.addRow("NMT / DEM:", self.cmb_dem)
        self.cmb_corine = QgsMapLayerComboBox()
        self.cmb_corine.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        self.cmb_corine.setAllowEmptyLayer(True)
        self.spn_g = QDoubleSpinBox()
        self.spn_g.setRange(0, 1); self.spn_g.setSingleStep(0.1); self.spn_g.setValue(0.5)
        fl.addRow("Współczynnik gruntu G:", self.spn_g)
        self.chk_corine = QCheckBox("Wyznacz G z CORINE (średnia wzdłuż ścieżek WTG–NSA)")
        self.chk_corine.setChecked(False)
        self.cmb_corine.setEnabled(False)
        self.chk_corine.toggled.connect(self.cmb_corine.setEnabled)
        fl.addRow(self.chk_corine)
        if rx.is_remote():
            # CORINE leży na serwerze obliczeń – warstwa w QGIS niepotrzebna
            self.chk_corine.setText("Wyznacz G z CORINE (dane na serwerze, średnia wzdłuż ścieżek WTG–NSA)")
            self.cmb_corine.setVisible(False)
        else:
            fl.addRow("Warstwa CORINE:", self.cmb_corine)
        fl.addRow(hint_label("WindPRO (ISO 9613-2 Polska) liczy ze stałym G = 0,5. "
                             "G z CORINE (np. 0,7 dla terenów rolnych) obniża poziomy o ok. 0,7 dB "
                             "i zmniejsza izofony – wyniki nie będą wtedy porównywalne z WindPRO."))
        lay.addWidget(gb)
        lay.addStretch()
        return w

    def _tab_params(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        gb = QGroupBox("Moc akustyczna źródła")
        fl = QFormLayout(gb)
        self.cmb_rule = QComboBox()
        for key in ("loudest95", "fixed", "max"):
            self.cmb_rule.addItem(RULE_LABELS[key], key)
        fl.addRow("Punkt z krzywej:", self.cmb_rule)
        self.spn_ws = QDoubleSpinBox()
        self.spn_ws.setRange(1, 25); self.spn_ws.setValue(10.0); self.spn_ws.setSuffix(" m/s")
        self.spn_ws.setEnabled(False)
        self.cmb_rule.currentIndexChanged.connect(
            lambda *_: self.spn_ws.setEnabled(self.cmb_rule.currentData() == "fixed"))
        fl.addRow("Prędkość wiatru:", self.spn_ws)
        self.cmb_ref = QComboBox()
        for key in ("10m", "hub"):
            self.cmb_ref.addItem(REF_LABELS[key], key)
        self.cmb_ref.setToolTip(
            "Producent podaje krzywe hałasu dla prędkości na 10 m i na wysokości piasty – z różnymi\n"
            "widmami. WindPRO „ISO 9613-2 Poland” używa prędkości na 10 m.")
        fl.addRow("Odniesienie prędkości:", self.cmb_ref)
        lay.addWidget(gb)

        gb = QGroupBox("Propagacja ISO 9613-2")
        fl = QFormLayout(gb)
        self.cmb_ground = QComboBox()
        self.cmb_ground.addItem("Ogólna (pasma oktawowe) – teren płaski", "general")
        self.cmb_ground.addItem("Uproszczona (poziom A, Dω) – teren pagórkowaty", "simplified")
        fl.addRow("Tłumienie gruntu Agr:", self.cmb_ground)
        self.cmb_air = QComboBox()
        for key, label in AIR_ABSORPTION_LABELS.items():
            self.cmb_air.addItem(label, key)
        fl.addRow("Tłumienie atmosferyczne:", self.cmb_air)
        self.spn_c0 = QDoubleSpinBox()
        self.spn_c0.setRange(0, 5); self.spn_c0.setDecimals(1); self.spn_c0.setSingleStep(0.5)
        self.spn_c0.setValue(0.0); self.spn_c0.setSuffix(" dB")
        self.spn_c0.setToolTip("Współczynnik meteorologiczny C0 (WindPRO Poland: zalecane maks. 2 dB; 0 = z wiatrem)")
        fl.addRow("C0 (Cmet):", self.spn_c0)
        self.spn_rad = QDoubleSpinBox()
        self.spn_rad.setRange(100, 50000); self.spn_rad.setValue(20000); self.spn_rad.setSuffix(" m")
        fl.addRow("Maks. odległość WTG–receptor:", self.spn_rad)
        self.spn_offset = QDoubleSpinBox()
        self.spn_offset.setRange(-10, 10); self.spn_offset.setDecimals(1)
        self.spn_offset.setSingleStep(0.5); self.spn_offset.setValue(0.0); self.spn_offset.setSuffix(" dB")
        self.spn_offset.setToolTip("Dodawany do wyników na receptorach i mapie (jak „Uncertainty margin” w WindPRO)")
        fl.addRow("Margines niepewności:", self.spn_offset)
        lay.addWidget(gb)

        gb = QGroupBox("Limit hałasu")
        fl = QFormLayout(gb)
        self.cmb_area = QComboBox()
        for label, lim in NOISE_AREAS:
            self.cmb_area.addItem(label, lim)
        self.cmb_area.setCurrentIndex(1)
        fl.addRow("Rodzaj terenu:", self.cmb_area)
        self.spn_lim = QDoubleSpinBox()
        self.spn_lim.setRange(20, 80); self.spn_lim.setValue(45); self.spn_lim.setSuffix(" dB(A)")
        fl.addRow("Limit:", self.spn_lim)
        self.cmb_area.currentIndexChanged.connect(self._on_area)
        lay.addWidget(gb)

        gb_opt = QGroupBox("Optymalizacja / straty AEP")
        fl_opt = QFormLayout(gb_opt)
        self.chk_for_opt = QCheckBox("Oblicz hałas na potrzeby optymalizacji")
        self.chk_for_opt.setChecked(False)
        self.chk_for_opt.setToolTip(
            "Zapisuje noise_paths.json: tłumienie każdej ścieżki WTG → receptor w pasmach oktawowych\n"
            "oraz opis turbin. Moduł optymalizacji liczy z tego poziomy dla każdego trybu pracy\n"
            "i prędkości wiatru (dane trybów bierze z pliku .wtg).")
        fl_opt.addRow(self.chk_for_opt)
        lay.addWidget(gb_opt)

        gb = QGroupBox("Raport PDF")
        fl = QFormLayout(gb)
        self.ed_project = QLineEdit(os.environ.get("WINDLAB_PROJECT_NAME") or "")
        fl.addRow("Nazwa projektu:", self.ed_project)
        row = QHBoxLayout()
        self.ed_pdf = QLineEdit("")
        self.ed_pdf.setPlaceholderText("ścieżka do PDF…")
        bp = browse_button("Wskaż plik PDF"); bp.clicked.connect(self._browse_pdf)
        row.addWidget(self.ed_pdf); row.addWidget(bp)
        fl.addRow("PDF:", row)
        lay.addWidget(gb)
        lay.addStretch()
        return w

    def _tab_map(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        gb = QGroupBox("Siatka hałasu – wyniki")
        fl = QFormLayout(gb)
        self.chk_iso = QCheckBox("Izolinie (SHP)")
        self.chk_iso.setChecked(True)
        fl.addRow(self.chk_iso)
        self.chk_raster = QCheckBox("Raster (GeoTIFF)")
        self.chk_raster.setChecked(False)
        fl.addRow(self.chk_raster)
        self.spn_res = QDoubleSpinBox()
        self.spn_res.setRange(10, 1000); self.spn_res.setValue(50); self.spn_res.setSuffix(" m")
        fl.addRow("Rozdzielczość siatki:", self.spn_res)
        self.spn_buf = QDoubleSpinBox()
        self.spn_buf.setRange(100, 20000); self.spn_buf.setValue(2000); self.spn_buf.setSuffix(" m")
        fl.addRow("Bufor wokół turbin:", self.spn_buf)
        self.ed_levels = QLineEdit("35, 40, 45, 50, 55")
        fl.addRow("Poziomy izolinii [dB(A)]:", self.ed_levels)
        lay.addWidget(gb)

        gb2 = QGroupBox("Katalog wyjściowy")
        fl2 = QFormLayout(gb2)
        row = QHBoxLayout()
        self.ed_outdir = QLineEdit(OUTPUT_DIR)
        b = browse_button("Wybierz katalog wyjściowy")
        b.clicked.connect(self._browse_outdir)
        row.addWidget(self.ed_outdir); row.addWidget(b)
        fl2.addRow(row)
        info = hint_label("Warstwy, raster, izolinie i raport PDF trafiają tutaj.")
        fl2.addRow(info)
        lay.addWidget(gb2)
        lay.addStretch()
        return w

    def _browse_outdir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Katalog wyjściowy", self.ed_outdir.text() or OUTPUT_DIR
        )
        if path:
            self.ed_outdir.setText(path)

    def _run_remote(self, cfg, crs, g_corine, want_paths, source_spec=None):
        """Obliczenia na serwerze (worker_noise.py) → (RunResult, paths, G, opis G, źródła)."""
        params = {"config": cfg.to_dict(), "crs": crs, "g_corine": g_corine, "want_paths": want_paths,
                  "source_spec": source_spec}
        fd, pfile = tempfile.mkstemp(prefix="wl_noise_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(params, f, ensure_ascii=False)
            r = rx.run([PYTHON_EXE, WORKER_NOISE, "--params-file", pfile],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(pfile)
        for line in (r.stderr or "").splitlines():
            if line.strip():
                self._log(f"  {line}")
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"Brak wyniku obliczeń hałasu (kod {r.returncode})")
        out = json.loads(lines[-1])
        if out.get("status") != "ok":
            raise RuntimeError(out.get("message") or "Błąd obliczeń hałasu")
        d = out["result"]
        grid = d.get("noise_grid")
        result = RunResult(
            results=[ReceiverResult(**x) for x in d["results"]],
            n_sources=d["n_sources"], n_receivers=d["n_receivers"],
            max_noise_dba=d["max_noise_dba"], n_exceedances=d["n_exceedances"],
            engine=d["engine"], messages=d.get("messages") or [],
            noise_grid=NoiseGrid(**grid) if grid else None,
        )
        srcs = []
        for s in out.get("sources") or []:
            if s.get("lw_octave"):
                s["lw_octave"] = {int(k): v for k, v in s["lw_octave"].items()}
            srcs.append(SourcePoint(**s))
        return (result, out.get("paths"), out.get("ground_g", cfg.ground_g),
                out.get("g_source", "stały"), srcs or list(cfg.sources))

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _browse_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Zapisz raport PDF", self.ed_pdf.text() or report_filename("HALAS", self.ed_project.text()), "PDF (*.pdf)")
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.ed_pdf.setText(path)

    def _autodetect(self):
        """Zgodność wsteczna – dane turbin pochodzą z plików .wtg (TurbinePicker)."""
        self._root = WTG_DIR

    def _on_area(self, *_):
        lim = self.cmb_area.currentData()
        if lim is not None:
            self.spn_lim.setValue(float(lim))

    def _on_run(self):
        try:
            self._do_run()
        except Exception as e:
            self._log(f"✗  BŁĄD: {e}")
            self._log(traceback.format_exc())
            QMessageBox.critical(self, "Błąd", str(e)[:800])
            self.progress.setVisible(False)
            self.btn_run.setEnabled(True)

    def _do_run(self):
        self.btn_run.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.progress.setVisible(True)
        self._log("─" * 50)
        wtg_path = self.turbine.path()
        curve = self.turbine.curve()
        if not wtg_path or curve is None:
            raise ValueError("Wybierz plik turbiny (.wtg) z krzywą hałasu")
        model_name = f"{self.turbine.model().name} · {curve.label}"
        turb = self.cmb_turb.currentLayer()
        rec = self.cmb_rec.currentLayer()
        if not isinstance(turb, QgsVectorLayer):
            raise ValueError("Wybierz warstwę turbin")
        if not isinstance(rec, QgsVectorLayer):
            raise ValueError("Wybierz warstwę receptorów")
        dem = self.cmb_dem.currentLayer()
        dem = dem if isinstance(dem, QgsRasterLayer) and not self.cmb_elev.is_gugik() else None
        corine = self.cmb_corine.currentLayer()
        corine = corine if isinstance(corine, QgsVectorLayer) else None

        rule = self.cmb_rule.currentData()
        ws_ref = self.cmb_ref.currentData()
        self._log(f"Turbina: {model_name}")
        sources = build_sources(
            turb, wtg_path, curve.label,
            hub_height_field=self.cmb_hh.currentField() or None,
            global_hub_height=self.spn_ghh.value() if self.chk_ghh.isChecked() else None,
            rule=rule, wind_speed=self.spn_ws.value(), ws_ref=ws_ref, dem=dem, log=self._log,
            remote=rx.is_remote(),
        )
        if not sources:
            raise ValueError("Brak punktów turbin")
        rx.check_turbine_limit(len(sources))      # wersja demo – przed GUGiK i wysyłką
        receivers = build_receivers(
            rec, receiver_height=self.spn_rh.value(),
            limit_dba=self.spn_lim.value(), dem=dem,
        )
        if not receivers:
            raise ValueError("Brak receptorów")
        if self.cmb_elev.is_gugik():
            fill_z_gugik(sources, turb.crs().authid(), log=self._log)
            fill_z_gugik(receivers, rec.crs().authid(), log=self._log)
        if len(receivers) > 2000:
            self._log(
                f"⚠  UWAGA: {len(receivers)} receptorów – wygląda na warstwę CORINE/gęstą, "
                f"nie NSA. Do limitu hałasu wybierz warstwę punktów zabudowy (~dziesiątki)."
            )
            from qgis.PyQt.QtWidgets import QMessageBox as _MB
            ans = _MB.question(
                self, "Dużo receptorów",
                f"Warstwa receptorów ma {len(receivers)} obiektów.\n"
                f"To zwykle CORINE lub inna warstwa pokrycia terenu, nie NSA.\n\n"
                f"Kontynuować? (obliczenia i raport będą wolniejsze)",
                _MB.Yes | _MB.No, _MB.No,
            )
            if ans != _MB.Yes:
                self.progress.setVisible(False)
                self.btn_run.setEnabled(True)
                return
        g = self.spn_g.value()
        g_source = "stały"
        g_corine_server = rx.is_remote() and self.chk_corine.isChecked()
        if g_corine_server:
            self._log("G z CORINE – wyznaczy serwer obliczeń")
        elif corine is not None and self.chk_corine.isChecked():
            try:
                g = estimate_g_corine(corine, sources, receivers, fallback=g)
                g_source = "z CORINE"
                self._log(f"G z CORINE ≈ {g:.2f}")
            except Exception as e:
                self._log(f"CORINE: {e} – użyto G = {g:.2f}")
        else:
            self._log(f"G = {g:.2f} (stały)")

        out_dir = self.ed_outdir.text().strip() or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        grid_cfg = None
        want_grid = self.chk_iso.isChecked() or self.chk_raster.isChecked()
        if want_grid:
            levels = []
            for part in self.ed_levels.text().replace(";", ",").split(","):
                part = part.strip()
                if part:
                    with contextlib.suppress(ValueError): levels.append(float(part))
            if not levels:
                levels = [35.0, 40.0, 45.0, 50.0, 55.0]
            grid_cfg = GridConfig(
                enabled=True, resolution_m=self.spn_res.value(),
                buffer_m=self.spn_buf.value(), iso_levels=levels,
                receiver_height=self.spn_rh.value(),
            )
        cfg = RunConfig(
            sources=sources, receivers=receivers, engine="iso",
            wind_speed=self.spn_ws.value(), max_radius_m=self.spn_rad.value(),
            receiver_limit_dba=self.spn_lim.value(), ground_g=g,
            level_offset_db=self.spn_offset.value(), grid=grid_cfg,
            ground_method=self.cmb_ground.currentData(),
            air_absorption=self.cmb_air.currentData(),
            c0_db=self.spn_c0.value(),
        )
        self._log("Obliczenia ISO 9613-2…")
        paths = None
        if rx.is_remote():
            spec = {"wtg_path": wtg_path, "curve_label": curve.label, "rule": rule,
                    "wind_speed": self.spn_ws.value(), "ref": ws_ref}
            result, paths, g, g_source, srcs = self._run_remote(
                cfg, turb.crs().authid() or "EPSG:2180", g_corine_server,
                want_paths=self.chk_for_opt.isChecked(), source_spec=spec)
            sources[:] = srcs          # dane akustyczne z serwera (do raportu i optymalizacji)
            cfg.sources = sources
            cfg.ground_g = g
            if g_corine_server:
                self._log(f"G {g_source} ≈ {g:.2f}")
        else:
            from ..engine import run      # silnik lokalny (wersja z obliczeniami w QGIS)
            result = run(cfg)
        for m in result.messages:
            self._log(m)
        crs = rec.crs().authid() or "EPSG:2180"
        layer = apply_results(result, crs_authid=crs)
        self._log(f"Warstwa: {layer.name()}")
        isolines_json = None
        if result.noise_grid is not None:
            rast, iso, isolines_json = apply_noise_grid_and_isolines(
                result, crs_authid=crs,
                output_dir=out_dir,
                write_raster=self.chk_raster.isChecked(),
                write_isolines=self.chk_iso.isChecked(),
            )
            if rast: self._log(f"Raster: {rast.name()}")
            if iso: self._log(f"Izolinie: {iso.name()} ({iso.featureCount()})")
            if isolines_json: self._log(f"Izolinie JSON (raport): {isolines_json}")
        self._log(f"Gotowe. Max={result.max_noise_dba:.1f} dB(A), przekr.={result.n_exceedances}/{result.n_receivers}")

        self._last_result = result
        self._last_sources = [
            {"id": s.id, "x": s.x, "y": s.y, "hub_height": s.hub_height,
             "lwa": s.lwa, "model_name": s.model_name, "z_ground": s.z_ground,
             "name": s.name or f"WTG{i+1}", "lw_octave": s.lw_octave,
             "noise_ws": s.noise_ws, "noise_ref": s.noise_ref, "noise_hub_height": s.noise_hub_height}
            for i, s in enumerate(sources)
        ]
        self._last_receivers = [
            {"id": r.id, "x": r.x, "y": r.y, "name": r.name or f"R{i+1}",
             "receiver_height": r.receiver_height, "z_ground": r.z_ground}
            for i, r in enumerate(receivers)
        ]
        self._last_meta = {
            "project": self.ed_project.text().strip() or os.environ.get("WINDLAB_PROJECT_NAME") or "Noise study",
            "project_name": self.ed_project.text().strip() or os.environ.get("WINDLAB_PROJECT_NAME") or "Noise study",
            "crs": crs, "turbine_model": model_name,
            "wtg_path": wtg_path, "noise_curve": curve.label,
            "hub_height": self.spn_ghh.value() if self.chk_ghh.isChecked() else sources[0].hub_height,
            "wind_speed": sources[0].noise_ws, "ws_ref": ws_ref, "source_rule": rule,
            "source_rule_label": RULE_LABELS.get(rule, rule),
            "noise_hub_height": sources[0].noise_hub_height,
            "ground_g": g, "ground_g_source": g_source, "ground_method": cfg.ground_method,
            "air_absorption": cfg.air_absorption,
            "air_absorption_label": AIR_ABSORPTION_LABELS.get(cfg.air_absorption, cfg.air_absorption),
            "c0_db": cfg.c0_db, "max_radius_m": cfg.max_radius_m,
            "limit_dba": self.spn_lim.value(), "limit_label": self.cmb_area.currentText(),
            "receiver_height": self.spn_rh.value(),
            "lwa": sources[0].lwa, "offset_db": self.spn_offset.value(),
            "octaves": sources[0].lw_octave,
            "method_notes": list(result.messages),
            "output_dir": out_dir,
            "isolines_json": isolines_json,
        }
        opt_path = None
        if self.chk_for_opt.isChecked():
            self._log("─" * 50)
            self._log("Optymalizacja: tłumienie ścieżek WTG → receptor (pasma oktawowe)…")
            try:
                payload = {
                    "schema": "windlab.noise_paths.v1",
                    "project": self._last_meta.get("project"),
                    "crs": crs,
                    "wtg_path": wtg_path,
                    "noise_curve": curve.label,
                    "ws_ref": ws_ref,
                    "limit_dba": self.spn_lim.value(),
                    "margin_db": self.spn_offset.value(),
                    "settings": {"ground_g": g, "ground_method": cfg.ground_method,
                                 "air_absorption": cfg.air_absorption, "c0_db": cfg.c0_db},
                    "sources": [
                        {"id": s.id, "name": s.name or f"WTG{i + 1}", "x": s.x, "y": s.y,
                         "hub_height": s.hub_height, "z_ground": s.z_ground}
                        for i, s in enumerate(sources)
                    ],
                    "receivers": [
                        {"id": r.id, "name": r.name, "x": r.x, "y": r.y,
                         "limit_dba": r.limit_dba if r.limit_dba is not None else self.spn_lim.value()}
                        for r in receivers
                    ],
                    "paths": paths if paths is not None else _attenuation_table(cfg),
                }
                opt_path = os.path.join(out_dir, "noise_paths.json")
                with open(opt_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                self._last_meta["optimization_matrix_path"] = opt_path
                self._log(f"✓  Dane dla optymalizacji: {opt_path}")
            except Exception as e:
                self._log(f"✗  Dane dla optymalizacji: {e}")
                self._log(traceback.format_exc()[-800:])

        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_report.setEnabled(True)
        extra = f"\nOptymalizacja: {opt_path}" if opt_path else ""
        QMessageBox.information(
            self, "Sukces",
            f"Model: {model_name}\nMax: {result.max_noise_dba:.1f} dB(A)\n"
            f"Przekroczenia: {result.n_exceedances}/{result.n_receivers}{extra}",
        )

    def _on_report(self):
        if self._last_result is None:
            QMessageBox.warning(self, "Raport", "Najpierw uruchom obliczenia.")
            return
        import re
        from datetime import datetime
        out_dir = self.ed_outdir.text().strip() or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        proj = (
            (self._last_meta or {}).get("project")
            or (self.ed_project.text().strip() if hasattr(self, "ed_project") else "")
            or os.environ.get("WINDLAB_PROJECT_NAME")
            or "projekt"
        )
        if self._last_meta is not None:
            self._last_meta["project"] = proj
            self._last_meta["project_name"] = proj
        pdf_path = os.path.join(out_dir, report_filename("HALAS", proj))
        if hasattr(self, "ed_pdf"):
            self.ed_pdf.setText(pdf_path)
        os.makedirs(os.path.dirname(pdf_path) or ".", exist_ok=True)
        plots_dir = os.path.join(os.path.dirname(pdf_path), "noise_plots")
        # bez noise_grid – pełna siatka potrafi mieć setki tys. komórek i blokuje worker
        result_dict = self._last_result.to_dict()
        result_dict.pop("noise_grid", None)
        payload = {
            "meta": self._last_meta or {},
            "result": result_dict,
            "sources": self._last_sources or [],
            "receivers": self._last_receivers or [],
        }
        fd, json_path = tempfile.mkstemp(suffix=".json", prefix="noise_result_")
        os.close(fd)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        self.btn_run.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)
        self._log("─" * 50)
        self._log(f"Generowanie raportu → {pdf_path}")
        self._thread = QThread()
        self._worker = ReportWorker(json_path, pdf_path, plots_dir)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_report_done)
        self._worker.error.connect(self._on_report_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._tmp_json = json_path
        self._thread.start()

    def _on_report_done(self, result):
        if result.get("status") == "cancelled":
            self._log("⏹  Raport przerwany.")
            return
        pdf = result.get("pdf") or self.ed_pdf.text()
        self._log(f"✓  Raport: {pdf}")
        with contextlib.suppress(Exception):
            open_path(pdf)
        QMessageBox.information(self, "Raport", f"Zapisano:\n{pdf}")

    def _on_report_error(self, msg):
        self._log(f"✗  Raport: {msg}")
        QMessageBox.critical(self, "Błąd raportu", msg[:600])

    def _on_stop(self):
        if self._worker is not None:
            self._log("⏹  Żądanie przerwania…")
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    def _cleanup_thread(self):
        self.btn_run.setEnabled(True)
        self.btn_report.setEnabled(self._last_result is not None)
        self.btn_stop.setEnabled(False)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None
        tmp = getattr(self, "_tmp_json", None)
        if tmp and os.path.isfile(tmp):
            with contextlib.suppress(OSError): os.remove(tmp)


def run_dialog(parent=None):
    with contextlib.suppress(Exception):
        from qgis.utils import iface
        parent = parent or iface.mainWindow()
    dlg = NoiseDialog(parent=parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    global _noise_dlg
    _noise_dlg = dlg
    return dlg
