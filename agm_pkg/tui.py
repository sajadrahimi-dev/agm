from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__
from .backend import (
    check_and_update,
    find_agy_binary,
    has_display,
    is_antigravity_gui_running,
    is_termux,
)
from .quota import (
    QuotaEntry,
    load_quota_entries,
    make_bar,
    pct_ansi_color,
    short_plan_label,
)
from .store import (
    ProfileInfo,
    ProfileStore,
    compute_fingerprint,
    suggest_profile_name,
    try_extract_email,
    validate_name,
)

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

TITLE_BLUE = "\033[38;2;122;162;247m"
CYAN = "\033[38;2;103;232;249m"
SKY = "\033[38;2;125;211;252m"
VIOLET = "\033[38;2;192;132;252m"
GREEN = "\033[38;2;74;222;128m"
YELLOW = "\033[38;2;250;204;21m"
RED = "\033[38;2;248;113;113m"
WHITE = "\033[38;2;241;245;249m"
MUTED = "\033[38;2;138;146;162m"
SUBTLE = "\033[38;2;86;94;110m"
GRAY = MUTED

ALT_SCREEN_ON = "\033[?1049h"
ALT_SCREEN_OFF = "\033[?1049l"
HOME = "\033[H"
CLEAR_EOL = "\033[K"
CLEAR_BELOW = "\033[J"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

_PALETTE: Dict[str, int] = {
    "O": 208,
    "Y": 220,
    "R": 203,
    "P": 204,
    "L": 154,
    "G": 83,
    "T": 49,
    "C": 45,
    "V": 141,
    "M": 171,
    "B": 39,
    "D": 33,
    "S": 75,
}

_LOGO_GRID = [
    "....OO....",
    "...YOOR...",
    "..LYOORR..",
    "..GGTVPP..",
    ".GGCB.VM..",
    ".GTC...BD.",
    "SS......BD",
    "SS......BD",
]


def render_logo_lines() -> List[str]:
    rows: List[str] = []
    for y in range(0, len(_LOGO_GRID), 2):
        top_row = _LOGO_GRID[y]
        bot_row = _LOGO_GRID[y + 1] if y + 1 < len(_LOGO_GRID) else "." * len(top_row)
        parts: List[str] = []
        for x in range(len(top_row)):
            tc = _PALETTE.get(top_row[x])
            bc = _PALETTE.get(bot_row[x])
            if tc is None and bc is None:
                parts.append(" ")
            elif tc is not None and bc is None:
                parts.append(f"\033[38;5;{tc}m▀{RESET}")
            elif tc is None and bc is not None:
                parts.append(f"\033[38;5;{bc}m▄{RESET}")
            elif tc == bc:
                parts.append(f"\033[38;5;{tc}m█{RESET}")
            else:
                parts.append(f"\033[38;5;{tc}m\033[48;5;{bc}m▀{RESET}")
        rows.append("".join(parts))
    return rows


def _term_width() -> int:
    return max(44, shutil.get_terminal_size(fallback=(84, 24)).columns)


