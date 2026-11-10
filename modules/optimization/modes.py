# -*- coding: utf-8 -*-
"""Tryby pracy turbiny do optymalizacji hałasu (GUI i silnik) – sam wybór trybów, bez obliczeń."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass
class Mode:
    code: str
    label: str
    rated_kw: float
    noise: object = None      # NoiseCurve (None dla STOP)
    power: object = None      # PowerCurve (None dla STOP)


def candidate_modes(tm, base_label: str, include: Optional[Sequence[str]] = None) -> Tuple[Mode, List[Mode], List[str]]:
    """Tryb bazowy + dopuszczalne tryby (ta sama wersja danych, z widmem oktawowym).

    include – lista etykiet krzywych hałasu wybranych przez użytkownika (None = automatycznie).
    Tryby z ząbkami wyłączonymi („-0S”) są pomijane, jeśli tryb bazowy ich nie ma.
    """
    notes: List[str] = []
    base_nc = tm.find_noise(base_label)
    if base_nc is None:
        raise ValueError(f"Plik {tm.name} nie zawiera krzywych hałasu")
    pairs = tm.operating_modes()
    base_pc = tm.pair_power(base_nc)
    if base_pc is None:
        raise ValueError(f"Brak krzywej mocy dla trybu „{base_nc.label}”")
    base = Mode(base_nc.code, base_nc.label, base_pc.rated_kw, base_nc, base_pc)
    out: List[Mode] = []
    for nc, pc in pairs:
        if include is not None:
            if nc.label not in include and nc.label != base_nc.label:
                continue
        else:
            if nc.variant != base_nc.variant:
                continue
            if "-0S" in nc.code.upper() and "-0S" not in base_nc.code.upper():
                continue
        if not nc.has_octaves:
            notes.append(f"tryb „{nc.label}” pominięty – brak widma oktawowego")
            continue
        out.append(Mode(nc.code, nc.label, pc.rated_kw, nc, pc))
    if not any(m.label == base.label for m in out):
        out.insert(0, base)
    out.sort(key=lambda m: (-m.rated_kw, m.noise.max_lwa))
    return base, out, notes
