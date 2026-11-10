# -*- coding: utf-8 -*-
"""Vestas Swept Path - warstwa orkiestracji (v2)"""

import math

from qgis.core import (
    QgsFeature, QgsFields, QgsField, QgsGeometry, QgsWkbTypes,
    QgsVectorLayer, Qgis
)
from qgis.PyQt.QtCore import QVariant

from .vestas_models import (
    ACTIVE_DOLLY, validate_crs, BLADE_ROOT_ENV_M, DEFAULT_STEER_GAIN, DEFAULT_WHEEL_WEIGHT,
)
import contextlib

try:
    _MODE_START_END = Qgis.VectorTemporalMode.FeatureDateTimeStartAndEndFromFields
except AttributeError:
    from qgis.core import QgsVectorLayerTemporalProperties as _QVLTP
    _MODE_START_END = _QVLTP.ModeFeatureDateTimeStartAndEndFromFields


def _enable_temporal(layer):
    tp = layer.temporalProperties()
    tp.setStartField("t_start")
    tp.setEndField("t_end")
    tp.setMode(_MODE_START_END)
    tp.setIsActive(True)


def _make_layer(geom_type, name, crs, fields_defs):
    uri = f"{geom_type}?crs={crs.authid()}"
    lyr = QgsVectorLayer(uri, name, "memory")
    prov = lyr.dataProvider()
    flds = QgsFields()
    for n, t in fields_defs:
        flds.append(QgsField(n, t))
    prov.addAttributes(flds)
    lyr.updateFields()
    return lyr


def _style_template_layers(lyr_tpl, lyr_swv, lyr_mis):
    """Styl warstw szablonu Vestas: obrys nośnej, kreskowane strefy przelotu, czerwone braki."""
    from qgis.core import QgsFillSymbol
    lyr_tpl.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "30,90,160,255", "outline_width": "0.6", "outline_style": "dash"}))
    lyr_swv.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "255,190,60,90", "outline_color": "200,130,0,255", "outline_width": "0.3"}))
    lyr_mis.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "220,30,30,140", "outline_color": "160,0,0,255", "outline_width": "0.4"}))
    for lyr in (lyr_tpl, lyr_swv, lyr_mis):
        lyr.triggerRepaint()


def _geom_to_single_line(geom):
    if geom is None or geom.isEmpty():
        return None
    wkb = geom.wkbType()
    if wkb in (QgsWkbTypes.Type.LineString, QgsWkbTypes.Type.LineString25D,
                QgsWkbTypes.Type.LineStringZ, QgsWkbTypes.Type.LineStringM,
                QgsWkbTypes.Type.LineStringZM):
        return geom
    parts = [QgsGeometry.fromWkt(part.asWkt()) for part in geom.parts()]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    merged = QgsGeometry.collectGeometry(parts).mergeLines()
    if merged and not merged.isEmpty():
        if merged.isMultipart():
            best, best_len = None, 0.0
            for part in merged.parts():
                g = QgsGeometry.fromWkt(part.asWkt())
                if g.length() > best_len:
                    best_len, best = g.length(), g
            return best
        return merged
    best, best_len = None, 0.0
    for part in geom.parts():
        g = QgsGeometry.fromWkt(part.asWkt())
        if g.length() > best_len:
            best_len, best = g.length(), g
    return best