def _trunc(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)] + "…"


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if not ch:
            return "q"
        if ch == b"\x03":
            return "CTRL_C"
        if ch in (b"\r", b"\n"):
            return "ENTER"
        if ch == b"\x1b":
            rlist, _, _ = select.select([fd], [], [], 0.05)
            if not rlist:
                return "ESC"
            seq = os.read(fd, 2)
            if seq == b"[A":
                return "UP"
            if seq == b"[B":
                return "DOWN"
            return "ESC"
        return ch.decode("utf-8", errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _prompt_line(label: str, default: str = "") -> Optional[str]:
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    hint = f" {MUTED}[{default}]{RESET}" if default else ""
    try:
        val = input(f"\n  {TITLE_BLUE}❯{RESET} {WHITE}{label}{RESET}{hint}: ").strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()


def _confirm(question: str) -> bool:
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    try:
        ans = input(f"\n  {YELLOW}?{RESET} {WHITE}{question}{RESET} {MUTED}[y/N]{RESET}: ").strip().lower()
        return ans in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        return False
    finally:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()


def run_login_flow(
    store: ProfileStore,
    name: str = "",
    email: str = "",
    note: str = "",
    interactive_name: bool = False,
    fetch_quota: bool = True,
) -> str:
    agy_bin = find_agy_binary()
    if not agy_bin:
        raise RuntimeError("`agy` CLI was not found in PATH.")

    previous_snap = store.backend.read_snapshot()
    previous_fp = compute_fingerprint(previous_snap.secret) if previous_snap else None

    store.backend.clear_snapshot()

    print()
    print(f"  {TITLE_BLUE}{BOLD}Login to a new Antigravity account{RESET}")
    print(f"  {MUTED}Complete sign-in in browser — AGM will auto-save & close agy immediately.{RESET}\n")

    saved_tty = None
    if sys.stdin.isatty():
        try:
            saved_tty = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            saved_tty = None

    proc = subprocess.Popen([agy_bin])
    detected_snap = None
    try:
        while proc.poll() is None:
            time.sleep(0.4)
            snap = store.backend.read_snapshot()
            if snap is not None:
                fp = compute_fingerprint(snap.secret)
                if not previous_fp or fp != previous_fp:
                    time.sleep(0.5)
                    detected_snap = store.backend.read_snapshot() or snap
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                    break
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
    finally:
        if saved_tty is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_tty)
            except Exception:
                pass
        os.write(sys.stdout.fileno(), (ALT_SCREEN_OFF + SHOW_CURSOR + RESET).encode("utf-8"))

    new_snap = detected_snap or store.backend.read_snapshot()
    if new_snap is None:
        if previous_snap is not None:
            store.backend.write_snapshot(previous_snap)
            return "Login cancelled — previous account restored."
        return "Login cancelled — no credential detected."

    new_fp = compute_fingerprint(new_snap.secret)
    if previous_fp and new_fp == previous_fp:
        return "Same account detected — no new login was completed."

    detected_email = email.strip() or try_extract_email(new_snap.secret)
    final_name = validate_name(name.strip() or suggest_profile_name(detected_email))
    prof = store.save_current(
        final_name,
        email=detected_email,
        note=note.strip(),
        overwrite=True,
    )

    if fetch_quota:
        print(f"\n  {GREEN}✓{RESET} Logged in as {WHITE}{BOLD}{prof.email or prof.name}{RESET}")
        print(f"  {MUTED}Fetching usage quota & plan...{RESET}")
        try:
            store.refresh_active_quota()
        except Exception:
            pass

    email_str = f" ({prof.email})" if prof.email else ""
    return f"Saved '{prof.name}'{email_str}"


def _plan_badge_colored(plan: str) -> str:
    short = short_plan_label(plan)
    if not short:
        return f"{SUBTLE}{'--':<5}{RESET}"
    low = short.lower()
    if low == "ultra":
        col = VIOLET
    elif low == "pro":
        col = TITLE_BLUE
    elif low == "free":
        col = MUTED
    else:
        col = CYAN
    return f"{col}{short:<5}{RESET}"


def _group_entries(quota_dict: Optional[dict]) -> Dict[str, Dict[str, QuotaEntry]]:
    grouped: Dict[str, Dict[str, QuotaEntry]] = {}
    for e in load_quota_entries(quota_dict):
        grouped.setdefault(e.short_group(), {})[e.short_window()] = e
    return grouped


def _row_quota_summary(quota_dict: Optional[dict], width: int) -> str:
    grouped = _group_entries(quota_dict)
    if not grouped:
        return f"{SUBTLE}--{RESET}"

    def _fmt_pair(tag: str, wins: Dict[str, QuotaEntry]) -> str:
        e5 = wins.get("5h")
        e7 = wins.get("7d")
        if e5 is not None and e7 is not None:
            c5 = pct_ansi_color(e5.remaining_pct)
            c7 = pct_ansi_color(e7.remaining_pct)
            return (
                f"{SUBTLE}{tag}{RESET} "
                f"{c5}{e5.remaining_pct:>3}%{RESET}"
                f"{SUBTLE}/{RESET}"
                f"{c7}{e7.remaining_pct:>3}%{RESET}"
            )
        ent = e5 or e7
        if ent is not None:
            c = pct_ansi_color(ent.remaining_pct)
            return f"{SUBTLE}{tag}{RESET} {c}{ent.remaining_pct:>3}%{RESET}"
        return f"{SUBTLE}{tag}  --{RESET}"

    parts: List[str] = []
    if "Gemini" in grouped:
        parts.append(_fmt_pair("G", grouped["Gemini"]))
    if "Claude/GPT" in grouped and width >= 68:
        parts.append(_fmt_pair("C", grouped["Claude/GPT"]))
    for grp, wins in grouped.items():
        if grp not in ("Gemini", "Claude/GPT") and width >= 78:
            parts.append(_fmt_pair(grp[0].upper(), wins))

    return "   ".join(parts) if parts else f"{SUBTLE}--{RESET}"


