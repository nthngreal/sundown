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
        dl = next(i for i in items if i.name == "Downloading")
        self.assertAlmostEqual(dl.progress, 0.25)

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

    def test_unreachable(self):
        client = watchers.QbitClient("http://127.0.0.1:1", timeout=1)
        with mock.patch.object(watchers, "process_running", return_value=False):
            st = watchers.QbitWatcher(client).poll()
            self.assertFalse(st.running or st.error)
        with mock.patch.object(watchers, "process_running", return_value=True):
            self.assertEqual(watchers.QbitWatcher(client).poll().error, "err_qbit_connect")


if __name__ == "__main__":
    unittest.main()
