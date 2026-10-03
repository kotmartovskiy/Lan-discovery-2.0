# NETWORK — сетевое подсистемы ядра (спека §29, фаза 2.0-6)

Две зоны: **core/network** (системные транзакции/диагностика) и
**core/discovery** (обнаружение устройств). Модули (network, monitoring,
iptv…) — потребители контрактов.

## core/network.py

```python
physical_ifaces(base="/sys/class/net") -> [iface]
list_interfaces(base="/sys/class/net") -> [...]
list_addresses() / list_routes()
wifi_scan(ifaces=None, timeout=15) -> [...]
net.transaction(name) -> Transaction
```

### Транзакции (изменение конфигурации сети безопасно)

```python
tx = net.transaction("включить wlan1")
tx.prepare()   # план + снимок текущего состояния (адреса/маршруты)
tx.apply()     # применение (ip/nmcli …)
tx.verify()    # контроль результата
tx.commit()    # или:
tx.rollback()  # восстановление снимка (_addr_restore, страховка)
```

Инвариант: сетевые правки не выполняются «в лоб» — только через
транзакцию с откатом (см. также `core/syschange` §26 для apt/файлов).

### HTTP (modules/network_routes)

```text
GET  /api/network/config     # текущая конфигурация
GET  /api/network/ifaces     # интерфейсы
POST /api/network/config     # применение через net.transaction
GET  /api/network/check      # диагностика (internet/DNS/…)
POST /api/network/check_host # проверка хоста
GET  /api/network/info|address …
```

## core/discovery.py

```python
run_scan(subnet=None, ifaces=None)     # nmap ARP-скан (job)
parse_scan(output) -> [...]
reconcile(con, current_devices, now)   # upsert + device identity
get_scan_status()
```

* фоновый цикл `scan_loop` управляется `network.scan_enabled`
  (одна ведущая панель на подсеть, №59); ручной `POST /api/scan`
  доступен всегда;
* подсеть/интервалы/само-IP — из settings (`network.subnet`,
  `network.scan_interval`, `network.self_ips`);
* `reconcile` присваивает/наследует `device_id` (`core/identity`,
  `mac:<mac>` / `ip:<ip>`) и пишет `ip_history`;
* события: `network.scan.*`, `device.online/offline/new`.

## Границы

| В core | В модуле |
|---|---|
| интерфейсы/адреса/маршруты, транзакции | отображение (страницы, бейджи) |
| скан/реконсиляция/идентичность | сценарные проверки (ping-хосты из UI) |
| конфигурация через транзакции | свои API поверх `net.*`/`discovery.*` |

Роутер/DHCP/DNS/firewall/VPN — задуманы как **независимые модули**
(§33 Phase 3), не как ядро.
