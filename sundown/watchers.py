"""Download watchers for Steam and qBittorrent. No GUI imports here.

Each watcher's poll() returns a Status: whether something is downloading right now,
a short list of what, and an error key when the source cannot be read.
"""
import glob
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Item:
    name: str
    progress: float = -1.0   # 0..1, or -1 when unknown
    speed: int = 0           # bytes/s, 0 when unknown


@dataclass
class Status:
    running: bool = False             # is the program itself running
    active: bool = False              # is anything downloading right now
    items: list = field(default_factory=list)
    speed: int = 0                    # total bytes/s, 0 when unknown
    error: str = ""                   # i18n key; the source could not be read
    detail: str = ""


def process_running(*names):
    """True when a process with one of these names (comm) runs for any user."""
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm) as f:
                if f.read().strip() in names:
                    return True
        except OSError:
            pass
    return False


# -------------------------------------------------------------- Steam ----
# AppState StateFlags from appmanifest_*.acf. Any of these means Steam is working on
# the app right now. UpdateRequired (2) alone is a queued or scheduled update that is
# not running, and UpdatePaused (512) is a download the user paused.
STEAM_UPDATE_RUNNING = 256
STEAM_UPDATE_PAUSED = 512
STEAM_ACTIVE = (STEAM_UPDATE_RUNNING | 1024 | 131072 | 262144 | 524288 | 1048576
                | 2097152 | 4194304)

STEAM_ROOTS = [
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.steam/root",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
]

