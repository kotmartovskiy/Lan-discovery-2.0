# API — каталог роутов

Сгенерировано из кода 30.09.2026 (PHASE 14), обновлено 01.10.2026
(версия 1.1): **173 роута** —
146 под `login_required`, 44 под `admin_required` (21 из них
дублирует `login_required`), **3 открытых**: `GET/POST /login`,
`GET /logout`, `GET /api/health`. Новые в 1.1: `/api/dashboard`,
`/capabilities`, `/api/capabilities`, `/roles` + `/api/roles` (см.
секции ниже).

Колонка **Доступ**: `login` — любая роль (admin/editor/guest),
`admin` — только администратор, `открытый` — без сессии.
Все `POST/PUT/DELETE` требуют CSRF-токен (форма — скрытое поле
`csrf_token`, JS — заголовок `X-CSRFToken` из `<meta name="csrf-token">`);
анонимный mutating-запрос → 400/302. Подробнее —
[Безопасность](Безопасность.md).

Связанные страницы: [Модули](Модули.md) (ответственность модулей),
[Архитектура](Архитектура.md) (структура кода).

## Аутентификация (`modules/auth.py`) — 2

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET,POST | `/login` | открытый | Вход (GET — форма с CSRF, POST — проверка bcrypt) |
| GET | `/logout` | открытый | Выход, уничтожение сессии |

## Устройства, сканирование и события (`modules/devices_routes.py`) — 7

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/` | login | Главная: список устройств LAN |
| GET | `/history` | login | История событий (бейджи severity) |
| GET | `/device/<ip>` | login | Карточка устройства |
| POST | `/device/<ip>/name` | login | Переименовать устройство |
| POST | `/api/device/<ip>/dismiss-new` | login | Скрыть пометку «новое» |
| GET | `/api/events` | login | Список событий (фильтры severity/source, JSON) |
| POST | `/api/scan` | admin | Запустить скан LAN в фоне (POST) |

## Приложения и данные (core) (`modules/core_routes.py`) — 30

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/games/<path:filename>` | login | Статические игры |
| GET | `/apps` | login | Все приложения (iframe-вкладки) |
| GET | `/torrent` | login | Торрент-клиент (страница) |
| GET | `/apps/notes` | login | Заметки |
| GET | `/apps/passwords` | login | Секреты/пароли |
| GET | `/apps/filemanager` | login | Файловый менеджер |
| GET | `/apps/terminal` | login | Терминал (веб) |
| GET | `/help` | login | Справка по модулям |
| GET | `/currencies` | login | Курсы валют (страница) |
| GET | `/api/currencies` | login | Курсы валют (JSON) |
| GET | `/api/settings` | login | Чтение/запись настроек панели |
| POST | `/api/settings` | admin | Чтение/запись настроек панели |
| GET | `/api/users` | admin | Список пользователей |
| POST | `/api/users/<username>/password` | admin | Смена пароля пользователя |
| POST | `/api/users/<username>/toggle` | admin | Вкл/отключить пользователя |
| GET | `/api/notes` | login | Заметки: список/создание |
| POST | `/api/notes` | login | Заметки: список/создание |
| PUT | `/api/notes/<int:note_id>` | login | Заметка: изменение/удаление |
| DELETE | `/api/notes/<int:note_id>` | login | Заметка: изменение/удаление |
| GET | `/api/secrets` | login | Секреты: список/создание |
| POST | `/api/secrets` | login | Секреты: список/создание |
| PUT | `/api/secrets/<int:secret_id>` | login | Секрет: изменение/удаление |
| DELETE | `/api/secrets/<int:secret_id>` | login | Секрет: изменение/удаление |
| GET | `/api/filemanager/list` | admin | Листинг каталога |
| GET | `/api/filemanager/read` | admin | Чтение файла |
| POST | `/api/filemanager/mkdir` | admin | Создать каталог |
| POST | `/api/filemanager/delete` | admin | Удалить файл/каталог |
| POST | `/api/filemanager/rename` | admin | Переименовать |
| POST | `/api/filemanager/copy` | admin | Копировать |
| POST | `/api/filemanager/move` | admin | Переместить |

