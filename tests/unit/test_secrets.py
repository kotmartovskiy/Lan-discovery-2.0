# -*- coding: utf-8 -*-
"""Unit: secrets layer (PHASE 2.0-14, §20–21)."""
import base64
import hashlib
import hmac
import json
import os

import pytest

import core.secrets as sec
from core import config as core_config
from core import module_catalog


@pytest.fixture()
def sec_env(tmp_path, monkeypatch):
    """Отдельные пути key/kv в tmp — не трогаем /etc."""
    monkeypatch.setattr(sec, "SECRETS_KEY_PATH", str(tmp_path / "secret.key"))
    monkeypatch.setattr(sec, "KV_PATH", str(tmp_path / "kv.json"))
    monkeypatch.setattr(sec, "_fernet_cache", {})
    yield tmp_path


# --- KV API §21 ---------------------------------------------------------------

def test_kv_roundtrip(sec_env):
    assert sec.get("missing") is None
    assert sec.get("missing", "dflt") == "dflt"
    assert sec.keys() == []

    assert sec.set("catalog_token", "s3cr3t") is True
    assert sec.get("catalog_token") == "s3cr3t"
    assert sec.keys() == ["catalog_token"]

    sec.set("catalog_token", "new-value")  # перезапись
    assert sec.get("catalog_token") == "new-value"

    assert sec.delete("catalog_token") is True
    assert sec.get("catalog_token") is None
    assert sec.delete("catalog_token") is False


def test_kv_encrypted_at_rest(sec_env):
    sec.set("wifi_pass", "top-secret-password")
    with open(sec.KV_PATH, encoding="utf-8") as f:
        raw = f.read()
    assert "top-secret-password" not in raw
    # ключ создан и не в kv.json
    assert os.path.exists(sec.SECRETS_KEY_PATH)
    data = json.loads(raw)
    assert data["wifi_pass"].startswith("gAAAA")


def test_kv_value_coerced_to_str(sec_env):
    sec.set("num", 12345)
    assert sec.get("num") == "12345"


# --- crypto -------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip(sec_env):
    tok = sec.encrypt("пароль-🙂")
    assert tok != "пароль-🙂"
    assert sec.decrypt(tok) == "пароль-🙂"


def test_decrypt_corrupt_raises(sec_env):
    with pytest.raises(ValueError):
        sec.decrypt("не-токен")
    with pytest.raises(ValueError):
        sec.decrypt(base64.b64encode(b"short").decode("ascii"))


def test_legacy_hmac_format(sec_env):
    """Legacy base64(HMAC[:16] || plaintext) расшифровывается (§32)."""
    key = sec.load_key()
    body = "legacy-password".encode("utf-8")
    mac = hmac.new(key, body, hashlib.sha256).digest()[:16]
    entry = base64.b64encode(mac + body).decode("ascii")
    assert sec.decrypt(entry) == "legacy-password"


def test_legacy_hmac_wrong_key(sec_env, tmp_path, monkeypatch):
    key = sec.load_key()
    body = b"legacy-password"
    mac = hmac.new(key, body, hashlib.sha256).digest()[:16]
    entry = base64.b64encode(mac + body).decode("ascii")
    # другой ключ — hmac mismatch
    monkeypatch.setattr(sec, "_fernet_cache", {})
    monkeypatch.setattr(sec, "SECRETS_KEY_PATH",
                        str(tmp_path / "other.key"))
    with pytest.raises(ValueError):
        sec.decrypt(entry)


# --- потребитель: токен каталога модулей (fallback §32) -----------------------

@pytest.fixture()
def catalog_env(tmp_path, monkeypatch, sec_env):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(
        {"modules_catalog": {"repo": "o/r", "branch": "b",
                             "token": "legacy-token"}}),
        encoding="utf-8")
    monkeypatch.setattr(module_catalog, "SETTINGS_PATH", str(settings))
    yield


def test_catalog_token_fallback_legacy(catalog_env):
    assert module_catalog.catalog_cfg()["token"] == "legacy-token"


def test_catalog_token_prefers_secrets(catalog_env, sec_env):
    sec.set("modules_catalog_token", "from-secrets")
    assert module_catalog.catalog_cfg()["token"] == "from-secrets"


# --- классы конфигов (§20): единый справочник путей ---------------------------

def test_config_paths_single_source():
    from core import module_loader
    from core import roles
    assert module_loader.STATE_PATH == core_config.MODULES_STATE_PATH
    assert roles.STATE_PATH == core_config.ROLES_STATE_PATH
    assert core_config.SETTINGS_PATH == module_catalog.SETTINGS_PATH
    assert sec.SECRETS_DIR == "/etc/lan-discovery/secrets"
    assert sec.KV_PATH.startswith(sec.SECRETS_DIR)