_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Minimal Valve KeyValues parser: nested dicts of strings."""
    root, stack, key = {}, [], None
    cur = root
    for m in _VDF_TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            new = {}
            if key is not None:
                cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop() if stack else root
            key = None
        elif key is None:
            key = s.replace('\\\\', '\\')
        else:
            cur[key] = s.replace('\\\\', '\\')
            key = None
    return root


def _read_vdf(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_vdf(f.read())
    except OSError:
        return {}


def steam_library_dirs(roots=None):
    """All steamapps folders: every Steam root plus libraries from libraryfolders.vdf."""
    seen, out = set(), []

    def add(path):
        path = os.path.realpath(path)
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            out.append(path)

    for root in roots if roots is not None else STEAM_ROOTS:
        steamapps = os.path.join(os.path.expanduser(root), "steamapps")
        add(steamapps)
        lib = _read_vdf(os.path.join(steamapps, "libraryfolders.vdf"))
        folders = lib.get("libraryfolders") or lib.get("LibraryFolders") or {}
        for v in folders.values():
            path = v.get("path") if isinstance(v, dict) else v
            if isinstance(path, str) and path.startswith("/"):
                add(os.path.join(path, "steamapps"))
    return out


def steam_downloads(library_dirs):
    """Items Steam is downloading, updating or validating, from the app manifests."""
    items = []
    for d in library_dirs:
        for acf in sorted(glob.glob(os.path.join(d, "appmanifest_*.acf"))):
            st = _read_vdf(acf).get("AppState") or {}
            try:
                flags = int(st.get("StateFlags", "0"))
            except ValueError:
                continue
            if not flags & STEAM_ACTIVE or flags & STEAM_UPDATE_PAUSED:
                continue
            name = st.get("name") or f"App {st.get('appid', '?')}"
            progress = -1.0
            try:
                total = int(st.get("BytesToDownload", "0"))
                done = int(st.get("BytesDownloaded", "0"))
                if total > 0:
                    progress = min(done / total, 1.0)
            except ValueError:
                pass
            items.append(Item(name, progress))
    return items


class SteamWatcher:
    def __init__(self, roots=None):
        self.roots = roots

    def poll(self):
        if not process_running("steam", "steamwebhelper"):
            return Status(running=False)
        libs = steam_library_dirs(self.roots)
        if not libs:
            return Status(running=True, error="err_steam_dir")
        items = steam_downloads(libs)
        return Status(running=True, active=bool(items), items=items)


# -------------------------------------------------------- qBittorrent ----
# Torrent states that mean "this torrent is still on its way". stalledDL (no peers)
# counts only when the user asks to wait for stalled torrents too, otherwise one dead
# torrent would keep the computer on forever. Paused/stopped torrents never count.
QBIT_ACTIVE = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "queuedDL",
               "checkingDL", "allocating", "checkingResumeData", "moving"}
QBIT_STALLED = {"stalledDL"}


class QbitError(Exception):
    def __init__(self, key, detail=""):
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key, self.detail = key, detail


class QbitClient:
    """qBittorrent Web UI API v2 client (Tools → Preferences → Web UI)."""

    def __init__(self, url="http://localhost:8080", username="", password="", timeout=5):
        self.url = url.rstrip("/")
        self.username, self.password, self.timeout = username, password, timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}))   # localhost: never through a proxy

    def _request(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=body)
        req.add_header("Referer", self.url)    # qBittorrent's CSRF check wants this
        req.add_header("Origin", self.url)
        with self.opener.open(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def login(self):
        try:
            text = self._request("/api/v2/auth/login",
                                 {"username": self.username, "password": self.password})
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise QbitError("err_qbit_banned") from e
            raise QbitError("err_qbit_connect", str(e)) from e
        except (urllib.error.URLError, OSError) as e:
            raise QbitError("err_qbit_connect", str(e)) from e
        if text.strip() != "Ok.":
            raise QbitError("err_qbit_login")

    def get_json(self, path):
        for attempt in (0, 1):
            try:
                return json.loads(self._request(path))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0:
                    self.login()       # no session yet, or it expired
                    continue
                if e.code in (401, 403):
                    raise QbitError("err_qbit_login") from e
                # Something answers, but not qBittorrent's API (e.g. Steam's CEF
                # debugger, which also likes port 8080).
                raise QbitError("err_qbit_other", str(e)) from e
            except (urllib.error.URLError, OSError) as e:
                raise QbitError("err_qbit_connect", str(e)) from e
            except ValueError as e:
                raise QbitError("err_qbit_other", "not a qBittorrent response") from e

    def torrents(self):
        return self.get_json("/api/v2/torrents/info")


QBIT_CONFIGS = [
    "~/.config/qBittorrent/qBittorrent.conf",
    "~/.var/app/org.qbittorrent.qBittorrent/config/qBittorrent/qBittorrent.conf",
]
LOCAL_URL = re.compile(r"^(https?)://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", re.I)


def qbit_config_url(paths=None):
    """Web UI address from qBittorrent's own settings, or None."""
    for path in paths if paths is not None else QBIT_CONFIGS:
        prefs, section = {}, ""
        try:
            with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        section = line
                    elif section == "[Preferences]" and "=" in line:
                        k, v = line.split("=", 1)
                        prefs[k.strip()] = v.strip()
        except OSError:
            continue
        port = prefs.get("WebUI\\Port", "8080")
        if not port.isdigit():
            continue
        scheme = "https" if prefs.get("WebUI\\HTTPS\\Enabled") == "true" else "http"
        return f"{scheme}://localhost:{port}"
    return None


def effective_qbit_url(url, paths=None):
    """A local address follows the port set in qBittorrent, so changing the port
    there needs no change here. A remote address is used as typed."""
    if LOCAL_URL.match(url or ""):
        return qbit_config_url(paths) or url
    return url


class QbitWatcher:
    def __init__(self, client, wait_stalled=False):
        self.client, self.wait_stalled = client, wait_stalled

    def poll(self):
        try:
            torrents = self.client.torrents()
        except QbitError as e:
            # Web UI unreachable and no qBittorrent process: it is simply closed.
            if e.key == "err_qbit_connect" and not process_running("qbittorrent",
                                                                     "qbittorrent-nox"):
                return Status(running=False)
            return Status(running=True, error=e.key, detail=e.detail)
        wanted = QBIT_ACTIVE | (QBIT_STALLED if self.wait_stalled else set())
        items, speed = [], 0
        for t in torrents:
            if t.get("state") in wanted:
                s = int(t.get("dlspeed") or 0)
                items.append(Item(t.get("name") or "?", float(t.get("progress") or 0), s))
                speed += s
        return Status(running=True, active=bool(items), items=items, speed=speed)
