"""`mt doctor`: what's wrong with this install, and the one line that fixes it.

Built from the failures seen on real machines: an update that couldn't replace a running mt.exe
and left the app uninstalled; files made from an administrator window that a normal window then
can't change; a second copy (or another program) on the port; an app that starts hidden and never
answers. Also the things that quietly cost data: no off-site backup, no phone alerts, an old version.

Each check is {key, name, level, text, fix}: level "ok", "warn" (works, but fix it), "fail"
(broken) or "info". Run it even when `mt` itself is broken, from the app's folder:
  Windows:      .venv\\Scripts\\python -m market_tracker.cli doctor
  Mac / Linux:  .venv/bin/python -m market_tracker.cli doctor
It never prints secrets (the ntfy topic, keys, passwords): only whether they're set.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import sysconfig
import tempfile

WIN = os.name == "nt"
LOG_LINES = 15


def _c(key, name, level, text, fix=""):
    return {"key": key, "name": name, "level": level, "text": text, "fix": fix}


def venv_python(root: str) -> str:
    return r".venv\Scripts\python" if WIN else ".venv/bin/python"


def check_python() -> dict:
    v = sys.version_info
    if v < (3, 11):
        return _c("python", "Python", "fail", f"Python {v.major}.{v.minor} is too old.", "Install Python 3.12 or newer from python.org.")
    return _c("python", "Python", "ok", f"Python {v.major}.{v.minor}.{v.micro}")


def check_installed(root: str) -> dict:
    fix = f"{venv_python(root)} -m pip install -e ."
    try:
        dist = importlib.metadata.distribution("market-tracker")
    except importlib.metadata.PackageNotFoundError:
        return _c("installed", "Install", "fail", "The app isn't installed in this environment (an update that couldn't finish removes it).",
                  f"Close any window running Plumbline, then from the app's folder: {fix}")
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        info = {}
    url = info.get("url", "")
    if url.startswith("file:"):
        from urllib.parse import unquote, urlparse
        where = unquote(urlparse(url).path)
        if WIN and where.startswith("/") and len(where) > 2 and where[2] == ":":
            where = where[1:]
        if os.path.normcase(os.path.abspath(where)) != os.path.normcase(os.path.abspath(root)):
            return _c("installed", "Install", "warn", f"The app is installed from another folder ({where}), so changes here don't take effect.",
                      f"From this folder: {fix}")
    missing = [m for m in ("fastapi", "uvicorn", "httpx") if importlib.util.find_spec(m) is None]
    if missing:
        return _c("installed", "Install", "fail", f"Missing requirements: {', '.join(missing)}.", f"From the app's folder: {fix}")
    return _c("installed", "Install", "ok", f"Installed (version {dist.version}) from this folder")


def check_setup(root: str) -> dict:
    from . import firstrun
    if not os.path.exists(os.path.join(root, firstrun.ENV)):
        if os.environ.get("REQUIRE_LOGIN"):            # a server install: settings come from the host's secrets
            return _c("setup", "Setup", "ok", "Configured by the host's settings")
        return _c("setup", "Setup", "fail", "Not set up yet (no .env in the app's folder).",
                  "Double-click start.bat (Windows) or run ./start.sh: it asks two questions.")
    return _c("setup", "Setup", "ok", "Set up (.env found)")


def _port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_running(port: int, answering=None, taken=None) -> dict:
    from . import bgservice
    if (answering or bgservice.answering)(port):
        return _c("running", "Running", "ok", f"Running at http://localhost:{port}")
    if (taken or _port_taken)(port):
        return _c("running", "Running", "fail", f"Another program is using port {port}, so Plumbline can't start there.",
                  f"Start Plumbline on another port: mt serve --port {port + 1} --open (and make the icon match: mt shortcut --port {port + 1}).")
    return _c("running", "Running", "info", "Not running right now.",
              "Double-click the Plumbline icon (or start.bat). If it doesn't open, the log below says why.")


def check_mt_exe_lock(root: str) -> dict | None:
    """Windows: a running mt.exe can't be replaced, so an update would fail half way."""
    if not WIN:
        return None
    exe = os.path.join(os.path.dirname(sys.executable), "mt.exe")
    if not os.path.exists(exe):
        return None
    try:
        with open(exe, "r+b"):
            pass
    except PermissionError:
        return _c("mt_exe", "Updates", "fail", "A copy of Plumbline started as mt.exe is running (an old start.bat window?). "
                  "Updating now would fail and leave the app uninstalled.",
                  "Close that window, or run: taskkill /IM mt.exe /F")
    except OSError:
        return None
    return _c("mt_exe", "Updates", "ok", "Nothing is holding the app's files open")


def _writable(folder: str) -> bool:
    try:
        fd, path = tempfile.mkstemp(dir=folder, prefix=".doctor-")
        os.close(fd)
        os.remove(path)
        return True
    except PermissionError:
        return False
    except OSError:
        return True                                  # not a permissions problem (read-only medium etc.): not ours to report


def check_permissions(root: str) -> dict | None:
    if not os.path.isdir(os.path.join(root, ".git")):
        return None                                  # a server install's files are read-only on purpose
    blocked = [f for f in (root, sysconfig.get_paths()["purelib"]) if os.path.isdir(f) and not _writable(f)]
    if not blocked:
        return _c("permissions", "Permissions", "ok", "This account can change the app's files")
    fix = ("Open PowerShell as administrator once and run: icacls \"" + blocked[0] + "\" /grant \"${env:USERNAME}:(OI)(CI)F\" /T"
           if WIN else f"sudo chown -R $(whoami) \"{blocked[0]}\"")
    return _c("permissions", "Permissions", "fail",
              f"This account can't change files in {blocked[0]} (usually: they were made from an administrator window). "
              "Updates and backups will fail.", fix)


