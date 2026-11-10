"""

NMT_Downloader_Q_GUI.py

GUI launcher do pobierania NMT (GUGiK / geoportal.gov.pl) dla warstwy punktowej.

Uruchom w edytorze Python QGIS.



- Warstwa punktowa z projektu lub plik SHP

- Punkty w tej samej siatce / arkuszu → plik pobierany tylko raz

- Układ wysokości: EVRF2007 (1 m + siatka 5 m) lub KRON86

- Format: ARC/INFO ASCII GRID / ASCII XYZ / wszystkie

- Opcja: tylko najnowsze arkusze

"""



import os

import re

import json

import urllib.parse

import urllib.request

import ssl

from pathlib import Path

from collections import OrderedDict



from qgis.core import (

    QgsCoordinateTransform,

    QgsCoordinateReferenceSystem,

    QgsProject,

    QgsVectorLayer,

    QgsWkbTypes,

    QgsPointXY,

    QgsTask,

    QgsApplication,

)

from qgis.PyQt.QtWidgets import (

    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,

    QLabel, QComboBox, QCheckBox,

    QPushButton, QGroupBox, QTextEdit, QProgressBar,

    QLineEdit, QFileDialog, QMessageBox, QWidget, QRadioButton,

    QButtonGroup, QSpinBox,

)

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject

from qgis.PyQt.QtGui import QFont
from WindLab.core.ui.widgets import open_path
import contextlib



# ── Konfiguracja GUGiK ───────────────────────────────────────────────────────

NMT_EVRF_WMS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/WMS/SkorowidzeUkladEVRF2007?"

NMT_GRID5M_WMS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/WMS/SheetsGrid5mEVRF2007?"

NMT_KRON86_WMS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/WMS/SkorowidzeUkladKRON86?"



CRS_PUWG1992 = "EPSG:2180"

WMS_GFI_PARAMS = {

    "SERVICE": "WMS",

    "request": "GetFeatureInfo",

    "version": "1.3.0",

    "styles": "",

    "crs": CRS_PUWG1992,

    "width": "101",

    "height": "101",

    "format": "image/png",

    "transparent": "true",

    "i": "50",

    "j": "50",

    "INFO_FORMAT": "text/html",

}



# Katalog roboczy – w edytorze QGIS __file__ bywa niedostępne

try:

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

except NameError:

    SCRIPT_DIR = os.path.expanduser("~")

DEFAULT_OUTPUT = os.path.join(SCRIPT_DIR, "dane_nmt")



_CURLY_RE = re.compile(r"\{[^{}]+\}")





# ── HTTP helpers (bez zależności od wtyczki) ──────────────────────────────────

def _http_get(url, params=None, timeout=30):

    """GET → (ok: bool, text_or_error: str)."""

    if params:

        qs = urllib.parse.urlencode(params, doseq=True)

        sep = "&" if ("?" in url and not url.endswith("?")) else ""

        if url.endswith("?"):

            full = url + qs

        else:

            full = url + ("?" if "?" not in url else sep) + qs

    else:

        full = url



    ctx = ssl.create_default_context()

    req = urllib.request.Request(

        full,

        headers={

            "User-Agent": "Mozilla/5.0 (QGIS NMT Downloader)",

            "Accept": "*/*",

        },

    )

    try:

        if not req.full_url.lower().startswith(("https://", "http://")):
            raise ValueError(f"Nieobsługiwany adres: {req.full_url}")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310 – tylko http(s), sprawdzone wyżej

            data = resp.read()

            charset = "utf-8"

            ctype = resp.headers.get_content_charset()

            if ctype:

                charset = ctype

            return True, data.decode(charset, errors="replace")

    except Exception as e:

        return False, str(e)





