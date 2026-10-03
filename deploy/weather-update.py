#!/usr/bin/env python3
"""Сбор погодных данных для панели LAN Discovery (Open-Meteo + met.no).

Основной источник — Open-Meteo; при его недоступности (блок/лимит по IP)
автоматически используется met.no Locationforecast (формат данных
приводится к open-meteo-подобному словарю, восход/заход считается локально).

Пишет в /opt/lan-discovery/devices.db:
  weather_observations   — текущий час (наблюдение для шапки/сейчас)
  weather_hourly         — почасовой прогноз: остаток сегодня + завтра
  weather_daily          — агрегаты текущего дня из наблюдений
  weather_forecast       — прогноз на 7 дней (+ восход/заход)
  weather_forecast_history — архив ежедневных прогнозов
  weather_alerts         — гидромет-предупреждение (meteoinfo.ru информер)
  mchs_alerts            — последнее экстренное предупреждение (37.mchs.gov.ru)

Координаты/регион берутся из /etc/lan-discovery/settings.json (секция weather).
"""
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DB = "/opt/lan-discovery/devices.db"
SETTINGS = "/etc/lan-discovery/settings.json"
TZ = timezone(timedelta(hours=3))

# Основной forecast API и запасной ensemble-эндпоинт (тот же формат полей).
ENDPOINTS = (
    "https://api.open-meteo.com/v1/forecast?",
    "https://ensemble-api.open-meteo.com/v1/ensemble?models=icon_seamless&",
)


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f).get("weather", {})
    except Exception:
        return {}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "lan-discovery-weather"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(query, tries=2, timeout=12, pause=5):
    """Перебирает эндпоинты с ретраями, возвращает (data, endpoint)."""
    last = None
    for base in ENDPOINTS:
        url = base + query
        for _ in range(tries):
            try:
                return get_json(url, timeout=timeout), base.split("?")[0]
            except Exception as e:
                last = e
                time.sleep(pause)
    raise last


# --- Запасной источник: met.no (api.met.no) -------------------------------
# open-meteo периодически отдаёт 429/таймауты по IP; met.no отвечает 200.
# Ответ Locationforecast приводится к словарю формата open-meteo, чтобы
# основной код main() остался без изменений.

METNO_UA = "lan-discovery-weather/1.1 (github.com/kotmartovskiy/Lan-discovery-1.1)"


def _tz(tzname):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzname)
    except Exception:
        return timezone(timedelta(hours=3))


def _metno_symbol_to_wmo(symbol):
    """Symbol-code met.no (clearsky_day, lightrainshowers...) -> код WMO."""
    s = re.sub(r"_(day|night|polartwilight)$", "", symbol or "")
    if not s:
        return None
    if s.startswith("clearsky"):
        return 0
    if s.startswith("fair"):
        return 1
    if s.startswith("partlycloudy"):
        return 2
    if s.startswith("cloudy"):
        return 3
    if s.startswith("fog"):
        return 45
    if "thunder" in s:
        return 96 if "hail" in s else 95
    if "hail" in s:
        return 96
    heavy = "heavy" in s
    light = "light" in s
    if "freezing" in s or "sleet" in s:
        return 67 if heavy else 66
    if "snow" in s:
        if "showers" in s:
            return 86 if heavy else 85
        return 75 if heavy else (71 if light else 73)
    if "rain" in s:
        if "showers" in s:
            return 82 if heavy else (80 if light else 81)
        return 65 if heavy else (61 if light else 63)
    return 3


def _sun_times(lat, lon, date, tz):
    """Восход и заход (уравнение восхода), локальное ISO-время дня date."""
    j0 = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    jd = j0.timestamp() / 86400.0 + 2440587.5
    n = math.ceil(jd - 2451545.0 + 0.0008)
    # J★ = n - lon/360 (lon — восточная долгота): солнечный полдень на 41°В
    # наступает в 09:16 UTC, а не в 14:44
    jstar = n - lon / 360.0
    m = (357.5291 + 0.98560028 * jstar) % 360
    c = (1.9148 * math.sin(math.radians(m))
         + 0.0200 * math.sin(math.radians(2 * m))
         + 0.0003 * math.sin(math.radians(3 * m)))
    lam = (m + c + 180.0 + 102.9372) % 360
    j_transit = (2451545.0 + jstar + 0.0053 * math.sin(math.radians(m))
                 - 0.0069 * math.sin(math.radians(2 * lam)))
    sin_dec = math.sin(math.radians(lam)) * math.sin(math.radians(23.44))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_w0 = ((math.sin(math.radians(-0.833))
               - math.sin(math.radians(lat)) * sin_dec)
              / (math.cos(math.radians(lat)) * cos_dec))
    if cos_w0 > 1 or cos_w0 < -1:
        return None, None
    w0 = math.degrees(math.acos(cos_w0))
    out = []
    for j in (j_transit - w0 / 360.0, j_transit + w0 / 360.0):
        ts = (j - 2440587.5) * 86400.0
        out.append(
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        )
    return out[0], out[1]


