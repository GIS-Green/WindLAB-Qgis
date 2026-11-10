# -*- coding: utf-8 -*-
"""
Dane wejściowe / wyjściowe modułu optymalizacji strat środowiskowych.

Pliki wejściowe
---------------
Shadow (model dokładny):
  shadow_optimization_calendars.json  schema: windlab.shadow_optimization.v1

Noise (tryby pracy producenta, MILP):
  noise_paths.json                    schema: windlab.noise_paths.v1 (moduł Hałas)
  plik .wtg wskazany w noise_paths.json (krzywe hałasu i mocy trybów)
  *_meso.nc (NEWA)                    szeregi czasowe wiatru – energia nocna

AEP (opcjonalnie):
  aep_results.json – AEP przed, deficyt śladu (ws_eff/meso) i pozostałe straty (final/net)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class OptimizationConfig:
    shadow_calendars_path: Optional[str] = None
    noise_paths_path: Optional[str] = None
    aep_result_path: Optional[str] = None
    nc_dir: Optional[str] = None
    limit_hours_year: float = 30.0
    limit_minutes_day: Optional[float] = 30.0  # None = bez limitu dziennego
    limit_dba: Optional[float] = None         # None = limity receptorów z modułu Hałas
    enable_shadow: bool = True
    enable_noise: bool = True
    output_dir: str = ""
    project_name: str = ""
    # noise: "per_ws" (tryby zależne od prędkości wiatru) | "fixed" (jeden tryb na noc)
    noise_mode_strategy: str = "per_ws"
    noise_modes: Optional[List[str]] = None    # etykiety dopuszczonych krzywych hałasu (None = auto)
    night_start_h: int = 22
    night_end_h: int = 6
    hours_per_year: float = 8760.0

    @classmethod
    def from_params(cls, p: Dict[str, Any]) -> "OptimizationConfig":
        lim = p.get("limit_dba")
        return cls(
            shadow_calendars_path=p.get("shadow_calendars_path"),
            noise_paths_path=p.get("noise_paths_path") or p.get("noise_matrix_path"),
            aep_result_path=p.get("aep_result_path"),
            nc_dir=p.get("nc_dir"),
            limit_hours_year=float(p.get("limit_hours_year", 30)),
            limit_minutes_day=(float(p["limit_minutes_day"]) if p.get("limit_minutes_day") not in (None, "", 0)
                               else None) if "limit_minutes_day" in p else 30.0,
            limit_dba=float(lim) if lim not in (None, "") else None,
            enable_shadow=bool(p.get("enable_shadow", True)),
            enable_noise=bool(p.get("enable_noise", True)),
            output_dir=p.get("output_dir") or "",
            project_name=p.get("project_name") or "",
            noise_mode_strategy=p.get("noise_mode_strategy") or "per_ws",
            noise_modes=p.get("noise_modes") or None,
            night_start_h=int(p.get("night_start_h", 22)),
            night_end_h=int(p.get("night_end_h", 6)),
            hours_per_year=float(p.get("hours_per_year", 8760)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CurtailmentRule:
    turbine_name: str
    kind: str  # shadow_off | noise_mode | noise_stop
    # shadow: liczba slotów / minut
    n_slots: int = 0
    minutes_off: float = 0.0
    # noise
    ws_cap: Optional[float] = None
    ws_bins_affected: List[float] = field(default_factory=list)
    energy_loss_mwh: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OptimizationResult:
    rules: List[CurtailmentRule] = field(default_factory=list)
    aep_before_mwh: float = 0.0
    aep_after_mwh: float = 0.0
    loss_shadow_mwh: float = 0.0
    loss_noise_mwh: float = 0.0
    loss_total_mwh: float = 0.0
    loss_total_pct: float = 0.0
    residual_shadow_exceedances: int = 0
    residual_noise_exceedances: int = 0
    shadow_summary: Dict[str, Any] = field(default_factory=dict)
    noise_summary: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    output_files: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rules"] = [r if isinstance(r, dict) else r.to_dict() for r in self.rules]
        return d
