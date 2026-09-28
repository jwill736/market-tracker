import importlib.metadata
import json

from market_tracker import db, decisions, doctor, offsite


def test_uninstalled_app_says_how_to_reinstall(monkeypatch, tmp_path):
    def gone(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(doctor.importlib.metadata, "distribution", gone)
    c = doctor.check_installed(str(tmp_path))
    assert c["level"] == "fail" and "pip install -e ." in c["fix"] and "Close any window" in c["fix"]


def test_installed_from_another_folder_is_a_warning(monkeypatch, tmp_path):
    class Dist:
        version = "0.1.0"

        def read_text(self, name):
            return json.dumps({"url": "file:///somewhere/else", "dir_info": {"editable": True}})
    monkeypatch.setattr(doctor.importlib.metadata, "distribution", lambda n: Dist())
    assert doctor.check_installed(str(tmp_path))["level"] == "warn"


def test_port_used_by_another_program(monkeypatch):
    c = doctor.check_running(8000, answering=lambda p: False, taken=lambda p: True)
    assert c["level"] == "fail" and "--port 8001" in c["fix"] and "mt shortcut --port 8001" in c["fix"]
    assert doctor.check_running(8000, answering=lambda p: False, taken=lambda p: False)["level"] == "info"
    assert doctor.check_running(8000, answering=lambda p: True)["level"] == "ok"


def test_locked_mt_exe_on_windows(monkeypatch, tmp_path):
    exe = tmp_path / "mt.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(doctor, "WIN", True)
    monkeypatch.setattr(doctor.sys, "executable", str(tmp_path / "python.exe"))
    real_open = open

    def locked(path, mode="r", *a, **k):
        if str(path) == str(exe) and "+" in mode:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("builtins.open", locked)
    c = doctor.check_mt_exe_lock(str(tmp_path))
    assert c["level"] == "fail" and "taskkill /IM mt.exe /F" in c["fix"]


def test_files_an_admin_made(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(doctor, "_writable", lambda folder: folder != str(tmp_path))
    monkeypatch.setattr(doctor, "WIN", True)
    c = doctor.check_permissions(str(tmp_path))
    assert c["level"] == "fail" and "icacls" in c["fix"] and str(tmp_path) in c["fix"]


def test_database_check(tmp_path):
    import sqlite3
    p = tmp_path / "t.db"
    assert doctor.check_database(str(p))["level"] == "info"
    with sqlite3.connect(p) as conn:
        conn.execute("CREATE TABLE transactions (id INTEGER)")
        conn.executemany("INSERT INTO transactions VALUES (?)", [(1,), (2,)])
    c = doctor.check_database(str(p))
    assert c["level"] == "ok" and c["text"].startswith("2 transactions")


def test_report_is_plain_ascii_and_hides_secrets(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic-abc123")
    result = doctor.run(8000, fetch=False)
    text = doctor.report(result)
    text.encode("ascii")
    assert "secret-topic-abc123" not in text and "Phone alerts set up" in text
    assert {c["key"] for c in result["checks"]} >= {"python", "installed", "setup", "running", "permissions", "database", "backup", "alerts", "update"}


def test_log_tail_finds_the_last_error(tmp_path, monkeypatch):
    (tmp_path / "plumbline.log").write_text("Plumbline is running\nTraceback (most recent call last):\nOSError: [Errno 98] address in use\nok\n")
    log = doctor.log_tail(str(tmp_path))
    assert log["error"] == "OSError: [Errno 98] address in use" and log["lines"][-1] == "ok"


def test_suggested_backup_folder(tmp_path):
    od = tmp_path / "OneDrive - Personal"
    od.mkdir()
    assert offsite.suggested_dir(env={"OneDrive": str(od)}, home=tmp_path) == {"service": "OneDrive", "path": str(od / "Plumbline backups")}
    cloud = tmp_path / "mac" / "Library" / "CloudStorage" / "GoogleDrive-me@example.com" / "My Drive"
    cloud.mkdir(parents=True)
    assert offsite.suggested_dir(env={}, home=tmp_path / "mac")["service"] == "Google Drive"
    assert offsite.suggested_dir(env={}, home=tmp_path / "nothing") is None


def test_backup_decision_and_one_click_folder(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from market_tracker import api
    od = tmp_path / "OneDrive"
    od.mkdir()
    for k in ("OFFSITE_DIR", "OFFSITE_KEY", "OFFSITE_SALT", "OFFSITE_REPO", "OFFSITE_GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OneDrive", str(od))
    monkeypatch.setattr(offsite.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert decisions.backup_prompt(False) is None                                       # nothing to protect yet
    d = decisions.backup_prompt(True)
    assert d["title"] == "Back up your ledger to OneDrive"
    item = decisions.build({"holdings": [], "reinvest": [], "tax": {"harvest": []}}, backup=d)[0]
    assert item["kind"] == "backup_setup" and item["action"] == {"type": "open", "page": "offsite"}
    saved = {}
    monkeypatch.setattr(api, "_save_env", lambda vals: saved.update(vals) or [monkeypatch.setenv(k, v) for k, v in vals.items()])
    c = TestClient(api.app)
    sug = c.get("/api/offsite").json()["suggest"]
    assert sug["path"] == str(od / "Plumbline backups")
    other = str(tmp_path / "not-suggested")
    assert c.post("/api/offsite", json={"dir": other, "create_dir": True}).status_code == 400    # only the suggested folder is made
    r = c.post("/api/offsite", json={"dir": sug["path"], "create_dir": True, "passphrase": "correct horse battery staple"}).json()
    assert (od / "Plumbline backups").is_dir() and saved["OFFSITE_DIR"] == sug["path"] and r["backup"]["ok"]
    assert list((od / "Plumbline backups").glob("plumbline-*.plb"))
    assert decisions.backup_prompt(True) is None                                         # set up: the decision goes away
    with db.connect() as conn:
        db.set_meta(conn, "offsite_last", "null")


def test_a_server_install_isnt_flagged(monkeypatch, tmp_path):
    monkeypatch.setenv("REQUIRE_LOGIN", "1")
    assert doctor.check_setup(str(tmp_path))["level"] == "ok"                 # settings come from the host
    assert doctor.check_permissions(str(tmp_path)) is None                   # read-only package folder on purpose