def check_elevated() -> dict | None:
    if not WIN:
        return None
    try:
        import ctypes
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None
    if admin:
        return _c("elevated", "Window", "warn", "This window is running as administrator: files it makes can later refuse to update.",
                  "Close it and open PowerShell from the Start menu without \"Run as administrator\".")
    return None


def check_database(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return _c("database", "Data", "info", "No data yet: import your accounts (Portfolio → Import).")
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='transactions'").fetchone() else 0
    except sqlite3.Error as exc:
        return _c("database", "Data", "fail", f"The database can't be read: {exc}", "Restore last night's backup (Portfolio → Accounts → Off-site backup).")
    if not ok:
        return _c("database", "Data", "fail", "The database failed its integrity check.",
                  "Restore last night's backup (Portfolio → Accounts → Off-site backup).")
    size = os.path.getsize(db_path) / 1e6
    return _c("database", "Data", "ok", f"{n:,} transaction{'s' if n != 1 else ''} in {os.path.basename(db_path)} ({size:.1f} MB), intact")


def check_backup(last: dict | None, suggest: dict | None) -> dict:
    from . import offsite
    if not offsite.configured():
        where = f" ({suggest['service']} found: {suggest['path']})" if suggest else ""
        return _c("backup", "Backup", "warn", "No off-site backup: if this computer dies, your ledger goes with it.",
                  "Portfolio → Accounts → Off-site backup" + where + ".")
    if not last:
        return _c("backup", "Backup", "ok", "Off-site backup set up; the first copy runs tonight")
    if not last.get("ok"):
        return _c("backup", "Backup", "fail", f"The last off-site backup failed: {last.get('error')}", "Portfolio → Accounts → Off-site backup.")
    return _c("backup", "Backup", "ok", f"Last off-site copy {last['at'][:16].replace('T', ' ')} UTC")


def check_alerts() -> dict:
    if os.environ.get("NTFY_TOPIC"):
        return _c("alerts", "Phone alerts", "ok", "Phone alerts set up (ntfy)")
    return _c("alerts", "Phone alerts", "warn", "No phone alerts: decisions and problems won't reach you.",
              "Portfolio → Accounts → Setup, the phone alerts step.")


def check_update(st: dict | None) -> dict:
    if not st or not st.get("supported"):
        return _c("update", "Version", "info", (st or {}).get("text") or "Can't check for a new version.")
    if st.get("ahead"):
        return _c("update", "Version", "info", st["text"])
    if st.get("can_update"):
        return _c("update", "Version", "warn", st["text"] + f" You're on {st['version']}.",
                  "Home → Update now (or double-click start.bat).")
    return _c("update", "Version", "ok" if st.get("fetched") is not False else "info", st["text"])


def log_tail(root: str, n: int = LOG_LINES) -> dict:
    """The end of plumbline.log, and the last error in it."""
    from . import bgservice
    path = os.path.join(root, bgservice.LOG_FILE)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[-400:]
    except OSError:
        return {"path": path, "lines": [], "error": None}
    err = next((ln for ln in reversed(lines) if ln.startswith(("Traceback", "ERROR")) or "Error:" in ln), None)
    return {"path": path, "lines": lines[-n:], "error": err}


def run(port: int = 8000, root: str | None = None, fetch: bool = True, in_app: bool = False) -> dict:
    from . import bgservice, config, db, offsite, updater
    root = root or bgservice.root_dir()
    checks = [check_python(), check_installed(root), check_setup(root)]
    if not in_app:
        checks.append(check_running(port))
    checks += [c for c in (check_mt_exe_lock(root), check_elevated()) if c]
    perms = check_permissions(root)
    if perms:
        checks.append(perms)
    db_path = config.settings.db_path
    checks.append(check_database(os.path.abspath(db_path)))
    last = None
    try:
        with db.connect() as conn:
            last = json.loads(db.get_meta(conn, "offsite_last", "null") or "null")
    except Exception:  # noqa: BLE001 - a broken database is reported above
        pass
    checks.append(check_backup(last, offsite.suggested_dir()))
    checks.append(check_alerts())
    checks.append(check_update(updater.cached_status() if in_app else updater.status(root, fetch=fetch)))
    log = log_tail(root)
    if log["error"]:
        checks.append(_c("log", "Log", "warn", f"Last error in the log: {log['error'][:200]}",
                         "If the app misbehaves, send the log lines below to whoever helps you."))
    worst = "fail" if any(c["level"] == "fail" for c in checks) else "warn" if any(c["level"] == "warn" for c in checks) else "ok"
    return {"root": root, "checks": checks, "log": log, "worst": worst,
            "version": updater.RUNNING if in_app and updater.RUNNING is not None else updater.version(root)}


def report(result: dict) -> str:
    """Plain ASCII (a Windows console may not print symbols)."""
    tag = {"ok": "[ok]  ", "warn": "[!]   ", "fail": "[FAIL]", "info": "[--]  "}
    out = [f"Plumbline doctor ({result['root']}, version {result['version'] or 'unknown'})", ""]
    for c in result["checks"]:
        out.append(f"{tag[c['level']]} {c['name']}: {c['text']}")
        if c["fix"] and c["level"] != "ok":
            out.append(f"        Fix: {c['fix']}")
    if result["log"]["lines"]:
        out += ["", f"Last lines of {result['log']['path']}:"] + ["  " + ln for ln in result["log"]["lines"]]
    out += ["", {"ok": "All good.", "warn": "Works; the [!] lines are worth fixing.", "fail": "Fix the [FAIL] lines first, top to bottom."}[result["worst"]]]
    text = "\n".join(out).replace("→", ">").replace("…", "...")
    return text.encode("ascii", "replace").decode("ascii")
