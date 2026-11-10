# -*- coding: utf-8 -*-
"""Vestas Swept Path - okno dialogowe wtyczki (v2)."""

import math
import traceback

from qgis.core import (
    QgsProject, QgsMapLayerProxyModel, QgsRasterLayer, QgsGeometry
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox,
    QPushButton, QProgressBar, QPlainTextEdit, QLabel,
    QMessageBox, QGroupBox,
)

from WindLab.core.ui import hint_label, set_role, style_module_dialog

from .vestas_models import MODELS, ACTIVE_DOLLY, DEFAULT_STEER_GAIN, DEFAULT_TRACTOR_R_MIN, DEFAULT_WHEEL_WEIGHT
from .vestas_runner import run_simulation
from WindLab.core.ui.widgets import open_path
import contextlib


class DialogFeedback:
    def __init__(self, progress_bar, log_widget):
        self.progress_bar = progress_bar
        self.log_widget = log_widget

    def pushInfo(self, msg):
        self.log_widget.appendPlainText(str(msg))
        QCoreApplication.processEvents()

    def pushWarning(self, msg):
        self.log_widget.appendPlainText(f"UWAGA: {msg}")
        QCoreApplication.processEvents()

    def reportError(self, msg):
        self.log_widget.appendPlainText(f"BLAD: {msg}")
        QCoreApplication.processEvents()

    def setProgress(self, value):
        self.progress_bar.setValue(max(0, min(100, int(value))))
        QCoreApplication.processEvents()

    def isCanceled(self):
        return False


def _dspin(lo, hi, val, step=0.1, suffix=""):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(val)
    if suffix:
        w.setSuffix(f" {suffix}")
    return w


def _ispin(lo, hi, val, step=1):
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(val)
    return w