def _http_download(url, dest_path, timeout=120, log_cb=None):

    """Pobiera plik na dysk. Zwraca (ok, msg)."""

    ctx = ssl.create_default_context()

    req = urllib.request.Request(

        url,

        headers={"User-Agent": "Mozilla/5.0 (QGIS NMT Downloader)"},

    )

    try:

        if not req.full_url.lower().startswith(("https://", "http://")):
            raise ValueError(f"Nieobsługiwany adres: {req.full_url}")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310 – tylko http(s), sprawdzone wyżej

            total = resp.headers.get("Content-Length")

            total = int(total) if total and total.isdigit() else None

            written = 0

            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

            with open(dest_path, "wb") as f:

                while True:

                    chunk = resp.read(1024 * 256)

                    if not chunk:

                        break

                    f.write(chunk)

                    written += len(chunk)

            if written == 0:

                with contextlib.suppress(OSError):

                    os.remove(dest_path)


                return False, "Pusty plik"

            return True, f"{written} B"

    except Exception as e:

        if os.path.isfile(dest_path):

            with contextlib.suppress(OSError):

                os.remove(dest_path)


        return False, str(e)





def _get_wms_layers(wms_url):

    """Lista warstw queryable z GetCapabilities."""

    ok, body = _http_get(wms_url, {"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"})

    if not ok:

        return []

    # proste wyciągnięcie <Name> wewnątrz Layer queryable="1"

    layers = []

    for m in re.finditer(

        r'<Layer[^>]*queryable=["\']1["\'][^>]*>.*?<Name>([^<]+)</Name>',

        body,

        re.DOTALL | re.IGNORECASE,

    ):

        layers.append(m.group(1).strip())

    if not layers:

        # fallback: wszystkie Name pod Layer

        for m in re.finditer(r"<Name>([^<]+)</Name>", body):

            name = m.group(1).strip()

            if name and name.lower() not in ("wms", "layers"):

                layers.append(name)

    # unikalne, zachowaj kolejność

    seen = set()

    out = []

    for n in layers:

        if n not in seen:

            seen.add(n)

            out.append(n)

    return out





def _parse_gfi_objects(html_text):

    """Parsuje obiekty { "k": "v", ... } z odpowiedzi GetFeatureInfo (text/html)."""

    objects = []

    for block in _CURLY_RE.findall(html_text or ""):

        inner = block.strip("{}")

        attrs = {}

        # pary key:value rozdzielone przecinkami – wartości mogą zawierać ':' (URL)

        parts = []

        buf = []

        in_quotes = False

        for ch in inner:

            if ch == '"':

                in_quotes = not in_quotes

                buf.append(ch)

            elif ch == "," and not in_quotes:

                parts.append("".join(buf).strip())

                buf = []

            else:

                buf.append(ch)

        if buf:

            parts.append("".join(buf).strip())

        for part in parts:

            if ":" not in part:

                continue

            key, val = part.split(":", 1)

            key = key.strip().strip('"')

            val = val.strip().strip('"')

            if key:

                attrs[key] = val

        if attrs:

            objects.append(attrs)

    return objects





def get_nmt_for_point_1992(point_xy, is_evrf2007=True):

    """

    Dla punktu w EPSG:2180 zwraca listę dict z metadanymi NMT (m.in. url, godlo, format).

    """

    x, y = point_xy.x(), point_xy.y()

    bbox = f"{y - 50},{x - 50},{y + 50},{x + 50}"  # WMS 1.3.0, EPSG:2180 → axis order Y,X



    urls = []

    if is_evrf2007:

        urls = [NMT_EVRF_WMS_URL, NMT_GRID5M_WMS_URL]

    else:

        urls = [NMT_KRON86_WMS_URL]



    results = []

    for wms_url in urls:

        layers = _get_wms_layers(wms_url)

        if not layers:

            continue

        params = dict(WMS_GFI_PARAMS)

        params["layers"] = ",".join(layers)

        params["query_layers"] = ",".join(layers)

        params["bbox"] = bbox

        ok, body = _http_get(wms_url, params)

        if not ok:

            continue

        results.extend(_parse_gfi_objects(body))

    return results





def _file_name_from_url(url):

    """Nazwa pliku z URL (jak w wtyczce)."""

    path = urllib.parse.urlparse(url).path

    name = os.path.basename(path) or "nmt_file"

    if "?" in name:

        name = name.split("?")[0]

    if not name or name == "/":

        name = urllib.parse.quote(url, safe="")[-80:] + ".zip"

    return name





