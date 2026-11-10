# -*- coding: utf-8 -*-
"""
Czysty silnik Python – bez zależności od QGIS.
"""
from .types import (
    SourcePoint, ReceiverPoint, RunConfig, ReceiverResult, RunResult,
    GridConfig, NoiseGrid,
    OCTAVE_BANDS, A_WEIGHTING,
)
from .labels import AIR_ABSORPTION_LABELS, RULE_LABELS, REF_LABELS

# Obliczenia (propagation / source_data) ładowane dopiero przy użyciu – wtyczka QGIS
# (samo GUI) ich nie zawiera, liczy serwer obliczeń.
_LAZY = {
    "AIR_ABSORPTION": "propagation", "run": "propagation", "compute_noise_grid": "propagation",
    "attenuation_table": "propagation", "band_attenuation": "propagation",
    "level_from_attenuation": "propagation", "source_noise": "source_data",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(name)

__all__ = [
    "SourcePoint", "ReceiverPoint", "RunConfig", "ReceiverResult", "RunResult",
    "GridConfig", "NoiseGrid", "OCTAVE_BANDS", "A_WEIGHTING",
    "AIR_ABSORPTION", "AIR_ABSORPTION_LABELS",
    "run", "compute_noise_grid", "attenuation_table", "band_attenuation", "level_from_attenuation",
    "source_noise", "RULE_LABELS", "REF_LABELS",
]
