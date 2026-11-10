# -*- coding: utf-8 -*-
"""
WindLab – wspólne helpery ścieżek, daty w nazwach plików, zewnętrzny Python.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def dated_name(prefix: str, ext: str, when: Optional[datetime] = None) -> str:
    """PREFIX_RRRR-MM-DD_GGMM.ext"""
    when = when or datetime.now()
    ext = ext.lstrip(".")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix.strip()) or "out"
    return f"{safe}_{when.strftime(STAMP_FMT)}.{ext}"


def dated_stamp(when: Optional[datetime] = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m-%d")


# ── Znacznik czasu w nazwach wyników ─────────────────────────────────────────
# Każdy plik wynikowy WindLab ma w nazwie RRRR-MM-DD_GGMM (np. aep_results_2026-11-10_1432.json),
# więc kolejne przebiegi nie nadpisują poprzednich ani plików otwartych w QGIS.
STAMP_FMT = "%Y-%m-%d_%H%M"
_STAMP_RE = r"\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2})?"


def run_stamp(out_dir: Optional[str] = None, when: Optional[datetime] = None) -> str:
    """Znacznik jednego przebiegu: RRRR-MM-DD_GGMM. Gdy w katalogu są już pliki z tym
    znacznikiem (drugi przebieg w tej samej minucie) – z sekundami (GGMMSS)."""
    when = when or datetime.now()
    s = when.strftime(STAMP_FMT)
    with contextlib.suppress(OSError):
        if out_dir and os.path.isdir(out_dir) and any(s in fn for fn in os.listdir(out_dir)):
            s = when.strftime("%Y-%m-%d_%H%M%S")
    return s


def stamped(out_dir: str, base: str, ext: str, stamp: Optional[str] = None) -> str:
    """Ścieżka wyniku: <out_dir>/<base>_<znacznik>.<ext>."""
    return os.path.join(out_dir, f"{base}_{stamp or run_stamp(out_dir)}.{ext.lstrip('.')}")


def latest(out_dir: str, base: str, ext: str) -> str:
    """Najnowszy plik wyniku <base>_<znacznik>.<ext> (albo starszy <base>.<ext> bez znacznika).
    Zwraca "" gdy brak."""
    if not out_dir or not os.path.isdir(out_dir):
        return ""
    ext = ext.lstrip(".")
    rx = re.compile(r"^" + re.escape(base) + r"(?:_" + _STAMP_RE + r"|_\d{4}-\d{2}-\d{2})?\." + re.escape(ext) + r"$",
                    re.I)
    found = [os.path.join(out_dir, fn) for fn in os.listdir(out_dir) if rx.match(fn)]
    return max(found, key=os.path.getmtime) if found else ""


def stamp_of(path: str) -> str:
    """Znacznik czasu z nazwy pliku wyniku ("" gdy brak)."""
    m = re.search(_STAMP_RE, os.path.basename(str(path or "")))
    return m.group(0) if m else ""


def report_filename(prefix: str, project: str, when: Optional[datetime] = None,
                    out_dir: Optional[str] = None) -> str:
    """Nazwa raportu PDF: MODUŁ_Projekt_RRRR-MM-DD_GGMM.pdf (np. AEP_Farma_X_2026-10-28_1432.pdf).
    Gdy podano out_dir i taki plik już tam jest (drugi raport w tej samej minucie, pierwszy
    może być otwarty w przeglądarce PDF) – znacznik z sekundami."""
    safe = re.sub(r"[^\w\-]+", "_", str(project or "").strip()).strip("_") or "projekt"
    when = when or datetime.now()
    name = f"{prefix}_{safe}_{when.strftime(STAMP_FMT)}.pdf"
    if out_dir and os.path.exists(os.path.join(out_dir, name)):
        name = f"{prefix}_{safe}_{when.strftime('%Y-%m-%d_%H%M%S')}.pdf"
    return name


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
