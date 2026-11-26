from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from . import __version__
from .backend import has_display, is_antigravity_gui_running
from .quota import (
    format_quota_compact_plain,
    load_quota_entries,
    make_bar,
)
from .store import ProfileInfo, ProfileStore, suggest_profile_name

BG_MAIN = "#0f172a"
BG_CARD = "#1e293b"
BG_INPUT = "#0b1220"
BG_HOVER = "#334155"
FG_TEXT = "#f8fafc"
FG_MUTED = "#94a3b8"
ACCENT = "#38bdf8"
ACCENT_DARK = "#0284c7"
INDIGO = "#6366f1"
GREEN = "#34d399"
YELLOW = "#fbbf24"
RED = "#f87171"


def _find_terminal_cmd(agm_cmd: str) -> Optional[List[str]]:
    candidates = [
        ("x-terminal-emulator", ["-e", agm_cmd, "login"]),
        ("gnome-terminal", ["--", agm_cmd, "login"]),
        ("konsole", ["-e", agm_cmd, "login"]),
        ("xfce4-terminal", ["-e", f"{agm_cmd} login"]),
        ("kitty", [agm_cmd, "login"]),
        ("alacritty", ["-e", agm_cmd, "login"]),
        ("xterm", ["-e", agm_cmd, "login"]),
    ]
    for bin_name, args in candidates:
        path = shutil.which(bin_name)
        if path:
            return [path, *args]
    return None


def _pct_tk_color(pct: int) -> str:
    if pct >= 50:
        return GREEN
    if pct >= 20:
        return YELLOW
    return RED


