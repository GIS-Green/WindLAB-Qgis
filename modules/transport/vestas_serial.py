# -*- coding: utf-8 -*-
"""Przesyłanie wyników symulacji transportu (serwer ↔ wtyczka): geometrie jako WKT, czasy jako ISO."""

import math
from datetime import datetime


def to_jsonable(obj):
    """Geometrie → {"__wkt__": …}, czasy → {"__dt__": ISO}; reszta bez zmian."""
    if hasattr(obj, "asWkt") and hasattr(obj, "isEmpty"):            # QgsGeometry (QGIS albo shapely)
        return {"__wkt__": obj.asWkt() if not obj.isEmpty() else ""}
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if hasattr(obj, "toPyDateTime"):                       # QDateTime
        return {"__dt__": obj.toPyDateTime().isoformat()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if hasattr(obj, "x") and hasattr(obj, "y") and callable(obj.x):   # QgsPointXY
        return [obj.x(), obj.y()]
    return obj


def from_jsonable(obj, geom_cls, dt_conv=None):
    """Odwrotność to_jsonable: WKT → geom_cls (np. QgsGeometry z QGIS), ISO → dt_conv(datetime)."""
    if isinstance(obj, dict):
        if "__wkt__" in obj and len(obj) == 1:
            return geom_cls.fromWkt(obj["__wkt__"]) if obj["__wkt__"] else geom_cls()
        if "__dt__" in obj and len(obj) == 1:
            dt = datetime.fromisoformat(obj["__dt__"])
            return dt_conv(dt) if dt_conv else dt
        return {k: from_jsonable(v, geom_cls, dt_conv) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_jsonable(v, geom_cls, dt_conv) for v in obj]
    return obj
