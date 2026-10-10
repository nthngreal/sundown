"""Window: pick an action (shut down, restart, sleep) and when: after Steam/qBittorrent
downloads finish, in N minutes, at a time, or now. A 60 s countdown with Cancel always
comes first."""
import html
import os
import sys
import time

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QSpinBox,
                               QStackedWidget, QSystemTrayIcon, QTimeEdit, QToolButton,
                               QVBoxLayout, QWidget)

from . import desktop, power, theme, watchers
from .i18n import LANGS, STRINGS

POLL_SEC = 3
COUNTDOWN_SEC = int(os.environ.get("SUNDOWN_COUNTDOWN", "60"))
MODES = ("downloads", "in", "at", "now")
ACTION_ICONS = {"poweroff": "power.svg", "reboot": "restart.svg", "suspend": "moon.svg"}
PRESETS = (15, 30, 60, 120)


def label(text="", name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def card(name="card"):
    f = QFrame()
    f.setObjectName(name)
    return f


def divider():
    f = QFrame()
    f.setObjectName("divider")
    return f


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
        self.qbit_url = "http://localhost:8080"
        self.qbit = watchers.QbitWatcher(watchers.QbitClient())
        self.watch_steam = self.watch_qbit = True
        self._stop = self._kick = False

    def configure_qbit(self, url, user, password, stalled):
        self.qbit_url = url
        self.qbit = watchers.QbitWatcher(
            watchers.QbitClient(watchers.effective_qbit_url(url), user, password), stalled)
        self.kick()

    def follow_qbit_port(self):
        """Pick up a Web UI port changed in qBittorrent while Sundown runs."""
        url = watchers.effective_qbit_url(self.qbit_url)
        c = self.qbit.client
        if url.rstrip("/") != c.url:
            self.qbit.client = watchers.QbitClient(url, c.username, c.password)

    def kick(self):
        self._kick = True

    def stop(self):
        self._stop = True
        self.wait(7000)

    def run(self):
        while not self._stop:
            self._kick = False
            s = self.steam.poll() if self.watch_steam else None
            if self.watch_qbit:
                self.follow_qbit_port()
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
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(6)
        self.caption = label(win.tr_("in_label", action=action_text).upper(), "section")
        self.caption.setAlignment(Qt.AlignCenter)
        self.number = label("", "big")
        self.number.setAlignment(Qt.AlignCenter)
        self.unit = label(win.tr_("seconds"), "muted")
        self.unit.setAlignment(Qt.AlignCenter)
        self.bar = QProgressBar()
        self.bar.setObjectName("countdown")
        self.bar.setRange(0, COUNTDOWN_SEC)
        self.bar.setTextVisible(False)
        self.cancel_btn = QPushButton(win.tr_("cancel"))
        self.cancel_btn.setObjectName("primary")
        self.cancel_btn.setDefault(True)    # Enter or Esc never turns the computer off
        self.now_btn = QPushButton(win.tr_("do_now", action=action_text))
        self.cancel_btn.clicked.connect(self.reject)
        self.now_btn.clicked.connect(self.accept)
        lay.addWidget(self.caption)
        lay.addWidget(self.number)
        lay.addWidget(self.unit)
        lay.addSpacing(10)
        lay.addWidget(self.bar)
        lay.addSpacing(14)
        lay.addWidget(self.cancel_btn)
        lay.addWidget(self.now_btn)
        self.cancel_btn.setFocus()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.update_text()

    def update_text(self):
        self.number.setText(str(self.left))
        self.bar.setValue(self.left)

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
        else:
            self.update_text()


class SourceRow(QWidget):
    """One watched program: checkbox, state chip, what is downloading, progress."""

    def __init__(self, extra=None):
        super().__init__()
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(6)
        self.check = QCheckBox()
        self.check.setObjectName("source")
        self.chip = label()
        self.detail = label("", "detail")
        # One line; a long text must not stretch the window.
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail.setContentsMargins(30, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        bar_box = QHBoxLayout()
        bar_box.setContentsMargins(30, 0, 0, 0)
        bar_box.addWidget(self.bar)
        g.addWidget(self.check, 0, 0)
        g.setColumnStretch(1, 1)
        if extra:
            g.addWidget(extra, 0, 2)
        g.addWidget(self.chip, 0, 3)
        g.addWidget(self.detail, 1, 0, 1, 4)
        g.addLayout(bar_box, 2, 0, 1, 4)
        # Keep the rows' space when hidden, so the window does not jump as downloads
        # start and stop.
        for w in (self.detail, self.bar):
            pol = w.sizePolicy()
            pol.setRetainSizeWhenHidden(True)
            w.setSizePolicy(pol)
            w.hide()

    def show_status(self, win, st):
        if st is None:
            self.chip.clear()
            self.detail.hide()
            self.bar.hide()
            self.setToolTip("")
            return
        if st.error:
            chip, color, detail = "chip_err", theme.ERR, win.tr_(st.error)
        elif not st.running:
            chip, color, detail = "chip_off", theme.MUTED, ""
        elif not st.active:
            chip, color, detail = "chip_idle", theme.MUTED, ""
        else:
            chip, color, detail = "chip_active", theme.OK, win.describe_items(st)
        self.chip.setText(f"<span style='color:{color}'>●</span>&nbsp;"
                          f"<span style='color:{color if chip != 'chip_off' else theme.MUTED}'>"
                          f"{win.tr_(chip)}</span>")
        self.detail.setText(f"<span style='color:{theme.ERR}'>{detail}</span>"
                            if st.error else detail)
        self.detail.setVisible(bool(detail))
        first = st.items[0] if st.active and st.items else None
        self.bar.setVisible(bool(first and first.progress >= 0))
        if first and first.progress >= 0:
            self.bar.setValue(int(first.progress * 1000))
        if st.error:
            self.setToolTip(detail + (f"<br><small>{html.escape(st.detail)}</small>"
                                      if st.detail else ""))
            return
        self.setToolTip("<br>".join(f"{html.escape(i.name)} {i.progress * 100:.0f}%"
                                    if i.progress >= 0 else html.escape(i.name)
                                    for i in st.items))


class QbitDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        s = win.settings
        self.setWindowTitle(win.tr_("qbit_title"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        help_ = label(win.tr_("qbit_help"), "muted")
        help_.setWordWrap(True)
        lay.addWidget(help_)
        form = QFormLayout()
        self.url = QLineEdit(watchers.effective_qbit_url(s.value("qbit_url", "http://localhost:8080")))
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
        btns.button(QDialogButtonBox.Ok).setText(win.tr_("save"))
        btns.button(QDialogButtonBox.Ok).setObjectName("primary")
        btns.button(QDialogButtonBox.Cancel).setText(win.tr_("cancel"))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.setMinimumWidth(440)

    def test(self):
        client = watchers.QbitClient(watchers.effective_qbit_url(self.url.text().strip()),
                                     self.user.text(),
                                     self.password.text(), timeout=3)
        try:
            n = len(client.torrents())
            self.result.setText(self.win.tr_("qbit_ok", n=n))
        except watchers.QbitError as e:
            # The raw reason (e.g. "Connection refused") tells a closed Web UI from a
            # wrong port or address.
            text = self.win.tr_(e.key)
            if e.detail:
                text += f"<br><small>{html.escape(e.detail)}</small>"
            self.result.setText(text)

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
        self.setMinimumWidth(520)

        self.armed = None            # None, "downloads" or "timer"
        self.deadline = 0.0          # timer mode: time.time() to act at
        self.idle_since = None       # downloads mode: when everything went quiet
        self.countdown = None
        self.tray = None
        self.tray_told = False
        self.last = (None, None)
        self.inhibitor = SleepInhibitor()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)

        # header
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(desktop.ICON).pixmap(QSize(44, 44)))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Sundown", "title"))
        self.subtitle = label("", "muted", wrap=True)
        titles.addWidget(self.subtitle)
        self.lang_box = QComboBox()
        for code, name in LANGS.items():
            self.lang_box.addItem(name, code)
        self.lang_box.setCurrentIndex(list(LANGS).index(self.lang))
        self.lang_box.currentIndexChanged.connect(self.set_lang)
        top.addWidget(logo)
        top.addSpacing(6)
        top.addLayout(titles, 1)
        top.addSpacing(10)
        top.addWidget(self.lang_box, 0, Qt.AlignTop)
        lay.addLayout(top)
        lay.addSpacing(8)

        # action
        self.sec_action = label("", "section")
        lay.addWidget(self.sec_action)
        act_row = QHBoxLayout()
        act_row.setSpacing(10)
        self.action_group = QButtonGroup(self)
        self.action_btns = {}
        for i, a in enumerate(power.ACTIONS):
            b = QToolButton()
            b.setObjectName("action")
            b.setCheckable(True)
            b.setIcon(QIcon(theme.asset(ACTION_ICONS[a])))
            b.setIconSize(QSize(26, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setCursor(Qt.PointingHandCursor)
            self.action_group.addButton(b, i)
            self.action_btns[a] = b
            act_row.addWidget(b)
        a = self.settings.value("action", "poweroff")
        self.action_btns[a if a in power.ACTIONS else "poweroff"].setChecked(True)
        self.action_group.idToggled.connect(lambda *_: self.update_ui())
        lay.addLayout(act_row)
        lay.addSpacing(6)

        # when: segmented tabs over a stacked card
        self.sec_when = label("", "section")
        lay.addWidget(self.sec_when)
        segbar = card("segbar")
        seg_lay = QHBoxLayout(segbar)
        seg_lay.setContentsMargins(4, 4, 4, 4)
        seg_lay.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.tabs = {}
        for i, m in enumerate(MODES):
            b = QPushButton()
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(b, i)
            self.tabs[m] = b
            seg_lay.addWidget(b)
        lay.addWidget(segbar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_downloads_page())
        self.pages.addWidget(self.build_in_page())
        self.pages.addWidget(self.build_at_page())
        self.pages.addWidget(self.build_now_page())
        lay.addWidget(self.pages)

        # hero: what is armed and how long is left
        self.hero = card("hero")
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(2)
        self.hero_top = label("", "section")
        self.hero_big = label("", "big")
        self.hero_note = label("", "muted", wrap=True)
        for w in (self.hero_top, self.hero_big, self.hero_note):
            w.setAlignment(Qt.AlignCenter)
            hl.addWidget(w)
        self.hero.hide()
        lay.addWidget(self.hero)

        lay.addStretch()
        self.status = label("", "muted", wrap=True)
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status)
        self.start_btn = QPushButton()
        self.start_btn.setObjectName("primary")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self.toggle)
        lay.addWidget(self.start_btn)

        mode = self.settings.value("mode", "downloads")
        self.tabs[mode if mode in MODES else "downloads"].setChecked(True)
        self.pages.setCurrentIndex(self.mode_group.checkedId())
        self.mode_group.idToggled.connect(self.mode_changed)

        self.inputs = [*self.action_btns.values(), *self.tabs.values(), self.steam.check,
                       self.qbit.check, self.grace, self.in_spin, self.at_edit,
                       *self.preset_btns]
        for w in (self.steam.check, self.qbit.check):
            w.toggled.connect(self.sources_changed)

        self.poller = Poller()
        self.apply_qbit_settings()
        self.poller.result.connect(self.on_poll)
        self.sources_changed()
        self.poller.start()

        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start(1000)

        self.retranslate()
        self.start_btn.setFocus()

    # ---------------------------------------------------------- pages
    def build_downloads_page(self):
        page = card()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)
        self.steam = SourceRow()
        self.steam.check.setText("Steam")
        self.steam.check.setChecked(self.settings.value("watch_steam", True, type=bool))
        self.qbit_btn = QPushButton(QIcon(theme.asset("sliders.svg")), "")
        self.qbit_btn.setObjectName("preset")
        self.qbit_btn.setCursor(Qt.PointingHandCursor)
        self.qbit_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.qbit_btn.clicked.connect(self.edit_qbit)
        self.qbit = SourceRow(self.qbit_btn)
        self.qbit.check.setText("qBittorrent")
        self.qbit.check.setChecked(self.settings.value("watch_qbit", True, type=bool))
        v.addWidget(self.steam)
        v.addWidget(divider())
        v.addWidget(self.qbit)
        v.addWidget(divider())
        row = QHBoxLayout()
        self.grace_label = label("", "muted")
        self.grace = QSpinBox()
        self.grace.setRange(1, 120)
        self.grace.setValue(self.settings.value("grace", 2, type=int))
        row.addWidget(self.grace_label, 1)
        row.addWidget(self.grace)
        v.addLayout(row)
        return page

    def build_in_page(self):
        page = card()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)
        self.in_label = label("", "section")
        self.in_spin = QSpinBox()
        self.in_spin.setObjectName("bigspin")
        self.in_spin.setRange(1, 24 * 60)
        self.in_spin.setValue(self.settings.value("in_minutes", 30, type=int))
        self.in_spin.setAlignment(Qt.AlignCenter)
        presets = QHBoxLayout()
        presets.addStretch()
        self.preset_btns = []
        for n in PRESETS:
            b = QPushButton()
            b.setObjectName("preset")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=n: self.in_spin.setValue(n))
            self.preset_btns.append(b)
            presets.addWidget(b)
        presets.addStretch()
        v.addStretch()
        v.addWidget(self.in_label, 0, Qt.AlignCenter)
        v.addWidget(self.in_spin, 0, Qt.AlignCenter)
        v.addLayout(presets)
        v.addStretch()
        return page

    def build_at_page(self):
        page = card()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)
        self.at_label = label("", "section")
        self.at_edit = QTimeEdit()
        self.at_edit.setObjectName("bigspin")
        self.at_edit.setDisplayFormat("HH:mm")
        self.at_edit.setAlignment(Qt.AlignCenter)
        t = QTime.fromString(self.settings.value("at_time", "23:30"), "HH:mm")
        self.at_edit.setTime(t if t.isValid() else QTime(23, 30))
        v.addStretch()
        v.addWidget(self.at_label, 0, Qt.AlignCenter)
        v.addWidget(self.at_edit, 0, Qt.AlignCenter)
        v.addStretch()
        return page

    def build_now_page(self):
        page = card()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 22, 18, 22)
        self.now_hint = label("", "muted", wrap=True)
        self.now_hint.setAlignment(Qt.AlignCenter)
        v.addWidget(self.now_hint)
        return page

    def mode_changed(self, i, checked):
        if checked:
            self.pages.setCurrentIndex(i)
            self.update_ui()

    # ---------------------------------------------------------- helpers
    def tr_(self, key, **kw):
        return STRINGS[self.lang][key].format(**kw)

    def action(self):
        return power.ACTIONS[self.action_group.checkedId()]

    def action_text(self):
        return self.tr_("act_" + self.action())

    def mode(self):
        return MODES[max(self.mode_group.checkedId(), 0)]

    def set_lang(self, i):
        self.lang = self.lang_box.itemData(i)
        self.settings.setValue("lang", self.lang)
        self.retranslate()

    def retranslate(self):
        self.setWindowTitle("Sundown")
        self.subtitle.setText(self.tr_("subtitle"))
        self.sec_action.setText(self.tr_("sec_action"))
        self.sec_when.setText(self.tr_("sec_when"))
        for a, b in self.action_btns.items():
            b.setText(self.tr_("act_" + a))
        for m, b in self.tabs.items():
            b.setText(self.tr_("tab_" + m))
        self.qbit_btn.setText(self.tr_("qbit_settings"))
        self.grace_label.setText(self.tr_("grace"))
        self.grace.setSuffix(" " + self.tr_("minutes"))
        self.in_spin.setSuffix(" " + self.tr_("minutes"))
        for n, b in zip(PRESETS, self.preset_btns):
            b.setText(f"{n} {self.tr_('minutes')}")
        self.now_hint.setText(self.tr_("now_hint"))
        if self.tray:
            self.build_tray_menu()
        self.show_sources(*self.last)
        self.update_ui()

    def save_settings(self):
        s = self.settings
        s.setValue("action", self.action())
        s.setValue("mode", self.mode())
        s.setValue("watch_steam", self.steam.check.isChecked())
        s.setValue("watch_qbit", self.qbit.check.isChecked())
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
        self.poller.watch_steam = self.steam.check.isChecked()
        self.poller.watch_qbit = self.qbit.check.isChecked()
        self.poller.kick()
        steam, qbit = self.last
        self.last = (steam if self.steam.check.isChecked() else None,
                     qbit if self.qbit.check.isChecked() else None)
        self.show_sources(*self.last)
        self.update_ui()

    def describe_items(self, st):
        parts = []
        for it in st.items[:2]:
            name = it.name if len(it.name) <= 28 else it.name[:27] + "…"
            name = html.escape(name)
            parts.append(f"{name} · {it.progress * 100:.0f}%" if it.progress >= 0 else name)
        if len(st.items) > 2:
            parts.append(self.tr_("st_more", n=len(st.items) - 2))
        text = ", ".join(parts)
        if st.speed:
            text += " · " + self.tr_("st_speed", speed=fmt_bytes(st.speed))
        return text

    def show_sources(self, steam, qbit):
        self.steam.show_status(self, steam)
        self.qbit.show_status(self, qbit)

    def on_poll(self, steam, qbit):
        # A poll that started before a checkbox changed may carry a stale source.
        steam = steam if self.steam.check.isChecked() else None
        qbit = qbit if self.qbit.check.isChecked() else None
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
        if self.mode() == "downloads" and not (self.steam.check.isChecked()
                                               or self.qbit.check.isChecked()):
            return self.tr_("no_source")
        return self.tr_("idle_state")

    def watched_names(self):
        return ", ".join(n for n, cb in (("Steam", self.steam.check), ("qBittorrent", self.qbit.check))
                         if cb.isChecked())

    def update_ui(self):
        busy = bool(self.armed or self.countdown)
        for w in self.inputs:
            w.setEnabled(not busy)
        no_source = self.mode() == "downloads" and not self.watched_names()
        self.start_btn.setObjectName("danger" if busy else "primary")
        self.start_btn.setText(self.tr_("cancel") if busy else self.tr_("start"))
        self.start_btn.setEnabled(busy or not no_source)
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        act = self.action_text()
        self.in_label.setText(self.tr_("in_label", action=act).upper())
        self.at_label.setText(self.tr_("at_label", action=act).upper())

        self.hero.setVisible(bool(self.armed))
        if self.armed == "timer":
            self.hero_top.setText(self.tr_("in_label", action=act).upper())
            self.hero_big.setObjectName("big")
            self.hero_big.setText(fmt_left(self.deadline - time.time()))
            self.hero_note.setText(self.tr_("at_note", time=time.strftime(
                "%H:%M", time.localtime(self.deadline))))
        elif self.armed == "downloads" and self.idle_since is None:
            self.hero_top.setText(act.upper())
            self.hero_big.setObjectName("bigaction")
            self.hero_big.setText(self.tr_("hero_wait"))
            self.hero_note.setText(self.tr_("watching", names=self.watched_names()))
        elif self.armed == "downloads":
            left = self.grace.value() * 60 - (time.time() - self.idle_since)
            self.hero_top.setText(self.tr_("in_label", action=act).upper())
            self.hero_big.setObjectName("big")
            self.hero_big.setText(fmt_left(left))
            self.hero_note.setText(self.tr_("quiet_note"))
        self.hero_big.style().unpolish(self.hero_big)
        self.hero_big.style().polish(self.hero_big)

        self.status.setText(self.tr_("no_source") if no_source and not busy else "")
        self.status.setVisible(no_source and not busy)
        if self.tray:
            self.tray.setToolTip("Sundown\n" + self.status_text())

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
    theme.apply(app)
    # A bundled Qt (AppImage) does not know the Plasma icon theme on its own.
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        for name in ("breeze-dark", "breeze", "Adwaita"):
            if os.path.isdir(f"/usr/share/icons/{name}"):
                QIcon.setThemeName(name)
                break
    desktop.install_menu_entry()
    w = MainWindow()
    w.show()
    return app.exec()
