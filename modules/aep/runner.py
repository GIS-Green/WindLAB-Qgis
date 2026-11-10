# -*- coding: utf-8 -*-
"""Run AEP worker as subprocess (isolates calculation libraries/PROJ from QGIS)."""

from __future__ import annotations

import json
import os
from ...core import remote_exec as rx
import sys
from typing import Callable, Optional

from .domain import AepRunConfig
from .validation import has_errors, validate_aep_config, format_validation_issues
import contextlib

_AEP_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKER_AEP = os.path.join(_AEP_ROOT, "workers", "worker_aep.py")
WORKER_REPORT = os.path.join(_AEP_ROOT, "workers", "worker_aep_report.py")


class EnergyValidationError(Exception):
    def __init__(self, message: str, issues=None):
        super().__init__(message)
        self.issues = issues or []


def default_python() -> str:
    return os.environ.get("AEP_PYTHON") or sys.executable


def run_aep(
    cfg: AepRunConfig,
    *,
    log: Optional[Callable[[str], None]] = None,
    timeout: int = 3600,
) -> dict:
    """Validate config and run worker_aep.py. Returns result dict."""
    issues = validate_aep_config(cfg)
    if has_errors(issues):
        raise EnergyValidationError(format_validation_issues(issues), issues)

    for iss in issues:
        if log and iss.level == "warning":
            log(f"[UWAGA] {iss.message}")

    python_exe = cfg.python_exe or default_python()
    params = cfg.to_worker_params()

    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)

    if log:
        log(f"Uruchamiam: {os.path.basename(WORKER_AEP)}  python={python_exe}")

    import tempfile
    fd, params_path = tempfile.mkstemp(suffix=".json", prefix="aep_params_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False)
        r = rx.run(
            [python_exe, WORKER_AEP, "--params-file", params_path],
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    finally:
        with contextlib.suppress(Exception):
            os.unlink(params_path)

    stdout = r.stdout.decode("utf-8", errors="replace").strip()
    stderr = r.stderr.decode("utf-8", errors="replace").strip()

    if log and stderr:
        for line in stderr.splitlines():
            if line.strip():
                log(f"  [log] {line}")

    if not stdout:
        raise RuntimeError(f"Brak outputu workera\n{stderr[:500]}")

    lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
    data = json.loads(lines[-1] if lines else stdout)
    if data.get("status") == "error":
        raise RuntimeError(f"{data.get('message')}\n{data.get('trace', '')}")
    return data


def run_report(
    result: dict,
    nc_dir: str,
    pdf_path: str,
    output_dir: str,
    *,
    python_exe: Optional[str] = None,
    timeout: int = 300,
) -> None:
    import tempfile

    python_exe = python_exe or default_python()
    fd, json_path = tempfile.mkstemp(suffix=".json", prefix="aep_result_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)

        proc = rx.run(
            [python_exe, WORKER_REPORT, json_path, nc_dir, pdf_path, output_dir],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Raport failed ({proc.returncode}): {proc.stderr or proc.stdout}"
            )
        if not os.path.isfile(pdf_path):
            raise FileNotFoundError(pdf_path)
    finally:
        with contextlib.suppress(Exception):
            os.unlink(json_path)
