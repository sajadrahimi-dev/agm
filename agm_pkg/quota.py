from __future__ import annotations

import concurrent.futures
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .backend import find_agy_binary

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Za-z]")
_PLAN_RE = re.compile(
    r"\((Google AI [^)\n]+|Free|Pro|Ultra|Enterprise|Standard|Team|Individual)\)"
)


@dataclass
class QuotaEntry:

    group: str
    window: str
    remaining_pct: int
    reset_at: str

    def short_group(self) -> str:
        low = self.group.lower()
        if "gemini" in low:
            return "Gemini"
        if "claude" in low or "gpt" in low:
            return "Claude/GPT"
        return self.group[:12]

    def short_window(self) -> str:
        low = self.window.lower()
        if "five hour" in low or "5" in low:
            return "5h"
        if "weekly" in low or "week" in low:
            return "7d"
        if "daily" in low or "day" in low:
            return "24h"
        return self.window[:6]

    def reset_human(self) -> str:
        if not self.reset_at or self.reset_at.startswith("1970-01-01"):
            return "full" if self.remaining_pct >= 100 else ""
        try:
            dt = datetime.fromisoformat(self.reset_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = int((dt - now).total_seconds())
            if delta <= 0:
                return "now"
            days, rem = divmod(delta, 86400)
            hours, rem = divmod(rem, 3600)
            mins = rem // 60
            if days > 0:
                return f"{days}d {hours}h"
            if hours > 0:
                return f"{hours}h {mins}m"
            return f"{mins}m"
        except Exception:
            return ""


def short_plan_label(plan: str) -> str:
    if not plan:
        return ""
    low = plan.lower()
    if "ultra" in low:
        return "Ultra"
    if "pro" in low:
        return "Pro"
    if "enterprise" in low:
        return "Ent"
    if "team" in low:
        return "Team"
    if "free" in low:
        return "Free"
    return plan[:6]


def parse_usage_output(stdout: str) -> List[QuotaEntry]:
    entries: List[QuotaEntry] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 3:
            continue
        group, window, pct_str = parts[0], parts[1], parts[2]
        reset_at = parts[3] if len(parts) >= 4 else ""
        pct_clean = pct_str.replace("%", "").strip()
        try:
            pct = int(round(float(pct_clean)))
        except ValueError:
            continue
        pct = max(0, min(100, pct))
        entries.append(
            QuotaEntry(
                group=group,
                window=window,
                remaining_pct=pct,
                reset_at=reset_at,
            )
        )
    return entries


def fetch_active_quota(timeout_sec: int = 18) -> Dict[str, Any]:
    agy_bin = find_agy_binary()
    if not agy_bin:
        raise RuntimeError("`agy` CLI not found in PATH.")

    res = subprocess.run(
        [agy_bin, "-p", "/usage", "--print-timeout", f"{timeout_sec}s"],
        capture_output=True,
        text=True,
        timeout=timeout_sec + 5,
        check=False,
    )
    entries = parse_usage_output(res.stdout or "")
    if not entries:
        err_msg = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(err_msg or "Could not fetch usage quota from `agy`.")

    fetched_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "fetched_at": fetched_at,
        "entries": [asdict(e) for e in entries],
    }


def fetch_active_plan(timeout_sec: float = 9.0) -> str:
    agy_bin = find_agy_binary()
    if not agy_bin:
        return ""

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    except OSError:
        pass

    proc = subprocess.Popen(
        [agy_bin],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)

    buf = ""
    plan = ""
    deadline = time.time() + timeout_sec
    try:
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master, 8192).decode("utf-8", errors="ignore")
                except OSError:
                    break
                if not chunk:
                    break
                buf += _ANSI_RE.sub(" ", chunk)
                m = _PLAN_RE.search(buf)
                if m:
                    plan = m.group(1).strip()
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        try:
            os.close(master)
        except OSError:
            pass

    return plan


def fetch_quota_and_plan() -> Tuple[Dict[str, Any], str]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_quota = pool.submit(fetch_active_quota)
        f_plan = pool.submit(fetch_active_plan)
        quota_data = f_quota.result()
        try:
            plan = f_plan.result()
        except Exception:
            plan = ""
    return quota_data, plan


def load_quota_entries(quota_dict: Optional[Dict[str, Any]]) -> List[QuotaEntry]:
    if not quota_dict or not isinstance(quota_dict, dict):
        return []
    out: List[QuotaEntry] = []
    for item in quota_dict.get("entries", []):
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                QuotaEntry(
                    group=str(item.get("group", "")),
                    window=str(item.get("window", "")),
                    remaining_pct=int(item.get("remaining_pct", 0)),
                    reset_at=str(item.get("reset_at", "")),
                )
            )
        except Exception:
            continue
    return out


def make_bar(pct: int, width: int = 8) -> str:
    filled = round((max(0, min(100, pct)) / 100.0) * width)
    return "█" * filled + "░" * (width - filled)


def pct_ansi_color(pct: int) -> str:
    if pct >= 50:
        return "\033[38;2;74;222;128m"
    if pct >= 20:
        return "\033[38;2;250;204;21m"
    return "\033[38;2;248;113;113m"


def format_quota_compact_plain(quota_dict: Optional[Dict[str, Any]]) -> str:
    entries = load_quota_entries(quota_dict)
    if not entries:
        return "Quota: --"
    grouped: Dict[str, List[str]] = {}
    for e in entries:
        g = "G" if "gemini" in e.group.lower() else "C"
        grouped.setdefault(g, []).append(f"{e.short_window()}:{e.remaining_pct}%")
    parts = [f"{g} {' '.join(vals)}" for g, vals in grouped.items()]
    return " · ".join(parts)
