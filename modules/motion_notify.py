# -*- coding: utf-8 -*-
"""Очередь уведомлений motion: Telegram/e-mail, backoff, доставка после
восстановления связи (фаза «События и оповещения»).

Гарантии:
- событие и фото пишутся локально немедленно (см. motion_engine);
- отправка фоновым worker'ом только при доступном интернете;
- недоступный/медленный канал → экспоненциальный backoff (30 с … 1 ч);
- фото предварительно сжимается до motion.max_photo_kb;
- накопленные фото уходят автоматически после восстановления связи;
- старше motion.queue_ttl_days → status='dead' (локальные события остаются).
"""
import logging
import os
import time

log = logging.getLogger("lan-discovery")

_STARTED = False
_net = {"ok": None, "down_since": None, "last_down": 0.0, "last_up": 0.0}


def backoff(attempts):
    """Экспоненциальный backoff: 30с, 60с, ... с потолком 1 часа."""
    attempts = max(1, int(attempts))
    return min(3600, 30 * (2 ** max(0, min(attempts - 1, 20))))


def _deep_cfg():
    from modules.motion_engine import cfg
    return cfg()


def queue_event(event_id, photo, caption, cfg_override=None):
    """Поставить уведомления по событию во все включённые каналы."""
    from modules.devices_routes import get_db
    c = cfg_override or _deep_cfg()
    channels = (c.get("channels") or {})
    enabled = [name for name, ch in channels.items()
               if isinstance(ch, dict) and ch.get("enabled")]
    if not enabled:
        return 0
    now = int(time.time())
    con = get_db()
    try:
        for name in enabled:
            con.execute(
                "INSERT INTO motion_queue "
                "(event_id, channel, created_epoch, attempts, next_epoch, status) "
                "VALUES (?,?,?,?,?, 'pending')",
                (int(event_id), name, now, 0, now),
            )
        con.commit()
        return len(enabled)
    finally:
        con.close()


def _shrink(path, max_kb):
    """Сжать JPEG до max_kb (если больше); возвращает путь или None."""
    if not path or not os.path.isfile(path) or max_kb <= 0:
        return None
    limit = max_kb * 1024
    if os.path.getsize(path) <= limit:
        return None
    try:
        from PIL import Image
        out = path + ".sm.jpg"
        im = Image.open(path)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        quality = 85
        while True:
            im.save(out, "JPEG", quality=quality, optimize=True)
            if os.path.getsize(out) <= limit:
                return out
            if quality > 25:
                quality -= 15
                continue
            # при предельном качестве не влезло — уменьшаем разрешение
            w, h = im.size
            if w <= 320:
                return out
            im = im.resize((int(w * 0.7), int(h * 0.7)), Image.LANCZOS)
            quality = 40
    except Exception as e:
        log.warning(f"MOTION PHOTO SHRINK ERROR: {e}")
        return None


def send_telegram(ch, photo, caption):
    token = (ch.get("bot_token") or "").strip()
    chat = (ch.get("chat_id") or "").strip()
    if not token or not chat:
        raise RuntimeError("telegram: не заданы bot_token/chat_id")
    import requests
    api = f"https://api.telegram.org/bot{token}/"
    if photo and os.path.isfile(photo):
        with open(photo, "rb") as f:
            r = requests.post(
                api + "sendPhoto",
                data={"chat_id": chat, "caption": caption[:1024]},
                files={"photo": f},
                timeout=(10, 60),
            )
    else:
        r = requests.post(api + "sendMessage",
                          data={"chat_id": chat, "text": caption[:4096]},
                          timeout=(10, 60))
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"telegram: HTTP {r.status_code}")
    if not data.get("ok"):
        raise RuntimeError(f"telegram: {data.get('description') or r.status_code}")


def send_email(ch, photo, caption):
    host = (ch.get("host") or "").strip()
    if not host:
        raise RuntimeError("email: не задан SMTP-хост")
    port = int(ch.get("port", 587) or 587)
    to_addrs = [t.strip() for t in
                (ch.get("to_addr") or "").replace(";", ",").split(",") if t.strip()]
    if not to_addrs:
        raise RuntimeError("email: не задан получатель (to_addr)")

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart()
    msg["From"] = ch.get("from_addr") or ch.get("user") or ""
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = f"LAN Discovery: {caption[:100]}"
    msg.attach(MIMEText(caption, "plain", "utf-8"))
    if photo and os.path.isfile(photo):
        from email.mime.image import MIMEImage
        with open(photo, "rb") as f:
            img = MIMEImage(f.read(), name=os.path.basename(photo))
        img.add_header("Content-Disposition", "attachment",
                       filename=os.path.basename(photo))
        msg.attach(img)

    import smtplib
    use_ssl = not ch.get("starttls", True)
    if use_ssl:
        s = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
    try:
        if not use_ssl:
            s.ehlo()
            s.starttls()
            s.ehlo()
        if ch.get("user"):
            s.login(ch.get("user"), ch.get("password") or "")
        s.sendmail(msg["From"], to_addrs, msg.as_string())
    finally:
        try:
            s.quit()
        except Exception:
            pass


def _send(channel, ch, photo, caption, c):
    max_kb = int(c.get("max_photo_kb", 300) or 0)
    tmp = _shrink(photo, max_kb)
    target = tmp or photo
    try:
        if channel == "telegram":
            send_telegram(ch, target, caption)
        elif channel == "email":
            send_email(ch, target, caption)
        else:
            raise RuntimeError(f"неизвестный канал {channel}")
    finally:
        if tmp and tmp != photo and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def send_test(channel, ch):
    """Тестовая доставка без фото (проверка токена/SMTP)."""
    caption = "LAN Discovery: тест уведомлений — канал работает."
    if channel == "telegram":
        send_telegram(ch, None, caption)
    elif channel == "email":
        send_email(ch, None, caption)
    else:
        raise RuntimeError(f"неизвестный канал {channel}")