def _dedupe_nmt_list(items, prefer_newest=False):

    """

    Unikalne arkusze: klucz = url, a gdy brak url → godlo+format.

    prefer_newest: zostaw najnowszy po 'aktualnosc' dla tego samego godło+format.

    """

    if prefer_newest:

        by_key = {}

        for it in items:

            godlo = it.get("godlo") or ""

            fmt = it.get("format") or ""

            key = (godlo, fmt) if godlo else (it.get("url") or id(it),)

            prev = by_key.get(key)

            if prev is None:

                by_key[key] = it

            else:

                a = str(it.get("aktualnosc") or "")

                b = str(prev.get("aktualnosc") or "")

                if a >= b:

                    by_key[key] = it

        items = list(by_key.values())



    seen = set()

    out = []

    for it in items:

        url = it.get("url") or ""

        key = url if url else (it.get("godlo"), it.get("format"), it.get("aktualnosc"))

        if key in seen:

            continue

        seen.add(key)

        out.append(it)

    return out





# ── Worker wątku ─────────────────────────────────────────────────────────────

class DownloadWorker(QObject):

    log = pyqtSignal(str)

    progress = pyqtSignal(int, int)  # current, total

    finished = pyqtSignal(dict)

    error = pyqtSignal(str)



    def __init__(self, points_2180, out_dir, is_evrf, formats, only_newest, timeout=120):

        super().__init__()

        self.points_2180 = points_2180  # list of QgsPointXY

        self.out_dir = out_dir

        self.is_evrf = is_evrf

        self.formats = formats  # set of allowed format strings or None = all

        self.only_newest = only_newest

        self.timeout = timeout

        self._stopped = False



    def stop(self):

        self._stopped = True



    def run(self):

        try:

            os.makedirs(self.out_dir, exist_ok=True)

            self.log.emit(f"Punkty: {len(self.points_2180)}  →  katalog: {self.out_dir}")

            self.log.emit(f"Układ wysokości: {'EVRF2007 (1m + 5m)' if self.is_evrf else 'KRON86'}")



            all_items = []

            for i, pt in enumerate(self.points_2180):

                if self._stopped:

                    self.finished.emit({"status": "cancelled"})

                    return

                self.log.emit(

                    f"[{i + 1}/{len(self.points_2180)}] zapytanie skorowidza "

                    f"({pt.x():.2f}, {pt.y():.2f})"

                )

                found = get_nmt_for_point_1992(pt, is_evrf2007=self.is_evrf)

                if not found:

                    self.log.emit("  → brak danych NMT dla punktu")

                else:

                    self.log.emit(f"  → znaleziono {len(found)} rekord(ów)")

                    all_items.extend(found)



            if self._stopped:

                self.finished.emit({"status": "cancelled"})

                return



            # filtr formatu

            if self.formats:

                before = len(all_items)

                all_items = [x for x in all_items if x.get("format") in self.formats]

                self.log.emit(f"Filtr formatu: {before} → {len(all_items)}")



            unique = _dedupe_nmt_list(all_items, prefer_newest=self.only_newest)

            self.log.emit(f"Unikalne arkusze do pobrania: {len(unique)}")



            if not unique:

                self.finished.emit({

                    "status": "ok",

                    "downloaded": 0,

                    "skipped": 0,

                    "errors": 0,

                    "total": 0,

                    "message": "Brak arkuszy spełniających kryteria",

                })

                return



            ok_n = skip_n = err_n = 0

            for idx, item in enumerate(unique):

                if self._stopped:

                    self.finished.emit({

                        "status": "cancelled",

                        "downloaded": ok_n,

                        "skipped": skip_n,

                        "errors": err_n,

                    })

                    return

                self.progress.emit(idx + 1, len(unique))

                url = item.get("url")

                if not url:

                    self.log.emit(f"  pominięto (brak url): godło={item.get('godlo')}")

                    err_n += 1

                    continue

                fname = _file_name_from_url(url)

                dest = os.path.join(self.out_dir, fname)

                if os.path.isfile(dest) and os.path.getsize(dest) > 0:

                    self.log.emit(f"  istnieje: {fname}")

                    skip_n += 1

                    continue

                self.log.emit(f"  pobieram: {fname}")

                self.log.emit(f"    godło={item.get('godlo')}  format={item.get('format')}  "

                              f"aktualność={item.get('aktualnosc')}")

                success, msg = _http_download(url, dest, timeout=self.timeout)

                if success:

                    self.log.emit(f"    ✓ {msg}")

                    ok_n += 1

                else:

                    self.log.emit(f"    ✗ {msg}")

                    err_n += 1



            # raport JSON

            report_path = os.path.join(self.out_dir, "nmt_download_report.json")

            with contextlib.suppress(OSError):

                with open(report_path, "w", encoding="utf-8") as f:

                    json.dump(

                        {

                            "items": unique,

                            "downloaded": ok_n,

                            "skipped_existing": skip_n,

                            "errors": err_n,

                        },

                        f,

                        ensure_ascii=False,

                        indent=2,

                    )

                self.log.emit(f"Raport: {report_path}")




            self.finished.emit({

                "status": "ok",

                "downloaded": ok_n,

                "skipped": skip_n,

                "errors": err_n,

                "total": len(unique),

                "output_dir": self.out_dir,

            })

        except Exception as e:

            if self._stopped:

                self.finished.emit({"status": "cancelled"})

            else:

                self.error.emit(str(e))





