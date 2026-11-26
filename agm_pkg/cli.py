from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .backend import (
    find_agy_binary,
    get_backend,
    has_display,
    is_antigravity_gui_running,
    is_termux,
)
from .quota import load_quota_entries, make_bar, pct_ansi_color
from .store import ProfileStore
from .tui import (
    BOLD,
    CYAN,
    GRAY,
    GREEN,
    RED,
    RESET,
    SKY,
    WHITE,
    YELLOW,
    AgmTUI,
    run_login_flow,
)


def _ok(msg: str) -> None:
    print(f"{GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}!{RESET} {msg}")


def _err(msg: str) -> None:
    print(f"{RED}✗{RESET} {msg}", file=sys.stderr)


def _print_quota_entries(quota_dict: Optional[dict], indent: str = "    ") -> None:
    entries = load_quota_entries(quota_dict)
    if not entries:
        print(f"{indent}{GRAY}Quota: not synced yet (run `agm quota`){RESET}")
        return
    for ent in entries:
        col = pct_ansi_color(ent.remaining_pct)
        bar = make_bar(ent.remaining_pct, width=10)
        rst = ent.reset_human()
        rst_str = f" {GRAY}resets {rst}{RESET}" if rst else ""
        print(
            f"{indent}{SKY}{ent.short_group():<11}{RESET} {GRAY}{ent.short_window():<3}{RESET} "
            f"{col}{bar} {BOLD}{ent.remaining_pct:>3}%{RESET}{rst_str}"
        )


def cmd_list(store: ProfileStore) -> int:
    live_fp, live_email = store.get_live_info()
    has_live = bool(live_fp)
    profiles = store.list_profiles(live_fp=live_fp)
    active_name = next((p.name for p in profiles if p.is_active), None)

    print(f"\n  {BOLD}{CYAN}◆ AGM{RESET} {GRAY}v{__version__} · {store.backend.name}{RESET}\n")
    if not profiles:
        print(f"  {GRAY}No saved profiles yet.{RESET}")
        if has_live:
            who = f" ({live_email})" if live_email else ""
            print(f"  Run {BOLD}agm save{RESET} to save current session{who}, or {BOLD}agm login{RESET} to add new.\n")
        else:
            print(f"  Run {BOLD}agm login{RESET} to sign into your first account.\n")
        return 0

    for prof in profiles:
        dot = f"{GREEN}●{RESET}" if prof.is_active else f"{GRAY}○{RESET}"
        badge = f" {GREEN}{BOLD}[ACTIVE]{RESET}" if prof.is_active else ""
        plan_s = f" ({prof.plan})" if prof.plan else ""
        meta = " · ".join(x for x in (prof.email, prof.note) if x)
        if not meta:
            meta = f"id:{prof.fingerprint[:8]}"
        print(f"  {dot} {BOLD}{WHITE}{prof.name}{RESET}{badge}{CYAN}{plan_s}{RESET}  {GRAY}— {meta}{RESET}")
        _print_quota_entries(prof.quota, indent="      ")
        print()

    if has_live and not active_name:
        who = live_email or f"id:{(live_fp or '')[:8]}"
        print(f"  {YELLOW}!{RESET} Active session ({who}) is not saved. Run {BOLD}agm save{RESET}.\n")
    return 0


def cmd_status(store: ProfileStore) -> int:
    live_fp, live_email = store.get_live_info()
    has_live, active_name, _ = store.get_active_status()
    if active_name:
        email_str = f" ({live_email})" if live_email else ""
        _ok(f"Active account: {BOLD}{active_name}{RESET}{email_str} {GRAY}(id:{(live_fp or '')[:8]}){RESET}")
    elif has_live:
        who = live_email or f"id:{(live_fp or '')[:8]}"
        _warn(f"Active session is unsaved ({who}). Run `agm save` to store it.")
    else:
        _warn("Not logged in to Antigravity.")
    print(f"  {GRAY}Backend:{RESET} {store.backend.name}")
    print(f"  {GRAY}Applies to:{RESET} agy CLI & Antigravity GUI")
    return 0


