#!/usr/bin/env python3
"""
steam.py - one-click Steam account switching, cross-platform.

How switching works (the same mechanism the open-source TcNo switcher uses):
  * Steam keeps a login session token for each account it has signed in before,
    inside its own files. We never need the account password to switch.
  * To switch we (1) ask a running Steam to shut down gracefully,
    (2) set AutoLoginUser + RememberPassword in Steam's registry store,
    (3) mark the target account MostRecent in loginusers.vdf, and
    (4) relaunch Steam, which signs in with its stored token.
  * If Steam has no stored token for that account (first time, or you chose
    "don't remember"), Steam shows its normal login prompt - this tool cannot
    and does not bypass that. It only switches between accounts already set up
    on this computer.

Platform storage:
  Windows : real registry  HKCU\\Software\\Valve\\Steam  + Steam\\config\\loginusers.vdf
  Linux   : ~/.steam/registry.vdf  + ~/.steam/steam/config/loginusers.vdf
  macOS   : ~/Library/Application Support/Steam/registry.vdf  + .../config/loginusers.vdf
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


class SteamError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Minimal text-VDF (KeyValues) parser / serializer
# --------------------------------------------------------------------------- #
def vdf_parse(text: str) -> dict:
    """Parse Valve text KeyValues into nested dicts. Good enough for Steam's
    own config files (quoted keys/values, nested {}, // comments)."""
    i, n = 0, len(text)

    def skip_ws():
        nonlocal i
        while i < n:
            c = text[i]
            if c in " \t\r\n":
                i += 1
            elif text.startswith("//", i):
                while i < n and text[i] != "\n":
                    i += 1
            else:
                break

    def read_token() -> str:
        nonlocal i
        skip_ws()
        if i >= n:
            raise SteamError("unexpected end of VDF")
        if text[i] == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            return "".join(buf)
        start = i
        while i < n and text[i] not in ' \t\r\n"{}':
            i += 1
        return text[start:i]

    def parse_block() -> dict:
        nonlocal i
        obj: dict = {}
        while True:
            skip_ws()
            if i >= n or text[i] == "}":
                i += 1 if i < n else 0
                return obj
            key = read_token()
            skip_ws()
            if i < n and text[i] == "{":
                i += 1
                obj[key] = parse_block()
            else:
                obj[key] = read_token()

    skip_ws()
    root: dict = {}
    while i < n:
        key = read_token()
        skip_ws()
        if i < n and text[i] == "{":
            i += 1
            root[key] = parse_block()
        elif key:
            root[key] = read_token()
        skip_ws()
    return root


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def vdf_dump(obj: dict, indent: int = 0) -> str:
    pad = "\t" * indent
    out = []
    for k, v in obj.items():
        if isinstance(v, dict):
            out.append(f'{pad}"{_esc(k)}"\n{pad}{{\n{vdf_dump(v, indent + 1)}{pad}}}\n')
        else:
            out.append(f'{pad}"{_esc(k)}"\t\t"{_esc(str(v))}"\n')
    return "".join(out)


# --------------------------------------------------------------------------- #
# Steam locations
# --------------------------------------------------------------------------- #
@dataclass
class SteamPaths:
    root: Path
    loginusers: Path
    registry_vdf: Path | None   # None on Windows (uses the real registry)
    exe: list[str]              # command to launch Steam


def _candidate_roots() -> list[Path]:
    home = Path.home()
    if sys.platform.startswith("win"):
        roots = []
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "Steam")
        roots.append(Path("C:/Program Files (x86)/Steam"))
        return roots
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    return [home / ".steam/steam", home / ".local/share/Steam", home / ".steam/root", home / ".steam"]


def _find_launch_cmd(root: Path) -> list[str]:
    if sys.platform.startswith("win"):
        exe = root / "steam.exe"
        return [str(exe)] if exe.exists() else ["steam.exe"]
    if sys.platform == "darwin":
        return ["open", "-a", "Steam"]
    found = shutil.which("steam")
    return [found] if found else ["steam"]


def detect_steam() -> SteamPaths:
    for root in _candidate_roots():
        login = root / "config" / "loginusers.vdf"
        if login.exists():
            reg = None
            if not sys.platform.startswith("win"):
                for cand in (root / "registry.vdf", Path.home() / ".steam" / "registry.vdf"):
                    if cand.exists():
                        reg = cand
                        break
                else:
                    reg = root / "registry.vdf"
            return SteamPaths(root, login, reg, _find_launch_cmd(root))
    raise SteamError(
        "Couldn't find a Steam install (looked for config/loginusers.vdf). "
        "Set the STEAM_ROOT environment variable to your Steam folder."
    )


