# -*- coding: utf-8 -*-
"""
Most QGIS ↔ silnik.

Odpowiedzialności:
- odczyt warstw QGIS → SourcePoint / ReceiverPoint (prymitywy)
- sampling DEM
- estymacja G z CORINE
- zapis wyników z powrotem do warstwy QGIS
"""
from __future__ import annotations

from typing import List, Optional

from qgis.core import (
    QgsVectorLayer, QgsRasterLayer, QgsPointXY, QgsGeometry,
    QgsFeature, QgsField, QgsFields, QgsProject, QgsWkbTypes,
    QgsFeatureRequest,
)
from qgis.PyQt.QtCore import QVariant

from ..engine import SourcePoint, ReceiverPoint, RunResult
import contextlib


CORINE_G = {
    111: 0.0, 112: 0.1, 121: 0.0, 122: 0.0, 123: 0.0, 124: 0.0,
    131: 0.2, 132: 0.2, 133: 0.2, 141: 0.4, 142: 0.3,
    211: 0.7, 212: 0.7, 213: 0.6, 221: 0.6, 222: 0.6, 223: 0.5,
    231: 0.8, 241: 0.7, 242: 0.7, 243: 0.7, 244: 0.8,
    311: 1.0, 312: 1.0, 313: 1.0, 321: 0.9, 322: 0.9, 323: 0.8, 324: 0.9,
    331: 0.3, 332: 0.2, 333: 0.4, 334: 0.3, 335: 0.1,
    411: 0.5, 412: 0.5, 421: 0.4, 422: 0.4, 423: 0.3,
    511: 0.0, 512: 0.0, 521: 0.0, 522: 0.0, 523: 0.0,
}


def sample_dem(dem: Optional[QgsRasterLayer], x: float, y: float) -> Optional[float]:
    if dem is None or not dem.isValid():
        return None
    with contextlib.suppress(Exception):
        val, ok = dem.dataProvider().sample(QgsPointXY(x, y), 1)
        if ok and val is not None and val == val:
            return float(val)
    return None


