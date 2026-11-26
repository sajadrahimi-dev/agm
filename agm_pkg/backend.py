from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVICE_NAME = "gemini"
ACCOUNT_NAME = "antigravity"
SECRET_LABEL = "Password for 'antigravity' on 'gemini'"
SECRET_SCHEMA = "org.freedesktop.Secret.Generic"

SCOPE_FILE = Path.home() / ".config" / "antigravity" / "credential-user-scope"


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or os.path.exists("/data/data/com.termux")


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def find_agy_binary() -> Optional[str]:
    found = shutil.which("agy")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path(os.environ.get("PREFIX", "/usr")) / "bin" / "agy",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def is_antigravity_gui_running() -> bool:
    singleton = Path.home() / ".config" / "Antigravity" / "SingletonLock"
    if singleton.is_symlink() or singleton.exists():
        try:
            target = os.readlink(singleton)
            pid_str = target.rsplit("-", 1)[-1]
            if pid_str.isdigit():
                os.kill(int(pid_str), 0)
                return True
        except OSError:
            pass
    return False


@dataclass
class CredentialSnapshot:

    secret: str
    user_scope: Optional[str] = None


class CredentialBackend:

    name: str = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def read_secret(self) -> Optional[str]:
        raise NotImplementedError

    def write_secret(self, secret: str) -> None:
        raise NotImplementedError

    def delete_secret(self) -> None:
        raise NotImplementedError

    def read_snapshot(self) -> Optional[CredentialSnapshot]:
        secret = self.read_secret()
        if not secret:
            return None
        scope = None
        if SCOPE_FILE.is_file():
            try:
                scope = SCOPE_FILE.read_text(encoding="utf-8")
            except OSError:
                scope = None
        return CredentialSnapshot(secret=secret, user_scope=scope)

    def write_snapshot(self, snapshot: CredentialSnapshot) -> None:
        self.write_secret(snapshot.secret)
        if snapshot.user_scope is not None:
            SCOPE_FILE.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(SCOPE_FILE.parent, 0o700)
            tmp = SCOPE_FILE.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, snapshot.user_scope.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp, SCOPE_FILE)

    def clear_snapshot(self) -> None:
        self.delete_secret()
        if SCOPE_FILE.is_file():
            try:
                SCOPE_FILE.unlink()
            except OSError:
                pass


class SecretStorageBackend(CredentialBackend):

    name = "secretstorage (Linux Keyring)"

    def _get_collection(self):
        import secretstorage

        bus = secretstorage.dbus_init()
        col = secretstorage.get_default_collection(bus)
        if col.is_locked():
            col.unlock()
        return bus, col

    def is_available(self) -> bool:
        if is_termux():
            return False
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            pass
        try:
            bus, col = self._get_collection()
            locked = col.is_locked()
            bus.close()
            return not locked
        except Exception:
            return False

    def _find_item(self, col):
        items = list(
            col.search_items({"service": SERVICE_NAME, "username": ACCOUNT_NAME})
        )
        if items:
            return items[0]
        for item in col.get_all_items():
            attrs = item.get_attributes()
            if (
                attrs.get("service") == SERVICE_NAME
                and attrs.get("username") == ACCOUNT_NAME
            ):
                return item
        return None

    def read_secret(self) -> Optional[str]:
        bus, col = self._get_collection()
        try:
            item = self._find_item(col)
            if item is None:
                return None
            raw = item.get_secret()
            if not raw:
                return None
            return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        finally:
            bus.close()

    def write_secret(self, secret: str) -> None:
        bus, col = self._get_collection()
        try:
            item = self._find_item(col)
            payload = secret.encode("utf-8")
            if item is not None:
                item.set_secret(payload)
            else:
                col.create_item(
                    SECRET_LABEL,
                    {
                        "service": SERVICE_NAME,
                        "username": ACCOUNT_NAME,
                        "xdg:schema": SECRET_SCHEMA,
                    },
                    payload,
                    replace=True,
                )
        finally:
            bus.close()

    def delete_secret(self) -> None:
        bus, col = self._get_collection()
        try:
            item = self._find_item(col)
            if item is not None:
                item.delete()
        finally:
            bus.close()


