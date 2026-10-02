"""Каталог модулей из удалённого GitHub-репозитория.

index.json с raw.githubusercontent.com описывает доступные модули;
установка/обновление — распаковка каталога модуля из tarball ветки
(codeload.github.com) в modules/<id>/. Только для администратора.

Настройки (опционально) в /etc/lan-discovery/settings.json:
    "modules_catalog": {"repo": "owner/name", "branch": "main", "token": "...",
        "require_sha256": true,            # отказывать без sha256 в index
        "trusted_publishers": ["Alice"]}   # непустой список = fail-closed:
                                           # index без publisher или с чужим
                                           # издателем не ставится

Trust (спека §12, PHASE 2.0-4): SHA-256 тарболла берётся из index.json и
сверяется всегда, когда там есть; trusted_publishers — opt-in fail-closed;
min_core_version/max_core_version (index и module.json) против APP_VERSION;
манифест после распаковки проходит core.manifest.validate_manifest.
Подписи/PKI — нет (спека: только фундамент).
"""
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import time
import urllib.request

from core import manifest as manifest_mod
from core.module_loader import MODULES_DIR, discover_modules, load_state, save_state

SETTINGS_PATH = "/etc/lan-discovery/settings.json"
DEFAULT_REPO = "kotmartovskiy/Lan-discovery-modules"
DEFAULT_BRANCH = "main"
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}$")
MAX_FILES = 500

_index_cache = {"data": None, "ts": 0.0, "key": None}


class CatalogError(Exception):
    pass


def _settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def catalog_cfg():
    s = _settings().get("modules_catalog") or {}
    tp = s.get("trusted_publishers")
    return {
        "repo": str(s.get("repo") or DEFAULT_REPO),
        "branch": str(s.get("branch") or DEFAULT_BRANCH),
        "token": str(s.get("token") or ""),
        "require_sha256": bool(s.get("require_sha256")),
        "trusted_publishers": [str(x) for x in tp] if isinstance(tp, list) else [],
    }


def _sha256_hex(blob):
    return hashlib.sha256(blob).hexdigest()


def _app_version():
    """APP_VERSION ядра; недоступен → "" (проверку версии пропускаем)."""
    try:
        from app import APP_VERSION
        return str(APP_VERSION)
    except Exception:
        return ""


def _trust_index_meta(meta):
    """Trust-проверки записи index.json до скачивания архива."""
    cfg = catalog_cfg()
    pub = str(meta.get("publisher") or "")
    tp = cfg["trusted_publishers"]
    if tp and pub not in tp:
        raise CatalogError(
            "издатель %r не входит в доверенные (trusted_publishers)"
            % (pub or "не указан"))
    ok, why = manifest_mod.core_version_ok(
        meta.get("min_core_version"), meta.get("max_core_version"),
        _app_version())
    if not ok:
        raise CatalogError("модуль «%s» несовместим: %s" % (meta["id"], why))


def _verify_checksum(blob, meta):
    """SHA-256 тарболла против index.json. Возвращает hex или ""."""
    want = str(meta.get("sha256") or "").strip().lower()
    if want:
        got = _sha256_hex(blob)
        if got != want:
            raise CatalogError(
                "SHA-256 архива не совпадает (ожидался %s…, получен %s…)"
                % (want[:12], got[:12]))
        return got
    if catalog_cfg()["require_sha256"]:
        raise CatalogError(
            "в index нет sha256 для «%s», а require_sha256=true" % meta["id"])
    return ""


def _validate_extracted_manifest(raw, meta, mid):
    """Проверка module.json из архива: JSON + схема v2 + id/publisher/версия."""
    try:
        mj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise CatalogError("module.json не читается: %s" % e)
    if mj.get("id") != mid:
        raise CatalogError(
            "id в module.json (%r) не совпадает с каталогом (%r)"
            % (mj.get("id"), mid))
    errs = manifest_mod.validate_manifest(mj)
    if errs:
        raise CatalogError("манифест невалиден: %s" % "; ".join(errs))
    ipub = str(meta.get("publisher") or "")
    if ipub and str(mj.get("publisher") or "") != ipub:
        raise CatalogError(
            "publisher в module.json (%r) не совпадает с index (%r)"
            % (mj.get("publisher") or "не указан", ipub))
    ok, why = manifest_mod.core_version_ok(
        mj.get("min_core_version"), mj.get("max_core_version"),
        _app_version())
    if not ok:
        raise CatalogError("модуль «%s» несовместим: %s" % (mj.get("id"), why))
    return mj


