# STORAGE — хранилище (спека §29, фаза 2.0-7)

`core/storage.py` — единая точка доступа к дискам/монтированиям/путям.
Устройство-agnostic: паттерны `mmcblk*`, `sd*`, `nvme*`, `vd*` — классы,
не модели плат (§30).

## Контракт

```python
storage.roots() -> {"media": "/srv/media", "data": "/srv/data", "backup": "/srv/backup"}
storage.path("media", *parts) -> str    # posixpath; неизвестный корень → ValueError
storage.disks() / mounts() / smart(dev) -> ...
```

* `ROOTS` — целевые корни appliance; **все** модули работают с
  пользовательскими файлами только через `storage.path()` (не склеивают
  пути сами — иначе бэкап/миграция сломаются);
* смежные helper'ы в `modules/system_routes`:
  `find_typed_block("SD"/"MMC"/"HDD")`, `_root_mount_source()`,
  `_block_kind()` — роли дисков (системный/данные/пустой).

## Потребители

* **`/storage`** (страница, фаза 2.0-19): корни + `lsblk_text`/`df_text`
  read-only контрактов, пустые состояния при недоступности тулов;
* **IPTV/медиа**: каталоги плейлистов и данных;
* **бэкапы**: цели бэкапов (`roots()["backup"]`);
* **клонирование SD ↔ eMMC**: `find_typed_block` (страница `/system`);
* **capabilities** (`storage`-группа): локальные/съёмные диски, SMART.

## HTTP

```text
GET  /storage                    # страница
GET  /api/… storage/…            # аддитивные контракты
```

## Правила

* модули не вызывают `lsblk`/`df` напрямую — контракты core;
* `smart(dev)` — опрос по требованию (не блокирует страницы);
* изменения (монтирование, format) — только транзакционно
  (network-транзакции — аналогия; для storage-изменений — jobs +
  syschange-подход §26);
* пустой/недоступный источник — `empty state` (§23), не ошибка 500.
