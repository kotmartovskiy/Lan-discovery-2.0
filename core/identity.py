# -*- coding: utf-8 -*-
"""Device identity (PHASE 2.0-11, спека §15): device_id → MAC → hostname → IP history.

Правила (v1, аддитивно к существующей модели):

- ``derive_id(mac, ip)`` — производный идентификатор: ``mac:<lower(mac)>``
  при известном MAC, иначе ``ip:<ip>`` (DHCP-хосты без ARP-MAC —
  консервативный fallback).
- ``device_id`` присваивается один раз (COALESCE) и **не переписывается**
  при смене MAC — device_id первичен, MAC-смена остаётся событием
  MAC_CHANGED (event-модель не ломается).
- Переезд (один MAC на другой IP) наследует device_id прежней строки —
  реализовано в core.discovery.reconcile (существующая identity-P5).
- ``ip_history`` — цепочка IP устройства: первое появление пары
  (device_id, ip) + обновление last_seen при каждом подтверждении.

Читающий API: ``identity_of(con, ip)`` — для роута
``GET /api/device/<ip>/identity`` (modules/devices_routes).
"""


def derive_id(mac, ip):
    """Стабильный device_id: MAC-производный, fallback на IP."""
    mac = (mac or "").strip()
    if mac:
        return "mac:" + mac.lower()
    return "ip:" + str(ip)


def record_ip(con, device_id, ip, ts):
    """Зафиксировать пару (device_id, ip): INSERT при первом появлении,
    иначе обновить last_seen. Возвращает True, если пара создана."""
    row = con.execute(
        "SELECT id FROM ip_history WHERE device_id=? AND ip=?",
        (device_id, ip),
    ).fetchone()
    if row:
        con.execute(
            "UPDATE ip_history SET last_seen=? WHERE id=?", (ts, row[0])
        )
        return False
    con.execute(
        "INSERT INTO ip_history (device_id, ip, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        (device_id, ip, ts, ts),
    )
    return True


def identity_of(con, ip):
    """Полная идентичность устройства по IP или None (нет строки).

    {"device_id", "ip", "mac", "hostname", "ip_history": [{ip, first_seen,
     last_seen}...]}
    """
    row = con.execute(
        "SELECT device_id, mac, hostname FROM devices WHERE ip=?",
        (ip,),
    ).fetchone()
    if row is None:
        return None
    device_id, mac, hostname = row
    if not device_id:
        device_id = derive_id(mac, ip)
    history = [
        {"ip": r[0], "first_seen": r[1], "last_seen": r[2]}
        for r in con.execute(
            "SELECT ip, first_seen, last_seen FROM ip_history "
            "WHERE device_id=? ORDER BY first_seen, id",
            (device_id,),
        ).fetchall()
    ]
    return {
        "device_id": device_id,
        "ip": ip,
        "mac": mac,
        "hostname": hostname,
        "ip_history": history,
    }
