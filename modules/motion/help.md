# Детекция движения и оповещения

Сценарий: камеры работают постоянно → обнаружено движение → фото сохранено
локально → событие → локальная тревога → уведомление в Telegram/e-mail.
Интернет нужен только для доставки; без него всё работает локально, а фото
уходят автоматически после восстановления связи.

## Детекторы (варианты на выбор, per-камера)

- **snapshot** (по умолчанию) — периодический снимок (ffmpeg из RTSP или
  HTTP-snapshot URL) + пиксельное сравнение с предыдущим кадром. Одна
  декодировка кадра раз в несколько секунд, потоки постоянно не декодируются.
- **onvif** — подписка на ONVIF-события камеры (собственная детекция камеры,
  почти ноль нагрузки). Требует пару ONVIF-учётных данных и установленного
  `onvif-zeep-async`; при ошибке автоматический fallback на snapshot.
- **hook** — камера сама дёргает URL при детекции (GET/POST
  `/api/motion/hook/<token>?cam=<id>`); токен — `motion.hook_token`.
- **off** — детекция выключена (по умолчанию для камеры).

Глобальный выключатель — `motion.enabled` (секция `motion` в
`/etc/lan-discovery/settings.json`, редактируется через `/api/settings`).

## Защита от флуда

- cooldown на камеру (`motion.cooldown_sec`, по умолчанию 180 с) — повторные
  срабатывания в окне не создают новых событий;
- ручная пауза тревоги: `POST /api/motion/silence {"minutes": N}`.

## Очередь уведомлений (работает офлайн)

События и фото пишутся локально сразу. Отправка — в фоне, когда интернет
доступен; при обрывах/низкой скорости — экспоненциальный backoff (30 с → 1 ч),
фото сжимается до `motion.max_photo_kb` (по умолчанию 300 КБ). Накопленные
фото уходят сами после восстановления связи. TTL очереди —
`motion.queue_ttl_days`.

Каналы (`motion.channels`):
- **telegram** — bot_token + chat_id (sendPhoto/sendMessage);
- **email** — SMTP (STARTTLS/SSL), письмо с фото-вложением.

Тест канала: `POST /api/motion/notify/test {"channel": "telegram"}` (admin).

## Хранение

- фото: `/srv/media/motion/<ГГГГ-ММ-ДД>/<cam>_<время>.jpg`;
- события: таблица `motion_events` (devices.db) + запись в общую «Историю»
  (source=`motion`, severity=`warning`);
- чистка по `motion.retention_days` (по умолчанию 30 дней).

## API

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/api/motion/status` | login |
| GET | `/api/motion/events?limit=&camera=` | login |
| GET | `/api/motion/photo/<id>` | login |
| GET/POST | `/api/motion/hook/<token>?cam=<id>` | токен |
| POST | `/api/motion/notify/test` | admin |
| POST | `/api/motion/silence` | admin |

## Локальная тревога

`motion.alarm`: `enabled`, `mode` (`none`/`beep` — проигрывание
`/srv/media/motion/alarm.wav` через aplay, для USB-бипера позже),
`cooldown_sec`. Логика не зависит от интернета. Плюс событие `motion_alarm`
через SocketIO для будущего UI.
