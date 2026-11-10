# -*- coding: utf-8 -*-
"""
Vestas Swept Path – dane do raportu PDF (strona QGIS).

collect_report_data() składa JSON z wynikami symulacji, kontroli szablonu Vestas i analizy
terenu, render_route_map() renderuje mapę łuku do PNG. PDF buduje worker_transport_report.py
(zewnętrzny Python, reportlab + matplotlib), jak w pozostałych modułach WindLab.
"""

import os

from qgis.core import QgsGeometry, QgsMapRendererParallelJob, QgsMapSettings, QgsRectangle
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor


def _area(g):
    return round(g.area(), 1) if g is not None and not g.isEmpty() else 0.0


def render_route_map(layers, extent, out_png, size=(1600, 1000)):
    """Render warstw (kolejność: pierwsza na wierzchu) w zadanym zasięgu do PNG."""
    ms = QgsMapSettings()
    ms.setLayers([l for l in layers if l is not None])
    ms.setOutputSize(QSize(*size))
    ms.setBackgroundColor(QColor(255, 255, 255))
    ms.setOutputDpi(150)
    if layers and layers[-1] is not None:
        ms.setDestinationCrs(layers[-1].crs())
    ms.setExtent(extent)
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    img = job.renderedImage()
    img.save(out_png, "PNG")
    return out_png if os.path.isfile(out_png) else None


def collect_report_data(swept, terrain, settings, road_layer, out_dir, dem_checks=None):
    """Dane do raportu (dict serializowalny do JSON) + mapy łuków w out_dir/_transport_plots."""
    plots = os.path.join(out_dir, "_transport_plots")
    os.makedirs(plots, exist_ok=True)
    routes = []
    for r in swept.get("results") or []:
        rid = r["route_idx"] + 1
        t, best = r["turn"], r["best"]
        vs = r.get("vestas")
        item = {
            "id": rid,
            "length_m": round(r["route_geom"].length(), 1),
            "angle_deg": round(t["vestas_angle"], 1),
            "heading_change_deg": round(t["heading_change_deg"], 1),
            "side": "w lewo" if t["turn_side"] == "left" else "w prawo",
            "offset_m": round(best["offset"], 2),
            "path_mode": best.get("path_mode", "const"),
            "offsets3": [round(v, 2) for v in best["offsets3"]] if best.get("offsets3") else None,
            "r_min_path_m": round(best["r_min_path"], 1) if best.get("r_min_path") else None,
            "wheels_fit": bool(best["fits"]),
            "overrun_m2": _area(r["overrun"]),
            "oversail_m2": _area(r["oversail"]),
            "hardstand_m2": _area(r["hardstand"]),
            "straight": r.get("straight"),
        }
        if vs:
            ck, tp = vs["check"], vs["tpl"]
            item["vestas"] = {k: v for k, v in ck.items() if k != "missing"}
            item["vestas"].update({"angle_deg": round(tp["angle"], 1),
                                   "params": {k: round(v, 2) for k, v in tp["params"].items()},
                                   "note": tp["note"]})
            if dem_checks and rid in dem_checks:
                item["vestas"]["dem"] = {k: v for k, v in dem_checks[rid].items() if k != "points_over"}
        # mapa łuku: zasięg = szablon + obrys łopaty + łuk trasy
        geoms = [r["route_geom"]]
        if vs:
            geoms.append(vs["tpl"]["bearing"].buffer(15, 4))
        if r["blade"] and not r["blade"].isEmpty():
            geoms.append(r["blade"])
        bb = QgsGeometry.unaryUnion(geoms).boundingBox()
        if vs:
            fr = vs["frame"]
            cx, cy = fr["V"]
            half = max(90.0, vs["tpl"]["params"]["r2"] * 1.25)
            bb = QgsRectangle(cx - half * 1.6, cy - half, cx + half * 1.6, cy + half)
        bb.scale(1.05)
        layers = [swept.get("tpl_missing"), swept.get("tpl_swivel"), swept.get("tpl_bearing"),
                  swept.get("wheel"), swept.get("overrun"), swept.get("blade"), swept.get("vehicle"), road_layer]
        png = os.path.join(plots, f"route_{rid}.png")
        try:
            item["map_png"] = render_route_map(layers, bb, png)
        except Exception as e:  # noqa: BLE001
            item["map_png"] = None
            item["map_error"] = str(e)
        routes.append(item)

    terr = None
    if terrain:
        terr = {"profile": [], "cross": [], "vert": [], "embankment": 0}
        lyr = terrain.get("slope_profile")
        if lyr is not None:
            for f in lyr.getFeatures():
                terr["profile"].append([f["dist_m"], f["z_start"], f["slope_signed"], f["slope_cat"]])
        lyr = terrain.get("cross_slope")
        if lyr is not None:
            terr["cross"] = [[f["dist_m"], f["cross_pct"], f["flag_cat"]] for f in lyr.getFeatures()]
        lyr = terrain.get("vert_curve")
        if lyr is not None:
            terr["vert"] = [[f["dist_m"], f["radius_m"], f["curve_tp"], f["flag_cat"]] for f in lyr.getFeatures()]
        lyr = terrain.get("embankment")
        if lyr is not None:
            terr["embankment"] = lyr.featureCount()
    return {
        "schema": "windlab.transport_report.v1",
        "project": os.environ.get("WINDLAB_PROJECT_NAME", ""),
        "model": swept.get("model"), "dolly_mode": swept.get("dolly_mode"),
        "blade_len_m": round(swept.get("blade_len") or 0.0, 1),
        "settings": settings, "routes": routes, "terrain": terr,
    }
