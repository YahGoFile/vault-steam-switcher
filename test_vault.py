"""Tests for vault.py and steam.py.  Run:  python -m pytest -q"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import steam as S

# fast KDF so the suite runs quickly (crypto path is identical)
FAST = {"alg": "argon2id", "t": 2, "m": 19456, "p": 1}


# ---------------------------------------------------------------- crypto ----
def test_encrypt_decrypt_roundtrip():
    blob = V.encrypt_blob(b"hello secret", "correct horse battery staple", FAST)
    assert blob["ct"] and blob["nonce"]
    assert V.decrypt_blob(blob, "correct horse battery staple") == b"hello secret"


def test_wrong_password_fails():
    blob = V.encrypt_blob(b"data", "rightpass", FAST)
    with pytest.raises(V.VaultError):
        V.decrypt_blob(blob, "wrongpass")


def test_unique_salt_and_nonce_each_save():
    a = V.encrypt_blob(b"x", "pw", FAST)
    b = V.encrypt_blob(b"x", "pw", FAST)
    assert a["kdf"]["salt"] != b["kdf"]["salt"]
    assert a["nonce"] != b["nonce"]
    assert a["ct"] != b["ct"]  # same plaintext, different ciphertext


def test_tamper_ciphertext_detected():
    blob = V.encrypt_blob(b"important", "pw", FAST)
    raw = bytearray(V._b64d(blob["ct"]))
    raw[0] ^= 0x01
    blob["ct"] = V._b64e(bytes(raw))
    with pytest.raises(V.VaultError):
        V.decrypt_blob(blob, "pw")


def test_tamper_kdf_params_detected():
    # header (KDF params) is authenticated as AAD -> downgrade must fail
    blob = V.encrypt_blob(b"important", "pw", FAST)
    blob["kdf"]["t"] = 99
    with pytest.raises(V.VaultError):
        V.decrypt_blob(blob, "pw")


def test_reject_weak_kdf():
    with pytest.raises(V.VaultError):
        V.encrypt_blob(b"x", "pw", {"alg": "argon2id", "t": 1, "m": 8, "p": 1})


# ----------------------------------------------------------------- store ----
def test_vault_create_open_persist(tmp_path):
    p = tmp_path / "v.json"
    v = V.Vault.create(p, "masterpass123", kdf=FAST)
    v.entries.append(V.Entry(title="GitHub", username="me", password="s3cret"))
    v.save()
    reopened = V.Vault.open(p, "masterpass123")
    assert len(reopened.entries) == 1
    assert reopened.entries[0].password == "s3cret"
    # file perms are owner-only
    assert (os.stat(p).st_mode & 0o777) == 0o600


def test_open_wrong_master(tmp_path):
    p = tmp_path / "v.json"
    V.Vault.create(p, "masterpass123", kdf=FAST)
    with pytest.raises(V.VaultError):
        V.Vault.open(p, "nope")


def test_backup_written_on_second_save(tmp_path):
    p = tmp_path / "v.json"
    v = V.Vault.create(p, "pw", kdf=FAST)
    v.save()
    assert p.with_suffix(".json.bak").exists()


def test_change_password(tmp_path):
    p = tmp_path / "v.json"
    v = V.Vault.create(p, "old-password-xx", kdf=FAST)
    v.entries.append(V.Entry(title="X", password="p"))
    v.change_password("new-password-xx")
    with pytest.raises(V.VaultError):
        V.Vault.open(p, "old-password-xx")
    assert V.Vault.open(p, "new-password-xx").entries[0].password == "p"


def test_find_ambiguous_and_search(tmp_path):
    v = V.Vault(tmp_path / "v.json", "pw", kdf=FAST)
    v.entries += [V.Entry(title="Gmail work"), V.Entry(title="Gmail home")]
    assert len(v.search("gmail")) == 2
    with pytest.raises(V.VaultError):
        v.find("gmail")
    assert v.find(v.entries[0].id).title == "Gmail work"


# ---------------------------------------------------- credential parser ----
def test_parse_labelled_block():
    # the labelled format: login, its password, an email with ITS OWN password,
    # and a webmail URL - all on one line. Values here are fake placeholders.
    text = ("Login demo_user01 Password : Pa55w0rd-demo "
            "Email Account : demo@example.com Password : Mailpw-demo "
            "Webmail : https://mail.example.com/")
    [e] = V.parse_accounts(text, title="My account")
    assert e.username == "demo_user01"
    assert e.password == "Pa55w0rd-demo"
    assert e.email == "demo@example.com"
    assert e.email_password == "Mailpw-demo"
    assert e.webmail == "https://mail.example.com/"
    assert e.title == "My account"


def test_parse_multiline_block():
    text = ("Login: alice\nPassword: pw1\n"
            "Email Account: alice@mail.com\nPassword: pw2\nWebmail: https://mail.com")
    [e] = V.parse_accounts(text)
    assert (e.username, e.password) == ("alice", "pw1")
    assert (e.email, e.email_password, e.webmail) == ("alice@mail.com", "pw2", "https://mail.com")


def test_parse_multiple_accounts_by_repeated_login():
    text = ("Login a Password : p1 Email Account : a@x.com Password : e1 "
            "Login b Password : p2 Email Account : b@x.com Password : e2")
    out = V.parse_accounts(text, title="Batch")
    assert len(out) == 2
    assert out[0].username == "a" and out[0].email_password == "e1"
    assert out[1].username == "b" and out[1].email == "b@x.com"
    assert out[0].title == "Batch #1"


def test_parse_colon_delimited_line():
    [e] = V.parse_accounts("bob:hunter2")
    assert e.username == "bob" and e.password == "hunter2"


def test_parse_pipe_delimited_four_fields():
    [e] = V.parse_accounts("carol|pw|carol@ex.com|mailpw|https://webmail.ex.com")
    assert e.username == "carol" and e.email == "carol@ex.com"
    assert e.email_password == "mailpw" and e.webmail == "https://webmail.ex.com"


def test_parse_dedup_on_import(tmp_path):
    v = V.Vault(tmp_path / "v.json", "pw", kdf=FAST)
    e = V.parse_accounts("dave:pw123")[0]
    v.entries.append(e)
    dup = V.parse_accounts("dave:pw123")[0]
    assert v.is_duplicate(dup)


# ------------------------------------------------------------ generator ----
def test_generate_password_properties():
    pw = V.generate_password(24)
    assert len(pw) == 24
    assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)


def test_generate_no_symbols_is_alnum():
    assert V.generate_password(30, symbols=False).isalnum()


# ------------------------------------------------------------------ VDF ----
def test_vdf_roundtrip():
    src = '"users"\n{\n\t"123"\n\t{\n\t\t"AccountName"\t\t"bob"\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n'
    data = S.vdf_parse(src)
    assert data["users"]["123"]["AccountName"] == "bob"
    reparsed = S.vdf_parse(S.vdf_dump(data))
    assert reparsed == data


def test_vdf_handles_comments_and_escapes():
    src = '"root"\n{\n\t// a comment\n\t"path"\t\t"C:\\\\Games"\n}'
    data = S.vdf_parse(src)
    assert data["root"]["path"] == "C:\\Games"


# -------------------------------------------------------- steam switching ----
def _fake_steam(tmp_path):
    root = tmp_path / "Steam"
    (root / "config").mkdir(parents=True)
    login = {"users": {
        "76561190000000001": {"AccountName": "main_acc", "PersonaName": "MainGuy",
                              "RememberPassword": "1", "MostRecent": "1",
                              "AllowAutoLogin": "1", "Timestamp": "2000"},
        "76561190000000002": {"AccountName": "smurf_acc", "PersonaName": "SmurfGuy",
                              "RememberPassword": "1", "MostRecent": "0",
                              "AllowAutoLogin": "1", "Timestamp": "1000"},
    }}
    (root / "config" / "loginusers.vdf").write_text(S.vdf_dump(login), "utf-8")
    reg = {"Registry": {"HKCU": {"Software": {"Valve": {"Steam": {
        "AutoLoginUser": "main_acc", "RememberPassword": "1"}}}}}}
    (root / "registry.vdf").write_text(S.vdf_dump(reg), "utf-8")
    return S.SteamPaths(root, root / "config" / "loginusers.vdf",
                        root / "registry.vdf", ["/bin/true"])


def test_steam_list_accounts(tmp_path):
    paths = _fake_steam(tmp_path)
    accts = S.list_accounts(paths)
    assert {a.account for a in accts} == {"main_acc", "smurf_acc"}
    assert accts[0].account == "main_acc"  # most recent timestamp first


def test_steam_switch_sets_most_recent_and_autologin(tmp_path, monkeypatch):
    paths = _fake_steam(tmp_path)
    monkeypatch.setattr(S, "is_steam_running", lambda: False)
    launched = {}
    monkeypatch.setattr(S, "launch_steam", lambda p: launched.setdefault("yes", True))
    acc = S.switch("smurf", paths=paths)
    assert acc.account == "smurf_acc"
    data = S.vdf_parse(paths.loginusers.read_text())
    flags = {u["AccountName"]: u["MostRecent"] for u in data["users"].values()}
    assert flags == {"main_acc": "0", "smurf_acc": "1"}
    reg = S.vdf_parse(paths.registry_vdf.read_text())
    assert reg["Registry"]["HKCU"]["Software"]["Valve"]["Steam"]["AutoLoginUser"] == "smurf_acc"
    assert launched["yes"]


def test_steam_switch_unknown_account_raises(tmp_path):
    paths = _fake_steam(tmp_path)
    with pytest.raises(S.SteamError):
        S.switch("nonexistent", paths=paths)


def test_steam_switch_backs_up_files(tmp_path, monkeypatch):
    paths = _fake_steam(tmp_path)
    monkeypatch.setattr(S, "is_steam_running", lambda: False)
    monkeypatch.setattr(S, "launch_steam", lambda p: None)
    S.switch("main_acc", paths=paths)
    assert paths.loginusers.with_suffix(".vdf.vaultbak").exists()


def test_avatar_path(tmp_path):
    paths = _fake_steam(tmp_path)
    cache = paths.root / "config" / "avatarcache"
    cache.mkdir(parents=True)
    assert S.avatar_path(paths, "76561190000000001") is None
    (cache / "76561190000000001.png").write_bytes(b"\x89PNG")
    assert S.avatar_path(paths, "76561190000000001").name == "76561190000000001.png"


def test_make_shortcuts(tmp_path):
    paths = _fake_steam(tmp_path)
    out = tmp_path / "shortcuts"
    made = S.make_shortcuts(out, paths=paths)
    assert len(made) == 2
    assert all(f.exists() for f in made)
    assert any("main_acc" in f.read_text() for f in made)
