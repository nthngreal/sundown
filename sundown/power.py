"""Power actions through systemd-logind (systemctl). No sudo: logind lets the user of
the active local session power off, reboot and suspend. No GUI imports here."""
import os
import subprocess
import sys

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
        print(f"[dry run] systemctl {action}", flush=True)
        return
    try:
        r = subprocess.run(["systemctl", action], capture_output=True, text=True,
                           timeout=30, env=_system_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise PowerError(str(e)) from e
    if r.returncode != 0:
        raise PowerError((r.stderr or r.stdout).strip())