def metno_to_openmeteo(met, lat, lon, tzname, now):
    """Словарь формата open-meteo из ответа met.no Locationforecast."""
    ts = (met.get("properties") or {}).get("timeseries") or []
    if not ts:
        return None
    tz = _tz(tzname)

    entries = []
    for item in ts:
        try:
            t = datetime.strptime(item["time"], "%Y-%m-%dT%H:%M:%SZ")
            t = t.replace(tzinfo=timezone.utc).astimezone(tz)
        except Exception:
            continue
        data = item.get("data") or {}
        entries.append({
            "t": t,
            "inst": (data.get("instant") or {}).get("details") or {},
            "h1": data.get("next_1_hours") or {},
            "h6": data.get("next_6_hours") or {},
            "h12": data.get("next_12_hours") or {},
        })
    if not entries:
        return None

    def code_of(e):
        for seg in (e["h1"], e["h6"], e["h12"]):
            sym = (seg.get("summary") or {}).get("symbol_code")
            if sym:
                wmo = _metno_symbol_to_wmo(sym)
                if wmo is not None:
                    return wmo
        return 3

    def wind_kmh(inst):
        v = inst.get("wind_speed")
        return round(v * 3.6, 1) if v is not None else None

    # --- current: точка, ближайшая к текущему моменту ---
    cur_e = min(entries, key=lambda e: abs((e["t"] - now).total_seconds()))
    inst = cur_e["inst"]
    temp = inst.get("air_temperature")
    v_kmh = wind_kmh(inst)
    apparent = temp
    if temp is not None and temp <= 10 and (v_kmh or 0.0) >= 4.8:
        try:
            apparent = (13.12 + 0.6215 * temp
                        - 11.37 * (v_kmh ** 0.16)
                        + 0.3965 * temp * (v_kmh ** 0.16))
        except Exception:
            apparent = temp
    precip_now = ((cur_e["h1"].get("details") or {})
                  .get("precipitation_amount") or 0.0)
    current = {
        "temperature_2m": temp,
        "apparent_temperature": (round(apparent, 1)
                                 if apparent is not None else temp),
        "relative_humidity_2m": inst.get("relative_humidity"),
        "precipitation": precip_now,
        "weather_code": code_of(cur_e),
        "wind_speed_10m": v_kmh if v_kmh is not None else 0.0,
        "wind_direction_10m": inst.get("wind_from_direction"),
        "surface_pressure": inst.get("air_pressure_at_sea_level"),
        "cloud_cover": inst.get("cloud_area_fraction"),
    }

    # --- hourly: остаток сегодня + завтра (пока есть next_1_hours) ---
    today_s = now.strftime("%Y-%m-%d")
    tomorrow_s = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    now_h = now.strftime("%Y-%m-%dT%H:00")
    hourly = {
        "time": [], "temperature_2m": [], "weather_code": [],
        "precipitation": [], "precipitation_probability": [],
        "wind_speed_10m": [], "wind_direction_10m": [], "cloud_cover": [],
    }
    for e in entries:
        th = e["t"].strftime("%Y-%m-%dT%H:%M")
        if th[:10] not in (today_s, tomorrow_s) or th < now_h:
            continue
        if not e["h1"]:
            break
        det = e["h1"].get("details") or {}
        hi = e["inst"]
        hourly["time"].append(th)
        hourly["temperature_2m"].append(hi.get("air_temperature"))
        hourly["weather_code"].append(code_of(e))
        hourly["precipitation"].append(det.get("precipitation_amount") or 0.0)
        hourly["precipitation_probability"].append(None)
        hourly["wind_speed_10m"].append(wind_kmh(hi))
        hourly["wind_direction_10m"].append(hi.get("wind_from_direction"))
        hourly["cloud_cover"].append(hi.get("cloud_area_fraction"))

    # --- daily: агрегаты по локальным дням + восход/заход локально ---
    by_date = {}
    for e in entries:
        dkey = e["t"].strftime("%Y-%m-%d")
        rec = by_date.get(dkey)
        if rec is None:
            rec = by_date[dkey] = {
                "date": e["t"].date(), "tmin": None, "tmax": None,
                "precip": 0.0, "wmax": None,
                "noon": None, "noon_dist": 99,
            }
        t = e["inst"].get("air_temperature")
        if t is not None:
            rec["tmin"] = t if rec["tmin"] is None else min(rec["tmin"], t)
            rec["tmax"] = t if rec["tmax"] is None else max(rec["tmax"], t)
        seg = e["h1"] or e["h6"] or e["h12"]
        p = (seg.get("details") or {}).get("precipitation_amount")
        if p is not None:
            rec["precip"] += p
        w = wind_kmh(e["inst"])
        if w is not None:
            rec["wmax"] = w if rec["wmax"] is None else max(rec["wmax"], w)
        dist = abs(e["t"].hour - 12)
        if dist <= rec["noon_dist"]:
            rec["noon_dist"] = dist
            rec["noon"] = e

    daily = {
        "time": [], "weather_code": [], "temperature_2m_max": [],
        "temperature_2m_min": [], "precipitation_sum": [],
        "precipitation_probability_max": [], "wind_speed_10m_max": [],
        "wind_direction_10m": [], "sunrise": [], "sunset": [],
    }
    for dkey in sorted(by_date):
        rec = by_date[dkey]
        if rec["tmin"] is None or rec["tmax"] is None:
            continue
        sunrise, sunset = _sun_times(lat, lon, rec["date"], tz)
        noon = rec["noon"] or {}
        daily["time"].append(dkey)
        daily["weather_code"].append(code_of(noon) if noon else 3)
        daily["temperature_2m_max"].append(round(rec["tmax"], 1))
        daily["temperature_2m_min"].append(round(rec["tmin"], 1))
        daily["precipitation_sum"].append(round(rec["precip"], 1))
        daily["precipitation_probability_max"].append(None)
        daily["wind_speed_10m_max"].append(rec["wmax"] or 0.0)
        noon_inst = (noon or {}).get("inst") or {}
        daily["wind_direction_10m"].append(
            noon_inst.get("wind_from_direction")
        )
        daily["sunrise"].append(sunrise)
        daily["sunset"].append(sunset)

    if not daily["time"]:
        return None
    return {"current": current, "hourly": hourly, "daily": daily}