def _quota_detail_lines(prof: Optional[ProfileInfo], width: int) -> List[str]:
    if prof is None:
        return ["", "", ""]

    who = prof.email or prof.name
    plan_s = f" ({prof.plan})" if prof.plan else ""
    fetched = (prof.quota or {}).get("fetched_at", "")
    sync_s = f"  ·  synced {fetched}" if fetched else ""
    hdr = f"  {SUBTLE}Quota · {_trunc(who + plan_s, max(20, width - 30))}{sync_s}{RESET}"

    grouped = _group_entries(prof.quota)
    if not grouped:
        return [
            hdr,
            f"    {SUBTLE}No quota cached yet — select '↻ Refresh quota'{RESET}",
            "",
        ]

    bar_w = 8 if width >= 76 else (5 if width >= 60 else 0)

    def _fmt_win(label: str, ent: Optional[QuotaEntry]) -> str:
        if ent is None:
            return f"{SUBTLE}{label} --{RESET}"
        col = pct_ansi_color(ent.remaining_pct)
        bar = f"{make_bar(ent.remaining_pct, width=bar_w)} " if bar_w else ""
        rst = ent.reset_human()
        rst_tag = f"({rst})" if rst else ""
        rst_s = f" {SUBTLE}{rst_tag:<9}{RESET}"
        return f"{MUTED}{label}{RESET} {col}{bar}{ent.remaining_pct:>3}%{RESET}{rst_s}"

    out_rows: List[str] = [hdr]
    for grp_name in ("Gemini", "Claude/GPT"):
        wins = grouped.get(grp_name)
        if wins:
            w5 = _fmt_win("5h", wins.get("5h"))
            w7 = _fmt_win("7d", wins.get("7d"))
            out_rows.append(f"    {SKY}{grp_name:<11}{RESET} {w5}  {w7}")
        else:
            out_rows.append(f"    {SUBTLE}{grp_name:<11} --{RESET}")

    return out_rows[:3]


@dataclass
class MenuItem:
    kind: str
    key: str
    label: str
    profile: Optional[ProfileInfo] = None


