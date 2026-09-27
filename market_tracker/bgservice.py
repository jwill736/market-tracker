"""Run Plumbline in the background whenever you're logged in: `mt service install`.

- macOS: a LaunchAgent (~/Library/LaunchAgents/com.plumbline.app.plist) that starts at login and
  restarts the app if it stops.
- Windows: a small script in your Startup folder that starts the app hidden at login. No
  administrator rights needed and no console window to close by accident.
- Linux: a systemd user service.

The app writes to plumbline.log in its folder. `mt service update` pulls the latest version,
reinstalls and restarts; `status`, `restart` and `uninstall` do what they say. Starting the app
the usual way (start.sh / start.bat) while the service runs just opens the browser.

Windows desktop icon: `mt shortcut` puts a Plumbline icon on the Desktop and in the Start menu. It
starts the app hidden if it isn't running (no console window to keep open) and opens the browser;
if it's already running it just opens the browser. "Stop Plumbline" in the Start menu (or `mt stop`)
stops it. The icon skips start.bat's update step: run start.bat now and then to get new versions.
"""

from __future__ import annotations

import base64
import os
import platform
import struct
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


# ------------------------------------------------------------------ Windows desktop icon

ICON_FILE = "plumbline.ico"
SERVE_ARGS = "-m market_tracker.cli serve --open --port {port} --log " + LOG_FILE + " --pidfile " + PID_FILE


def ico_from_png(png: bytes) -> bytes:
    """A Windows .ico holding one PNG image (Windows Vista and later read these), no imaging library needed."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    w, h = struct.unpack(">II", png[16:24])
    if w > 256 or h > 256:
        raise ValueError("an icon image is at most 256 pixels")
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def shortcut_script(pythonw: str, root: str, icon: str, port: int, remove: bool = False) -> str:
    """PowerShell that makes (or removes) the Desktop and Start menu shortcuts; prints each path it touched."""
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731  (PowerShell doubles quotes inside '...')
    lines = ["$ErrorActionPreference = 'Stop'",
             "$desk = [Environment]::GetFolderPath('Desktop')",          # follows OneDrive's moved Desktop
             "$menu = [Environment]::GetFolderPath('Programs')",
             "$made = @(@((Join-Path $desk 'Plumbline.lnk'), " + q(SERVE_ARGS.format(port=port)) + ", 'Open Plumbline'),",
             "          @((Join-Path $menu 'Plumbline.lnk'), " + q(SERVE_ARGS.format(port=port)) + ", 'Open Plumbline'),",
             "          @((Join-Path $menu 'Stop Plumbline.lnk'), " + q(f"-m market_tracker.cli stop --port {port}") + ", 'Stop Plumbline'))"]
    if remove:
        lines += ["foreach ($m in $made) { if (Test-Path -LiteralPath $m[0]) { Remove-Item -LiteralPath $m[0]; Write-Output $m[0] } }"]
    else:
        lines += ["$sh = New-Object -ComObject WScript.Shell",
                  "foreach ($m in $made) {",
                  "  $s = $sh.CreateShortcut($m[0])",
                  f"  $s.TargetPath = {q(pythonw)}",
                  "  $s.Arguments = $m[1]",
                  f"  $s.WorkingDirectory = {q(root)}",
                  f"  $s.IconLocation = {q(icon + ',0')}",
                  "  $s.Description = $m[2]",
                  "  $s.Save()",
                  "  Write-Output $m[0]",
                  "}"]
    body = "\r\n".join("  " + x for x in lines[1:])
    # Errors come back as plain text on stdout (redirected PowerShell errors arrive as unreadable CLIXML otherwise).
    return lines[0] + "\r\ntry {\r\n" + body + "\r\n} catch { Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }\r\n"


def _powershell(script: str) -> subprocess.CompletedProcess:
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")   # no quoting problems with spaces or quotes in paths
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], capture_output=True, text=True)


def shortcut(port: int = 8000, say=print, remove: bool = False) -> int:
    root = root_dir()
    if platform.system() != "Windows":
        say(f"mt shortcut makes a Windows desktop icon. On a Mac or Linux: mt service install, then bookmark http://localhost:{port}.")
        return 1
    from . import firstrun
    if not remove and not os.path.exists(os.path.join(root, firstrun.ENV)):
        say("Run start.bat once first: it sets the app up. Then run this again.")
        return 2
    pythonw = venv_bin("pythonw")
    icon = os.path.join(root, ICON_FILE)
    if not remove:
        with open(os.path.join(root, "market_tracker", "static", "icon-192.png"), "rb") as fh:
            png = fh.read()
        with open(icon, "wb") as fh:
            fh.write(ico_from_png(png))
    try:
        done = _powershell(shortcut_script(pythonw, root, icon, port, remove))
    except OSError as e:
        say(f"Couldn't run PowerShell: {e}")
        return 1
    lines = [x.strip() for x in done.stdout.splitlines() if x.strip()]
    if done.returncode != 0:
        err = next((x[7:] for x in lines if x.startswith("ERROR: ")), "PowerShell stopped (exit code %d)" % done.returncode)
        say("Couldn't " + ("remove" if remove else "make") + " the shortcuts: " + err)
        return 1
    paths = lines
    if remove:
        say("Removed: " + (", ".join(paths) if paths else "there were no Plumbline shortcuts."))
        return 0
    say("Made: " + ", ".join(paths))
    say("Double-click Plumbline on your Desktop: it starts the app in the background (no window to keep open) and opens it in "
        "your browser. Stop it with Stop Plumbline in the Start menu. To pin it to the taskbar: Start → Plumbline → right-click → Pin to taskbar.")
    say("The icon doesn't update the app; double-click start.bat now and then for new versions (then close its window: the icon keeps working).")
    return 0


def _pid(root: str) -> int | None:
    try:
        with open(os.path.join(root, PID_FILE)) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _is_python(pid: int) -> bool:
    """Whether that process is a Python (the app) and not something that reused an old process id."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
        return out.strip().strip('"').lower().startswith("python")
    out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return "market_tracker" in out or "mt serve" in out


def stop(port: int = 8000, say=print) -> int:
    """Stop the app started by the icon or the service (it starts again at next login if the service is installed)."""
    root = root_dir()
    if not answering(port):
        say("Plumbline isn't running.")
        return 0
    pid = _pid(root)
    if pid is None or not _is_python(pid):   # a stale pid file must never stop some other program
        say("Plumbline is running, but not in a way this can stop: close the window it runs in (or mt service uninstall).")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(20):
        if not answering(port, 0.5):
            say("Stopped.")
            return 0
        time.sleep(0.5)
    say(f"It's still answering on port {port}: if you started it with start.bat, close that window.")
    return 1
