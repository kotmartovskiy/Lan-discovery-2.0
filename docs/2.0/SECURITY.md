# SECURITY — модель безопасности 2.0 (спека §29, §20–21, фазы 2.0-4/14/16)

Локальный appliance-профиль: панель работает в LAN, «root by design»
(см. [`docs/Безопасность.md`](../Безопасность.md)). Что меняется в 2.0 —
граничные механизмы ядра:

## Секреты (§21, `core/secrets.py`)

```python
secrets.get(key, default=None) / set(key, value) / delete(key) / keys()
```

* отдельный `kv.json` рядом с `settings.json` (секреты ≠ конфиг);
* значения — **Fernet at rest**, файл `0600`, атомарная запись;
* токен каталога модулей (`modules_catalog_token`) читается из secrets
  с fallback на legacy-место в settings (§32);
* UI-менеджер криптографии (Fernet/legacy-HMAC, `secret.key`)
  перенесён из роутов в ядро — модули не содержат крипто-логики.

## Trust манифестов каталога (фаза 2.0-4, `core/module_catalog`)

Настройки каталога в settings:

```json
"modules_catalog": {
  "require_sha256": true,
  "trusted_publishers": ["..."]
}
```

* `sha256` index-блока сверяется **всегда**, когда он есть;
* непустой `trusted_publishers` → fail-closed: издатель вне списка =
  отказ;
* без подписанного/проверенного манифеста установка не идёт.

## System rollback (§26, `core/syschange.py`)

Установка пакетов/правка файлов — транзакция:
`preflight → backup → apply → verify → rollback`. Снимок —
`/var/lib/lan-discovery/rollback/<id>/` (manifest, состояния dpkg,
selections, копии файлов). Публичный `rollback(txn_dir)` — для ручного
отката/смоука. Разделение: **application rollback** (обновление панели)
— в `update.sh`; **system rollback** — здесь.

## Роли и доступ (§18/§35)

* пользователи/пароли: `users.json`, SHA-256 (операторские,
  residual из 1.1 — не трогаем);
* `login_required` / `admin_required` / `can_edit` — из `ctx`
  (единые декораторы, модули не изобретают свои);
* роль модуля в UI-доступе — через module enable/disable и roles
  (см. [ROLES.md](ROLES.md)); permissions-поля манифеста —
  trust-слой state источника.

## Границы (§35 «security boundaries»)

| Слой | Доступ |
|---|---|
| core | секреты, БД, sys-вызовы (`core.process/services`) |
| module | только через `ctx` (auth) и `core.*`; нет прямого `app` |
| UI | только HTTP API с auth; системные действия — POST + admin |
| каталог модулей | fail-closed trust (sha256/publisher) |

## Не делаем (принято, §37)

* TLS/WAN-проброс/файрвол — вне периметра 2.0;
* `Server` header без версий — закрыто в 1.1 (№66), сохранить;
* пароли не переходим на argon2 — операторские, residual осознан.