def _paths() -> SteamPaths:
    override = os.environ.get("STEAM_ROOT")
    if override:
        root = Path(override)
        login = root / "config" / "loginusers.vdf"
        if not login.exists():
            raise SteamError(f"STEAM_ROOT={root} has no config/loginusers.vdf")
        reg = None if sys.platform.startswith("win") else (root / "registry.vdf")
        return SteamPaths(root, login, reg, _find_launch_cmd(root))
    return detect_steam()


# --------------------------------------------------------------------------- #
# Reading accounts
# --------------------------------------------------------------------------- #
@dataclass
class SteamAccount:
    steamid: str
    account: str
    persona: str
    most_recent: bool
    remembered: bool
    timestamp: int = 0


def list_accounts(paths: SteamPaths | None = None) -> list[SteamAccount]:
    paths = paths or _paths()
    data = vdf_parse(paths.loginusers.read_text("utf-8", errors="replace"))
    users = data.get("users", {})
    out = []
    for steamid, info in users.items():
        if not isinstance(info, dict):
            continue
        out.append(SteamAccount(
            steamid=steamid,
            account=info.get("AccountName", ""),
            persona=info.get("PersonaName", ""),
            most_recent=str(info.get("MostRecent", "0")) == "1",
            remembered=str(info.get("RememberPassword", "0")) == "1",
            timestamp=int(info.get("Timestamp", "0") or 0),
        ))
    out.sort(key=lambda a: a.timestamp, reverse=True)
    return out


def avatar_path(paths: SteamPaths, steamid: str) -> Path | None:
    """Path to Steam's cached avatar for an account, if the client downloaded one."""
    cache = paths.root / "config" / "avatarcache"
    for ext in ("png", "jpg", "jpeg"):
        p = cache / f"{steamid}.{ext}"
        if p.exists():
            return p
    return None


def ssfn_files(paths: SteamPaths) -> list[Path]:
    """Steam Guard machine-auth files in the Steam root (ssfn*)."""
    try:
        return [p for p in paths.root.glob("ssfn*") if p.is_file()]
    except OSError:
        return []


def snapshot_sessions(paths: SteamPaths, store: Path) -> None:
    """Save the current signed-in state so accounts can be restored later.

    Copies loginusers.vdf (which accounts Steam remembers) and the ssfn machine-
    auth files. Copy-only and non-destructive: it never changes Steam's files.
    """
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    # Merge, don't overwrite: an account Steam has since forgotten must stay in the
    # snapshot so it can be restored later. Current live entries win for shared keys.
    snap = store / "loginusers.vdf"
    merged = {}
    if snap.exists():
        merged = vdf_parse(snap.read_text("utf-8", errors="replace")).get("users", {})
    if paths.loginusers.exists():
        cur = vdf_parse(paths.loginusers.read_text("utf-8", errors="replace")).get("users", {})
        merged.update(cur)
    if merged:
        snap.write_text(vdf_dump({"users": merged}), "utf-8")
    sdir = store / "ssfn"
    sdir.mkdir(exist_ok=True)
    for f in ssfn_files(paths):
        try:
            shutil.copy2(f, sdir / f.name)
        except OSError:
            pass


def restore_remembered(paths: SteamPaths, account: str, store: Path) -> bool:
    """Re-add `account` to loginusers.vdf from the last snapshot (and restore ssfn
    files), so Steam treats it as remembered again. Returns True if restored."""
    store = Path(store)
    snap = store / "loginusers.vdf"
    if not snap.exists():
        return False
    saved = vdf_parse(snap.read_text("utf-8", errors="replace")).get("users", {})
    sid, block = None, None
    for s, u in saved.items():
        if isinstance(u, dict) and u.get("AccountName", "").lower() == account.lower():
            sid, block = s, dict(u)
            break
    if not block:
        return False
    for f in (store / "ssfn").glob("ssfn*") if (store / "ssfn").exists() else []:
        dst = paths.root / f.name
        if not dst.exists():
            try:
                shutil.copy2(f, dst)
            except OSError:
                pass
    data = (vdf_parse(paths.loginusers.read_text("utf-8", errors="replace"))
            if paths.loginusers.exists() else {})
    users = data.setdefault("users", {})
    block["RememberPassword"] = "1"
    block["AllowAutoLogin"] = "1"
    users[sid] = block
    _backup(paths.loginusers)
    paths.loginusers.parent.mkdir(parents=True, exist_ok=True)
    paths.loginusers.write_text(vdf_dump(data), "utf-8")
    return True


def _resolve(accounts: list[SteamAccount], ref: str) -> SteamAccount:
    ref_l = ref.lower()
    for a in accounts:
        if a.account.lower() == ref_l or a.steamid == ref:
            return a
    partial = [a for a in accounts if ref_l in a.account.lower() or ref_l in a.persona.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise SteamError(f"No Steam account matches '{ref}'. Known: "
                         + ", ".join(a.account for a in accounts))
    raise SteamError(f"'{ref}' matches several accounts: " + ", ".join(a.account for a in partial))


# --------------------------------------------------------------------------- #
# Writing: set active account
# --------------------------------------------------------------------------- #
def _backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + ".vaultbak")
    try:
        shutil.copy2(p, bak)
    except OSError:
        pass


