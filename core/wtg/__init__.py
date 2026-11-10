# -*- coding: utf-8 -*-
"""
WindLab – wspólny czytnik modeli turbin (.wtg).

    from WindLab.core.wtg import load_turbine, list_turbine_files
    tm = load_turbine(path)                 # TurbineModel (z pamięcią podręczną)
    pc = tm.find_power("SO2 - 2024-10")     # krzywa mocy trybu
    nc = tm.find_noise(None)                # krzywa hałasu trybu domyślnego

Obsługiwane formaty:
  * WindPRO .wtg – klasyczne krzywe (dtPowerCurve / dtNoise) i Power Matrix,
  * WAsP .wtg (XML) – tylko krzywe mocy/Ct.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .model import (
    A_WEIGHTING, OCTAVE_BANDS, NoiseCurve, NoisePoint, PowerCurve, TurbineModel,
    db_sum, mode_code, variant_tag,
)

__all__ = [
    "load_turbine", "list_turbine_files",
    "TurbineModel", "PowerCurve", "NoiseCurve", "NoisePoint",
    "OCTAVE_BANDS", "A_WEIGHTING", "db_sum",
]


def list_turbine_files(directory: str) -> List[str]:
    """Pliki .wtg w katalogu (posortowane po nazwie)."""
    if not directory or not os.path.isdir(directory):
        return []
    return sorted(
        (os.path.join(directory, f) for f in os.listdir(directory) if f.lower().endswith(".wtg")),
        key=lambda p: os.path.basename(p).lower(),
    )


def load_turbine(path: str) -> TurbineModel:
    """Model turbiny: plik .wtg (loader – serwer / wersja lokalna) albo „wtg://plik” z biblioteki serwera
    (sam opis modelu, bez krzywych – wtyczka QGIS)."""
    from .remote import is_remote_wtg, meta_to_model, wtg_name
    if is_remote_wtg(path):
        from ..remote_exec import wtg_info
        return meta_to_model(wtg_info(wtg_name(path)))
    from .loader import load_local
    return load_local(path)
