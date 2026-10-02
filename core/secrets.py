# -*- coding: utf-8 -*-
"""Secrets layer (PHASE 2.0-14, спека §21): секреты отдельно от settings.

Модель (§20–21):

- **Crypto**: Fernet (AES-128-CBC + HMAC-SHA256), ключ — отдельный файл
  ``secret.key`` (chmod 600), не в коде/БД/settings; legacy-формат
  base64(HMAC[:16] || plaintext) читается и мигрирует при первом
  шифровании (перенесено из modules/core_routes без смены формата).
- **KV API**: ``get/set/delete/keys`` — программные секреты (токены
  и т.п.) в отдельном файле ``secrets/kv.json`` (chmod 600, атомарная
  запись), значения шифруются при хранении.
- Запрет §21: НЕ хранить passwords/tokens/keys в module manifests или
  обычных JSON settings — читатели переходят на этот API
  (fallback на legacy-место сохраняется временно, §32).
"""
import base64
import hashlib
import hmac
import json
import os

SECRETS_DIR = "/etc/lan-discovery/secrets"
SECRETS_KEY_PATH = "/etc/lan-discovery/secret.key"
KV_PATH = os.path.join(SECRETS_DIR, "kv.json")

_fernet_cache = {}


def load_key():
    """32-байтный ключ из secret.key; файла нет — сгенерировать (0600)."""
    if os.path.exists(SECRETS_KEY_PATH):
        with open(SECRETS_KEY_PATH, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key
    key = os.urandom(32)
    os.makedirs(os.path.dirname(SECRETS_KEY_PATH), exist_ok=True)
    with open(SECRETS_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRETS_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def _fernet():
    from cryptography.fernet import Fernet
    raw = load_key()
    hit = _fernet_cache.get(raw)
    if hit is not None:
        return hit
    if len(raw) == 32:
        key32 = raw
    else:
        key32 = None
        stripped = raw.strip()
        if len(stripped) == 64:
            try:
                key32 = bytes.fromhex(stripped.decode("ascii"))
            except Exception:
                key32 = None
        if key32 is None:
            key32 = hashlib.sha256(raw).digest()
    f = Fernet(base64.urlsafe_b64encode(key32))
    _fernet_cache[raw] = f
    return f


def encrypt(text):
    """Зашифровать строку → Fernet-token (ascii)."""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(data):
    """Расшифровать Fernet-token или legacy base64(HMAC||text).

    Битая запись → ValueError (вызывающий решает: fail или оставить as-is).
    """
    try:
        return _fernet().decrypt(data.encode("ascii")).decode("utf-8")
    except Exception:
        pass
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise ValueError("secrets: corrupt entry")
    if len(raw) < 16:
        raise ValueError("secrets: corrupt entry")
    body = raw[16:]
    if hmac.new(load_key(), body, hashlib.sha256).digest()[:16] != raw[:16]:
        raise ValueError("secrets: hmac mismatch")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("secrets: corrupt entry")


# --- KV API (§21: secrets.get/set/delete) -----------------------------------

def _kv_read():
    try:
        with open(KV_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _kv_write(data):
    os.makedirs(os.path.dirname(KV_PATH), exist_ok=True)
    tmp = KV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, KV_PATH)
    try:
        os.chmod(KV_PATH, 0o600)
    except OSError:
        pass
    return True


def get(key, default=None):
    """Значение секрета или default; отсутствует/не расшифровалось → None.

    default возвращается только для отсутствующего ключа; битое значение
    логируется и считается отсутствующим (не поднимает исключений наружу).
    """
    import logging
    raw = _kv_read().get(key)
    if raw is None:
        return default
    try:
        return decrypt(raw)
    except ValueError:
        logging.getLogger("lan-discovery").error(
            "SECRETS: не расшифровалось значение %r", key)
        return default


def set(key, value):
    """Сохранить секрет (шифрование at rest); value приводится к str."""
    data = _kv_read()
    data[key] = encrypt(str(value))
    return _kv_write(data)


def delete(key):
    """Удалить секрет; True если ключ был."""
    data = _kv_read()
    if key not in data:
        return False
    del data[key]
    _kv_write(data)
    return True


def keys():
    """Список имён секретов (без значений)."""
    return sorted(_kv_read())
