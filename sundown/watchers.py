"""Download watchers for Steam and qBittorrent. No GUI imports here.

Each watcher's poll() returns a Status: whether something is downloading right now,
a short list of what, and an error key when the source cannot be read.
"""
import glob
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

WINDOWS = sys.platform == "win32"


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
    args: dict = field(default_factory=dict)   # values for the error text


def _exe_name(name):
    """steam.exe -> steam, so Linux and Windows process names compare alike."""
    name = (name or "").lower()
    return name[:-4] if name.endswith(".exe") else name


def process_running(*names):
    """True when a process with one of these names (comm) runs for any user."""
    if WINDOWS:
        import psutil
        wanted = {_exe_name(n) for n in names}
        for p in psutil.process_iter(["name"]):
            if _exe_name(p.info.get("name")) in wanted:
                return True
        return False
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

def _windows_steam_roots():
    roots = []
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE,
                                  r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(winreg.QueryValueEx(k, value)[0])
            except OSError:
                pass
    except ImportError:
        pass
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    roots.append(os.path.join(pf86, "Steam"))
    return roots


STEAM_ROOTS = _windows_steam_roots() if WINDOWS else [
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
            if isinstance(path, str) and os.path.isabs(path):
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
            # No percentage: Steam rewrites BytesDownloaded in the manifest only now and
            # then, so it lags far behind its own window (22% shown at 54%).
            items.append(Item(name))
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
    def __init__(self, key, detail="", **args):
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key, self.detail, self.args_ = key, detail, args


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
            raise QbitError("err_qbit_other", str(e)) from e
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
    os.path.join(os.environ.get("APPDATA", "~"), "qBittorrent", "qBittorrent.ini"),
] if WINDOWS else [
    "~/.config/qBittorrent/qBittorrent.conf",
    "~/.var/app/org.qbittorrent.qBittorrent/config/qBittorrent/qBittorrent.conf",
]
LOCAL_URL = re.compile(r"^(https?)://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", re.I)


def qbit_config(paths=None):
    """Web UI settings from qBittorrent's own config: {enabled, port, https}, or None."""
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
        return {"enabled": prefs.get("WebUI\\Enabled") == "true", "port": int(port),
                "https": prefs.get("WebUI\\HTTPS\\Enabled") == "true"}
    return None


def qbit_config_url(paths=None):
    """Web UI address from qBittorrent's own settings, or None."""
    cfg = qbit_config(paths)
    if not cfg:
        return None
    return f"{'https' if cfg['https'] else 'http'}://localhost:{cfg['port']}"


def _windows_listeners(port):
    """{pid} of processes listening on this TCP port (Windows)."""
    import psutil
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return set()
    return {c.pid or 0 for c in conns
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port}


def _listening_inodes(port):
    if WINDOWS:
        return _windows_listeners(port)
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                next(f)
                for line in f:
                    cols = line.split()
                    # local address is HEXIP:HEXPORT, state 0A is LISTEN
                    if int(cols[1].rsplit(":", 1)[1], 16) == port and cols[3] == "0A":
                        inodes.add(cols[9])
        except (OSError, ValueError, IndexError, StopIteration):
            pass
    return inodes


# Friendlier names for programs that often sit on qBittorrent's default port.
PROGRAM_NAMES = {"steamwebhelper": "Steam", "steam": "Steam", "java": "Java",
                 "node": "Node.js", "docker-proxy": "Docker"}


def port_owner(port):
    """Name of the process listening on this TCP port, if it is ours to see."""
    inodes = _listening_inodes(port)
    if not inodes:
        return None
    if WINDOWS:
        import psutil
        for pid in inodes:
            try:
                return _exe_name(psutil.Process(pid).name())
            except (psutil.Error, ValueError):
                continue
        return "?"
    targets = {f"socket:[{i}]" for i in inodes}
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        try:
            for fd in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, fd)) in targets:
                    with open(os.path.join(os.path.dirname(fd_dir), "comm")) as f:
                        return f.read().strip()
        except OSError:
            continue
    return "?"       # someone listens, but runs as another user


def free_port(start=8090):
    """First port from start up that nothing listens on."""
    for port in range(start, start + 100):
        if not _listening_inodes(port):
            return port
    return start


def diagnose(err, url, paths=None):
    """Turn a raw connection error into the most likely cause and its fix."""
    try:
        port = urllib.parse.urlsplit(url).port or (443 if url.startswith("https") else 80)
    except ValueError:
        return err
    if not LOCAL_URL.match(url or "") or err.key not in ("err_qbit_connect", "err_qbit_other"):
        return err
    cfg = qbit_config(paths)
    if cfg and not cfg["enabled"]:
        return QbitError("err_qbit_off", err.detail)
    if port < 1024 and not WINDOWS:     # only Linux keeps low ports for root
        return QbitError("err_qbit_lowport", err.detail, port=port, free=free_port())
    if err.key == "err_qbit_other":
        owner = port_owner(port)
        if owner and owner != "?" and not owner.startswith("qbittorrent"):
            return QbitError("err_qbit_taken", err.detail, port=port,
                             prog=PROGRAM_NAMES.get(owner, owner),
                             free=free_port())
        return QbitError("err_qbit_other_port", err.detail, port=port, free=free_port())
    if cfg:   # Web UI is on in the settings, yet nothing answers: not applied yet
        return QbitError("err_qbit_restart", err.detail, port=port)
    return err


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
            e = diagnose(e, self.client.url)
            return Status(running=True, error=e.key, detail=e.detail, args=e.args_)
        wanted = QBIT_ACTIVE | (QBIT_STALLED if self.wait_stalled else set())
        items, speed = [], 0
        for t in torrents:
            if t.get("state") in wanted:
                s = int(t.get("dlspeed") or 0)
                items.append(Item(t.get("name") or "?", float(t.get("progress") or 0), s))
                speed += s
        return Status(running=True, active=bool(items), items=items, speed=speed)
