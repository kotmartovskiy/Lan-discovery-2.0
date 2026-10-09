# Цифровое ТВ, камеры и SDR (фазы 2–5)

Подсистемы, собранные поэтапно на X96 Max: IP-камеры с детекцией движения
(фаза 2), DVB-T2 приемник + tvheadend (фаза 3), SDR-стек (фаза 4) и
LTE-failover (фаза 5). Камеры/тюнер/RTL-свистки физически не установлены —
софт готов, активация после подключения железа.

## Камеры и движение (фаза 2)

Модуль `modules/motion` (вкладка «Модули», роуты `modules/motion_routes.py`):

- **Детекторы:** `snapshot` (сравнение кадров, Pillow), `onvif`
  (ONVIF Events c fallback на snapshot), `hook` (POST на
  `/api/motion/hook/<token>` — внешние камеры сами присылают событие);
  режим `off` — выключен.
- **Событие:** фото сохраняется в `/srv/media/motion/<дата>/`, запись в
  `motion_events`, событие панели, тревога (alarm), очередь уведомлений.
- **Уведомления:** Telegram и e-mail с экспоненциальным бэкоффом (30 с … 1 ч),
  доставка после восстановления связи, фото сжимается до
  `motion.max_photo_kb`; TTL очереди — `motion.queue_ttl_days`.
- **Потеря связи с камерой:** TCP-проба, `MOTION_CAM_DOWN` /
  `MOTION_CAM_UP` (дедупликация 1 ч) — уходит и в события панели, и в
  очередь Telegram/e-mail (`motion_queue.caption`).
- **Hook exempt от CSRF** через `app.extensions["csrf"]` (важно при запуске
  `python app.py` как `__main__` — см. регресс `test_hook_exempt_from_csrf`).

Настройки — секция `motion` в `/etc/lan-discovery/settings.json`
(каналы, пороги, TTL, пауза `silence`).

## DVB-T2 (фаза 3)

### Модули ядра

Ядро `6.18.51-ophub` собрано без DVB (headers-only), поэтому модули собраны
вручную из полного исходника `linux-6.18.51`:

| Модуль | Назначение |
|---|---|
| `dvb-core.ko` | подсистема DVB |
| `cx231xx.ko` | USB-мост Conexant CX23102 (селектор демодуляторов) |
| `cx2341x.ko`, `tveeprom.ko` | зависимости cx231xx |
| `mn88473.ko`, `lgdt3305.ko`, `mb86a20s.ko` | ATSC/ISDB-T демоды (селекты cx231xx) |
| `xc5000.ko`, `tda18271.ko` | тюнеры (селекты cx231xx) |

Установка: `/lib/modules/6.18.51-ophub/updates/dvb/` + `depmod`
(вермажик совпадает — оба конфига с пустым `CONFIG_LOCALVERSION`).
Пересборка после обновления ядра — `tools/build_dvb_modules.sh`.

Тонкости сборки (для пересборки):

- нужен `libelf-dev` — без него падает `modules_prepare` и не создаётся
  `scripts/module.lds`;
- kconfig поднимает `DVB_CORE`/`XC5000`/`TDA18271` в `y` (select'ы) —
  обходится переопределением `CONFIG_*=m` в командной строке make;
- в DVB отсутствует драйвер `r828d` (в mainline только `r820t`) —
  тюнеры на R828D не поддержаны.

### tvheadend

Собран из исходников (`/usr/src/tvheadend`), `make install` →
`/usr/local/bin/tvheadend`, конфиг: `/usr/local/share/tvheadend`,
веб-интерфейс `http://192.168.3.243:9981` (мастер настройки адаптера).
Сервис: `systemctl status tvheadend`.

Сборка: нужны `cmake`, `libdvbcsa-dev`, `liburiparser-dev`, `python3-requests`;
ключи configure используют **подчёркивание** (`--disable-hdhomerun_client`);
вендорные опции `--enable-ffmpeg_static` / `--disable-ffmpeg` отключены,
вместе с `--disable-libx264/x265/vpx/fdkaac/opus/vorbis/theora`.

PVR в Kodi: пакет `kodi-pvr-hts` — после запуска
tvheadend каналы появятся в Kodi → Телевидение.

Пакеты из apt: `w-scan` (скан эфира), `dvb-apps` (`szap`/`czap`).

## SDR (фаза 4)

Установлено: `rtl-sdr` 0.6.0 (бинари `rtl_sdr`, `rtl_tcp`, `rtl_fm`),
`rtl-433` 22.11 (приём 433 МГц датчиков), `dump1090-mutability`
(ADS-B), `dsdcc` 1.9.3 (цифровые речевые режимы DMR/DMR+ вместо `dsd`,
которого нет в Debian bookworm).

**До вставки RTL-свистка сервисы выключены** (чтобы не было
restart-loop): `dump1090-mutability` и `rtl_tcp` — `disabled`.
После вставки:

```bash
systemctl enable --now dump1090-mutability   # ADS-B, веб: http://<ip>:8080/dump1090
systemctl enable --now rtl_tcp                # удаленный RTL-сервер, порт 1234
```

`rtl_433` — консольный, запуск по требованию.

## LTE-failover (фаза 5)

Сервис `lan-failover.service` (`/usr/local/sbin/lan_failover.sh`)
пингует шлюз LAN и внешний хост; серия неудач подряд → подъём
NM-профиля LTE, серия успехов → снятие. Длинный таймаут пробы (8 с)
и пороги серий защищают от ложных срабатываний при большом пинге
и низкой скорости — переключение только на **полный** обрыв.

- конфиг: `/etc/lan-discovery/failover.conf` (все значения опциональны);
- состояние: `/run/lan-failover.state` (`LAN` / `LTE`);
- без LTE-профиля — режим наблюдения (пишет статус, ничего не поднимает).

Когда появится USB-модем, создать профиль NetworkManager:

```bash
nmcli con add type gsm con-name lte apn <apn> \
  ipv4.route-metric 50 user <login> password <pass>
systemctl restart lan-failover
```

`route-metric 50` — чтобы маршрут LTE победил LAN-маршруты (metric 100/600)
только на время обрыва.

## Встраивание в «Приложения»

Сервисы встроены как обычные модули (страница «Модули», admin) —
плитки на странице «Приложения», вкл/выкл и профили ролей работают
как у всех:

| Плитка | Источник iframe | Модуль |
|---|---|---|
| 📺 TVheadend | напрямую `:9981` (своя авторизация) | `tvheadend` |
| 📈 Netdata | напрямую `:19999` | `netdata` |
| ✈️ ADS-B | `/dump1090/` — статику отдаёт панель (роут в `core_routes`), JSON из `/run/dump1090-mutability/` | `dump1090` |

Выключенный модуль `dump1090` → `/dump1090/*` отвечает 404; зависимости
(apt/сервисы) ставятся кнопкой «Установить зависимости».

## Известные ограничения

- железо (камеры, тюнер CX23102, RTL-свисток, LTE-модем) не подключено —
  live-тесты tvheadend/ONVIF/PVR/SDR после покупки;
- `r828d` в mainline-ядре отсутствует (см. выше);
- браузер для Kodi (`System.Exec`) не установлен — нет кандидата в apt
  (`firefox`, `chromium-browser` — `Candidate: (none)`), RetroArch 1.14
  установлен;
- встраивание чужих веб-интерфейсов в панель — отдельным этапом после
  согласования дизайна.
