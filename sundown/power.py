"""Power actions. No GUI imports here.

Linux: systemd-logind (systemctl). No sudo: logind lets the user of the active local
session power off, reboot and suspend.
Windows: shutdown.exe for power off and restart, SetSuspendState for sleep."""
import os
import subprocess
import sys

WINDOWS = sys.platform == "win32"
ACTIONS = ("poweroff", "reboot", "suspend")


class PowerError(Exception):
    def __init__(self, detail=""):
        super().__init__(detail)
        self.detail = detail


def _system_env():
    """Environment for system tools. A frozen build (AppImage) points LD_LIBRARY_PATH and
    QT_PLUGIN_PATH at its bundled libraries; systemctl must not load those."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
        for k in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            env.pop(k, None)
    return env


def run(action):
    if action not in ACTIONS:
        raise ValueError(action)
    if os.environ.get("SUNDOWN_DRY_RUN"):     # for testing: print instead of acting
        print(f"[dry run] {action}", flush=True)
        return
    if WINDOWS:
        _run_windows(action)
        return
    try:
        r = subprocess.run(["systemctl", action], capture_output=True, text=True,
                           timeout=30, env=_system_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise PowerError(str(e)) from e
    if r.returncode != 0:
        raise PowerError((r.stderr or r.stdout).strip())


def _run_windows(action):
    import ctypes
    if action == "suspend":
        # Hibernate=False, ForceCritical=False, DisableWakeEvent=False. Called
        # through ctypes (not rundll32), so it sleeps even with hibernation on.
        if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
            raise PowerError(f"SetSuspendState failed ({ctypes.GetLastError()})")
        return
    # /f: closes apps without asking, like systemctl on Linux; otherwise one app with
    # an unsaved document would keep an unattended computer on all night.
    args = ["shutdown", "/s" if action == "poweroff" else "/r", "/t", "0", "/f"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise PowerError(str(e)) from e
    if r.returncode != 0:
        raise PowerError((r.stderr or r.stdout).strip())


class KeepAwake:
    """Stops the computer from going to sleep on its own while Sundown waits.
    Linux: org.freedesktop.PowerManagement.Inhibit (Plasma) over D-Bus.
    Windows: SetThreadExecutionState, called from the GUI thread."""

    def __init__(self):
        self.cookie = None

    def _iface(self):
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
        return QDBusInterface("org.freedesktop.PowerManagement.Inhibit",
                              "/org/freedesktop/PowerManagement/Inhibit",
                              "org.freedesktop.PowerManagement.Inhibit",
                              QDBusConnection.sessionBus())

    def inhibit(self, reason):
        if self.cookie is not None:
            return
        if WINDOWS:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            if ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS
                                                               | ES_SYSTEM_REQUIRED):
                self.cookie = True
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
        if WINDOWS:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        else:
            iface = self._iface()
            if iface.isValid():
                iface.call("UnInhibit", self.cookie)
        self.cookie = None
