# -*- coding: utf-8 -*-
"""
Uruchamianie okien modułów z kontekstem projektu WindLab.
Silniki analiz bez zmian – tylko ścieżki i nazewnictwo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from qgis.PyQt.QtWidgets import QMessageBox, QDialog, QWidget
import contextlib


def _qgis_iface():
    try:
        from qgis.utils import iface
        return iface
    except Exception:
        return None


def _parent_window(parent):
    if parent is not None:
        return parent
    iface = _qgis_iface()
    if iface is not None:
        with contextlib.suppress(Exception):
            return iface.mainWindow()
    return None


def _ensure_iface_global():
    """Wstrzyknij iface do builtins, gdy skrypt oczkuje globalnego iface."""
    with contextlib.suppress(Exception):
        import builtins
        if not hasattr(builtins, "iface") or builtins.iface is None:
            iface = _qgis_iface()
            if iface is not None:
                builtins.iface = iface


def _ensure_path(mod_root: Path) -> None:
    s = str(mod_root)
    if s not in sys.path:
        sys.path.insert(0, s)
    parent = str(mod_root.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    wl = mod_root
    for _ in range(6):
        if (wl / "core").is_dir() and (wl / "gui").is_dir():
            parent_of_wl = str(wl.parent)
            if parent_of_wl not in sys.path:
                sys.path.insert(0, parent_of_wl)
            break
        if wl.parent == wl:
            break
        wl = wl.parent


def _apply_env(project) -> None:
    from WindLab.core.paths import apply_project_env, find_external_python, models_wtg_dir
    apply_project_env(project)
    os.environ["AEP_PYTHON"] = find_external_python()
    os.environ["WINDLAB_WTG_DIR"] = str(models_wtg_dir())
    os.environ["AEP_DATA_ROOT"] = str(project.project_dir)



def _set_project_name(dlg, project) -> None:
    """Wymuś nazwę projektu we wszystkich polach dialogu i atrybutach."""
    name = project.name or ""
    for attr in ("project_edit", "ed_project", "project_name_edit", "edit_project"):
        w = getattr(dlg, attr, None)
        if w is not None and hasattr(w, "setText"):
            w.setText(name)
    dlg._windlab_project_name = name
    # zapamiętaj też na module (gdy dialog czyta z globals)
    with contextlib.suppress(Exception):
        mod = type(dlg).__module__
        import sys
        if mod in sys.modules:
            setattr(sys.modules[mod], "WINDLAB_PROJECT_NAME", name)


def _modules_root() -> Path:
    return Path(__file__).resolve().parent.parent / "modules"


def _default_nmt_path(project) -> str:
    """Ścieżka do scalonego NMT: Data/NMT/{projekt}_NMT.asc lub pierwszy .asc/.tif."""
    nmt_dir = Path(project.data_nmt_dir)
    if not nmt_dir.is_dir():
        return str(nmt_dir)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.name.strip()) or "projekt"
    preferred = nmt_dir / f"{safe}_NMT.asc"
    if preferred.is_file():
        return str(preferred)
    # fallback: merged_nmt.asc, potem dowolny asc/tif
    for name in ("merged_nmt.asc",):
        cand = nmt_dir / name
        if cand.is_file():
            return str(cand)
    for ext in ("*.asc", "*.tif", "*.tiff"):
        files = sorted(nmt_dir.glob(ext))
        if files:
            return str(files[0])
    return str(nmt_dir)


def _prefill_turbine_layer(dlg, project) -> None:
    """Ustaw warstwę/SHP turbin z projektu w typowych polach dialogu."""
    shp = project.turbine_shp
    if not shp or not Path(shp).is_file():
        return
    shp_s = str(shp)
    id_col = project.turbine_id_column

    # upewnij się, że warstwa jest w projekcie QGIS
    try:
        from qgis.core import QgsProject, QgsVectorLayer
        found = None
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            src = (lyr.source() or "").split("|")[0]
            with contextlib.suppress(Exception):
                if Path(src).resolve() == Path(shp).resolve():
                    found = lyr
                    break
        if found is None:
            found = QgsVectorLayer(shp_s, "turbines", "ogr")
            if found.isValid():
                QgsProject.instance().addMapLayer(found)
    except Exception as e:
        print(f"[WindLab] ensure turbine layer: {e}")

    for attr in ("edit_shp", "shp_edit", "turbine_edit", "layer_path_edit"):
        if hasattr(dlg, attr):
            with contextlib.suppress(Exception):
                getattr(dlg, attr).setText(shp_s)

    # QGIS layer combo – wybierz warstwę o tym samym source lub turbines
    try:
        from qgis.core import QgsProject, QgsVectorLayer
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            src = (lyr.source() or "").split("|")[0]
            match = False
            with contextlib.suppress(Exception):
                if Path(src).resolve() == Path(shp).resolve():
                    match = True
            if not match and lyr.name().lower() in ("turbines", Path(shp).stem.lower()):
                match = True
            if match:
                for combo_name in ("combo_layer", "layer_combo", "turbine_combo", "cbo_layer"):
                    if hasattr(dlg, combo_name):
                        combo = getattr(dlg, combo_name)
                        for i in range(combo.count()):
                            data = combo.itemData(i)
                            if data == lyr.id() or combo.itemText(i) == lyr.name():
                                combo.setCurrentIndex(i)
                                break
                break
    except Exception as e:
        print(f"[WindLab] prefill layer: {e}")

    # kolumna ID
    for attr in ("id_combo", "field_combo", "label_combo", "userlabel_combo"):
        if hasattr(dlg, attr):
            with contextlib.suppress(Exception):
                combo = getattr(dlg, attr)
                idx = combo.findText(id_col)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

    # metody load
    if hasattr(dlg, "_load_shp"):
        try:
            if hasattr(dlg, "_label_field"):
                dlg._label_field = id_col
                dlg._label_field_is_fid = False
            dlg._load_shp(shp_s)
        except Exception as e:
            print(f"[WindLab] _load_shp: {e}")



def _load_dem_layer(dem_path: str, name: str = "NMT"):
    """Dodaj raster NMT do projektu QGIS (jeśli jeszcze nie ma) i zwróć warstwę."""
    if not dem_path or not Path(dem_path).is_file():
        return None
    try:
        from qgis.core import QgsProject, QgsRasterLayer
        dem_path = str(Path(dem_path).resolve())
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer):
                src = (lyr.source() or "").split("|")[0]
                with contextlib.suppress(Exception):
                    if Path(src).resolve() == Path(dem_path):
                        return lyr
        layer = QgsRasterLayer(dem_path, name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer
    except Exception as e:
        print(f"[WindLab] load DEM: {e}")
    return None


def _select_dem_combo(dlg, dem_path: str, project_name: str = "NMT") -> None:
    """Załaduj DEM i wybierz w combo (cmb_dem / dem_combo)."""
    layer = _load_dem_layer(dem_path, name=f"{project_name}_NMT")
    if layer is None:
        return
    for attr in ("cmb_dem", "dem_combo", "raster_combo"):
        if hasattr(dlg, attr):
            try:
                combo = getattr(dlg, attr)
                # QgsMapLayerComboBox
                if hasattr(combo, "setLayer"):
                    combo.setLayer(layer)
                else:
                    for i in range(combo.count()):
                        if combo.itemData(i) == layer or combo.itemText(i) == layer.name():
                            combo.setCurrentIndex(i)
                            break
            except Exception as e:
                print(f"[WindLab] select dem combo: {e}")


def _prefill_dem(dlg, project) -> None:
    """Ustaw ścieżkę DEM/NMT (scalone {projekt}_NMT.asc) i warstwę w combo."""
    dem = _default_nmt_path(project)
    for attr in (
        "dem_edit", "nmt_edit", "dem_path_edit", "edit_dem", "edit_nmt",
        "dem_folder_edit", "raster_edit", "dsm_edit",
    ):
        if hasattr(dlg, attr):
            with contextlib.suppress(Exception):
                getattr(dlg, attr).setText(dem)
    if Path(dem).is_file():
        _select_dem_combo(dlg, dem, project.name)



def _find_dialog_class(mod, preferred_names):
    for name in preferred_names:
        if hasattr(mod, name):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, QDialog):
                return obj
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        if isinstance(obj, type) and issubclass(obj, QDialog) and name not in ("QDialog", "QWidget"):
            return obj
    return None


def launch_aep(parent, project) -> None:
    _ensure_iface_global()
    _apply_env(project)
    root = _modules_root() / "aep"
    _ensure_path(root)
    import WindLab.modules.aep.gui.aep_dialog as dlg_mod
    dlg_mod.DEFAULT_NC_DIR = str(project.data_nc_dir)
    dlg_mod.DEFAULT_OUTPUT_DIR = str(project.output_dir)
    dlg_mod.DEFAULT_WTG_DIR = os.environ.get("WINDLAB_WTG_DIR", getattr(dlg_mod, "DEFAULT_WTG_DIR", ""))
    dlg_mod._DATA_ROOT = str(project.project_dir)
    dlg_mod.WORKER_AEP = str(root / "workers" / "worker_aep.py")
    dlg_mod.REPORT_GEN = str(root / "workers" / "worker_aep_report.py")
    cls = _find_dialog_class(dlg_mod, ("AepDialog", "AEPDialog"))
    if cls is None:
        raise RuntimeError("Brak klasy dialogu AEP (AepDialog).")
    dlg = cls(_parent_window(parent))
    _prefill_turbine_layer(dlg, project)
    if hasattr(dlg, "nc_edit"):
        dlg.nc_edit.setText(str(project.data_nc_dir))
    if hasattr(dlg, "out_edit"):
        dlg.out_edit.setText(str(project.output_dir))
    _set_project_name(dlg, project)
    dlg.exec()


def launch_wake(parent, project) -> None:
    _ensure_iface_global()
    _apply_env(project)
    root = _modules_root() / "wake"
    _ensure_path(root)
    import WindLab.modules.wake.gui.wake_map_dialog as dlg_mod
    dlg_mod.DEFAULT_NC_DIR = str(project.data_nc_dir)
    dlg_mod.DEFAULT_OUTPUT_DIR = str(project.output_dir)
    dlg_mod.DEFAULT_WTG_DIR = os.environ.get("WINDLAB_WTG_DIR", getattr(dlg_mod, "DEFAULT_WTG_DIR", ""))
    dlg_mod._DATA_ROOT = str(project.project_dir)
    dlg_mod.WORKER = str(root / "workers" / "worker_wake_map.py")
    cls = _find_dialog_class(dlg_mod, ("WakeMapDialog", "WakeDialog"))
    if cls is None:
        raise RuntimeError("Brak klasy dialogu Wake.")
    dlg = cls(_parent_window(parent))
    _prefill_turbine_layer(dlg, project)
    if hasattr(dlg, "nc_edit"):
        dlg.nc_edit.setText(str(project.data_nc_dir))
    if hasattr(dlg, "out_edit"):
        dlg.out_edit.setText(str(project.output_dir))
    _set_project_name(dlg, project)
    dlg.exec()


def launch_shadow(parent, project) -> None:
    _ensure_iface_global()
    _apply_env(project)
    # scalony NMT jako domyślny plik DEM
    nmt_file = _default_nmt_path(project)
    if nmt_file and Path(nmt_file).is_file():
        os.environ["WINDLAB_NMT_FILE"] = nmt_file
    else:
        os.environ.pop("WINDLAB_NMT_FILE", None)
    root = _modules_root() / "shadow"
    _ensure_path(root)
    eng = str(root)
    if eng not in sys.path:
        sys.path.insert(0, eng)

    import importlib.util
    gui_path = root / "worker_shadow_GUI.py"
    spec = importlib.util.spec_from_file_location("windlab_shadow_gui", gui_path)
    mod = importlib.util.module_from_spec(spec)
    # pre-inject iface into module namespace before exec
    iface = _qgis_iface()
    mod.iface = iface
    sys.modules["windlab_shadow_gui"] = mod
    spec.loader.exec_module(mod)

    mod.WORKERS_DIR = str(root)
    mod.WORKER_SHADOW = str(root / "worker_shadow.py")
    mod.REPORT_GEN = str(root / "worker_shadow_report.py")
    mod.WTG_DIR = os.environ.get("WINDLAB_WTG_DIR", getattr(mod, "WTG_DIR", ""))
    mod.NC_DIR = str(project.data_nc_dir)
    mod.OUTPUT_DIR = str(project.output_dir)
    mod.NMT_DIR = str(project.data_nmt_dir)
    mod.PYTHON_EXE = os.environ.get("AEP_PYTHON", getattr(mod, "PYTHON_EXE", "python"))
    if iface is not None:
        mod.iface = iface

    cls = _find_dialog_class(mod, ("ShadowDialog", "ShadowFlickerDialog"))
    if cls is None:
        raise RuntimeError("Brak klasy dialogu Shadow.")
    dlg = cls(_parent_window(parent))
    _prefill_turbine_layer(dlg, project)
    _prefill_dem(dlg, project)
    if hasattr(dlg, "nc_edit"):
        dlg.nc_edit.setText(str(project.data_nc_dir))
    if hasattr(dlg, "out_edit"):
        dlg.out_edit.setText(str(project.output_dir))
    _set_project_name(dlg, project)
    dlg.exec()


def launch_noise(parent, project) -> None:
    _ensure_iface_global()
    _apply_env(project)
    # scalony NMT jako domyślny plik DEM
    nmt_file = _default_nmt_path(project)
    if nmt_file and Path(nmt_file).is_file():
        os.environ["WINDLAB_NMT_FILE"] = nmt_file
    else:
        os.environ.pop("WINDLAB_NMT_FILE", None)
    root = _modules_root() / "noise"
    _ensure_path(root)
    # ensure package import path
    wl_parent = str(root.parent.parent.parent)  # .../WindLab parent
    if str(root.parent.parent) not in sys.path:
        sys.path.insert(0, str(root.parent.parent.parent))

    import WindLab.modules.noise.gui.dialog as dlg_mod
    for attr, val in (
        ("DEFAULT_OUTPUT", str(project.output_dir)),
        ("OUTPUT_DIR", str(project.output_dir)),
        ("DEFAULT_NC_DIR", str(project.data_nc_dir)),
        ("WTG_DIR", os.environ.get("WINDLAB_WTG_DIR", "")),
    ):
        if hasattr(dlg_mod, attr) or attr in ("OUTPUT_DIR", "DEFAULT_OUTPUT"):
            setattr(dlg_mod, attr, val)

    cls = _find_dialog_class(dlg_mod, ("NoiseDialog", "Dialog"))
    if cls is None:
        raise RuntimeError("Brak klasy dialogu hałasu.")
    dlg = cls(_parent_window(parent))
    _prefill_turbine_layer(dlg, project)
    _prefill_dem(dlg, project)
    for edit_name, path in (
        ("out_edit", project.output_dir),
        ("ed_out", project.output_dir),
        ("output_edit", project.output_dir),
        ("nmt_edit", project.data_nmt_dir),
        ("dem_edit", project.data_nmt_dir),
    ):
        if hasattr(dlg, edit_name):
            getattr(dlg, edit_name).setText(str(path))
    _set_project_name(dlg, project)
    dlg.exec()


def launch_transport(parent, project) -> None:
    _ensure_iface_global()
    _apply_env(project)
    # scalony NMT jako domyślny plik DEM
    nmt_file = _default_nmt_path(project)
    if nmt_file and Path(nmt_file).is_file():
        os.environ["WINDLAB_NMT_FILE"] = nmt_file
    else:
        os.environ.pop("WINDLAB_NMT_FILE", None)
    root = _modules_root() / "transport"
    _ensure_path(root)
    import WindLab.modules.transport.vestas_dialog as dlg_mod
    cls = _find_dialog_class(dlg_mod, ("VestasDialog", "SweptPathDialog", "Dialog"))
    if cls is None:
        raise RuntimeError("Brak dialogu VestasSweptPath.")
    dlg = cls(_parent_window(parent))
    _prefill_turbine_layer(dlg, project)
    _prefill_dem(dlg, project)
    _set_project_name(dlg, project)
    dlg.exec()


def launch_cables(parent, project) -> None:
    """Optymalizacja tras kablowych – Cable/gui/cable_dialog.py."""
    _ensure_iface_global()
    _apply_env(project)
    nmt_file = _default_nmt_path(project)
    if nmt_file and Path(nmt_file).is_file():
        os.environ["WINDLAB_NMT_FILE"] = nmt_file
    else:
        os.environ.pop("WINDLAB_NMT_FILE", None)

    root = _modules_root() / "cables"
    gui_root = root / "Cable" / "gui"
    _ensure_path(root)
    _ensure_path(gui_root)
    # pakiet Cable może wymagać parent na sys.path
    cable_pkg = root / "Cable"
    if str(cable_pkg) not in sys.path:
        sys.path.insert(0, str(cable_pkg))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import importlib.util
    gui_path = gui_root / "cable_dialog.py"
    if not gui_path.is_file():
        raise RuntimeError(f"Brak cable_dialog.py w {gui_root}")

    # preferowany import pakietowy, fallback: load z pliku
    mod = None
    try:
        import WindLab.modules.cables.Cable.gui.cable_dialog as mod
    except Exception:
        try:
            import Cable.gui.cable_dialog as mod  # type: ignore
        except Exception:
            spec = importlib.util.spec_from_file_location(
                "windlab_cables_dialog", str(gui_path)
            )
            mod = importlib.util.module_from_spec(spec)
            iface = _qgis_iface()
            mod.iface = iface
            sys.modules["windlab_cables_dialog"] = mod
            if spec is None or spec.loader is None:
                raise ImportError("Nie można wczytać modułu okna")
            spec.loader.exec_module(mod)

    iface = _qgis_iface()
    if iface is not None and hasattr(mod, "iface"):
        mod.iface = iface

    # typowe ścieżki / zmienne z projektu
    for attr, val in (
        ("OUTPUT_DIR", str(project.output_dir)),
        ("DEFAULT_OUTPUT", str(project.output_dir)),
        ("NC_DIR", str(project.data_nc_dir)),
        ("NMT_DIR", str(project.data_nmt_dir)),
        ("WTG_DIR", os.environ.get("WINDLAB_WTG_DIR", "")),
        ("PYTHON_EXE", os.environ.get("AEP_PYTHON", sys.executable)),
        ("WORKERS_DIR", str(root)),
    ):
        if hasattr(mod, attr) or attr in ("OUTPUT_DIR", "DEFAULT_OUTPUT", "NC_DIR", "NMT_DIR"):
            setattr(mod, attr, val)

    cls = _find_dialog_class(mod, (
        "CableDialog", "CablesDialog", "CableRouteDialog",
        "CableCommonDialog", "Dialog",
    ))
    parent_w = _parent_window(parent)

    if cls is not None:
        try:
            dlg = cls(parent_w, project=project)
        except TypeError:
            try:
                dlg = cls(parent_w)
            except TypeError:
                dlg = cls()
        _prefill_turbine_layer(dlg, project)
        _prefill_dem(dlg, project)
        for edit_name, path in (
            ("out_edit", project.output_dir),
            ("output_edit", project.output_dir),
            ("ed_out", project.output_dir),
            ("nmt_edit", project.data_nmt_dir),
            ("dem_edit", project.data_nmt_dir),
            ("nc_edit", project.data_nc_dir),
        ):
            if hasattr(dlg, edit_name):
                with contextlib.suppress(Exception):
                    getattr(dlg, edit_name).setText(str(path))
        _set_project_name(dlg, project)
        if hasattr(dlg, "exec_"):
            dlg.exec()
        elif hasattr(dlg, "show"):
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        return

    for fn_name in ("main", "run", "launch", "show_dialog", "open_dialog", "start"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            try:
                fn(parent_w, project)
            except TypeError:
                try:
                    fn(parent_w)
                except TypeError:
                    fn()
            return

    raise RuntimeError(
        "Nie znaleziono klasy dialogu ani funkcji startowej w cable_dialog.py "
        "(oczekiwano CableDialog / main / run)."
    )


def _load_nc_module():
    root = _modules_root() / "wind_data"
    _ensure_path(root)
    _ensure_path(_modules_root() / "download")
    import importlib.util
    gui_path = root / "gui_dialog.py"
    spec = importlib.util.spec_from_file_location("windlab_nc_gui", gui_path)
    mod = importlib.util.module_from_spec(spec)
    iface = _qgis_iface()
    mod.iface = iface
    sys.modules["windlab_nc_gui"] = mod
    spec.loader.exec_module(mod)
    if iface is not None:
        mod.iface = iface
    return mod, root



def launch_wind_analysis(parent, project) -> None:
    """Analiza zasobów wiatrowych – NC_Analyzer_GUI (+ raport PDF)."""
    _ensure_iface_global()
    _apply_env(project)
    root = _modules_root() / "wind_data"
    _ensure_path(root)

    import importlib.util
    gui_path = root / "NC_Analyzer_GUI.py"
    if not gui_path.is_file():
        raise RuntimeError(f"Brak NC_Analyzer_GUI.py w {root}")

    spec = importlib.util.spec_from_file_location("windlab_nc_analyzer_gui", gui_path)
    mod = importlib.util.module_from_spec(spec)
    iface = _qgis_iface()
    mod.iface = iface
    sys.modules["windlab_nc_analyzer_gui"] = mod
    spec.loader.exec_module(mod)

    # ścieżki z projektu
    mod.PYTHON_EXE = os.environ.get("AEP_PYTHON", getattr(mod, "PYTHON_EXE", "python"))
    mod.WORKERS_DIR = str(root)
    mod.ANALYZER = str(root / "NC_Analizer.py")
    mod.REPORT_WORKER = str(root / "worker_nc_report.py")
    mod.DEFAULT_DATA_DIR = str(project.data_nc_dir)

    cls = _find_dialog_class(mod, ("NCAnalyzerDialog", "NCDataDialog"))
    if cls is None:
        raise RuntimeError("Brak klasy NCAnalyzerDialog.")
    dlg = cls(_parent_window(parent))
    _set_project_name(dlg, project)
    if hasattr(dlg, "edit_datadir"):
        dlg.edit_datadir.setText(str(project.data_nc_dir))
    # wykresy / raporty → Output projektu
    plots = project.output_dir / "wykresy_nc"
    if hasattr(dlg, "edit_plotsdir"):
        dlg.edit_plotsdir.setText(str(plots))
    # podpowiedź nazwy projektu w raporcie – worker bierze z params
    dlg._windlab_project_name = project.name
    dlg._windlab_output_dir = str(project.output_dir)
    _set_project_name(dlg, project)
    dlg.exec()


def launch_download(parent, project) -> None:
    """Pobieranie NC + NMT równolegle (Combined_Downloader_GUI)."""
    _ensure_iface_global()
    _apply_env(project)
    root = _modules_root() / "download"
    _ensure_path(root)

    import importlib.util
    gui_path = root / "Combined_Downloader_GUI.py"
    if not gui_path.is_file():
        raise RuntimeError(f"Brak Combined_Downloader_GUI.py w {root}")

    # NMT_Downloader_GUI musi być importowalny z tego katalogu
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    spec = importlib.util.spec_from_file_location("windlab_combined_dl", gui_path)
    mod = importlib.util.module_from_spec(spec)
    iface = _qgis_iface()
    mod.iface = iface
    sys.modules["windlab_combined_dl"] = mod
    # przed exec – nadpisz SCRIPT_DIR / PYTHON
    # (moduł ustawi SCRIPT_DIR z __file__ przy imporcie)
    spec.loader.exec_module(mod)

    mod.SCRIPT_DIR = str(root)
    mod.PYTHON_EXE = os.environ.get("AEP_PYTHON", getattr(mod, "PYTHON_EXE", "python"))
    mod.NC_DOWNLOADER = str(root / "NC_Downloader.py")
    mod.NC_DEFAULT_OUTPUT = str(project.data_nc_dir)
    mod.NMT_DEFAULT_OUTPUT = str(project.data_nmt_dir)
    sys.path.insert(0, str(root))

    cls = _find_dialog_class(mod, ("CombinedDownloaderDialog",))
    if cls is None:
        raise RuntimeError("Brak klasy CombinedDownloaderDialog.")
    dlg = cls(_parent_window(parent))
    _set_project_name(dlg, project)

    # domyślne katalogi z projektu
    if hasattr(dlg, "edit_nc_outdir"):
        dlg.edit_nc_outdir.setText(str(project.data_nc_dir))
    if hasattr(dlg, "edit_nmt_outdir"):
        dlg.edit_nmt_outdir.setText(str(project.data_nmt_dir))

    # domyślnie warstwa / SHP turbin z projektu
    shp = project.turbine_shp
    if shp and Path(shp).is_file():
        if hasattr(dlg, "edit_shp"):
            dlg.edit_shp.setText(str(shp))
        # wczytaj punkty z SHP projektu (z kolumną ID z projektu)
        try:
            if hasattr(dlg, "_load_shp"):
                # ustaw preferowaną kolumnę etykiet
                dlg._label_field = project.turbine_id_column
                dlg._label_field_is_fid = False
                dlg._load_shp(str(shp))
            elif hasattr(dlg, "edit_shp"):
                pass
        except Exception as e:
            print(f"[WindLab] prefill turbines: {e}")
            # fallback: spróbuj wybrać warstwę o nazwie z pliku w QGIS
            try:
                from qgis.core import QgsProject, QgsVectorLayer
                for lyr in QgsProject.instance().mapLayers().values():
                    if not isinstance(lyr, QgsVectorLayer):
                        continue
                    src = (lyr.source() or "").split("|")[0]
                    if Path(src).resolve() == Path(shp).resolve() or lyr.name() in ("turbines", Path(shp).stem):
                        if hasattr(dlg, "combo_layer"):
                            for i in range(dlg.combo_layer.count()):
                                if dlg.combo_layer.itemData(i) == lyr.id():
                                    dlg.combo_layer.setCurrentIndex(i)
                                    break
                        break
            except Exception as e2:
                print(f"[WindLab] prefill layer: {e2}")

    dlg.exec()



def launch_optimization(parent, project) -> None:
    """Optymalizacja strat AEP (cień + hałas)."""
    import importlib.util
    import os
    import sys

    _apply_env(project)
    root = _modules_root() / "optimization"
    # zapewnij import WindLab.*
    wl = windlab_root() if "windlab_root" in dir() else root.parent.parent
    with contextlib.suppress(Exception):
        from WindLab.core.paths import windlab_root as _wr
        wl = _wr()
    for p in (str(wl.parent), str(wl)):
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        import WindLab.modules.optimization.gui.optimization_dialog as dlg_mod
    except Exception:
        gui_path = root / "gui" / "optimization_dialog.py"
        spec = importlib.util.spec_from_file_location("windlab_opt_gui", str(gui_path))
        dlg_mod = importlib.util.module_from_spec(spec)
        sys.modules["windlab_opt_gui"] = dlg_mod
        if spec is None or spec.loader is None:
            raise ImportError("Nie można wczytać modułu okna")
        spec.loader.exec_module(dlg_mod)

    dlg_mod.PYTHON_EXE = (
        os.environ.get("AEP_PYTHON")
        or os.environ.get("WINDLAB_PYTHON")
        or sys.executable
    )
    dlg_mod.WORKER_OPT = str(root / "workers" / "worker_optimization.py")
    dlg_mod.WORKER_REPORT = str(root / "workers" / "worker_optimization_report.py")
    parent_w = _parent_window(parent)
    dlg = dlg_mod.OptimizationDialog(parent=parent_w, project=project)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


LAUNCHERS = {
    "aep": launch_aep,
    "wake": launch_wake,
    "shadow": launch_shadow,
    "noise": launch_noise,
    "transport": launch_transport,
    "cables": launch_cables,
    "wind_analysis": launch_wind_analysis,
    "download": launch_download,
    "optimization": launch_optimization,
}


def launch(name: str, parent, project) -> None:
    fn = LAUNCHERS.get(name)
    if not fn:
        QMessageBox.information(parent, name, f"Moduł „{name}” nie jest jeszcze podłączony.")
        return
    try:
        fn(parent, project)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[WindLab] launch {name} error:\n{tb}")
        QMessageBox.critical(
            parent,
            f"Błąd – {name}",
            f"Nie udało się uruchomić modułu:\n{e}",
        )