def cmd_save(store: ProfileStore, args: argparse.Namespace) -> int:
    try:
        prof = store.save_current(
            args.name or "",
            email=args.email or "",
            note=args.note or "",
            overwrite=args.force,
        )
        email_str = f" ({prof.email})" if prof.email else ""
        _ok(f"Saved active login as {BOLD}{prof.name}{RESET}{email_str}.")
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_login(store: ProfileStore, args: argparse.Namespace) -> int:
    try:
        msg = run_login_flow(
            store,
            name=args.name or "",
            email=args.email or "",
            note=args.note or "",
            interactive_name=not bool(args.name),
            fetch_quota=not args.no_quota,
        )
        _ok(msg)
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_quota(store: ProfileStore, args: argparse.Namespace) -> int:
    if args.all:
        profiles = store.list_profiles()
        if not profiles:
            _warn("No saved profiles yet.")
            return 0
        print(f"\n  {CYAN}◆{RESET} Syncing usage quota for {len(profiles)} accounts...\n")

        def _prog(pname: str, i: int, total: int) -> None:
            print(f"  {GRAY}[{i}/{total}] Fetching {pname}...{RESET}")

        res = store.refresh_all_quotas(progress_cb=_prog)
        print()
        for prof in store.list_profiles():
            ok, msg = res.get(prof.name, (False, "skipped"))
            dot = f"{GREEN}●{RESET}" if prof.is_active else f"{GRAY}○{RESET}"
            who = f" ({prof.email})" if prof.email else ""
            print(f"  {dot} {BOLD}{WHITE}{prof.name}{RESET}{GRAY}{who}{RESET}")
            if ok:
                _print_quota_entries(prof.quota, indent="      ")
            else:
                print(f"      {RED}Error: {msg}{RESET}")
            print()
        return 0

    try:
        print(f"  {GRAY}Fetching live usage quota via `agy`...{RESET}")
        matched, quota_data = store.refresh_active_quota()
        _, live_email = store.get_live_info()
        label = matched or live_email or "Active Session"
        print(f"\n  {BOLD}{CYAN}◆ Usage Quota · {label}{RESET}\n")
        _print_quota_entries(quota_data, indent="  ")
        print()
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_use(store: ProfileStore, args: argparse.Namespace) -> int:
    try:
        prof = store.switch_to(args.name)
        email_str = f" ({prof.email})" if prof.email else ""
        _ok(f"Switched to {BOLD}{prof.name}{RESET}{email_str} (agy CLI & Antigravity GUI).")
        if is_antigravity_gui_running():
            _warn("Antigravity desktop app is running — restart it to pick up the switched account.")
        if args.run:
            agy_bin = find_agy_binary()
            if not agy_bin:
                _err("`agy` binary not found in PATH.")
                return 1
            os.execv(agy_bin, [agy_bin])
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_remove(store: ProfileStore, args: argparse.Namespace) -> int:
    try:
        store.remove_profile(args.name)
        _ok(f"Removed saved profile {BOLD}{args.name}{RESET}.")
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_rename(store: ProfileStore, args: argparse.Namespace) -> int:
    try:
        prof = store.rename_profile(args.old_name, args.new_name)
        _ok(f"Renamed {BOLD}{args.old_name}{RESET} → {BOLD}{prof.name}{RESET}.")
        return 0
    except Exception as exc:
        _err(str(exc))
        return 1


def cmd_logout(store: ProfileStore) -> int:
    store.logout_active()
    _ok("Cleared active Antigravity session (saved profiles remain in vault).")
    return 0


