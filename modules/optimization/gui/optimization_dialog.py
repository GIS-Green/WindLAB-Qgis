# -*- coding: utf-8 -*-
"""
Dialog QGIS – Optymalizacja strat AEP (cień + hałas).
"""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
from WindLab.core import remote_exec as rx
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QCheckBox, QDoubleSpinBox, QPushButton, QLineEdit,
    QFileDialog, QMessageBox, QTextEdit, QProgressBar, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem,
)

from WindLab.core.paths import find_external_python, report_filename
from WindLab.core.ui import (
    browse_button, hint_label, log_view, set_role, style_module_dialog,
)
from WindLab.core.ui.widgets import open_path
import contextlib

PYTHON_EXE = find_external_python() or sys.executable
WORKER_OPT = ""  # ustawiane przez launcher
WORKER_REPORT = ""  # worker_optimization_report.py


KIND_LABELS = {"shadow_off": "Cień – wyłączenie", "noise_mode": "Hałas – tryb",
               "noise_stop": "Hałas – STOP"}


def _rule_extra(r: dict) -> str:
    if r.get("minutes_off"):
        return f"{r.get('minutes_off')} min"
    bins = r.get("ws_bins_affected") or []
    if bins:
        return f"{min(bins)}–{max(bins)} m/s" if len(bins) > 1 else f"{bins[0]} m/s"
    return ""


def _find_in_dir(directory: str, names) -> str:
    """Najnowszy plik wyniku: <nazwa>_<znacznik czasu>.<ext> albo starszy <nazwa>.<ext>."""
    if not directory or not os.path.isdir(directory):
        return ""
    from WindLab.core.paths import latest
    for n in names:
        base, ext = os.path.splitext(n)
        p = latest(directory, base, ext)
        if p:
            return p
    for n in names:
        p = os.path.join(directory, n)
        if os.path.isfile(p):
            return p
    # dated variants
    with contextlib.suppress(Exception):
        for fn in sorted(os.listdir(directory), reverse=True):
            for n in names:
                if fn == n or (fn.startswith(n.replace(".json", "")) and fn.endswith(".json")):
                    return os.path.join(directory, fn)
    return ""


def _find_optimization_result(directory: str) -> str:
    """Najnowszy optimization_results*.json w katalogu Output."""
    if not directory or not os.path.isdir(directory):
        return ""
    exact = os.path.join(directory, "optimization_results.json")
    if os.path.isfile(exact):
        return exact
    candidates = []
    try:
        for fn in os.listdir(directory):
            low = fn.lower()
            if not low.endswith(".json"):
                continue
            if low.startswith("optimization_results") or (
                "optimization" in low and "result" in low and "noise_modes" not in low
                and "shadow_shutdown" not in low
            ):
                candidates.append(os.path.join(directory, fn))
    except Exception:
        return ""
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


class OptWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        """Worker w zewnętrznym Pythonie (numpy/scipy/xarray – szeregi NEWA i MILP)."""
        try:
            worker = WORKER_OPT
            if not worker or not os.path.isfile(worker):
                worker = str(Path(__file__).resolve().parents[1] / "workers" / "worker_optimization.py")
            self.log.emit(f"python={PYTHON_EXE}")
            fd, pfile = tempfile.mkstemp(prefix="wl_opt_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.params, f, ensure_ascii=False)
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            env["PYTHONIOENCODING"] = "utf-8"
            out_file = pfile + ".out"
            # stdout do pliku (wynik bywa duży – pipe mógłby się zablokować), stderr = log na żywo
            with open(out_file, "w", encoding="utf-8") as fout:
                kwargs = dict(stdout=fout, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace", env=env)
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                proc = rx.Popen([PYTHON_EXE, "-X", "utf8", worker, "--params-file", pfile], **kwargs)
                for line in proc.stderr:
                    if line.strip():
                        self.log.emit(line.rstrip())
                    if self._stop:
                        proc.kill()
                        raise RuntimeError("Przerwano")
                proc.wait()
            with open(out_file, "r", encoding="utf-8") as f:
                stdout = f.read().strip()
            for p in (pfile, out_file):
                with contextlib.suppress(OSError):
                    os.remove(p)
            if not stdout:
                raise RuntimeError(f"Brak wyniku workera (code={proc.returncode})")
            data = json.loads(stdout.splitlines()[-1])
            if data.get("status") == "error":
                raise RuntimeError((data.get("message") or "Błąd workera") + "\n" + (data.get("trace") or "")[-800:])
            self.finished.emit(data)
        except Exception as e:
            self.error.emit("%s\n%s" % (e, traceback.format_exc()[-1200:]))


class OptimizationDialog(QDialog):
    def __init__(self, parent=None, project=None):
        super().__init__(parent)
        self.setMinimumWidth(860)
        self.setMinimumHeight(600)
        self._project = project
        self._thread = None
        self._worker = None
        self._last_result = None
        self._build_ui()
        self._prefill_from_project()

    def _build_ui(self):
        root = QVBoxLayout(self)

        hdr = hint_label(
            "Cień: najtańszy (MILP) kalendarz wyłączeń WTG spełniający limit roczny i dzienny, "
            "koszt z mocy oczekiwanej w danym miesiącu i godzinie (NEWA). Hałas: dobór trybów pracy producenta (z pliku .wtg) "
            "dla każdej prędkości wiatru w nocy – optymalnie (MILP), z energią liczoną "
            "z szeregów czasowych NEWA. Cel: najmniejsza strata AEP przy spełnionych limitach."
        )
        root.addWidget(hdr)

        # Wejścia
        gb = QGroupBox("Pliki wejściowe (z Output projektu)")
        fl = QFormLayout(gb)

        self.ed_shadow = QLineEdit()
        self.ed_shadow.setPlaceholderText("shadow_optimization_calendars_<data>.json")
        row = QHBoxLayout()
        row.addWidget(self.ed_shadow)
        b = browse_button("Wskaż plik JSON cienia")
        b.clicked.connect(lambda: self._browse(self.ed_shadow, "JSON cienia (*.json)"))
        row.addWidget(b)
        fl.addRow("Cień (kalendarze):", row)

        self.ed_noise = QLineEdit()
        self.ed_noise.setPlaceholderText("noise_paths.json (moduł Hałas)")
        self.ed_noise.editingFinished.connect(self._load_noise_modes)
        row = QHBoxLayout()
        row.addWidget(self.ed_noise)
        b = browse_button("Wskaż plik JSON hałasu")
        b.clicked.connect(lambda: (self._browse(self.ed_noise, "JSON hałasu (*.json)"), self._load_noise_modes()))
        row.addWidget(b)
        fl.addRow("Hałas (ścieżki):", row)

        self.ed_nc = QLineEdit()
        self.ed_nc.setPlaceholderText("katalog z plikami *_meso.nc (NEWA)")
        row = QHBoxLayout()
        row.addWidget(self.ed_nc)
        b = browse_button("Wskaż katalog NC")
        b.clicked.connect(self._browse_nc)
        row.addWidget(b)
        fl.addRow("Wiatr (NEWA):", row)

        self.ed_aep = QLineEdit()
        self.ed_aep.setPlaceholderText("opcjonalnie – wynik AEP JSON")
        row = QHBoxLayout()
        row.addWidget(self.ed_aep)
        b = browse_button("Wskaż plik JSON AEP")
        b.clicked.connect(lambda: self._browse(self.ed_aep, "JSON AEP (*.json)"))
        row.addWidget(b)
        fl.addRow("AEP (wagi):", row)

        self.ed_out = QLineEdit()
        row = QHBoxLayout()
        row.addWidget(self.ed_out)
        b = browse_button("Wybierz katalog wyjściowy")
        b.clicked.connect(self._browse_dir)
        row.addWidget(b)
        fl.addRow("Katalog wyjściowy:", row)
        root.addWidget(gb)

        # Parametry
        gb2 = QGroupBox("Parametry")
        fl2 = QFormLayout(gb2)
        self.chk_shadow = QCheckBox("Optymalizuj cień")
        self.chk_shadow.setChecked(True)
        self.chk_noise = QCheckBox("Optymalizuj hałas")
        self.chk_noise.setChecked(True)
        fl2.addRow(self.chk_shadow)
        fl2.addRow(self.chk_noise)

        self.spn_h = QDoubleSpinBox()
        self.spn_h.setRange(1, 200)
        self.spn_h.setValue(30)
        self.spn_h.setSuffix(" h/rok")
        fl2.addRow("Limit cienia (rok):", self.spn_h)

        self.chk_day = QCheckBox("Limit dzienny")
        self.chk_day.setChecked(True)
        self.spn_day = QDoubleSpinBox()
        self.spn_day.setRange(1, 240)
        self.spn_day.setDecimals(0)
        self.spn_day.setValue(30)
        self.spn_day.setSuffix(" min/dzień")
        self.chk_day.toggled.connect(self.spn_day.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.chk_day)
        row.addWidget(self.spn_day)
        row.addStretch()
        fl2.addRow("Limit cienia (dzień):", row)

        self.chk_db = QCheckBox("Nadpisz limit z modułu Hałas")
        self.spn_db = QDoubleSpinBox()
        self.spn_db.setRange(20, 80)
        self.spn_db.setValue(40)
        self.spn_db.setSuffix(" dB(A)")
        self.spn_db.setEnabled(False)
        self.chk_db.toggled.connect(self.spn_db.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.chk_db)
        row.addWidget(self.spn_db)
        row.addStretch()
        fl2.addRow("Limit hałasu (noc):", row)

        self.cmb_noise_strat = QComboBox()
        self.cmb_noise_strat.addItem("Tryby zależne od prędkości wiatru (jak WindPRO NCO)", "per_ws")
        self.cmb_noise_strat.addItem("Jeden tryb na całą noc (ocena jak w module Hałas)", "fixed")
        fl2.addRow("Strategia hałasu:", self.cmb_noise_strat)

        self.lst_modes = QListWidget()
        self.lst_modes.setMaximumHeight(110)
        fl2.addRow("Dopuszczalne tryby:", self.lst_modes)
        fl2.addRow(hint_label(
            "Tryby z pliku turbiny wskazanego w noise_paths.json (ta sama wersja danych, z widmem "
            "oktawowym). STOP jest zawsze dostępny. Noc 22:00–6:00 czasu lokalnego."))
        root.addWidget(gb2)

        # Tabela reguł
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["WTG", "Rodzaj", "Szczegóły", "Strata [MWh/rok]", "Min / prędkość"]
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMaximumHeight(220)
        root.addWidget(self.table)

        self.lbl_summary = QLabel("—")
        self.lbl_summary.setStyleSheet("font-weight:600; padding:4px 0;")
        root.addWidget(self.lbl_summary)

        self.log_box = log_view(max_height=140)
        root.addWidget(self.log_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_run = set_role(QPushButton("Uruchom optymalizację"), "primary")
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

        style_module_dialog(self, "opt", "Optymalizacja strat AEP",
                            "ograniczenia cienia i hałasu",
                            footer_items=3)  # log, postęp, przyciski

    def _prefill_from_project(self):
        out = ""
        if self._project is not None:
            try:
                out = str(self._project.output_dir)
            except Exception:
                out = os.environ.get("WINDLAB_OUTPUT_DIR", "")
        if not out:
            out = os.environ.get("WINDLAB_OUTPUT_DIR", "")
        self.ed_out.setText(out)
        nc = os.environ.get("WINDLAB_NC_DIR", "")
        if self._project is not None:
            with contextlib.suppress(Exception):
                nc = str(self._project.data_nc_dir)
        self.ed_nc.setText(nc)
        if out:
            sh = _find_in_dir(out, ["shadow_optimization_calendars.json"])
            ns = _find_in_dir(out, ["noise_paths.json"])
            aep = _find_in_dir(out, [
                "aep_results.json", "aep_result.json", "aep_per_wtg.csv",
            ])
            if not aep and os.path.isdir(out):
                try:
                    from WindLab.modules.optimization.engine.aep_weights import find_aep_weights_file
                    aep = find_aep_weights_file(out) or ""
                except Exception:
                    aep = ""
                if not aep:
                    for fn in sorted(os.listdir(out), reverse=True):
                        low = fn.lower()
                        if "optimization" in low:
                            continue
                        if ("aep" in low) and (low.endswith(".json") or low.endswith(".csv")):
                            aep = os.path.join(out, fn)
                            break
            if sh:
                self.ed_shadow.setText(sh)
            if ns:
                self.ed_noise.setText(ns)
                self._load_noise_modes()
            if aep:
                self.ed_aep.setText(aep)
        # jeśli są już wyniki optymalizacji – wczytaj i odblokuj raport
        self._try_load_existing_result(out)

    def _try_load_existing_result(self, out_dir: str = "") -> bool:
        """Wczytaj optimization_results*.json i włącz przycisk Raport PDF."""
        out_dir = out_dir or self.ed_out.text().strip()
        path = _find_optimization_result(out_dir)
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            # minimalna walidacja
            if "rules" not in data and "loss_total_mwh" not in data and "aep_before_mwh" not in data:
                return False
            files = data.get("output_files") or {}
            files["results_json"] = path
            data["output_files"] = files
            self._last_result = data
            self.btn_report.setEnabled(True)
            # podsumowanie w UI
            before = data.get("aep_before_mwh", 0) or 0
            after = data.get("aep_after_mwh", 0) or 0
            loss = data.get("loss_total_mwh", 0) or 0
            pct = data.get("loss_total_pct", 0) or 0
            n_rules = len(data.get("rules") or [])
            self.lbl_summary.setText(
                f"Wczytano wynik: {os.path.basename(path)}   |   "
                f"AEP {before:.1f} → {after:.1f} MWh   |   "
                f"strata {loss:.1f} MWh ({pct:.2f}%)   |   reguł: {n_rules}"
            )
            # tabela reguł
            rules = data.get("rules") or []
            self.table.setRowCount(len(rules))
            for i, r in enumerate(rules):
                kind = KIND_LABELS.get(r.get("kind", ""), r.get("kind", ""))
                detail = r.get("detail", "")
                loss_r = r.get("energy_loss_mwh", 0)
                extra = _rule_extra(r)
                self.table.setItem(i, 0, QTableWidgetItem(str(r.get("turbine_name", ""))))
                self.table.setItem(i, 1, QTableWidgetItem(str(kind)))
                self.table.setItem(i, 2, QTableWidgetItem(str(detail)))
                self.table.setItem(i, 3, QTableWidgetItem(f"{float(loss_r or 0):.2f}"))
                self.table.setItem(i, 4, QTableWidgetItem(extra))
            self._log(f"Wczytano istniejący wynik optymalizacji: {path}")
            self._log("Przycisk Raport PDF jest aktywny (bez ponownego liczenia).")
            return True
        except Exception as e:
            self._log(f"Nie wczytano wyniku optymalizacji: {e}")
            return False

    def _browse(
self, edit: QLineEdit, filt: str):
        start = edit.text().strip() or self.ed_out.text().strip() or ""
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik", start, filt)
        if path:
            edit.setText(path)

    def _browse_nc(self):
        path = QFileDialog.getExistingDirectory(self, "Katalog NC (NEWA)", self.ed_nc.text().strip())
        if path:
            self.ed_nc.setText(path)

    def _load_noise_modes(self):
        """Lista trybów z pliku turbiny wskazanego w noise_paths.json (zaznaczone = automatyczne)."""
        self.lst_modes.clear()
        path = self.ed_noise.text().strip()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("schema") != "windlab.noise_paths.v1":
                self._log("Plik hałasu ma stary format – uruchom ponownie moduł Hałas "
                          "z opcją zapisu danych dla optymalizacji (noise_paths.json).")
                return
            from WindLab.core.wtg import load_turbine
            from WindLab.modules.optimization.modes import candidate_modes
            tm = load_turbine(data["wtg_path"])
            base, auto, _ = candidate_modes(tm, data.get("noise_curve"))
            auto_labels = {m.label for m in auto}
            for nc, pc in tm.operating_modes():
                if not nc.has_octaves:
                    continue
                it = QListWidgetItem(f"{nc.label}   ·   {pc.rated_kw:.0f} kW, {nc.max_lwa:.1f} dB(A)")
                it.setData(Qt.ItemDataRole.UserRole, nc.label)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if nc.label in auto_labels else Qt.CheckState.Unchecked)
                if nc.label == base.label:
                    it.setText(it.text() + "   (tryb bazowy)")
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.lst_modes.addItem(it)
            self._log(f"Turbina: {tm.name} · tryb bazowy {base.label} · limit z Hałasu "
                      f"{float(data.get('limit_dba') or 0):.1f} dB(A)")
            if not self.chk_db.isChecked() and data.get("limit_dba"):
                self.spn_db.setValue(float(data["limit_dba"]))
        except Exception as e:
            self._log(f"Tryby turbiny: {e}")

    def _selected_modes(self):
        if self.lst_modes.count() == 0:
            return None
        return [self.lst_modes.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lst_modes.count())
                if self.lst_modes.item(i).checkState() == Qt.CheckState.Checked]

    def _browse_dir(self):
        start = self.ed_out.text().strip() or ""
        path = QFileDialog.getExistingDirectory(self, "Katalog wyjściowy", start)
        if path:
            self.ed_out.setText(path)
            self._try_load_existing_result(path)

    def _log(self, msg: str):
        self.log_box.append(msg)

    def _on_run(self):
        out_dir = self.ed_out.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Błąd", "Podaj katalog wyjściowy.")
            return
        os.makedirs(out_dir, exist_ok=True)

        enable_s = self.chk_shadow.isChecked()
        enable_n = self.chk_noise.isChecked()
        sel = self._selected_modes()
        if enable_n and sel is not None and len(sel) <= 1:
            ans = QMessageBox.question(
                self, "Tryby pracy",
                "Zaznaczony jest tylko tryb bazowy – jedyną redukcją hałasu będzie wyłączenie (STOP), "
                "co zwykle daje wielokrotnie większe straty niż tryby SO.\n\nKontynuować?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        if not enable_s and not enable_n:
            QMessageBox.warning(self, "Błąd", "Włącz cień i/lub hałas.")
            return

        sh = self.ed_shadow.text().strip()
        ns = self.ed_noise.text().strip()
        if enable_s and (not sh or not os.path.isfile(sh)):
            QMessageBox.warning(
                self, "Brak danych cienia",
                "Brak shadow_optimization_calendars.json.\n"
                "Uruchom analizę Flicker z opcją eksportu kalendarzy.",
            )
            return
        if enable_n and (not ns or not os.path.isfile(ns)):
            QMessageBox.warning(
                self, "Brak danych hałasu",
                "Brak noise_paths.json.\n"
                "Uruchom Hałas z opcją zapisu danych dla optymalizacji (zakładka Parametry).",
            )
            return
        nc_dir = self.ed_nc.text().strip()
        if enable_n and (not nc_dir or not os.path.isdir(nc_dir)):
            QMessageBox.warning(self, "Brak danych wiatru",
                                "Wskaż katalog z szeregami czasowymi NEWA (*_meso.nc).")
            return

        params = {
            "shadow_calendars_path": sh if enable_s else None,
            "noise_paths_path": ns if enable_n else None,
            "nc_dir": nc_dir,
            "aep_result_path": self.ed_aep.text().strip() or None,
            "limit_hours_year": self.spn_h.value(),
            "limit_minutes_day": self.spn_day.value() if self.chk_day.isChecked() else None,
            "limit_dba": self.spn_db.value() if self.chk_db.isChecked() else None,
            "enable_shadow": enable_s,
            "enable_noise": enable_n,
            "output_dir": out_dir,
            "project_name": os.environ.get("WINDLAB_PROJECT_NAME", ""),
            "noise_mode_strategy": self.cmb_noise_strat.currentData(),
            "noise_modes": self._selected_modes(),
        }

        self.log_box.clear()
        self.table.setRowCount(0)
        self.lbl_summary.setText("—")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)
        self._log("Start optymalizacji…")

        self._thread = QThread()
        self._worker = OptWorker(params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _on_stop(self):
        if self._worker:
            self._worker.stop()
        self._log("Żądanie przerwania (bieżąca iteracja domknie się)…")
        self.btn_stop.setEnabled(False)

    def _cleanup(self):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress.setVisible(False)
        self._thread = None
        self._worker = None

    def _on_error(self, msg: str):
        self._log(f"✗ {msg}")
        QMessageBox.critical(self, "Błąd optymalizacji", msg[:800])

    def _on_done(self, data: dict):
        self._last_result = data
        self._log("✓ Zakończono.")
        rules = data.get("rules") or []
        self.table.setRowCount(len(rules))
        for i, r in enumerate(rules):
            kind = KIND_LABELS.get(r.get("kind", ""), r.get("kind", ""))
            detail = r.get("detail", "")
            loss = r.get("energy_loss_mwh", 0)
            extra = _rule_extra(r)
            self.table.setItem(i, 0, QTableWidgetItem(str(r.get("turbine_name", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(kind)))
            self.table.setItem(i, 2, QTableWidgetItem(str(detail)))
            self.table.setItem(i, 3, QTableWidgetItem(f"{loss:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(extra))

        before = data.get("aep_before_mwh", 0)
        after = data.get("aep_after_mwh", 0)
        loss = data.get("loss_total_mwh", 0)
        pct = data.get("loss_total_pct", 0)
        self.lbl_summary.setText(
            f"AEP przed: {before:.1f} MWh   →   po: {after:.1f} MWh   |   "
            f"strata: {loss:.1f} MWh ({pct:.2f}%)   |   "
            f"residual cień: {data.get('residual_shadow_exceedances', 0)}   "
            f"hałas: {data.get('residual_noise_exceedances', 0)}"
        )
        files = data.get("output_files") or {}
        for k, v in files.items():
            self._log(f"  {k}: {v}")
        self.btn_report.setEnabled(True)
        QMessageBox.information(
            self, "Optymalizacja",
            f"Strata łączna: {loss:.1f} MWh ({pct:.2f}%)\n"
            f"Reguł: {len(rules)}\n"
            f"Wynik: {files.get('results_json', '—')}\n\n"
            f"Możesz wygenerować raport PDF.",
        )


    def _on_report(self):
        """Generuj PDF – preferuj subprocess (jak hałas), bez crasha QGIS."""
        if not self._last_result:
            QMessageBox.warning(self, "Raport", "Najpierw uruchom optymalizację.")
            return

        data = self._last_result
        files = data.get("output_files") or {}
        json_path = files.get("results_json") or ""
        out_dir = self.ed_out.text().strip() or (data.get("config") or {}).get("output_dir") or ""
        if not out_dir and json_path:
            out_dir = os.path.dirname(json_path)
        if not out_dir:
            QMessageBox.warning(self, "Raport", "Brak katalogu wyjściowego.")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Raport", f"Nie można utworzyć katalogu:\n{out_dir}\n{e}")
            return

        # upewnij się, że JSON wyniku leży na dysku
        if not json_path or not os.path.isfile(json_path):
            from WindLab.core.paths import run_stamp as _run_stamp
            json_path = os.path.join(out_dir, f"optimization_results_{_run_stamp(out_dir)}.json")
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                files["results_json"] = json_path
                if isinstance(self._last_result, dict):
                    self._last_result.setdefault("output_files", {})["results_json"] = json_path
                self._log(f"Zapisano JSON wyniku: {json_path}")
            except Exception as e:
                QMessageBox.critical(self, "Raport", f"Nie zapisano JSON wyniku:\n{e}")
                return

        from datetime import datetime as _dt
        stamp = _dt.now().strftime("%Y-%m-%d")
        proj = os.environ.get("WINDLAB_PROJECT_NAME") or "projekt"
        pdf_path = os.path.join(out_dir, report_filename("OPTYMALIZACJA", proj, out_dir=out_dir))
        from WindLab.core.paths import stamp_of as _stamp_of
        plots_dir = os.path.join(out_dir, "wykresy_optymalizacji_" + (_stamp_of(pdf_path) or "raport"))
        try:
            os.makedirs(plots_dir, exist_ok=True)
        except Exception:
            plots_dir = out_dir

        worker = WORKER_REPORT
        if not worker or not os.path.isfile(worker):
            worker = str(
                Path(__file__).resolve().parents[1] / "workers" / "worker_optimization_report.py"
            )

        self._log(f"Generuję raport PDF → {pdf_path}")
        self.btn_report.setEnabled(False)
        self.progress.setVisible(True)

        ok = False
        err_msg = ""

        # 1) Subprocess z zewnętrznym Pythonem (bezpieczniej dla QGIS)
        try:
            py = PYTHON_EXE or sys.executable
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            cmd = [py, worker, json_path, pdf_path, plots_dir]
            self._log(f"  python={py}")
            kwargs = dict(
                capture_output=True, text=True, timeout=300, env=env,
            )
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            r = rx.run(cmd, **kwargs)
            if r.stderr:
                for line in (r.stderr or "").splitlines()[-15:]:
                    if line.strip():
                        self._log(f"  [err] {line}")
            stdout = (r.stdout or "").strip()
            if stdout:
                last = stdout.splitlines()[-1]
                try:
                    payload = json.loads(last)
                except Exception:
                    payload = {"status": "error", "message": stdout[-500:]}
            else:
                payload = {"status": "error", "message": f"Brak stdout (code={r.returncode})"}
            if payload.get("status") == "ok" and os.path.isfile(pdf_path):
                ok = True
            else:
                err_msg = payload.get("message") or f"code={r.returncode}"
                self._log(f"  subprocess nieudany: {err_msg}")
        except Exception as e:
            err_msg = str(e)
            self._log(f"  subprocess wyjątek: {e}")

        # 2) Fallback in-process (tylko reportlab; matplotlib lazy)
        if not ok:
            self._log("  Próbuję generację lokalną (reportlab)…")
            try:
                generate_pdf = None
                try:
                    from WindLab.modules.optimization.workers import worker_optimization_report as rep_mod
                    generate_pdf = rep_mod.generate_pdf
                except Exception:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("windlab_opt_report", worker)
                    mod = importlib.util.module_from_spec(spec)
                    if spec is None or spec.loader is None:
                        raise ImportError("Nie można wczytać modułu okna")
                    spec.loader.exec_module(mod)
                    generate_pdf = mod.generate_pdf
                generate_pdf(json_path, pdf_path, plots_dir=plots_dir)
                if os.path.isfile(pdf_path):
                    ok = True
                    err_msg = ""
            except Exception as e:
                err_msg = f"{err_msg}\nLokalnie: {e}" if err_msg else str(e)
                self._log(f"  lokalnie: {e}")
                self._log(traceback.format_exc()[-600:])

        self.progress.setVisible(False)
        self.btn_report.setEnabled(True)

        if ok and os.path.isfile(pdf_path):
            self._log(f"✓ Raport: {pdf_path}")
            with contextlib.suppress(Exception):
                open_path(pdf_path)
            QMessageBox.information(self, "Raport", f"Zapisano:\n{pdf_path}")
        else:
            QMessageBox.critical(
                self, "Błąd raportu",
                f"Nie udało się wygenerować PDF.\n\n{err_msg[:700]}\n\n"
                f"Sprawdź czy w AEP_PYTHON jest reportlab:\n"
                f"  {PYTHON_EXE} -m pip install reportlab",
            )



def run_dialog(parent=None, project=None):
    with contextlib.suppress(Exception):
        from qgis.utils import iface
        parent = parent or iface.mainWindow()
    dlg = OptimizationDialog(parent=parent, project=project)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    global _opt_dlg
    _opt_dlg = dlg
    return dlg