def _simulate_remote(pairs, opts, feedback, crs_authid="EPSG:2180"):
    """Symulacja na serwerze obliczeń (worker_transport.py) → wyniki z geometriami QgsGeometry."""
    import json
    import os
    import sys
    import tempfile
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime
    from WindLab.core import remote_exec as rx
    from .vestas_serial import from_jsonable

    params = dict(opts, crs=crs_authid, routes=[(i, r.asWkt() if r else "", d.asWkt() if d else "") for i, r, d in pairs])
    fd, pfile = tempfile.mkstemp(prefix="wl_transport_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(params, f)
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker_transport.py")
    try:
        proc = rx.Popen([sys.executable, worker, "--params-file", pfile],
                        stdout=rx.PIPE, stderr=rx.PIPE, text=True, encoding="utf-8", errors="replace")
        for line in proc.stderr:                       # log na żywo: I|… W|… E|… P|nn
            kind, _, msg = line.rstrip("\r\n").partition("|")
            if kind == "P":
                feedback.setProgress(float(msg or 0))
            elif kind == "W":
                feedback.pushWarning(msg)
            elif kind == "E":
                feedback.reportError(msg)
            elif line.strip():
                feedback.pushInfo(msg if kind == "I" else line.rstrip())
        proc.wait()
        out, _ = proc.communicate()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(pfile)
    lines = [ln for ln in (out or "").splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"Brak wyniku symulacji z serwera (kod {proc.returncode})")
    data = json.loads(lines[-1])
    if data.get("status") != "ok":
        raise RuntimeError(data.get("message") or "Błąd symulacji na serwerze")

    def qdt(dt):
        return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute, dt.second))
    return from_jsonable(data["results"], QgsGeometry, qdt)


