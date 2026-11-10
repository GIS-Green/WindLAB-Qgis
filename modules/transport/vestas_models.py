# -*- coding: utf-8 -*-
"""
Vestas Swept Path – lista modeli i ustawienia domyślne dla GUI (wtyczka QGIS).
Wymiary i szablony z dokumentacji Vestas są tylko na serwerze obliczeń (vestas_params.py).
"""
import contextlib

MODELS = ("V150", "V162", "V172")
ACTIVE_DOLLY = {"V150": False, "V162": True, "V172": True}   # czy wózek ma aktywnie skrętną oś

BLADE_ROOT_ENV_M = 7.0          # nasada łopaty wliczana do obwiedni pojazdu [m]
DEFAULT_STEER_GAIN = 0.85
DEFAULT_WHEEL_WEIGHT = 500.0
DEFAULT_TRACTOR_R_MIN = 12.0    # m  min. promień toru ciągnika (sworzeń siodła) – parametr użytkownika


def validate_crs(crs, feedback=None):
    if crs is None or not crs.isValid():
        msg = "CRS warstwy jest nieprawidłowy lub nieustawiony."
        if feedback:
            feedback.reportError(msg)
        return False
    if crs.isGeographic():
        msg = (f"CRS {crs.authid()} jest geograficzny (stopnie). "
               "Symulacja wymaga układu metrycznego (np. EPSG:2180, UTM).")
        if feedback:
            feedback.reportError(msg)
        return False
    with contextlib.suppress(Exception):
        units = crs.mapUnits()
        from qgis.core import QgsUnitTypes
        if units != QgsUnitTypes.DistanceUnit.DistanceMeters:
            msg = (f"CRS {crs.authid()} ma jednostki inne niż metry "
                   f"(mapUnits={units}). Wyniki będą zniekształcone.")
            if feedback:
                feedback.pushWarning(msg)
    return True
