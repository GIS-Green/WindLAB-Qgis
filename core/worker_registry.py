# -*- coding: utf-8 -*-
"""
Rejestr workerów uruchamianych zdalnie (serwer obliczeń WindLab).

Wspólny dla wtyczki (core/remote_exec.py) i serwera (server/app.py):
  - biała lista workerów (identyfikatory; ścieżki tylko na serwerze),
  - które argumenty pozycyjne są ścieżkami WYJŚCIOWYMI (indeksy po nazwie skryptu),
  - liczenie turbin w zadaniu (limit wersji demo).

Bez importu qgis/PyQt.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

# identyfikator workera → opis ("file" = nazwa skryptu; położenie skryptów zna tylko serwer)
WORKERS: Dict[str, Dict[str, Any]] = {
    # obliczenia
    "aep": {"file": "worker_aep.py"},
    "wake_map": {"file": "worker_wake_map.py"},
    "shadow": {"file": "worker_shadow.py"},
    "noise": {"file": "worker_noise.py"},
    "optimization": {"file": "worker_optimization.py"},
    "cable_mst": {"file": "worker_cable_mst.py"},
    # czyta wcześniejsze wyniki z katalogu wyjściowego po nazwie (out_inputs → dołączane do zadania)
    "cable_opt": {"file": "worker_cable_opt.py", "out_inputs": ["cable_graph.json", "cable_mst_results.json"]},
    # brak plots_dir → worker pisze do data_dir/wykresy (na serwerze: katalog wejściowy) – ustal jawnie
    "nc_analysis": {"file": "NC_Analizer.py", "default_out": {"plots_dir": ("data_dir", "wykresy")}},
    "nc_download": {"file": "NC_Downloader.py"},
    "transport": {"file": "worker_transport.py"},
    # raporty PDF
    "aep_report": {"file": "worker_aep_report.py", "out_args": [2, 3]},        # json nc_dir pdf out_dir
    "wake_report": {"file": "worker_wake_report.py", "out_args": [1, 2]},      # json pdf out_dir
    "shadow_report": {"file": "worker_shadow_report.py", "out_args": [1, 2]},  # json pdf out_dir
    "noise_report": {"file": "worker_noise_report.py", "out_args": [1, 2]},    # json pdf plots_dir
    "optimization_report": {"file": "worker_optimization_report.py", "out_args": [1, 2]},
    "cable_report": {"file": "worker_cable_report.py",
                     "out_inputs": ["cable_graph.json", "cable_mst_results.json", "cable_opt_results.json"]},
    "transport_report": {"file": "worker_transport_report.py", "out_args": [1]},  # data.json pdf
    "nc_report": {"file": "worker_nc_report.py"},
}
BY_FILE: Dict[str, str] = {v["file"]: k for k, v in WORKERS.items()}


def worker_id(script_path: str) -> Optional[str]:
    """Identyfikator workera z nazwy skryptu (None = nieznany)."""
    return BY_FILE.get(os.path.basename(str(script_path)))


# klucze JSON, których wartości są ścieżkami wyjściowymi (katalog lub plik)
OUT_KEYS = {
    "output_dir", "out_dir", "plots_dir", "output_pdf", "pdf_path",
    "output_path", "out_path", "report_path",
}

_TURBINE_LIST_KEYS = ("x", "x_2180", "labels", "names", "userlabel", "ids")


def _len_turbines(v: Any) -> int:
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return max((len(v[k]) for k in _TURBINE_LIST_KEYS if isinstance(v.get(k), list)), default=0)
    return 0


def _count_in_obj(obj: Any) -> int:
    """Największa liczba turbin znaleziona w strukturze (klucze turbines / sources / points)."""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("turbines", "wtg", "sources"):
                n = max(n, _len_turbines(v))
            elif k == "points" and isinstance(v, list):
                n = max(n, len(v))
            n = max(n, _count_in_obj(v))
    elif isinstance(obj, list):
        for v in obj[:50]:
            n = max(n, _count_in_obj(v))
    return n


def _count_shp(path: str) -> int:
    try:
        import shapefile  # pyshp
        with shapefile.Reader(path) as r:
            return len(r)
    except Exception:
        return 0


def _load_json(path: Optional[str]) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def count_turbines(worker: str, params: Any) -> int:
    """Liczba turbin w zadaniu (0 = nie dotyczy / nie da się ustalić)."""
    if not isinstance(params, dict):
        return 0
    n = _count_in_obj(params)
    if worker == "nc_download" and params.get("shape_file"):
        n = max(n, _count_shp(params["shape_file"]))
    if worker == "nc_analysis" and params.get("data_dir"):
        n = max(n, len(glob.glob(os.path.join(params["data_dir"], "*_meso.nc"))))
    if worker == "optimization":
        n = max(n, _count_in_obj(_load_json(params.get("aep_result_path"))))
    if worker == "cable_opt":
        n = max(n, _count_in_obj(_load_json(params.get("graph_path"))))
    return n


def params_from_argv(argv: List[str]) -> Any:
    """Parametry zadania z argumentów skryptu: --params-file plik | surowy JSON | plik .json."""
    for i, a in enumerate(argv):
        if a == "--params-file" and i + 1 < len(argv):
            return _load_json(argv[i + 1])
    if argv:
        a = argv[0]
        if a.lstrip().startswith("{"):
            try:
                return json.loads(a)
            except ValueError:
                return None
        if a.lower().endswith(".json") and os.path.isfile(a):
            return _load_json(a)
    return None
