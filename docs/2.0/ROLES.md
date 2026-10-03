# ROLES — декларативные профили (спека §29, фаза 2.0-5)

Роль (profile) — декларативный набор модулей для сценария
использования appliance: «что включено», без логики включения
(логика — в module loader; hardware — в capabilities).

## Манифесты `roles/*.json`

```json
{
  "id": "default",
  "name": "Базовый",
  "description": "Обнаружение сети, мониторинг, базовые приложения",
  "required_modules": ["devices", "history", "..."],
  "optional_modules": ["iptv", "weather"],
  "conflicts": ["..."]
}
```

* `required_modules` — включаются всегда ( `"*"` = все модули);
* `optional_modules` — предлагаются оператору (без `"*"`);
* `conflicts` — взаимоисключающие модули;
* `id` обязан совпадать с именем файла (`default.json`);
* валидация: `roles.validate_role(m, stem)` — битая роль пропускается
  (не ломает страницу), `load_roles()` — кэш 5 с.

В репозитории: `default`, `media`, `network`, `home-server`,
`network-gateway`, `network-diagnostic-box`, `camera-gateway`,
`industrial-gateway`, `remote-site`.

## API

```text
GET  /roles                 # обзор: роли, активная, blockers
POST /api/roles/<rid>/apply  # применить (admin, can_edit)
GET  /api/roles              # JSON
```

## Применение

```python
roles.apply_role(rid)
  → role_blockers(rid, ctx)   # compat-check: какие required-модули
                              #   несовместимы с capabilities сейчас
  → включение/выключение модулей (module_loader.set_module_status)
  → запись активной роли (state)
```

* активная роль читается через `roles.active_role()` (state);
* `role_blockers` не даёт применить профиль, требующий отсутствующего
  hardware, — оператор видит причину;
* всегда-включённые модули (ядро UI) не отключаются ролью.

## Граница «что принадлежит роли»

| Роль | Не роль |
|---|---|
| какие модули включены для сценария | логика модуля |
| порядок/приоритет сценария (`required`/`optional`) | hardware-детект (capabilities) |
| конфликты модулей | UI-разметка |

Правило: новую роль можно создать, положив JSON в `roles/`, **не
изменяя core** (§36 — «позволяет создавать новые roles без изменения
Core»).

## Тесты

`tests/unit/test_roles.py` — валидация манифестов, `nav_groups`,
apply/compat-check на заглушках.
