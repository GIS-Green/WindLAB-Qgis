# -*- coding: utf-8 -*-
"""
Źródło wysokości terenu: lokalny NMT (raster) albo usługa GUGiK NMT online.

Bez importu qgis/PyQt – używane w GUI i w workerach (zewnętrzny Python).

Usługa GUGiK (https://services.gugik.gov.pl/nmt/):
  - GetHByPointList (POST) – maks. 1000 punktów z wysokością na zapytanie (~1,2 s);
    dalsze punkty wracają bez wartości,
  - współrzędne EPSG:2180 w kolejności geodezyjnej „północ wschód”,
  - wysokości zaokrąglone do 0,1 m (w testach = arkusze NMT 1 m, |Δ| ≤ 0,05 m),
  - punkt poza zasięgiem danych → 0.0, ALE kolejne wartości w odpowiedzi
    przesuwają się o jedną pozycję (zera trafiają na koniec listy).
    Dlatego paczka z zerem jest dzielona na pół aż do pojedynczych punktów.
  - tylko Polska; 0,1 m to za mało dla analiz transportu (spadek poprzeczny 2 %).
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
from typing import Callable, List, Optional, Sequence, Tuple

SOURCE_LOCAL = "local"
SOURCE_GUGIK = "gugik"
SOURCE_LABELS = {
    SOURCE_LOCAL: "Lokalny NMT (raster)",
    SOURCE_GUGIK: "GUGiK NMT online (tylko Polska)",
}
GUGIK_LABEL = "GUGiK NMT (usługa online)"

GUGIK_URL = "https://services.gugik.gov.pl/nmt/"
CHUNK = 1000
RETRIES = 3


class GugikError(RuntimeError):
    pass


def _post_list(pts: Sequence[Tuple[float, float]], timeout: float) -> List[float]:
    """Jedno zapytanie GetHByPointList; pts w EPSG:2180 (x = wschód, y = północ)."""
    lst = ",".join(f"{y:.2f} {x:.2f}" for x, y in pts)
    data = urllib.parse.urlencode({"request": "GetHByPointList", "list": lst}).encode()
    last_err = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(GUGIK_URL, data, timeout=timeout) as resp:  # nosec B310 – stały adres https GUGiK
                text = resp.read().decode("utf-8", "replace").strip()
            items = [item.split() for item in text.split(",") if item.strip()]
            # wpis bez wysokości traktujemy jak brak danych (0.0) → podział paczki
            vals = [float(it[2]) if len(it) >= 3 else 0.0 for it in items]
            if len(vals) != len(pts):
                raise GugikError(f"GUGiK zwrócił {len(vals)} wartości dla {len(pts)} punktów")
            return vals
        except (OSError, ValueError, GugikError) as e:
            last_err = e
            time.sleep(1.0 + 2.0 * attempt)
    raise GugikError(f"Usługa GUGiK NMT niedostępna: {last_err}")


def gugik_heights(
    points: Sequence[Tuple[float, float]],
    *,
    timeout: float = 120.0,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[Optional[float]]:
    """
    Wysokości [m n.p.m.] z GUGiK dla punktów (x, y) w EPSG:2180.
    None = brak danych (poza Polską / poza zasięgiem NMT).
    """
    n = len(points)
    out: List[Optional[float]] = [None] * n
    done = [0]

    def _resolve(idx: List[int]) -> None:
        vals = _post_list([points[i] for i in idx], timeout)
        if all(v != 0.0 for v in vals):
            for i, v in zip(idx, vals):
                out[i] = v
        elif len(idx) > 1 and any(v != 0.0 for v in vals):
            # zero w paczce = możliwe przesunięcie wartości → dziel
            mid = len(idx) // 2
            _resolve(idx[:mid])
            _resolve(idx[mid:])
            return
        # wszystkie zera lub pojedynczy punkt z zerem → brak danych
        done[0] += len(idx)
        if progress:
            progress(done[0], n)

    for start in range(0, n, CHUNK):
        _resolve(list(range(start, min(start + CHUNK, n))))
    return out


def to_2180(points: Sequence[Tuple[float, float]], src_crs: str) -> List[Tuple[float, float]]:
    """Przelicz punkty do EPSG:2180 (pyproj) – gdy dane są w innym układzie."""
    code = (src_crs or "EPSG:2180").upper()
    if code in ("EPSG:2180", "2180"):
        return [(float(x), float(y)) for x, y in points]
    from pyproj import Transformer
    tr = Transformer.from_crs(src_crs, "EPSG:2180", always_xy=True)
    xs, ys = tr.transform([p[0] for p in points], [p[1] for p in points])
    return list(zip(xs, ys))


class GugikSampler:
    """
    sampler(x, y) -> wysokość, zgodny z make_dem_sampler (0.0 = brak danych).
    prefetch() pobiera wiele punktów naraz – pojedyncze wywołania tylko dla braków.
    """

    def __init__(self, src_crs: str = "EPSG:2180", log: Optional[Callable[[str], None]] = None):
        self.src_crs = src_crs
        self.log = log
        self._cache: dict = {}
        self.missing: list = []          # punkty bez danych (jak DemSampler.missing)

    @staticmethod
    def _key(x: float, y: float) -> Tuple[int, int]:
        return (round(float(x) * 100), round(float(y) * 100))

    def prefetch(self, points: Sequence[Tuple[float, float]]) -> None:
        todo = [p for p in points if self._key(*p) not in self._cache]
        if not todo:
            return
        t0 = time.time()
        z = gugik_heights(to_2180(todo, self.src_crs))
        for p, v in zip(todo, z):
            self._cache[self._key(*p)] = v
        if self.log:
            n_ok = sum(v is not None for v in z)
            self.log(f"  GUGiK NMT: {n_ok}/{len(todo)} punktów z wysokością ({time.time() - t0:.1f} s)")

    has_dem = True

    def sample(self, x: float, y: float) -> Optional[float]:
        """Wysokość albo None (punkt poza zasięgiem danych GUGiK)."""
        k = self._key(x, y)
        if k not in self._cache:
            self.prefetch([(x, y)])
        v = self._cache.get(k)
        if v is None:
            self.missing.append((float(x), float(y)))
            return None
        return float(v)

    def __call__(self, x: float, y: float) -> float:
        v = self.sample(x, y)
        return v if v is not None else 0.0