def queue_stats(c=None):
    """Счётчики очереди для статуса."""
    from modules.devices_routes import get_db
    c = c or _deep_cfg()
    out = {"pending": 0, "sent": 0, "dead": 0, "oldest_pending_sec": 0}
    con = get_db()
    try:
        for status, cnt, oldest in con.execute(
                "SELECT status, COUNT(*), MIN(created_epoch) "
                "FROM motion_queue GROUP BY status"):
            if status in ("pending", "sent", "dead"):
                out[status] = cnt
                if status == "pending" and oldest:
                    out["oldest_pending_sec"] = max(0, int(time.time()) - int(oldest))
        return out
    finally:
        con.close()


def process_queue(now=None, cfg_override=None, inet_ok=None):
    """Один проход отправки. Возвращает статистику {sent, errors, dead, ...}."""
    from modules.devices_routes import get_db
    from core.events import add_event
    c = cfg_override or _deep_cfg()
    now = time.time() if now is None else float(now)
    if inet_ok is None:
        from app import check_internet_cached
        inet_ok = check_internet_cached()
    stats = {"sent": 0, "errors": 0, "dead": 0, "pending": 0, "skipped": 0}
    con = get_db()
    try:
        ttl = int(c.get("queue_ttl_days", 60) or 0)
        if ttl > 0:
            cur = con.execute(
                "UPDATE motion_queue SET status='dead', last_error='TTL' "
                "WHERE status='pending' AND created_epoch < ?",
                (int(now) - ttl * 86400,))
            stats["dead"] = cur.rowcount
        con.commit()
        stats["pending"] = con.execute(
            "SELECT COUNT(*) FROM motion_queue WHERE status='pending'"
        ).fetchone()[0]
        if not inet_ok:
            return stats
        rows = con.execute(
            "SELECT q.id, q.attempts, q.channel, m.photo, m.ts, m.camera_name "
            "FROM motion_queue q "
            "LEFT JOIN motion_events m ON m.id = q.event_id "
            "WHERE q.status='pending' AND q.next_epoch <= ? "
            "ORDER BY q.id LIMIT 10",
            (int(now),),
        ).fetchall()
        channels = c.get("channels") or {}
        for qid, attempts, channel, photo, ts, cam_name in rows:
            ch = channels.get(channel) or {}
            if not isinstance(ch, dict) or not ch.get("enabled"):
                stats["skipped"] += 1
                continue
            caption = f"Движение: {cam_name or '?'} {ts or ''}".strip()
            try:
                _send(channel, ch, photo, caption, c)
                con.execute(
                    "UPDATE motion_queue SET status='sent', sent_epoch=?, "
                    "attempts=?, last_error=NULL WHERE id=?",
                    (int(now), attempts + 1, qid))
                con.commit()
                stats["sent"] += 1
            except Exception as e:
                attempts += 1
                nxt = int(now + backoff(attempts))
                con.execute(
                    "UPDATE motion_queue SET attempts=?, next_epoch=?, "
                    "last_error=? WHERE id=?",
                    (attempts, nxt, str(e)[:200], qid))
                con.commit()
                stats["errors"] += 1
                log.warning(f"MOTION NOTIFY ERROR ({channel}, попытка "
                            f"{attempts}): {e}")
        if stats["sent"] or stats["errors"] or stats["dead"]:
            log.info(f"MOTION QUEUE: sent={stats['sent']} "
                     f"errors={stats['errors']} dead={stats['dead']} "
                     f"pending={stats['pending']}")
        return stats
    finally:
        con.close()


def _watch_internet(ok, c):
    """События потери/восстановления интернета (cooldown 1 час)."""
    from modules.devices_routes import get_db
    from core.events import add_event
    now = time.time()
    prev = _net["ok"]
    if prev is None:
        _net["ok"] = bool(ok)
        _net["down_since"] = None if ok else now
        return
    if ok and prev is False:
        _net["ok"] = True
        _net["down_since"] = None
        if now - _net["last_up"] > 3600:
            _net["last_up"] = now
            con = get_db()
            try:
                add_event(con, ip=None, event="MOTION_NET_UP", source="motion",
                          severity="info",
                          metadata={"note": "интернет восстановлен"})
                con.commit()
            finally:
                con.close()
            log.info("MOTION NET: интернет восстановлен")
    elif not ok and prev is True:
        _net["ok"] = False
        _net["down_since"] = now
    elif (not ok and _net["down_since"]
          and now - _net["down_since"] >= 60
          and now - _net["last_down"] > 3600):
        _net["last_down"] = now
        con = get_db()
        try:
            add_event(con, ip=None, event="MOTION_NET_DOWN", source="motion",
                      severity="warning",
                      metadata={"note": "нет интернета — уведомления "
                                        "откладываются в очередь"})
            con.commit()
        finally:
            con.close()
        log.warning("MOTION NET: нет интернета — очередь отложена")


def _worker():
    while True:
        time.sleep(15)
        try:
            import app as _app
            if _app.app.config.get("TESTING"):
                continue
            from modules.motion_engine import cfg
            c = cfg()
            if not c.get("enabled"):
                continue
            from app import check_internet_cached
            ok = check_internet_cached()
            _watch_internet(ok, c)
            if ok:
                process_queue(cfg_override=c, inet_ok=True)
        except Exception as e:
            log.error(f"MOTION QUEUE WORKER ERROR: {e}")


def start():
    global _STARTED
    if _STARTED:
        return
    import threading
    _STARTED = True
    threading.Thread(target=_worker, daemon=True, name="motion-queue").start()
