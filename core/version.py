# -*- coding: utf-8 -*-
"""Единственный источник версии ядра (спека §34, PHASE 2.0-10).

Потребители APP_VERSION:
- app.py (health / «О системе»; app.APP_VERSION — реэкспорт для тестов);
- core/module_catalog._app_version (min/max_core_version);
- core/module_loader.status_context (compute_status);
- CHANGELOG.md — тест tests/unit/test_version.py сверяет раздел
  «## [<APP_VERSION>]».

Модули имеют собственные версии (module.json/index.json) — не здесь.
"""
APP_VERSION = "2.0.0"
