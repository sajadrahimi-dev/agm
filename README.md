# AGM — Antigravity Account Manager (`v1.0.0`)

Lightweight, zero-dependency account manager & usage quota viewer for **Antigravity (`agy` CLI & Desktop GUI)** on **Linux** and **Termux**.

## Features

- **Minimal Flicker-Free TUI (`agm`)** — Clean Antigravity-style interface with `↑/↓` and `Enter` navigation, no terminal clutter, and responsive layout for Linux terminals and Termux.
- **Desktop GUI (`agm gui`)** — Lightweight dark-themed window powered by Python stdlib `tkinter`.
- **Unified CLI & GUI Sync** — Switches accounts for both `agy` CLI and Antigravity Desktop App via the system keyring (`gemini:antigravity`) with automatic file fallback on Termux/headless Linux.
- **Auto-Detect Email & Plan** — Automatically detects the logged-in email (`id_token_email`) and subscription plan (`Google AI Pro`, `Ultra`, `Free`, etc.).
- **Usage Quota per Account** — Shows 5-hour (`5h`) and weekly (`7d`) remaining limits with reset countdowns for both **Gemini** and **Claude/GPT** models.
- **In-App Login** — Log into new accounts directly from `agm` with safe rollback if cancelled.
- **Secure Local Vault** — Stores profiles in `~/.config/agm/profiles/` with `0700`/`0600` permissions and atomic writes.

---

## Installation

### Linux

```bash
git clone https://github.com/sajadrahimi-dev/agm.git ~/.agm
cd ~/.agm && ./install.sh
```

> Installs symlink to `~/.local/bin/agm`. Make sure `~/.local/bin` is in your `PATH`.
> For desktop GUI (`agm gui`), `python3-tk` is required (`sudo apt install python3-tk`).

### Termux (Android)

```bash
pkg update && pkg install -y git python
git clone https://github.com/sajadrahimi-dev/agm.git ~/.agm
cd ~/.agm && ./install.sh
```

> Automatically detects Termux and installs `agm` into `$PREFIX/bin/agm`.

---

## Usage

```bash
agm                     # Open interactive menu
agm gui                 # Open desktop GUI window
agm login               # Log into a new account via agy
agm save [name]         # Save active session (auto-detects email)
agm use <name>          # Switch active account (CLI & GUI)
agm use <name> --run    # Switch account and launch agy immediately
agm quota               # Fetch live usage quota & plan for active account
agm quota --all         # Sync usage quota & plan for all saved accounts
agm ls                  # List saved accounts with plan & quotas
agm status              # Show currently active account
agm rename <old> <new>  # Rename a saved account
agm rm <name>           # Delete a saved account
agm doctor              # Check environment, backend, and agy binary
```

## Uninstall

**Linux:**
```bash
rm -f ~/.local/bin/agm && rm -rf ~/.agm
```

**Termux:**
```bash
rm -f "$PREFIX/bin/agm" && rm -rf ~/.agm
```