class KeyringLibraryBackend(CredentialBackend):

    name = "keyring"

    def is_available(self) -> bool:
        if is_termux():
            return False
        try:
            import keyring

            backend = keyring.get_keyring()
            return "fail" not in type(backend).__name__.lower()
        except Exception:
            return False

    def read_secret(self) -> Optional[str]:
        import keyring

        return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)

    def write_secret(self, secret: str) -> None:
        import keyring

        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, secret)

    def delete_secret(self) -> None:
        import keyring

        try:
            keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            pass


class SecretToolBackend(CredentialBackend):

    name = "secret-tool"

    def is_available(self) -> bool:
        return not is_termux() and shutil.which("secret-tool") is not None

    def read_secret(self) -> Optional[str]:
        res = subprocess.run(
            ["secret-tool", "lookup", "service", SERVICE_NAME, "username", ACCOUNT_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return None
        return res.stdout

    def write_secret(self, secret: str) -> None:
        res = subprocess.run(
            [
                "secret-tool",
                "store",
                f"--label={SECRET_LABEL}",
                "service",
                SERVICE_NAME,
                "username",
                ACCOUNT_NAME,
            ],
            input=secret,
            text=True,
            capture_output=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "secret-tool store failed")

    def delete_secret(self) -> None:
        subprocess.run(
            ["secret-tool", "clear", "service", SERVICE_NAME, "username", ACCOUNT_NAME],
            capture_output=True,
            check=False,
        )


class FileFallbackBackend(CredentialBackend):

    name = "file (Termux / Headless)"

    def __init__(self, path: Optional[Path] = None) -> None:
        env_path = os.environ.get("AGM_CRED_FILE")
        if path is not None:
            self.path = path
        elif env_path:
            self.path = Path(env_path).expanduser()
        else:
            candidates = [
                Path.home() / ".config" / "antigravity" / "credentials",
                Path.home() / ".config" / "antigravity" / "credentials.json",
                Path.home() / ".gemini" / "antigravity-cli" / "credentials.json",
            ]
            chosen = candidates[0]
            for c in candidates:
                if c.is_file():
                    chosen = c
                    break
            self.path = chosen

    def is_available(self) -> bool:
        return True

    def read_secret(self) -> Optional[str]:
        if not self.path.is_file():
            return None
        content = self.path.read_text(encoding="utf-8").strip()
        return content or None

    def write_secret(self, secret: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, secret.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def delete_secret(self) -> None:
        if self.path.is_file():
            self.path.unlink()


def get_backend(force: Optional[str] = None) -> CredentialBackend:
    forced = (force or os.environ.get("AGM_BACKEND", "")).strip().lower()
    if forced == "file":
        return FileFallbackBackend()
    if forced == "secretstorage":
        return SecretStorageBackend()
    if forced == "keyring":
        return KeyringLibraryBackend()
    if forced == "secret-tool":
        return SecretToolBackend()

    for backend in (
        SecretStorageBackend(),
        KeyringLibraryBackend(),
        SecretToolBackend(),
    ):
        if backend.is_available():
            return backend
    return FileFallbackBackend()


def check_and_update() -> tuple[bool, str]:
    repo_dir = Path(__file__).resolve().parent.parent
    if not (repo_dir / ".git").exists():
        return False, "Not a git installation — clone from GitHub to enable updates."
    if not shutil.which("git"):
        return False, "git is not installed."

    fetch = subprocess.run(
        ["git", "-C", str(repo_dir), "fetch", "origin", "main", "--quiet"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if fetch.returncode != 0:
        err = (fetch.stderr or "").strip() or "Failed to reach GitHub."
        return False, err

    local = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "origin/main"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    if not remote or local == remote:
        return False, "Already up to date."

    pull = subprocess.run(
        ["git", "-C", str(repo_dir), "pull", "--ff-only", "origin", "main"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if pull.returncode != 0:
        err = (pull.stderr or pull.stdout or "").strip() or "Update failed."
        return False, err

    short_sha = remote[:7]
    return True, f"Updated to latest ({short_sha})"