class AgmTUI:

    def __init__(self, store: ProfileStore) -> None:
        self.store = store
        self.logo = render_logo_lines()
        self.cursor = 0
        self.toast = ""
        self.toast_color = GREEN
        self.launch_agy_after = False
        self.restart_self_after = False

        self.live_fp: Optional[str] = None
        self.live_email: str = ""
        self.profiles: List[ProfileInfo] = []
        self.active_prof: Optional[ProfileInfo] = None
        self.items: List[MenuItem] = []
        self.gui_running: bool = False

    def _set_toast(self, msg: str, color: str = GREEN) -> None:
        self.toast = msg
        self.toast_color = color

    def _reload_state(self) -> None:
        self.live_fp, self.live_email = self.store.get_live_info()
        self.profiles = self.store.list_profiles(live_fp=self.live_fp)
        self.active_prof = next((p for p in self.profiles if p.is_active), None)
        self.gui_running = is_antigravity_gui_running()

        items: List[MenuItem] = []
        for p in self.profiles:
            items.append(MenuItem(kind="account", key=p.name, label=p.name, profile=p))
        items.append(MenuItem(kind="action", key="login", label="+ Add new account"))
        items.append(MenuItem(kind="action", key="quota", label="↻ Refresh quotas"))
        if self.profiles:
            items.append(MenuItem(kind="action", key="rename", label="✎ Rename account"))
            items.append(MenuItem(kind="action", key="delete", label="− Remove account"))
        items.append(MenuItem(kind="action", key="update", label="↑ Check updates"))
        items.append(MenuItem(kind="action", key="run_agy", label="▸ Launch agy"))
        if has_display():
            items.append(MenuItem(kind="action", key="gui", label="◻ Open GUI"))
        items.append(MenuItem(kind="action", key="quit", label="× Quit"))
        self.items = items
        self.cursor = max(0, min(self.cursor, len(self.items) - 1))

    def _auto_save_live_if_needed(self) -> None:
        live_fp, live_email = self.store.get_live_info()
        if not live_fp:
            return
        profiles = self.store.list_profiles(live_fp=live_fp)
        if any(p.is_active for p in profiles):
            return
        base_name = suggest_profile_name(live_email, fallback="default")
        candidate = base_name
        counter = 2
        while self.store.profile_exists(candidate):
            candidate = f"{base_name}-{counter}"
            counter += 1
        try:
            prof = self.store.save_current(candidate, email=live_email, overwrite=False)
            self._set_toast(f"Saved active session as '{prof.name}'", MUTED)
        except Exception:
            pass

    def _render(self) -> None:
        term = shutil.get_terminal_size(fallback=(84, 24))
        w = max(44, term.columns)
        h = max(16, term.lines)

        env_str = "Termux" if is_termux() else "Linux"
        backend_short = self.store.backend.name.split(" ")[0]
        gui_note = " · GUI running" if self.gui_running else ""

        h1 = f"{TITLE_BLUE}{BOLD}Antigravity Manager v{__version__}{RESET}"
        if self.active_prof:
            who = self.active_prof.email or self.active_prof.name
            plan_tail = f" ({self.active_prof.plan})" if self.active_prof.plan else ""
            h2 = f"{WHITE}{_trunc(who, w - 34)}{RESET}{MUTED}{plan_tail}{RESET}"
        elif self.live_email:
            h2 = f"{WHITE}{_trunc(self.live_email, w - 20)}{RESET}"
        else:
            h2 = f"{YELLOW}Not logged in{RESET}"
        h3 = f"{MUTED}{env_str} ({backend_short}) · CLI & GUI{gui_note}{RESET}"
        h4 = f"{SUBTLE}{len(self.profiles)} account{'s' if len(self.profiles) != 1 else ''} saved{RESET}"

        header_rows = [
            f"  {self.logo[i]}    {info}"
            for i, info in enumerate((h1, h2, h3, h4))
        ]

        acct_rows: List[str] = []
        if self.profiles:
            acct_rows.append(f"  {SUBTLE}Accounts{RESET}")
            right_reserve = 35 if w >= 68 else 20
            label_col_w = max(18, min(32, w - right_reserve - 6))
            for idx, item in enumerate(self.items):
                if item.kind != "account" or item.profile is None:
                    continue
                p = item.profile
                is_sel = idx == self.cursor
                ptr = f"{TITLE_BLUE}❯{RESET}" if is_sel else " "
                dot = f"{GREEN}●{RESET}" if p.is_active else f"{SUBTLE}○{RESET}"
                lbl_c = f"{BOLD}{WHITE}" if is_sel else (WHITE if p.is_active else MUTED)
                if p.email:
                    prefix = suggest_profile_name(p.email)
                    display_lbl = p.email if p.name == prefix else f"{p.name} ({p.email})"
                else:
                    display_lbl = p.name
                padded_lbl = _trunc(display_lbl, label_col_w).ljust(label_col_w)
                plan_col = _plan_badge_colored(p.plan)
                q_col = _row_quota_summary(p.quota, w)
                acct_rows.append(f"  {ptr} {dot} {lbl_c}{padded_lbl}{RESET}  {plan_col}  {q_col}")

        action_rows: List[str] = [f"  {SUBTLE}Actions{RESET}"]
        for idx, item in enumerate(self.items):
            if item.kind != "action":
                continue
            is_sel = idx == self.cursor
            ptr = f"{TITLE_BLUE}❯{RESET}" if is_sel else " "
            lbl_c = f"{BOLD}{WHITE}" if is_sel else MUTED
            action_rows.append(f"  {ptr} {lbl_c}{item.label}{RESET}")

        cur = self.items[self.cursor]
        focus_prof = cur.profile if (cur.kind == "account" and cur.profile) else self.active_prof
        quota_rows = _quota_detail_lines(focus_prof, w)

        if self.toast:
            footer_row = f"  {self.toast_color}{_trunc(self.toast, w - 4)}{RESET}"
        else:
            footer_row = f"  {SUBTLE}↑↓ move  ·  Enter select  ·  r run agy  ·  q quit{RESET}"

        spacious = len(header_rows) + len(acct_rows) + len(action_rows) + len(quota_rows) + 4 <= h
        lines: List[str] = []
        lines.extend(header_rows)
        if spacious:
            lines.append("")
        if acct_rows:
            lines.extend(acct_rows)
            if spacious:
                lines.append("")
        lines.extend(action_rows)
        if spacious:
            lines.append("")
        lines.extend(quota_rows)
        if len(lines) + 2 <= h:
            lines.append("")
        lines.append(footer_row)

        if len(lines) > h:
            lines = lines[:h]

        frame = HOME + "\n".join(line + CLEAR_EOL for line in lines) + CLEAR_BELOW
        os.write(sys.stdout.fileno(), frame.encode("utf-8"))

    def _pick_account(self, title: str) -> Optional[ProfileInfo]:
        idx = 0
        while True:
            lines: List[str] = ["", f"  {TITLE_BLUE}{BOLD}{title}{RESET}", ""]
            for i, p in enumerate(self.profiles):
                is_sel = i == idx
                ptr = f"{TITLE_BLUE}❯{RESET}" if is_sel else " "
                dot = f"{GREEN}●{RESET}" if p.is_active else f"{SUBTLE}○{RESET}"
                lbl_c = f"{BOLD}{WHITE}" if is_sel else MUTED
                who = p.email or p.name
                plan_s = f" ({p.plan})" if p.plan else ""
                lines.append(f"  {ptr} {dot} {lbl_c}{p.name}{RESET}  {MUTED}{who}{plan_s}{RESET}")
            lines.append("")
            lines.append(f"  {SUBTLE}↑↓ move  ·  Enter select  ·  Esc cancel{RESET}")
            frame = HOME + "\n".join(line + CLEAR_EOL for line in lines) + CLEAR_BELOW
            os.write(sys.stdout.fileno(), frame.encode("utf-8"))

            key = _read_key()
            if key in ("q", "Q", "ESC", "CTRL_C"):
                self._set_toast("Cancelled.", MUTED)
                return None
            if key in ("UP", "k", "K"):
                idx = (idx - 1) % len(self.profiles)
            elif key in ("DOWN", "j", "J"):
                idx = (idx + 1) % len(self.profiles)
            elif key == "ENTER":
                return self.profiles[idx]

    def _rename_account_flow(self) -> None:
        try:
            target = self._pick_account("Rename Account")
            if not target:
                return
            os.write(sys.stdout.fileno(), (HOME + CLEAR_BELOW + SHOW_CURSOR).encode("utf-8"))
            print(f"\n  {TITLE_BLUE}{BOLD}Rename Account{RESET}  {MUTED}({target.email or target.name}){RESET}")
            new_name = _prompt_line("New name", default=target.name)
            if new_name and new_name != target.name:
                self.store.rename_profile(target.name, new_name)
                self._set_toast(f"✓ Renamed '{target.name}' → '{new_name}'", GREEN)
            else:
                self._set_toast("Cancelled.", MUTED)
        except Exception as exc:
            self._set_toast(f"✗ {exc}", RED)
        finally:
            os.write(sys.stdout.fileno(), (HOME + CLEAR_BELOW + HIDE_CURSOR).encode("utf-8"))
            self._reload_state()

    def _delete_account_flow(self) -> None:
        try:
            target = self._pick_account("Remove Account from AGM")
            if not target:
                return
            os.write(sys.stdout.fileno(), (HOME + CLEAR_BELOW + SHOW_CURSOR).encode("utf-8"))
            print(f"\n  {TITLE_BLUE}{BOLD}Remove Account{RESET}  {MUTED}({target.email or target.name}){RESET}")
            if _confirm(f"Remove '{target.name}' from AGM?"):
                self.store.remove_profile(target.name)
                self._set_toast(f"✓ Removed '{target.name}'", YELLOW)
            else:
                self._set_toast("Cancelled.", MUTED)
        except Exception as exc:
            self._set_toast(f"✗ {exc}", RED)
        finally:
            os.write(sys.stdout.fileno(), (HOME + CLEAR_BELOW + HIDE_CURSOR).encode("utf-8"))
            self._reload_state()

    def run(self) -> int:
        if not sys.stdin.isatty():
            print("Interactive menu requires a TTY. Use `agm --help` for CLI commands.")
            return 1

        self._auto_save_live_if_needed()
        self._reload_state()

        os.write(sys.stdout.fileno(), (ALT_SCREEN_ON + HIDE_CURSOR).encode("utf-8"))
        try:
            while True:
                self._render()
                key = _read_key()

                if key in ("q", "Q", "ESC", "CTRL_C"):
                    break

                elif key in ("UP", "k", "K"):
                    self.toast = ""
                    self.cursor = (self.cursor - 1) % len(self.items)

                elif key in ("DOWN", "j", "J"):
                    self.toast = ""
                    self.cursor = (self.cursor + 1) % len(self.items)

                elif key in ("r", "R"):
                    cur = self.items[self.cursor]
                    if cur.kind == "account" and cur.profile:
                        try:
                            self.store.switch_to(cur.profile.name)
                            self.launch_agy_after = True
                            break
                        except Exception as exc:
                            self._set_toast(f"✗ {exc}", RED)

                elif key == "ENTER":
                    cur = self.items[self.cursor]

                    if cur.kind == "account" and cur.profile:
                        try:
                            self.store.switch_to(cur.profile.name)
                            self._reload_state()
                            hint = " (restart GUI to apply)" if self.gui_running else ""
                            self._set_toast(f"✓ Switched to {cur.profile.name}{hint}", GREEN)
                        except Exception as exc:
                            self._set_toast(f"✗ {exc}", RED)

                    elif cur.key == "login":
                        os.write(sys.stdout.fileno(), (ALT_SCREEN_OFF + SHOW_CURSOR).encode("utf-8"))
                        try:
                            msg = run_login_flow(
                                self.store,
                                interactive_name=True,
                                fetch_quota=True,
                            )
                            self._set_toast(f"✓ {msg}", GREEN)
                        except Exception as exc:
                            self._set_toast(f"✗ {exc}", RED)
                        finally:
                            os.write(sys.stdout.fileno(), (ALT_SCREEN_ON + HIDE_CURSOR).encode("utf-8"))
                            self._reload_state()

                    elif cur.key == "quota":
                        if not self.profiles:
                            self._set_toast("No saved accounts to refresh.", YELLOW)
                            continue
                        if len(self.profiles) == 1:
                            self._set_toast(f"Syncing quota & plan for {self.profiles[0].name}...", CYAN)
                            self._render()
                            try:
                                self.store.refresh_active_quota()
                                self._reload_state()
                                self._set_toast("✓ Quota & plan updated", GREEN)
                            except Exception as exc:
                                self._set_toast(f"✗ {exc}", RED)
                        else:
                            def _prog(pname: str, i: int, total: int) -> None:
                                self._set_toast(f"Syncing ({i}/{total}): {pname}...", CYAN)
                                self._render()

                            try:
                                res = self.store.refresh_all_quotas(progress_cb=_prog)
                                self._reload_state()
                                ok_cnt = sum(1 for ok, _ in res.values() if ok)
                                self._set_toast(
                                    f"✓ Updated quotas & plans ({ok_cnt}/{len(res)})",
                                    GREEN if ok_cnt == len(res) else YELLOW,
                                )
                            except Exception as exc:
                                self._set_toast(f"✗ {exc}", RED)

                    elif cur.key == "rename":
                        self._rename_account_flow()

                    elif cur.key == "delete":
                        self._delete_account_flow()

                    elif cur.key == "run_agy":
                        self.launch_agy_after = True
                        break

                    elif cur.key == "gui":
                        self._set_toast("GUI open — close window to return here", CYAN)
                        self._render()
                        from .gui import launch_gui

                        launch_gui(self.store)
                        self._reload_state()
                        self._set_toast("Returned from GUI", MUTED)

                    elif cur.key == "update":
                        self._set_toast("Checking GitHub for updates...", CYAN)
                        self._render()
                        updated, msg = check_and_update()
                        if updated:
                            self._set_toast(f"✓ {msg} — restarting...", GREEN)
                            self._render()
                            time.sleep(0.6)
                            self.restart_self_after = True
                            break
                        else:
                            col = GREEN if "up to date" in msg.lower() else YELLOW
                            self._set_toast(msg, col)

                    elif cur.key == "quit":
                        break

        finally:
            os.write(sys.stdout.fileno(), (SHOW_CURSOR + ALT_SCREEN_OFF + RESET).encode("utf-8"))

        if self.restart_self_after:
            agm_entry = str(Path(__file__).resolve().parent.parent / "agm")
            os.execv(sys.executable, [sys.executable, agm_entry])

        if self.launch_agy_after:
            agy_bin = find_agy_binary()
            if agy_bin:
                os.execv(agy_bin, [agy_bin])
            else:
                print("Error: `agy` binary not found in PATH.")
                return 1

        return 0
