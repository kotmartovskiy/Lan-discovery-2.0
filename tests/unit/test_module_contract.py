# -*- coding: utf-8 -*-
"""Unit: module contract v2 (PHASE 2.0-15, §19/§24).

Контракт: единственная сигнатура register_routes(app, ctx); builtin
манифесты несут v2-поля version/capabilities.
"""
import importlib
import inspect
import io
import json
import os

from core.manifest import parse_version, validate_manifest

_ROOT = os.path.dirname(  # корень репо (из tests/unit/...)
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULES_DIR = os.path.join(_ROOT, "modules")

ROUTE_MODULES = (
    "auth",
    "module_manager",
    "devices_routes",
    "jobs_routes",
    "automation_routes",
    "weather_routes",
    "system_routes",
    "network_routes",
    "media_routes",
    "monitoring_routes",
    "inventory_routes",
    "core_routes",
)

# builtin-манифесты с hardware-требованиями (§24)
CAPS_EXPECTED = {
    "camera": ["camera"],
    "radio": ["radio"],
    "wifianalyzer": ["network"],
    "disks": ["storage"],
    "sys-emmc": ["storage"],
    "player": ["media"],
}


def test_register_routes_uniform_signature():
    for name in ROUTE_MODULES:
        mod = importlib.import_module("modules." + name)
        sig = inspect.signature(mod.register_routes)
        params = list(sig.parameters)
        assert params == ["app", "ctx"], \
            "%s.register_routes: %s (ожидается [app, ctx])" % (name, params)


def _builtin_manifests():
    out = []
    for name in sorted(os.listdir(_MODULES_DIR)):
        path = os.path.join(_MODULES_DIR, name, "module.json")
        if os.path.isfile(path):
            with io.open(path, encoding="utf-8") as f:
                out.append((name, json.load(f)))
    return out


def test_builtin_manifests_have_version():
    found = _builtin_manifests()
    assert len(found) >= 33
    for mid, m in found:
        assert parse_version(m.get("version")) is not None, \
            "%s: нет читаемого version" % mid
        assert not validate_manifest(m), \
            "%s: %s" % (mid, validate_manifest(m))


def test_builtin_manifest_capabilities():
    seen = {}
    for mid, m in _builtin_manifests():
        caps = m.get("capabilities")
        if caps is None:
            continue
        assert isinstance(caps, list) and caps, mid
        assert all(isinstance(c, str) for c in caps), mid
        seen[mid] = caps
    for mid, caps in CAPS_EXPECTED.items():
        assert seen.get(mid) == caps, "%s: %r" % (mid, seen.get(mid))
