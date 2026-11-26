#!/usr/bin/env python3
"""
vault.py - a small, local, encrypted personal vault.

Crypto
  * Key derivation : Argon2id (argon2-cffi), random 16-byte salt per save,
                     default t=3, m=64 MiB, p=4 -> 256-bit key.
  * Encryption     : AES-256-GCM (pyca/cryptography), random 96-bit nonce per save.
  * Integrity      : the GCM tag covers the ciphertext AND the file header
                     (format version + KDF params) as associated data, so any
                     tampering - including downgrading the KDF params - is
                     detected and decryption fails.
  * Storage        : one JSON file, written atomically (temp file + rename),
                     permissions 0600, previous version kept as <file>.bak.

Usage
  python vault.py init                     create a new vault
  python vault.py shell                    unlock once, then run commands interactively
  python vault.py import [FILE|-]          parse "Login ... Password : ..." blocks
  python vault.py add | list | show | search | edit | rm | note | gen | passwd
  python vault.py <command> -h             help for a command

The vault file defaults to ~/.vault/vault.json; override with --vault PATH or
the VAULT_FILE environment variable.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import secrets
import shlex
import string
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from argon2.low_level import hash_secret_raw, Type
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover
    sys.exit("Missing dependencies. Run:  pip install cryptography argon2-cffi")

FORMAT_VERSION = 1
DEFAULT_KDF = {"alg": "argon2id", "t": 3, "m": 65536, "p": 4}  # m in KiB (64 MiB)
MIN_KDF = {"t": 2, "m": 19456, "p": 1}  # OWASP floor; refuse anything weaker
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12
DEFAULT_PATH = Path(os.environ.get("VAULT_FILE", Path.home() / ".vault" / "vault.json"))
AUTOLOCK_SECONDS = 300


class VaultError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Crypto
# --------------------------------------------------------------------------- #
def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def derive_key(password: str, salt: bytes, kdf: dict) -> bytes:
    if kdf.get("alg") != "argon2id":
        raise VaultError(f"Unsupported KDF: {kdf.get('alg')}")
    for k, floor in MIN_KDF.items():
        if int(kdf[k]) < floor:
            raise VaultError(f"KDF parameter {k}={kdf[k]} is below the safe minimum {floor}")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=int(kdf["t"]),
        memory_cost=int(kdf["m"]),
        parallelism=int(kdf["p"]),
        hash_len=KEY_LEN,
        type=Type.ID,
    )


def _aad(header: dict) -> bytes:
    """Canonical bytes of everything in the header except the ciphertext/nonce."""
    return json.dumps(
        {"v": header["v"], "kdf": header["kdf"]}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def encrypt_blob(plaintext: bytes, password: str, kdf: dict | None = None) -> dict:
    kdf = dict(kdf or DEFAULT_KDF)
    salt = secrets.token_bytes(SALT_LEN)
    kdf["salt"] = _b64e(salt)
    header = {"v": FORMAT_VERSION, "kdf": kdf}
    key = derive_key(password, salt, kdf)
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, _aad(header))
    header["nonce"] = _b64e(nonce)
    header["ct"] = _b64e(ct)
    return header


def decrypt_blob(blob: dict, password: str) -> bytes:
    try:
        if blob["v"] != FORMAT_VERSION:
            raise VaultError(f"Unsupported vault format version {blob['v']}")
        kdf = blob["kdf"]
        salt = _b64d(kdf["salt"])
        nonce = _b64d(blob["nonce"])
        ct = _b64d(blob["ct"])
    except (KeyError, TypeError, ValueError) as e:
        raise VaultError(f"Vault file is malformed ({e})") from None
    key = derive_key(password, salt, kdf)
    try:
        return AESGCM(key).decrypt(nonce, ct, _aad(blob))
    except InvalidTag:
        raise VaultError("Wrong master password, or the vault file has been tampered with.") from None


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Entry:
    title: str
    kind: str = "login"  # "login" or "note"
    username: str = ""
    password: str = ""
    url: str = ""
    email: str = ""
    email_password: str = ""
    webmail: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    SECRET_FIELDS = ("password", "email_password")

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Vault:
    def __init__(self, path: Path, password: str, entries: list[Entry] | None = None,
                 kdf: dict | None = None):
        self.path = Path(path)
        self._password = password
        self.entries: list[Entry] = entries or []
        self.kdf = {k: v for k, v in (kdf or DEFAULT_KDF).items() if k != "salt"}

    # ---- persistence -------------------------------------------------------
    @classmethod
    def create(cls, path: Path, password: str, kdf: dict | None = None) -> "Vault":
        path = Path(path)
        if path.exists():
            raise VaultError(f"{path} already exists")
        v = cls(path, password, [], kdf)
        v.save()
        return v

    @classmethod
    def open(cls, path: Path, password: str) -> "Vault":
        path = Path(path)
        if not path.exists():
            raise VaultError(f"No vault at {path}. Create one with:  vault.py init")
        try:
            blob = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as e:
            raise VaultError(f"Vault file is not valid JSON ({e})") from None
        data = json.loads(decrypt_blob(blob, password))
        entries = [Entry.from_dict(e) for e in data.get("entries", [])]
        return cls(path, password, entries, blob["kdf"])

    def save(self) -> None:
        payload = json.dumps(
            {"entries": [asdict(e) for e in self.entries]}, ensure_ascii=False
        ).encode("utf-8")
        blob = encrypt_blob(payload, self._password, self.kdf)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".vault-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(blob, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            if self.path.exists():
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def change_password(self, new_password: str) -> None:
        self._password = new_password
        self.save()

    def check_password(self, password: str) -> bool:
        return secrets.compare_digest(password.encode(), self._password.encode())

    # ---- queries -----------------------------------------------------------
    def find(self, ref: str) -> Entry:
        ref_l = ref.lower()
        by_id = [e for e in self.entries if e.id == ref_l]
        if by_id:
            return by_id[0]
        exact = [e for e in self.entries if e.title.lower() == ref_l]
        if len(exact) == 1:
            return exact[0]
        partial = self.search(ref)
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise VaultError(f"No entry matches '{ref}'")
        raise VaultError(
            f"'{ref}' matches {len(partial)} entries - use the id: "
            + ", ".join(f"{e.id} ({e.title})" for e in partial[:10])
        )

    def search(self, term: str) -> list[Entry]:
        t = term.lower()
        out = []
        for e in self.entries:
            hay = " ".join([e.title, e.username, e.url, e.email, e.webmail, e.notes,
                            " ".join(e.tags), " ".join(e.extra.values())]).lower()
            if t in hay:
                out.append(e)
        return out

    def is_duplicate(self, new: Entry) -> bool:
        return any(
            e.username == new.username and e.password == new.password
            and e.email == new.email and e.kind == new.kind
            for e in self.entries
        )


# --------------------------------------------------------------------------- #
# Parser for "Login X Password : Y Email Account : Z Password : W Webmail : U"
# --------------------------------------------------------------------------- #
# label -> canonical field. Longest labels first so "Email Account" wins over "Email".
_LABELS: list[tuple[str, str]] = [
    ("email account password", "email_password"),
    ("e-mail account password", "email_password"),
    ("email password", "email_password"),
    ("e-mail password", "email_password"),
    ("mail password", "email_password"),
    ("email account", "email"),
    ("e-mail account", "email"),
    ("mail account", "email"),
    ("recovery email", "x:Recovery email"),
    ("backup email", "x:Recovery email"),
    ("web mail", "webmail"),
    ("webmail", "webmail"),
    ("mail url", "webmail"),
    ("e-mail", "email"),
    ("email", "email"),
    ("username", "username"),
    ("user name", "username"),
    ("login", "username"),
    ("user", "username"),
    ("password", "password"),
    ("passwd", "password"),
    ("pass", "password"),
    ("pwd", "password"),
    ("website", "url"),
    ("site", "url"),
    ("url", "url"),
    ("link", "url"),
    ("title", "title"),
    ("service", "title"),
    ("phone", "x:Phone"),
    ("2fa", "x:2FA"),
    ("backup codes", "x:Backup codes"),
    ("notes", "notes"),
    ("note", "notes"),
]
_LABEL_MAP = {lbl: fld for lbl, fld in _LABELS}
_LABEL_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[|;,]))("
    + "|".join(re.escape(l).replace(r"\ ", r"\s+") for l, _ in _LABELS)
    + r")\s*(?::|=|-|\s)\s*",
    re.IGNORECASE | re.MULTILINE,
)
_DELIM_RE = re.compile(r"\s*[|;:\t]\s*")


def _strip_value(v: str) -> str:
    return v.strip().strip("|;,").strip()


def parse_accounts(text: str, title: str | None = None, tags: list[str] | None = None) -> list[Entry]:
    """Parse one or many account blocks.

    Understands the labelled format, all on one line or across lines, e.g.
        Login abc Password : xyz Email Account : a@b.c Password : 123 Webmail : http://...
    A "Password" that follows an email label is stored as the email's password.
    A repeated "Login" starts a new account.

    Lines with no labels fall back to delimited formats:
        user:pass     user|pass|email|emailpass     (separator : | ; or tab)
    """
    tags = list(tags or [])
    entries: list[Entry] = []
    labelled_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            labelled_lines.append("")
            continue
        if _LABEL_RE.search(line):
            labelled_lines.append(line)
        else:
            e = _parse_delimited(line)
            if e:
                entries.append(e)
            elif labelled_lines and labelled_lines[-1]:
                # continuation of a multi-line value (e.g. notes)
                labelled_lines[-1] += "\n" + line
    entries = _parse_labelled("\n".join(labelled_lines)) + entries

    for i, e in enumerate(entries, 1):
        if title:
            e.title = title if len(entries) == 1 else f"{title} #{i}"
        elif not e.title:
            e.title = _guess_title(e)
        e.tags = sorted(set(e.tags + tags))
    return entries


def _parse_labelled(text: str) -> list[Entry]:
    matches = list(_LABEL_RE.finditer(text))
    if not matches:
        return []
    records: list[dict] = []
    cur: dict = {}
    context = "account"  # which identity the next bare "Password" belongs to

    def flush():
        nonlocal cur, context
        if any(k in cur for k in ("username", "password", "email", "email_password")):
            records.append(cur)
        cur, context = {}, "account"

    for i, m in enumerate(matches):
        label = re.sub(r"\s+", " ", m.group(1).lower())
        fld = _LABEL_MAP[label]
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        value = _strip_value(text[m.end():end])
        # a blank line between blocks also separates accounts
        if "\n\n" in text[m.start():end] and value:
            value = _strip_value(value.split("\n\n")[0])

        if fld == "username":
            if "username" in cur or (cur and context == "email"):
                flush()
            cur["username"] = value
            context = "account"
        elif fld == "password":
            if context == "email":
                key = "email_password"
            else:
                key = "password"
            if key in cur:  # third password with no new label -> new record
                flush()
                key = "password"
            cur[key] = value
        elif fld == "email":
            if "email" in cur:
                flush()
            cur["email"] = value
            context = "email"
        elif fld == "email_password":
            cur["email_password"] = value
        elif fld == "webmail":
            cur["webmail"] = value
            context = "email"
        elif fld.startswith("x:"):
            cur.setdefault("extra", {})[fld[2:]] = value
        else:  # url, title, notes
            if fld in cur and fld == "title":
                flush()
            cur[fld] = value if fld != "notes" else (cur.get("notes", "") + "\n" + value).strip()
        # paragraph break ends the record
        if "\n\n" in text[m.end():end]:
            flush()
    flush()

    out = []
    for r in records:
        extra = r.pop("extra", {})
        out.append(Entry(title=r.pop("title", ""), extra=extra, **r))
    return out


def _parse_delimited(line: str) -> Entry | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    # protect URLs (scheme://) from being split on ':'
    urls = re.findall(r"[a-z][a-z0-9+.-]*://\S+?(?=\s*[|;\t]|\s*$)", s, re.I)
    tmp = s
    for i, u in enumerate(urls):
        tmp = tmp.replace(u, f"\x00{i}\x00", 1)
    parts = [p for p in _DELIM_RE.split(tmp)]
    parts = [re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], p).strip() for p in parts]
    if len(parts) == 2 and all(parts):
        user, pw = parts
        if "@" in user:
            return Entry(title="", username=user, password=pw, email=user)
        return Entry(title="", username=user, password=pw)
    if len(parts) in (4, 5) and all(parts[:4]):
        e = Entry(title="", username=parts[0], password=parts[1],
                  email=parts[2], email_password=parts[3])
        if len(parts) == 5:
            e.webmail = parts[4]
        return e
    return None


def _guess_title(e: Entry) -> str:
    if e.url:
        host = re.sub(r"^[a-z]+://", "", e.url, flags=re.I).split("/")[0]
        return host or e.url
    if e.username:
        return f"Account {e.username}"
    if e.email:
        return f"Account {e.email}"
    return "Imported account"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def generate_password(length: int = 20, symbols: bool = True) -> str:
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{};:,.?" if symbols else "")
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and (not symbols or any(not c.isalnum() for c in pw))):
            return pw


def _mask(s: str) -> str:
    return "••••••••" if s else ""


def format_entry(e: Entry, reveal: bool = False) -> str:
    rows = [("Title", e.title), ("Id", e.id), ("Type", e.kind)]
    if e.kind == "login":
        rows += [
            ("URL", e.url),
            ("Username", e.username),
            ("Password", e.password if reveal else _mask(e.password)),
            ("Email", e.email),
            ("Email password", e.email_password if reveal else _mask(e.email_password)),
            ("Webmail", e.webmail),
        ]
    rows += [(k, v) for k, v in e.extra.items()]
    rows += [("Tags", ", ".join(e.tags)), ("Notes", e.notes),
             ("Created", e.created), ("Updated", e.updated)]
    w = max(len(k) for k, v in rows if v)
    lines = []
    for k, v in rows:
        if not v:
            continue
        v = str(v).replace("\n", "\n" + " " * (w + 2))
        lines.append(f"{k.ljust(w)}  {v}")
    if not reveal and (e.password or e.email_password):
        lines.append("(use --reveal to show secrets)")
    return "\n".join(lines)


def _copy_to_clipboard(text: str, clear_after: int = 20) -> bool:
    """Best-effort clipboard copy with auto-clear. Returns False if unavailable."""
    try:
        import pyperclip  # optional dependency
    except ImportError:
        return False
    try:
        pyperclip.copy(text)
    except Exception:
        return False
    import threading

    def _clear():
        try:
            if pyperclip.paste() == text:
                pyperclip.copy("")
        except Exception:
            pass

    t = threading.Timer(clear_after, _clear)
    t.daemon = False
    t.start()
    return True


def _prompt_new_master() -> str:
    while True:
        pw = getpass.getpass("New master password: ")
        if len(pw) < 12:
            print("Use at least 12 characters (a few random words works well).")
            continue
        if getpass.getpass("Repeat master password: ") != pw:
            print("Passwords didn't match, try again.")
            continue
        return pw


def _read_multiline(prompt: str) -> str:
    print(prompt + " (finish with an empty line, or Ctrl-D):")
    lines = []
    try:
        while True:
            line = input()
            if not line.strip() and lines:
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands (each takes args + an open Vault)
# --------------------------------------------------------------------------- #
def cmd_list(v: Vault, a) -> None:
    items = v.search(a.term) if getattr(a, "term", None) else v.entries
    if a.tag:
        items = [e for e in items if a.tag in e.tags]
    if not items:
        print("(no entries)")
        return
    for e in sorted(items, key=lambda x: x.title.lower()):
        who = e.username or e.email
        tag = f"  [{', '.join(e.tags)}]" if e.tags else ""
        print(f"{e.id}  {e.kind:<5}  {e.title}" + (f"  — {who}" if who else "") + tag)
    print(f"{len(items)} entr{'y' if len(items) == 1 else 'ies'}")


def cmd_show(v: Vault, a) -> None:
    print(format_entry(v.find(a.ref), reveal=a.reveal))


def cmd_copy(v: Vault, a) -> None:
    e = v.find(a.ref)
    value = getattr(e, a.field, "")
    if not value:
        raise VaultError(f"'{e.title}' has no {a.field}")
    if _copy_to_clipboard(value):
        print(f"Copied {a.field} of '{e.title}' to clipboard (clears in 20s).")
    else:
        raise VaultError("Clipboard unavailable - install it with:  pip install pyperclip")


def cmd_add(v: Vault, a) -> None:
    title = a.title or input("Title: ").strip()
    e = Entry(title=title)
    e.url = a.url if a.url is not None else input("URL (optional): ").strip()
    e.username = a.username if a.username is not None else input("Username: ").strip()
    if a.generate:
        e.password = generate_password(a.length)
        print("Generated a password.")
    else:
        e.password = getpass.getpass("Password (blank = generate): ") or generate_password(a.length)
    e.email = a.email if a.email is not None else input("Email (optional): ").strip()
    if e.email:
        e.email_password = getpass.getpass("Email password (optional): ")
    e.tags = a.tag or []
    v.entries.append(e)
    v.save()
    print(f"Added '{e.title}' ({e.id}).")


def cmd_note(v: Vault, a) -> None:
    title = a.title or input("Title: ").strip()
    body = _read_multiline("Note text")
    e = Entry(title=title, kind="note", notes=body, tags=a.tag or [])
    v.entries.append(e)
    v.save()
    print(f"Saved note '{e.title}' ({e.id}).")


def cmd_import(v: Vault, a) -> None:
    if a.file and a.file != "-":
        text = Path(a.file).read_text("utf-8")
    elif a.file == "-" or not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = _read_multiline("Paste account details")
    found = parse_accounts(text, title=a.title, tags=a.tag)
    if not found:
        raise VaultError("Couldn't find any accounts in that text.")
    added = skipped = 0
    for e in found:
        if v.is_duplicate(e):
            skipped += 1
            continue
        v.entries.append(e)
        added += 1
        email = f"  email: {e.email}" if e.email else ""
        print(f"  + {e.id}  {e.title}  user: {e.username or '-'}{email}"
              f"{'  webmail: ' + e.webmail if e.webmail else ''}")
    if a.dry_run:
        v.entries = v.entries[: len(v.entries) - added]
        print(f"Dry run: would add {added}, skip {skipped} duplicate(s). Nothing saved.")
        return
    if added:
        v.save()
    print(f"Imported {added} account(s), skipped {skipped} duplicate(s).")
    if a.file and a.file != "-":
        print(f"Note: {a.file} still contains these credentials in plain text - delete it.")


EDITABLE = ["title", "url", "username", "password", "email", "email_password",
            "webmail", "notes", "tags"]


def cmd_edit(v: Vault, a) -> None:
    e = v.find(a.ref)
    if a.field:
        fields = [a.field]
    else:
        fields = EDITABLE
        print(f"Editing '{e.title}'. Press Enter to keep the current value.")
    for f in fields:
        if f not in EDITABLE:
            raise VaultError(f"Unknown field '{f}'. Fields: {', '.join(EDITABLE)}")
        cur = getattr(e, f)
        if f in Entry.SECRET_FIELDS:
            new = (a.value if a.value is not None else
                   getpass.getpass(f"{f} [hidden, 'gen' to generate]: "))
            if new == "gen":
                new = generate_password()
                print("  generated a new password")
        elif f == "tags":
            shown = ", ".join(cur)
            new = a.value if a.value is not None else input(f"{f} [{shown}]: ")
            if new:
                new = [t.strip() for t in new.split(",") if t.strip()]
        else:
            new = a.value if a.value is not None else input(f"{f} [{cur}]: ")
        if new:
            setattr(e, f, new)
    e.updated = _now()
    v.save()
    print(f"Updated '{e.title}'.")


def cmd_rm(v: Vault, a) -> None:
    e = v.find(a.ref)
    if not a.yes:
        if input(f"Delete '{e.title}' ({e.id})? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return
    v.entries.remove(e)
    v.save()
    print(f"Deleted '{e.title}'.")


def cmd_passwd(v: Vault, a) -> None:
    v.change_password(_prompt_new_master())
    print("Master password changed; vault re-encrypted with a fresh salt and nonce.")


def cmd_gen(_v, a) -> None:
    print(generate_password(a.length, not a.no_symbols))


def cmd_steam(v: Vault, a) -> None:
    try:
        import steam as steammod
    except ImportError:
        raise VaultError("steam.py is missing (keep it next to vault.py).")

    def nicknames() -> dict:
        return {e.username: (e.extra.get("nickname") or e.title)
                for e in v.entries if e.kind == "steam" and e.username}

    if a.steam_cmd == "list":
        try:
            accounts = steammod.list_accounts()
        except steammod.SteamError as e:
            raise VaultError(str(e))
        nn = nicknames()
        for acc in accounts:
            label = nn.get(acc.account, acc.persona)
            flag = "  ● active" if acc.most_recent else ""
            print(f"{acc.account:<22} {label:<20} {acc.steamid}{flag}")
        print(f"{len(accounts)} Steam account(s) on this machine")
    elif a.steam_cmd == "switch":
        try:
            res = steammod.switch(a.account, relaunch=not a.no_launch)
        except steammod.SteamError as e:
            raise VaultError(str(e))
        msg = f"Switched to {res.account}."
        if not res.had_session:
            msg += " Steam has no saved session for it, so it will prompt for the password."
        print(msg + ("" if a.no_launch else " Steam is starting."))
    elif a.steam_cmd == "gui":
        try:
            steammod.run_gui(nicknames=nicknames())
        except steammod.SteamError as e:
            raise VaultError(str(e))
    elif a.steam_cmd == "shortcuts":
        try:
            made = steammod.make_shortcuts(Path(a.out_dir))
        except steammod.SteamError as e:
            raise VaultError(str(e))
        print(f"Wrote {len(made)} launcher(s) to {a.out_dir}. Each one switches with a double-click.")
    elif a.steam_cmd == "link":
        # store a Steam account's password + nickname in the encrypted vault
        try:
            accounts = {x.account.lower(): x for x in steammod.list_accounts()}
        except steammod.SteamError:
            accounts = {}
        acc = accounts.get(a.account.lower())
        nickname = a.nickname or (acc.persona if acc else "") or a.account
        existing = next((e for e in v.entries if e.kind == "steam"
                         and e.username.lower() == a.account.lower()), None)
        pw = getpass.getpass("Steam password (optional, stored encrypted): ")
        e = existing or Entry(title=f"Steam: {nickname}", kind="steam", username=a.account)
        e.title = f"Steam: {nickname}"
        e.extra["nickname"] = nickname
        if acc:
            e.extra["steamid"] = acc.steamid
        if pw:
            e.password = pw
        e.url = "https://store.steampowered.com/login/"
        if "steam" not in e.tags:
            e.tags.append("steam")
        if not existing:
            v.entries.append(e)
        v.save()
        print(f"Linked Steam account '{a.account}' as '{nickname}' ({e.id}).")


def cmd_export(v: Vault, a) -> None:
    """Plain-text export - for migrating to another password manager."""
    if input("This writes ALL secrets unencrypted. Type EXPORT to continue: ") != "EXPORT":
        print("Cancelled.")
        return
    out = Path(a.out)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in v.entries], f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(v.entries)} entries to {out}. Delete it when you're done.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vault", description="Encrypted personal vault "
                                "(Argon2id + AES-256-GCM).")
    p.add_argument("--vault", type=Path, default=DEFAULT_PATH, help=f"vault file (default {DEFAULT_PATH})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create a new vault")
    sub.add_parser("shell", help="unlock once and run commands interactively")

    s = sub.add_parser("list", aliases=["ls"], help="list entries")
    s.add_argument("term", nargs="?")
    s.add_argument("--tag")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("search", help="search titles, usernames, emails, urls, notes, tags")
    s.add_argument("term")
    s.add_argument("--tag")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="show one entry")
    s.add_argument("ref", help="id, title or search term")
    s.add_argument("-r", "--reveal", action="store_true", help="show passwords")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("copy", aliases=["cp"], help="copy a field to the clipboard (needs pyperclip)")
    s.add_argument("ref")
    s.add_argument("field", nargs="?", default="password",
                   choices=["password", "username", "email", "email_password", "url", "webmail"])
    s.set_defaults(fn=cmd_copy)

    s = sub.add_parser("add", help="add a login")
    s.add_argument("title", nargs="?")
    s.add_argument("-u", "--username")
    s.add_argument("--url")
    s.add_argument("--email")
    s.add_argument("-g", "--generate", action="store_true", help="generate the password")
    s.add_argument("-l", "--length", type=int, default=20)
    s.add_argument("-t", "--tag", action="append")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("note", help="add a secure note")
    s.add_argument("title", nargs="?")
    s.add_argument("-t", "--tag", action="append")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("import", help="import 'Login … Password : … Email Account : …' text")
    s.add_argument("file", nargs="?", help="text file, or - for stdin; omit to paste")
    s.add_argument("--title", help="title for the imported account(s), e.g. the site name")
    s.add_argument("-t", "--tag", action="append")
    s.add_argument("-n", "--dry-run", action="store_true", help="parse and preview only")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("edit", help="edit an entry (interactively, or one field)")
    s.add_argument("ref")
    s.add_argument("field", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("rm", aliases=["delete"], help="delete an entry")
    s.add_argument("ref")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_rm)

    s = sub.add_parser("passwd", help="change the master password")
    s.set_defaults(fn=cmd_passwd)

    s = sub.add_parser("gen", help="generate a random password")
    s.add_argument("-l", "--length", type=int, default=20)
    s.add_argument("--no-symbols", action="store_true")
    s.set_defaults(fn=cmd_gen, no_vault=True)

    s = sub.add_parser("steam", help="manage and one-click-switch Steam accounts")
    ssub = s.add_subparsers(dest="steam_cmd", required=True)
    ssub.add_parser("list", help="list Steam accounts on this machine")
    sw = ssub.add_parser("switch", help="switch active account and relaunch Steam")
    sw.add_argument("account", help="Steam account name, SteamID, or nickname")
    sw.add_argument("--no-launch", action="store_true", help="don't relaunch Steam")
    ssub.add_parser("gui", help="open a window with one button per account")
    sc = ssub.add_parser("shortcuts", help="write a desktop launcher per account")
    sc.add_argument("out_dir")
    lk = ssub.add_parser("link", help="store a Steam account's nickname/password in the vault")
    lk.add_argument("account")
    lk.add_argument("--nickname")
    s.set_defaults(fn=cmd_steam)

    s = sub.add_parser("export", help="export everything as plain JSON (for migration)")
    s.add_argument("out")
    s.set_defaults(fn=cmd_export)
    return p


def _unlock(path: Path) -> Vault:
    for attempt in range(3):
        pw = getpass.getpass(f"Master password for {path}: ")
        try:
            return Vault.open(path, pw)
        except VaultError as e:
            if "Wrong master password" not in str(e) or attempt == 2:
                raise
            print("Wrong password.")
            time.sleep(1)
    raise VaultError("unreachable")


def run_shell(path: Path, parser: argparse.ArgumentParser) -> None:
    v = _unlock(path)
    print(f"Unlocked {len(v.entries)} entries. Type 'help' for commands, 'lock' or 'exit' to quit.")
    print(f"Auto-locks after {AUTOLOCK_SECONDS // 60} minutes idle.")
    last = time.monotonic()
    sub_names = [n for n in parser._subparsers._group_actions[0].choices if n not in ("init", "shell")]
    while True:
        try:
            line = input("vault> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if time.monotonic() - last > AUTOLOCK_SECONDS:
            print("Locked after inactivity.")
            pw = getpass.getpass("Master password: ")
            if not v.check_password(pw):
                print("Wrong password - exiting.")
                break
        last = time.monotonic()
        if not line:
            continue
        if line in ("exit", "quit", "lock", "q"):
            break
        if line in ("help", "?"):
            print("Commands: " + ", ".join(sorted(sub_names)) + ", exit")
            print("Try '<command> -h' for details, e.g. 'import -h'.")
            continue
        try:
            a = parser.parse_args(["--vault", str(path)] + shlex.split(line))
        except SystemExit:
            continue
        except ValueError as e:
            print(f"error: {e}")
            continue
        if a.cmd in ("init", "shell"):
            print("Not available inside the shell.")
            continue
        try:
            a.fn(v, a)
        except VaultError as e:
            print(f"error: {e}")
        except KeyboardInterrupt:
            print("\n(cancelled)")
    v = None  # drop references to decrypted data
    print("Vault locked.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        if a.cmd == "init":
            if a.vault.exists():
                raise VaultError(f"{a.vault} already exists")
            print("Choose a master password. It cannot be recovered if you forget it.")
            Vault.create(a.vault, _prompt_new_master())
            print(f"Created encrypted vault at {a.vault}")
            return 0
        if a.cmd == "shell":
            run_shell(a.vault, parser)
            return 0
        if getattr(a, "no_vault", False):
            a.fn(None, a)
            return 0
        a.fn(_unlock(a.vault), a)
        return 0
    except VaultError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