## Система, сервисы и бэкапы (`modules/system_routes.py`) — 29

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| POST | `/api/service/<service>/<action>` | admin | Управление systemd-сервисом (start/stop/restart/enable/disable) |
| GET | `/api/samba/guest` | login | Состояние гостевого доступа Samba (шары из smb.conf) |
| POST | `/api/samba/guest` | admin | Включение/выключение гостевого доступа Samba (бэкап + testparm + reload) |
| GET | `/api/status` | login | Статус системы (CPU/RAM/сеть, JSON) |
| GET | `/api/system/health` | login | Health c деталями (platform/thermal/storage) |
| GET | `/api/dashboard` | login | Агрегат для поллера шапки: система + health + устройства + события + alerts (1.1, один запрос вместо ×2) |
| GET | `/api/health` | открытый | Короткий health-check (открытый эндпоинт) |
| GET | `/system` | login | Раздел «Система» |
| POST | `/system/backup` | admin | Полный бэкап (код+настройки+БД) |
| POST | `/system/backup-test` | admin | Тест бэкапа |
| POST | `/system/db-backup` | admin | Бэкап devices.db |
| POST | `/system/db-restore` | admin | Восстановить БД из копии |
| POST | `/system/emmc-restore` | admin | Восстановление eMMC |
| GET | `/api/backup-status` | login | Статус/список бэкапов |
| GET | `/about` | login | О системе |
| GET | `/api/disk/check` | login | Проверка диска |
| GET | `/api/disk/info` | login | Информация о диске |
| POST | `/api/disk/prepare` | admin | Подготовка диска |
| POST | `/api/disk/transfer` | admin | Перенос данных на HDD |
| POST | `/api/disk/verify` | admin | Верификация переноса |
| POST | `/api/disk/poweroff` | admin | Выключить диск |
| POST | `/api/reboot` | admin | Перезагрузка хоста |
| POST | `/api/clone/start` | admin | Запуск клонирования eMMC→SD |
| GET | `/api/sd-info` | login | Информация о SD-карте |
| GET | `/api/clone/status` | login | Прогресс клонирования |
| GET | `/apps/disks` | login | Диски (страница) |
| GET | `/api/disks` | login | Список дисков (JSON) |
| GET | `/api/disktool/disks` | login | Disk Manager: диски с guard-флагами (system/mounted/operable) |
| GET | `/api/disktool/partitions?dev=` | login | Таблица разделов (parted -m) |
| GET | `/api/disktool/smart?dev=` | login | SMART: здоровье/температура/атрибуты (smartctl -j) |
| GET | `/api/disktool/images` | login | Образы: каталог FreeDOS + загруженные .img/.iso |
| GET | `/api/disktool/status` | login | Прогресс долгой операции (клон/запись/скачивание) |
| POST | `/api/disktool/wipe` | admin | Очистка целого диска (wipefs -a) |
| POST | `/api/disktool/part` | admin | Создание раздела (parted mkpart, байты) |
| POST | `/api/disktool/part-del` | admin | Удаление раздела (parted rm) |
| POST | `/api/disktool/format` | admin | Формат раздела (mkfs.ext4/vfat/ntfs/exfat/…) |
| POST | `/api/disktool/fsck` | admin | Проверка ФС (e2fsck -n) |
| POST | `/api/disktool/clone` | admin | Клон диска→диск (dd с прогрессом, фон) |
| POST | `/api/disktool/write` | admin | Запись образа на диск (dd с прогрессом, фон) |
| POST | `/api/disktool/op/stop` | admin | Остановка фоновой операции |
| POST | `/api/disktool/images/freedos` | admin | Скачивание/извлечение FreeDOS-образа (фон) |
| POST | `/api/disktool/images/upload` | admin | Загрузка своего .img/.iso (multipart, ≤700МБ) |
| GET | `/tv` | login | TV-режим (Kodi/CRT/LCD): крупные шрифты, D-pad |
| GET | `/capabilities` | login | Страница «Возможности»: hardware-снимок платформы (1.1, STEP 7) |
| GET | `/api/capabilities` | login | Возможности платформы: CPU/RAM/диски/сеть/аппаратура + reliability (1.1, JSON) |

