# -*- coding: utf-8 -*-
"""
WindLab – zarządzanie projektem.
Plik projektu: <NazwaProjektu>.json w katalogu głównym projektu.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REQUIRED_SUBDIRS = [
    "WTG",
    "Output",
    "Data",
    "Data/NC",
    "Data/NMT",
]


def _safe_filename(name: str) -> str:
    """Nazwa pliku bezpieczna dla systemu plików."""
    s = re.sub(r'[<>:"/\\|?*]', "_", name.strip())
    s = re.sub(r"\s+", "_", s)
    return s or "projekt"


def project_filename(project_name: str) -> str:
    """Zwraca nazwę pliku projektu: NazwaProjektu.json"""
    return f"{_safe_filename(project_name)}.json"


class WindLabProject:
    """Reprezentacja otwartego projektu WindLab."""

    def __init__(self, data: Dict[str, Any], project_file: Path):
        self._data = data
        self.project_file = project_file
        self.project_dir = project_file.parent

    @property
    def name(self) -> str:
        return self._data.get("project_name", self.project_dir.name)

    @property
    def created(self) -> str:
        return self._data.get("created", "")

    @property
    def turbine_shp(self) -> Path:
        rel = self._data.get("turbine_shp", "WTG/turbines.shp")
        return (self.project_dir / rel).resolve()

    @property
    def turbine_id_column(self) -> str:
        return self._data.get("turbine_id_column", "userlabel")

    @property
    def output_dir(self) -> Path:
        rel = self._data.get("output_dir", "Output")
        p = Path(rel)
        if p.is_absolute():
            return p
        return (self.project_dir / rel).resolve()

    @property
    def data_nc_dir(self) -> Path:
        rel = self._data.get("data_nc_dir", "Data/NC")
        p = Path(rel)
        if p.is_absolute():
            return p
        return (self.project_dir / rel).resolve()

    @property
    def data_nmt_dir(self) -> Path:
        rel = self._data.get("data_nmt_dir", "Data/NMT")
        p = Path(rel)
        if p.is_absolute():
            return p
        return (self.project_dir / rel).resolve()

    @property
    def wtg_dir(self) -> Path:
        return (self.project_dir / "WTG").resolve()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def save(self) -> None:
        self.project_file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)
        self.save()

    def dated_name(self, prefix: str, ext: str = "") -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        if ext and not ext.startswith("."):
            ext = "." + ext
        return f"{prefix}_{today}{ext}"

    def output_path(self, *parts: str) -> Path:
        p = self.output_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def __repr__(self) -> str:
        return f"<WindLabProject {self.name!r} @ {self.project_dir}>"


def _copy_shp(src: Path, dst_dir: Path, new_name: str = "turbines") -> Path:
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"Brak pliku SHP turbin: {src}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    parent = src.parent
    copied = []
    for f in parent.glob(stem + ".*"):
        target = dst_dir / f"{new_name}{f.suffix}"
        shutil.copy2(f, target)
        copied.append(target.name)
    if not copied:
        raise FileNotFoundError(f"Nie skopiowano żadnych plików z: {src}")
    return dst_dir / f"{new_name}.shp"


def create_project(
    project_name: str,
    project_root: Path,
    turbine_shp: Path,
    id_column: str,
    output_dir: Optional[Path] = None,
    data_nc_dir: Optional[Path] = None,
    data_nmt_dir: Optional[Path] = None,
) -> WindLabProject:
    project_root = Path(project_root).resolve()
    fname = project_filename(project_name)
    project_file = project_root / fname

    if project_file.exists():
        raise FileExistsError(f"Projekt już istnieje: {project_file}")

    project_root.mkdir(parents=True, exist_ok=True)
    for sub in REQUIRED_SUBDIRS:
        (project_root / sub).mkdir(parents=True, exist_ok=True)

    wtg_dir = project_root / "WTG"
    _copy_shp(Path(turbine_shp), wtg_dir, new_name="turbines")

    out_rel = "Output"
    nc_rel = "Data/NC"
    nmt_rel = "Data/NMT"

    if output_dir is not None:
        output_dir = Path(output_dir).resolve()
        if output_dir != (project_root / "Output").resolve():
            out_rel = str(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

    if data_nc_dir is not None:
        data_nc_dir = Path(data_nc_dir).resolve()
        if data_nc_dir != (project_root / "Data" / "NC").resolve():
            nc_rel = str(data_nc_dir)
            data_nc_dir.mkdir(parents=True, exist_ok=True)

    if data_nmt_dir is not None:
        data_nmt_dir = Path(data_nmt_dir).resolve()
        if data_nmt_dir != (project_root / "Data" / "NMT").resolve():
            nmt_rel = str(data_nmt_dir)
            data_nmt_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "project_name": project_name,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "turbine_shp": "WTG/turbines.shp",
        "turbine_id_column": id_column,
        "output_dir": out_rel,
        "data_nc_dir": nc_rel,
        "data_nmt_dir": nmt_rel,
        "version": "1.0",
    }

    project_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return WindLabProject(data, project_file)


def open_project(project_path: Path) -> WindLabProject:
    project_path = Path(project_path).resolve()
    if project_path.is_dir():
        # szukaj *.json w katalogu (preferuj plik z nazwą katalogu)
        candidates = list(project_path.glob("*.json"))
        # pomiń nmt_download_report.json itp.
        candidates = [c for c in candidates if c.name != "nmt_download_report.json"]
        if not candidates:
            raise FileNotFoundError(
                f"Brak pliku projektu (*.json) w: {project_path}"
            )
        # preferuj plik o nazwie katalogu
        preferred = project_path / f"{project_path.name}.json"
        if preferred in candidates:
            project_file = preferred
        else:
            project_file = candidates[0]
    else:
        project_file = project_path

    if not project_file.is_file():
        raise FileNotFoundError(f"Nie znaleziono pliku projektu: {project_file}")

    data = json.loads(project_file.read_text(encoding="utf-8"))
    return WindLabProject(data, project_file)


def find_id_column(layer_or_path, preferred: str = "userlabel") -> Optional[str]:
    try:
        from qgis.core import QgsVectorLayer
    except ImportError:
        return preferred

    if isinstance(layer_or_path, QgsVectorLayer):
        layer = layer_or_path
    else:
        layer = QgsVectorLayer(str(layer_or_path), "tmp", "ogr")
        if not layer.isValid():
            return None

    fields = [f.name() for f in layer.fields()]
    lower_map = {f.lower(): f for f in fields}
    if preferred.lower() in lower_map:
        return lower_map[preferred.lower()]
    return None


def list_fields(layer_or_path) -> List[str]:
    try:
        from qgis.core import QgsVectorLayer
    except ImportError:
        return []

    if isinstance(layer_or_path, QgsVectorLayer):
        layer = layer_or_path
    else:
        layer = QgsVectorLayer(str(layer_or_path), "tmp", "ogr")
        if not layer.isValid():
            return []
    return [f.name() for f in layer.fields()]
