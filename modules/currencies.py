import requests
import sqlite3
import time
from datetime import datetime, timedelta


# Task4: БД — из core.db (единый источник пути, §20/§25)
from core.db import DB
_USD_RUB_CACHE = None
_USD_RUB_TS = 0
_OZ_TO_G = 31.1035


def init_db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute("""
        CREATE TABLE IF NOT EXISTS currency_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            symbol TEXT,
            value REAL,
            unit TEXT,
            change_24h REAL,
            fetched_at TEXT,
            source TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS currency_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            value REAL,
            unit TEXT,
            fetched_at TEXT
        )
    """)

    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rates_cat_name ON currency_rates(category, name)")
    except Exception:
        pass

    con.commit()
    con.close()


def get_usd_rub(force=False):
    global _USD_RUB_CACHE, _USD_RUB_TS

    if not force and _USD_RUB_CACHE and (time.time() - _USD_RUB_TS) < 300:
        return _USD_RUB_CACHE

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=tether"
            "&vs_currencies=rub",
            timeout=10
        )
        if resp.ok:
            data = resp.json()
            rate = data.get("tether", {}).get("rub")
            if rate and rate > 50:
                _USD_RUB_CACHE = float(rate)
                _USD_RUB_TS = time.time()
                return _USD_RUB_CACHE
    except Exception:
        pass

    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=RUB,EUR",
            timeout=10
        )
        if resp.ok:
            data = resp.json()
            rates = data.get("rates", {})
            rub = rates.get("RUB")
            if rub and rub > 50:
                _USD_RUB_CACHE = float(rub)
                _USD_RUB_TS = time.time()
                return _USD_RUB_CACHE
    except Exception:
        pass

    if _USD_RUB_CACHE:
        return _USD_RUB_CACHE

    return 85.0


def fetch_fiat_rates():
    rates = {}
    usd_rub = get_usd_rub()

    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,CNY,JPY",
            timeout=10
        )
        if resp.ok:
            data = resp.json()
            fx = data.get("rates", {})

            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                yresp = requests.get(
                    f"https://api.frankfurter.app/{yesterday}?from=USD&to=RUB,EUR,GBP,CNY,JPY",
                    timeout=10
                )
                ydata = yresp.json().get("rates", {}) if yresp.ok else {}
            except Exception:
                ydata = {}

            y_usd_rub = ydata.get("RUB", usd_rub) if ydata.get("RUB") else usd_rub

            rates["USD/RUB"] = {
                "name": "USD/RUB",
                "symbol": "$",
                "value": round(usd_rub, 2),
                "unit": "RUB",
                "change_24h": round((usd_rub - y_usd_rub) / y_usd_rub * 100, 2) if y_usd_rub else 0,
                "source": "CoinGecko"
            }

            eur_usd = fx.get("EUR")
            if eur_usd:
                eur_rub = round(usd_rub / eur_usd, 2)
                y_eur_usd = ydata.get("EUR")
                if y_eur_usd and y_usd_rub:
                    y_eur_rub = y_usd_rub / y_eur_usd
                    ch = round((eur_rub - y_eur_rub) / y_eur_rub * 100, 2)
                else:
                    ch = 0
                rates["EUR/RUB"] = {
                    "name": "EUR/RUB",
                    "symbol": "\u20ac",
                    "value": eur_rub,
                    "unit": "RUB",
                    "change_24h": ch,
                    "source": "Frankfurter"
                }

            cny_usd = fx.get("CNY")
            if cny_usd:
                cny_rub = round(usd_rub / cny_usd, 2)
                y_cny_usd = ydata.get("CNY")
                if y_cny_usd and y_usd_rub:
                    y_cny_rub = y_usd_rub / y_cny_usd
                    ch = round((cny_rub - y_cny_rub) / y_cny_rub * 100, 2)
                else:
                    ch = 0
                rates["CNY/RUB"] = {
                    "name": "CNY/RUB",
                    "symbol": "\u00a5",
                    "value": cny_rub,
                    "unit": "RUB",
                    "change_24h": ch,
                    "source": "Frankfurter"
                }

            gbp_usd = fx.get("GBP")
            gbp_ch = 0
            if gbp_usd:
                gbp_rub = round(usd_rub / gbp_usd, 2)
                y_gbp_usd = ydata.get("GBP")
                if y_gbp_usd and y_usd_rub:
                    y_gbp_rub = y_usd_rub / y_gbp_usd
                    gbp_ch = round((gbp_rub - y_gbp_rub) / y_gbp_rub * 100, 2)

            jpy_usd = fx.get("JPY")
            jpy_ch = 0
            if jpy_usd:
                jpy_rub = round(usd_rub / jpy_usd * 100, 2)
                y_jpy_usd = ydata.get("JPY")
                if y_jpy_usd and y_usd_rub:
                    y_jpy_rub = y_usd_rub / y_jpy_usd * 100
                    jpy_ch = round((jpy_rub - y_jpy_rub) / y_jpy_rub * 100, 2)
                rates["JPY/RUB"] = {
                    "name": "JPY/RUB",
                    "symbol": "\u00a5",
                    "value": jpy_rub,
                    "unit": "RUB/100",
                    "change_24h": jpy_ch,
                    "source": "Frankfurter"
                }

            if gbp_usd:
                rates["GBP/RUB"] = {
                    "name": "GBP/RUB",
                    "symbol": "\u00a3",
                    "value": gbp_rub,
                    "unit": "RUB",
                    "change_24h": gbp_ch,
                    "source": "Frankfurter"
                }
    except Exception as e:
        print(f"FIAT ERROR: {e}", flush=True)

    return rates


