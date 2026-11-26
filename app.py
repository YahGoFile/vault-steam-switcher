#!/usr/bin/env python3
"""
app.py - Vault: a polished local desktop app combining an encrypted password
vault with a one-click Steam account switcher.

Visual design lives in ui.py; this file is the behaviour. Everything is local —
the window never makes a network request. Launch via Vault.bat.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import steam as S
import ui


class VaultApp(ctk.CTk):
    def __init__(self, vault_path: Path | None = None):
        super().__init__()
        self.vault_path = Path(vault_path or V.DEFAULT_PATH)
        self.vault: V.Vault | None = None
        self.steam_paths = None
        self._imgs: list = []          # keep CTkImage refs alive
        self._active = "steam"

        self.title("Vault")
        self.geometry("1080x708")
        self.minsize(940, 620)
        self.configure(fg_color=ui.BG0)
        ctk.set_appearance_mode("dark")

        self.toast = _Toast(self)
        self._show_gate()

    # ------------------------------------------------------------------ util
    def _clear(self):
        for w in self.winfo_children():
            if w is not self.toast:
                w.destroy()

    def keep(self, img):
        self._imgs.append(img)
        return img

    def _focus(self, widget):
        if widget.winfo_exists():
            widget.focus_set()

    # ----------------------------------------------------------------- gate
    def _show_gate(self):
        self._clear()
        creating = not self.vault_path.exists()
        wrap = ctk.CTkFrame(self, fg_color=ui.BG0)
        wrap.pack(fill="both", expand=True)

        card = ctk.CTkFrame(wrap, fg_color=ui.BG1, corner_radius=20,
                            border_width=1, border_color=ui.LINE, width=420, height=440)
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.pack_propagate(False)

        ctk.CTkLabel(card, text="", image=self.keep(ui.brand_mark(46))).pack(pady=(44, 10))
        ctk.CTkLabel(card, text="VAULT", font=ui.font(26, "bold"), text_color=ui.TEXT).pack()
        ctk.CTkLabel(card, text=("Create a master password to begin" if creating
                                 else "Enter your master password"),
                     font=ui.font(13), text_color=ui.MUTED).pack(pady=(4, 24))

        pw = self._gate_entry(card, "Master password")
        pw2 = self._gate_entry(card, "Confirm password") if creating else None
        err = ctk.CTkLabel(card, text="", font=ui.font(12), text_color=ui.WARN)
        err.pack(pady=(10, 0))

        def submit(_=None):
            p = pw.get()
            if creating:
                if len(p) < 8:
                    return err.configure(text="Use at least 8 characters.")
                if p != pw2.get():
                    return err.configure(text="Passwords don't match.")
                try:
                    self.vault = V.Vault.create(self.vault_path, p)
                except V.VaultError as e:
                    return err.configure(text=str(e))
            else:
                try:
                    self.vault = V.Vault.open(self.vault_path, p)
                except V.VaultError:
                    pw.delete(0, "end")
                    return err.configure(text="Wrong password. Try again.")
            self._show_main()

        ui.primary(card, "Create vault" if creating else "Unlock", submit,
                   width=340, icon_name="unlock" if not creating else None).pack(pady=22)
        for e in (pw, pw2):
            if e:
                e.bind("<Return>", submit)
        self.after(120, lambda: self._focus(pw))

    def _gate_entry(self, parent, placeholder):
        e = ctk.CTkEntry(parent, show="•", width=340, height=46, font=ui.font(14),
                         fg_color=ui.BG2, border_color=ui.LINE, border_width=1,
                         corner_radius=11, placeholder_text=placeholder)
        e.pack(pady=6)
        return e

    # ----------------------------------------------------------------- shell
    def _show_main(self):
        self._clear()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, fg_color=ui.BG1, corner_radius=0, width=248)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=24, pady=(26, 30))
        ctk.CTkLabel(brand, text="", image=self.keep(ui.brand_mark(26))).pack(side="left")
        ctk.CTkLabel(brand, text="  Vault", font=ui.font(19, "bold"),
                     text_color=ui.TEXT).pack(side="left")

        ctk.CTkLabel(side, text="MENU", font=ui.font(11, "bold"), text_color=ui.FAINT,
                     anchor="w").pack(fill="x", padx=28, pady=(0, 6))
        self._nav = {}
        for key, label, ic in (("steam", "Steam", "gamepad"), ("pw", "Passwords", "key")):
            self._nav[key] = _NavItem(side, label, ic, lambda k=key: self._select(k))
            self._nav[key].pack(fill="x", padx=14, pady=3)

        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=14, pady=16)
        ctk.CTkFrame(foot, fg_color=ui.LINE, height=1).pack(fill="x", padx=10, pady=(0, 12))
        _NavItem(foot, "Lock vault", "lock", self._lock, muted=True).pack(fill="x")

        self.content = ctk.CTkFrame(self, fg_color=ui.BG0, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self._select(self._active)

    def _select(self, key):
        self._active = key
        for k, item in self._nav.items():
            item.set_active(k == key)
        for w in self.content.winfo_children():
            w.destroy()
        self._imgs.clear()
        (self._view_steam if key == "steam" else self._view_passwords)()

    def _lock(self):
        self.vault = None
        self._active = "steam"
        self._show_gate()

    def _header(self, title, subtitle, action=None):
        head = ctk.CTkFrame(self.content, fg_color="transparent")
        head.pack(fill="x", padx=34, pady=(30, 0))
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.pack(side="left")
        ctk.CTkLabel(txt, text=title, font=ui.font(25, "bold"), text_color=ui.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=subtitle, font=ui.font(13), text_color=ui.MUTED).pack(anchor="w",
                                                                                     pady=(2, 0))
        if action:
            action().pack(in_=head, side="right")
        ctk.CTkFrame(self.content, fg_color=ui.LINE, height=1).pack(fill="x", padx=34, pady=(18, 0))

    def _empty(self, ic, title, body):
        box = ctk.CTkFrame(self.content, fg_color="transparent")
        box.pack(expand=True)
        ctk.CTkLabel(box, text="", image=self.keep(ui.icon(ic, 46, ui.FAINT, stroke=0.06))).pack()
        ctk.CTkLabel(box, text=title, font=ui.font(18, "bold"), text_color=ui.TEXT).pack(pady=(14, 4))
        ctk.CTkLabel(box, text=body, font=ui.font(13), text_color=ui.MUTED,
                     justify="center").pack()

    # ----------------------------------------------------------------- STEAM
    def _view_steam(self):
        try:
            self.steam_paths = S._paths()
            live = S.list_accounts(self.steam_paths)
            err = None
        except S.SteamError as e:
            self.steam_paths, live, err = None, [], str(e)

        if self.steam_paths:
            self._sync_roster(live)                       # remember every account seen
            S.snapshot_sessions(self.steam_paths, self._session_store())  # keep logins warm

        live_by = {a.account.lower(): a for a in live}
        roster = [e for e in self.vault.entries if e.kind == "steam" and e.username]
        items = [(e, live_by.get(e.username.lower())) for e in roster]

        def rank(it):
            e, a = it
            return 0 if (a and a.most_recent) else (1 if a else 2)
        items.sort(key=lambda it: (rank(it), -float(it[0].extra.get("last_seen_ts", 0) or 0)))

        active = next((self._steam_label(e, a) for e, a in items if a and a.most_recent), None)
        n = len(items)
        sub = (f"Signed in as {active} · {n} account{'s' if n != 1 else ''} saved"
               if active else f"{n} account{'s' if n != 1 else ''} saved · click one to switch")
        self._header("Steam", sub if items else "One click signs you in and restarts Steam")

        if not items:
            if err:
                return self._empty("gamepad", "Steam not found", err.split(".")[0]
                                   + ".\nSet STEAM_ROOT to your Steam folder, or sign in once.")
            return self._empty("gamepad", "No accounts yet",
                               "Sign in to Steam once with “Remember me” ticked.\n"
                               "Every account you use is then saved here for one-click switching.")

        body = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(10, 20))
        body._scrollbar.configure(width=8)
        for e, a in items:
            self._steam_card(body, e, a)

    # ---- roster persistence ----
    def _sync_roster(self, live):
        """Upsert every live Steam account into the vault so it's never lost."""
        by_sid = {e.extra.get("steamid"): e for e in self.vault.entries
                  if e.kind == "steam" and e.extra.get("steamid")}
        by_acc = {e.username.lower(): e for e in self.vault.entries
                  if e.kind == "steam" and e.username}
        now = str(time.time())
        changed = False
        for a in live:
            e = by_sid.get(a.steamid) or by_acc.get(a.account.lower())
            if e is None:
                e = V.Entry(title=f"Steam: {a.persona or a.account}", kind="steam",
                            username=a.account, tags=["steam"])
                self.vault.entries.append(e)
                changed = True
            if a.steamid and e.extra.get("steamid") != a.steamid:
                e.extra["steamid"] = a.steamid
                changed = True
            if a.persona and e.extra.get("persona") != a.persona:
                e.extra["persona"] = a.persona
                changed = True
            e.extra["last_seen_ts"] = now
            if a.steamid:
                self._persist_avatar(a.steamid)
        if changed:
            self.vault.save()

    def _avatar_dir(self) -> Path:
        d = self.vault_path.parent / "avatars"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _session_store(self) -> Path:
        return self.vault_path.parent / "steam_sessions"

    def _has_session_snapshot(self, account) -> bool:
        snap = self._session_store() / "loginusers.vdf"
        if not snap.exists():
            return False
        try:
            users = S.vdf_parse(snap.read_text("utf-8", errors="replace")).get("users", {})
        except Exception:
            return False
        return any(isinstance(u, dict) and u.get("AccountName", "").lower() == account.lower()
                   for u in users.values())

    def _persist_avatar(self, steamid):
        """Copy Steam's cached avatar into our folder so it survives Steam forgetting it."""
        src = S.avatar_path(self.steam_paths, steamid) if self.steam_paths else None
        if not src:
            return
        dst = self._avatar_dir() / f"{steamid}.png"
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copy2(src, dst)
        except OSError:
            pass

    def _steam_avatar(self, steamid):
        live = S.avatar_path(self.steam_paths, steamid) if (self.steam_paths and steamid) else None
        if live:
            return live
        saved = self.vault_path.parent / "avatars" / f"{steamid}.png" if steamid else None
        return saved if (saved and saved.exists()) else None

    def _steam_label(self, e, a):
        return (e.extra.get("nickname") or e.extra.get("persona")
                or (a.persona if a else "") or e.username)

    def _steam_card(self, parent, e, a):
        steamid = e.extra.get("steamid", "")
        label = self._steam_label(e, a)
        active = bool(a and a.most_recent)
        saved = a is None                      # Steam no longer holds a session

        card = ctk.CTkFrame(parent, fg_color=ui.BG2, corner_radius=16,
                            border_width=1, border_color=ui.LINE, height=92)
        card.pack(fill="x", pady=7)
        card.pack_propagate(False)
        if active:
            ctk.CTkFrame(card, fg_color=ui.TEAL, width=4, corner_radius=2).place(
                relx=0, rely=0.5, anchor="w", relheight=0.56, x=2)

        ring = ui.TEAL if active else (ui.LINE if saved else ui.LINE2)
        av = self.keep(ui.avatar(label, 54, self._steam_avatar(steamid), ring=ring))
        ctk.CTkLabel(card, text="", image=av).pack(side="left", padx=(20, 16))

        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(side="left", fill="y", expand=True, anchor="w")
        row = ctk.CTkFrame(mid, fg_color="transparent")
        row.pack(anchor="w", pady=(20, 0))
        ctk.CTkLabel(row, text=label, font=ui.font(17, "bold"),
                     text_color=ui.TEXT if not saved else ui.MUTED).pack(side="left")
        if active:
            self._pill(row, "ACTIVE", ui.GOOD, "#0f3328")
        elif saved:
            self._pill(row, "SAVED", ui.FAINT, ui.BG4)
        sub = e.username
        if saved:
            if self._has_session_snapshot(e.username):
                sub += "  ·  signs in from backup"
            elif e.password:
                sub += "  ·  password saved — one-paste login"
            else:
                sub += "  ·  signed out — one login needed"
        ctk.CTkLabel(mid, text=sub, font=ui.mono(12), text_color=ui.MUTED).pack(anchor="w")

        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=18)
        if active:
            ctk.CTkLabel(right, text="Signed in", font=ui.font(12), text_color=ui.FAINT).pack(
                side="right")
        else:
            ui.primary(right, "Switch", lambda: self._do_switch(e, a),
                       width=118, icon_name="chevron").pack(side="right")
            ui.icon_button(right, "trash", lambda: self._remove_steam(e), size=36).pack(
                side="right", padx=(0, 8))

        def hov(c):
            if not active:
                card.configure(fg_color=c)
        for w in (card, mid, row):
            w.bind("<Enter>", lambda _: hov(ui.BG3))
            w.bind("<Leave>", lambda _: hov(ui.BG2))

    def _pill(self, parent, text, color, bg):
        pill = ctk.CTkFrame(parent, fg_color=bg, corner_radius=7)
        pill.pack(side="left", padx=10)
        ctk.CTkLabel(pill, text="", image=self.keep(ui.icon("dot", 10, color))).pack(
            side="left", padx=(8, 3), pady=3)
        ctk.CTkLabel(pill, text=text, font=ui.font(10, "bold"), text_color=color).pack(
            side="left", padx=(0, 9))

    def _remove_steam(self, e):
        self.vault.entries.remove(e)
        self.vault.save()
        self.toast.show(f"Removed {self._steam_label(e, None)} from the list", ui.WARN)
        self._select("steam")

    def _do_switch(self, e, a):
        overlay = ctk.CTkFrame(self.content, fg_color=ui.BG0, corner_radius=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = ctk.CTkFrame(overlay, fg_color=ui.BG1, corner_radius=18,
                           border_width=1, border_color=ui.LINE)
        box.place(relx=0.5, rely=0.5, anchor="center")
        label = self._steam_label(e, a)
        av = self.keep(ui.avatar(label, 76, self._steam_avatar(e.extra.get("steamid", "")),
                                 ring=ui.ACCENT))
        ctk.CTkLabel(box, text="", image=av).pack(padx=66, pady=(38, 14))
        ctk.CTkLabel(box, text=f"Switching to {label}", font=ui.font(17, "bold"),
                     text_color=ui.TEXT).pack()
        status = ctk.CTkLabel(box, text="Closing Steam…", font=ui.font(13), text_color=ui.MUTED)
        status.pack(pady=(5, 38))

        def done(res, msg):
            overlay.destroy()
            if msg:
                return self.toast.show(msg, ui.WARN, ms=6000)
            if res.had_session:
                self.toast.show(f"Signed in as {label} — Steam is starting", ui.GOOD)
            elif e.password:
                self._set_clip(e.password)
                self.toast.show(f"First sign-in for {e.username} — password copied, just paste it "
                                "(Steam remembers it after that)", ui.ACCENT_HI, ms=7000)
            else:
                self.toast.show(f"Steam needs one login for {e.username} — then it's saved for good",
                                ui.ACCENT_HI, ms=7000)
            self._select("steam")

        def work():
            try:
                res = S.switch(e.username, paths=self.steam_paths,
                               session_store=self._session_store())
                self.after(0, lambda: done(res, None))
            except S.SteamError as ex:
                self.after(0, lambda m=str(ex): done(None, m))

        self.after(60, lambda: status.configure(text="Setting active account & relaunching…"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- PASSWORDS
    def _view_passwords(self):
        n = len(self.vault.entries)
        self._header("Passwords", f"{n} item{'s' if n != 1 else ''} · encrypted with AES-256-GCM",
                     action=lambda: ui.primary(self.content, "Add", self._add_dialog,
                                               width=104, icon_name="plus"))
        bar = ctk.CTkFrame(self.content, fg_color="transparent")
        bar.pack(fill="x", padx=34, pady=(16, 6))
        wrap = ctk.CTkFrame(bar, fg_color=ui.BG2, corner_radius=11, border_width=1,
                            border_color=ui.LINE)
        wrap.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(wrap, text="", image=self.keep(ui.icon("search", 17, ui.FAINT))).pack(
            side="left", padx=(14, 0))
        self.search = ctk.CTkEntry(wrap, height=42, font=ui.font(13), fg_color="transparent",
                                   border_width=0, placeholder_text="Search titles, usernames, emails, tags…")
        self.search.pack(side="left", fill="x", expand=True, padx=6)
        self.search.bind("<KeyRelease>", lambda _: self._render_entries())
        ui.ghost(bar, "Import", self._import_dialog, width=104, icon_name="download").pack(
            side="left", padx=(10, 0))

        self.entry_body = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self.entry_body.pack(fill="both", expand=True, padx=26, pady=(8, 20))
        self.entry_body._scrollbar.configure(width=8)
        self._render_entries()

    def _render_entries(self):
        for w in self.entry_body.winfo_children():
            w.destroy()
        term = self.search.get().strip() if hasattr(self, "search") else ""
        items = sorted(self.vault.search(term) if term else self.vault.entries,
                       key=lambda e: e.title.lower())
        if not items:
            ctk.CTkLabel(self.entry_body, text="No matches" if term else "Nothing saved yet",
                         font=ui.font(14), text_color=ui.MUTED).pack(pady=50)
            return
        for e in items:
            self._entry_row(e)

    def _entry_row(self, e):
        card = ctk.CTkFrame(self.entry_body, fg_color=ui.BG2, corner_radius=13,
                            border_width=1, border_color=ui.LINE, height=74)
        card.pack(fill="x", pady=5)
        card.pack_propagate(False)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=0)

        is_steam = e.kind == "steam"
        av = self.keep(ui.avatar(e.title, 42, ring=ui.LINE2))
        ctk.CTkLabel(inner, text="", image=av).pack(side="left", padx=(0, 14), pady=14)

        mid = ctk.CTkFrame(inner, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, anchor="w")
        trow = ctk.CTkFrame(mid, fg_color="transparent")
        trow.pack(anchor="w", pady=(16, 0))
        ctk.CTkLabel(trow, text=e.title, font=ui.font(15, "bold"), text_color=ui.TEXT).pack(side="left")
        if is_steam:
            tag = ctk.CTkFrame(trow, fg_color=ui.BG4, corner_radius=6)
            tag.pack(side="left", padx=8)
            ctk.CTkLabel(tag, text="STEAM", font=ui.font(9, "bold"), text_color=ui.TEAL).pack(
                padx=7, pady=2)
        ctk.CTkLabel(mid, text=e.username or e.email or "—", font=ui.mono(12),
                     text_color=ui.MUTED).pack(anchor="w")

        act = ctk.CTkFrame(inner, fg_color="transparent")
        act.pack(side="right", pady=17)
        if e.username:
            ui.icon_button(act, "user", lambda: self._copy(e.username, "Username")).pack(
                side="left", padx=3)
        if e.password:
            ui.icon_button(act, "copy", lambda: self._copy(e.password, "Password"),
                           color=ui.ACCENT).pack(side="left", padx=3)
        ui.icon_button(act, "chevron", lambda: self._detail_dialog(e)).pack(side="left", padx=3)

    # -------------------------------------------------------------- clipboard
    def _copy(self, value, what):
        try:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update()
            self.toast.show(f"{what} copied — clears in 20s", ui.ACCENT_HI)
            self.after(20000, self._clear_clip(value))
        except Exception:
            self.toast.show("Couldn't access the clipboard", ui.WARN)

    def _set_clip(self, value):
        try:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update()
            self.after(20000, self._clear_clip(value))
        except Exception:
            pass

    def _clear_clip(self, value):
        def _c():
            try:
                if self.clipboard_get() == value:
                    self.clipboard_clear()
                    self.update()
            except Exception:
                pass
        return _c

    # ----------------------------------------------------------------- modals
    def _modal(self, w=470, h=None):
        top = ctk.CTkToplevel(self)
        top.configure(fg_color=ui.BG1)
        top.transient(self)
        top.title("")
        top.resizable(False, False)
        top.after(40, lambda: self._center(top, w, h))
        top.after(80, top.grab_set)
        return top

    def _center(self, top, w, h):
        top.update_idletasks()
        h = h or top.winfo_height()
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 2
        top.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _labeled(self, parent, label, show=None, mono_val=False):
        ctk.CTkLabel(parent, text=label.upper(), font=ui.font(11, "bold"),
                     text_color=ui.FAINT, anchor="w").pack(anchor="w", padx=2, pady=(12, 3))
        e = ctk.CTkEntry(parent, height=40, fg_color=ui.BG2, border_color=ui.LINE,
                         border_width=1, corner_radius=9, show=show or "",
                         font=ui.mono(13) if mono_val else ui.font(13))
        e.pack(fill="x")
        return e

    def _add_dialog(self):
        top = self._modal(h=620)
        box = ctk.CTkFrame(top, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=26, pady=22)
        ctk.CTkLabel(box, text="Add login", font=ui.font(19, "bold"), text_color=ui.TEXT).pack(
            anchor="w")
        title = self._labeled(box, "Title")
        url = self._labeled(box, "URL")
        user = self._labeled(box, "Username")
        ctk.CTkLabel(box, text="PASSWORD", font=ui.font(11, "bold"), text_color=ui.FAINT,
                     anchor="w").pack(anchor="w", padx=2, pady=(12, 3))
        prow = ctk.CTkFrame(box, fg_color="transparent")
        prow.pack(fill="x")
        pw = ctk.CTkEntry(prow, height=40, fg_color=ui.BG2, border_color=ui.LINE, border_width=1,
                          corner_radius=9, font=ui.mono(13))
        pw.pack(side="left", fill="x", expand=True)
        ui.ghost(prow, "Generate", lambda: (pw.delete(0, "end"), pw.insert(0, V.generate_password())),
                 width=104).pack(side="left", padx=(8, 0))

        link = ctk.CTkCheckBox(box, text="  Link as a Steam account — show it in the Steam switcher",
                               font=ui.font(12), text_color=ui.TEXT, fg_color=ui.ACCENT,
                               hover_color=ui.ACCENT_LO, checkbox_width=20, checkbox_height=20,
                               border_color=ui.LINE2, corner_radius=6)
        link.pack(anchor="w", pady=(16, 0))
        hint = ctk.CTkLabel(box, text="Username must be your Steam account (login) name.",
                            font=ui.font(11), text_color=ui.FAINT)
        hint.pack(anchor="w", padx=28)
        hint.pack_forget()
        link.configure(command=lambda: hint.pack(anchor="w", padx=28) if link.get()
                       else hint.pack_forget())

        def save():
            if not title.get().strip():
                return
            is_steam = bool(link.get())
            e = V.Entry(title=title.get().strip(), url=url.get().strip(),
                        username=user.get().strip(), password=pw.get() or V.generate_password(),
                        kind="steam" if is_steam else "login")
            if is_steam:
                e.extra["nickname"] = e.title
                e.tags = sorted(set(e.tags + ["steam"]))
            self.vault.entries.append(e)
            self.vault.save()
            top.destroy()
            self.toast.show(f"Saved “{e.title}”" + (" · added to Steam" if is_steam else ""), ui.GOOD)
            if is_steam:
                self._select("steam")
            else:
                self._render_entries()

        ui.primary(box, "Save", save, width=418).pack(pady=(20, 0))
        self.after(140, lambda: self._focus(title))

    def _detail_dialog(self, e):
        top = self._modal(w=500)
        box = ctk.CTkFrame(top, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=26, pady=24)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="", image=self.keep(ui.avatar(e.title, 44, ring=ui.LINE2))).pack(
            side="left", padx=(0, 12))
        tt = ctk.CTkFrame(head, fg_color="transparent")
        tt.pack(side="left", anchor="w")
        ctk.CTkLabel(tt, text=e.title, font=ui.font(20, "bold"), text_color=ui.TEXT).pack(anchor="w")
        ctk.CTkLabel(tt, text=e.kind, font=ui.font(12), text_color=ui.MUTED).pack(anchor="w")

        secret_rows = {}
        shown = {"on": False}

        def add_row(label, value, secret=False):
            if not value:
                return
            r = ctk.CTkFrame(box, fg_color=ui.BG2, corner_radius=9)
            r.pack(fill="x", pady=4)
            ctk.CTkLabel(r, text=label.upper(), font=ui.font(10, "bold"), text_color=ui.FAINT,
                         width=120, anchor="w").pack(side="left", padx=14, pady=11)
            val = ctk.CTkLabel(r, text="••••••••••" if secret else value, font=ui.mono(13),
                               text_color=ui.TEXT, anchor="w")
            val.pack(side="left", fill="x", expand=True)
            ui.icon_button(r, "copy", lambda: self._copy(value, label), size=34).pack(
                side="right", padx=10)
            if secret:
                secret_rows[val] = value

        add_row("URL", e.url)
        add_row("Username", e.username)
        add_row("Password", e.password, secret=True)
        add_row("Email", e.email)
        add_row("Email password", e.email_password, secret=True)
        add_row("Webmail", e.webmail)
        for k, v in e.extra.items():
            add_row(k, v)

        def toggle():
            shown["on"] = not shown["on"]
            for lbl, v in secret_rows.items():
                lbl.configure(text=v if shown["on"] else "••••••••••")
            reveal.configure(text="Hide secrets" if shown["on"] else "Reveal secrets")

        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.pack(fill="x", pady=(18, 0))
        reveal = ui.ghost(btns, "Reveal secrets", toggle, width=150, color=ui.ACCENT,
                          icon_name="unlock")
        reveal.pack(side="left")
        ui.ghost(btns, "Delete", lambda: self._delete(e, top), width=100, color=ui.WARN,
                 icon_name="trash").pack(side="right")
        if e.kind == "steam":
            ui.ghost(btns, "Unlink Steam", lambda: self._toggle_steam_link(e, top, False),
                     width=130, color=ui.MUTED, icon_name="gamepad").pack(side="right", padx=8)
        elif e.username:
            ui.ghost(btns, "Link to Steam", lambda: self._toggle_steam_link(e, top, True),
                     width=130, color=ui.TEAL, icon_name="gamepad").pack(side="right", padx=8)

    def _toggle_steam_link(self, e, top, link):
        if link:
            e.kind = "steam"
            e.extra.setdefault("nickname", e.title)
            e.tags = sorted(set(e.tags + ["steam"]))
        else:
            e.kind = "login"
            e.tags = [t for t in e.tags if t != "steam"]
        e.updated = V._now()
        self.vault.save()
        top.destroy()
        if link:
            self.toast.show(f"“{e.title}” is now in the Steam switcher", ui.GOOD)
            self._select("steam")
        else:
            self.toast.show(f"“{e.title}” removed from the Steam switcher", ui.MUTED)
            self._select("pw")

    def _delete(self, e, top):
        self.vault.entries.remove(e)
        self.vault.save()
        top.destroy()
        self.toast.show(f"Deleted “{e.title}”", ui.WARN)
        self._render_entries()

    def _import_dialog(self):
        top = self._modal(w=580, h=520)
        box = ctk.CTkFrame(top, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=26, pady=24)
        ctk.CTkLabel(box, text="Import accounts", font=ui.font(19, "bold"),
                     text_color=ui.TEXT).pack(anchor="w")
        ctk.CTkLabel(box, text="Paste lines like:  Login … Password : … Email Account : … Webmail : …",
                     font=ui.font(12), text_color=ui.MUTED).pack(anchor="w", pady=(3, 12))
        tb = ctk.CTkTextbox(box, height=200, font=ui.mono(12), fg_color=ui.BG2,
                            border_color=ui.LINE, border_width=1, corner_radius=10)
        tb.pack(fill="both", expand=True)

        as_steam = ctk.CTkCheckBox(box, text="  These are Steam accounts — add them to the switcher",
                                   font=ui.font(12), text_color=ui.TEXT, fg_color=ui.ACCENT,
                                   hover_color=ui.ACCENT_LO, checkbox_width=20, checkbox_height=20,
                                   border_color=ui.LINE2, corner_radius=6)
        as_steam.pack(anchor="w", pady=(14, 0))

        def do():
            text = tb.get("1.0", "end").strip()
            if not text:
                return
            steam = bool(as_steam.get())
            found = V.parse_accounts(text)
            added = 0
            for e in found:
                if self.vault.is_duplicate(e):
                    continue
                if steam:
                    e.kind = "steam"
                    e.title = e.username or e.title       # the Steam login is the cleanest label
                    e.extra["nickname"] = e.title
                    e.tags = sorted(set(e.tags + ["steam"]))
                self.vault.entries.append(e)
                added += 1
            if added:
                self.vault.save()
            top.destroy()
            dup = len(found) - added
            self.toast.show(f"Imported {added} account(s)"
                            + (f" to Steam" if steam and added else "")
                            + (f", skipped {dup} duplicate(s)" if dup else ""),
                            ui.GOOD if added else ui.WARN)
            self._select("steam" if steam and added else "pw")

        ui.primary(box, "Parse & import", do, width=528).pack(pady=(16, 0))


# --------------------------------------------------------------------------- #
# Sidebar item + toast
# --------------------------------------------------------------------------- #
class _NavItem(ctk.CTkFrame):
    def __init__(self, master, label, icon_name, command, muted=False):
        super().__init__(master, fg_color="transparent", height=46)
        self.pack_propagate(False)
        self.icon_name, self.label, self.muted = icon_name, label, muted
        self.bar = ctk.CTkFrame(self, fg_color="transparent", width=4, corner_radius=2)
        self.bar.pack(side="left", fill="y", padx=(0, 0), pady=9)
        self.btn = ctk.CTkButton(self, text=f"  {label}", image=ui.icon(icon_name, 19, ui.MUTED),
                                 anchor="w", compound="left", height=44, font=ui.font(14, "bold"),
                                 corner_radius=10, fg_color="transparent", text_color=ui.MUTED,
                                 hover_color=ui.BG2, command=command)
        self.btn.pack(side="left", fill="both", expand=True, padx=(8, 0))

    def set_active(self, on):
        self.bar.configure(fg_color=ui.ACCENT if on else "transparent")
        self.btn.configure(fg_color=ui.BG2 if on else "transparent",
                           text_color=ui.ACCENT if on else ui.MUTED,
                           image=ui.icon(self.icon_name, 19, ui.ACCENT if on else ui.MUTED))


class _Toast(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=ui.BG4, corner_radius=11,
                         border_width=1, border_color=ui.LINE2)
        self.dot = ctk.CTkLabel(self, text="", image=ui.icon("dot", 10, ui.ACCENT))
        self.dot.pack(side="left", padx=(14, 6), pady=11)
        self.label = ctk.CTkLabel(self, text="", font=ui.font(12, "bold"), text_color=ui.TEXT)
        self.label.pack(side="left", padx=(0, 16))
        self._after = None

    def show(self, text, color=ui.ACCENT, ms=2800):
        self.dot.configure(image=ui.icon("dot", 10, color))
        self.label.configure(text=text)
        self.place(relx=0.5, rely=0.965, anchor="s")
        self.lift()
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(ms, self.place_forget)


def main():
    app = VaultApp()
    app.mainloop()


if __name__ == "__main__":
    main()
