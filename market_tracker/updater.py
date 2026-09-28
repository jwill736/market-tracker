"""Update Plumbline from inside the app: a banner says a new version is out, one button installs it.

Only for a copy cloned with git (start.sh / start.bat, the desktop icon, `mt service`). A server
install (Fly.io, Docker) has no git checkout and updates with its deploy script instead.

Checking: `git fetch`, then how many commits this copy is behind the branch it tracks, with their
titles. Remembered for six hours so pages don't wait on the network.

Updating, in order, stopping at the first thing that fails:
1. fast-forward to the fetched version (never a merge: a copy with its own commits is left alone);
2. reinstall only when pyproject.toml changed (new dependencies or commands). The app is an
   editable install, so code changes need no reinstall. If the reinstall fails, the checkout goes
   back to the old version (`git reset --keep`, which never throws away your own edits) and the
   old version keeps running;
3. restart: a small hidden helper waits for this process to exit, then starts the app again with
   the same settings (port, log, pid file). Under `mt service` on a Mac or Linux the system's
   service manager restarts it instead. The open page reloads itself when the new version answers.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

CHECK_HOURS = 6
MARKER = "installed-pyproject.toml"      # start.bat's note of the pyproject.toml last installed (in .venv)
LAUNCH: dict | None = None               # how this process was started (set by `mt serve`)
RUNNING: str | None = None               # the version this process started with (the files on disk change during an update)
_cache: dict = {}


def root_dir() -> str:
    from . import bgservice
    return bgservice.root_dir()


def _git(args: list[str], root: str, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def supported(root: str) -> tuple[bool, str]:
    if not os.path.isdir(os.path.join(root, ".git")):
        return False, "This copy wasn't installed with git (a server install updates with its deploy script)."
    if not shutil.which("git"):
        return False, "Git isn't installed on this computer, so the app can't update itself."
    return True, ""


def status(root: str | None = None, fetch: bool = True, git=_git) -> dict:
    """{supported, version, behind, changes, ahead, fetched, text}."""
    root = root or root_dir()
    ok, why = supported(root)
    if not ok:
        return {"supported": False, "text": why, "behind": 0, "changes": []}
    fetched = git(["fetch", "-q"], root, timeout=30).returncode == 0 if fetch else None
    head = git(["rev-parse", "HEAD"], root).stdout.strip()
    up = git(["rev-parse", "@{u}"], root)
    if up.returncode != 0 or not head:
        return {"supported": False, "text": "This copy isn't following a branch on GitHub, so there's nothing to update from.",
                "behind": 0, "changes": [], "version": head[:7]}
    log = git(["log", "--format=%h %s", "-20", "HEAD..@{u}"], root).stdout.strip().splitlines()
    behind = int(git(["rev-list", "--count", "HEAD..@{u}"], root).stdout.strip() or 0)
    ahead = int(git(["rev-list", "--count", "@{u}..HEAD"], root).stdout.strip() or 0)
    changes = [{"id": ln[:7], "title": ln[8:].strip()} for ln in log if ln.strip()]
    if ahead:
        text = f"This copy has {ahead} change{'s' if ahead != 1 else ''} of its own, so it can't update itself: use git."
    elif behind:
        text = f"A new version is out: {behind} change{'s' if behind != 1 else ''}."
    elif fetched is False:
        text = f"Couldn't reach GitHub to check for a new version (you're on {head[:7]})."
    else:
        text = f"Up to date ({head[:7]})."
    return {"supported": True, "version": head[:7], "behind": behind, "ahead": ahead, "changes": changes, "fetched": fetched,
            "can_update": bool(behind and not ahead), "text": text,
            "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def version(root: str | None = None, git=_git) -> str:
    """This copy's version (the short commit id), '' when it isn't a git checkout."""
    root = root or root_dir()
    return git(["rev-parse", "--short=7", "HEAD"], root).stdout.strip() if supported(root)[0] else ""


def cached_status(refresh: bool = False, now: datetime | None = None, status_fn=None) -> dict:
    """status(), remembered CHECK_HOURS so pages don't wait on the network."""
    now = now or datetime.now(timezone.utc)
    hit = _cache.get("status")
    if hit and not refresh and now - hit[0] < timedelta(hours=CHECK_HOURS):
        return hit[1]
    st = (status_fn or status)()
    _cache["status"] = (now, st)
    return st


