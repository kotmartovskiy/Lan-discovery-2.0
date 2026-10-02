# -*- coding: utf-8 -*-
"""Jobs subsystem (PHASE 2.0-3, спека §5, Архитектура §3.3/§5).

Долгая операция ≠ HTTP request: всё длинное (apt/pip install, nmap-скан,
backup) исполняется в фоне, HTTP сразу возвращает id задачи.

Модель:
- состояния queued → running → completed | failed | cancelled;
- executor: пул worker-тредов (WORKERS) + очередь (queue.Queue), ленивый
  старт при первом submit;
- поля: id/type/meta/status/progress/queued_at/started_at/finished_at/
  logs/result/error/cancelable (Архитектура §5);
- хранение: sqlite-таблица `jobs` в devices.db; DDL — JOBS_DDL,
  ensure_jobs_table() идемпотентен (его зовёт и
  modules/devices_routes._ensure_extra_tables);
- retention: cleanup_old_jobs(con, days) — по образцу cleanup_old_events
  (настройка events.retention_days), плюс ежесуточный retention_loop();
- отмена: cooperative — задача сама зовёт ctx.check_cancel() между шагами
  (nmap/apt прервать изнутри нельзя — cancelable=False честнее);
- восстановление: recover_interrupted(con) помечает queued/running,
  оборванные рестартом панели, failed.

Публичный API (модульный, обёртки над глобальным manager):
    init / submit / get / list / cancel / wait
"""
import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta

log = logging.getLogger("lan-discovery")

STATUSES = ("queued", "running", "completed", "failed", "cancelled")
TERMINAL = frozenset(("completed", "failed", "cancelled"))
WORKERS = 2
MAX_LOG_LINES = 500

JOBS_DDL = (
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        meta TEXT,
        status TEXT NOT NULL,
        progress INTEGER,
        queued_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        logs TEXT,
        result TEXT,
        error TEXT,
        cancelable INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
)

_COLUMNS = (
    "id, type, meta, status, progress, queued_at, started_at, "
    "finished_at, logs, result, error, cancelable"
)


def now_ts():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


# --- DDL / retention / recovery (зовутся и из devices_routes) ---------------

def ensure_jobs_table(con):
    """Идемпотентный CREATE таблицы jobs (без commit — как DDL рядом)."""
    for stmt in JOBS_DDL:
        con.execute(stmt)


def recover_interrupted(con):
    """queued/running, оборванные рестартом панели → failed. Коммитит сама."""
    cur = con.execute(
        "UPDATE jobs SET status='failed', error=?, finished_at=? "
        "WHERE status IN ('queued', 'running')",
        ("прервано перезапуском панели", now_ts()),
    )
    if cur.rowcount:
        con.commit()
        log.info("JOBS RECOVER: %d interrupted jobs marked failed",
                 cur.rowcount)
    return cur.rowcount


def cleanup_old_jobs(con, days):
    """Удалить задачи старше days дней (retention как у events).

    queued_at в формате DD.MM.YYYY HH:MM:SS — парсится в Python, удаление
    батчами по id. days <= 0 → no-op. Коммитит сама.
    """
    if not days or int(days) <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=int(days))

    stale = []
    for row in con.execute("SELECT id, queued_at FROM jobs"):
        try:
            ts = datetime.strptime(row[1], "%d.%m.%Y %H:%M:%S")
        except Exception:
            continue
        if ts < cutoff:
            stale.append(row[0])

    removed = 0
    for i in range(0, len(stale), 500):
        batch = stale[i:i + 500]
        cur = con.execute(
            "DELETE FROM jobs WHERE id IN (%s)"
            % ",".join("?" * len(batch)),
            batch,
        )
        removed += cur.rowcount
    if removed:
        con.commit()
        log.info(f"JOBS RETENTION: removed {removed} jobs "
                 f"older than {days} days")
    return removed


# --- job-объекты ------------------------------------------------------------

class JobCancelled(Exception):
    """Задача отменена (ctx.check_cancel())."""


class Job:
    """Одна задача; атрибуты — поля из ROADMAP 2.0-3 + meta (Архитектура §5).

    _fn/_manager — только для живых задач из менеджера (в dict/строку БД
    не сериализуются).
    """

    def __init__(self, jid, job_type, meta=None, cancelable=False,
                 queued_at=None, manager=None):
        self.id = jid
        self.type = job_type
        self.meta = meta or None
        self.status = "queued"
        self.progress = None
        self.queued_at = queued_at or now_ts()
        self.started_at = None
        self.finished_at = None
        self.logs = []
        self.result = None
        self.error = None
        self.cancelable = bool(cancelable)
        self._fn = None
        self._manager = manager

    def add_log(self, msg):
        self.logs.append(str(msg))
        if len(self.logs) > MAX_LOG_LINES:
            del self.logs[:len(self.logs) - MAX_LOG_LINES]
        if self._manager:
            self._manager._persist(self)

    def set_progress(self, pct):
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            return
        self.progress = max(0, min(100, pct))
        if self._manager:
            self._manager._persist(self)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "meta": self.meta,
            "status": self.status,
            "progress": self.progress,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "logs": self.logs[:],
            "result": self.result,
            "error": self.error,
            "cancelable": self.cancelable,
        }

    def _row(self):
        return (
            self.id,
            self.type,
            json.dumps(self.meta, ensure_ascii=False, default=str)
            if self.meta is not None else None,
            self.status,
            self.progress,
            self.queued_at,
            self.started_at,
            self.finished_at,
            json.dumps(self.logs, ensure_ascii=False, default=str),
            json.dumps(self.result, ensure_ascii=False, default=str)
            if self.result is not None else None,
            self.error,
            int(self.cancelable),
        )


