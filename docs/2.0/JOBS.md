# JOBS — долгие операции (спека §29, фаза 2.0-3)

Jobs — подсистема ядра для всего, что выполняется дольше HTTP-запроса.
Цель: долгие операции **безопаснее** (§36) — изолированный поток,
статус, лог, отмена, событие, rollback при сбое.

## Контракт

```python
core.jobs.submit(type, fn, *, cancelable=False, meta=None) -> job_id
core.jobs.get(job_id) -> {id, type, status, progress, started_at,
                          finished_at, logs, result, error, cancelable}
core.jobs.list(status=None) -> [job]     # новые сверху
core.jobs.cancel(job_id) -> ok|error
```

* статусы: `running` → `done` / `error` / `canceled`;
* `wait()` возвращает терминальный статус только **после**
  финализации persist+событие (read-after-wait — тест гарантирует);
* `progress`/`logs` — для UI (dashboard «что происходит», §22).

## События (core/events)

```text
job.started / job.completed / job.failed / job.cancelled
  + metadata: job_id, job_type
```

Подписчики (automation, лента событий) получают их через
`events.subscribe`.

## Кто использует

| Операция | Тип job |
|---|---|
| установка/обновление модуля (apt, manifest trust) | module install |
| бэкапы (файлы панели, БД) | backup |
| сканы сети (длительные) | scan |
| системные операции (`core/syschange`: apply/rollback) | system |
| клонирование SD ↔ eMMC | clone |

Все перечисленные — через `submit`; ручной `POST /api/scan` тоже
создаёт job и возвращается после постановки в очередь.

## HTTP

```text
GET  /api/jobs        -> {ok, jobs: [...], active}
GET  /api/jobs/<id>   -> {ok, job}
POST .../cancel       -> отмена (если cancelable)
```

Формат аддитивен (§32). UI: dashboard и страницы читают `active` и
показывают прогресс компонентами §23.

## Правила

* модуль не должен запускать долгие `subprocess` в роуте — только
  `jobs.submit`;
* `fn` не пишет в чужие таблицы мимо ядра (схема БД — `core.db`);
* сбой `fn` → статус `error` + событие `job.failed`, частичные
  эффекты откатывает сама операция (см. system rollback §26);
* в тестах job-менеджер патчится на фейковый (изоляция
  `test_appliance_smoke`).