## Сеть и инструменты (`modules/network_routes.py`) — 21

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/api/network/config` | login | Чтение/запись сетевой конфигурации |
| POST | `/api/network/config` | admin | Чтение/запись сетевой конфигурации |
| GET | `/api/network/ifaces` | admin | Сетевые интерфейсы хоста (форма выбора скана) |
| GET | `/api/network/check` | login | Проверка сети (HTTP fallback) |
| POST | `/api/network/check_host` | login | Проверить конкретный хост |
| POST | `/api/nettools/ping` | login | ping |
| POST | `/api/nettools/dns` | login | DNS-резолв |
| POST | `/api/nettools/ports` | login | Скан портов |
| POST | `/api/nettools/trace` | login | traceroute |
| GET | `/apps/nettools` | login | Сетевые инструменты (страница) |
| GET | `/api/wifi/scan` | login | Скан WiFi-сетей |
| GET | `/apps/wifianalyzer` | login | WiFi-анализатор (страница) |
| GET | `/api/bluetooth/status` | login | Статус Bluetooth |
| POST | `/api/bluetooth/scan` | login | Скан Bluetooth |
| POST | `/api/bluetooth/discoverable` | login | Сделать обнаруживаемым |
| GET | `/api/bluetooth/devices` | login | Список Bluetooth-устройств |
| POST | `/api/bluetooth/connect` | login | Подключить |
| POST | `/api/bluetooth/disconnect` | login | Отключить |
| POST | `/api/bluetooth/pair` | login | Спарить |
| POST | `/api/bluetooth/remove` | login | Удалить устройство |
| POST | `/api/bluetooth/power` | login | Питание BT вкл/выкл |
| GET | `/apps/bluetooth` | login | Bluetooth (страница) |

## Медиа: IPTV, радио, плеер, камеры, торренты, DLNA/UPnP (`modules/media_routes.py`) — 64

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| POST | `/system/iptv/add` | login | Добавить IPTV-плейлист |
| POST | `/system/iptv/delete/<int:index>` | login | Удалить плейлист |
| POST | `/system/iptv/toggle/<int:index>` | login | Вкл/выкл плейлист |
| POST | `/system/iptv/update/<int:index>` | login | Обновить плейлист |
| POST | `/system/iptv` | admin | Сохранить все плейлисты |
| GET | `/api/alarm/files` | login | Файлы для будильника |
| GET | `/api/alarm/volume` | login | Громкость будильника |
| POST | `/api/alarm/volume` | login | Громкость будильника |
| POST | `/api/alarm/play` | login | Запустить сигнал будильника |
| POST | `/api/alarm/stop` | login | Остановить сигнал |
| GET | `/api/alarms` | login | Список/создание будильников |
| POST | `/api/alarms` | login | Список/создание будильников |
| DELETE | `/api/alarms/<int:aid>` | login | Будильник: удаление/правка |
| POST | `/api/alarms/<int:aid>/toggle` | login | Вкл/выкл будильник |
| GET | `/api/radio/stations` | login | Список радиостанций |
| GET | `/api/radio/playlists` | login | Радиоплейлисты |
| POST | `/api/radio/play` | login | Включить радио |
| POST | `/api/radio/stop` | login | Выключить радио |
| POST | `/api/radio/next` | login | Следующая станция |
| POST | `/api/radio/prev` | login | Предыдущая станция |
| GET | `/api/radio/status` | login | Статус радиоплеера |
| GET | `/api/player/browse` | login | Обзор медиатеки |
| GET | `/api/player/playlists` | login | Плейлисты плеера |
| POST | `/api/player/playlist` | admin | Создать/сохранить плейлист |
| DELETE | `/api/player/playlist/<filename>` | admin | Удалить плейлист |
| GET | `/api/player/load` | login | Загрузить трек/плейлист |
| POST | `/api/player/play` | login | Play |
| POST | `/api/player/stop` | login | Stop |
| POST | `/api/player/next` | login | Next |
| POST | `/api/player/prev` | login | Prev |
| POST | `/api/player/random` | login | Перемешать |
| GET | `/api/player/status` | login | Статус плеера |
| GET | `/api/cameras` | login | Список/добавление камер |
| POST | `/api/cameras` | admin | Список/добавление камер |
| PUT | `/api/cameras/<int:cam_id>` | admin | Камера: правка/удаление |
| DELETE | `/api/cameras/<int:cam_id>` | admin | Камера: правка/удаление |
| POST | `/api/cameras/<int:cam_id>/start` | login | Запустить поток |
| POST | `/api/cameras/<int:cam_id>/stop` | login | Остановить поток |
| POST | `/api/cameras/start_all` | login | Запустить все потоки |
| POST | `/api/cameras/stop_all` | login | Остановить все потоки |
| GET | `/api/cameras/stream/<int:cam_id>` | login | MJPEG-поток камеры |
| GET | `/api/transmission` | admin | Настройки Transmission |
| POST | `/api/transmission` | admin | Настройки Transmission |
| GET | `/api/transmission/torrents` | login | Список торрентов |
| POST | `/api/transmission/add` | login | Добавить торрент |
| POST | `/api/transmission/action` | login | Действие (start/stop/remove) |
| POST | `/api/transmission/queue` | login | Очередь загрузок |
| GET | `/apps/downloads` | login | Загрузки (страница) |
| GET | `/api/dlna/scan` | login | Скан DLNA-серверов |
| GET | `/api/dlna/content` | login | Контент DLNA |
| GET | `/api/dlna/file_url` | login | URL файла DLNA |
| GET | `/api/upnp/renderers` | login | UPnP-рендереры |
| GET | `/api/upnp/servers` | login | UPnP-серверы |
| POST | `/api/upnp/play` | login | UPnP play |
| POST | `/api/upnp/pause` | login | UPnP pause |
| POST | `/api/upnp/stop` | login | UPnP stop |
| POST | `/api/upnp/next` | login | UPnP next |
| POST | `/api/upnp/prev` | login | UPnP prev |
| POST | `/api/upnp/volume` | login | UPnP громкость |
| POST | `/api/upnp/mute` | login | UPnP mute |
| GET | `/api/upnp/status` | login | Статус UPnP-плеера |
| GET | `/api/upnp/browse` | login | Обзор UPnP-сервера |
| GET | `/apps/dlna` | login | DLNA (страница) |
| GET | `/apps/upnp` | login | UPnP (страница) |

## Погода (`modules/weather_routes.py`) — 2

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/weather` | login | Погода (страница) |
| GET | `/api/weather-status` | login | Статус погоды/таймеров (JSON) |