def fetch_crypto_rates():
    rates = {}

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,tether,binancecoin"
            "&vs_currencies=rub,usd"
            "&include_24hr_change=true",
            timeout=10
        )
        if resp.ok:
            data = resp.json()

            mapping = {
                "bitcoin": ("BTC", "Bitcoin"),
                "ethereum": ("ETH", "Ethereum"),
                "binancecoin": ("BNB", "BNB"),
                "tether": ("USDT", "Tether"),
            }

            for cid, (symbol, name) in mapping.items():
                if cid in data:
                    rub = data[cid].get("rub")
                    usd = data[cid].get("usd")
                    change = data[cid].get("usd_24h_change", 0)
                    rates[symbol] = {
                        "name": f"{symbol}/RUB",
                        "symbol": symbol,
                        "value": rub,
                        "unit": "RUB",
                        "value_usd": usd,
                        "change_24h": round(change, 2) if change else 0,
                        "source": "CoinGecko"
                    }
    except Exception as e:
        print(f"CRYPTO ERROR: {e}", flush=True)

    return rates


def fetch_crypto_history():
    result = {}
    coin_ids = {"bitcoin": "BTC", "ethereum": "ETH", "binancecoin": "BNB"}

    for cid, symbol in coin_ids.items():
        try:
            resp = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
                "?vs_currency=usd&days=365&interval=daily",
                timeout=15
            )
            if resp.ok:
                data = resp.json().get("prices", [])
                if len(data) >= 2:
                    now_price = data[-1][1]
                    week_ago = now_price
                    month_ago = now_price
                    year_ago = now_price

                    for ts_ms, price in data:
                        age_days = (time.time() * 1000 - ts_ms) / 86400000
                        if 6 <= age_days <= 8:
                            week_ago = price
                        elif 28 <= age_days <= 32:
                            month_ago = price
                        elif 360 <= age_days <= 370:
                            year_ago = price

                    if week_ago and week_ago > 0:
                        result[f"{symbol}/RUB"] = {
                            "week": round((now_price - week_ago) / week_ago * 100, 2),
                            "month": round((now_price - month_ago) / month_ago * 100, 2) if month_ago != now_price else None,
                            "year": round((now_price - year_ago) / year_ago * 100, 2) if year_ago != now_price else None,
                        }
        except Exception as e:
            print(f"CRYPTO HISTORY ERROR {cid}: {e}", flush=True)

    return result


def fetch_fiat_history():
    result = {}
    pairs = [("USD", "RUB"), ("EUR", "RUB"), ("GBP", "RUB"), ("CNY", "RUB"), ("JPY", "RUB")]

    for base, quote in pairs:
        name = f"{base}/{quote}"
        try:
            now_resp = requests.get(
                f"https://api.frankfurter.app/latest?from={base}&to={quote}",
                timeout=10
            )
            if not now_resp.ok:
                continue
            now_rate = now_resp.json().get("rates", {}).get(quote)
            if not now_rate:
                continue

            changes = {}
            for label, days in [("week", 7), ("month", 30), ("year", 365)]:
                date_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                hist_resp = requests.get(
                    f"https://api.frankfurter.app/{date_str}?from={base}&to={quote}",
                    timeout=10
                )
                if hist_resp.ok:
                    old_rate = hist_resp.json().get("rates", {}).get(quote)
                    if old_rate and old_rate > 0:
                        changes[label] = round((now_rate - old_rate) / old_rate * 100, 2)

            if changes:
                result[name] = changes
        except Exception as e:
            print(f"FIAT HISTORY ERROR {name}: {e}", flush=True)

    return result


