from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backend import CredentialBackend, CredentialSnapshot, get_backend
from .quota import fetch_quota_and_plan

VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,47}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def compute_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def try_extract_email(secret: str) -> str:
    try:
        data = json.loads(secret)
        if isinstance(data, dict):
            for key in ("id_token_email", "email", "user_email", "account", "user"):
                val = data.get(key)
                if isinstance(val, str) and "@" in val:
                    return val.strip()
            for val in data.values():
                if isinstance(val, dict):
                    for key in ("id_token_email", "email", "user_email"):
                        inner = val.get(key)
                        if isinstance(inner, str) and "@" in inner:
                            return inner.strip()
    except Exception:
        pass
    return ""


def suggest_profile_name(email: str, fallback: str = "account") -> str:
    if email and "@" in email:
        local = email.split("@", 1)[0]
        cleaned = re.sub(r"[^a-zA-Z0-9._-]", "-", local).strip(".-_")
        if cleaned and VALID_NAME_RE.match(cleaned):
            return cleaned[:48]
    return fallback


def validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Profile name cannot be empty.")
    if not VALID_NAME_RE.match(cleaned):
        raise ValueError(
            "Invalid profile name. Use letters, numbers, '.', '_' or '-' (max 48 chars)."
        )
    return cleaned


@dataclass
class ProfileInfo:

    name: str
    email: str
    note: str
    fingerprint: str
    created_at: str
    last_used_at: str
    is_active: bool = False
    quota: Optional[Dict[str, Any]] = None
    plan: str = ""


