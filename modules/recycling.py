import requests
from bs4 import BeautifulSoup
import sqlite3
import json
import re
import time
from datetime import datetime


# Task4: БД — из core.db (единый источник пути, §20/§25)
from core.db import DB


def init_db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute("""
        CREATE TABLE IF NOT EXISTS recycling_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            material TEXT NOT NULL,
            price_min REAL,
            price_max REAL,
            unit TEXT,
            region TEXT,
            source TEXT,
            fetched_at TEXT
        )
    """)

    con.commit()
    con.close()


def parse_pushkin_prices():
    prices = []

    try:
        resp = requests.get(
            "https://pushkin.ru/price/",
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux armv7l) "
                              "AppleWebKit/537.36"
            }
        )

        if not resp.ok:
            return prices

        soup = BeautifulSoup(resp.text, "html.parser")

        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    material = cells[0].get_text(strip=True)
                    price_text = cells[1].get_text(strip=True)

                    price_match = re.search(
                        r"([\d\s,.]+)",
                        price_text
                    )
                    if price_match:
                        price_str = price_match.group(1) \
                            .replace(" ", "") \
                            .replace(",", ".")
                        try:
                            price = float(price_str)
                            prices.append({
                                "material": material,
                                "price": price,
                                "unit": "RUB/kg",
                                "source": "pushkin.ru"
                            })
                        except ValueError:
                            pass

    except Exception as e:
        print(f"PUSHKIN PARSE ERROR: {e}", flush=True)

    return prices


def parse_vitaminstir_prices():
    prices = []
    last_error = None

    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            resp = requests.get(
                "https://vitaminstir.ru/price/",
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux armv7l) "
                                  "AppleWebKit/537.36"
                }
            )

            if not resp.ok:
                return prices

            prices = []
            soup = BeautifulSoup(resp.text, "html.parser")

            tables = soup.find_all("table")

            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        material = cells[0].get_text(strip=True)
                        price_text = cells[1].get_text(strip=True)

                        price_match = re.search(
                            r"([\d\s,.]+)",
                            price_text
                        )
                        if price_match:
                            price_str = price_match.group(1) \
                                .replace(" ", "") \
                                .replace(",", ".")
                            try:
                                price = float(price_str)
                                prices.append({
                                    "material": material,
                                    "price": price,
                                    "unit": "RUB/kg",
                                    "source": "vitaminstir.ru"
                                })
                            except ValueError:
                                pass

            return prices

        except Exception as e:
            last_error = e
            print(
                f"VITAMINSTIR ATTEMPT {attempt + 1}/3 FAILED: {e}",
                flush=True
            )

    print(f"VITAMINSTIR PARSE ERROR: {last_error}", flush=True)
    return prices


def parse_avito_prices():
    prices = []

    try:
        resp = requests.get(
            "https://www.avito.ru/ivanovo/kupit/?"
            "q=макулатура+прием",
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux armv7l) "
                              "AppleWebKit/537.36"
            }
        )

        if not resp.ok:
            return prices

        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.find_all(
            "div",
            {"data-marker": "item"}
        )

        for item in items[:10]:
            title_el = item.find(
                "div",
                {"itemprop": "name"}
            )
            price_el = item.find(
                "meta",
                {"itemprop": "price"}
            )

            if title_el and price_el:
                title = title_el.get_text(strip=True)
                price = price_el.get("content", "")

                if price:
                    try:
                        prices.append({
                            "material": title[:100],
                            "price": float(price),
                            "unit": "RUB",
                            "source": "avito.ru"
                        })
                    except ValueError:
                        pass

    except Exception as e:
        print(f"AVITO PARSE ERROR: {e}", flush=True)

    return prices


def save_prices(category, prices):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")

    for price in prices:
        con.execute("""
            INSERT INTO recycling_prices
            (category, material, price_min, price_max, unit, region, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            category,
            price.get("material", ""),
            price.get("price"),
            price.get("price_max"),
            price.get("unit", ""),
            "Ивановская обл. / ЦФО",
            price.get("source", ""),
            now
        ))

    con.commit()
    con.close()


def update_all():
    init_db()

    pushkin = parse_pushkin_prices()
    if pushkin:
        save_prices("paper", pushkin)

    total = len(pushkin)
    print(f"RECYCLING UPDATE: {total} prices saved", flush=True)


def get_latest(category=None):
    con = sqlite3.connect(DB, timeout=10)

    if category:
        rows = con.execute("""
            SELECT category, material, price_min, price_max, unit, region, source, fetched_at
            FROM recycling_prices
            WHERE category = ?
            ORDER BY fetched_at DESC
        """, (category,)).fetchall()
    else:
        rows = con.execute("""
            SELECT category, material, price_min, price_max, unit, region, source, fetched_at
            FROM recycling_prices
            ORDER BY category, fetched_at DESC
        """).fetchall()

    con.close()

    result = {}
    seen = set()

    for row in rows:
        cat, material, pmin, pmax, unit, region, source, fetched = row
        key = f"{cat}:{material}"
        if key not in seen:
            seen.add(key)
            if cat not in result:
                result[cat] = []
            result[cat].append({
                "material": material,
                "price_min": pmin,
                "price_max": pmax,
                "unit": unit,
                "region": region,
                "source": source,
                "fetched_at": fetched
            })

    return result