def _http_get(url, token="", timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "lan-discovery-panel"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_index(refresh=False):
    """index.json каталога (кэш 5 минут). CatalogError при недоступности."""
    cfg = catalog_cfg()
    key = cfg["repo"] + "@" + cfg["branch"]
    now = time.time()
    if (not refresh and _index_cache["data"] is not None
            and now - _index_cache["ts"] < 300 and _index_cache["key"] == key):
        return _index_cache["data"]
    url = "https://raw.githubusercontent.com/%s/%s/index.json" % (cfg["repo"], cfg["branch"])
    try:
        raw = _http_get(url, cfg["token"])
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise CatalogError("не удалось получить index.json: %s" % e)
    mods = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(mods, list):
        raise CatalogError("в index.json нет списка modules")
    clean = [m for m in mods
             if isinstance(m, dict) and m.get("id") and _ID_RE.match(str(m["id"]))]
    out = {
        "repo": cfg["repo"],
        "branch": cfg["branch"],
        "updated": str((data.get("updated") or "") if isinstance(data, dict) else ""),
        "modules": clean,
    }
    _index_cache.update({"data": out, "ts": now, "key": key})
    return out


def catalog_module_ids():
    """Модули, установленные из каталога (state.source == 'catalog')."""
    out = set()
    for mid, e in load_state().items():
        if isinstance(e, dict) and e.get("installed") and e.get("source") == "catalog":
            out.add(mid)
    return out


def module_source(mid):
    return (load_state().get(mid) or {}).get("source")


def install_module(mid, update=False):
    """Ставит (или обновляет) модуль из каталога в modules/<id>/."""
    if not _ID_RE.match(mid or ""):
        raise CatalogError("недопустимый id модуля")
    idx = fetch_index(refresh=True)
    meta = next((m for m in idx["modules"] if m["id"] == mid), None)
    if not meta:
        raise CatalogError("модуль «%s» отсутствует в каталоге" % mid)
    _trust_index_meta(meta)

    dest = os.path.join(MODULES_DIR, mid)
    exists = os.path.isdir(dest)
    if exists and not update:
        raise CatalogError("каталог модуля уже существует: %s" % mid)
    if update and module_source(mid) != "catalog":
        raise CatalogError("обновление доступно только для модулей из каталога")

    cfg = catalog_cfg()
    url = "https://codeload.github.com/%s/tar.gz/refs/heads/%s" % (cfg["repo"], cfg["branch"])
    try:
        blob = _http_get(url, cfg["token"], timeout=120)
    except Exception as e:
        raise CatalogError("не удалось скачать архив: %s" % e)
    digest = _verify_checksum(blob, meta)

    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except Exception as e:
        raise CatalogError("архив повреждён: %s" % e)

    with tf:
        names = tf.getnames()
        if not names:
            raise CatalogError("пустой архив")
        top = names[0].split("/")[0]
        prefix = top + "/" + mid + "/"
        members = [m for m in tf.getmembers() if m.name.startswith(prefix) and m.isfile()]
        if not any(m.name == prefix + "module.json" for m in members):
            raise CatalogError("в архиве нет %s/module.json" % mid)
        if len(members) > MAX_FILES:
            raise CatalogError("слишком много файлов в модуле")
        files = {}
        for m in members:
            rel = m.name[len(prefix):]
            if not rel or rel.startswith("/") or ".." in rel.split("/") or "\\" in rel:
                raise CatalogError("небезопасный путь в архиве")
            src = tf.extractfile(m)
            files[rel] = src.read() if src else b""

    _validate_extracted_manifest(files["module.json"], meta, mid)

    tmp = dest + ".new"
    old = dest + ".old"
    for p in (tmp, old):
        if os.path.isdir(p):
            shutil.rmtree(p)
    os.makedirs(tmp)
    try:
        for rel, data in files.items():
            target = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
        if exists:
            os.rename(dest, old)
        os.rename(tmp, dest)
        if os.path.isdir(old):
            shutil.rmtree(old)
    except Exception as e:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        if not os.path.isdir(dest) and os.path.isdir(old):
            os.rename(old, dest)
        raise CatalogError("ошибка записи файлов: %s" % e)

    state = load_state()
    entry = state.get(mid) or {}
    entry.update({
        "installed": True,
        "enabled": entry.get("enabled", True),
        "source": "catalog",
        "version": str(meta.get("version") or ""),
        "installed_at": time.strftime("%d.%m.%Y %H:%M:%S"),
        "publisher": str(meta.get("publisher") or ""),
        "sha256": digest,
    })
    state[mid] = entry
    save_state(state)
    discover_modules(force=True)
    return meta


def remove_module(mid):
    """Удаляет модуль, установленный из каталога."""
    if not _ID_RE.match(mid or ""):
        raise CatalogError("недопустимый id модуля")
    m = None
    for x in discover_modules():
        if x["id"] == mid:
            m = x
            break
    if m and m.get("builtin"):
        raise CatalogError("встроенный модуль нельзя удалить")
    if module_source(mid) != "catalog":
        raise CatalogError("удаление доступно только для модулей из каталога")
    dest = os.path.join(MODULES_DIR, mid)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    state = load_state()
    state.pop(mid, None)
    save_state(state)
    discover_modules(force=True)
