# -*- coding: utf-8 -*-
"""WindLab – wtyczka QGIS (analizy farm wiatrowych; obliczenia na serwerze WindLab)."""


def classFactory(iface):  # noqa: N802 – nazwa wymagana przez QGIS
    """Punkt wejścia wtyczki QGIS. Import QGIS dopiero tutaj – pakiet WindLab działa też na serwerze."""
    from .plugin import WindLabPlugin
    return WindLabPlugin(iface)
