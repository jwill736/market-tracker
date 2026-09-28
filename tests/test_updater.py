import subprocess

import pytest

from market_tracker import updater


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A 'GitHub' repository and the user's clone of it."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}.items():
        monkeypatch.setenv(k, v)
    origin, work, mine = tmp_path / "origin.git", tmp_path / "work", tmp_path / "mine"
    _run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    _run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    (work / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (work / "app.py").write_text("v = 1\n")
    _run(work, "git", "add", ".")
    _run(work, "git", "commit", "-q", "-m", "First version")
    _run(work, "git", "push", "-q", "origin", "HEAD:main")
    _run(tmp_path, "git", "clone", "-q", str(origin), str(mine))

    def publish(title, files):
        for name, text in files.items():
            (work / name).write_text(text)
        _run(work, "git", "commit", "-q", "-am", title)
        _run(work, "git", "push", "-q", "origin", "HEAD:main")
    return mine, publish


def _head(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_status_lists_whats_new(repos):
    mine, publish = repos
    assert updater.status(str(mine))["text"].startswith("Up to date")
    publish("Desktop icon", {"app.py": "v = 2\n"})
    publish("Fix start.bat", {"app.py": "v = 3\n"})
    st = updater.status(str(mine))
    assert st["can_update"] and st["behind"] == 2 and [c["title"] for c in st["changes"]] == ["Fix start.bat", "Desktop icon"]
    assert st["version"] == _head(mine) and updater.version(str(mine)) == _head(mine)


def test_apply_skips_the_reinstall_for_code_only_changes(repos):
    mine, publish = repos
    publish("Code only", {"app.py": "v = 2\n"})
    pips = []
    out = updater.apply(str(mine), pip=lambda root: pips.append(root))
    assert out["updated"] and not out["reinstalled"] and not pips and (mine / "app.py").read_text() == "v = 2\n"
    assert out["to"] == _head(mine) and updater.status(str(mine), fetch=False)["behind"] == 0


def test_a_failed_reinstall_goes_back_to_the_old_version(repos):
    mine, publish = repos
    before = _head(mine)
    publish("New requirement", {"pyproject.toml": "[project]\nname = 'x'\ndependencies = ['y']\n", "app.py": "v = 2\n"})
    (mine / "notes.txt").write_text("mine")                                   # the user's own untracked file survives
    fail = lambda root: subprocess.CompletedProcess([], 1, "", "ERROR: Could not install packages: [WinError 32] in use")  # noqa: E731
    out = updater.apply(str(mine), pip=fail)
    assert not out["updated"] and "WinError 32" in out["error"] and "Went back" in out["error"]
    assert _head(mine) == before and (mine / "app.py").read_text() == "v = 1\n" and (mine / "notes.txt").exists()
    ok = updater.apply(str(mine), pip=lambda root: subprocess.CompletedProcess([], 0, "", ""))
    assert ok["updated"] and ok["reinstalled"]


def test_a_copy_with_its_own_commits_is_left_alone(repos):
    mine, publish = repos
    publish("Upstream", {"app.py": "v = 2\n"})
    (mine / "app.py").write_text("v = 99\n")
    _run(mine, "git", "commit", "-q", "-am", "My tweak")
    out = updater.apply(str(mine), pip=lambda root: 1 / 0)
    assert not out["updated"] and "of its own" in out["error"] and (mine / "app.py").read_text() == "v = 99\n"


def test_not_a_git_copy(tmp_path):
    assert not updater.status(str(tmp_path))["supported"] and updater.version(str(tmp_path)) == ""
    assert "deploy script" in updater.apply(str(tmp_path))["error"]


def test_restart_hands_over_to_a_hidden_helper(monkeypatch):
    monkeypatch.setattr(updater, "managed_by_service", lambda: False)
    spawned, exited = [], []
    launch = {"host": "127.0.0.1", "port": 8000, "lan": False, "log": None, "pidfile": None}
    assert updater.restart(launch, spawn=spawned.append, exit_fn=lambda: exited.append(1), delay=0.01) == "helper"
    cmd = spawned[0]
    assert cmd[1:5] == ["-m", "market_tracker.cli", "relaunch", "--port"]
    assert cmd[6:] == ["--", "serve", "--port", "8000", "--log", "plumbline.log", "--pidfile", "plumbline.pid"]
    assert "--open" not in cmd
    import time
    time.sleep(0.1)
    assert exited == [1]
    assert updater.restart(None) == "manual"                                  # not started by `mt serve`: nothing to do
    assert updater.serve_args({"port": 8001, "lan": True, "host": "0.0.0.0", "log": "x.log", "pidfile": None})[:3] == ["serve", "--port", "8001"]


def test_relaunch_waits_for_the_old_one_and_steps_aside_for_a_service_manager():
    calls, served = {"n": 0}, []

    def answering(port, timeout=2.0):                 # old app answers twice, then it's gone and nothing comes back
        calls["n"] += 1
        return calls["n"] <= 2
    assert updater.relaunch(8000, ["serve"], answering=answering, sleep=lambda s: None, serve=lambda a: served.append(a) or 0) == 0
    assert served == [["serve"]]
    served.clear()
    assert updater.relaunch(8000, ["serve"], answering=lambda p, t=2.0: True, sleep=lambda s: None, serve=served.append, wait=0) == 0
    assert served == []                                                        # something already answers: don't start a second one


def test_update_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from market_tracker.api import app
    monkeypatch.setattr(updater, "status", lambda *a, **k: {"supported": True, "can_update": True, "behind": 1, "version": "abc1234",
                                                            "changes": [{"id": "def5678", "title": "x"}], "text": "A new version is out: 1 change."})
    updater._cache.clear()
    c = TestClient(app)
    assert c.get("/api/update").json()["behind"] == 1
    monkeypatch.setattr(updater, "apply", lambda: {"updated": False, "error": "Couldn't update the files (conflict). Nothing changed."})
    assert c.post("/api/update").status_code == 400
    monkeypatch.setattr(updater, "apply", lambda: {"updated": True, "from": "abc1234", "to": "def5678", "text": "Updated abc1234 → def5678."})
    monkeypatch.setattr(updater, "restart", lambda: "helper")
    r = c.post("/api/update").json()
    assert r["restarting"] == "helper" and r["to"] == "def5678"


def test_the_version_is_the_running_one_not_the_files_on_disk(monkeypatch):
    from fastapi.testclient import TestClient

    from market_tracker.api import app
    monkeypatch.setattr(updater, "RUNNING", "old1234")
    monkeypatch.setattr(updater, "version", lambda *a, **k: "new5678")       # files already updated, restart pending
    assert TestClient(app).get("/api/update/version").json() == {"version": "old1234"}