## Мониторинг (`modules/monitoring_routes.py`) — 2

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/monitoring` | login | Мониторинг (страница) |
| GET | `/api/monitoring/<ip>` | login | Метрики Netdata хоста |

## Инвентаризация (`modules/inventory_routes.py`) — 4

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/inventory` | login | Инвентаризация (страница) |
| POST | `/inventory/scan` | login | Скан для инвентаризации |
| GET | `/inventory/device/<ip>` | login | Redirect → `/device/<ip>` (мертвая заглушка удалена в 1.1) |
| GET | `/api/inventory/<ip>` | login | Данные инвентаризации (JSON) |

## Сканер открытых портов (`modules/portscan_routes.py`) — 8

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/portscan` | login | Страница сканера (диапазон IP + порты) |
| POST | `/portscan/start` | edit | Запуск скана (range, ports, timeout; confirm > 500k комбинаций) |
| POST | `/portscan/stop` | edit | Остановка текущего скана |
| POST | `/portscan/delete` | edit | Удалить файл результата |
| GET | `/portscan/file/<name>` | login | Просмотр файла результата |
| GET | `/portscan/download/<name>` | login | Скачать файл результата |
| GET | `/api/portscan/status` | login | Прогресс скана (JSON) |
| GET | `/api/portscan/files` | login | Список файлов результатов (JSON) |

Файлы пишутся только на смонтированный HDD/SSD (`/srv/lan-discovery/portscan`
либо `portscan.results_dir` в settings.json); без диска старт отклоняется
и на X96 (только eMMC) страница показывает предупреждение.

## Диагностика сети (`modules/diagnose_routes.py`) — 4

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/diagnose` | login | Страница «Что сломано?» (вердикт + шаги) |
| POST | `/diagnose/run` | edit | Запуск прогона (форма; speed=0 — без теста скорости) |
| POST | `/api/diagnose/run` | edit | Запуск прогона (JSON `{speed: bool}`) |
| GET | `/api/diagnose/status` | login | Прогресс/результаты шагов (JSON) |

