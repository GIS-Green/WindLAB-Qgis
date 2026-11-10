# -*- coding: utf-8 -*-
"""
Jednolity model turbiny WindLab – niezależny od formatu pliku źródłowego.

TurbineModel
  ├─ power_curves: [PowerCurve]  – krzywa mocy + Ct dla trybu pracy
  └─ noise_curves: [NoiseCurve]  – LwA(WS) + widmo oktawowe (ważone A)

Prędkość wiatru w krzywych hałasu ma jedno z dwóch odniesień:
  „hub” – prędkość na wysokości piasty,
  „10m” – prędkość na 10 m (dane dla konkretnej wysokości piasty).
Producent podaje często obie wersje z różnymi widmami – wybór odniesienia
zmienia wynik na receptorze (dla V150 o ~0,9 dB), więc jest zawsze jawny.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

OCTAVE_BANDS = [63, 125, 250, 500, 1000, 2000, 4000, 8000]
A_WEIGHTING = {63: -26.2, 125: -16.1, 250: -8.6, 500: -3.2, 1000: 0.0, 2000: 1.2, 4000: 1.0, 8000: -1.1}


def db_sum(values) -> float:
    e = sum(10.0 ** (v / 10.0) for v in values if v is not None and math.isfinite(v))
    return 10.0 * math.log10(e) if e > 0 else float("-inf")


# ── Krzywa mocy ───────────────────────────────────────────────────────────────
@dataclass
class PowerCurve:
    label: str                      # nazwa z pliku (np. „SO2 - 2024-10”)
    variant: str                    # wersja danych / rekord Power Matrix
    code: str                       # kod trybu do łączenia z hałasem (np. „SO2”)
    ws: List[float]                 # m/s (na wysokości piasty)
    power_kw: List[float]
    ct: List[float]
    air_density: float = 1.225
    hub_height: Optional[float] = None
    default: bool = False
    source: str = "windpro"         # windpro | powermatrix | wasp
    # Power Matrix: pełne macierze (WS × gęstość) do przeliczenia gęstości
    _pm_rho: Optional[List[float]] = field(default=None, repr=False)
    _pm_power: Optional[list] = field(default=None, repr=False)
    _pm_ct: Optional[list] = field(default=None, repr=False)

    @property
    def rated_kw(self) -> float:
        return max(self.power_kw) if self.power_kw else 0.0

    @property
    def display(self) -> str:
        return f"{self.label}  ({self.rated_kw:.0f} kW)"

    def at_density(self, rho: float) -> "PowerCurve":
        """Krzywa dla zadanej gęstości powietrza (Power Matrix: interpolacja kolumn)."""
        if self._pm_rho is None or self._pm_power is None:
            return self
        import numpy as np
        rhos = np.asarray(self._pm_rho)
        pw = np.asarray(self._pm_power)
        ct = np.asarray(self._pm_ct) if self._pm_ct is not None else None
        p = [float(np.interp(rho, rhos, pw[i])) for i in range(pw.shape[0])]
        c = [float(np.interp(rho, rhos, ct[i])) for i in range(ct.shape[0])] if ct is not None else list(self.ct)
        return PowerCurve(self.label, self.variant, self.code, list(self.ws), p, c, float(rho),
                          self.hub_height, self.default, self.source)

    def power_at(self, ws: float) -> float:
        """Moc [kW] przy prędkości na wysokości piasty (interpolacja liniowa, 0 poza zakresem)."""
        if not self.ws or ws < self.ws[0] or ws > self.ws[-1]:
            return 0.0
        for i in range(len(self.ws) - 1):
            if self.ws[i] <= ws <= self.ws[i + 1]:
                t = (ws - self.ws[i]) / ((self.ws[i + 1] - self.ws[i]) or 1e-9)
                return self.power_kw[i] + t * (self.power_kw[i + 1] - self.power_kw[i])
        return self.power_kw[-1]


# ── Krzywa hałasu ─────────────────────────────────────────────────────────────
@dataclass
class NoisePoint:
    ws: float
    ref: str                        # "hub" | "10m"
    hub_height: Optional[float]     # wysokość piasty, dla której podano punkt „10m”
    lwa: float
    octaves: Optional[Dict[int, float]] = None   # ważone A, 63…8000 Hz
    tonal: bool = False


@dataclass
class NoiseCurve:
    label: str
    variant: str
    code: str
    points: List[NoisePoint]
    default: bool = False
    source: str = "windpro"

    @property
    def max_lwa(self) -> float:
        return max((p.lwa for p in self.points), default=0.0)

    @property
    def has_octaves(self) -> bool:
        return any(p.octaves for p in self.points)

    @property
    def refs(self) -> List[str]:
        return sorted({p.ref for p in self.points})

    @property
    def display(self) -> str:
        okt = "" if self.has_octaves else ", bez widma"
        return f"{self.label}  ({self.max_lwa:.1f} dB(A){okt})"

    def curve(self, ref: str = "10m", hub_height: Optional[float] = None) -> Tuple[List[NoisePoint], Optional[float]]:
        """Punkty danego odniesienia, posortowane po WS.

        Dla „10m” wybiera dane dla wysokości piasty najbliższej podanej
        (jak WindPRO: „From other hub height”). Zwraca (punkty, użyta wysokość).
        """
        pts = [p for p in self.points if p.ref == ref and p.lwa > 0]
        used = None
        if ref == "10m" and pts:
            heights = sorted({p.hub_height for p in pts if p.hub_height})
            if heights:
                used = heights[0] if hub_height is None else min(heights, key=lambda h: abs(h - hub_height))
                pts = [p for p in pts if p.hub_height == used]
        return sorted(pts, key=lambda p: p.ws), used

    def at(self, ws: float, ref: str = "10m", hub_height: Optional[float] = None) -> Optional[NoisePoint]:
        """LwA i widmo przy prędkości WS (interpolacja liniowa; poza zakresem – skrajny punkt)."""
        pts, used = self.curve(ref, hub_height)
        if not pts:
            return None
        if ws <= pts[0].ws:
            return pts[0]
        if ws >= pts[-1].ws:
            return pts[-1]
        for a, b in zip(pts, pts[1:]):
            if a.ws <= ws <= b.ws:
                t = (ws - a.ws) / ((b.ws - a.ws) or 1e-9)
                octs = None
                if a.octaves and b.octaves:
                    octs = {f: a.octaves[f] + t * (b.octaves[f] - a.octaves[f]) for f in OCTAVE_BANDS}
                return NoisePoint(ws, ref, used, a.lwa + t * (b.lwa - a.lwa), octs, a.tonal or b.tonal)
        return pts[-1]

    def loudest(self, ref: str = "10m", hub_height: Optional[float] = None,
                ws_max: Optional[float] = 10.0) -> Optional[NoisePoint]:
        """Najgłośniejszy punkt do ws_max (WindPRO „Loudest up to 95% rated power”:
        głośniejszy z punktów 95% mocy i 10 m/s → maks. LwA do 10 m/s).
        Przy równych LwA wybiera najwyższą prędkość (pełna moc)."""
        pts, _ = self.curve(ref, hub_height)
        if ws_max is not None:
            pts = [p for p in pts if p.ws <= ws_max + 1e-9] or pts
        if not pts:
            return None
        return max(pts, key=lambda p: (round(p.lwa, 2), p.ws))


# ── Turbina ───────────────────────────────────────────────────────────────────
@dataclass
class TurbineModel:
    path: str
    name: str
    manufacturer: str = ""
    type_variant: str = ""
    rated_kw: float = 0.0
    rotor_diameter: float = 0.0
    default_hub_height: float = 0.0
    hub_heights: List[float] = field(default_factory=list)
    blade_width_max: float = 0.0
    blade_width_90: float = 0.0
    rpm_nominal: float = 0.0
    power_curves: List[PowerCurve] = field(default_factory=list)
    noise_curves: List[NoiseCurve] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ── wybór krzywych ───────────────────────────────────────────────────────
    def find_power(self, label: Optional[str]) -> Optional[PowerCurve]:
        if label:
            for c in self.power_curves:
                if c.label == label:
                    return c
        return self.default_power_curve()

    def find_noise(self, label: Optional[str]) -> Optional[NoiseCurve]:
        if label:
            for c in self.noise_curves:
                if c.label == label:
                    return c
        return self.default_noise_curve()

    def default_power_curve(self) -> Optional[PowerCurve]:
        """Krzywa domyślna: najnowsza wersja danych, pełna moc (maks. moc znamionowa)."""
        if not self.power_curves:
            return None
        return max(self.power_curves, key=lambda c: (_variant_rank(c.variant), round(c.rated_kw), c.default))

    def default_noise_curve(self) -> Optional[NoiseCurve]:
        """Hałas trybu domyślnego: para krzywej mocy domyślnej, inaczej najnowsza i najgłośniejsza."""
        if not self.noise_curves:
            return None
        pc = self.default_power_curve()
        if pc is not None:
            paired = [n for n in self.noise_curves if self.pair_power(n) is pc]
            if paired:
                return max(paired, key=lambda n: (n.has_octaves, _variant_rank(n.variant), -n.max_lwa))
        return max(self.noise_curves, key=lambda n: (n.has_octaves, _variant_rank(n.variant), n.max_lwa))

    def pair_power(self, noise: NoiseCurve) -> Optional[PowerCurve]:
        """Krzywa mocy tego samego trybu pracy (dopasowanie po kodzie i wersji danych)."""
        best, best_score = None, 0
        ncodes = set(noise.code.split("/"))
        for pc in self.power_curves:
            pcodes = set(pc.code.split("/"))
            if not (ncodes & pcodes):
                continue
            score = 10 + (5 if pc.variant == noise.variant else 0)
            if score > best_score:
                best, best_score = pc, score
        return best

    def operating_modes(self) -> List[Tuple[NoiseCurve, PowerCurve]]:
        """Tryby pracy z kompletem danych (hałas + moc) – do optymalizacji."""
        out = []
        for n in self.noise_curves:
            pc = self.pair_power(n)
            if pc is not None:
                out.append((n, pc))
        return out


# ── kody trybów i wersje danych ──────────────────────────────────────────────
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})(?:-\d{2})?\b|\b(\d{1,2})-(\d{4})\b")
_CODE_PATTERNS = [
    r"\bPO\d+(?:-0S)?\b", r"\bSO\d+\b", r"\b\d-0S\b",
    r"\bMode\s*\d+(?:\.\w+)?\b", r"\bAM\s*-?\d+(?:\.\d+)?\b", r"\bNR[OS]?\s*\d+\b",
]


def variant_tag(name: str) -> str:
    """Wersja danych z nazwy: data (RRRR-MM) albo numer rewizji (R03)."""
    m = _DATE_RE.search(name or "")
    if m:
        if m.group(1):
            return f"{m.group(1)}-{m.group(2)}"
        return f"{m.group(4)}-{int(m.group(3)):02d}"
    m = re.search(r"\bR\d{2}\b", name or "")
    return m.group(0) if m else ""


def _variant_rank(v: str) -> str:
    return v or ""


def mode_code(name: str) -> str:
    """Kod trybu pracy z nazwy krzywej, np. „Level 2 - Measured - Mode SO2 - 10-2020” → „SO2”,
    „Level 0 - - Modes PO6000/PO6000-0S” → „PO6000/PO6000-0S”, „Mode 12 - 97.0 db(A)” → „MODE12”.
    Tryby z ząbkowaną krawędzią spływu (Serrations/STE) dostają przyrostek „+STE”."""
    s = name or ""
    found: List[str] = []
    for part in s.split("/"):
        for pat in _CODE_PATTERNS:
            for m in re.finditer(pat, part, flags=re.I):
                code = re.sub(r"\s+", "", m.group(0)).upper()
                if code not in found:
                    found.append(code)
    if not found:
        cleaned = _DATE_RE.sub("", s)
        cleaned = re.sub(r"\d+(?:[.,]\d+)?\s*d[bB]\s*\(?A\)?", "", cleaned)
        cleaned = re.sub(r"\b(Level\s*\S+|Measured|Calculated|Modes?)\b", "", cleaned, flags=re.I)
        found = [re.sub(r"[\s\-]+", " ", cleaned).strip(" -").upper() or s.upper()]
    if re.search(r"serration|\bSTE\b", s, flags=re.I):
        found = [f + "+STE" for f in found]
    return "/".join(found)
