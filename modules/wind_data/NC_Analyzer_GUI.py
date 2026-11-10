"""
NC_Analyzer_GUI.py
Osobne narzędzie do analizy danych NEWA (.nc).
Uruchom w edytorze Python QGIS.

Wymaga w tym samym katalogu: NC_Analizer.py, worker_nc_report.py
"""

import os
import re
import sys
import json
import tempfile
import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
from WindLab.core import remote_exec as rx
from pathlib import Path
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QProgressBar,
    QLineEdit, QFileDialog, QMessageBox, QWidget,
    QRadioButton, QButtonGroup,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont

from WindLab.core.paths import find_external_python, models_wtg_dir, report_filename
from WindLab.core.ui import (
    TurbinePicker, browse_button, hint_label, log_view, set_role, style_module_dialog,
)
from WindLab.core.ui.widgets import open_path
import contextlib

# katalog z NC_Analizer.py / worker_nc_report.py (launcher WindLab nadpisuje)
WORKERS_DIR = ""

PYTHON_EXE = find_external_python()

with contextlib.suppress(NameError):
    _here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(_here, "NC_Analizer.py")):
        WORKERS_DIR = _here

ANALYZER = os.path.join(WORKERS_DIR, "NC_Analizer.py")
REPORT_WORKER = os.path.join(WORKERS_DIR, "worker_nc_report.py")
# WindLab: WORKERS_DIR ustawiane przez launcher
DEFAULT_DATA_DIR = os.environ.get("WINDLAB_NC_DIR") or os.path.join(WORKERS_DIR, "dane_turbin")
if os.environ.get("AEP_PYTHON") and os.path.isfile(os.environ["AEP_PYTHON"]):
    PYTHON_EXE = os.environ["AEP_PYTHON"]
HEIGHT_OPTIONS = [50, 100, 150, 200, 250]


