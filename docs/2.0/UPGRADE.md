# UPGRADE — обновление и откаты (§33 Ph.10, §26)

Два уровня отката, **не путать**:

| Уровень | Инструмент | Что откатывает |
|---|---|---|
| **Application** (панель) | `update.sh` | код панели + config + devices.db |
| **System** (пакеты/файлы модулей) | `core/syschange` (§26) | apt-пакеты и файлы, поставленные модулем |

## Обновление панели: `update.sh`

Цикл: **бэкап → apply → verify (py_compile) → pip → restart →
health**; любая ошибка на verify/health ⇒ **авто-rollback** к бэкапу
этого запуска (код/БД восстанавливаются, сервис поднимается,
контрольный health).

```bash
sudo ./update.sh --from /path/to/new-code   # из каталога с новым кодом
sudo ./update.sh                            # git pull (если PREFIX — git-репо)
sudo ./update.sh --dry-run                  # показать план
sudo ./update.sh --rollback [TS]            # откат к бэкапу TS (или последнему)
sudo ./update.sh --keep 10                  # сколько бэкапов хранить (default 5)
```

Бэкапы: `/var/backups/lan-discovery/<TS>/` (код, config, БД).

## Миграции БД

Схему владеет `core/db.py`: при старте применяются шаги строго по
`PRAGMA user_version` (v1 → v2 → v3: device identity `device_id` +
`ip_history`, аддитивно к `devices.ip` PK, backfill существующих
строк). Откат шага не предусмотрен — **сделайте бэкап devices.db
перед первым запуском новой версии** (update.sh делает его сам).

```bash
sqlite3 /opt/lan-discovery/devices.db "PRAGMA user_version;"   # ожидается 3
```

## Обновление зависимостей

```bash
/opt/lan-discovery/venv/bin/pip install -r requirements.txt
sudo systemctl restart lan-discovery
```

## Сбои пакетов, которые ставил модуль (§26)

Транзакции `core/syschange`: `preflight → backup → apply → verify →
rollback` — снимок в `/var/lib/lan-discovery/rollback/<id>/`
(manifest, состояния dpkg, selections, копии файлов). Сбой apt при
установке модуля откатывается автоматически (модуль не помечается
установленным). Ручной откат смоука/транзакции:

```python
from core.syschange import rollback
rollback("/var/lib/lan-discovery/rollback/<id>")
```

## Проверка после обновления

1. `systemctl status lan-discovery` — active;
2. `GET /api/health` — `ok`, версия = ожидаемая (`core/version.py`);
3. `pytest tests/unit -m "not live"` — на dev-стенде;
4. лента событий `/history` — без новых ошибок `system.*`.
