# -*- coding: utf-8 -*-
"""
Uruchamianie workerów WindLab lokalnie albo na serwerze obliczeń.

run() / Popen() mają interfejs zgodny z subprocess (podzbiór używany przez dialogi):
  - tryb lokalny  – zwykły subprocess (zachowanie jak dotąd),
  - tryb serwerowy – gdy ustawiono WINDLAB_SERVER_URL (+ WINDLAB_API_KEY):
      1. ścieżki w argumentach i parametrach JSON są mapowane:
         istniejące pliki/katalogi → wysyłane (in/…), ścieżki wyjściowe → katalogi out/…,
      2. zadanie (ZIP) idzie na serwer, log przychodzi na żywo,
      3. wyniki (ZIP) są rozpakowywane do lokalnych katalogów wyjściowych,
         a ścieżki serwerowe w stdout/stderr zamieniane na lokalne.

Bez importu qgis/PyQt. Serwer nie przechowuje danych – katalog zadania jest usuwany po odbiorze.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess  # nosec B404 – tylko stałe/obsługa procesów workera (uruchamianie przez core.remote_exec)
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import latest
from .worker_registry import WORKERS, OUT_KEYS, count_turbines, params_from_argv, worker_id
import contextlib

PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT

WINDLAB_ROOT = Path(__file__).resolve().parents[1]
JOB_MARK = "{JOB}"
WTG_MARK = "{WTG}"                 # biblioteka turbin na serwerze (wtg://plik → {WTG}/plik)
WTG_SCHEME = "wtg://"
MAX_UPLOAD_MB = 600
POLL_S = 0.7

_ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


class RemoteError(RuntimeError):
    pass


# ── konfiguracja ──────────────────────────────────────────────────────────────

def server_url() -> str:
    return (os.environ.get("WINDLAB_SERVER_URL") or "").strip().rstrip("/")


def api_key() -> str:
    return (os.environ.get("WINDLAB_API_KEY") or "").strip()


def is_remote() -> bool:
    """Tryb serwerowy: wersja demo zawsze (bez adresu – czytelny błąd połączenia), inaczej gdy podano adres."""
    if os.environ.get("WINDLAB_EXEC", "").lower() == "local":
        return False
    from . import demo
    return demo.DEMO or bool(server_url())


def _request(method: str, path: str, data: Optional[bytes] = None,
             ctype: Optional[str] = None, timeout: float = 60.0) -> Tuple[int, bytes]:
    if not server_url():
        raise RemoteError("Nie ustawiono adresu serwera obliczeń – WindLab › Aktywacja WindLab.")
    req = urllib.request.Request(server_url() + path, data=data, method=method)
    req.add_header("X-API-Key", api_key())
    if ctype:
        req.add_header("Content-Type", ctype)
    if not req.full_url.lower().startswith(("https://", "http://")):
        raise RemoteError(f"Nieprawidłowy adres serwera obliczeń: {server_url()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 – tylko http(s), sprawdzone wyżej
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            msg = json.loads(body).get("detail") or body.decode("utf-8", "replace")
        except Exception:
            msg = body.decode("utf-8", "replace")
        raise RemoteError(f"Serwer obliczeń: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RemoteError(f"Brak połączenia z serwerem obliczeń ({server_url()}): {e}") from None


def health() -> dict:
    _, body = _request("GET", "/health", timeout=15)
    return json.loads(body)


_LIMIT: Dict[str, Optional[int]] = {}


def _turbine_limit() -> Optional[int]:
    """Limit turbin z serwera (wersja demo) – pobierany raz na adres serwera."""
    url = server_url()
    if url not in _LIMIT:
        info = health()
        _LIMIT[url] = int(info["max_turbines"]) if info.get("demo") and info.get("max_turbines") else None
    return _LIMIT[url]


def _precheck(script: str, rest: List[str]) -> None:
    """Limit turbin sprawdzany PRZED pakowaniem i wysyłką danych (serwer sprawdza go ponownie)."""
    try:
        params = params_from_argv(rest)
    except Exception:  # noqa: BLE001
        return
    spec = WORKERS.get(script) or {}
    if isinstance(params, dict):
        params = _with_names(_Package(), dict(params), spec)
    check_turbine_limit(count_turbines(script, params))


def check_turbine_limit(n: int) -> None:
    """Błąd, gdy zadanie przekracza limit turbin wersji demo (tryb serwerowy)."""
    if not n or not is_remote():
        return
    lim = _turbine_limit()
    if lim is not None and n > lim:
        raise RemoteError(f"Wersja demo: obliczenia dla maks. {lim} turbin (w zadaniu: {n}). "
                          f"Zmniejsz liczbę turbin w warstwie lub zaznacz wybrane.")


_WTG_INFO: Dict[str, dict] = {}


def wtg_list() -> List[dict]:
    """Turbiny z biblioteki serwera: [{"file": "…wtg", "name": "…"}]."""
    _, body = _request("GET", "/wtg", timeout=30)
    return json.loads(body)


def wtg_info(file_name: str) -> dict:
    """Opis modelu turbiny z serwera (bez krzywych) – zapamiętany do końca sesji."""
    if file_name not in _WTG_INFO:
        _, body = _request("GET", "/wtg/" + urllib.parse.quote(file_name, safe=""), timeout=60)
        _WTG_INFO[file_name] = json.loads(body)
    return _WTG_INFO[file_name]


# ── mapowanie ścieżek ─────────────────────────────────────────────────────────

class _Package:
    """Zbiera pliki wejściowe i katalogi wyjściowe jednego zadania."""

    def __init__(self):
        self.files: List[Tuple[str, Path]] = []        # (ścieżka w ZIP, plik lokalny)
        self.inputs: Dict[str, str] = {}               # lokalna ścieżka → token
        self.in_back: List[Tuple[str, str]] = []       # (ścieżka w zadaniu, lokalna) do podmiany w logu
        self.out_roots: List[Tuple[Path, int]] = []    # (lokalny katalog, k)
        self.templated: List[str] = []
        self.payload: List[Tuple[str, bytes]] = []     # pliki JSON po podmianie ścieżek
        self.size = 0
        self.nc_names: List[str] = []                  # turbiny zadania – filtr plików {name}_*.nc

    # wyjścia
    def _out_root(self, local_dir: Path) -> int:
        local_dir = Path(os.path.abspath(local_dir))
        for d, k in self.out_roots:
            if d == local_dir:
                return k
        k = len(self.out_roots)
        self.out_roots.append((local_dir, k))
        return k

    def _inside_out(self, p: Path) -> Optional[Tuple[int, str]]:
        p = Path(os.path.abspath(p))
        for d, k in sorted(self.out_roots, key=lambda t: -len(str(t[0]))):
            try:
                return k, p.relative_to(d).as_posix()
            except ValueError:
                continue
        return None

    def map_output(self, value: str) -> str:
        p = Path(value)
        is_file = bool(p.suffix) and not p.is_dir()
        if is_file:
            k = self._out_root(p.parent)
            return f"{JOB_MARK}/out/{k}/{p.name}"
        k = self._out_root(p)
        return f"{JOB_MARK}/out/{k}"

    # wejścia
    def _add_file(self, arc: str, f: Path) -> None:
        self.size += f.stat().st_size
        if self.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise RemoteError(f"Za dużo danych do wysłania (> {MAX_UPLOAD_MB} MB) – sprawdź katalogi wejściowe.")
        if f.suffix.lower() == ".json" and f.stat().st_size < 50 * 1024 * 1024:
            # JSON (wyniki, parametry) – ścieżki w środku też muszą wskazywać pliki w zadaniu
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if data is not None:
                self.add_json(arc, self.map_json(data))
                return
        self.files.append((arc, f))

    def add_json(self, arc: str, data: Any) -> None:
        self.payload.append((arc, json.dumps(data, ensure_ascii=False).encode("utf-8")))
        self.templated.append(arc)

    def map_input(self, value: str, key: Optional[str] = None) -> str:
        p = Path(value)
        ck = os.path.normcase(os.path.abspath(p))
        if ck in self.inputs:
            return self.inputs[ck]
        inside = self._inside_out(p)
        if inside is not None:                         # plik z katalogu wyjściowego → na swoje miejsce w out/
            k, rel = inside
            tok = f"{JOB_MARK}/out/{k}/{rel}" if rel != "." else f"{JOB_MARK}/out/{k}"
            self.inputs[ck] = tok
            if p.is_file():
                self._add_file(f"out/{k}/{rel}", p)
        else:
            n = len(self.inputs)
            base = f"in/{n}"
            tok = f"{JOB_MARK}/{base}/{p.name}"
            self.inputs[ck] = tok
            self.in_back.append((tok, p.as_posix()))
            if p.is_dir():
                files = [f for f in sorted(p.rglob("*")) if f.is_file()]
                if key == "nc_dir" and self.nc_names:
                    # tylko dane NEWA turbin z zadania (katalog projektu ma zwykle wszystkie)
                    sel = [f for f in files if f.suffix.lower() == ".nc"
                           and any(f.name.startswith(f"{n}_") for n in self.nc_names)]
                    files = sel or files
                for f in files:
                    self._add_file(f"{base}/{p.name}/{f.relative_to(p).as_posix()}", f)
            else:
                # plik + pliki towarzyszące o tej samej nazwie (shapefile: .dbf .shx .prj .cpg …)
                for f in sorted(p.parent.glob(glob_escape(p.stem) + ".*")):
                    if f.is_file() and (f == p or p.suffix.lower() == ".shp"):
                        self._add_file(f"{base}/{f.name}", f)
        return tok

    def map_json(self, obj: Any, key: Optional[str] = None) -> Any:
        if isinstance(obj, dict):
            # najpierw wyjścia (żeby pliki z katalogów wyjściowych trafiły do out/)
            for k, v in obj.items():
                if k in OUT_KEYS and isinstance(v, str) and _ABS_PATH.match(v):
                    self.map_output(v)
            return {k: self.map_json(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.map_json(v, key) for v in obj]
        if isinstance(obj, str) and obj.startswith(WTG_SCHEME):
            return WTG_MARK + "/" + obj[len(WTG_SCHEME):]
        if isinstance(obj, str) and len(obj) < 1024 and _ABS_PATH.match(obj):
            if key in OUT_KEYS:
                return self.map_output(obj)
            if os.path.exists(obj):
                return self.map_input(obj, key)
        return obj


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def _rel_script(arg: str) -> Optional[str]:
    """Identyfikator workera dla ścieżki skryptu (serwer zna ścieżki – wtyczka tylko identyfikatory)."""
    if not arg.lower().endswith(".py"):
        return None
    return worker_id(arg)


def _build(cmd: List[str]) -> Tuple[bytes, _Package]:
    script, rest = None, []
    for i, a in enumerate(cmd[1:], start=1):
        script = _rel_script(str(a))
        if script:
            rest = [str(x) for x in cmd[i + 1:]]
            break
    if not script:
        raise RemoteError(f"Nieznany worker – nie można uruchomić zdalnie: {cmd}")
    spec = WORKERS[script]
    _precheck(script, rest)
    pkg = _Package()
    out_args = set(spec.get("out_args", []))
    for i in out_args:
        if i < len(rest):
            pkg.map_output(rest[i])
    argv: List[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if i in out_args:
            argv.append(pkg.map_output(a))
        elif a == "--params-file" and i + 1 < len(rest):
            name = f"in/params{len(pkg.payload)}.json"
            pkg.add_json(name, pkg.map_json(_with_names(pkg, _read_json(rest[i + 1]), spec)))
            argv += [a, f"{JOB_MARK}/{name}"]
            i += 1
        elif a.lstrip().startswith("{"):
            argv.append(json.dumps(pkg.map_json(_with_names(pkg, json.loads(a), spec)), ensure_ascii=False))
        elif a.lower().endswith(".json") and os.path.isfile(a):
            name = f"in/params{len(pkg.payload)}.json"
            pkg.add_json(name, pkg.map_json(_with_names(pkg, _read_json(a), spec)))
            argv.append(f"{JOB_MARK}/{name}")
        elif a.startswith(WTG_SCHEME):
            argv.append(WTG_MARK + "/" + a[len(WTG_SCHEME):])
        elif _ABS_PATH.match(a) and os.path.exists(a):
            argv.append(pkg.map_input(a))
        else:
            argv.append(a)
        i += 1

    # pliki wyników, które worker czyta z katalogu wyjściowego po nazwie (np. raport kabli)
    # (najnowszy <nazwa>_<znacznik czasu>.<ext> albo starszy <nazwa>.<ext>)
    for name in spec.get("out_inputs", []):
        base, ext = os.path.splitext(name)
        for root, _k in list(pkg.out_roots):
            f = latest(str(root), base, ext)
            if f:
                pkg.map_input(f)

    manifest = {"worker": script, "proto": 2, "argv": argv, "templated": pkg.templated,
                "outputs": [k for _, k in pkg.out_roots]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, data in pkg.payload:
            z.writestr(name, data)
        for arc, f in pkg.files:
            z.write(f, arc)
    return buf.getvalue(), pkg


def _with_names(pkg: _Package, params: Any, spec: Optional[dict] = None) -> Any:
    """Uzupełnij domyślne wyjścia workera i zapamiętaj nazwy turbin (filtr katalogu NC)."""
    for out_key, (src_key, sub) in ((spec or {}).get("default_out") or {}).items():
        if isinstance(params, dict) and not params.get(out_key) and params.get(src_key):
            params[out_key] = os.path.join(params[src_key], sub)
    if isinstance(params, dict) and isinstance(params.get("turbines"), dict):
        t = params["turbines"]
        names = t.get("names") or t.get("userlabel") or t.get("labels") or []
        pkg.nc_names = [str(n) for n in names if str(n)]
    return params


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── proces zdalny ─────────────────────────────────────────────────────────────

class _Lines:
    """Iterator linii jednego strumienia (stdout / stderr / oba) zadania zdalnego."""

    def __init__(self, proc: "RemoteProcess", streams: Tuple[str, ...]):
        self._p, self._streams = proc, streams

    def __iter__(self):
        return self

    def __next__(self):
        line = self._p._next_line(self._streams)
        if line is None:
            raise StopIteration
        return line

    def read(self):
        return ("" if self._p._text else b"").join(iter(self))

    def close(self):
        pass


class RemoteProcess:
    def __init__(self, cmd, stdout=None, stderr=None, text=False, encoding=None,
                 errors=None, universal_newlines=None, **_ignored):
        self.args = cmd
        self._text = bool(text or encoding or errors or universal_newlines)
        self._enc = encoding or "utf-8"
        self._errors = errors or "replace"
        self._stdout_target = stdout
        self.returncode: Optional[int] = None
        self._queue: List[Tuple[str, str]] = []
        self._next = 0
        self._done = False
        self._finished = False
        self._out_text = ""
        self._err_text = ""

        data, self._pkg = _build([str(c) for c in cmd])
        _, body = _request("POST", "/jobs", data=data, ctype="application/zip", timeout=900)
        info = json.loads(body)
        self.job_id = info["job_id"]
        self.pid = self.job_id
        self._proto = int(info.get("proto") or 1)          # 2: serwer nie podaje swoich ścieżek (znaczniki)
        self._job_dir = info.get("job_dir") or JOB_MARK
        self._wtg_dir = info.get("wtg_dir") or ""
        self._subs = self._substitutions()

        if stdout == PIPE and stderr == STDOUT:
            self.stdout, self.stderr = _Lines(self, ("out", "err")), None
        else:
            self.stdout = _Lines(self, ("out",)) if stdout == PIPE else None
            self.stderr = _Lines(self, ("err",)) if stderr == PIPE else None

    # podmiana ścieżek serwerowych na lokalne (warianty /, \ i \\ z JSON)
    def _substitutions(self) -> List[Tuple[str, str]]:
        jd = self._job_dir.rstrip("/")
        pairs = [(f"{jd}/out/{k}", d.as_posix()) for d, k in self._pkg.out_roots]
        pairs += [(tok.replace(JOB_MARK, jd), local) for tok, local in self._pkg.in_back]
        if self._proto >= 2:                                # biblioteka serwera jako znacznik {WTG} → wtg://
            pairs.append((WTG_MARK + "/", WTG_SCHEME))
        elif self._wtg_dir:                                 # starszy serwer: jawna ścieżka biblioteki
            pairs.append((self._wtg_dir.rstrip("/") + "/", WTG_SCHEME))
        subs = []
        for srv, loc in sorted(pairs, key=lambda t: -len(t[0])):
            subs.append((srv, loc))
            win = srv.replace("/", "\\")
            if win != srv:
                subs.append((win, loc))
                subs.append((win.replace("\\", "\\\\"), loc))
        return subs

    def _fix(self, s: str) -> str:
        for a, b in self._subs:
            if a in s:
                s = s.replace(a, b)
        return s

    def _poll_log(self) -> None:
        _, body = _request("GET", f"/jobs/{self.job_id}/log?start={self._next}", timeout=60)
        info = json.loads(body)
        for stream, line in info["lines"]:
            self._queue.append((stream, self._fix(line)))
        self._next = info["next"]
        if info["done"]:
            self._done = True
            self.returncode = info.get("returncode")

    def _next_line(self, streams):
        while True:
            for i, (s, line) in enumerate(self._queue):
                if s in streams:
                    del self._queue[i]
                    return line if self._text else line.encode(self._enc, self._errors)
            if self._done:
                self._finish()
                return None
            self._poll_log()
            if not self._queue and not self._done:
                time.sleep(POLL_S)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        _, body = _request("GET", f"/jobs/{self.job_id}/result", timeout=120)
        res = json.loads(body)
        self.returncode = res["returncode"]
        self._out_text = self._fix(res.get("stdout") or "")
        self._err_text = self._fix(res.get("stderr") or "")
        _, zbytes = _request("GET", f"/jobs/{self.job_id}/outputs", timeout=900)
        roots = {k: d for d, k in self._pkg.out_roots}
        with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
            for name in z.namelist():
                parts = name.split("/", 2)                     # out/k/rel
                if len(parts) < 3 or parts[0] != "out" or not parts[2] or name.endswith("/"):
                    continue
                root = roots.get(int(parts[1]))
                if root is None:
                    continue
                dest = (root / parts[2]).resolve()
                if root.resolve() not in dest.parents:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = z.read(name)
                if dest.suffix.lower() in (".json", ".geojson", ".csv", ".txt"):   # ścieżki serwera → lokalne
                    data = self._fix(data.decode("utf-8", "replace")).encode("utf-8")
                with open(dest, "wb") as dst:
                    dst.write(data)
        with contextlib.suppress(RemoteError):
            _request("DELETE", f"/jobs/{self.job_id}", timeout=30)
        if self._stdout_target not in (None, PIPE, STDOUT, subprocess.DEVNULL) and hasattr(self._stdout_target, "write"):
            self._stdout_target.write(self._out_text)
            self._stdout_target.flush()

    # interfejs subprocess.Popen
    def poll(self):
        if not self._done:
            self._poll_log()
        if self._done:
            self._finish()
            return self.returncode
        return None

    def wait(self, timeout=None):
        t_end = None if timeout is None else time.time() + timeout
        while self.poll() is None:
            if t_end is not None and time.time() > t_end:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(POLL_S)
        return self.returncode

    def communicate(self, input=None, timeout=None):
        self.wait(timeout)
        out, err = self._out_text, self._err_text
        if self.stdout is not None and self.stderr is None and self._stdout_target == PIPE \
                and isinstance(self.stdout, _Lines) and self.stdout._streams == ("out", "err"):
            out = self._out_text + self._err_text
        if not self._text:
            out, err = out.encode(self._enc, self._errors), err.encode(self._enc, self._errors)
        return (out if self._stdout_target == PIPE else None,
                err if self.stderr is not None else None)

    def kill(self):
        with contextlib.suppress(RemoteError):
            _request("DELETE", f"/jobs/{self.job_id}", timeout=30)
        self._done = self._finished = True
        if self.returncode is None:
            self.returncode = -9

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if not self._finished:
            self.kill()


# ── API zgodne z subprocess ───────────────────────────────────────────────────

def Popen(cmd, *args, **kwargs):
    if not is_remote():
        return subprocess.Popen(cmd, *args, **kwargs)  # nosec B603 – worker WindLab uruchamiany lokalnie (polecenie budowane przez wtyczkę)
    return RemoteProcess(cmd, **kwargs)


def run(cmd, *args, capture_output=False, timeout=None, check=False, **kwargs):
    if not is_remote():
        return subprocess.run(cmd, *args, capture_output=capture_output, timeout=timeout,  # nosec B603 – jw.
                              check=check, **kwargs)
    if capture_output:
        kwargs["stdout"] = kwargs["stderr"] = PIPE
    for k in ("env", "cwd", "creationflags", "startupinfo", "bufsize", "shell", "input"):
        kwargs.pop(k, None)
    p = RemoteProcess(cmd, **kwargs)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise
    cp = subprocess.CompletedProcess(cmd, p.returncode, out, err)
    if check and p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd, out, err)
    return cp
