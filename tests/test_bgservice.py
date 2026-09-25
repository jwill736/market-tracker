import sys

from market_tracker import bgservice, cli


def test_files_for_each_system():
    plist = bgservice.mac_plist("/Users/j/market-tracker/.venv/bin/mt", "/Users/j/market-tracker", 8000)
    assert "<string>/Users/j/market-tracker/.venv/bin/mt</string><string>serve</string>" in plist
    assert "<key>WorkingDirectory</key><string>/Users/j/market-tracker</string>" in plist and "KeepAlive" in plist
    vbs = bgservice.windows_vbs(r"C:\Users\J Doe\market-tracker\.venv\Scripts\pythonw.exe", r"C:\Users\J Doe\market-tracker", 8000)
    assert r'sh.CurrentDirectory = "C:\Users\J Doe\market-tracker"' in vbs
    assert r'sh.Run """C:\Users\J Doe\market-tracker\.venv\Scripts\pythonw.exe"" -m market_tracker.cli serve --port 8000 ' \
           r'--log plumbline.log --pidfile plumbline.pid", 0, False' in vbs
    unit = bgservice.systemd_unit("/home/j/mt/.venv/bin/mt", "/home/j/mt", 8001)
    assert "WorkingDirectory=/home/j/mt" in unit and "ExecStart=/home/j/mt/.venv/bin/mt serve --port 8001" in unit


def test_windows_install_uses_the_startup_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(bgservice.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    started = []
    monkeypatch.setattr(bgservice.subprocess, "Popen", lambda args: started.append(args))
    state = {"up": False}
    monkeypatch.setattr(bgservice, "answering", lambda port, timeout=2.0: state["up"])
    monkeypatch.setattr(bgservice, "wait_until_up", lambda port, seconds=30.0: True)
    said = []
    assert bgservice.install(8000, say=said.append) == 0
    vbs = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Plumbline.vbs"
    assert vbs.exists() and started == [["wscript.exe", str(vbs)]]
    state["up"] = True
    bgservice.status(8000, say=said.append)
    assert said[-1] == "Starts at login: yes. Running now: yes, http://localhost:8000."
    monkeypatch.setattr(bgservice, "_kill_pidfile", lambda root: None)
    bgservice.uninstall(8000, say=said.append)
    assert not vbs.exists()


def test_serve_just_opens_the_browser_when_the_service_runs(monkeypatch, capsys):
    monkeypatch.setattr(bgservice, "answering", lambda port, timeout=2.0: True)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert cli.main(["serve", "--open", "--port", "8123"]) == 0
    assert opened == ["http://localhost:8123"] and "already running" in capsys.readouterr().out


def test_venv_bin_prefers_this_environment():
    import os
    assert bgservice.venv_bin("python") == os.path.join(os.path.dirname(sys.executable), "python")