def _fetch_yahoo_metals(symbols):
    result = {}
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{','.join(symbols.values())}"
            if len(symbols) == 1 else None,
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
    except Exception:
        pass

    for name, sym in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if resp.ok:
                res = resp.json().get("chart", {}).get("result", [{}])[0]
                closes = res.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                valid = [c for c in closes if c is not None]
                if len(valid) >= 2:
                    result[name] = {
                        "current": valid[-1],
                        "prev": valid[-2],
                        "closes": valid,
                    }
        except Exception:
            pass

    return result


def fetch_precious_metals():
    rates = {}
    usd_rub = get_usd_rub()

    yahoo_symbols = {
        "Gold (Au)": "GC=F",
        "Silver (Ag)": "SI=F",
        "Platinum (Pt)": "PL=F",
        "Palladium (Pd)": "PA=F",
    }

    yahoo_data = _fetch_yahoo_metals(yahoo_symbols)

    fallback_usd = {
        "Gold (Au)": 3250.0,
        "Silver (Ag)": 38.5,
        "Platinum (Pt)": 1020.0,
        "Palladium (Pd)": 1050.0,
    }

    for name in yahoo_symbols:
        data = yahoo_data.get(name)
        fb = fallback_usd.get(name, 3000.0)

        if data:
            price_usd_oz = data["current"]
            prev_usd_oz = data["prev"]
            source = "Yahoo Finance"
        else:
            price_usd_oz = fb
            prev_usd_oz = fb
            source = "Fallback"

        price_per_gram_usd = round(price_usd_oz / _OZ_TO_G, 4)
        price_per_gram_rub = round(price_per_gram_usd * usd_rub, 2)

        change_24h = 0
        if prev_usd_oz and prev_usd_oz > 0:
            change_24h = round((price_usd_oz - prev_usd_oz) / prev_usd_oz * 100, 2)

        rates[name] = {
            "name": name,
            "symbol": name.split("(")[1].rstrip(")"),
            "value": price_per_gram_rub,
            "unit": "RUB/г",
            "value_usd": price_per_gram_usd,
            "change_24h": change_24h,
            "source": source,
        }

    return rates


def fetch_industrial_metals():
    rates = {}
    usd_rub = get_usd_rub()

    y_usd_rub = usd_rub
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yresp = requests.get(
            f"https://api.frankfurter.app/{yesterday}?from=USD&to=RUB",
            timeout=10
        )
        if yresp.ok:
            yr = yresp.json().get("rates", {}).get("RUB")
            if yr and yr > 50:
                y_usd_rub = yr
    except Exception:
        pass

    yahoo_symbols = {
        "Copper (Cu)": "HG=F",
        "Aluminum (Al)": "ALI=F",
    }

    yahoo_data = _fetch_yahoo_metals(yahoo_symbols)

    hardcoded_usd_per_ton = {
        "Nickel (Ni)": 16500.0,
        "Tin (Sn)": 33000.0,
        "Zinc (Zn)": 2800.0,
        "Lead (Pb)": 2100.0,
    }

    usd_rub_change = round((usd_rub - y_usd_rub) / y_usd_rub * 100, 2) if y_usd_rub > 0 else 0

    for name, sym in yahoo_symbols.items():
        data = yahoo_data.get(name)
        if data:
            if name == "Copper (Cu)":
                price_per_kg_usd = round(data["current"] * 2.20462, 4)
                prev_per_kg_usd = round(data["prev"] * 2.20462, 4)
            else:
                price_per_kg_usd = round(data["current"] / 1000, 4)
                prev_per_kg_usd = round(data["prev"] / 1000, 4)

            price_per_kg_rub = round(price_per_kg_usd * usd_rub, 2)
            change_24h = 0
            if prev_per_kg_usd > 0:
                change_24h = round((price_per_kg_usd - prev_per_kg_usd) / prev_per_kg_usd * 100, 2)

            rates[name] = {
                "name": name,
                "symbol": name.split("(")[1].rstrip(")"),
                "value": price_per_kg_rub,
                "unit": "RUB/кг",
                "value_usd": price_per_kg_usd,
                "change_24h": change_24h,
                "source": "Yahoo Finance",
            }

    for name, price_per_ton_usd in hardcoded_usd_per_ton.items():
        price_per_kg_usd = round(price_per_ton_usd / 1000, 4)
        price_per_kg_rub = round(price_per_kg_usd * usd_rub, 2)

        rates[name] = {
            "name": name,
            "symbol": name.split("(")[1].rstrip(")"),
            "value": price_per_kg_rub,
            "unit": "RUB/кг",
            "value_usd": price_per_kg_usd,
            "change_24h": usd_rub_change,
            "source": "LME",
        }

    return rates