Шаги: LAN (адрес/шлюз/DNS/DHCP-источник) → ping шлюза → DNS-резолв →
HTTP-пробы интернета → ping WAN (потери/задержки) → download-тест скорости.
Один прогон за раз (singleton), результаты не сохраняются.

## Hardware-инструменты (`modules/flipper_routes.py`) — 17

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/flipper` | login | Страница: детект + пейджер + serial + программаторы |
| GET | `/api/flipper/detect` | login | Инструменты/железо/порты (JSON) |
| POST | `/api/flipper/install` | admin | Установка пакета (whitelist apt/pip, async) |
| GET | `/api/flipper/install/status` | login | Статус/лог установки |
| POST | `/api/flipper/pager/start`, `/stop` | edit | rtl_433: старт/стоп live-ленты 433 МГц |
| GET | `/api/flipper/pager/status`, `/messages` | login | Статус и сообщения (JSON, `after=<n>`) |
| POST | `/api/flipper/serial/open`, `/close`, `/write` | edit | Монитор UART (pyserial); консольные порты запрещены |
| GET | `/api/flipper/serial/status`, `/read` | login | Порты/статус/лог (JSON) |
| POST | `/api/flipper/prog/upload` | edit | Загрузка файла прошивки (`/tmp/flipper/`, ≤32 МБ) |
| POST | `/api/flipper/prog/avrdude`, `/esptool`, `/stm32flash` | edit | Запуск программатора (whitelist-поля) |
| GET | `/api/flipper/prog/status`, `/log` | login | Статус/лог программатора |

Команды программаторов собираются только из whitelist-полей (программисты/
MCU/чипы — списки, порты — из детекта ttyUSB/ttyACM); произвольные аргументы
не передаются. Установка пакетов — только admin.

## Дом/сигнализация (`modules/home_routes.py`) — 12

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/home` | login | Страница: охрана, зоны, сирена, MQTT |
| GET | `/api/home/status` | login | Состояние: armed/triggered, зоны, temp, MQTT (JSON) |
| POST | `/api/home/arm`, `/api/home/disarm` | edit | Поставить/снять охрану |
| POST | `/api/home/siren` | edit | Ручной вкл/выкл сирены (тест) |
| POST | `/api/home/zones` | edit | Добавить GPIO-зону (name, chip, line, trigger_level) |
| DELETE | `/api/home/zones/<id>` | edit | Удалить зону |
| POST | `/api/home/zones/<id>/test` | login | Текущее значение GPIO зоны |
| POST | `/api/home/siren_zone` | edit | Настроить/убрать сирену (зона `__siren__`) |
| POST | `/api/home/mqtt` | edit | Настройки MQTT (host, port, prefix, enabled) |
| POST | `/api/home/mqtt/test` | edit | Тестовая публикация |
| POST | `/api/home/install` | admin | Установка mosquitto-clients (whitelist) |