# ── Główne okno ──────────────────────────────────────────────────────────────

class NMTDataDialog(QDialog):



    def __init__(self, parent=None):

        super().__init__(parent)

        self.points_wgs = []   # list of (lon, lat) – tylko do podglądu

        self.points_2180 = []  # list of QgsPointXY w EPSG:2180

        self._thread = None

        self._worker = None

        self._last_shp = None



        self.setWindowTitle("NMT GUGiK – pobieranie dla warstwy punktowej")

        self.setMinimumWidth(620)

        self.setMinimumHeight(640)

        self._build_ui()

        self._refresh_layers()



    def _build_ui(self):

        root = QVBoxLayout(self)

        root.setSpacing(8)



        # ── Warstwa / SHP ────────────────────────────────────────────────────

        grp_layer = QGroupBox("Punkty (turbiny / lokalizacje)")

        gl = QVBoxLayout(grp_layer)



        row1 = QHBoxLayout()

        row1.addWidget(QLabel("Warstwa:"))

        self.combo_layer = QComboBox()

        self.combo_layer.setSizeAdjustPolicy(

            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon

        )

        self.combo_layer.currentIndexChanged.connect(self._on_layer_changed)

        row1.addWidget(self.combo_layer, 1)

        btn_refresh = QPushButton("Odśwież")

        btn_refresh.setFixedWidth(70)

        btn_refresh.clicked.connect(self._refresh_layers)

        row1.addWidget(btn_refresh)

        gl.addLayout(row1)



        row2 = QHBoxLayout()

        row2.addWidget(QLabel("SHP:"))

        self.edit_shp = QLineEdit("")

        self.edit_shp.setPlaceholderText(r"opcjonalnie: plik .shp z dysku")

        row2.addWidget(self.edit_shp, 1)

        btn_shp = QPushButton("…")

        btn_shp.setFixedWidth(30)

        btn_shp.clicked.connect(self._browse_shp)

        row2.addWidget(btn_shp)

        gl.addLayout(row2)



        root.addWidget(grp_layer)



        self.lbl_header = QLabel("Punktów: 0")

        self.lbl_header.setStyleSheet(

            "padding: 6px; background:#e3f2fd; border-radius:4px;"

        )

        root.addWidget(self.lbl_header)



        # ── Katalog ──────────────────────────────────────────────────────────

        grp_out = QGroupBox("Katalog wynikowy")

        go = QHBoxLayout(grp_out)

        self.edit_outdir = QLineEdit(DEFAULT_OUTPUT)

        go.addWidget(self.edit_outdir)

        btn_out = QPushButton("…")

        btn_out.setFixedWidth(30)

        btn_out.clicked.connect(self._browse_outdir)

        go.addWidget(btn_out)

        root.addWidget(grp_out)



        # ── Układ wysokości ──────────────────────────────────────────────────

        grp_h = QGroupBox("Układ wysokości (skorowidz)")

        gh = QHBoxLayout(grp_h)

        self.rb_evrf = QRadioButton("EVRF2007 (1 m + siatka 5 m)")

        self.rb_kron = QRadioButton("KRON86-NH")

        self.rb_evrf.setChecked(True)

        bg_h = QButtonGroup(self)

        bg_h.addButton(self.rb_evrf)

        bg_h.addButton(self.rb_kron)

        gh.addWidget(self.rb_evrf)

        gh.addWidget(self.rb_kron)

        gh.addStretch()

        root.addWidget(grp_h)



        # ── Format ───────────────────────────────────────────────────────────

        grp_f = QGroupBox("Format pliku")

        gf = QHBoxLayout(grp_f)

        self.cb_arc = QCheckBox("ARC/INFO ASCII GRID")

        self.cb_xyz = QCheckBox("ASCII XYZ GRID")

        self.cb_arc.setChecked(True)

        self.cb_xyz.setChecked(False)

        gf.addWidget(self.cb_arc)

        gf.addWidget(self.cb_xyz)

        gf.addWidget(QLabel("(odznacz oba = wszystkie formaty)"))

        gf.addStretch()

        root.addWidget(grp_f)



        # ── Opcje ────────────────────────────────────────────────────────────

        grp_o = QGroupBox("Opcje")

        go2 = QHBoxLayout(grp_o)

        self.cb_newest = QCheckBox("Tylko najnowszy arkusz")

        self.cb_newest.setChecked(True)

        go2.addWidget(self.cb_newest)

        go2.addStretch()

        root.addWidget(grp_o)



        # ── Pobierz ──────────────────────────────────────────────────────────

        self.btn_download = QPushButton("▶  Pobierz NMT")

        self.btn_download.setStyleSheet(

            "QPushButton { background:#1565c0; color:white; font-weight:bold; "

            "padding:10px 20px; border-radius:4px; }"

            "QPushButton:hover { background:#1976d2; }"

            "QPushButton:disabled { background:#aaa; }"

        )

        self.btn_download.clicked.connect(self._on_download)

        root.addWidget(self.btn_download)



        # Log

        self.log_box = QTextEdit()

        self.log_box.setReadOnly(True)

        self.log_box.setMinimumHeight(180)

        self.log_box.setFont(QFont("Consolas", 8))

        self.log_box.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")

        root.addWidget(self.log_box)



        self.progress = QProgressBar()

        self.progress.setVisible(False)

        root.addWidget(self.progress)



        btn_row = QHBoxLayout()

        self.btn_stop = QPushButton("⏹  Przerwij")

        self.btn_stop.setEnabled(False)

        self.btn_stop.setStyleSheet(

            "QPushButton { background:#c62828; color:white; font-weight:bold; "

            "padding:8px 16px; border-radius:4px; }"

            "QPushButton:hover { background:#e53935; }"

            "QPushButton:disabled { background:#aaa; color:#eee; }"

        )

        self.btn_stop.clicked.connect(self._on_stop)

        btn_close = QPushButton("Zamknij")

        btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()

        btn_row.addWidget(btn_close)

        root.addLayout(btn_row)



    # ── Warstwy ──────────────────────────────────────────────────────────────

    def _refresh_layers(self):

        current_id = self.combo_layer.currentData()

        self.combo_layer.blockSignals(True)

        self.combo_layer.clear()

        self.combo_layer.addItem("— wybierz warstwę punktową —", userData=None)

        for layer in QgsProject.instance().mapLayers().values():

            if not isinstance(layer, QgsVectorLayer):

                continue

            if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:

                continue

            self.combo_layer.addItem(layer.name(), userData=layer.id())

        idx = 0

        if current_id:

            for i in range(self.combo_layer.count()):

                if self.combo_layer.itemData(i) == current_id:

                    idx = i

                    break

        self.combo_layer.setCurrentIndex(idx)

        self.combo_layer.blockSignals(False)



    def _read_points(self, layer):

        """Z warstwy → listy w WGS84 (podgląd) i EPSG:2180 (API)."""

        pts_wgs = []

        pts_2180 = []

        if layer is None:

            return pts_wgs, pts_2180

        crs_src = layer.crs()

        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")

        crs_92 = QgsCoordinateReferenceSystem(CRS_PUWG1992)

        t_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())

        t_92 = QgsCoordinateTransform(crs_src, crs_92, QgsProject.instance())



        for feat in layer.getFeatures():

            geom = feat.geometry()

            if geom is None or geom.isEmpty():

                continue

            pt = geom.asPoint()

            p_wgs = t_wgs.transform(pt)

            p_92 = t_92.transform(pt)

            pts_wgs.append((round(p_wgs.x(), 6), round(p_wgs.y(), 6)))

            pts_2180.append(QgsPointXY(p_92.x(), p_92.y()))

        return pts_wgs, pts_2180



    def _on_layer_changed(self):

        lid = self.combo_layer.currentData()

        layer = QgsProject.instance().mapLayer(lid) if lid else None

        if layer is None:

            if self.edit_shp.text().strip() and self.points_2180:

                return

            self.points_wgs = []

            self.points_2180 = []

            self.lbl_header.setText("Punktów: 0")

            return

        self.edit_shp.blockSignals(True)

        self.edit_shp.setText("")

        self.edit_shp.blockSignals(False)

        self.points_wgs, self.points_2180 = self._read_points(layer)

        n = len(self.points_2180)

        self.lbl_header.setText(

            f"<b>Warstwa:</b> {layer.name()}  ·  <b>Punktów:</b> {n}"

        )

        sample = self.points_wgs[:3]

        self._log(

            f"📂 Warstwa „{layer.name()}”: {n} punktów  "

            f"(WGS84 przykł.: {sample}{'…' if n > 3 else ''})"

        )



    def _browse_shp(self):

        path, _ = QFileDialog.getOpenFileName(

            self,

            "Wybierz plik SHP z punktami",

            self.edit_shp.text() or SCRIPT_DIR,

            "Shapefile (*.shp);;Wszystkie pliki (*.*)",

        )

        if path:

            self.edit_shp.setText(path)

            self._load_shp(path)



    def _load_shp(self, path):

        self.points_wgs = []

        self.points_2180 = []

        if not path or not os.path.isfile(path):

            self.lbl_header.setText("Punktów: 0")

            return

        layer = QgsVectorLayer(path, "nmt_pts_tmp", "ogr")

        if not layer.isValid():

            QMessageBox.warning(self, "Błąd", f"Nie można wczytać SHP:\n{path}")

            self.lbl_header.setText("Punktów: 0 – błąd odczytu")

            return

        if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:

            QMessageBox.warning(self, "Błąd", "Plik SHP musi zawierać geometrię punktową.")

            self.lbl_header.setText("Punktów: 0 – zła geometria")

            return

        self.combo_layer.blockSignals(True)

        self.combo_layer.setCurrentIndex(0)

        self.combo_layer.blockSignals(False)

        self.points_wgs, self.points_2180 = self._read_points(layer)

        n = len(self.points_2180)

        self.lbl_header.setText(

            f"<b>SHP:</b> {os.path.basename(path)}  ·  <b>Punktów:</b> {n}"

        )

        self._log(f"📂 Wczytano {n} punktów z {path}")

        self._last_shp = path



    def _browse_outdir(self):

        path = QFileDialog.getExistingDirectory(

            self, "Katalog wynikowy", self.edit_outdir.text()

        )

        if path:

            self.edit_outdir.setText(path)



    def _log(self, msg):

        self.log_box.append(msg)

        self.log_box.verticalScrollBar().setValue(

            self.log_box.verticalScrollBar().maximum()

        )



    # ── Pobieranie ───────────────────────────────────────────────────────────

    def _on_download(self):

        shp = self.edit_shp.text().strip()

        if shp and (not self.points_2180 or shp != getattr(self, "_last_shp", None)):

            self._load_shp(shp)

            self._last_shp = shp



        if not self.points_2180:

            QMessageBox.warning(

                self, "Błąd",

                "Wskaż warstwę punktową lub plik SHP.",

            )

            return



        out_dir = self.edit_outdir.text().strip() or DEFAULT_OUTPUT

        try:

            os.makedirs(out_dir, exist_ok=True)

        except OSError as e:

            QMessageBox.critical(self, "Błąd", f"Nie można utworzyć katalogu:\n{e}")

            return



        formats = set()

        if self.cb_arc.isChecked():

            formats.add("ARC/INFO ASCII GRID")

        if self.cb_xyz.isChecked():

            formats.add("ASCII XYZ GRID")

        # puste = wszystkie



        is_evrf = self.rb_evrf.isChecked()

        only_newest = self.cb_newest.isChecked()



        self._log("─" * 50)

        self._log(f"Start: {len(self.points_2180)} punktów → {out_dir}")

        self._log(f"Formaty: {formats or 'wszystkie'}")

        self._log(f"Tylko najnowsze: {only_newest}")



        self.btn_download.setEnabled(False)

        self.btn_stop.setEnabled(True)

        self.progress.setVisible(True)

        self.progress.setRange(0, 0)



        self._thread = QThread()

        self._worker = DownloadWorker(

            list(self.points_2180),

            out_dir,

            is_evrf,

            formats if formats else None,

            only_newest,

        )

        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)

        self._worker.log.connect(self._log)

        self._worker.progress.connect(self._on_progress)

        self._worker.finished.connect(self._on_finished)

        self._worker.error.connect(self._on_error)

        self._worker.finished.connect(self._thread.quit)

        self._worker.error.connect(self._thread.quit)

        self._thread.finished.connect(self._cleanup)

        self._thread.start()



    def _on_progress(self, cur, total):

        self.progress.setRange(0, total)

        self.progress.setValue(cur)



    def _on_finished(self, result):

        self._log("─" * 50)

        if result.get("status") == "cancelled":

            self._log("⏹  Przerwano.")

            return

        if result.get("status") == "ok":

            self._log(

                f"✓  Gotowe. Pobrano: {result.get('downloaded', 0)}, "

                f"już istniały: {result.get('skipped', 0)}, "

                f"błędy: {result.get('errors', 0)} "

                f"(arkuszy: {result.get('total', '?')})"

            )

            if result.get("message"):

                self._log(result["message"])

            out = result.get("output_dir")

            if out and os.path.isdir(out):

                with contextlib.suppress(Exception):

                    open_path(out)


        else:

            self._log(f"⚠  Wynik: {result}")



    def _on_stop(self):

        if self._worker is not None:

            self._log("⏹  Żądanie przerwania…")

            self._worker.stop()

        self.btn_stop.setEnabled(False)



    def _on_error(self, msg):

        self._log(f"✗  BŁĄD: {msg}")

        QMessageBox.critical(self, "Błąd", msg[:600])



    def _cleanup(self):

        self.btn_download.setEnabled(True)

        self.btn_stop.setEnabled(False)

        self.progress.setVisible(False)

        self._thread = None

        self._worker = None





# ── Entry point ──────────────────────────────────────────────────────────────

def main():

    print(f"[OK] NMT Downloader GUI  ·  katalog domyślny: {DEFAULT_OUTPUT}")

    global _nmt_dlg

    try:

        parent = iface.mainWindow()

    except NameError:

        parent = None

    _nmt_dlg = NMTDataDialog(parent=parent)

    _nmt_dlg.show()

    _nmt_dlg.raise_()

    _nmt_dlg.activateWindow()






if __name__ == '__main__':
    main()