# ── Worker ────────────────────────────────────────────────────────────────────
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

            self.log.emit(f"Uruchamiam: {os.path.basename(self.script_path)}")
            self.log.emit(f"  python: {PYTHON_EXE}")

            fd, params_file = tempfile.mkstemp(suffix=".json", prefix="nc_anal_")
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
                    f"Sprawdź czy {PYTHON_EXE} ma xarray/scipy/matplotlib/reportlab."
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
class NCAnalyzerDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None
        self._last_job = None  # "analyze" | "report"

        self.setMinimumWidth(640)
        self.setMinimumHeight(560)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Katalog danych
        grp_d = QGroupBox("Katalog z plikami .nc")
        gld = QHBoxLayout(grp_d)
        self.edit_datadir = QLineEdit(DEFAULT_DATA_DIR)
        gld.addWidget(self.edit_datadir)
        btn = browse_button("Wybierz katalog z plikami .nc")
        btn.clicked.connect(self._browse_datadir)
        gld.addWidget(btn)
        root.addWidget(grp_d)

        # Tryb
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
        info = hint_label(
            "• single – generuje wykresy dla każdej turbiny osobno\n"
            "• average – uśrednia wszystkie turbiny i generuje jeden zestaw wykresów\n"
            "Uwaga: analiza działa na plikach *_meso.nc (time series).\n"
            "Raport PDF zawsze buduje średnią farmy (jak raport AEP)."
        )
        glm.addWidget(info)
        root.addWidget(grp_m)

        # Wysokość
        grp_h = QGroupBox("Wysokość analizy [m]")
        glh = QHBoxLayout(grp_h)
        self.combo_height = QComboBox()
        for h in HEIGHT_OPTIONS:
            self.combo_height.addItem(str(h), userData=h)
        self.combo_height.setCurrentText("150")
        glh.addWidget(self.combo_height)
        glh.addStretch()
        root.addWidget(grp_h)

        # Turbina do wykresu produkcji
        grp_t = QGroupBox("Turbina (wykres skumulowanej produkcji)")
        glt = QVBoxLayout(grp_t)
        self.chk_turbine = QCheckBox("Licz produkcję z krzywej mocy turbiny")
        self.chk_turbine.setChecked(True)
        self.turbine = TurbinePicker("power", str(models_wtg_dir()))
        self.chk_turbine.toggled.connect(self.turbine.setEnabled)
        glt.addWidget(self.chk_turbine)
        glt.addWidget(self.turbine)
        glt.addWidget(hint_label(
            "Produkcja liczona dla prędkości na wysokości analizy (bez strat śladu i pozostałych). "
            "Bez turbiny wykres pokazuje tylko energię wiatru."))
        root.addWidget(grp_t)

        # Katalog wykresów
        grp_p = QGroupBox("Katalog wykresów (opcjonalnie)")
        glp = QHBoxLayout(grp_p)
        self.edit_plotsdir = QLineEdit("")
        self.edit_plotsdir.setPlaceholderText("domyślnie: <katalog danych>/wykresy")
        glp.addWidget(self.edit_plotsdir)
        btnp = browse_button("Wybierz katalog wykresów")
        btnp.clicked.connect(self._browse_plotsdir)
        glp.addWidget(btnp)
        root.addWidget(grp_p)

        # Przyciski akcji
        self.btn_analyze = set_role(QPushButton("Uruchom analizę"), "primary")
        self.btn_analyze.clicked.connect(self._on_analyze)

        self.btn_report = QPushButton("Raport PDF")
        self.btn_report.setToolTip(
            "Generuje raport PDF „Analiza zasobów wiatru” "
            "(Weibull, róże wiatrów, energia, przebieg czasowy)."
        )
        self.btn_report.clicked.connect(self._on_report)

        # Log
        self.log_box = log_view(min_height=140, max_height=None)
        root.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Dolne
        btn_row = QHBoxLayout()
        self.btn_stop = set_role(QPushButton("Przerwij"), "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_report)
        btn_row.addWidget(self.btn_analyze)
        root.addLayout(btn_row)

        style_module_dialog(self, "wind", "Analiza zasobów wiatru",
                            "NEWA · rozkład Weibulla · róże wiatrów",
                            footer_items=3)  # log, postęp, przyciski

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

    def _set_busy(self, busy: bool):
        self.btn_analyze.setEnabled(not busy)
        self.btn_report.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.progress.setVisible(busy)

    def _start_worker(self, script_path, params, job_name: str):
        self._last_job = job_name
        self._set_busy(True)

        self._thread = QThread()
        self._worker = ScriptWorker(script_path, params)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)

        self._thread.start()

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
        if self.chk_turbine.isChecked() and self.turbine.path():
            params["wtg_path"] = self.turbine.path()
            params["power_curve"] = self.turbine.curve_label()

        self._log("─" * 50)
        self._log(f"Analiza: tryb={mode}, wysokość={height} m")
        self._log(f"Katalog: {data_dir}")

        self._start_worker(ANALYZER, params, "analyze")

    def _on_report(self):
        if not rx.is_remote() and not os.path.isfile(PYTHON_EXE):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono interpretera Python:\n{PYTHON_EXE}"
            )
            return
        if not rx.is_remote() and not os.path.isfile(REPORT_WORKER):
            QMessageBox.critical(
                self, "Błąd",
                f"Nie znaleziono worker_nc_report.py w:\n{WORKERS_DIR}\n\n"
                "Umieść plik worker_nc_report.py w tym samym katalogu co GUI."
            )
            return

        data_dir = self.edit_datadir.text().strip()
        if not data_dir or not Path(data_dir).is_dir():
            QMessageBox.warning(self, "Błąd", "Podaj prawidłowy katalog z plikami .nc")
            return

        height = self.combo_height.currentData()
        out_root = (
            os.environ.get("WINDLAB_OUTPUT_DIR")
            or self.edit_plotsdir.text().strip()
            or data_dir
        )
        os.makedirs(out_root, exist_ok=True)
        proj = (
            getattr(self, "_windlab_project_name", None)
            or os.environ.get("WINDLAB_PROJECT_NAME")
            or Path(data_dir).name
            or "projekt"
        )
        path = os.path.join(out_root, report_filename("ZASOBY_WIATRU", proj))

        plots_dir = self.edit_plotsdir.text().strip() or None
        if not plots_dir:
            plots_dir = os.path.join(out_root, "wykresy_nc")

        params = {
            "data_dir": data_dir,
            "output_pdf": path,
            "output_dir": plots_dir,
            "hub_height": height,
            "target_height": height,
            "project_name": proj,
        }

        self._log("─" * 50)
        self._log(f"Raport PDF: wysokość={height} m")
        self._log(f"Katalog: {data_dir}")
        self._log(f"PDF: {path}")

        self._start_worker(REPORT_WORKER, params, "report")

    def _on_finished(self, result):
        self._log("─" * 50)
        if result.get("status") == "cancelled":
            self._log("⏹  Operacja przerwana.")
            return

        job = self._last_job or "analyze"

        if result.get("status") == "ok":
            if job == "report":
                pdf = result.get("output_pdf")
                self._log(f"✓  Raport wygenerowany.")
                if pdf:
                    self._log(f"  PDF: {pdf}")
                    with contextlib.suppress(Exception):
                        open_path(pdf)
                plots = result.get("output_dir")
                if plots:
                    self._log(f"  Wykresy: {plots}")
            else:
                self._log(f"✓  Analiza zakończona. Przetworzono: {result.get('processed', [])}")
                plots = result.get("plots_dir")
                if plots:
                    self._log(f"  Wykresy: {plots}")
                    with contextlib.suppress(Exception):
                        open_path(plots)
        else:
            self._log(f"✗  Błąd: {result.get('message', result)}")

    def _on_stop(self):
        if self._worker is not None:
            self._log("⏹  Żądanie przerwania…")
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    def _on_error(self, msg):
        self._log(f"✗  BŁĄD: {msg}")
        QMessageBox.critical(self, "Błąd", msg[:600])

    def _cleanup(self):
        self._set_busy(False)
        self._thread = None
        self._worker = None


def main():
    dlg = NCAnalyzerDialog()
    dlg.show()
    return dlg



if __name__ == '__main__':
    try:
        _dlg = main()
    except Exception as e:
        from qgis.PyQt.QtWidgets import QMessageBox
        QMessageBox.critical(None, 'Błąd NC Analyzer', str(e))
