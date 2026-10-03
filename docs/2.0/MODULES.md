# MODULES — система модулей 2.0 (спека §29, §19/§24)

Модуль — единица расширения: своя предметная область, роуты, страницы,
справка. Ядро не знает о конкретных модулях; модули не знают про `app`.

## Структура

```text
modules/<id>/
  module.json     # манифест (см. ниже)
  <id>_routes.py   # роуты: def register_routes(app, ctx)
  templates/...    # свои шаблоны (от base.html)
  help.md          # раздел справки модуля (§24)
```

## Манифест `module.json`

```json
{
  "id": "camera",
  "version": "2.0.0",
  "name": "Камеры",
  "description": "IP-камеры: RTSP/HTTP-потоки, сетка просмотра",
  "builtin": true,
  "type": "app",
  "capabilities": ["camera"],
  "help": true,
  "prefixes": ["/api/cameras"],
  "app": {"title": "Камеры", "icon": "📷", "category": "Медиа", "order": 30},
  "block": {"slot": "system", "order": 10, "title": "Плата"},
  "tab": {"title": "...", "order": 100, "section": "monitoring"},
  "deps": {"apt": [], "ports": []}
}
```

* `capabilities` — какие capability-группы нужны модулю (compat-check
  в ролях, отображение в `/modules`);
* `app`/`block`/`tab` — как модуль представлен в UI (плитка приложения,
  блок в слоте вкладки, вкладка; `tab.section` — аддитивный override
  раздела §22, иначе `GROUP_TO_SECTION`);
* `prefixes` — URL-префиксы (для выключенного модуля → 404 через
  `disabled_prefixes()`); выключенный модуль не рендерится в навигации;
* `deps.apt` — пакеты (устанавливаются одним dpkg-батчем при install);
* `version` — версия модуля (своя, §34); `source`/`permissions`/
  `hardware` — поля trust-слоя (state источника: `catalog`/`builtin`).

## Единая сигнатура (контракт v2, фаза 2.0-15)

```python
def register_routes(app, ctx): ...
```

`ctx` собирает assembly root (`app.py::ctx = SimpleNamespace(...)`):

| Поле | Что |
|---|---|
| `login_required`, `admin_required`, `can_edit` | auth-декораторы |
| `get_current_user`, `get_current_username` | текущий пользователь |
| `page_data` | общие данные страниц (навигация, статусы) |
| `_cmd`, `_cfg` | запуск команд / чтение settings |
| `DB` | доступ к sqlite (ядро владеет схемой) |
| `service_state`, `_SERVICE_START` | systemd-состояния |
| `GAMES_DIR`, `panel_name`, `check_internet_cached`, `weather_current` | прочие сервисы |

Модули **не импортируют `app`** (инвариант: `modules → app` = 0,
проверяется тестами). Бизнес-нужды core — прямые импорты
`core.*` (это норма: `modules → core`).

## Жизненный цикл

```python
core.module_loader.discover_modules()   # чтение manifests (+кэш)
core.module_loader.module_status(mid)   # (installed, enabled)
core.module_loader.set_module_status(mid, installed=None, enabled=None)
```

* **Установка**: `POST /modules/<id>/install` → job → trust-проверка
  манифеста → apt через `core/syschange` (backup → apply → verify,
  сбой → rollback, модуль НЕ помечается установленным) → state;
* **Страницы модулей**: контракт §24 — рендер через `page_data()`,
  пустые состояния и loading/error — компоненты §23;
* **Справка**: `help.md` модуля попадает в `/help` (`help_sections()`);
  выключенный модуль — секция и якорь скрыты;
* **Каталог**: `core/module_catalog` — установка/обновление из
  `modules_catalog` (публичный репо `Lan-discovery-modules`,
  `index.json` + `<id>/module.json`), токен — из `core.secrets`.

## Правила

* модуль не ломает JSON-контракты API (аддитивно, §32);
* новая абстракция core под модуль — с потребителем и тестом (№5);
* hardware-зависимость — только через `capabilities`-декларацию,
  не через имена плат (§30);
* долгие операции — только через `core.jobs`.