def _set_loginusers_most_recent(paths: SteamPaths, account: str) -> bool:
    """Mark `account` as the most-recent user in loginusers.vdf.

    Returns True if Steam already has a stored session for the account (so the
    switch is instant), False if it doesn't (Steam will show its login prompt).
    """
    if not paths.loginusers.exists():
        return False
    text = paths.loginusers.read_text("utf-8", errors="replace")
    data = vdf_parse(text)
    users = data.get("users", {})
    matched = False
    for info in users.values():
        if not isinstance(info, dict):
            continue
        is_target = info.get("AccountName", "").lower() == account.lower()
        info["MostRecent"] = "1" if is_target else "0"
        if is_target:
            # Everything Steam needs to sign in silently on launch - no click, no
            # offline-mode prompt - for an account it already remembers.
            info["RememberPassword"] = "1"
            info["AllowAutoLogin"] = "1"
            info["WantsOfflineMode"] = "0"
            info["SkipOfflineModeWarning"] = "1"
            matched = True
    if matched:
        _backup(paths.loginusers)
        paths.loginusers.write_text(vdf_dump(data), "utf-8")
    return matched


def _set_autologin(paths: SteamPaths, account: str) -> None:
    if sys.platform.startswith("win"):
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        try:
            winreg.SetValueEx(key, "AutoLoginUser", 0, winreg.REG_SZ, account)
            winreg.SetValueEx(key, "RememberPassword", 0, winreg.REG_DWORD, 1)
        finally:
            winreg.CloseKey(key)
        return
    reg = paths.registry_vdf
    if reg and reg.exists():
        data = vdf_parse(reg.read_text("utf-8", errors="replace"))
        node = data
        for part in ("Registry", "HKCU", "Software", "Valve", "Steam"):
            nxt = None
            for k, v in list(node.items()):
                if k.lower() == part.lower() and isinstance(v, dict):
                    node, nxt = v, v
                    break
            if nxt is None:
                nxt = {}
                node[part] = nxt
                node = nxt
        node["AutoLoginUser"] = account
        node["RememberPassword"] = "1"
        _backup(reg)
        reg.write_text(vdf_dump(data), "utf-8")


def is_steam_running() -> bool:
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                                 capture_output=True, text=True, timeout=10)
            return "steam.exe" in out.stdout.lower()
        out = subprocess.run(["pgrep", "-x", "steam"], capture_output=True, timeout=10)
        return out.returncode == 0
    except Exception:
        return False


def shutdown_steam(paths: SteamPaths, wait: float = 12.0) -> None:
    if not is_steam_running():
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(paths.exe + ["-shutdown"], timeout=10)
        else:
            subprocess.run(paths.exe + ["-shutdown"], timeout=10)
    except Exception:
        pass
    deadline = time.time() + wait
    while time.time() < deadline:
        if not is_steam_running():
            return
        time.sleep(0.5)
    # graceful shutdown timed out; force it
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/IM", "steam.exe", "/F"], timeout=10)
        else:
            subprocess.run(["pkill", "-x", "steam"], timeout=10)
    except Exception:
        pass
    time.sleep(1.0)