def _pip(root: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", root], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess([], 1, "", str(exc))


def _last_line(p: subprocess.CompletedProcess) -> str:
    lines = [x for x in (p.stderr or p.stdout or "").strip().splitlines() if x.strip()]
    return lines[-1][:300] if lines else "no details"


def apply(root: str | None = None, git=_git, pip=_pip) -> dict:
    """Fetch, fast-forward, reinstall if needed. {updated, from, to, reinstalled, text} or {updated: False, error}."""
    root = root or root_dir()
    st = status(root, fetch=True, git=git)
    if not st.get("supported"):
        return {"updated": False, "error": st["text"]}
    if st.get("ahead"):
        return {"updated": False, "error": st["text"]}
    if not st.get("behind"):
        return {"updated": False, "error": None, "text": st["text"]}
    old = git(["rev-parse", "HEAD"], root).stdout.strip()
    ff = git(["merge", "--ff-only", "-q", "@{u}"], root)
    if ff.returncode != 0:
        return {"updated": False, "error": "Couldn't update the files (" + _last_line(ff) + "). Nothing changed."}
    new = git(["rev-parse", "HEAD"], root).stdout.strip()
    reinstall = git(["diff", "--quiet", old, new, "--", "pyproject.toml"], root).returncode != 0
    if reinstall:
        done = pip(root)
        if done.returncode != 0:
            git(["reset", "--keep", old], root)
            return {"updated": False, "error": "Couldn't install the new version's requirements (" + _last_line(done)
                    + "). Went back to the version you had; it keeps running."}
        try:
            shutil.copyfile(os.path.join(root, "pyproject.toml"), os.path.join(sys.prefix, MARKER))
        except OSError:
            pass
    _cache.pop("status", None)
    return {"updated": True, "from": old[:7], "to": new[:7], "reinstalled": reinstall, "changes": st["changes"],
            "text": f"Updated {old[:7]} → {new[:7]}" + (" (and its requirements)" if reinstall else "") + "."}


# ------------------------------------------------------------------ restart

def serve_args(launch: dict) -> list[str]:
    """`mt serve` arguments to start again the way this process was started, hidden: always with a log
    and a pid file (so Stop Plumbline works), never opening another browser tab."""
    out = ["serve", "--port", str(launch["port"])]
    if launch.get("lan"):
        out.append("--lan")
    elif launch.get("host") and launch["host"] != "127.0.0.1":
        out += ["--host", launch["host"]]
    out += ["--log", launch.get("log") or "plumbline.log", "--pidfile", launch.get("pidfile") or "plumbline.pid"]
    return out


def managed_by_service() -> bool:
    """A Mac LaunchAgent or Linux systemd service restarts the app by itself when it exits."""
    from . import bgservice
    system = platform.system()
    return (system == "Darwin" and os.path.exists(bgservice.mac_plist_path())) or \
           (system == "Linux" and os.path.exists(bgservice.systemd_path()))


def helper_command(launch: dict) -> list[str]:
    from . import bgservice
    py = bgservice.venv_bin("pythonw") if os.name == "nt" else sys.executable
    return [py, "-m", "market_tracker.cli", "relaunch", "--port", str(launch["port"]), "--", *serve_args(launch)]


def restart(launch: dict | None = None, spawn=None, exit_fn=None, delay: float = 1.5) -> str:
    """Hand over to a fresh process and exit this one (after the reply has gone out). Returns how."""
    launch = launch or LAUNCH
    if not launch:
        return "manual"
    how = "service" if managed_by_service() else "helper"
    if how == "helper":
        cmd = helper_command(launch)
        if spawn:
            spawn(cmd)
        elif os.name == "nt":
            flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives this window
            subprocess.Popen(cmd, cwd=root_dir(), creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(cmd, cwd=root_dir(), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Updated: restarting Plumbline in the background. You can close this window.", flush=True)
    threading.Timer(delay, exit_fn or (lambda: os._exit(0))).start()
    return how


def relaunch(port: int, argv: list[str], answering=None, sleep=time.sleep, serve=None, wait: float = 30.0) -> int:
    """The helper: wait for the old process to stop answering, give a service manager a moment, then serve."""
    from . import bgservice
    answering = answering or bgservice.answering
    end = time.monotonic() + wait
    while answering(port, 0.5) and time.monotonic() < end:
        sleep(0.5)
    sleep(3)                                          # the port frees up; a service manager would restart it by now
    if answering(port, 0.5):
        return 0
    if serve:
        return serve(argv)
    from . import cli
    return cli.main(argv)
