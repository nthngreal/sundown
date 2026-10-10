"""Window: pick an action (shut down, restart, sleep) and when: after Steam/qBittorrent
downloads finish, in N minutes, at a time, or now. A 60 s countdown with Cancel always
comes first."""
import html
import os
import sys
import time

from PySide6.QtCore import QSettings, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSpinBox,
                               QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget)

from . import desktop, power, watchers
from .i18n import LANGS, STRINGS

POLL_SEC = 3
COUNTDOWN_SEC = int(os.environ.get("SUNDOWN_COUNTDOWN", "60"))
MODES = ("downloads", "in", "at", "now")


def fmt_left(sec):
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class Poller(QThread):
    """Reads Steam and qBittorrent every few seconds off the GUI thread."""
    result = Signal(object, object)    # steam Status or None, qbit Status or None

    def __init__(self):
        super().__init__()
        self.steam = watchers.SteamWatcher()
        self.qbit = watchers.QbitWatcher(watchers.QbitClient())
        self.watch_steam = self.watch_qbit = True
        self._stop = self._kick = False

    def configure_qbit(self, url, user, password, stalled):
        self.qbit = watchers.QbitWatcher(watchers.QbitClient(url, user, password), stalled)
        self.kick()

    def kick(self):
        self._kick = True

    def stop(self):
        self._stop = True
        self.wait(7000)

    def run(self):
        while not self._stop:
            self._kick = False
            s = self.steam.poll() if self.watch_steam else None
            q = self.qbit.poll() if self.watch_qbit else None
            if self._stop:
                break
            self.result.emit(s, q)
            for _ in range(POLL_SEC * 10):
                if self._stop or self._kick:
                    break
                self.msleep(100)


class SleepInhibitor:
    """Keeps Plasma from suspending the computer while Sundown waits."""

    def __init__(self):
        self.cookie = None

    def _iface(self):
        return QDBusInterface("org.freedesktop.PowerManagement.Inhibit",
                              "/org/freedesktop/PowerManagement/Inhibit",
                              "org.freedesktop.PowerManagement.Inhibit",
                              QDBusConnection.sessionBus())

    def inhibit(self, reason):
        if self.cookie is not None:
            return
        iface = self._iface()
        if iface.isValid():
            reply = iface.call("Inhibit", "Sundown", reason)
            args = reply.arguments()
            if args:
                self.cookie = args[0]

    def release(self):
        if self.cookie is None:
            return
        iface = self._iface()
        if iface.isValid():
            iface.call("UnInhibit", self.cookie)
        self.cookie = None


class Countdown(QDialog):
    def __init__(self, win, action_text):
        super().__init__(win)
        self.win, self.action_text, self.left = win, action_text, COUNTDOWN_SEC
        self.setWindowTitle(win.tr_("countdown_title"))
        self.setWindowIcon(QIcon(desktop.ICON))
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, COUNTDOWN_SEC)
        self.bar.setTextVisible(False)
        btns = QDialogButtonBox()
        self.now_btn = btns.addButton(win.tr_("do_now", action=action_text),
                                      QDialogButtonBox.AcceptRole)
        self.cancel_btn = btns.addButton(win.tr_("cancel"), QDialogButtonBox.RejectRole)
        self.cancel_btn.setDefault(True)    # Enter or Esc never turns the computer off
        self.cancel_btn.setFocus()
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(btns)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.update_text()

    def update_text(self):
        self.label.setText("<h3>" + self.win.tr_("countdown_text", action=self.action_text,
                                                  sec=self.left) + "</h3>")
        self.bar.setValue(self.left)

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
        else:
            self.update_text()


class QbitDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        s = win.settings
        self.setWindowTitle(win.tr_("qbit_title"))
        lay = QVBoxLayout(self)
        help_ = QLabel(win.tr_("qbit_help"))
        help_.setWordWrap(True)
        lay.addWidget(help_)
        form = QFormLayout()
        self.url = QLineEdit(s.value("qbit_url", "http://localhost:8080"))
        self.user = QLineEdit(s.value("qbit_user", ""))
        self.password = QLineEdit(s.value("qbit_pass", ""))
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow(win.tr_("qbit_url"), self.url)
        form.addRow(win.tr_("qbit_user"), self.user)
        form.addRow(win.tr_("qbit_pass"), self.password)
        lay.addLayout(form)
        self.stalled = QCheckBox(win.tr_("qbit_stalled"))
        self.stalled.setChecked(s.value("qbit_stalled", False, type=bool))
        lay.addWidget(self.stalled)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton(win.tr_("qbit_test"))
        self.test_btn.clicked.connect(self.test)
        self.result = QLabel()
        self.result.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.result, 1)
        lay.addLayout(test_row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.setMinimumWidth(440)

    def test(self):
        client = watchers.QbitClient(self.url.text().strip(), self.user.text(),
                                     self.password.text(), timeout=3)
        try:
            n = len(client.torrents())
            self.result.setText(self.win.tr_("qbit_ok", n=n))
        except watchers.QbitError as e:
            self.result.setText(self.win.tr_(e.key))

    def save(self):
        s = self.win.settings
        s.setValue("qbit_url", self.url.text().strip() or "http://localhost:8080")
        s.setValue("qbit_user", self.user.text())
        s.setValue("qbit_pass", self.password.text())
        s.setValue("qbit_stalled", self.stalled.isChecked())


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("sundown", "sundown")
        self.lang = self.settings.value("lang") or "en"
        if self.lang not in LANGS:
            self.lang = "en"
        self.setWindowIcon(QIcon(desktop.ICON))
        self.setMinimumWidth(480)

        self.armed = None            # None, "downloads" or "timer"
        self.deadline = 0.0          # timer mode: time.time() to act at
        self.idle_since = None       # downloads mode: when everything went quiet
        self.countdown = None
        self.tray = None
        self.tray_told = False
        self.last = (None, None)
        self.inhibitor = SleepInhibitor()

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("<h3>Sundown</h3>"))
        top.addStretch()
        self.lang_label = QLabel()
        self.lang_box = QComboBox()
        for code, name in LANGS.items():
            self.lang_box.addItem(name, code)
        self.lang_box.setCurrentIndex(list(LANGS).index(self.lang))
        self.lang_box.currentIndexChanged.connect(self.set_lang)
        top.addWidget(self.lang_label)
        top.addWidget(self.lang_box)
        lay.addLayout(top)

        act_row = QHBoxLayout()
        self.action_label = QLabel()
        self.action_box = QComboBox()
        for a in power.ACTIONS:
            self.action_box.addItem("", a)
        a = self.settings.value("action", "poweroff")
        self.action_box.setCurrentIndex(power.ACTIONS.index(a) if a in power.ACTIONS else 0)
        act_row.addWidget(self.action_label)
        act_row.addWidget(self.action_box, 1)
        lay.addLayout(act_row)

        self.when_box = QGroupBox()
        g = QGridLayout(self.when_box)
        self.mode_group = QButtonGroup(self)
        self.radios = {}
        for i, m in enumerate(MODES):
            self.radios[m] = QRadioButton()
            self.mode_group.addButton(self.radios[m], i)

        # after downloads
        g.addWidget(self.radios["downloads"], 0, 0, 1, 4)
        self.steam_cb = QCheckBox()
        self.steam_cb.setChecked(self.settings.value("watch_steam", True, type=bool))
        self.steam_state = QLabel()
        self.qbit_cb = QCheckBox()
        self.qbit_cb.setChecked(self.settings.value("watch_qbit", True, type=bool))
        self.qbit_state = QLabel()
        for lbl in (self.steam_state, self.qbit_state):
            # One line under its checkbox; a long text must not stretch the window.
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl.setContentsMargins(24, 0, 0, 4)
        self.qbit_btn = QPushButton(QIcon.fromTheme("configure"), "")
        self.qbit_btn.clicked.connect(self.edit_qbit)
        g.addWidget(self.steam_cb, 1, 1, 1, 3)
        g.addWidget(self.steam_state, 2, 1, 1, 3)
        g.addWidget(self.qbit_cb, 3, 1, 1, 2)
        g.addWidget(self.qbit_btn, 3, 3)
        g.addWidget(self.qbit_state, 4, 1, 1, 3)
        grace_row = QHBoxLayout()
        self.grace_label = QLabel()
        self.grace = QSpinBox()
        self.grace.setRange(1, 120)
        self.grace.setValue(self.settings.value("grace", 2, type=int))
        grace_row.addWidget(self.grace_label)
        grace_row.addWidget(self.grace)
        grace_row.addStretch()
        g.addLayout(grace_row, 5, 1, 1, 3)

        # in N minutes
        g.addWidget(self.radios["in"], 6, 0, 1, 1)
        self.in_spin = QSpinBox()
        self.in_spin.setRange(1, 24 * 60)
        self.in_spin.setValue(self.settings.value("in_minutes", 30, type=int))
        in_row = QHBoxLayout()
        in_row.addWidget(self.in_spin)
        in_row.addStretch()
        g.addLayout(in_row, 6, 1, 1, 3)

        # at HH:MM
        g.addWidget(self.radios["at"], 7, 0, 1, 1)
        self.at_edit = QTimeEdit()
        self.at_edit.setDisplayFormat("HH:mm")
        t = QTime.fromString(self.settings.value("at_time", "23:30"), "HH:mm")
        self.at_edit.setTime(t if t.isValid() else QTime(23, 30))
        at_row = QHBoxLayout()
        at_row.addWidget(self.at_edit)
        at_row.addStretch()
        g.addLayout(at_row, 7, 1, 1, 3)

        g.addWidget(self.radios["now"], 8, 0, 1, 4)
        g.setColumnMinimumWidth(0, 24)
        g.setColumnStretch(2, 1)
        lay.addWidget(self.when_box)

        mode = self.settings.value("mode", "downloads")
        self.radios[mode if mode in MODES else "downloads"].setChecked(True)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.start_btn = QPushButton()
        self.start_btn.clicked.connect(self.toggle)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.start_btn)
        lay.addLayout(bottom)
        self.about = QLabel()
        self.about.setEnabled(False)
        self.about.setWordWrap(True)
        lay.addWidget(self.about)

        self.inputs = [self.action_box, self.steam_cb, self.qbit_cb, self.grace, self.in_spin,
                       self.at_edit, *self.radios.values()]
        for w in (self.steam_cb, self.qbit_cb):
            w.toggled.connect(self.sources_changed)
        self.mode_group.idToggled.connect(lambda *_: self.update_ui())
        self.in_spin.valueChanged.connect(lambda: self.radios["in"].setChecked(True))
        self.at_edit.timeChanged.connect(lambda: self.radios["at"].setChecked(True))

        self.poller = Poller()
        self.apply_qbit_settings()
        self.poller.result.connect(self.on_poll)
        self.sources_changed()
        self.poller.start()

        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start(1000)

        self.retranslate()

    # ---------------------------------------------------------- helpers
    def tr_(self, key, **kw):
        return STRINGS[self.lang][key].format(**kw)

    def action(self):
        return self.action_box.currentData()

    def action_text(self):
        return self.tr_("act_" + self.action())

    def mode(self):
        return MODES[self.mode_group.checkedId()]

    def set_lang(self, i):
        self.lang = self.lang_box.itemData(i)
        self.settings.setValue("lang", self.lang)
        self.retranslate()

    def retranslate(self):
        self.setWindowTitle("Sundown")
        self.lang_label.setText(self.tr_("language"))
        self.action_label.setText(self.tr_("action"))
        for i, a in enumerate(power.ACTIONS):
            self.action_box.setItemText(i, self.tr_("act_" + a))
        self.when_box.setTitle(self.tr_("when"))
        self.radios["downloads"].setText(self.tr_("mode_downloads"))
        self.radios["in"].setText(self.tr_("mode_in"))
        self.radios["at"].setText(self.tr_("mode_at"))
        self.radios["now"].setText(self.tr_("mode_now"))
        self.in_spin.setSuffix(" " + self.tr_("minutes"))
        self.grace.setSuffix(" " + self.tr_("minutes"))
        self.grace_label.setText(self.tr_("grace"))
        self.steam_cb.setText(self.tr_("watch_steam"))
        self.qbit_cb.setText(self.tr_("watch_qbit"))
        self.qbit_btn.setText(self.tr_("qbit_settings"))
        self.about.setText(f"<small>{self.tr_('about')}</small>")
        if self.tray:
            self.build_tray_menu()
        self.show_sources(*self.last)
        self.update_ui()

    def save_settings(self):
        s = self.settings
        s.setValue("action", self.action())
        s.setValue("mode", self.mode())
        s.setValue("watch_steam", self.steam_cb.isChecked())
        s.setValue("watch_qbit", self.qbit_cb.isChecked())
        s.setValue("grace", self.grace.value())
        s.setValue("in_minutes", self.in_spin.value())
        s.setValue("at_time", self.at_edit.time().toString("HH:mm"))

    # ---------------------------------------------------------- sources
    def apply_qbit_settings(self):
        s = self.settings
        self.poller.configure_qbit(s.value("qbit_url", "http://localhost:8080"),
                                   s.value("qbit_user", ""), s.value("qbit_pass", ""),
                                   s.value("qbit_stalled", False, type=bool))

    def edit_qbit(self):
        dlg = QbitDialog(self)
        if dlg.exec() == QDialog.Accepted:
            dlg.save()
            self.apply_qbit_settings()

    def sources_changed(self):
        self.poller.watch_steam = self.steam_cb.isChecked()
        self.poller.watch_qbit = self.qbit_cb.isChecked()
        self.poller.kick()
        self.update_ui()

    def describe(self, st):
        if st is None:
            return ""
        if st.error:
            return f"<span style='color:#d9534f'>{self.tr_(st.error)}</span>"
        if not st.running:
            return self.tr_("st_not_running")
        if not st.active:
            return self.tr_("st_idle")
        parts = []
        for it in st.items[:2]:
            name = it.name if len(it.name) <= 28 else it.name[:27] + "…"
            name = html.escape(name)
            parts.append(f"{name} {it.progress * 100:.0f}%" if it.progress >= 0 else name)
        if len(st.items) > 2:
            parts.append(self.tr_("st_more", n=len(st.items) - 2))
        text = self.tr_("st_active", items=", ".join(parts))
        if st.speed:
            text += " · " + self.tr_("st_speed", speed=fmt_bytes(st.speed))
        return f"<b>{text}</b>"

    def show_sources(self, steam, qbit):
        for lbl, st in ((self.steam_state, steam), (self.qbit_state, qbit)):
            text = self.describe(st)
            lbl.setText(text)
            lbl.setToolTip("<br>".join(f"{html.escape(i.name)} {i.progress * 100:.0f}%" if i.progress >= 0
                                       else html.escape(i.name) for i in st.items) if st else "")
            lbl.setVisible(bool(text))

    def on_poll(self, steam, qbit):
        # A poll that started before a checkbox changed may carry a stale source.
        steam = steam if self.steam_cb.isChecked() else None
        qbit = qbit if self.qbit_cb.isChecked() else None
        self.last = (steam, qbit)
        self.show_sources(steam, qbit)
        if self.armed != "downloads":
            return
        # An unreadable source counts as busy: better stay on than turn off too early.
        busy = any(st is not None and (st.active or st.error) for st in (steam, qbit))
        if busy:
            self.idle_since = None
        elif self.idle_since is None:
            self.idle_since = time.time()
        self.update_ui()

    # ---------------------------------------------------------- arming
    def toggle(self):
        if self.armed or self.countdown:
            self.disarm()
        else:
            self.arm()

    def arm(self):
        self.save_settings()
        mode = self.mode()
        if mode == "now":
            self.start_countdown()
            return
        if mode == "downloads":
            self.armed, self.idle_since = "downloads", None
            self.poller.kick()
        else:
            if mode == "in":
                self.deadline = time.time() + self.in_spin.value() * 60
            else:
                now = QTime.currentTime()
                secs = now.secsTo(self.at_edit.time())
                if secs <= 0:
                    secs += 24 * 3600
                self.deadline = time.time() + secs
            self.armed = "timer"
        self.inhibitor.inhibit(self.status_text())
        self.ensure_tray()
        self.update_ui()

    def disarm(self):
        self.armed, self.idle_since = None, None
        if self.countdown:
            cd, self.countdown = self.countdown, None
            cd.timer.stop()
            cd.reject()
        self.inhibitor.release()
        if self.tray:
            self.tray.hide()
        self.update_ui()

    def tick(self):
        if self.countdown:
            return
        if self.armed == "timer" and time.time() >= self.deadline:
            self.start_countdown()
        elif (self.armed == "downloads" and self.idle_since is not None
              and time.time() - self.idle_since >= self.grace.value() * 60):
            self.start_countdown()
        elif self.armed:
            self.update_ui()

    def start_countdown(self):
        action_text = self.action_text()
        self.countdown = Countdown(self, action_text)
        self.countdown.finished.connect(self.countdown_done)
        self.ensure_tray()
        if self.tray:
            self.tray.showMessage("Sundown",
                                  self.tr_("countdown_text", action=action_text,
                                           sec=COUNTDOWN_SEC),
                                  QIcon(desktop.ICON), COUNTDOWN_SEC * 1000)
        self.countdown.show()
        self.countdown.raise_()
        self.countdown.activateWindow()
        QApplication.alert(self.countdown)
        self.update_ui()

    def countdown_done(self, result):
        if self.countdown is None:       # cancelled through disarm()
            return
        self.countdown = None
        if result != QDialog.Accepted:
            self.disarm()
            return
        action = self.action()
        self.inhibitor.release()
        try:
            power.run(action)
        except power.PowerError as e:
            QMessageBox.warning(self, self.tr_("error"),
                                self.tr_("err_power") + (f"\n\n{e.detail}" if e.detail else ""))
        self.disarm()

    # ---------------------------------------------------------- UI state
    def status_text(self):
        act = self.action_text()
        if self.armed == "timer":
            return self.tr_("armed_timer", action=act, left=fmt_left(self.deadline - time.time()),
                            time=time.strftime("%H:%M", time.localtime(self.deadline)))
        if self.armed == "downloads":
            if self.idle_since is None:
                return self.tr_("armed_downloads", action=act)
            left = self.grace.value() * 60 - (time.time() - self.idle_since)
            return self.tr_("armed_quiet", action=act, left=fmt_left(left))
        if self.mode() == "downloads" and not (self.steam_cb.isChecked()
                                               or self.qbit_cb.isChecked()):
            return self.tr_("no_source")
        return self.tr_("idle_state")

    def update_ui(self):
        busy = bool(self.armed or self.countdown)
        for w in self.inputs:
            w.setEnabled(not busy)
        self.qbit_btn.setEnabled(True)
        no_source = self.mode() == "downloads" and not (self.steam_cb.isChecked()
                                                        or self.qbit_cb.isChecked())
        self.start_btn.setText(self.tr_("cancel") if busy else self.tr_("start"))
        self.start_btn.setIcon(QIcon.fromTheme("dialog-cancel" if busy else "system-shutdown"))
        self.start_btn.setEnabled(busy or not no_source)
        text = self.status_text()
        self.status.setText(f"<b>{text}</b>" if busy else text)
        if self.tray:
            self.tray.setToolTip("Sundown\n" + text)

    # ---------------------------------------------------------- tray
    def ensure_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if not self.tray:
            self.tray = QSystemTrayIcon(QIcon(desktop.ICON), self)
            self.tray.activated.connect(self.tray_clicked)
            self.build_tray_menu()
        self.tray.show()

    def build_tray_menu(self):
        menu = QMenu(self)
        menu.addAction(self.tr_("tray_show"), self.show_window)
        menu.addAction(self.tr_("cancel"), self.disarm)
        menu.addSeparator()
        menu.addAction(self.tr_("tray_quit"), self.quit_app)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu

    def tray_clicked(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.disarm()
        self.save_settings()
        self.poller.stop()
        QApplication.quit()

    def closeEvent(self, e):
        if (self.armed or self.countdown) and self.tray and self.tray.isVisible():
            e.ignore()
            self.hide()
            if not self.tray_told:
                self.tray.showMessage("Sundown", self.tr_("tray_hidden"),
                                      QIcon(desktop.ICON), 5000)
                self.tray_told = True
            return
        if self.armed or self.countdown:
            # No tray to live in: closing means giving up the wait.
            if QMessageBox.question(self, "Sundown", self.tr_("quit_armed")) != QMessageBox.Yes:
                e.ignore()
                return
        e.accept()
        self.quit_app()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("sundown")
    app.setDesktopFileName("sundown")
    app.setQuitOnLastWindowClosed(False)    # the tray keeps it alive while armed
    # A bundled Qt (AppImage) does not know the Plasma icon theme on its own.
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        for theme in ("breeze", "breeze-dark", "Adwaita"):
            if os.path.isdir(f"/usr/share/icons/{theme}"):
                QIcon.setThemeName(theme)
                break
    desktop.install_menu_entry()
    w = MainWindow()
    w.show()
    return app.exec()