Охрана: опрос зон 2 с (GPIO через gpioset/gpioget, валидация chip/line);
тревога → сирена + `home.alarm_triggered` (critical) + MQTT retained
`<prefix>/state`, события `<prefix>/event`. Тревога снимается только
вручную. На боксах без gpiochip — зоны скрыты, arm/disarm работают.

## Спасательный AP (`modules/rescue_routes.py`) — 7

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/rescue` | login | Страница спасательного Wi-Fi AP |
| GET | `/api/rescue/status` | login | Интерфейсы/аплинк/hostapd/dnsmasq/конфиг |
| POST | `/api/rescue/config` | edit | Сохранить SSID/WPA2/канал/подсеть (валидация: SSID ≤32, пароль 8–63, канал 1–16, подсеть /24) |
| POST | `/api/rescue/start` | edit | Поднять AP (dnsmasq+hostapd, IP интерфейсу); отказ, если iface = default route |
| POST | `/api/rescue/stop` | edit | Остановить AP, снять IP |
| POST | `/api/rescue/install` | admin | Установить hostapd/dnsmasq (whitelist apt, async+лог) |
| GET | `/api/rescue/install/status` | login | Статус/лог установки |

---

## Менеджер модулей (`modules/module_manager.py`) — 11

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/modules` | admin | Админка модулей (единый semantic-бейдж статуса, версия/source/permissions/hardware из module.json — 1.1) |
| POST | `/modules/catalog/refresh` | admin | Обновить каталог из GitHub |
| POST | `/modules/<mid>/catalog/install` | admin | Установить модуль из каталога |
| POST | `/modules/<mid>/catalog/update` | admin | Обновить модуль |
| POST | `/modules/<mid>/catalog/remove` | admin | Удалить модуль из каталога |
| POST | `/modules/<mid>/toggle` | admin | Вкл/выкл модуль |
| POST | `/modules/<mid>/install` | admin | Установить зависимости модуля |
| GET | `/roles` | admin | Страница «Роли»: профили default/media/network + compat-check (1.1, STEP 9) |
| POST | `/roles/<rid>/apply` | admin | Применить профиль: вкл/выкл модулей, пропуск несовместимых |
| GET | `/api/roles` | login | Профили + статусы модулей для роли (JSON, 1.1) |
| POST | `/api/roles/<rid>/apply` | admin | Применить профиль (JSON-вариант) |

---

## Внешний вид (темы/фон/акцент)

Без отдельных роутов — блок **«Внешний вид»** на вкладке «Система»
(модуль `sys-appearance`, слот `system`):

- **Персонально** (каждый браузер): выбор темы/фона/акцента сохраняется
  в `localStorage`-ключ `ld_ui` (JSON: `theme`, `bg`, `accent`) и
  применяется мгновенно. Тема `auto` следует за `prefers-color-scheme`.
  Ранний скрипт в `<head>` (`templates/base.html`) применяет выбор до
  отрисовки — без «мигания» стартовой темы.
- **Глобальный дефолт** (для всех): у администратора в блоке есть кнопка
  «По умолчанию для всех» → `POST /api/settings` с секцией `ui`
  (`{"ui": {"theme": "dark", "bg": "mono", "accent": "blue"}}`).
  Приоритет: localStorage → `ui` из settings → дефолт `dark/mono/blue`.
- Реализация: `data-theme` / `data-bg` / `data-accent` на `<html>`;
  блоки тем в `static/style.css` (секция 8): `light` (светлая тема),
  фоны `mono/aurora/sunset/forest`, акценты
  `blue/violet/green/orange/rose/teal`.

---

Итого таблиц: 2, 7, 30, 29, 21, 64, 2, 2, 4, 8, 4, 17, 12, 7, 11 = 218

Роуты без `def`-декоратора `login_required`/`admin_required`
помечены `открытый` — их белый список: `/login`, `/logout`,
`/api/health`. Новые роуты: обязательно ставить `@login_required`
(или `@admin_required` для опасных операций) и проверять CSRF.
