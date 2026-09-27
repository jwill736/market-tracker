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


def test_lan_needs_a_password(monkeypatch, capsys):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert cli.main(["serve", "--lan", "--port", "8124"]) == 2
    assert "APP_PASSWORD" in capsys.readouterr().out
    # With one set it lists the Wi-Fi address (and here finds the running service)
    monkeypatch.setenv("APP_PASSWORD", "correct horse battery")
    monkeypatch.setattr(bgservice, "answering", lambda port, timeout=2.0: True)
    monkeypatch.setattr(cli, "lan_addresses", lambda: ["192.168.1.20"])
    assert cli.main(["serve", "--lan", "--port", "8124"]) == 0
    assert "http://192.168.1.20:8124" in capsys.readouterr().out


def test_service_files_carry_lan():
    assert "<string>--lan</string></array>" in bgservice.mac_plist("/x/mt", "/x", 8000, lan=True)
    assert "serve --port 8000 --lan --log" in bgservice.windows_vbs("C:\\p.exe", "C:\\x", 8000, lan=True)
    assert "serve --port 8000 --lan\n" in bgservice.systemd_unit("/x/mt", "/x", 8000, lan=True)
    assert "--lan" not in bgservice.systemd_unit("/x/mt", "/x", 8000)


def test_service_install_lan_refuses_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(bgservice, "root_dir", lambda: str(tmp_path))
    said = []
    assert bgservice.install(8000, say=said.append, lan=True) == 2 and "APP_PASSWORD" in said[0]


def test_icon_is_a_valid_ico_around_the_app_png():
    import os
    import struct
    png = open(os.path.join(bgservice.root_dir(), "market_tracker", "static", "icon-192.png"), "rb").read()
    ico = bgservice.ico_from_png(png)
    assert struct.unpack("<HHH", ico[:6]) == (0, 1, 1)
    w, h, _, _, planes, bpp, size, offset = struct.unpack("<BBBBHHII", ico[6:22])
    assert (w, h, planes, bpp, size, offset) == (192, 192, 1, 32, len(png), 22) and ico[22:] == png
    big = png[:16] + struct.pack(">II", 512, 512) + png[24:]
    try:
        bgservice.ico_from_png(big)
        raise AssertionError("a 512-pixel image can't go in an .ico")
    except ValueError:
        pass


def test_shortcut_script_survives_awkward_paths():
    ps = bgservice.shortcut_script(r"C:\Users\O'Neil Q\market-tracker\.venv\Scripts\pythonw.exe", r"C:\Users\O'Neil Q\market-tracker",
                                   r"C:\Users\O'Neil Q\market-tracker\plumbline.ico", 8000)
    assert r"$s.TargetPath = 'C:\Users\O''Neil Q\market-tracker\.venv\Scripts\pythonw.exe'" in ps
    assert r"$s.WorkingDirectory = 'C:\Users\O''Neil Q\market-tracker'" in ps
    assert r"$s.IconLocation = 'C:\Users\O''Neil Q\market-tracker\plumbline.ico,0'" in ps
    assert "'-m market_tracker.cli serve --open --port 8000 --log plumbline.log --pidfile plumbline.pid'" in ps
    assert "'-m market_tracker.cli stop --port 8000'" in ps and "GetFolderPath('Desktop')" in ps
    gone = bgservice.shortcut_script("p", "r", "i", 8000, remove=True)
    assert "Remove-Item" in gone and "CreateShortcut" not in gone


def test_shortcut_on_windows(tmp_path, monkeypatch):
    import base64
    monkeypatch.setattr(bgservice.platform, "system", lambda: "Windows")
    (tmp_path / "market_tracker" / "static").mkdir(parents=True)
    png = open(bgservice.os.path.join(bgservice.root_dir(), "market_tracker", "static", "icon-192.png"), "rb").read()
    (tmp_path / "market_tracker" / "static" / "icon-192.png").write_bytes(png)
    monkeypatch.setattr(bgservice, "root_dir", lambda: str(tmp_path))
    said, ran = [], []
    assert bgservice.shortcut(8000, say=said.append) == 2 and "start.bat" in said[-1]      # not set up yet
    (tmp_path / ".env").write_text("X=1\n")

    def fake_run(args, capture_output, text):
        ran.append(base64.b64decode(args[-1]).decode("utf-16-le"))
        return bgservice.subprocess.CompletedProcess(args, 0, stdout="C:\\Users\\q\\Desktop\\Plumbline.lnk\r\n", stderr="")
    monkeypatch.setattr(bgservice.subprocess, "run", fake_run)
    assert bgservice.shortcut(8000, say=said.append) == 0
    assert (tmp_path / "plumbline.ico").read_bytes()[22:] == png
    assert "CreateShortcut" in ran[-1] and said[-3] == "Made: C:\\Users\\q\\Desktop\\Plumbline.lnk"


def test_shortcut_elsewhere_points_to_the_service(monkeypatch):
    monkeypatch.setattr(bgservice.platform, "system", lambda: "Darwin")
    said = []
    assert bgservice.shortcut(8000, say=said.append) == 1 and "mt service install" in said[0]


def test_stop_only_kills_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(bgservice, "root_dir", lambda: str(tmp_path))
    killed, said = [], []
    monkeypatch.setattr(bgservice.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(bgservice, "answering", lambda port, timeout=2.0: False)
    assert bgservice.stop(8000, say=said.append) == 0 and said[-1] == "Plumbline isn't running." and not killed
    up = {"n": 0}

    def answering(port, timeout=2.0):
        up["n"] += 1
        return up["n"] == 1                               # up when asked, down once stopped
    monkeypatch.setattr(bgservice, "answering", answering)
    (tmp_path / "plumbline.pid").write_text("4242")
    monkeypatch.setattr(bgservice, "_is_python", lambda pid: False)                  # the pid was reused by another program
    assert bgservice.stop(8000, say=said.append) == 1 and not killed
    up["n"] = 0
    monkeypatch.setattr(bgservice, "_is_python", lambda pid: True)
    assert bgservice.stop(8000, say=said.append) == 0 and killed == [4242] and said[-1] == "Stopped."


def test_shortcut_reports_a_powershell_error(tmp_path, monkeypatch):
    monkeypatch.setattr(bgservice.platform, "system", lambda: "Windows")
    (tmp_path / "market_tracker" / "static").mkdir(parents=True)
    (tmp_path / "market_tracker" / "static" / "icon-192.png").write_bytes(
        open(bgservice.os.path.join(bgservice.root_dir(), "market_tracker", "static", "icon-192.png"), "rb").read())
    (tmp_path / ".env").write_text("X=1\n")
    monkeypatch.setattr(bgservice, "root_dir", lambda: str(tmp_path))
    monkeypatch.setattr(bgservice.subprocess, "run", lambda args, capture_output, text: bgservice.subprocess.CompletedProcess(
        args, 1, stdout="ERROR: Access is denied.\r\n", stderr="#< CLIXML <Objs/>"))
    said = []
    assert bgservice.shortcut(8000, say=said.append) == 1 and said[-1] == "Couldn't make the shortcuts: Access is denied."
