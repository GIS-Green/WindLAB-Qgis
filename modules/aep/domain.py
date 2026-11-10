# -*- coding: utf-8 -*-
"""Domain objects for the AEP (NEWA time-series) module."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class LossConfig:
    """Loss handling: wake_only | global | detailed."""

    loss_type: str = "wake_only"  # wake_only | global | detailed
    global_loss_pct: float = 0.0
    detailed_losses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "loss_type": self.loss_type,
            "global_loss_pct": self.global_loss_pct if self.loss_type == "global" else None,
            "detailed_losses": self.detailed_losses if self.loss_type == "detailed" else None,
        }
        return d


@dataclass
class TurbineLayout:
    """Snapshot of turbine geometry and labels (no live QGIS objects)."""

    names: list[str]  # e.g. ["WTG_1", "WTG_4a"] — from InputNC filenames
    lons: list[float]
    lats: list[float]
    x_2180: list[float]
    y_2180: list[float]
    hub_heights: list[float]
    ids: list[Any] = field(default_factory=list)
    z: list[float] = field(default_factory=list)  # elevation if known

    @property
    def n(self) -> int:
        return len(self.names)

    def to_dict(self) -> dict:
        return {
            "userlabel": self.names,
            "names": self.names,
            "lons": self.lons,
            "lats": self.lats,
            "x_2180": self.x_2180,
            "y_2180": self.y_2180,
            "hub_heights": self.hub_heights,
            "ids": self.ids if self.ids else list(range(self.n)),
            "z": self.z if self.z else list(self.hub_heights),
        }


@dataclass
class AepRunConfig:
    """Boundary object between UI and calculation engine."""

    turbines: TurbineLayout
    nc_dir: str
    output_dir: str
    wtg_path: str
    wake_model: str = "BastankhahGaussian"
    ti: float = 0.06
    ws_bin_step: float = 0.5
    wd_bin_step: float = 5.0
    loss: LossConfig = field(default_factory=LossConfig)
    # inflow / downscaling
    inflow_mode: str = "per_turbine"  # per_turbine | farm_mean
    apply_micro_scale: bool = True
    scale_clamp_min: float = 0.70
    scale_clamp_max: float = 1.35
    project_name: str = ""
    # optional override of python executable for subprocess
    python_exe: Optional[str] = None

    def to_worker_params(self) -> dict:
        """Serialize for worker_aep.py subprocess (JSON)."""
        params = {
            "turbines": self.turbines.to_dict(),
            "wtg_path": self.wtg_path,
            "nc_dir": self.nc_dir,
            "output_dir": self.output_dir,
            "wake_model": self.wake_model,
            "ti": self.ti,
            "ws_bin_step": self.ws_bin_step,
            "wd_bin_step": self.wd_bin_step,
            "inflow_mode": self.inflow_mode,
            "apply_micro_scale": self.apply_micro_scale,
            "scale_clamp_min": self.scale_clamp_min,
            "scale_clamp_max": self.scale_clamp_max,
            "project_name": self.project_name or "–",
        }
        params.update(self.loss.to_dict())
        return params


@dataclass
class MicroPoint:
    """Single-turbine microscale climatology at hub height."""

    name: str
    wind_speed: float
    air_dens: Optional[float] = None
    power_dens: Optional[float] = None
    weib_A: Optional[float] = None
    weib_k: Optional[float] = None
    rix: Optional[float] = None
    elevation: Optional[float] = None
    height_used: Optional[float] = None


def loss_config_from_dict(d: dict) -> LossConfig:
    return LossConfig(
        loss_type=d.get("loss_type", "wake_only"),
        global_loss_pct=float(d.get("global_loss_pct") or 0.0),
        detailed_losses=list(d.get("detailed_losses") or []),
    )
