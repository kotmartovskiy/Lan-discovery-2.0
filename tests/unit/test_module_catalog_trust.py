# -*- coding: utf-8 -*-
"""Unit: trust каталога модулей (PHASE 2.0-4, спека §12) — sha256,
trusted_publishers, min_core_version, валидация извлечённого манифеста."""
import hashlib
import io
import json
import tarfile

import pytest

import core.module_catalog as cat
import core.module_loader as loader
from core.module_catalog import CatalogError, install_module

MID = "demo"


def make_tgz(manifest):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo("repo-main/%s/module.json" % MID)
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
        b = b"hello"
        info = tarfile.TarInfo("repo-main/%s/module.py" % MID)
        info.size = len(b)
        tf.addfile(info, io.BytesIO(b))
    return buf.getvalue()


BASE_MANIFEST = {"id": MID, "name": "Демо", "version": "1.0.0"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    mods = tmp_path / "modules"
    mods.mkdir()
    monkeypatch.setattr(cat, "MODULES_DIR", str(mods))
    state = {}
    monkeypatch.setattr(cat, "load_state", lambda: state)
    monkeypatch.setattr(cat, "save_state", lambda s: True)
    cfg = {"repo": "x/y", "branch": "main"}
    monkeypatch.setattr(cat, "_settings", lambda: {"modules_catalog": cfg})
    e = {"cfg": cfg, "state": state, "mods": str(mods), "tgz": None}
    monkeypatch.setattr(
        cat, "_http_get",
        lambda url, token="", timeout=30: (
            json.dumps({"modules": [dict(e["index"])]}).encode()
            if "raw.githubusercontent" in url else e["tgz"]),
    )
    return e


def index(env, _manifest=None, **fields):
    """Заполняет index-запись и готовит тарболл (sha256 считаем от него)."""
    m = {"id": MID, "name": "Демо", "version": "1.0.0"}
    m.update(fields)
    env["index"] = m
    env["tgz"] = make_tgz(_manifest or BASE_MANIFEST)
    return env


# ---------------- happy path + sha256 ----------------

def test_install_ok_with_sha256(env):
    index(env)
    digest = hashlib.sha256(env["tgz"]).hexdigest()
    env["index"]["sha256"] = digest
    meta = install_module(MID)
    assert meta["id"] == MID
    import os
    assert os.path.isfile(os.path.join(env["mods"], MID, "module.json"))
    assert env["state"][MID]["sha256"] == digest
    assert env["state"][MID]["source"] == "catalog"


def test_install_ok_without_sha256_by_default(env):
    index(env)  # legacy index без sha256 → ставим (back-compat)
    assert install_module(MID)["id"] == MID


def test_sha256_mismatch_refused(env):
    index(env, sha256="0" * 64)
    with pytest.raises(CatalogError, match="SHA-256"):
        install_module(MID)


def test_require_sha256_refuses_legacy_index(env):
    env["cfg"]["require_sha256"] = True
    index(env)
    with pytest.raises(CatalogError, match="require_sha256"):
        install_module(MID)


# ---------------- trusted_publishers ----------------

def test_trusted_publishers_empty_allows_anything(env):
    index(env)  # без издателя и без ограничений → можно
    assert install_module(MID)["id"] == MID


def test_trusted_publishers_fail_closed(env):
    env["cfg"]["trusted_publishers"] = ["Alice"]
    index(env, publisher="Bob")
    with pytest.raises(CatalogError, match="не входит в доверенные"):
        install_module(MID)
    index(env)  # издатель вообще не указан → тоже отказ (fail-closed)
    with pytest.raises(CatalogError, match="не входит в доверенные"):
        install_module(MID)


def test_trusted_publishers_allows_listed(env):
    env["cfg"]["trusted_publishers"] = ["Alice"]
    index(env, publisher="Alice", _manifest=dict(BASE_MANIFEST, publisher="Alice"))
    assert install_module(MID)["id"] == MID
    assert env["state"][MID]["publisher"] == "Alice"


def test_publisher_declared_only_in_index_refused(env):
    # identity обязан быть и в самом манифесте (fail-closed)
    env["cfg"]["trusted_publishers"] = ["Alice"]
    index(env, publisher="Alice")
    with pytest.raises(CatalogError, match="не совпадает"):
        install_module(MID)


def test_publisher_mismatch_index_vs_manifest(env):
    index(env, publisher="Alice", _manifest=dict(BASE_MANIFEST, publisher="Bob"))
    with pytest.raises(CatalogError, match="не совпадает"):
        install_module(MID)


# ---------------- min_core_version против APP_VERSION ----------------

def test_min_core_version_from_index(env):
    index(env, min_core_version="99.0.0")
    with pytest.raises(CatalogError, match="несовместим"):
        install_module(MID)


def test_min_core_version_from_manifest(env):
    index(env, _manifest=dict(BASE_MANIFEST, min_core_version="99.0.0"))
    with pytest.raises(CatalogError, match="несовместим"):
        install_module(MID)

def test_max_core_version_from_manifest(env):
    index(env, _manifest=dict(BASE_MANIFEST, max_core_version="1.9.9"))
    with pytest.raises(CatalogError, match="несовместим"):
        install_module(MID)


def test_current_core_version_accepted(env):
    import app
    index(env, min_core_version=app.APP_VERSION, max_core_version=app.APP_VERSION)
    assert install_module(MID)["id"] == MID


# ---------------- валидация извлечённого манифеста ----------------

def test_invalid_manifest_permission_refused(env):
    index(env, _manifest=dict(BASE_MANIFEST, permissions=["network"]))
    with pytest.raises(CatalogError, match="неизвестное право"):
        install_module(MID)


def test_wrong_id_in_manifest_refused(env):
    index(env, _manifest=dict(BASE_MANIFEST, id="other"))
    with pytest.raises(CatalogError, match="не совпадает"):
        install_module(MID)


def test_broken_json_manifest_refused(env):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        raw = b"{not json"
        info = tarfile.TarInfo("repo-main/%s/module.json" % MID)
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    index(env)
    env["tgz"] = buf.getvalue()
    with pytest.raises(CatalogError, match="не читается"):
        install_module(MID)


def test_existing_module_requires_update_flag(env):
    index(env)
    install_module(MID)
    with pytest.raises(CatalogError, match="уже существует"):
        install_module(MID)
    assert install_module(MID, update=True)["id"] == MID