def launch_steam(paths: SteamPaths) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(paths.exe[0])  # type: ignore[attr-defined]
        else:
            subprocess.Popen(paths.exe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception as e:
        raise SteamError(f"Switched the active account, but couldn't launch Steam: {e}. "
                         "Start Steam yourself.") from None


@dataclass
class SwitchResult:
    account: str
    had_session: bool          # True = instant sign-in; False = Steam will prompt
    steamid: str = ""


def switch(ref: str, relaunch: bool = True, paths: SteamPaths | None = None,
           session_store: Path | str | None = None) -> SwitchResult:
    """Make `ref` the active Steam account and (optionally) relaunch Steam.

    If Steam no longer holds a session for the account but we have a saved
    snapshot (session_store), restore it first so the sign-in stays password-less.
    had_session is True when Steam will sign in without prompting.
    """
    paths = paths or _paths()
    try:
        acc = _resolve(list_accounts(paths), ref)
        name, steamid = acc.account, acc.steamid
    except SteamError:
        acc, name, steamid = None, ref, ""     # saved account Steam no longer holds
    shutdown_steam(paths)
    had_session = _set_loginusers_most_recent(paths, name)
    if not had_session and session_store is not None:
        if restore_remembered(paths, name, session_store):
            had_session = _set_loginusers_most_recent(paths, name)
    _set_autologin(paths, name)
    if relaunch:
        launch_steam(paths)
    return SwitchResult(account=name, had_session=had_session, steamid=steamid)


# --------------------------------------------------------------------------- #
# One-click helpers: desktop shortcuts and a button GUI
# --------------------------------------------------------------------------- #
def make_shortcuts(out_dir: Path, python: str | None = None, paths: SteamPaths | None = None) -> list[Path]:
    """Write one launcher per account so each switch is a literal single click."""
    paths = paths or _paths()
    accounts = list_accounts(paths)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    py = python or sys.executable
    script = str(Path(__file__).resolve())
    made = []
    for a in accounts:
        label = a.persona or a.account
        safe = "".join(c for c in label if c.isalnum() or c in " -_").strip() or a.account
        if sys.platform.startswith("win"):
            f = out_dir / f"Steam - {safe}.bat"
            f.write_text(f'@echo off\r\n"{py}" "{script}" switch "{a.account}"\r\n', "utf-8")
        elif sys.platform == "darwin":
            f = out_dir / f"Steam - {safe}.command"
            f.write_text(f'#!/bin/sh\n"{py}" "{script}" switch "{a.account}"\n', "utf-8")
            os.chmod(f, 0o755)
        else:
            f = out_dir / f"steam-{safe}.desktop"
            f.write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name=Steam: {label}\n"
                f'Exec={py} "{script}" switch "{a.account}"\n'
                "Icon=steam\nTerminal=false\nCategories=Game;\n", "utf-8")
            os.chmod(f, 0o755)
        made.append(f)
    return made


def run_gui(paths: SteamPaths | None = None, nicknames: dict[str, str] | None = None) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        raise SteamError("Tkinter isn't available, so the GUI can't open. "
                         "Use 'vault.py steam switch <account>' or generate shortcuts instead.")
    paths = paths or _paths()
    nicknames = nicknames or {}

    def refresh(frame, status):
        for w in frame.winfo_children():
            w.destroy()
        accounts = list_accounts(paths)
        for a in accounts:
            label = nicknames.get(a.account) or a.persona or a.account
            mark = "  ● active" if a.most_recent else ""
            btn = tk.Button(frame, text=f"{label}{mark}", width=34, height=2,
                            anchor="w", justify="left",
                            command=lambda acc=a: do_switch(acc, status, frame))
            btn.pack(fill="x", padx=12, pady=4)
            tk.Label(frame, text=f"    {a.account}", fg="#777").pack(anchor="w", padx=12)

    def do_switch(acc, status, frame):
        status.config(text=f"Switching to {acc.persona or acc.account}…")
        status.update()
        try:
            switch(acc.account, paths=paths)
            status.config(text=f"Now signed in as {acc.persona or acc.account}. Steam is starting.")
        except SteamError as e:
            messagebox.showerror("Switch failed", str(e))
            status.config(text="Switch failed.")
        refresh(frame, status)

    win = tk.Tk()
    win.title("Steam Account Switcher")
    win.configure(bg="#1b2838")
    tk.Label(win, text="Click an account to switch", bg="#1b2838", fg="white",
             font=("Segoe UI", 13, "bold")).pack(pady=(14, 6))
    frame = tk.Frame(win, bg="#1b2838")
    frame.pack(fill="both", expand=True)
    status = tk.Label(win, text="", bg="#1b2838", fg="#8fbc8f")
    status.pack(pady=8)
    refresh(frame, status)
    win.minsize(340, 260)
    win.mainloop()


# --------------------------------------------------------------------------- #
# Standalone entry (also used by the generated shortcuts)
# --------------------------------------------------------------------------- #
def _main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="steam", description="One-click Steam account switcher.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list Steam accounts set up on this machine")
    sw = sub.add_parser("switch", help="switch to an account and relaunch Steam")
    sw.add_argument("account")
    sw.add_argument("--no-launch", action="store_true")
    sub.add_parser("gui", help="open the one-click button window")
    sc = sub.add_parser("shortcuts", help="write a desktop launcher per account")
    sc.add_argument("out_dir")
    a = p.parse_args(argv)
    try:
        if a.cmd == "list":
            for acc in list_accounts():
                flag = " (active)" if acc.most_recent else ""
                print(f"{acc.account:<24} {acc.persona:<20} {acc.steamid}{flag}")
        elif a.cmd == "switch":
            res = switch(a.account, relaunch=not a.no_launch)
            msg = f"Switched to {res.account}."
            if not res.had_session:
                msg += " Steam has no saved session for it, so it will prompt for the password."
            print(msg + ("" if a.no_launch else " Steam is starting."))
        elif a.cmd == "gui":
            run_gui()
        elif a.cmd == "shortcuts":
            made = make_shortcuts(Path(a.out_dir))
            print(f"Wrote {len(made)} launcher(s) to {a.out_dir}:")
            for f in made:
                print(f"  {f.name}")
        return 0
    except SteamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