def fill_z_gugik(points, crs_authid: str, log=None) -> None:
    """Uzupełnij z_ground (SourcePoint / ReceiverPoint) wysokościami z usługi GUGiK NMT."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform
    from WindLab.core.elevation import gugik_heights
    if not points:
        return
    xy = [(p.x, p.y) for p in points]
    if (crs_authid or "EPSG:2180").upper() != "EPSG:2180":
        tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem(crs_authid),
                                    QgsCoordinateReferenceSystem("EPSG:2180"),
                                    QgsProject.instance())
        xy = [(q.x(), q.y()) for q in (tr.transform(QgsPointXY(x, y)) for x, y in xy)]
    z = gugik_heights(xy)
    for p, v in zip(points, z):
        p.z_ground = v
    if log:
        log(f"GUGiK NMT: {sum(v is not None for v in z)}/{len(z)} punktów z wysokością")


def _feature_label(layer: QgsVectorLayer, feat) -> str:
    fields_lower = {f.name().lower(): f.name() for f in layer.fields()}
    for cand in ("userlabel", "user_label", "name", "nazwa", "label", "id"):
        if cand in fields_lower:
            with contextlib.suppress(Exception):
                v = feat.attribute(fields_lower[cand])
                if v is not None and str(v).strip():
                    return str(v).strip()
    return ""


def build_sources(
    layer: QgsVectorLayer,
    wtg_path: str,
    curve_label: Optional[str] = None,
    *,
    hub_height_field: Optional[str] = None,
    global_hub_height: Optional[float] = None,
    rule: str = "loudest95",
    wind_speed: float = 10.0,
    ws_ref: str = "10m",
    dem: Optional[QgsRasterLayer] = None,
    log=None,
    remote: bool = False,
) -> List[SourcePoint]:
    """Źródła z warstwy punktowej; dane akustyczne z pliku turbiny .wtg (jeden typ dla wszystkich).

    Dla każdej wysokości piasty pobiera LwA i widmo zgodnie z regułą (domyślnie WindPRO
    „Loudest up to 95% rated power”, prędkość na 10 m).
    remote=True – plik .wtg jest na serwerze: dane akustyczne uzupełni worker_noise.py.
    """
    if not wtg_path:
        raise ValueError("Nie wybrano pliku turbiny (.wtg)")
    cache = {}
    out: List[SourcePoint] = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.GeometryType.PointGeometry:
            pt = geom.asPoint()
        else:
            pt = geom.centroid().asPoint()

        hh = None
        if global_hub_height is not None and global_hub_height > 0:
            hh = float(global_hub_height)
        elif hub_height_field:
            try:
                hh = float(feat.attribute(hub_height_field))
            except (TypeError, ValueError):
                hh = None
        if not hh or hh <= 0:
            raise ValueError(
                f"Brak wysokości piasty dla obiektu {feat.id()} – wybierz pole hub_height "
                f"albo ustaw globalną wysokość piasty")

        if remote:
            out.append(SourcePoint(
                id=feat.id(), x=pt.x(), y=pt.y(), hub_height=hh, model_name="", lwa=0.0,
                z_ground=sample_dem(dem, pt.x(), pt.y()), name=_feature_label(layer, feat),
                wtg_path=wtg_path, curve_label=curve_label or "",
            ))
            continue
        key = round(hh, 1)
        if key not in cache:
            from ..engine import source_noise   # lokalny .wtg (wersja z obliczeniami w QGIS)
            data = source_noise(wtg_path, curve_label, hh, rule=rule, wind_speed=wind_speed, ref=ws_ref)
            cache[key] = data
            if log:
                extra = ("; " + "; ".join(data["notes"])) if data["notes"] else ""
                log(f"HH {hh:.0f} m: {data['model_label']} · LwA {data['lwa']:.1f} dB(A) "
                    f"przy {data['ws']:.1f} m/s ({data['ref']}){extra}")
        data = cache[key]
        out.append(SourcePoint(
            id=feat.id(),
            x=pt.x(), y=pt.y(),
            hub_height=hh,
            model_name=data["model_label"],
            lwa=data["lwa"],
            z_ground=sample_dem(dem, pt.x(), pt.y()),
            lw_octave=data["octaves"],
            name=_feature_label(layer, feat),
            wtg_path=wtg_path,
            curve_label=data["curve_label"],
            noise_ws=data["ws"],
            noise_ref=data["ref"],
            noise_hub_height=data["hub_height_data"],
            tonal=data["tonal"],
        ))
    return out


def build_receivers(
    layer: QgsVectorLayer,
    *,
    receiver_height: float = 4.0,
    name_field: Optional[str] = None,
    limit_field: Optional[str] = None,
    limit_dba: Optional[float] = None,
    dem: Optional[QgsRasterLayer] = None,
) -> List[ReceiverPoint]:
    """
    limit_field – opcjonalne pole atrybutowe z limitem per receptor
    limit_dba   – globalny limit (z GUI), używany gdy brak/nieczytelne pole
    """
    out: List[ReceiverPoint] = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.GeometryType.PointGeometry:
            pt = geom.asPoint()
        else:
            pt = geom.centroid().asPoint()

        # nazwy raportowe: R1, R2, … (kolejność obiektów w warstwie)
        name = f"R{len(out) + 1}"
        limit = None
        if limit_field:
            with contextlib.suppress((TypeError, ValueError)):
                limit = float(feat.attribute(limit_field))
        if limit is None and limit_dba is not None:
            limit = float(limit_dba)

        out.append(ReceiverPoint(
            id=feat.id(),
            x=pt.x(), y=pt.y(),
            receiver_height=receiver_height,
            z_ground=sample_dem(dem, pt.x(), pt.y()),
            name=name,
            limit_dba=limit,
        ))
    return out


def estimate_g_corine(
    landuse: Optional[QgsVectorLayer],
    sources: List[SourcePoint],
    receivers: List[ReceiverPoint],
    code_field: str = "Code_18",
    max_pairs: int = 25,
    fallback: float = 0.5,
) -> float:
    """Średnie G z CORINE wzdłuż ścieżek (próbka par źródło–receptor); fallback gdy brak danych."""
    if landuse is None or not landuse.isValid():
        return fallback
    gs = []
    n = 0
    for src in sources:
        for rec in receivers:
            if n >= max_pairs:
                break
            for i in range(5):
                t = i / 4.0
                x = src.x + t * (rec.x - src.x)
                y = src.y + t * (rec.y - src.y)
                pt = QgsGeometry.fromPointXY(QgsPointXY(x, y))
                req = QgsFeatureRequest().setFilterRect(pt.boundingBox())
                for feat in landuse.getFeatures(req):
                    if feat.geometry().contains(pt):
                        try:
                            code = int(feat.attribute(code_field))
                        except (TypeError, ValueError):
                            code = None
                        gs.append(CORINE_G.get(code, fallback) if code is not None else fallback)
                        break
            n += 1
        if n >= max_pairs:
            break
    return sum(gs) / len(gs) if gs else fallback


def apply_results(
    result: RunResult,
    crs_authid: str = "EPSG:2180",
    layer_name: str = "Noise · Receivers",
) -> QgsVectorLayer:
    uri = f"Point?crs={crs_authid}"
    vl = QgsVectorLayer(uri, layer_name, "memory")
    pr = vl.dataProvider()

    fields = QgsFields()
    for name, typ in [
        ("receiver_id", QVariant.Int),
        ("name", QVariant.String),
        ("noise_dba", QVariant.Double),
        ("limit_dba", QVariant.Double),
        ("margin_dba", QVariant.Double),
        ("exceeds", QVariant.Int),
        ("n_sources", QVariant.Int),
        ("dom_src_id", QVariant.Int),
        ("dom_model", QVariant.String),
        ("dom_lp", QVariant.Double),
    ]:
        fields.append(QgsField(name, typ))
    pr.addAttributes(fields)
    vl.updateFields()

    feats = []
    for r in result.results:
        f = QgsFeature(vl.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.x, r.y)))
        f.setAttributes([
            r.receiver_id, r.receiver_name,
            r.noise_dba, r.limit_dba, r.margin_dba,
            1 if r.exceeds else 0, r.n_sources,
            r.dominant_source_id, r.dominant_model or "", r.dominant_lp,
        ])
        feats.append(f)
    pr.addFeatures(feats)
    vl.updateExtents()
    QgsProject.instance().addMapLayer(vl)
    return vl


def apply_noise_grid_and_isolines(
    result: "RunResult",
    crs_authid: str = "EPSG:2180",
    raster_name: str = "Noise · Map",
    iso_name: str = "Noise · Isolines",
    output_dir: Optional[str] = None,
    write_raster: bool = True,
    write_isolines: bool = True,
) -> tuple:
    """
    Z NoiseGrid tworzy raster i/lub izolinie.
    Pliki zapisuje w output_dir (domyślnie temp).
    Zwraca (raster_layer, isoline_layer, isolines_geojson_path).
    """
    import os
    import json
    import tempfile
    import numpy as np

    grid = result.noise_grid
    if grid is None:
        return None, None, None

    arr = np.full((grid.nrows, grid.ncols), -9999.0, dtype=np.float32)
    for r in range(grid.nrows):
        for c in range(grid.ncols):
            v = grid.values[r][c]
            if v is not None:
                arr[r, c] = float(v)

    try:
        from osgeo import gdal, osr, ogr
    except ImportError:
        return None, None, None

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        outdir = output_dir
    else:
        outdir = tempfile.mkdtemp(prefix="windlab_noise_")

    tif_path = os.path.join(outdir, "noise_map.tif")
    raster_layer = None
    iso_layer = None
    geojson_path = None

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(tif_path, grid.ncols, grid.nrows, 1, gdal.GDT_Float32)
    gt = (grid.xmin, grid.resolution_m, 0.0, grid.ymax, 0.0, -grid.resolution_m)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs_authid)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(-9999.0)
    band.FlushCache()
    ds = None

    if write_raster:
        raster_layer = QgsRasterLayer(tif_path, raster_name)
        if raster_layer.isValid():
            QgsProject.instance().addMapLayer(raster_layer)
        else:
            raster_layer = None

    levels = list(grid.iso_levels or [35, 40, 45, 50, 55])
    if write_isolines:
        try:
            shp_path = os.path.join(outdir, "isolines.shp")
            src_ds = gdal.Open(tif_path)
            src_band = src_ds.GetRasterBand(1)
            drv = ogr.GetDriverByName("ESRI Shapefile")
            if os.path.exists(shp_path):
                drv.DeleteDataSource(shp_path)
            dst_ds = drv.CreateDataSource(shp_path)
            dst_layer = dst_ds.CreateLayer("isolines", srs, ogr.wkbLineString)
            field_def = ogr.FieldDefn("level", ogr.OFTReal)
            dst_layer.CreateField(field_def)
            gdal.ContourGenerate(
                src_band, 0, 0, levels, 0, -9999.0, dst_layer, 0, 0,
            )
            # GeoJSON dla raportu (poziomy 40 i 45)
            features = []
            dst_layer.ResetReading()
            for feat in dst_layer:
                lvl = feat.GetField("level")
                if lvl is None:
                    continue
                geom = feat.GetGeometryRef()
                if geom is None:
                    continue
                # MultiLineString or LineString -> list of rings
                gjson = json.loads(geom.ExportToJson())
                features.append({"level": float(lvl), "geometry": gjson})
            dst_ds = None
            src_ds = None

            geojson_path = os.path.join(outdir, "isolines_for_report.json")
            with open(geojson_path, "w", encoding="utf-8") as f:
                json.dump({"crs": crs_authid, "features": features}, f)

            vl = QgsVectorLayer(shp_path, iso_name, "ogr")
            if vl.isValid():
                QgsProject.instance().addMapLayer(vl)
                iso_layer = vl
        except Exception:
            iso_layer = None
            geojson_path = None

    # jeśli nie chcemy rastra na dysku na stałe poza report – i tak zostaje tif
    if not write_raster and not write_isolines:
        pass

    return raster_layer, iso_layer, geojson_path