class VestasDialog(QDialog):
    MODELS = list(MODELS)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(680)
        self.setMinimumHeight(560)
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        box_layers = QGroupBox("Warstwy wejściowe")
        f = QFormLayout(box_layers)
        self.route_combo = QgsMapLayerComboBox()
        self.route_combo.setFilters(QgsMapLayerProxyModel.Filter.LineLayer)
        f.addRow("Linia trasy (kierunek jazdy):", self.route_combo)
        self.road_combo = QgsMapLayerComboBox()
        self.road_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        f.addRow("Poligon drogi (nawierzchnia):", self.road_combo)
        main.addWidget(box_layers)

        box_turb = QGroupBox("Turbina")
        f = QFormLayout(box_turb)
        self.model_combo = QComboBox()
        self.model_combo.addItems(self.MODELS)
        self.model_combo.setCurrentIndex(1)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        f.addRow("Model turbiny Vestas:", self.model_combo)
        self.dolly_cb = QCheckBox("Aktywne sterowanie wózkiem tylnym (Active Steering)")
        self.dolly_cb.setChecked(True)
        f.addRow("Tryb wózka:", self.dolly_cb)
        self.dolly_hint = hint_label("")
        f.addRow("", self.dolly_hint)
        self.steer_gain_spin = _dspin(0.50, 1.20, DEFAULT_STEER_GAIN, 0.05)
        f.addRow("Steer gain (active steering):", self.steer_gain_spin)
        main.addWidget(box_turb)

        box_sim = QGroupBox("Parametry symulacji")
        f = QFormLayout(box_sim)
        self.ds_spin = _dspin(0.1, 5.0, 0.5, 0.1, "m")
        self.dense_spin = _ispin(100, 5000, 2000, 100)
        self.offsets_spin = _ispin(3, 25, 13, 1)
        self.snaps_spin = _ispin(2, 200, 100, 5)
        self.smooth_spin = _dspin(0.0, 3.0, 0.6, 0.1, "m")
        self.wheel_w_spin = _dspin(50.0, 2000.0, DEFAULT_WHEEL_WEIGHT, 50.0)
        self.path_mode_combo = QComboBox()
        self.path_mode_combo.addItem("Przesunięcie stałe całej trasy", "const")
        self.path_mode_combo.addItem("Przesunięcie tylko na łuku", "local")
        self.path_mode_combo.addItem("Trzy przesunięcia: wjazd – środek – wyjście (wolniej)", "three")
        self.r_min_spin = _dspin(6.0, 40.0, DEFAULT_TRACTOR_R_MIN, 0.5, "m")
        self.path_mode_combo.currentIndexChanged.connect(
            lambda _i: self.r_min_spin.setEnabled(self.path_mode_combo.currentData() == "three"))
        self.r_min_spin.setEnabled(False)
        self.force_widen_cb = QCheckBox("Wymuś generowanie hardstandu")
        f.addRow("Krok symulacji ds (Blade / Dolly / Snapshots):", self.ds_spin)
        f.addRow("Liczba kroków dense (Vehicle Envelope):", self.dense_spin)
        f.addRow("Liczba prób offsetu:", self.offsets_spin)
        f.addRow("Liczba klatek animacji (1 klatka = 1 s):", self.snaps_spin)
        f.addRow("Promień wygładzania blade/dolly (0 = brak):", self.smooth_spin)
        f.addRow("Waga kary za koła poza drogą:", self.wheel_w_spin)
        f.addRow("Tor ciągnika:", self.path_mode_combo)
        f.addRow("Min. promień toru ciągnika:", self.r_min_spin)
        f.addRow("", hint_label("Trzy przesunięcia: optymalizator dobiera tor „szeroko–ciasno–szeroko” "
                                "(gładka krzywa, bez skoków kierownicy); dziesiątki symulacji na łuk."))
        f.addRow("", self.force_widen_cb)
        main.addWidget(box_sim)

        self.terrain_cb = QCheckBox("Wykonaj analizę terenu (NMT/DEM)")
        self.terrain_cb.setStyleSheet("font-weight:600;")
        self.terrain_cb.toggled.connect(self._on_terrain_toggled)
        main.addWidget(self.terrain_cb)
        from WindLab.core import demo
        if demo.DEMO:
            self.terrain_cb.setChecked(False)
            self.terrain_cb.setEnabled(False)
            self.terrain_cb.setText(f"Wykonaj analizę terenu (NMT/DEM) – {demo.NOTE}")

        self.terrain_box = QGroupBox("Parametry analizy terenu")
        ft = QFormLayout(self.terrain_box)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)
        ft.addRow("Warstwa NMT/DEM (raster):", self.dem_combo)
        self.blade_h_spin = _dspin(1.0, 10.0, 4.5, 0.1, "m")
        ft.addRow("Wysokość łopaty nad jezdnią:", self.blade_h_spin)
        self.sl_warn_spin = _dspin(1.0, 20.0, 8.0, 0.5, "%")
        ft.addRow("Spadek podłużny – asysta ciągnięcia od:", self.sl_warn_spin)
        self.sl_crit_spin = _dspin(1.0, 25.0, 14.0, 0.5, "%")
        ft.addRow("Spadek podłużny – krytyczny od:", self.sl_crit_spin)
        self.vert_r_spin = _dspin(50.0, 2000.0, 600.0, 50.0, "m")
        ft.addRow("Min. promień łuku pionowego:", self.vert_r_spin)
        self.emb_dz_spin = _dspin(0.2, 5.0, 1.5, 0.1, "m")
        ft.addRow("Próg skarpy (teren vs dolna krawędź łopaty):", self.emb_dz_spin)
        self.emb_step_spin = _dspin(1.0, 10.0, 3.0, 0.5, "m")
        ft.addRow("Krok siatki próbkowania skarp:", self.emb_step_spin)
        hint = hint_label(
            "Progi domyślne wg Vestas 0098-1890 (2023): spadek podłużny > 8 % asysta, > 14 % krytyczny; "
            "spadek poprzeczny maks. 2 %; łuk pionowy V150 600 m, V162/V172 700 m.\n"
            "Warstwy: 10 Slope Profile (ascent/descent) | 11 Cross Slope | "
            "12 Vertical Curve | 13 Embankment (vs dolna krawędź łopaty)")
        ft.addRow("", hint)
        self.terrain_box.setVisible(False)
        main.addWidget(self.terrain_box)

        self.log = set_role(QPlainTextEdit(), "log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        self.log.setMaximumHeight(160)
        main.addWidget(self.log)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        main.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.run_btn = set_role(QPushButton("Uruchom symulację"), "primary")
        self.run_btn.clicked.connect(self._on_run_clicked)
        self.report_btn = QPushButton("Raport PDF")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_report_clicked)
        self.close_btn = QPushButton("Zamknij")
        self.close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.close_btn)
        btn_row.addWidget(self.report_btn)
        btn_row.addWidget(self.run_btn)
        main.addLayout(btn_row)
        self._on_model_changed(self.model_combo.currentIndex())

        style_module_dialog(self, "transport", "Transport turbin",
                            "swept path · analiza terenu",
                            footer_items=3)  # log, postęp, przyciski

    def _on_terrain_toggled(self, checked):
        self.terrain_box.setVisible(checked)

    def _on_model_changed(self, _idx):
        name = self.model_combo.currentText()
        with contextlib.suppress(ImportError):
            from .vestas_params import VERT_R_MIN_BY_MODEL   # tabela Vestas – wersja z obliczeniami lokalnymi
            self.vert_r_spin.setValue(VERT_R_MIN_BY_MODEL.get(name, 600.0))
        supports = ACTIVE_DOLLY[name]
        if not supports:
            self.dolly_cb.setChecked(False)
            self.dolly_cb.setEnabled(False)
            self.steer_gain_spin.setEnabled(False)
            self.dolly_hint.setText(f"{name} nie obsluguje Active Steering - Fixed.")
        else:
            self.dolly_cb.setEnabled(True)
            self.steer_gain_spin.setEnabled(True)
            self.dolly_hint.setText("")

    def _on_run_clicked(self):
        route_lyr = self.route_combo.currentLayer()
        road_lyr = self.road_combo.currentLayer()
        if route_lyr is None or road_lyr is None:
            QMessageBox.warning(self, "Brak warstw",
                "Wybierz warstwe trasy (linia) i warstwe drogi (poligon).")
            return
        do_terrain = self.terrain_cb.isChecked()
        dem_lyr = self.dem_combo.currentLayer() if do_terrain else None
        if do_terrain and (dem_lyr is None or not isinstance(dem_lyr, QgsRasterLayer)):
            QMessageBox.warning(self, "Brak DEM",
                "Zaznaczono analize DEM - wybierz warstwe rastrowa NMT.")
            return
        model_name = self.model_combo.currentText()
        use_active = self.dolly_cb.isChecked()
        self.log.clear()
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        feedback = DialogFeedback(self.progress, self.log)
        swept = None
        try:
            swept = run_simulation(
                route_lyr, road_lyr, model_name, use_active,
                self.ds_spin.value(), self.dense_spin.value(),
                self.offsets_spin.value(), self.snaps_spin.value(),
                self.smooth_spin.value(), self.force_widen_cb.isChecked(),
                feedback,
                steer_gain=self.steer_gain_spin.value(),
                wheel_weight=self.wheel_w_spin.value(),
                local_offset=self.path_mode_combo.currentData() == "local",
                path_mode=self.path_mode_combo.currentData(),
                r_min_tractor=self.r_min_spin.value())
        except Exception:
            feedback.reportError("Nieoczekiwany wyjatek podczas symulacji:")
            feedback.reportError(traceback.format_exc())
        finally:
            self.run_btn.setEnabled(True)
            self.close_btn.setEnabled(True)
        if not swept:
            return
        project = QgsProject.instance()
        # Tylko wybrane warstwy wynikowe
        for key in ("hardstand", "blade", "snapshots", "tpl_bearing", "tpl_swivel", "tpl_missing"):
            if key in swept and swept[key] is not None:
                project.addMapLayer(swept[key])
        self._last = {"swept": swept, "terrain": None, "dem_checks": None, "road": road_lyr,
                      "settings": {"ds": self.ds_spin.value(), "n_off": self.offsets_spin.value(),
                                   "wheel_weight": self.wheel_w_spin.value(),
                                   "path_mode": self.path_mode_combo.currentData(),
                                   "r_min_tractor": self.r_min_spin.value(),
                                   "slope_warn": self.sl_warn_spin.value(), "slope_crit": self.sl_crit_spin.value(),
                                   "vert_r_min": self.vert_r_spin.value()}}
        if do_terrain:
            feedback.pushInfo("\n" + "=" * 60)
            feedback.pushInfo("ANALIZA TERENU (NMT/DEM)")
            feedback.pushInfo("=" * 60)
            snapshots_from_layer = self._extract_snapshots_from_layer(
                swept["snapshots"], feedback)
            terrain_params = {
                "blade_height_m": self.blade_h_spin.value(),
                "slope_warn": self.sl_warn_spin.value(),
                "slope_crit": self.sl_crit_spin.value(),
                "vert_r_min": self.vert_r_spin.value(),
                "emb_diff_min": self.emb_dz_spin.value(),
                "emb_sample_m": self.emb_step_spin.value(),
            }
            self.run_btn.setEnabled(False)
            self.close_btn.setEnabled(False)
            terrain = None
            try:
                from .vestas_terrain import run_terrain_analysis   # analiza NMT – wersja z obliczeniami lokalnymi
                terrain = run_terrain_analysis(
                    dem_lyr, swept, snapshots_from_layer,
                    model_name, terrain_params, feedback)
            except Exception:
                feedback.reportError("Nieoczekiwany wyjatek podczas analizy DEM:")
                feedback.reportError(traceback.format_exc())
            finally:
                self.run_btn.setEnabled(True)
                self.close_btn.setEnabled(True)
            if terrain:
                # Tylko slope profile + cross slope flags
                for key in ("slope_profile", "cross_slope"):
                    lyr = terrain.get(key)
                    if lyr is not None:
                        project.addMapLayer(lyr)
                self._last["terrain"] = terrain
            self._last["dem_checks"] = self._swivel_dem_checks(swept, dem_lyr, feedback, project)
        info = ("Symulacja zakonczona.\n\n"
                "Dodane warstwy: 04 Blade Envelope | 07 Hardstand | 08 Snapshots.\n\n"
                "Animacja: View -> Panels -> Temporal Controller,\n"
                "zaznacz '08 Snapshots', krok 1 s, kliknij Play.")
        if do_terrain:
            info += ("\n\nAnaliza terenu: 10 Slope Profile | 11 Cross Slope Flags.")
        info += "\n\nSzablon Vestas 0096-6291: warstwy 14–16. Raport: przycisk „Raport PDF”."
        self.report_btn.setEnabled(True)
        QMessageBox.information(self, "Gotowe", info)

    def _swivel_dem_checks(self, swept, dem_lyr, feedback, project):
        """Teren w strefach przelotu Vestas (prześwit ≤ 15 cm) względem nawierzchni na osi trasy."""
        if dem_lyr is None:
            return None
        from qgis.core import QgsFeature, QgsField, QgsPointXY, QgsVectorLayer
        from qgis.PyQt.QtCore import QVariant
        from .vestas_template import swivel_dem_check
        from .vestas_terrain import _open_dem, _sample_z
        try:
            ds = _open_dem(dem_lyr)
        except Exception as e:  # noqa: BLE001
            feedback.pushWarning(f"Strefy przelotu – NMT: {e}")
            return None
        out = {}
        crs = swept["wheel"].crs()
        lyr = QgsVectorLayer(f"Point?crs={crs.authid()}", "17 Vestas – teren > 15 cm w strefie przelotu", "memory")
        lyr.dataProvider().addAttributes([QgsField("excess_m", QVariant.Double), QgsField("route_id", QVariant.Int)])
        lyr.updateFields()
        feats = []
        for r in swept.get("results") or []:
            vs = r.get("vestas")
            if not vs:
                continue
            rid = r["route_idx"] + 1
            res = swivel_dem_check(vs["tpl"], r["route_geom"], ds, _sample_z)
            out[rid] = res
            for x, y, ex in res["points_over"]:
                f = QgsFeature(lyr.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                f.setAttributes([ex, rid])
                feats.append(f)
            msg = (f"Trasa #{rid}: strefy przelotu – {res['n_over']} z {res['n']} punktów NMT > 15 cm nad "
                   f"nawierzchnią (maks. +{res['max_excess_m']:.2f} m)")
            (feedback.pushWarning if res["n_over"] else feedback.pushInfo)(msg)
        if feats:
            lyr.dataProvider().addFeatures(feats)
            lyr.updateExtents()
            project.addMapLayer(lyr)
        return out

    def _on_report_clicked(self):
        import json
        import os
        from WindLab.core import remote_exec as rx
        import sys
        from WindLab.core.paths import find_external_python, report_filename
        from .vestas_report import collect_report_data
        last = getattr(self, "_last", None)
        if not last:
            QMessageBox.warning(self, "Raport", "Najpierw uruchom symulację.")
            return
        out_dir = os.environ.get("WINDLAB_OUTPUT_DIR") or os.path.expanduser("~")
        os.makedirs(out_dir, exist_ok=True)
        feedback = DialogFeedback(self.progress, self.log)
        feedback.pushInfo("Raport PDF: zbieranie danych i mapy łuków…")
        try:
            data = collect_report_data(last["swept"], last["terrain"], last["settings"], last["road"],
                                       out_dir, last.get("dem_checks"))
            data_path = os.path.join(out_dir, "transport_report_data.json")
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            pdf = os.path.join(out_dir, report_filename("TRANSPORT", data.get("project") or "projekt"))
            worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker_transport_report.py")
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
                  "timeout": 300, "env": env}
            if sys.platform == "win32":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW (tylko tryb lokalny)
            r = rx.run([find_external_python(), "-X", "utf8", worker, data_path, pdf], **kw)
            last_line = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "{}"
            res = json.loads(last_line)
            if res.get("status") != "ok":
                raise RuntimeError(res.get("message") or (r.stderr or "")[-600:])
            feedback.pushInfo(f"✓ Raport: {pdf}")
            with contextlib.suppress(Exception):
                open_path(pdf)
        except Exception as e:  # noqa: BLE001
            feedback.reportError(f"Raport PDF: {e}")
            QMessageBox.critical(self, "Raport PDF", str(e)[:800])

    def _extract_snapshots_from_layer(self, snaps_layer, feedback):
        from collections import defaultdict
        by_id = defaultdict(dict)
        for feat in snaps_layer.getFeatures():
            sid = feat["snap_id"]
            comp = feat["component"]
            by_id[sid]["snap_id"] = sid
            by_id[sid]["dist_m"] = feat["dist_m"]
            by_id[sid]["pct"] = feat["pct"]
            by_id[sid][comp] = QgsGeometry(feat.geometry())
            with contextlib.suppress(Exception):
                a_deg = feat["alpha_deg"]
                if a_deg is not None and "alpha" not in by_id[sid]:
                    by_id[sid]["alpha"] = math.radians(float(a_deg))
            with contextlib.suppress(Exception):
                t_deg = feat["theta_deg"]
                if t_deg is not None and "theta" not in by_id[sid]:
                    by_id[sid]["theta"] = math.radians(float(t_deg))
            with contextlib.suppress(Exception):
                g_deg = feat["gamma_deg"]
                if g_deg is not None and "gamma" not in by_id[sid]:
                    by_id[sid]["gamma"] = math.radians(float(g_deg))
        result = sorted(by_id.values(), key=lambda s: s["snap_id"])
        n_alpha = sum(1 for s in result if "alpha" in s)
        feedback.pushInfo(f"  Wczytano {len(result)} snapshotow ({n_alpha} z alpha).")
        return result
