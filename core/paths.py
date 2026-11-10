# -*- coding: utf-8 -*-
"""
WindLab – wspólne helpery ścieżek, daty w nazwach plików, zewnętrzny Python.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def dated_name(prefix: str, ext: str, when: Optional[datetime] = None) -> str:
    """PREFIX_YYYY-MM-DD.ext"""
    when = when or datetime.now()
    ext = ext.lstrip(".")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix.strip()) or "out"
    return f"{safe}_{when.strftime('%Y-%m-%d')}.{ext}"


def dated_stamp(when: Optional[datetime] = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m-%d")


def report_filename(prefix: str, project: str, when: Optional[datetime] = None) -> str:
    """Nazwa raportu PDF: MODUŁ_Projekt_RRRR-MM-DD.pdf (np. AEP_Farma_X_2026-10-28.pdf)."""
    safe = re.sub(r"[^\w\-]+", "_", str(project or "").strip()).strip("_") or "projekt"
    return f"{prefix}_{safe}_{dated_stamp(when)}.pdf"


def find_external_python() -> str:
    """Zewnętrzny Python z bibliotekami obliczeniowymi (nie QGIS) – wersja z obliczeniami lokalnymi."""
    env = (os.environ.get("AEP_PYTHON") or os.environ.get("WINDLAB_PYTHON") or "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    candidates = [
        r"C:\Python3\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
        r"C:\Python39\python.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python311\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python310\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python39\python.exe"),
        sys.executable,
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return sys.executable


def windlab_root() -> Path:
    """Katalog instalacji WindLab (zawiera core/, gui/, modules/, WTG/)."""
    env = os.environ.get("WINDLAB_ROOT", "").strip()
    if env:
        p = Path(env)
        if (p / "core").is_dir() and (p / "gui").is_dir():
            return p.resolve()
        if (p / "WindLab" / "core").is_dir():
            return (p / "WindLab").resolve()
    # core/paths.py → core → WindLab
    here = Path(__file__).resolve().parent.parent
    if (here / "core").is_dir() and (here / "gui").is_dir():
        return here
    return here


def models_wtg_dir() -> Path:
    """Katalog modeli turbin w instalacji (nie w projekcie użytkownika)."""
    env = os.environ.get("WINDLAB_WTG_DIR", "").strip()
    if env and Path(env).is_dir():
        return Path(env).resolve()
    root = windlab_root()
    for cand in (root / "WTG", root.parent / "WTG"):
        if cand.is_dir():
            return cand.resolve()
    return (root / "WTG").resolve()


def apply_project_env(project) -> None:
    """Ustaw zmienne środowiskowe z otwartego projektu (dla workerów)."""
    os.environ["WINDLAB_PROJECT_NAME"] = project.name
    os.environ["WINDLAB_PROJECT_JSON"] = str(project.project_file)
    os.environ["WINDLAB_TURBINE_SHP"] = str(project.turbine_shp)
    os.environ["WINDLAB_TURBINE_ID_COL"] = project.turbine_id_column
    os.environ["WINDLAB_OUTPUT_DIR"] = str(project.output_dir)
    os.environ["WINDLAB_NC_DIR"] = str(project.data_nc_dir)
    os.environ["WINDLAB_NMT_DIR"] = str(project.data_nmt_dir)
    os.environ.setdefault("AEP_PYTHON", find_external_python())
    os.environ.setdefault("WINDLAB_WTG_DIR", str(models_wtg_dir()))


def project_from_env():
    """Odczytaj projekt z WINDLAB_PROJECT_JSON (opcjonalnie)."""
    path = os.environ.get("WINDLAB_PROJECT_JSON", "").strip()
    if not path:
        return None
    from WindLab.core.project import open_project
    return open_project(Path(path))
