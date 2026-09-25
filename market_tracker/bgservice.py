"""Run Plumbline in the background whenever you're logged in: `mt service install`.

- macOS: a LaunchAgent (~/Library/LaunchAgents/com.plumbline.app.plist) that starts at login and
  restarts the app if it stops.
- Windows: a small script in your Startup folder that starts the app hidden at login. No
  administrator rights needed and no console window to close by accident.
- Linux: a systemd user service.

The app writes to plumbline.log in its folder. `mt service update` pulls the latest version,
reinstalls and restarts; `status`, `restart` and `uninstall` do what they say. Starting the app
the usual way (start.sh / start.bat) while the service runs just opens the browser.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

LABEL = "com.plumbline.app"
PID_FILE = "plumbline.pid"
LOG_FILE = "plumbline.log"


def root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def venv_bin(name: str) -> str:
    """A program from the same environment as this Python (the app's .venv)."""
    d = os.path.dirname(sys.executable)
    for cand in (name + ".exe", name) if os.name == "nt" else (name,):
        p = os.path.join(d, cand)
        if os.path.exists(p):
            return p
    return shutil.which(name) or os.path.join(d, name)


# ------------------------------------------------------------------ files each system needs

def _extra(lan: bool) -> list[str]:
    return ["--lan"] if lan else []


def mac_plist(mt: str, root: str, port: int, lan: bool = False) -> str:
    args = "".join(f"<string>{a}</string>" for a in _extra(lan))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{mt}</string><string>serve</string><string>--port</string><string>{port}</string>{args}</array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{root}/{LOG_FILE}</string><key>StandardErrorPath</key><string>{root}/{LOG_FILE}</string>
</dict></plist>
"""


def windows_vbs(pythonw: str, root: str, port: int, lan: bool = False) -> str:
    """Starts pythonw (no console) in the app folder; the app writes its own log and pid file."""
    q = lambda s: s.replace('"', '""')  # noqa: E731  (VBScript doubles quotes inside strings)
    flag = " --lan" if lan else ""
    return ("' Plumbline: starts the app in the background at login (mt service uninstall removes this file)\r\n"
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{q(root)}"\r\n'
            f'sh.Run """{q(pythonw)}"" -m market_tracker.cli serve --port {port}{flag} --log {LOG_FILE} --pidfile {PID_FILE}", 0, False\r\n')


def systemd_unit(mt: str, root: str, port: int, lan: bool = False) -> str:
    return (f"[Unit]\nDescription=Plumbline\n\n[Service]\nWorkingDirectory={root}\nExecStart={mt} serve --port {port}{' --lan' if lan else ''}\n"
            f"Restart=always\n\n[Install]\nWantedBy=default.target\n")


def windows_startup_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Plumbline.vbs")


def mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def systemd_path() -> str:
    return os.path.expanduser("~/.config/systemd/user/plumbline.service")


# ------------------------------------------------------------------ is it up?

def answering(port: int, timeout: float = 2.0) -> bool:
    """True when a Plumbline app answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=timeout) as r:
            return r.status == 200 and b"auth" in r.read(200)
    except OSError:
        return False


def wait_until_up(port: int, seconds: float = 30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if answering(port):
            return True
        time.sleep(1)
    return False


# ------------------------------------------------------------------ actions

def _kill_pidfile(root: str) -> None:
    path = os.path.join(root, PID_FILE)
    try:
        with open(path) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


def install(port: int = 8000, say=print, lan: bool = False) -> int:
    root, system = root_dir(), platform.system()
    if lan:
        from . import firstrun
        if not firstrun.get(firstrun.read_env(os.path.join(root, firstrun.ENV)), "APP_PASSWORD") and not os.environ.get("APP_PASSWORD"):
            say('--lan lets other devices on your network open Plumbline: add APP_PASSWORD="a-long-passphrase" to .env first.')
            return 2
    if system == "Darwin":
        plist = mac_plist_path()
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        with open(plist, "w") as fh:
            fh.write(mac_plist(venv_bin("mt"), root, port, lan))
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", plist], check=True)
    elif system == "Windows":
        vbs = windows_startup_path()
        os.makedirs(os.path.dirname(vbs), exist_ok=True)
        with open(vbs, "w", encoding="utf-8") as fh:
            fh.write(windows_vbs(venv_bin("pythonw"), root, port, lan))
        if not answering(port):
            subprocess.Popen(["wscript.exe", vbs])
    elif system == "Linux":
        unit = systemd_path()
        os.makedirs(os.path.dirname(unit), exist_ok=True)
        with open(unit, "w") as fh:
            fh.write(systemd_unit(venv_bin("mt"), root, port, lan))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "plumbline"], check=True)
    else:
        say(f"Not supported on {system}")
        return 1
    up = wait_until_up(port)
    say(f"Plumbline now runs in the background and starts when you log in: http://localhost:{port}")
    say("It's up and answering." if up else f"It didn't answer yet; see {os.path.join(root, LOG_FILE)}.")
    say("Stop it for good: mt service uninstall. Get the latest version: mt service update.")
    return 0 if up else 1


def uninstall(port: int = 8000, say=print) -> int:
    root, system = root_dir(), platform.system()
    if system == "Darwin":
        subprocess.run(["launchctl", "unload", "-w", mac_plist_path()], capture_output=True)
        if os.path.exists(mac_plist_path()):
            os.remove(mac_plist_path())
    elif system == "Windows":
        if os.path.exists(windows_startup_path()):
            os.remove(windows_startup_path())
        _kill_pidfile(root)
    elif system == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", "plumbline"], capture_output=True)
        if os.path.exists(systemd_path()):
            os.remove(systemd_path())
    say("Removed: Plumbline no longer starts at login. Your data stays where it was.")
    return 0


def restart(port: int = 8000, say=print) -> int:
    root, system = root_dir(), platform.system()
    if system == "Darwin":
        subprocess.run(["launchctl", "unload", mac_plist_path()], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", mac_plist_path()], check=True)
    elif system == "Windows":
        _kill_pidfile(root)
        for _ in range(20):
            if not answering(port, 0.5):
                break
            time.sleep(0.5)
        subprocess.Popen(["wscript.exe", windows_startup_path()])
    elif system == "Linux":
        subprocess.run(["systemctl", "--user", "restart", "plumbline"], check=True)
    up = wait_until_up(port)
    say("Restarted." if up else f"Restarted, but it isn't answering yet; see {os.path.join(root, LOG_FILE)}.")
    return 0 if up else 1


def update(port: int = 8000, say=print) -> int:
    root = root_dir()
    pulled = subprocess.run(["git", "-C", root, "pull", "--ff-only"], capture_output=True, text=True)
    say(pulled.stdout.strip() or pulled.stderr.strip() or "git pull done")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", root], check=True)
    return restart(port, say)


def status(port: int = 8000, say=print) -> int:
    system = platform.system()
    installed = {"Darwin": mac_plist_path, "Windows": windows_startup_path, "Linux": systemd_path}.get(system)
    inst = bool(installed and os.path.exists(installed()))
    up = answering(port)
    say(f"Starts at login: {'yes' if inst else 'no'}. Running now: {'yes, http://localhost:%d' % port if up else 'no'}.")
    return 0 if up else 1
