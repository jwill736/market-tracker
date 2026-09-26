"""Nightly off-site backup: the whole database, encrypted, copied somewhere that isn't this machine.

Every trade that came from an email, every cost you typed for coins moved in from outside, and
every thesis lives only in the local database. If the disk dies, so does the ledger. Once a day
this takes a consistent copy of the database (SQLite's online backup, so it's safe while the
app runs), compresses it, encrypts it and writes it to:

- a folder: best a synced one (Google Drive, iCloud, Dropbox, OneDrive), keeping the last 14;
- and/or a private GitHub repository (one file, updated in place; the repository's history
  keeps the older copies), with a token that can write to that repository only.

Encryption: AES-256-GCM with a key made from your passphrase by scrypt (a slow key function,
so guessing passphrases is expensive). File layout: b"PLB1" + 16-byte salt + 12-byte nonce +
ciphertext. Only the passphrase opens a backup, on any machine. So that it can run at night
without you, the app keeps the derived key (not the passphrase) in .env on this computer, which
already holds the unencrypted database; the copies that leave the machine are unreadable
without the passphrase. API keys live in .env, not the database, so they're never in a backup.
"""

from __future__ import annotations

import base64
import gzip
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAGIC = b"PLB1"
KEEP = 14
GITHUB_PATH = "plumbline.plb"


class BackupError(Exception):
    pass


def derive(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def encrypt(data: bytes, key: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, gzip.compress(data), MAGIC)


def decrypt(blob: bytes, passphrase: str | None = None, key: bytes | None = None) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 33 or blob[:4] != MAGIC:
        raise BackupError("This isn't a Plumbline encrypted backup (.plb).")
    salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
    k = key if key is not None else derive(passphrase or "", salt)
    try:
        return gzip.decompress(AESGCM(k).decrypt(nonce, ct, MAGIC))
    except InvalidTag:
        raise BackupError("Wrong passphrase (or the file is damaged).") from None


def snapshot(db_path: str) -> bytes:
    """A consistent copy of the live database, as bytes."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "copy.db")
        src, dst = sqlite3.connect(db_path), sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return Path(out).read_bytes()


# ------------------------------------------------------------------ settings (.env)

def config() -> dict:
    return {"dir": os.environ.get("OFFSITE_DIR", ""), "repo": os.environ.get("OFFSITE_REPO", ""),
            "token": os.environ.get("OFFSITE_GITHUB_TOKEN", ""), "key": os.environ.get("OFFSITE_KEY", ""),
            "salt": os.environ.get("OFFSITE_SALT", "")}


def configured() -> bool:
    c = config()
    return bool(c["key"] and c["salt"] and (c["dir"] or (c["repo"] and c["token"])))


def new_key(passphrase: str) -> dict[str, str]:
    if len(passphrase) < 12:
        raise BackupError("Use a passphrase of at least 12 characters (four random words work well).")
    salt = os.urandom(16)
    return {"OFFSITE_SALT": base64.b64encode(salt).decode(), "OFFSITE_KEY": base64.b64encode(derive(passphrase, salt)).decode()}


# ------------------------------------------------------------------ destinations

def to_folder(blob: bytes, folder: str, now: datetime, keep: int = KEEP) -> str:
    p = Path(folder).expanduser()
    if not p.is_dir():
        raise BackupError(f"The folder {folder} doesn't exist.")
    name = p / f"plumbline-{now.strftime('%Y-%m-%d')}.plb"
    tmp = name.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(name)
    old = sorted(p.glob("plumbline-*.plb"))
    for f in old[:-keep]:
        f.unlink(missing_ok=True)
    return str(name)


def to_github(blob: bytes, repo: str, token: str, now: datetime, send=None) -> str:
    """Create or update GITHUB_PATH in the repository through the contents API."""
    import httpx
    if repo.count("/") != 1:
        raise BackupError("The repository should look like owner/name.")
    url = f"https://api.github.com/repos/{repo}/contents/{GITHUB_PATH}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    send = send or (lambda method, u, **kw: httpx.request(method, u, headers=headers, timeout=60, **kw))
    got = send("GET", url)
    if got.status_code not in (200, 404):
        raise BackupError(f"GitHub said {got.status_code} reading {repo}: check the name and that the token can read it.")
    body = {"message": f"Plumbline backup {now.strftime('%Y-%m-%d %H:%M')} UTC", "content": base64.b64encode(blob).decode()}
    if got.status_code == 200:
        body["sha"] = got.json().get("sha")
    put = send("PUT", url, json=body)
    if put.status_code not in (200, 201):
        raise BackupError(f"GitHub said {put.status_code} saving to {repo}: the token needs Contents: write on it.")
    return f"github.com/{repo}/{GITHUB_PATH}"


def check_private(repo: str, token: str, send=None) -> None:
    """Refuse a public repository: an encrypted backup still shouldn't be published."""
    import httpx
    send = send or (lambda method, u, **kw: httpx.request(method, u, headers={"Authorization": f"Bearer {token}",
                                                                           "Accept": "application/vnd.github+json"}, timeout=30, **kw))
    r = send("GET", f"https://api.github.com/repos/{repo}")
    if r.status_code != 200:
        raise BackupError(f"GitHub said {r.status_code} for {repo}: check the name and the token.")
    if not r.json().get("private"):
        raise BackupError(f"{repo} is public. Use a private repository.")


def run(db_path: str, now: datetime | None = None, send=None) -> dict:
    """Back up now to every destination set up. Returns where it went (raises if nowhere)."""
    now = now or datetime.now(timezone.utc)
    c = config()
    if not (c["key"] and c["salt"]):
        raise BackupError("Set a passphrase first.")
    blob = encrypt(snapshot(db_path), base64.b64decode(c["key"]), base64.b64decode(c["salt"]))
    done, errors = [], []
    if c["dir"]:
        try:
            done.append(to_folder(blob, c["dir"], now))
        except (BackupError, OSError) as exc:
            errors.append(f"Folder: {exc}")
    if c["repo"] and c["token"]:
        try:
            done.append(to_github(blob, c["repo"], c["token"], now, send))
        except Exception as exc:  # noqa: BLE001 - network errors come in many shapes; report them
            errors.append(f"GitHub: {str(exc)[:200]}")
    if not done:
        raise BackupError("; ".join(errors) or "No destination set up.")
    return {"at": now.isoformat(timespec="seconds"), "to": done, "errors": errors, "bytes": len(blob)}


def restore(blob: bytes, passphrase: str, db_path: str, now: datetime | None = None) -> str:
    """Replace the database with a backup's. The current one is kept next to it. Returns its name."""
    data = decrypt(blob, passphrase)
    if not data.startswith(b"SQLite format 3\x00"):
        raise BackupError("The backup opened but doesn't hold a database.")
    now = now or datetime.now(timezone.utc)
    target = Path(db_path)
    kept = target.with_name(f"{target.name}.before-restore-{now.strftime('%Y%m%d-%H%M%S')}")
    tmp = target.with_name(target.name + ".restoring")
    tmp.write_bytes(data)
    with sqlite3.connect(tmp) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            tmp.unlink(missing_ok=True)
            raise BackupError("The backup's database failed its integrity check.")
    if target.exists():
        target.replace(kept)
    tmp.replace(target)
    return kept.name
