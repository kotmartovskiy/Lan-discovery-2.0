# DEVELOPMENT — разработка в 2.0 (спека §29)

## Окружение

* Python ≥ 3.9 (CI — 3.11), зависимости: `requirements.txt` +
  `requirements-dev.txt` (pytest);
* запуск из корня: `python app.py` (Flask, порт из
  `settings.web.flask_port`, default 8080);
* конфиги: `/etc/lan-discovery/` (`settings.json`, `users.json`,
  `kv.json`, state-файлы) — тесты перенаправляют пути в tmp
  (патчи `SETTINGS_PATH`/`STATE_PATH` и т.п.);
* systemd: `lan-discovery` (venv в `/opt/lan-discovery/venv`).

## Тесты

```bash
pytest tests/unit -m "not live"      # юнит (дефолтный прогон)
pytest tests/live -m live             # против живого стенда:
                                      #   LAN_PANEL_URL, LAN_PANEL_PASS
```

* `tests/unit/` — 40 файлов: контракты core, роуты (Flask
  `test_client`), shell/UI, installer, appliance smoke §27,
  portability §30;
* `tests/live/` — смоук живого стендa (login/health/scan/install),
  skip при `user_version < 3`; маркер `live` зарегистрирован в
  `tests/live/conftest.py`;
* **изоляция**: unit-тесты патчат пути state/settings/DB в tmp,
  фоновые треды (scan/jobs/events) — фейками; выбор job — по дельте
  `jobs_before`, не по «последнему»;
* известные **Windows-only фейлы** (на CI зелёные):
  `test_core_layer::test_api_disks_shape`, `test_help::test_help_facts_structure`;
  Linux-skip в Windows: `test_capabilities` (/proc).

## CI (`.github/workflows/ci.yml`)

1. `pip install -r requirements.txt -r requirements-dev.txt`;
2. `sudo mkdir -p /etc/lan-discovery` (app создаёт там каталоги);
3. `pytest tests/unit -v`;
4. `python -m py_compile app.py core/*.py modules/*.py tools/*.py`;
5. `bash -n install.sh && ./install.sh --dry-run --skip-apt` (§25).

Ожидаемое число passed = локальное + 5 (пропущенные Windows-тесты).

## Процесс правок (см. AGENTS.md)

1. **бэкап** (на сервере — `cp … backup-$(date …)`; в репо — git);
2. правка → прогон `pytest tests/unit -m "not live"`;
3. коммиты парой: `core:` (код+тесты), затем `docs:` (ROADMAP,
   CHANGELOG, `docs/Архитектура-2.0.md`); префиксы
   `core:/ui:/docs:/fix:/tools:`;
4. `git push origin main` → CI → проверить conclusion явно;
5. если менялся `docs/` для публичной копии — `tools/sanitize_docs.py`.

## Как добавить модуль

1. `modules/<id>/module.json` (id/version/name/type/capabilities/…)
   + `register_routes(app, ctx)` — см. [MODULES.md](MODULES.md);
2. подключить регистратор в `app.py` (assembly root);
3. `help.md` → секция `/help`; раздел UI — `tab.section` §22;
4. unit-тесты (роуты через `test_client`, патчи state в tmp);
5. при hardware-зависимости — декларировать `capabilities`, не
   ветвиться по платам (§30, [CAPABILITIES.md](CAPABILITIES.md)).

## Инварианты (ломать нельзя)

* `modules → app` = 0, `core → app/modules` = 0;
* версия — `core/version.py` (тест сверяет CHANGELOG §34);
* JSON-контракты API — аддитивно (§32);
* долгие операции — `core.jobs`;
* нет плато-специфики (grep-тест §30);
* новый абстрактный слой — потребитель + тест (правило №5).
