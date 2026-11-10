# -*- coding: utf-8 -*-
"""
Biblioteka turbin na serwerze obliczeń – pliki .wtg nie opuszczają serwera.

Wtyczka odwołuje się do turbiny przez „wtg://<plik>.wtg” i dostaje tylko OPIS modelu
(nazwy, wersje danych, tryby pracy, moc znamionowa, maks. LwA, czy jest widmo) –
bez krzywych mocy/Ct/hałasu. Serwer podstawia własną ścieżkę dopiero w zadaniu.

  serwer:  model_to_meta(load_turbine(path)) → JSON
  wtyczka: meta_to_model(json)               → TurbineModel z „lekkimi” krzywymi
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .model import NoiseCurve, PowerCurve, TurbineModel

SCHEME = "wtg://"


def is_remote_wtg(path: Optional[str]) -> bool:
    return bool(path) and str(path).startswith(SCHEME)


def wtg_name(path: str) -> str:
    """„wtg://VESTAS V150.wtg” → „VESTAS V150.wtg”."""
    return str(path)[len(SCHEME):]


@dataclass
class LightPowerCurve(PowerCurve):
    """Krzywa mocy bez danych – tylko opis (moc znamionowa podana wprost)."""
    rated: float = 0.0

    @property
    def rated_kw(self) -> float:
        return self.rated

    def at_density(self, rho: float):
        raise RuntimeError("Krzywa mocy jest dostępna tylko na serwerze obliczeń")

    def power_at(self, ws: float) -> float:
        raise RuntimeError("Krzywa mocy jest dostępna tylko na serwerze obliczeń")


@dataclass
class LightNoiseCurve(NoiseCurve):
    """Krzywa hałasu bez punktów – tylko opis (maks. LwA, czy jest widmo)."""
    lwa_max: float = 0.0
    octaves: bool = False

    @property
    def max_lwa(self) -> float:
        return self.lwa_max

    @property
    def has_octaves(self) -> bool:
        return self.octaves

    def curve(self, *a, **k):
        raise RuntimeError("Dane akustyczne są dostępne tylko na serwerze obliczeń")

    at = loudest = curve


def model_to_meta(tm: TurbineModel, file_name: str) -> Dict[str, Any]:
    return {
        "path": SCHEME + file_name,
        "name": tm.name, "manufacturer": tm.manufacturer, "type_variant": tm.type_variant,
        "rated_kw": tm.rated_kw, "rotor_diameter": tm.rotor_diameter,
        "default_hub_height": tm.default_hub_height, "hub_heights": list(tm.hub_heights),
        "power_curves": [
            {"label": c.label, "variant": c.variant, "code": c.code, "default": c.default,
             "source": c.source, "hub_height": c.hub_height, "rated_kw": c.rated_kw}
            for c in tm.power_curves
        ],
        "noise_curves": [
            {"label": c.label, "variant": c.variant, "code": c.code, "default": c.default,
             "source": c.source, "max_lwa": c.max_lwa, "has_octaves": c.has_octaves}
            for c in tm.noise_curves
        ],
        "warnings": list(tm.warnings),
    }


def meta_to_model(d: Dict[str, Any]) -> TurbineModel:
    return TurbineModel(
        path=d["path"], name=d.get("name") or wtg_name(d["path"]),
        manufacturer=d.get("manufacturer") or "", type_variant=d.get("type_variant") or "",
        rated_kw=float(d.get("rated_kw") or 0.0), rotor_diameter=float(d.get("rotor_diameter") or 0.0),
        default_hub_height=float(d.get("default_hub_height") or 0.0),
        hub_heights=[float(h) for h in d.get("hub_heights") or []],
        power_curves=[
            LightPowerCurve(c["label"], c["variant"], c["code"], [], [], [],
                            hub_height=c.get("hub_height"), default=bool(c.get("default")),
                            source=c.get("source") or "", rated=float(c.get("rated_kw") or 0.0))
            for c in d.get("power_curves") or []
        ],
        noise_curves=[
            LightNoiseCurve(c["label"], c["variant"], c["code"], [], default=bool(c.get("default")),
                            source=c.get("source") or "", lwa_max=float(c.get("max_lwa") or 0.0),
                            octaves=bool(c.get("has_octaves")))
            for c in d.get("noise_curves") or []
        ],
        warnings=list(d.get("warnings") or []),
    )
