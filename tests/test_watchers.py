"""python3 -m unittest discover tests"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from sundown import watchers

ACF = """"AppState"
{{
\t"appid"\t\t"{appid}"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"{flags}"
\t"BytesToDownload"\t\t"{total}"
\t"BytesDownloaded"\t\t"{done}"
}}
"""


class SteamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.main = os.path.join(root, "Steam", "steamapps")
        self.extra = os.path.join(root, "Games", "steamapps")
        os.makedirs(self.main)
        os.makedirs(self.extra)
        with open(os.path.join(self.main, "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n'
                    '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n\t\t\t"730"\t\t"1"\n'
                    '\t\t}\n\t}\n}\n' % (os.path.join(root, "Steam"), os.path.join(root, "Games")))
        self.roots = [os.path.join(root, "Steam")]

    def tearDown(self):
        self.tmp.cleanup()

    def acf(self, d, appid, name, flags, total=0, done=0):
        with open(os.path.join(d, f"appmanifest_{appid}.acf"), "w") as f:
            f.write(ACF.format(appid=appid, name=name, flags=flags, total=total, done=done))

    def test_libraries(self):
        self.assertEqual(watchers.steam_library_dirs(self.roots),
                         [os.path.realpath(self.main), os.path.realpath(self.extra)])

    def test_states(self):
        self.acf(self.main, 1, "Installed", 4)
        self.acf(self.main, 2, "Queued update", 6)
        self.acf(self.extra, 3, "Downloading", 1026, 1000, 250)
        self.acf(self.extra, 4, "Paused", 1542, 1000, 10)
        self.acf(self.main, 5, "Updating", 1030)
        items = watchers.steam_downloads(watchers.steam_library_dirs(self.roots))
        self.assertEqual(sorted(i.name for i in items), ["Downloading", "Updating"])
        self.assertTrue(all(i.progress < 0 for i in items))   # manifest bytes lag

    def test_watcher(self):
        self.acf(self.extra, 3, "Downloading", 1026)
        w = watchers.SteamWatcher(self.roots)
        with mock.patch.object(watchers, "process_running", return_value=False):
            st = w.poll()
            self.assertFalse(st.running or st.active)
        with mock.patch.object(watchers, "process_running", return_value=True):
            st = w.poll()
            self.assertTrue(st.running and st.active)
            self.acf(self.extra, 3, "Downloading", 4)
            self.assertFalse(w.poll().active)


class FakeQbit(BaseHTTPRequestHandler):
    torrents = []
    need_login = True

    def log_message(self, *a):
        pass

    def _send(self, code, body, cookie=None):
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode()
        if self.path == "/api/v2/auth/login":
            if "username=admin" in body and "password=secret" in body:
                self._send(200, "Ok.", "SID=abc; HttpOnly; path=/")
            else:
                self._send(200, "Fails.")
        else:
            self._send(404, "")

    def do_GET(self):
        if self.path.startswith("/api/v2/torrents/info"):
            if self.need_login and "SID=abc" not in (self.headers.get("Cookie") or ""):
                self._send(403, "Forbidden")
            else:
                self._send(200, json.dumps(self.torrents))
        else:
            self._send(404, "")


class QbitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeQbit)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_login_and_states(self):
        FakeQbit.torrents = [
            {"name": "a", "state": "downloading", "progress": 0.5, "dlspeed": 1000},
            {"name": "b", "state": "stalledDL", "progress": 0.1, "dlspeed": 0},
            {"name": "c", "state": "uploading", "progress": 1, "dlspeed": 0},
            {"name": "d", "state": "pausedDL", "progress": 0.2, "dlspeed": 0},
            {"name": "e", "state": "queuedDL", "progress": 0, "dlspeed": 0},
        ]
        w = watchers.QbitWatcher(watchers.QbitClient(self.url, "admin", "secret"))
        st = w.poll()
        self.assertEqual(st.error, "")
        self.assertTrue(st.active)
        self.assertEqual([i.name for i in st.items], ["a", "e"])
        self.assertEqual(st.speed, 1000)
        w.wait_stalled = True
        self.assertEqual([i.name for i in w.poll().items], ["a", "b", "e"])
        FakeQbit.torrents = [{"name": "c", "state": "stalledUP", "progress": 1}]
        self.assertFalse(w.poll().active)

    def test_bad_password(self):
        st = watchers.QbitWatcher(watchers.QbitClient(self.url, "admin", "nope")).poll()
        self.assertEqual(st.error, "err_qbit_login")

    def test_not_qbittorrent(self):
        st = watchers.QbitWatcher(watchers.QbitClient(self.url + "/nothing")).poll()
        self.assertEqual(st.error, "err_qbit_other")

    def test_config_port(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "qBittorrent.conf")
            with open(conf, "w") as f:
                f.write("[LegalNotice]\nAccepted=true\n\n[Preferences]\n"
                        "WebUI\\Enabled=true\nWebUI\\Port=8090\n")
            self.assertEqual(watchers.qbit_config_url([conf]), "http://localhost:8090")
            self.assertEqual(watchers.effective_qbit_url("http://localhost:8080", [conf]),
                             "http://localhost:8090")
            self.assertEqual(watchers.effective_qbit_url("http://127.0.0.1:8080/", [conf]),
                             "http://localhost:8090")
            self.assertEqual(watchers.effective_qbit_url("http://nas:8080", [conf]),
                             "http://nas:8080")
            self.assertEqual(watchers.effective_qbit_url("http://localhost:8080",
                                                         [conf + ".missing"]),
                             "http://localhost:8080")

    @unittest.skipIf(watchers.WINDOWS, "reads /proc")
    def test_port_owner(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        with open("/proc/self/comm") as f:
            me = f.read().strip()
        self.assertEqual(watchers.port_owner(port), me)
        self.assertNotEqual(watchers.free_port(port), port)
        srv.close()
        self.assertIsNone(watchers.port_owner(port))

    def test_diagnose(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "qBittorrent.conf")

            def cfg(enabled, port):
                with open(conf, "w") as f:
                    f.write("[Preferences]\nWebUI\\Enabled=%s\nWebUI\\Port=%d\n"
                            % ("true" if enabled else "false", port))

            refused = watchers.QbitError("err_qbit_connect", "refused")
            other = watchers.QbitError("err_qbit_other", "HTTP Error 404")
            cfg(False, 8080)
            self.assertEqual(watchers.diagnose(refused, "http://localhost:8080", [conf]).key,
                             "err_qbit_off")
            if not watchers.WINDOWS:     # low ports are fine for users on Windows
                cfg(True, 90)
                e = watchers.diagnose(refused, "http://localhost:90", [conf])
                self.assertEqual((e.key, e.args_["port"]), ("err_qbit_lowport", 90))
            cfg(True, 8090)
            e = watchers.diagnose(refused, "http://localhost:8090", [conf])
            self.assertEqual((e.key, e.args_["port"]), ("err_qbit_restart", 8090))
            with mock.patch.object(watchers, "port_owner", return_value="steamwebhelper"):
                e = watchers.diagnose(other, "http://localhost:8080", [conf])
                self.assertEqual((e.key, e.args_["prog"]), ("err_qbit_taken", "Steam"))
            with mock.patch.object(watchers, "port_owner", return_value="?"):
                self.assertEqual(watchers.diagnose(other, "http://localhost:8080", [conf]).key,
                                 "err_qbit_other_port")
            self.assertEqual(watchers.diagnose(other, "http://nas:8080", [conf]).key,
                             "err_qbit_other")
        self.assertEqual(watchers.diagnose(refused, "http://localhost:8080", []).key,
                         "err_qbit_connect")

    def test_unreachable(self):
        client = watchers.QbitClient(f"http://127.0.0.1:{watchers.free_port(59000)}",
                                     timeout=1)
        with mock.patch.object(watchers, "process_running", return_value=False):
            st = watchers.QbitWatcher(client).poll()
            self.assertFalse(st.running or st.error)
        with mock.patch.object(watchers, "process_running", return_value=True), \
                mock.patch.object(watchers, "QBIT_CONFIGS", []):
            self.assertEqual(watchers.QbitWatcher(client).poll().error, "err_qbit_connect")


if __name__ == "__main__":
    unittest.main()