def _parse_json(value, fallback):
    if value is None or value == "":
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _row_to_job(row):
    j = Job(row[0], row[1], queued_at=row[5])
    j.meta = _parse_json(row[2], None)
    j.status = row[3]
    j.progress = row[4]
    j.started_at = row[6]
    j.finished_at = row[7]
    j.logs = _parse_json(row[8], [])
    j.result = _parse_json(row[9], None)
    j.error = row[10]
    j.cancelable = bool(row[11])
    return j


class JobContext:
    """Контекст исполняемой задачи: лог, прогресс, проверка отмены."""

    def __init__(self, job, cancel_event):
        self._job = job
        self._event = cancel_event

    def log(self, msg):
        self._job.add_log(msg)

    def progress(self, pct):
        self._job.set_progress(pct)

    def cancelled(self):
        return bool(self._event and self._event.is_set())

    def check_cancel(self):
        if self.cancelled():
            raise JobCancelled()


class JobManager:
    """Пул worker-тредов + очередь + sqlite-персист."""

    def __init__(self, db_path=None, workers=WORKERS):
        self._db_path_cfg = db_path
        self._workers_n = workers
        self._lock = threading.RLock()
        self._jobs = {}
        self._events = {}
        self._queue = queue.Queue()
        self._retention_cfg = None
        self._init_done = False
        self._workers_started = False
        self._last_cleanup = 0.0

    # --- конфигуция / схема -------------------------------------------------

    def configure(self, db_path=None, retention_days=None):
        """Только значения, без I/O (безопасно на import app)."""
        with self._lock:
            if db_path:
                self._db_path_cfg = db_path
            if retention_days is not None:
                self._retention_cfg = retention_days

    def _db_path(self):
        if self._db_path_cfg:
            return self._db_path_cfg
        from modules.devices_routes import DB
        return DB

    def _connect(self):
        import sqlite3
        con = sqlite3.connect(self._db_path(), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def ensure_schema(self):
        """DDL + recover прерванных — один раз на процесс (лениво, I/O)."""
        with self._lock:
            if self._init_done:
                return
            con = self._connect()
            try:
                ensure_jobs_table(con)
                recover_interrupted(con)
                con.commit()
                self._init_done = True
            finally:
                con.close()

    # --- persistence (best-effort: память авторитетна, БД — для истории) ---

    def _persist(self, job):
        try:
            con = self._connect()
            try:
                cur = con.execute(
                    "UPDATE jobs SET type=?, meta=?, status=?, progress=?, "
                    "queued_at=?, started_at=?, finished_at=?, logs=?, "
                    "result=?, error=?, cancelable=? WHERE id=?",
                    job._row()[1:] + (job.id,),
                )
                if cur.rowcount == 0:
                    con.execute(
                        "INSERT INTO jobs (%s) VALUES (%s)"
                        % (_COLUMNS, ",".join("?" * 12)),
                        job._row(),
                    )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            log.error("JOBS persist %s: %s", job.id, e)

    def _resolve_retention(self):
        if self._retention_cfg is not None:
            return int(self._retention_cfg)
        try:
            from app import _cfg
            return int(_cfg("events", "retention_days", 180) or 0)
        except Exception:
            return 180

    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < 86400:
            return
        self._last_cleanup = now
        days = self._resolve_retention()
        if not days:
            return

        def _run():
            try:
                con = self._connect()
                try:
                    cleanup_old_jobs(con, days)
                finally:
                    con.close()
            except Exception as e:
                log.error("JOBS RETENTION ERROR: %s", e)

        threading.Thread(target=_run, daemon=True,
                         name="lan-jobs-cleanup").start()

    # --- submit / workers ---------------------------------------------------

    def submit(self, job_type, fn, *, cancelable=False, meta=None):
        self.ensure_schema()
        jid = uuid.uuid4().hex[:12]
        job = Job(jid, job_type, meta=meta, cancelable=cancelable,
                  manager=self)
        job._fn = fn
        with self._lock:
            self._jobs[jid] = job
            self._events[jid] = threading.Event()
        self._persist(job)
        self._ensure_workers()
        self._queue.put(jid)
        self._maybe_cleanup()
        return jid

    def _ensure_workers(self):
        with self._lock:
            if self._workers_started or self._workers_n <= 0:
                return
            self._workers_started = True
            for i in range(self._workers_n):
                threading.Thread(
                    target=self._worker,
                    name="lan-jobs-%d" % (i + 1),
                    daemon=True,
                ).start()

    def _worker(self):
        while True:
            jid = self._queue.get()
            try:
                self._run_one(jid)
            except Exception as e:
                log.error("JOBS worker error %s: %s", jid, e)
            finally:
                self._queue.task_done()

    def _run_one(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
            if job is None or job.status != "queued":
                # отменена до старта либо чужой остаток очереди
                ev = self._events.pop(jid, None)
                if ev:
                    ev.set()
                return
            job.status = "running"
            job.started_at = now_ts()
            event = self._events.get(jid)
        self._persist(job)

        ctx = JobContext(job, event)
        try:
            result = job._fn(ctx)
        except JobCancelled:
            with self._lock:
                job.status = "cancelled"
        except Exception as e:
            with self._lock:
                job.status = "failed"
                job.error = str(e) or e.__class__.__name__
                job.logs.append("ERROR: " + job.error)
                if len(job.logs) > MAX_LOG_LINES:
                    del job.logs[:len(job.logs) - MAX_LOG_LINES]
        else:
            with self._lock:
                job.status = "completed"
                job.progress = 100
                job.result = result

        with self._lock:
            job.finished_at = now_ts()
            ev = self._events.pop(job.id, None)
        # persist до ev.set(): wait() возвращает управление только когда
        # строка уже в sqlite (read-after-wait не видит status=running)
        self._persist(job)
        if ev:
            ev.set()

    # --- отмена -------------------------------------------------------------

    def cancel(self, jid):
        """None — задача не найдена; иначе dict {ok, status, ...}."""
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return None
            if job.status in TERMINAL:
                return {"ok": False, "status": job.status,
                        "error": "задача уже завершена"}
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = now_ts()
                ev = self._events.get(jid)
                self._persist(job)
                if ev:
                    ev.set()
                return {"ok": True, "status": "cancelled"}
            if not job.cancelable:
                return {"ok": False, "status": job.status,
                        "error": "задачу нельзя отменить"}
            ev = self._events.get(jid)
            if ev:
                ev.set()
            return {"ok": True, "status": job.status, "cancelling": True}

    # --- чтение -------------------------------------------------------------

    def get(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
            if job is not None:
                return job.to_dict()
        try:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT %s FROM jobs WHERE id=?" % _COLUMNS,
                    (jid,),
                ).fetchone()
            finally:
                con.close()
            return _row_to_job(row).to_dict() if row else None
        except Exception as e:
            log.error("JOBS get %s: %s", jid, e)
            return None

    def list(self, status=None, limit=50):
        """Последние задачи: строки БД (история) + живые из памяти свежее."""
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT %s FROM jobs ORDER BY rowid DESC LIMIT ?"
                    % _COLUMNS,
                    (max(1, int(limit)),),
                ).fetchall()
            finally:
                con.close()
        except Exception as e:
            log.error("JOBS list: %s", e)
            rows = ()

        items = {}
        order = []
        for row in rows:
            job = _row_to_job(row)
            items[job.id] = job.to_dict()
            order.append(job.id)

        with self._lock:
            mem_only = []
            for jid, job in self._jobs.items():
                if jid not in items:
                    mem_only.append(jid)
                items[jid] = job.to_dict()
            # память поверх БД по тем же id; только-в-памяти — новые сверху
            order = mem_only + order

        out = [items[jid] for jid in order]
        if status:
            out = [j for j in out if j["status"] == status]
        return out[:limit]

    def wait(self, jid, timeout=10.0, interval=0.02):
        """Дождаться терминального статуса; None — таймаут/не найдено."""
        deadline = time.time() + timeout
        while True:
            with self._lock:
                job = self._jobs.get(jid)
                if job is None:
                    return None
                if job.status in TERMINAL:
                    return job.status
                if time.time() >= deadline:
                    return None
            time.sleep(interval)


# --- module-level API (Архитектура §5) --------------------------------------

manager = JobManager()


def init(db_path=None, retention_days=None):
    """configure + ensure_schema; I/O только здесь (после configure — лениво)."""
    manager.configure(db_path=db_path, retention_days=retention_days)
    manager.ensure_schema()
    return True


def submit(job_type, fn, *, cancelable=False, meta=None):
    return manager.submit(job_type, fn, cancelable=cancelable, meta=meta)


def get(job_id):
    return manager.get(job_id)


def list(status=None, limit=50):
    return manager.list(status=status, limit=limit)


def cancel(job_id):
    return manager.cancel(job_id)


def wait(job_id, timeout=10.0):
    return manager.wait(job_id, timeout=timeout)


def retention_loop(interval=86400):
    """Ежедневная чистка jobs по events.retention_days (как у events)."""
    while True:
        time.sleep(interval)
        try:
            days = manager._resolve_retention()
            if not days:
                continue
            con = manager._connect()
            try:
                cleanup_old_jobs(con, days)
            finally:
                con.close()
        except Exception as e:
            log.error(f"JOBS RETENTION ERROR: {e}")