def launch_gui(store: ProfileStore) -> int:
    if not has_display():
        print("Error: No graphical display detected. Run `agm` for the terminal menu.")
        return 1

    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print(
            "Error: python3-tk is not installed.\n"
            "Install it with: sudo apt install python3-tk\n"
            "Or run `agm` for the interactive terminal menu."
        )
        return 1

    root = tk.Tk()
    root.title(f"AGM v{__version__} — Antigravity Account Manager")
    root.geometry("680x580")
    root.minsize(560, 480)
    root.configure(bg=BG_MAIN)

    profiles_cache: List[ProfileInfo] = []
    busy_flag = {"busy": False}

    header = tk.Frame(root, bg=BG_CARD, padx=18, pady=14)
    header.pack(fill="x")

    tk.Label(
        header,
        text=f"◆ AGM v{__version__} · Antigravity Account Manager",
        font=("Sans", 13, "bold"),
        bg=BG_CARD,
        fg=FG_TEXT,
    ).pack(anchor="w")

    active_var = tk.StringVar(value="Loading...")
    active_lbl = tk.Label(
        header,
        textvariable=active_var,
        font=("Sans", 10, "bold"),
        bg=BG_CARD,
        fg=GREEN,
    )
    active_lbl.pack(anchor="w", pady=(4, 0))

    tk.Label(
        header,
        text=f"Syncs agy CLI & Antigravity GUI  ·  Backend: {store.backend.name}",
        font=("Sans", 8),
        bg=BG_CARD,
        fg=FG_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    body = tk.Frame(root, bg=BG_MAIN, padx=18, pady=12)
    body.pack(fill="both", expand=True)

    tk.Label(
        body,
        text="Saved Accounts (double-click to switch)",
        font=("Sans", 9, "bold"),
        bg=BG_MAIN,
        fg=FG_MUTED,
    ).pack(anchor="w", pady=(0, 6))

    list_frame = tk.Frame(
        body, bg=BG_CARD, bd=0, highlightthickness=1, highlightbackground="#334155"
    )
    list_frame.pack(fill="both", expand=True)

    listbox = tk.Listbox(
        list_frame,
        bg=BG_CARD,
        fg=FG_TEXT,
        selectbackground=ACCENT_DARK,
        selectforeground="#ffffff",
        activestyle="none",
        bd=0,
        highlightthickness=0,
        font=("Monospace", 10),
        exportselection=False,
    )
    scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    scrollbar.pack(side="right", fill="y")

    quota_card = tk.Frame(
        body, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground="#334155"
    )
    quota_card.pack(fill="x", pady=(10, 0))

    quota_title_var = tk.StringVar(value="Usage Quota")
    tk.Label(
        quota_card,
        textvariable=quota_title_var,
        font=("Sans", 9, "bold"),
        bg=BG_CARD,
        fg=ACCENT,
    ).pack(anchor="w")

    quota_rows_frame = tk.Frame(quota_card, bg=BG_CARD)
    quota_rows_frame.pack(fill="x", pady=(6, 0))

    status_var = tk.StringVar(value="Ready.")
    status_lbl = tk.Label(
        root,
        textvariable=status_var,
        font=("Sans", 9),
        bg=BG_CARD,
        fg=FG_MUTED,
        anchor="w",
        padx=18,
        pady=8,
    )
    status_lbl.pack(fill="x", side="bottom")

    def set_status(msg: str, color: str = FG_MUTED) -> None:
        status_var.set(msg)
        status_lbl.configure(fg=color)

    def render_quota_panel(prof: Optional[ProfileInfo]) -> None:
        for child in quota_rows_frame.winfo_children():
            child.destroy()

        if prof is None:
            quota_title_var.set("Usage Quota")
            tk.Label(
                quota_rows_frame,
                text="Select an account to view its usage quota.",
                font=("Sans", 9),
                bg=BG_CARD,
                fg=FG_MUTED,
            ).pack(anchor="w")
            return

        fetched = (prof.quota or {}).get("fetched_at", "")
        sync_tag = f"  ·  synced {fetched}" if fetched else ""
        quota_title_var.set(f"Usage Quota · {prof.name}{sync_tag}")

        entries = load_quota_entries(prof.quota)
        if not entries:
            tk.Label(
                quota_rows_frame,
                text="No quota data cached yet. Click '↻ Quota' or '↻ All Quotas' to fetch.",
                font=("Sans", 9),
                bg=BG_CARD,
                fg=FG_MUTED,
            ).pack(anchor="w")
            return

        for ent in entries:
            bar = make_bar(ent.remaining_pct, width=12)
            rst = ent.reset_human()
            rst_str = f"  (resets in {rst})" if rst else ""
            row_txt = f"{ent.short_group():<11} {ent.short_window():<3}  {bar}  {ent.remaining_pct:>3}%{rst_str}"
            tk.Label(
                quota_rows_frame,
                text=row_txt,
                font=("Monospace", 9),
                bg=BG_CARD,
                fg=_pct_tk_color(ent.remaining_pct),
            ).pack(anchor="w")

    def get_selected() -> Optional[ProfileInfo]:
        sel = listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(profiles_cache):
            return profiles_cache[idx]
        return None

    def refresh() -> None:
        nonlocal profiles_cache
        prev_sel = get_selected()
        prev_name = prev_sel.name if prev_sel else None

        live_fp, live_email = store.get_live_info()
        has_live = bool(live_fp)
        profiles_cache = store.list_profiles(live_fp=live_fp)
        active_name = next((p.name for p in profiles_cache if p.is_active), None)

        if active_name:
            email_tail = f" ({live_email})" if live_email else ""
            active_var.set(f"● Active: {active_name}{email_tail}")
            active_lbl.configure(fg=GREEN)
        elif has_live:
            who = live_email or f"id:{(live_fp or '')[:8]}"
            active_var.set(f"● Active: Unsaved session ({who})")
            active_lbl.configure(fg=YELLOW)
        else:
            active_var.set("○ Not logged in")
            active_lbl.configure(fg=RED)

        listbox.delete(0, tk.END)
        sel_idx = 0
        for idx, prof in enumerate(profiles_cache):
            if prev_name and prof.name == prev_name:
                sel_idx = idx
            marker = "●" if prof.is_active else "○"
            badge = " [ACTIVE]" if prof.is_active else ""
            plan_tag = f" [{prof.plan}]" if prof.plan else ""
            who = prof.email or f"id:{prof.fingerprint[:8]}"
            q_compact = format_quota_compact_plain(prof.quota)
            listbox.insert(
                tk.END, f" {marker} {prof.name}{badge}{plan_tag}  ·  {who}  │  {q_compact}"
            )

        if profiles_cache:
            listbox.selection_set(sel_idx)
            render_quota_panel(profiles_cache[sel_idx])
        else:
            render_quota_panel(None)

    def on_select(_event=None) -> None:
        render_quota_panel(get_selected())

    def open_profile_dialog(
        title: str,
        name_init: str = "",
        email_init: str = "",
        note_init: str = "",
        lock_name: bool = False,
    ) -> Optional[tuple[str, str, str]]:
        result: Optional[tuple[str, str, str]] = None
        dlg = tk.Toplevel(root)
        dlg.title(title)
        dlg.geometry("400x250")
        dlg.configure(bg=BG_MAIN)
        dlg.transient(root)
        dlg.grab_set()

        frm = tk.Frame(dlg, bg=BG_MAIN, padx=18, pady=14)
        frm.pack(fill="both", expand=True)

        def add_field(lbl: str, initial: str, disabled: bool = False) -> tk.Entry:
            tk.Label(frm, text=lbl, bg=BG_MAIN, fg=FG_MUTED, font=("Sans", 9)).pack(
                anchor="w", pady=(6, 2)
            )
            ent = tk.Entry(
                frm,
                bg=BG_INPUT,
                fg=FG_TEXT,
                insertbackground=FG_TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground="#334155",
                highlightcolor=ACCENT,
                font=("Sans", 10),
            )
            ent.insert(0, initial)
            if disabled:
                ent.configure(
                    state="disabled",
                    disabledbackground=BG_CARD,
                    disabledforeground=FG_MUTED,
                )
            ent.pack(fill="x", ipady=4)
            return ent

        e_name = add_field("Profile Name (e.g. personal, work)", name_init, disabled=lock_name)
        e_email = add_field("Email / Label (auto-detected)", email_init)
        e_note = add_field("Short Note (optional)", note_init)

        def on_ok() -> None:
            nonlocal result
            result = (e_name.get().strip(), e_email.get().strip(), e_note.get().strip())
            dlg.destroy()

        btns = tk.Frame(frm, bg=BG_MAIN)
        btns.pack(fill="x", pady=(14, 0))
        tk.Button(
            btns,
            text="Cancel",
            command=dlg.destroy,
            bg=BG_CARD,
            fg=FG_TEXT,
            activebackground=BG_HOVER,
            activeforeground=FG_TEXT,
            relief="flat",
            padx=14,
            pady=5,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            btns,
            text="Save",
            command=on_ok,
            bg=ACCENT_DARK,
            fg="#ffffff",
            activebackground=ACCENT,
            activeforeground="#0f172a",
            relief="flat",
            padx=16,
            pady=5,
        ).pack(side="right")

        (e_email if lock_name else e_name).focus_set()
        dlg.bind("<Return>", lambda _e: on_ok())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        root.wait_window(dlg)
        return result

    def do_switch(_event=None) -> None:
        prof = get_selected()
        if not prof:
            set_status("Select an account first.", YELLOW)
            return
        try:
            store.switch_to(prof.name)
            refresh()
            hint = (
                " — Restart Antigravity GUI to apply."
                if is_antigravity_gui_running()
                else ""
            )
            set_status(f"Switched to '{prof.name}'{hint}", GREEN)
        except Exception as exc:
            messagebox.showerror("Switch Failed", str(exc))

    def do_save_current() -> None:
        live_fp, live_email = store.get_live_info()
        if not live_fp:
            messagebox.showwarning(
                "No Active Session",
                "No active Antigravity login found.\nClick '+ Login New' to sign in.",
            )
            return
        suggested = suggest_profile_name(live_email)
        res = open_profile_dialog(
            "Save Current Active Account",
            name_init=suggested,
            email_init=live_email,
        )
        if not res:
            return
        name, email, note = res
        if not name:
            return
        overwrite = False
        if store.profile_exists(name):
            overwrite = messagebox.askyesno(
                "Overwrite Profile", f"Profile '{name}' already exists. Overwrite it?"
            )
            if not overwrite:
                return
        try:
            prof = store.save_current(name, email=email, note=note, overwrite=overwrite)
            refresh()
            set_status(f"Saved active login as '{prof.name}'.", GREEN)
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc))

    def do_login_new() -> None:
        agm_exe = str(Path(__file__).resolve().parent.parent / "agm")
        term_cmd = _find_terminal_cmd(agm_exe)
        if not term_cmd:
            messagebox.showinfo(
                "Terminal Needed",
                "Could not detect a terminal emulator automatically.\n"
                "Run `agm login` in your terminal to sign into a new account.",
            )
            return

        set_status("Launched terminal for new account login...", ACCENT)

        def _worker() -> None:
            try:
                subprocess.run(term_cmd, check=False)
            finally:
                root.after(0, lambda: (refresh(), set_status("Login window closed — list refreshed.", GREEN)))

        threading.Thread(target=_worker, daemon=True).start()

    def do_refresh_quota() -> None:
        if busy_flag["busy"]:
            return
        busy_flag["busy"] = True
        set_status("Fetching live usage quota via `agy`...", ACCENT)

        def _worker() -> None:
            try:
                matched, _ = store.refresh_active_quota()
                msg = f"Updated quota for '{matched or 'active session'}'."
                root.after(0, lambda: (refresh(), set_status(msg, GREEN)))
            except Exception as exc:
                err = str(exc)
                root.after(0, lambda: set_status(f"Quota error: {err}", RED))
            finally:
                busy_flag["busy"] = False

        threading.Thread(target=_worker, daemon=True).start()

    def do_refresh_all_quotas() -> None:
        if busy_flag["busy"]:
            return
        if not profiles_cache:
            set_status("No saved accounts to refresh.", YELLOW)
            return
        busy_flag["busy"] = True
        set_status("Syncing usage quotas for all accounts...", ACCENT)

        def _prog(pname: str, i: int, total: int) -> None:
            root.after(
                0, lambda p=pname, idx=i, tot=total: set_status(f"Syncing quota ({idx}/{tot}): {p}...", ACCENT)
            )

        def _worker() -> None:
            try:
                res = store.refresh_all_quotas(progress_cb=_prog)
                ok_cnt = sum(1 for ok, _ in res.values() if ok)
                msg = f"Synced quota for {ok_cnt}/{len(res)} accounts."
                col = GREEN if ok_cnt == len(res) else YELLOW
                root.after(0, lambda: (refresh(), set_status(msg, col)))
            except Exception as exc:
                err = str(exc)
                root.after(0, lambda: set_status(f"Quota sync error: {err}", RED))
            finally:
                busy_flag["busy"] = False

        threading.Thread(target=_worker, daemon=True).start()

    def do_edit() -> None:
        prof = get_selected()
        if not prof:
            set_status("Select an account to edit.", YELLOW)
            return
        res = open_profile_dialog(
            f"Edit '{prof.name}'",
            name_init=prof.name,
            email_init=prof.email,
            note_init=prof.note,
            lock_name=True,
        )
        if not res:
            return
        _, email, note = res
        try:
            store.update_meta(prof.name, email=email, note=note)
            refresh()
            set_status(f"Updated '{prof.name}'.", GREEN)
        except Exception as exc:
            messagebox.showerror("Update Failed", str(exc))

    def do_delete() -> None:
        prof = get_selected()
        if not prof:
            set_status("Select an account to delete.", YELLOW)
            return
        if not messagebox.askyesno(
            "Delete Profile", f"Delete saved profile '{prof.name}'?"
        ):
            return
        try:
            store.remove_profile(prof.name)
            refresh()
            set_status(f"Deleted '{prof.name}'.", YELLOW)
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc))

    actions = tk.Frame(body, bg=BG_MAIN)
    actions.pack(fill="x", pady=(12, 0))

    def mk_btn(parent, text: str, cmd, bg: str = BG_CARD, fg: str = FG_TEXT):
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            activebackground=BG_HOVER,
            activeforeground=FG_TEXT,
            relief="flat",
            font=("Sans", 9, "bold"),
            padx=11,
            pady=7,
            cursor="hand2",
        )

    mk_btn(actions, "Switch", do_switch, bg=ACCENT_DARK, fg="#ffffff").pack(side="left", padx=(0, 6))
    mk_btn(actions, "+ Login New", do_login_new, bg=INDIGO, fg="#ffffff").pack(side="left", padx=(0, 6))
    mk_btn(actions, "Save Current", do_save_current).pack(side="left", padx=(0, 6))
    mk_btn(actions, "↻ Quota", do_refresh_quota, fg=ACCENT).pack(side="left", padx=(0, 6))
    mk_btn(actions, "↻ All", do_refresh_all_quotas, fg=ACCENT).pack(side="left", padx=(0, 6))
    mk_btn(actions, "Edit", do_edit).pack(side="left", padx=(0, 6))
    mk_btn(actions, "Del", do_delete, fg=RED).pack(side="right")

    listbox.bind("<<ListboxSelect>>", on_select)
    listbox.bind("<Double-Button-1>", do_switch)
    root.bind("<Escape>", lambda _e: root.destroy())

    refresh()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(launch_gui(ProfileStore()))