def run_simulation(route_layer, road_layer, model_name, use_active,
                    ds, n_dense, n_off, n_snap, smooth_r, force_w, feedback,
                    steer_gain=None, wheel_weight=None, local_offset=False,
                    path_mode=None, r_min_tractor=None):
    crs = route_layer.crs()
    if not validate_crs(crs, feedback):
        return None
    dlabel = "Active Steering" if use_active else "Fixed (pasywny)"
    try:                                     # wymiary łopaty – tabela Vestas (serwer / wersja lokalna)
        from .vestas_params import VESTAS_DB
        d = VESTAS_DB[model_name]
        blade_len = d["root_b"] + d["L"] + d["tip_o"]
    except ImportError:
        d, blade_len = None, None
    sg = steer_gain if steer_gain is not None else DEFAULT_STEER_GAIN
    ww = wheel_weight if wheel_weight is not None else DEFAULT_WHEEL_WEIGHT
    if use_active and not ACTIVE_DOLLY[model_name]:
        feedback.pushWarning(f"{model_name} nie obsluguje Active Steering -> Fixed dolly.")
        use_active = False
        dlabel = "Fixed (pasywny)"
    feedback.pushInfo("=" * 60)
    feedback.pushInfo(f"Model: {model_name}  |  Wozek: {dlabel}")
    if d is not None:
        feedback.pushInfo(f"Lopata: {blade_len:.1f}m (root_b={d['root_b']}m + L={d['L']}m + tip_o={d['tip_o']}m)")
    feedback.pushInfo(f"ds={ds}m | n_dense={n_dense} | offsets={n_off} | snaps={n_snap}")
    feedback.pushInfo(f"steer_gain={sg:.2f} | wheel_weight={ww:.0f} | local_offset={local_offset}")
    feedback.pushInfo("=" * 60)
    route_feats = list(route_layer.getFeatures())
    road_feats = list(road_layer.getFeatures())
    n_routes, n_roads = len(route_feats), len(road_feats)
    if n_routes == 0:
        feedback.reportError("Warstwa trasy jest pusta.")
        return None
    if n_roads == 0:
        feedback.reportError("Warstwa drogi jest pusta.")
        return None
    feedback.pushInfo(f"Znaleziono {n_routes} tras i {n_roads} poligonow drogi.")
    if n_roads == 1:
        road_geoms = [road_feats[0].geometry()] * n_routes
    elif n_roads == n_routes:
        road_geoms = [f.geometry() for f in road_feats]
        feedback.pushInfo("Parowanie tras i drog: 1:1")
    else:
        feedback.pushWarning(f"Liczba tras ({n_routes}) != liczba drog ({n_roads}). Uzywam pierwszego poligonu.")
        road_geoms = [road_feats[0].geometry()] * n_routes
    fl_defs = [("model", QVariant.String), ("offset_m", QVariant.Double),
               ("v_angle", QVariant.Double), ("hdg_chg", QVariant.Double),
               ("fits", QVariant.Int), ("dolly", QVariant.String), ("route_id", QVariant.Int)]
    fp_defs = [("layer", QVariant.String), ("area_m2", QVariant.Double),
               ("model", QVariant.String), ("v_angle", QVariant.Double),
               ("hdg_chg", QVariant.Double), ("dolly", QVariant.String), ("route_id", QVariant.Int)]
    fs_defs = [("snap_id", QVariant.Int), ("component", QVariant.String),
               ("dist_m", QVariant.Double), ("pct", QVariant.Double),
               ("model", QVariant.String), ("dolly", QVariant.String),
               ("t_start", QVariant.DateTime), ("t_end", QVariant.DateTime),
               ("route_id", QVariant.Int), ("alpha_deg", QVariant.Double),
               ("theta_deg", QVariant.Double), ("gamma_deg", QVariant.Double)]
    fa_defs = [("snap_id", QVariant.Int), ("dist_m", QVariant.Double),
               ("pct", QVariant.Double), ("model", QVariant.String),
               ("dolly", QVariant.String), ("t_start", QVariant.DateTime),
               ("t_end", QVariant.DateTime), ("route_id", QVariant.Int)]
    lyr_wheel = _make_layer("MultiLineString", "01 Wheel Path", crs, fl_defs)
    lyr_veh = _make_layer("MultiPolygon", "02 Vehicle Envelope", crs, fp_defs)
    lyr_dolly = _make_layer("MultiPolygon", "03 Dolly Envelope", crs, fp_defs)
    lyr_blade = _make_layer("MultiPolygon", "04 Blade Envelope", crs, fp_defs)
    lyr_overrun = _make_layer("MultiPolygon", "05 Over-run", crs, fp_defs)
    lyr_oversail = _make_layer("MultiPolygon", "06 Over-sail", crs, fp_defs)
    lyr_hardstand = _make_layer("MultiPolygon", "07 Hardstand", crs, fp_defs)
    lyr_snaps = _make_layer("MultiPolygon", "08 Snapshots - Temporal Animation", crs, fs_defs)
    lyr_axes = _make_layer("MultiLineString", "09 Snapshot Axes - Temporal Animation", crs, fa_defs)
    ft_defs = [("kind", QVariant.String), ("area_m2", QVariant.Double), ("v_angle", QVariant.Double),
               ("r1", QVariant.Double), ("r2", QVariant.Double), ("w3", QVariant.Double),
               ("w4", QVariant.Double), ("status", QVariant.String), ("route_id", QVariant.Int)]
    lyr_tpl = _make_layer("MultiPolygon", "14 Vestas – nawierzchnia nośna (szablon 0096-6291)", crs, ft_defs)
    lyr_swv = _make_layer("MultiPolygon", "15 Vestas – strefy przelotu (prześwit ≤ 15 cm)", crs, ft_defs)
    lyr_mis = _make_layer("MultiPolygon", "16 Vestas – brak nawierzchni nośnej", crs, ft_defs)
    pairs = []
    for idx, (route_feat, road_geom) in enumerate(zip(route_feats, road_geoms)):
        pairs.append((idx, _geom_to_single_line(route_feat.geometry()), road_geom))
    opts = dict(model_name=model_name, use_active=use_active, dlabel=dlabel, ds=ds, n_dense=n_dense,
                n_off=n_off, n_snap=n_snap, smooth_r=smooth_r, force_w=force_w, steer_gain=sg,
                wheel_weight=ww, local_offset=local_offset, path_mode=path_mode, r_min_tractor=r_min_tractor)
    from WindLab.core import remote_exec as rx
    if rx.is_remote():
        feedback.pushInfo("Symulacja na serwerze obliczeń…")
        all_results = _simulate_remote(pairs, opts, feedback, crs.authid())
        if blade_len is None and all_results:
            blade_len = all_results[0].get("blade_len_m")     # wymiary łopaty podaje serwer
    else:
        from .vestas_compute import simulate_routes   # silnik lokalny (wersja z obliczeniami w QGIS)
        all_results = simulate_routes(pairs, feedback=feedback, **opts)
    for result in all_results:
        idx = result["route_idx"]
        rid = idx + 1
        va = round(result["turn"]["vestas_angle"], 1)
        hc = round(result["turn"]["heading_change_deg"], 1)
        off = round(result["best"]["offset"], 2)
        fi = 1 if result["best"]["fits"] else 0

        def add_line(lyr, geom, _m=model_name, _o=off, _v=va, _h=hc, _f=fi, _d=dlabel, _r=rid):
            if not geom or geom.isEmpty():
                return
            g = geom.convertToType(QgsWkbTypes.GeometryType.LineGeometry, True)
            f = QgsFeature(lyr.fields())
            f.setAttributes([_m, _o, _v, _h, _f, _d, _r])
            f.setGeometry(g)
            lyr.dataProvider().addFeatures([f])

        def add_poly(lyr, geom, name, _m=model_name, _v=va, _h=hc, _d=dlabel, _r=rid):
            if not geom or geom.isEmpty():
                return
            g = geom.convertToType(QgsWkbTypes.GeometryType.PolygonGeometry, True)
            f = QgsFeature(lyr.fields())
            f.setAttributes([name, round(g.area(), 1), _m, _v, _h, _d, _r])
            f.setGeometry(g)
            lyr.dataProvider().addFeatures([f])
            feedback.pushInfo(f"  {name:<42} {g.area():.0f} m2  [trasa #{_r}]")

        add_line(lyr_wheel, result["wheel"])
        add_poly(lyr_veh, result["vehicle"], f"02 Vehicle Envelope (tractor+nasada {BLADE_ROOT_ENV_M}m)")
        add_poly(lyr_dolly, result["dolly"], "03 Dolly Envelope")
        add_poly(lyr_blade, result["blade"], "04 Blade Envelope")
        add_poly(lyr_overrun, result["overrun"], "05 Over-run")
        add_poly(lyr_oversail, result["oversail"], "06 Over-sail")
        add_poly(lyr_hardstand, result["hardstand"], "07 Hardstand")
        snap_feats, axis_feats = [], []
        for s in result["snapshots"]:
            sid, dm, pct = s["snap_id"], s["dist_m"], s["pct"]
            t_start, t_end = s["t_start"], s["t_end"]
            a_deg = round(math.degrees(s["alpha"]), 2) if s.get("alpha") is not None else None
            t_deg = round(math.degrees(s["theta"]), 2) if s.get("theta") is not None else None
            g_deg = round(math.degrees(s["gamma"]), 2) if s.get("gamma") is not None else None
            for comp in ("tractor", "blade", "dolly"):
                g_raw = s.get(comp)
                if g_raw is None or g_raw.isEmpty():
                    continue
                g = g_raw.convertToType(QgsWkbTypes.GeometryType.PolygonGeometry, True)
                if g.isEmpty():
                    continue
                f = QgsFeature(lyr_snaps.fields())
                f.setAttributes([sid, comp, dm, pct, model_name, dlabel, t_start, t_end, rid, a_deg, t_deg, g_deg])
                f.setGeometry(g)
                snap_feats.append(f)
            ax = s.get("blade_axis")
            if ax and not ax.isEmpty():
                g2 = ax.convertToType(QgsWkbTypes.GeometryType.LineGeometry, True)
                f2 = QgsFeature(lyr_axes.fields())
                f2.setAttributes([sid, dm, pct, model_name, dlabel, t_start, t_end, rid])
                f2.setGeometry(g2)
                axis_feats.append(f2)
        if snap_feats:
            lyr_snaps.dataProvider().addFeatures(snap_feats)
        if axis_feats:
            lyr_axes.dataProvider().addFeatures(axis_feats)
        feedback.pushInfo(f"  Snapshots: {len(result['snapshots'])} klatek  [trasa #{rid}]")
        vs = result.get("vestas")
        if vs:
            p_, ck = vs["tpl"]["params"], vs["check"]
            st_ = "ZGODNA" if ck["compliant"] else "NIEZGODNA"

            def add_tpl(lyr, geom, kind, _p=p_, _a=round(vs["tpl"]["angle"], 1), _s=st_, _r=rid):
                if not geom or geom.isEmpty():
                    return
                g = geom.convertToType(QgsWkbTypes.GeometryType.PolygonGeometry, True)
                f = QgsFeature(lyr.fields())
                f.setAttributes([kind, round(g.area(), 1), _a, _p["r1"], _p["r2"], _p["w3"], _p["w4"], _s, _r])
                f.setGeometry(g)
                lyr.dataProvider().addFeatures([f])

            add_tpl(lyr_tpl, vs["tpl"]["bearing"], "nawierzchnia nośna")
            add_tpl(lyr_swv, vs["tpl"]["swivel_in"], "przelot – wewnątrz (w3)")
            add_tpl(lyr_swv, vs["tpl"]["swivel_out"], "przelot – zewnątrz (w4)")
            add_tpl(lyr_mis, ck["missing"], "brak nawierzchni nośnej")
    if not all_results:
        feedback.reportError("Zadna trasa nie dala wynikow.")
        return None
    for lyr in (lyr_wheel, lyr_veh, lyr_dolly, lyr_blade, lyr_overrun, lyr_oversail, lyr_hardstand, lyr_snaps, lyr_axes,
                lyr_tpl, lyr_swv, lyr_mis):
        lyr.updateExtents()
    _style_template_layers(lyr_tpl, lyr_swv, lyr_mis)
    _enable_temporal(lyr_snaps)
    _enable_temporal(lyr_axes)
    feedback.setProgress(100)
    feedback.pushInfo(f"\n{'=' * 60}\nRAPORT ZBIORCZY - Vestas {model_name}\n{'=' * 60}")
    feedback.pushInfo(f"Tras przetworzonych: {len(all_results)} / {n_routes}")
    feedback.pushInfo(f"Tryb wozka: {dlabel} | Lopata: {blade_len:.1f} m")
    feedback.pushInfo(f"Snapshots lacznie: {sum(r['n_snaps'] for r in all_results)}")
    for r in all_results:
        rid = r["route_idx"] + 1
        va = round(r["turn"]["vestas_angle"], 1)
        off = round(r["best"]["offset"], 2)
        fits = "TAK" if r["best"]["fits"] else "NIE"
        orn = r["best"]["overrun_area"]
        hs = r["hardstand"]
        hs_a = f"{hs.area():.0f} m2" if hs and not hs.isEmpty() else "brak"
        feedback.pushInfo(f"  Trasa #{rid}: kat={va} | offset={off}m | fits={fits} | overrun={orn:.0f}m2 | hardstand={hs_a}")
    feedback.pushInfo("Animacja: View -> Panels -> Temporal Controller, krok 1 s, Play.")
    feedback.pushInfo("=" * 60)
    return {"wheel": lyr_wheel, "vehicle": lyr_veh, "dolly": lyr_dolly, "blade": lyr_blade,
            "overrun": lyr_overrun, "oversail": lyr_oversail, "hardstand": lyr_hardstand,
            "snapshots": lyr_snaps, "axes": lyr_axes,
            "tpl_bearing": lyr_tpl, "tpl_swivel": lyr_swv, "tpl_missing": lyr_mis,
            "results": all_results, "model": model_name, "dolly_mode": dlabel, "blade_len": blade_len}
