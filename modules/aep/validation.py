# -*- coding: utf-8 -*-
"""Early validation before launching the AEP worker."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .domain import AepRunConfig


@dataclass
class ValidationIssue:
    level: str  # "error" | "warning"
    message: str
    field: Optional[str] = None


def format_validation_issues(issues: list[ValidationIssue]) -> str:
    lines = []
    for iss in issues:
        prefix = "BŁĄD" if iss.level == "error" else "UWAGA"
        lines.append(f"[{prefix}] {iss.message}")
    return "\n".join(lines)


_MESO_RE = re.compile(r"^(.+)_meso\.nc$", re.IGNORECASE)
_MICRO_RE = re.compile(r"^(.+)_micro\.nc$", re.IGNORECASE)


def discover_nc_pairs(nc_dir: str) -> tuple[list[str], list[ValidationIssue]]:
    """
    Scan InputNC for {name}_meso.nc + {name}_micro.nc pairs.
    Returns sorted turbine names and any issues.
    """
    issues: list[ValidationIssue] = []
    if not os.path.isdir(nc_dir):
        issues.append(ValidationIssue("error", f"Katalog NC nie istnieje: {nc_dir}", "nc_dir"))
        return [], issues

    meso_names: set[str] = set()
    micro_names: set[str] = set()
    for fname in os.listdir(nc_dir):
        m = _MESO_RE.match(fname)
        if m:
            meso_names.add(m.group(1))
            continue
        m = _MICRO_RE.match(fname)
        if m:
            micro_names.add(m.group(1))

    only_meso = sorted(meso_names - micro_names)
    only_micro = sorted(micro_names - meso_names)
    for n in only_meso:
        issues.append(ValidationIssue(
            "error",
            f"Brak pliku micro dla '{n}': oczekiwano {n}_micro.nc",
            "nc_dir",
        ))
    for n in only_micro:
        issues.append(ValidationIssue(
            "warning",
            f"Plik {n}_micro.nc bez pary meso — pomijam",
            "nc_dir",
        ))

    paired = sorted(meso_names & micro_names, key=_natural_key)
    if not paired and not any(i.level == "error" for i in issues):
        issues.append(ValidationIssue(
            "error",
            "Brak par plików *_meso.nc / *_micro.nc w katalogu NC",
            "nc_dir",
        ))
    return paired, issues


def _natural_key(s: str):
    """Sort WTG_2 before WTG_10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def meso_path(nc_dir: str, name: str) -> str:
    return os.path.join(nc_dir, f"{name}_meso.nc")


def micro_path(nc_dir: str, name: str) -> str:
    return os.path.join(nc_dir, f"{name}_micro.nc")


def validate_aep_config(cfg: AepRunConfig) -> list[ValidationIssue]:
    """Full pre-run validation."""
    issues: list[ValidationIssue] = []

    from WindLab.core.wtg.remote import is_remote_wtg
    if not cfg.wtg_path or not (is_remote_wtg(cfg.wtg_path) or os.path.isfile(cfg.wtg_path)):
        issues.append(ValidationIssue("error", "Wybierz prawidłowy plik .wtg", "wtg_path"))

    if not cfg.nc_dir or not os.path.isdir(cfg.nc_dir):
        issues.append(ValidationIssue("error", "Katalog NC nie istnieje", "nc_dir"))
        return issues

    if not cfg.output_dir:
        issues.append(ValidationIssue("error", "Podaj katalog wyjściowy", "output_dir"))

    paired, disc_issues = discover_nc_pairs(cfg.nc_dir)
    issues.extend(disc_issues)

    if cfg.turbines.n == 0:
        issues.append(ValidationIssue("error", "Brak turbin w układzie", "turbines"))
        return issues

    names = list(cfg.turbines.names)
    missing_in_nc = [n for n in names if n not in paired]
    for n in missing_in_nc:
        issues.append(ValidationIssue(
            "error",
            f"Turbina '{n}' z warstwy nie ma pary {n}_meso.nc / {n}_micro.nc",
            "turbines",
        ))

    extra_in_nc = [n for n in paired if n not in names]
    for n in extra_in_nc:
        issues.append(ValidationIssue(
            "warning",
            f"Para NC '{n}' nie ma dopasowanego punktu w warstwie (userlabel) — pomijam",
            "turbines",
        ))

    if len(cfg.turbines.x_2180) != cfg.turbines.n:
        issues.append(ValidationIssue("error", "Niespójna liczba współrzędnych x", "turbines"))
    if len(cfg.turbines.hub_heights) != cfg.turbines.n:
        issues.append(ValidationIssue("error", "Niespójna liczba hub_height", "turbines"))

    for i, h in enumerate(cfg.turbines.hub_heights):
        if h is None or not (30 <= float(h) <= 300):
            issues.append(ValidationIssue(
                "error",
                f"Nieprawidłowa wysokość piasty dla {names[i] if i < len(names) else i}: {h}",
                "hub_height",
            ))

    if cfg.loss.loss_type == "global":
        if not (0 <= cfg.loss.global_loss_pct <= 100):
            issues.append(ValidationIssue("error", "Globalna strata musi być w zakresie 0–100 %", "loss"))
    elif cfg.loss.loss_type == "detailed":
        active = [l for l in cfg.loss.detailed_losses if l.get("active", True)]
        if not active:
            issues.append(ValidationIssue(
                "warning",
                "Tryb detailed bez aktywnych strat — wynik = tylko wake",
                "loss",
            ))

    if cfg.inflow_mode not in ("per_turbine", "farm_mean"):
        issues.append(ValidationIssue("error", f"Nieznany inflow_mode: {cfg.inflow_mode}", "inflow_mode"))

    if cfg.turbines.n > 30 and cfg.inflow_mode == "per_turbine":
        issues.append(ValidationIssue(
            "warning",
            f"Duża farma ({cfg.turbines.n} WT) + per_turbine może być wolna — rozważ farm_mean",
            "inflow_mode",
        ))

    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.level == "error" for i in issues)