def save_rates(category, rates):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")

    for key, rate in rates.items():
        con.execute("""
            INSERT OR REPLACE INTO currency_rates
            (category, name, symbol, value, unit, change_24h, fetched_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            category,
            rate.get("name", key),
            rate.get("symbol", ""),
            rate.get("value"),
            rate.get("unit", ""),
            rate.get("change_24h", 0),
            now,
            rate.get("source", "")
        ))

        con.execute("""
            INSERT INTO currency_history
            (category, name, value, unit, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            category,
            rate.get("name", key),
            rate.get("value"),
            rate.get("unit", ""),
            now
        ))

    con.commit()
    con.close()


def update_all():
    init_db()

    fiat = fetch_fiat_rates()
    save_rates("fiat", fiat)

    crypto = fetch_crypto_rates()
    save_rates("crypto", crypto)

    precious = fetch_precious_metals()
    save_rates("precious", precious)

    industrial = fetch_industrial_metals()
    save_rates("industrial", industrial)

    total = len(fiat) + len(crypto) + len(precious) + len(industrial)
    print(f"CURRENCIES UPDATE: {total} rates saved", flush=True)


def get_latest(category=None):
    con = sqlite3.connect(DB, timeout=10)

    if category:
        rows = con.execute("""
            SELECT category, name, symbol, value, unit, change_24h, fetched_at, source
            FROM currency_rates
            WHERE category = ?
            ORDER BY fetched_at DESC
        """, (category,)).fetchall()
    else:
        rows = con.execute("""
            SELECT category, name, symbol, value, unit, change_24h, fetched_at, source
            FROM currency_rates
            ORDER BY category, fetched_at DESC
        """).fetchall()

    con.close()

    result = {}
    seen = set()
    cat_order = {"fiat": 0, "crypto": 1, "precious": 2, "industrial": 3}

    item_order = {
        "fiat": {"USD/RUB": 0, "EUR/RUB": 1, "CNY/RUB": 2, "JPY/RUB": 3, "GBP/RUB": 4},
        "crypto": {"BTC/RUB": 0, "ETH/RUB": 1, "BNB/RUB": 2, "USDT/RUB": 3},
        "precious": {"Gold (Au)": 0, "Silver (Ag)": 1, "Platinum (Pt)": 2, "Palladium (Pd)": 3},
        "industrial": {"Copper (Cu)": 0, "Aluminum (Al)": 1, "Nickel (Ni)": 2, "Tin (Sn)": 3, "Zinc (Zn)": 4, "Lead (Pb)": 5},
    }

    for row in rows:
        cat, name, symbol, value, unit, change, fetched, source = row
        key = f"{cat}:{name}"
        if key not in seen:
            seen.add(key)
            changes = get_changes(name)
            if cat not in result:
                result[cat] = []
            result[cat].append({
                "name": name,
                "symbol": symbol,
                "value": value,
                "unit": unit,
                "change_24h": change,
                "change_week": changes.get("week"),
                "change_month": changes.get("month"),
                "change_year": changes.get("year"),
                "fetched_at": fetched,
                "source": source
            })

    for cat in result:
        order = item_order.get(cat)
        if order:
            result[cat].sort(key=lambda x: order.get(x["name"], 99))

    return dict(sorted(result.items(), key=lambda x: cat_order.get(x[0], 99)))


def get_history(name, hours=24):
    con = sqlite3.connect(DB, timeout=10)

    rows = con.execute("""
        SELECT value, unit, fetched_at
        FROM currency_history
        WHERE name = ?
        ORDER BY id DESC
        LIMIT ?
    """, (name, hours * 4)).fetchall()

    con.close()

    return [{"value": r[0], "unit": r[1], "fetched_at": r[2]} for r in reversed(rows)]


