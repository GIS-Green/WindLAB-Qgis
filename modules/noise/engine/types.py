# -*- coding: utf-8 -*-
"""
Czyste typy danych – zero zależności od QGIS.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


OCTAVE_BANDS = [63, 125, 250, 500, 1000, 2000, 4000, 8000]

A_WEIGHTING = {
    63: -26.2,
    125: -16.1,
    250: -8.6,
    500: -3.2,
    1000: 0.0,
    2000: 1.2,
    4000: 1.0,
    8000: -1.1,
}


@dataclass
class SourcePoint:
    """Jedna turbina – dane już zserializowane (bez obiektów QGIS)."""
    id: int
    x: float
    y: float
    hub_height: float
    model_name: str
    lwa: float
    z_ground: Optional[float] = None
    lw_octave: Optional[Dict[int, float]] = None  # {63: Lw, ...}
    diameter: Optional[float] = None
    name: str = ""  # USERLABEL / nazwa z warstwy
    octave_a_weighted: bool = True     # widma z .wtg są ważone A
    # pochodzenie danych akustycznych (do raportu i optymalizacji)
    wtg_path: str = ""
    curve_label: str = ""
    noise_ws: Optional[float] = None   # prędkość wiatru użyta z krzywej
    noise_ref: str = ""                # "10m" | "hub"
    noise_hub_height: Optional[float] = None  # wysokość piasty danych producenta
    tonal: bool = False


@dataclass
class ReceiverPoint:
    """Jeden receptor – dane zserializowane."""
    id: int
    x: float
    y: float
    receiver_height: float
    z_ground: Optional[float] = None
    name: str = ""
    limit_dba: Optional[float] = None


@dataclass
class RunConfig:
    """Pełna konfiguracja obliczeń – tylko prymitywy."""
    sources: List[SourcePoint]
    receivers: List[ReceiverPoint]
    engine: str = "iso"                   # ISO 9613-2
    wind_speed: float = 8.0
    max_radius_m: float = 20000.0         # jak WindPRO: wszystkie turbiny (5 km pomijało dalsze WTG)
    receiver_limit_dba: float = 40.0
    ground_g: float = 0.5                 # współczynnik gruntu G
    alpha_db_per_m: float = 0.002         # nieużywane (zgodność wsteczna)
    level_offset_db: float = 0.0          # margines niepewności dodawany do wyników [dB]
    path_max_z: Optional[Dict[str, float]] = None  # nieużywane (zgodność wsteczna)
    grid: Optional["GridConfig"] = None
    ground_method: str = "general"        # "general" (ISO 7.3.1) | "simplified" (ISO 7.3.2)
    air_absorption: str = "10C_70"        # tabela α z ISO 9613-2
    c0_db: float = 0.0                    # współczynnik meteorologiczny C0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunConfig":
        sources = [SourcePoint(**s) for s in d.get("sources", [])]
        receivers = [ReceiverPoint(**r) for r in d.get("receivers", [])]
        return cls(
            sources=sources,
            receivers=receivers,
            engine=d.get("engine", "iso"),
            wind_speed=float(d.get("wind_speed", 8.0)),
            max_radius_m=float(d.get("max_radius_m", 20000.0)),
            receiver_limit_dba=float(d.get("receiver_limit_dba", 40.0)),
            ground_g=float(d.get("ground_g", 0.5)),
            alpha_db_per_m=float(d.get("alpha_db_per_m", 0.002)),
            level_offset_db=float(d.get("level_offset_db", 0.0)),
            path_max_z=d.get("path_max_z"),
            ground_method=d.get("ground_method", "general"),
            air_absorption=d.get("air_absorption", "10C_70"),
            c0_db=float(d.get("c0_db", 0.0)),
        )


@dataclass
class ReceiverResult:
    receiver_id: int
    receiver_name: str
    x: float
    y: float
    noise_dba: Optional[float]
    limit_dba: float
    margin_dba: Optional[float]
    exceeds: bool
    n_sources: int
    dominant_source_id: Optional[int]
    dominant_model: Optional[str]
    dominant_lp: Optional[float]
    contributions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RunResult:
    results: List[ReceiverResult]
    n_sources: int
    n_receivers: int
    max_noise_dba: float
    n_exceedances: int
    engine: str
    messages: List[str] = field(default_factory=list)
    noise_grid: Optional["NoiseGrid"] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "results": [asdict(r) for r in self.results],
            "n_sources": self.n_sources,
            "n_receivers": self.n_receivers,
            "max_noise_dba": self.max_noise_dba,
            "n_exceedances": self.n_exceedances,
            "engine": self.engine,
            "messages": self.messages,
        }
        if self.noise_grid is not None:
            d["noise_grid"] = asdict(self.noise_grid)
        return d


@dataclass
class GridConfig:
    """Konfiguracja siatki hałasu do izolinii."""
    enabled: bool = False
    resolution_m: float = 100.0
    # extent: None = auto z sources + buffer
    xmin: Optional[float] = None
    ymin: Optional[float] = None
    xmax: Optional[float] = None
    ymax: Optional[float] = None
    buffer_m: float = 2000.0
    iso_levels: List[float] = field(default_factory=lambda: [35.0, 40.0, 45.0, 50.0, 55.0])
    receiver_height: float = 4.0


@dataclass
class NoiseGrid:
    """Wynikowa siatka hałasu (czysty Python)."""
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    resolution_m: float
    ncols: int
    nrows: int
    # wiersze od północy do południa (jak GeoTIFF), wartości dB(A) lub None
    values: List[List[Optional[float]]] = field(default_factory=list)
    iso_levels: List[float] = field(default_factory=list)