def fetch_metno(lat, lon, tzname, now):
    url = ("https://api.met.no/weatherapi/locationforecast/2.0/compact"
           "?lat=%s&lon=%s" % (lat, lon))
    req = urllib.request.Request(url, headers={"User-Agent": METNO_UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        met = json.loads(r.read().decode("utf-8"))
    return metno_to_openmeteo(met, lat, lon, tzname, now)


REGION_CODES = {
    "ivanovo": "019",
    "yaroslavl": "076",
    "kostroma": "044",
    "vladimir": "33",
    "nizhny_novgorod": "52",
    "ryazan": "62",
    "moscow": "50",
    "moscow_region": "50",
    "tver": "69",
}


def fetch_weather_alert(w):
    """Гидромет-предупреждение с информера meteoinfo.ru (POST-регион)."""
    region_code = w.get("region_code", "ivanovo")
    post_code = REGION_CODES.get(region_code, "019")

    url = "https://meteoinfo.ru/informer/meteoalert/"
    request = urllib.request.Request(
        url,
        data=f"a={post_code}".encode(),
        method="POST",
        headers={"User-Agent": "lan-discovery-weather"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        html = response.read().decode("utf-8", "ignore")

    match = re.search(
        r"Ивановская обл\..{0,1500}?<img[^>]+title=\"([^\"]+)\"",
        html,
        re.I | re.S,
    )
    if not match:
        return None
    alert = match.group(1).strip()
    if alert == "Оповещения о погоде не требуется":
        alert = None
    return alert


def update_weather_alert(con, w, now):
    fetched_at = now.isoformat(timespec="minutes")
    try:
        alert = fetch_weather_alert(w)
    except Exception as e:
        print("=== WEATHER ALERT ERROR: %s" % e, file=sys.stderr)
        return
    con.execute(
        """INSERT INTO weather_alerts (id, fetched_at, region, alert, source_window)
           VALUES (1, ?, ?, ?, 'ближайшие 24 часа')
           ON CONFLICT(id) DO UPDATE SET
               fetched_at=excluded.fetched_at, region=excluded.region,
               alert=excluded.alert, source_window=excluded.source_window""",
        (fetched_at, w.get("region_name", "Иваново"), alert),
    )
    con.commit()
    print("=== WEATHER ALERT OK: %s" % (alert or "нет предупреждений"))


def fetch_mchs_alert():
    """Последнее экстренное предупреждение с регионального сайта МЧС."""
    list_url = (
        "https://37.mchs.gov.ru/deyatelnost/press-centr/"
        "operativnaya-informaciya/shtormovye-i-ekstrennye-preduprezhdeniya"
    )
    request = urllib.request.Request(
        list_url, headers={"User-Agent": "lan-discovery-weather"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        html = response.read().decode("utf-8", "ignore")

    match = re.search(
        r"<a class=\"articles-item__title\" href=\"([^\"]+)\">([^<]+)</a>",
        html,
        re.I,
    )
    if not match:
        return None

    path = match.group(1)
    title = re.sub(r"\s+", " ", match.group(2)).strip()
    article_url = (
        path if path.startswith("http") else "https://37.mchs.gov.ru" + path
    )

    request = urllib.request.Request(
        article_url, headers={"User-Agent": "lan-discovery-weather"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        article_html = response.read().decode("utf-8", "ignore")

    published = re.search(
        r"<meta[^>]+itemprop=\"datePublished\"[^>]+(?:datetime|content)=\"([^\"]+)\"",
        article_html,
        re.I,
    )
    body = re.search(
        r"<article[^>]+itemprop=\"articleBody\"[^>]*>(.*?)</article>",
        article_html,
        re.I | re.S,
    )

    text = None
    if body:
        text = re.sub(r"<br\s*/?>", "\n", body.group(1), flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()

    return {
        "title": title,
        "published_at": published.group(1).strip() if published else None,
        "text": text,
        "source_url": article_url,
    }


def update_mchs_alert(con, now):
    fetched_at = now.isoformat(timespec="minutes")
    try:
        alert = fetch_mchs_alert()
    except Exception as e:
        print("=== MCHS ALERT ERROR: %s" % e, file=sys.stderr)
        return
    if not alert:
        print("=== MCHS ALERT: предупреждение не найдено")
        return
    con.execute(
        """INSERT INTO mchs_alerts (id, fetched_at, published_at, title, text, source_url)
           VALUES (1, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               fetched_at=excluded.fetched_at, published_at=excluded.published_at,
               title=excluded.title, text=excluded.text,
               source_url=excluded.source_url""",
        (fetched_at, alert["published_at"], alert["title"],
         alert["text"], alert["source_url"]),
    )
    con.commit()
    print("=== MCHS ALERT OK: %s" % alert["title"])


def main():
    w = load_settings()
    lat = w.get("latitude", 57.0)
    lon = w.get("longitude", 41.0)
    tzname = w.get("timezone", "Europe/Moscow")
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    query = (
        f"latitude={lat}&longitude={lon}"
        f"&timezone={urllib.parse.quote(tzname)}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "precipitation,weather_code,wind_speed_10m,wind_direction_10m,"
        "surface_pressure,cloud_cover"
        "&hourly=temperature_2m,weather_code,precipitation,precipitation_probability,"
        "wind_speed_10m,wind_direction_10m,cloud_cover"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
        "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
        "&forecast_days=7"
    )
    try:
        data, endpoint = fetch(query)
    except Exception as e:
        print("=== OPEN-METEO FAILED: %s" % e, file=sys.stderr)
        data, endpoint = None, None

    if data is None:
        # open-meteo недоступен (429/блок по IP) — пробуем met.no
        try:
            data = fetch_metno(lat, lon, tzname, now)
            endpoint = "met.no"
        except Exception as e:
            print("=== MET.NO FAILED: %s" % e, file=sys.stderr)
        if data is None:
            raise RuntimeError(
                "погодные источники недоступны (open-meteo, met.no)"
            )

    current = data.get("current", {})
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})
    fetched_at = now.isoformat(timespec="minutes")

    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")

    # --- текущий час: одно наблюдение на час ---
    ts_hour = now.strftime("%Y-%m-%dT%H:00")
    con.execute("DELETE FROM weather_observations WHERE timestamp = ?", (ts_hour,))
    con.execute(
        """INSERT INTO weather_observations
           (timestamp, temperature, apparent_temperature, humidity, precipitation,
            weather_code, wind_speed, wind_direction, pressure, cloud_cover)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ts_hour,
            current.get("temperature_2m"),
            current.get("apparent_temperature"),
            current.get("relative_humidity_2m"),
            current.get("precipitation"),
            current.get("weather_code"),
            current.get("wind_speed_10m"),
            current.get("wind_direction_10m"),
            current.get("surface_pressure"),
            current.get("cloud_cover"),
        ),
    )

    # --- агрегаты текущего дня из накопленных наблюдений ---
    con.execute("DELETE FROM weather_daily WHERE date = ?", (today,))
    con.execute(
        """INSERT INTO weather_daily
           (date, temp_min, temp_max, temp_avg, humidity_avg, pressure_avg,
            wind_avg, precipitation_sum, observations)
           SELECT substr(timestamp, 1, 10), min(temperature), max(temperature),
                  avg(temperature), avg(humidity), avg(pressure), avg(wind_speed),
                  coalesce(sum(precipitation), 0), count(*)
           FROM weather_observations
           WHERE substr(timestamp, 1, 10) = ?""",
        (today,),
    )

    # --- почасовой: остаток сегодня + завтра ---
    con.execute("DELETE FROM weather_hourly")
    times = hourly.get("time", [])
    now_h = now.strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(times):
        if len(t) < 13:
            continue
        fdate, hour = t[:10], int(t[11:13])
        if fdate not in (today, tomorrow):
            continue
        if fdate == today and t < now_h:
            continue
        con.execute(
            """INSERT OR REPLACE INTO weather_hourly
               (forecast_date, hour, temperature, weather_code, precipitation,
                precipitation_probability, wind_speed, wind_direction, cloud_cover)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fdate,
                hour,
                hourly.get("temperature_2m", [None] * len(times))[i],
                hourly.get("weather_code", [None] * len(times))[i],
                hourly.get("precipitation", [None] * len(times))[i],
                hourly.get("precipitation_probability", [None] * len(times))[i],
                hourly.get("wind_speed_10m", [None] * len(times))[i],
                hourly.get("wind_direction_10m", [None] * len(times))[i],
                hourly.get("cloud_cover", [None] * len(times))[i],
            ),
        )

    # --- прогноз на 7 дней (направление ветра — с полудня) ---
    # Чистим просроченные даты, иначе LIMIT 7 в ридере отдаёт старые хвосты.
    con.execute("DELETE FROM weather_forecast WHERE forecast_date < ?", (today,))

    noon_wdir = {}
    for i, t in enumerate(times):
        if len(t) >= 13 and int(t[11:13]) == 12:
            noon_wdir[t[:10]] = hourly.get("wind_direction_10m", [None] * len(times))[i]
    # у met.no-фолбэка направление дня сразу в daily (полудень прошёл)
    fc_rows = []
    d_times = daily.get("time", [])
    daily_wdir = daily.get("wind_direction_10m") or [None] * len(d_times)
    for i, fdate in enumerate(d_times):
        row = (
            fdate,
            daily.get("weather_code", [None] * len(d_times))[i],
            daily.get("temperature_2m_min", [None] * len(d_times))[i],
            daily.get("temperature_2m_max", [None] * len(d_times))[i],
            daily.get("precipitation_sum", [None] * len(d_times))[i],
            daily.get("precipitation_probability_max", [None] * len(d_times))[i],
            daily.get("wind_speed_10m_max", [None] * len(d_times))[i],
            daily_wdir[i] or noon_wdir.get(fdate),
            (daily.get("sunrise", [None] * len(d_times))[i] or None),
            (daily.get("sunset", [None] * len(d_times))[i] or None),
            fetched_at,
        )
        fc_rows.append(row)
        con.execute(
            """INSERT OR REPLACE INTO weather_forecast
               (forecast_date, weather_code, temp_min, temp_max, precipitation_sum,
                precipitation_probability, wind_speed_max, wind_direction,
                sunrise, sunset, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        con.execute(
            """INSERT OR REPLACE INTO weather_forecast_history
               (fetched_at, forecast_date, weather_code, temp_min, temp_max,
                precipitation_sum, precipitation_probability, wind_speed_max,
                wind_direction, sunrise, sunset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row[:11],
        )

    con.commit()

    # --- предупреждения (meteoinfo + МЧС), сбой источника не валит погоду ---
    update_weather_alert(con, w, now)
    update_mchs_alert(con, now)
    con.close()

    print(
        "=== WEATHER UPDATE OK: %s (прогноз: %d дн., источник: %s)"
        % (
            now.strftime("%d.%m.%Y %H:%M:%S"),
            len(fc_rows),
            endpoint.rsplit("/", 1)[-1],
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("=== WEATHER UPDATE FAILED: %s" % e, file=sys.stderr)
        raise SystemExit(1)