def get_changes(name):
    con = sqlite3.connect(DB, timeout=10)

    now_row = con.execute("""
        SELECT value FROM currency_history WHERE name = ? ORDER BY id DESC LIMIT 1
    """, (name,)).fetchone()
    if not now_row:
        con.close()
        return {}
    current = now_row[0]
    if not current:
        con.close()
        return {}

    result = {}
    need_fetch = []

    for label, hours in [("week", 168), ("month", 720), ("year", 8760)]:
        row = con.execute("""
            SELECT value FROM currency_history
            WHERE name = ? AND fetched_at <= datetime('now', 'localtime', ?)
            ORDER BY id DESC LIMIT 1
        """, (name, f"-{hours} hours")).fetchone()
        if row and row[0]:
            old = row[0]
            pct = (current - old) / old * 100
            result[label] = round(pct, 2)
        else:
            need_fetch.append(label)

    con.close()

    if not need_fetch:
        return result

    metal_yahoo = {
        "Gold (Au)": "GC=F",
        "Silver (Ag)": "SI=F",
        "Platinum (Pt)": "PL=F",
        "Palladium (Pd)": "PA=F",
        "Copper (Cu)": "HG=F",
        "Aluminum (Al)": "ALI=F",
    }

    if name in metal_yahoo and need_fetch:
        symbol = metal_yahoo[name]
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if resp.ok:
                data = resp.json().get("chart", {}).get("result", [{}])[0]
                closes = data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                valid = [c for c in closes if c is not None]
                if len(valid) >= 2:
                    now_p = valid[-1]
                    for label in need_fetch:
                        offsets = {"week": 7, "month": 30, "year": 365}
                        idx = min(offsets[label], len(valid) - 1)
                        old_p = valid[-(idx + 1)]
                        if old_p > 0:
                            result[label] = round((now_p - old_p) / old_p * 100, 2)
        except Exception:
            pass

    if name in ("BTC/RUB", "ETH/RUB", "BNB/RUB"):
        symbol = name.split("/")[0]
        coin_map = {"BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin"}
        cid = coin_map.get(symbol)
        if cid:
            try:
                resp = requests.get(
                    f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
                    "?vs_currency=usd&days=365&interval=daily",
                    timeout=15
                )
                if resp.ok:
                    prices = resp.json().get("prices", [])
                    if len(prices) >= 2:
                        now_p = prices[-1][1]
                        for ts_ms, price in prices:
                            age_days = (time.time() * 1000 - ts_ms) / 86400000
                            if "week" in need_fetch and 6 <= age_days <= 8:
                                result["week"] = round((now_p - price) / price * 100, 2)
                            if "month" in need_fetch and 28 <= age_days <= 32:
                                result["month"] = round((now_p - price) / price * 100, 2)
                            if "year" in need_fetch and 360 <= age_days <= 370:
                                result["year"] = round((now_p - price) / price * 100, 2)
                pass
            except Exception:
                pass

    elif name in ("USD/RUB", "EUR/RUB", "GBP/RUB", "CNY/RUB", "JPY/RUB"):
        target = name.split("/")[0].lower()
        try:
            now_resp = requests.get(
                "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
                timeout=10
            )
            if now_resp.ok:
                now_data = now_resp.json().get("usd", {})
                rub_now = now_data.get("rub", 0)
                cur_now = now_data.get(target, 0) if target != "usd" else 1
                if rub_now and cur_now:
                    now_rate = rub_now / cur_now if target != "usd" else rub_now
                    for label in list(need_fetch):
                        days = {"week": 7, "month": 30, "year": 365}[label]
                        date_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                        hist_resp = requests.get(
                            f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date_str}/v1/currencies/usd.json",
                            timeout=10
                        )
                        if hist_resp.ok:
                            hist_data = hist_resp.json().get("usd", {})
                            rub_old = hist_data.get("rub", 0)
                            cur_old = hist_data.get(target, 0) if target != "usd" else 1
                            if rub_old and cur_old:
                                old_rate = rub_old / cur_old if target != "usd" else rub_old
                                if old_rate > 0:
                                    result[label] = round((now_rate - old_rate) / old_rate * 100, 2)
                pass
        except Exception:
            pass

    return result