class ProfileStore:

    def __init__(
        self,
        backend: Optional[CredentialBackend] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        self.backend = backend or get_backend()
        self.base_dir = base_dir or Path(
            os.environ.get("AGM_HOME", str(Path.home() / ".config" / "agm"))
        )
        self.profiles_dir = self.base_dir / "profiles"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.base_dir, 0o700)
            os.chmod(self.profiles_dir, 0o700)
        except OSError:
            pass

    def _profile_path(self, name: str) -> Path:
        return self.profiles_dir / f"{name}.json"

    def _write_json_secure(self, path: Path, payload: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _read_profile_raw(self, name: str) -> dict:
        path = self._profile_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"Profile '{name}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def profile_exists(self, name: str) -> bool:
        return self._profile_path(name).is_file()

    def get_live_info(self) -> Tuple[Optional[str], str]:
        secret = self.backend.read_secret()
        if not secret:
            return None, ""
        return compute_fingerprint(secret), try_extract_email(secret)

    def get_live_fingerprint(self) -> Optional[str]:
        fp, _ = self.get_live_info()
        return fp

    def get_live_email(self) -> str:
        _, email = self.get_live_info()
        return email

    def get_active_status(self) -> Tuple[bool, Optional[str], Optional[str]]:
        live_fp, live_email = self.get_live_info()
        if not live_fp:
            return False, None, None
        for prof in self.list_profiles(live_fp=live_fp, live_email=live_email):
            if prof.is_active:
                return True, prof.name, live_fp
        return True, None, live_fp

    def find_by_email(self, email: str) -> Optional[str]:
        target = email.strip().lower()
        if not target:
            return None
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                p_email = (data.get("email") or "").strip().lower()
                if not p_email and data.get("secret"):
                    p_email = try_extract_email(data["secret"]).lower()
                if p_email == target:
                    return data.get("name", path.stem)
            except Exception:
                continue
        return None

    def sync_live_token_if_matched(self) -> Optional[str]:
        snap = self.backend.read_snapshot()
        if snap is None:
            return None
        live_fp = compute_fingerprint(snap.secret)
        live_email = try_extract_email(snap.secret).lower()
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fp = data.get("fingerprint", "")
                p_email = (data.get("email") or "").strip().lower()
                if not p_email and data.get("secret"):
                    p_email = try_extract_email(data["secret"]).lower()
                if (live_email and p_email == live_email) or (live_fp and fp == live_fp):
                    if fp != live_fp or (live_email and not data.get("email")):
                        data["secret"] = snap.secret
                        data["fingerprint"] = live_fp
                        data["user_scope"] = snap.user_scope
                        if live_email and not data.get("email"):
                            data["email"] = live_email
                        data["last_used_at"] = now_iso()
                        self._write_json_secure(path, data)
                    return data.get("name", path.stem)
            except Exception:
                continue
        return None

    def deduplicate_by_email(self) -> int:
        by_email: Dict[str, List[Tuple[Path, dict]]] = {}
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                p_email = (data.get("email") or "").strip().lower()
                if not p_email and data.get("secret"):
                    p_email = try_extract_email(data["secret"]).lower()
                if p_email:
                    by_email.setdefault(p_email, []).append((path, data))
            except Exception:
                continue

        removed = 0
        for entries in by_email.values():
            if len(entries) <= 1:
                continue
            entries.sort(key=lambda pair: (len(pair[0].stem), pair[0].stem.lower()))
            keep_path, keep_data = entries[0]
            freshest = max(entries, key=lambda pair: pair[1].get("last_used_at", ""))[1]
            if freshest.get("secret"):
                keep_data["secret"] = freshest["secret"]
                keep_data["fingerprint"] = freshest.get("fingerprint", keep_data.get("fingerprint", ""))
                keep_data["user_scope"] = freshest.get("user_scope", keep_data.get("user_scope"))
            if not keep_data.get("quota") and freshest.get("quota"):
                keep_data["quota"] = freshest["quota"]
            if not keep_data.get("plan") and freshest.get("plan"):
                keep_data["plan"] = freshest["plan"]
            keep_data["last_used_at"] = freshest.get("last_used_at", keep_data.get("last_used_at", ""))
            self._write_json_secure(keep_path, keep_data)
            for dup_path, _ in entries[1:]:
                try:
                    dup_path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def list_profiles(
        self,
        live_fp: Optional[str] = None,
        live_email: Optional[str] = None,
    ) -> List[ProfileInfo]:
        if live_fp is None:
            live_fp, live_email = self.get_live_info()
        live_email_low = (live_email or "").strip().lower()

        profiles: List[ProfileInfo] = []
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fp = data.get("fingerprint", "")
                email = data.get("email", "")
                if not email and data.get("secret"):
                    email = try_extract_email(data["secret"])
                email_match = bool(live_email_low and email and email.lower() == live_email_low)
                fp_match = bool(live_fp and fp == live_fp)
                profiles.append(
                    ProfileInfo(
                        name=data.get("name", path.stem),
                        email=email,
                        note=data.get("note", ""),
                        fingerprint=fp,
                        created_at=data.get("created_at", ""),
                        last_used_at=data.get("last_used_at", ""),
                        is_active=email_match or fp_match,
                        quota=data.get("quota"),
                        plan=data.get("plan", ""),
                    )
                )
            except Exception:
                continue

        profiles.sort(key=lambda p: (not p.is_active, p.name.lower()))
        return profiles

    def save_current(
        self,
        name: str = "",
        email: str = "",
        note: str = "",
        overwrite: bool = False,
        quota: Optional[Dict[str, Any]] = None,
    ) -> ProfileInfo:
        snap = self.backend.read_snapshot()
        if snap is None:
            raise RuntimeError(
                "No active Antigravity login found. Log in with `agy` first."
            )

        detected_email = email.strip() or try_extract_email(snap.secret)
        existing_for_email = self.find_by_email(detected_email) if detected_email else None
        if not name.strip() and existing_for_email:
            clean_name = existing_for_email
            overwrite = True
        else:
            raw_name = name.strip() or suggest_profile_name(detected_email)
            clean_name = validate_name(raw_name)
        path = self._profile_path(clean_name)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"Profile '{clean_name}' already exists. Use --force to overwrite."
            )

        fp = compute_fingerprint(snap.secret)
        ts = now_iso()

        existing_created = ts
        existing_quota = quota
        existing_plan = ""
        if path.exists():
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
                existing_created = prev.get("created_at", ts)
                if existing_quota is None:
                    existing_quota = prev.get("quota")
                existing_plan = prev.get("plan", "")
            except Exception:
                pass

        payload = {
            "name": clean_name,
            "email": detected_email,
            "note": note.strip(),
            "plan": existing_plan,
            "fingerprint": fp,
            "created_at": existing_created,
            "last_used_at": ts,
            "quota": existing_quota,
            "secret": snap.secret,
            "user_scope": snap.user_scope,
        }
        self._write_json_secure(path, payload)
        return ProfileInfo(
            name=clean_name,
            email=detected_email,
            note=note.strip(),
            fingerprint=fp,
            created_at=existing_created,
            last_used_at=ts,
            is_active=True,
            quota=existing_quota,
            plan=existing_plan,
        )

    def switch_to(self, name: str) -> ProfileInfo:
        clean_name = validate_name(name)
        data = self._read_profile_raw(clean_name)
        secret = data.get("secret")
        if not secret:
            raise RuntimeError(f"Profile '{clean_name}' has no stored credential.")

        snap = CredentialSnapshot(
            secret=secret,
            user_scope=data.get("user_scope"),
        )
        self.backend.write_snapshot(snap)

        data["last_used_at"] = now_iso()
        if not data.get("email"):
            data["email"] = try_extract_email(secret)
        self._write_json_secure(self._profile_path(clean_name), data)

        return ProfileInfo(
            name=data["name"],
            email=data.get("email", ""),
            note=data.get("note", ""),
            fingerprint=data.get("fingerprint", ""),
            created_at=data.get("created_at", ""),
            last_used_at=data["last_used_at"],
            is_active=True,
            quota=data.get("quota"),
            plan=data.get("plan", ""),
        )

    def save_quota_for_profile(
        self, name: str, quota_data: Dict[str, Any], plan: str = ""
    ) -> None:
        clean_name = validate_name(name)
        data = self._read_profile_raw(clean_name)
        data["quota"] = quota_data
        if plan:
            data["plan"] = plan
        self._write_json_secure(self._profile_path(clean_name), data)

    def refresh_active_quota(self) -> Tuple[Optional[str], Dict[str, Any]]:
        has_live, active_name, _ = self.get_active_status()
        if not has_live:
            raise RuntimeError("No active Antigravity login found.")
        quota_data, plan = fetch_quota_and_plan()
        if active_name:
            self.save_quota_for_profile(active_name, quota_data, plan=plan)
        return active_name, quota_data

    def refresh_all_quotas(
        self,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Tuple[bool, str]]:
        profiles = self.list_profiles()
        if not profiles:
            return {}

        original_snap = self.backend.read_snapshot()
        results: Dict[str, Tuple[bool, str]] = {}
        total = len(profiles)

        try:
            for idx, prof in enumerate(profiles, start=1):
                if progress_cb:
                    progress_cb(prof.name, idx, total)
                try:
                    data = self._read_profile_raw(prof.name)
                    snap = CredentialSnapshot(
                        secret=data["secret"],
                        user_scope=data.get("user_scope"),
                    )
                    self.backend.write_snapshot(snap)
                    quota_data, plan = fetch_quota_and_plan()
                    data["quota"] = quota_data
                    if plan:
                        data["plan"] = plan
                    self._write_json_secure(self._profile_path(prof.name), data)
                    results[prof.name] = (True, "ok")
                except Exception as exc:
                    results[prof.name] = (False, str(exc))
        finally:
            if original_snap is not None:
                self.backend.write_snapshot(original_snap)
            else:
                self.backend.clear_snapshot()

        return results

    def remove_profile(self, name: str) -> None:
        clean_name = validate_name(name)
        path = self._profile_path(clean_name)
        if not path.is_file():
            raise FileNotFoundError(f"Profile '{clean_name}' not found.")
        path.unlink()

    def rename_profile(self, old_name: str, new_name: str) -> ProfileInfo:
        old_clean = validate_name(old_name)
        new_clean = validate_name(new_name)
        old_path = self._profile_path(old_clean)
        new_path = self._profile_path(new_clean)
        if not old_path.is_file():
            raise FileNotFoundError(f"Profile '{old_clean}' not found.")
        if new_path.exists():
            raise FileExistsError(f"Profile '{new_clean}' already exists.")

        data = self._read_profile_raw(old_clean)
        data["name"] = new_clean
        self._write_json_secure(new_path, data)
        old_path.unlink()

        live_fp = self.get_live_fingerprint()
        fp = data.get("fingerprint", "")
        return ProfileInfo(
            name=new_clean,
            email=data.get("email", ""),
            note=data.get("note", ""),
            fingerprint=fp,
            created_at=data.get("created_at", ""),
            last_used_at=data.get("last_used_at", ""),
            is_active=bool(live_fp and fp == live_fp),
            quota=data.get("quota"),
        )

    def update_meta(
        self,
        name: str,
        email: Optional[str] = None,
        note: Optional[str] = None,
    ) -> ProfileInfo:
        clean_name = validate_name(name)
        data = self._read_profile_raw(clean_name)
        if email is not None:
            data["email"] = email.strip()
        if note is not None:
            data["note"] = note.strip()
        self._write_json_secure(self._profile_path(clean_name), data)
        live_fp = self.get_live_fingerprint()
        fp = data.get("fingerprint", "")
        return ProfileInfo(
            name=clean_name,
            email=data.get("email", ""),
            note=data.get("note", ""),
            fingerprint=fp,
            created_at=data.get("created_at", ""),
            last_used_at=data.get("last_used_at", ""),
            is_active=bool(live_fp and fp == live_fp),
            quota=data.get("quota"),
        )

    def logout_active(self) -> None:
        self.backend.clear_snapshot()