def cmd_doctor(store: ProfileStore) -> int:
    agy_bin = find_agy_binary()
    live_fp, live_email = store.get_live_info()
    has_live, active_name, _ = store.get_active_status()
    profiles = store.list_profiles(live_fp=live_fp)

    print(f"\n  {BOLD}{CYAN}◆ AGM Doctor{RESET} {GRAY}v{__version__}{RESET}\n")
    print(f"  Environment : {'Termux (Android)' if is_termux() else 'Linux'}")
    print(f"  Display/GUI : {'Available' if has_display() else 'Headless / Terminal only'}")
    print(f"  agy CLI     : {agy_bin or 'NOT FOUND'}")
    print(f"  GUI Running : {'Yes' if is_antigravity_gui_running() else 'No'}")
    print(f"  Backend     : {store.backend.name}")
    print(f"  Vault Dir   : {store.base_dir}")
    print(f"  Profiles    : {len(profiles)} saved")
    if active_name:
        print(f"  Live Auth   : Present — profile '{active_name}' ({live_email or 'no email'})")
    elif has_live:
        who = live_email or f"id:{(live_fp or '')[:8]}"
        print(f"  Live Auth   : Present — unsaved ({who})")
    else:
        print("  Live Auth   : None")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agm",
        description="AGM — Lightweight Account Manager for Antigravity (agy CLI & GUI)",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"agm {__version__}"
    )
    parser.add_argument(
        "--gui", action="store_true", help="Open the desktop GUI window"
    )
    parser.add_argument(
        "--backend",
        choices=["secretstorage", "keyring", "secret-tool", "file"],
        help="Override credential backend",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("menu", help="Open interactive TUI menu (default)")
    sub.add_parser("gui", help="Open lightweight desktop GUI window")

    sub.add_parser("ls", aliases=["list"], help="List saved accounts & cached quotas")
    sub.add_parser("status", help="Show currently active account & email")
    sub.add_parser("doctor", help="Check environment, backend, and agy installation")
    sub.add_parser("logout", help="Clear active login (keeps saved profiles)")

    p_save = sub.add_parser(
        "save", aliases=["add"], help="Save currently active login (auto-detects email)"
    )
    p_save.add_argument(
        "name", nargs="?", default="", help="Optional profile alias (defaults to email prefix)"
    )
    p_save.add_argument("--email", help="Override auto-detected email/label")
    p_save.add_argument("--note", help="Optional short note")
    p_save.add_argument("-f", "--force", action="store_true", help="Overwrite if profile exists")

    p_login = sub.add_parser(
        "login", help="Log into a new account via agy, auto-detect email, and save it"
    )
    p_login.add_argument(
        "name", nargs="?", default="", help="Optional profile alias (auto-suggested from email)"
    )
    p_login.add_argument("--email", help="Override auto-detected email/label")
    p_login.add_argument("--note", help="Optional short note")
    p_login.add_argument(
        "--no-quota", action="store_true", help="Skip fetching initial usage quota after login"
    )

    p_quota = sub.add_parser(
        "quota", aliases=["usage"], help="Fetch & show usage quota via `agy`"
    )
    p_quota.add_argument(
        "-a", "--all", action="store_true", help="Refresh usage quota for all saved accounts"
    )

    p_use = sub.add_parser("use", aliases=["switch"], help="Switch active account (CLI & GUI)")
    p_use.add_argument("name", help="Profile alias to activate")
    p_use.add_argument("-r", "--run", action="store_true", help="Launch agy immediately after switching")

    p_rm = sub.add_parser("rm", aliases=["remove", "delete"], help="Delete a saved profile")
    p_rm.add_argument("name", help="Profile alias to delete")

    p_ren = sub.add_parser("rename", help="Rename a saved profile")
    p_ren.add_argument("old_name", help="Current profile alias")
    p_ren.add_argument("new_name", help="New profile alias")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    backend = get_backend(force=args.backend)
    store = ProfileStore(backend=backend)

    if args.gui or args.command == "gui":
        from .gui import launch_gui

        return launch_gui(store)

    if args.command is None or args.command == "menu":
        return AgmTUI(store).run()

    if args.command in ("ls", "list"):
        return cmd_list(store)
    if args.command == "status":
        return cmd_status(store)
    if args.command in ("save", "add"):
        return cmd_save(store, args)
    if args.command == "login":
        return cmd_login(store, args)
    if args.command in ("quota", "usage"):
        return cmd_quota(store, args)
    if args.command in ("use", "switch"):
        return cmd_use(store, args)
    if args.command in ("rm", "remove", "delete"):
        return cmd_remove(store, args)
    if args.command == "rename":
        return cmd_rename(store, args)
    if args.command == "logout":
        return cmd_logout(store)
    if args.command == "doctor":
        return cmd_doctor(store)

    parser.print_help()
    return 0
