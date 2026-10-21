import sqlite3
import re
import json
from datetime import datetime, timezone, timedelta
from flask import render_template, jsonify

from core import config as core_config
# Task4: БД — из core.db (единственный владелец пути; --prefix уводит
# devices.db в каталог кода, а не в чужой /opt)
from core.db import DB

# Task1: путь/кэш/чтение settings — только core.config (§20); алиас —
# совместимость (имя модуля сохранено, §32)
SETTINGS_PATH = core_config.SETTINGS_PATH


def load_settings():
    """Чтение settings — делегирование core.config (единый кэш §20)."""
    return core_config.load()


def weather_is_weekend(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() >= 5
    except Exception:
        return False


def weather_date_name(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")

        weekdays = [
            "Пн", "Вт", "Ср", "Чт",
            "Пт", "Сб", "Вс"
        ]

        months = [
            "янв.", "февр.", "марта", "апр.",
            "мая", "июня", "июля", "авг.",
            "сент.", "окт.", "нояб.", "дек."
        ]

        return f"{weekdays[dt.weekday()]}, {dt.day} {months[dt.month - 1]}"

    except Exception:
        return date_str


def weather_code_name(code):
    names = {
        0: "Ясно",
        1: "Преимущественно ясно",
        2: "Переменная облачность",
        3: "Пасмурно",

        45: "Туман",
        48: "Туман с изморозью",

        51: "Слабая морось",
        53: "Морось",
        55: "Сильная морось",
        56: "Слабая ледяная морось",
        57: "Сильная ледяная морось",

        61: "Слабый дождь",
        63: "Дождь",
        65: "Сильный дождь",
        66: "Слабый ледяной дождь",
        67: "Сильный ледяной дождь",

        71: "Слабый снег",
        73: "Снег",
        75: "Сильный снег",
        77: "Снежные зёрна",

        80: "Слабый ливень",
        81: "Ливень",
        82: "Сильный ливень",

        85: "Слабый снегопад",
        86: "Сильный снегопад",

        95: "Гроза",
        96: "Гроза с небольшим градом",
        99: "Гроза с сильным градом",
    }

    return names.get(code, "Неизвестно")


def wind_direction_name(degrees):
    if degrees is None:
        return "-"

    directions = [
        "С", "ССВ", "СВ", "ВСВ",
        "В", "ВЮВ", "ЮВ", "ЮЮВ",
        "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ",
        "З", "ЗСЗ", "СЗ", "ССЗ"
    ]

    index = int((degrees + 11.25) / 22.5) % 16
    return directions[index]


def weather_current():
    try:
        con = sqlite3.connect(DB, timeout=5)

        row = con.execute("""
            SELECT
                timestamp,
                temperature,
                apparent_temperature,
                humidity,
                precipitation,
                weather_code,
                wind_speed,
                wind_direction,
                pressure,
                cloud_cover
            FROM weather_observations
            ORDER BY timestamp DESC
            LIMIT 1
        """).fetchone()

        con.close()

        if not row:
            return None

        return {
            "timestamp": row[0],
            "temperature": row[1],
            "apparent_temperature": row[2],
            "humidity": row[3],
            "precipitation": row[4],
            "weather_code": row[5],
            "wind_speed": row[6],
            "wind_direction": row[7],
            "pressure": row[8],
            "cloud_cover": row[9] if len(row) > 9 else None
        }

    except Exception:
        return None

def weather_daily():
    try:
        con = sqlite3.connect(DB, timeout=5)

        rows = con.execute("""
            SELECT
                date,
                temp_min,
                temp_max,
                temp_avg,
                humidity_avg,
                pressure_avg,
                wind_avg,
                precipitation_sum,
                observations
            FROM weather_daily
            ORDER BY date DESC
            LIMIT 30
        """).fetchall()

        con.close()

        return rows

    except Exception:
        return []

def load_env_data():
    """Today's UV/air/radiation + nearest EGASRMRO points (fallback to latest if today not yet fetched)"""
    try:
        today = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        con = sqlite3.connect(DB, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        cols = {r[1] for r in con.execute("PRAGMA table_info(env_data)")}
        has_pts = "radiation_points" in cols
        base = ("uv_index, uv_level, aqi, aqi_level, pm25, pm10, "
                "radiation, radiation_level")
        if has_pts:
            base += ", radiation_points"
        row = con.execute(
            f"SELECT {base} FROM env_data WHERE date = ?",
            (today,)
        ).fetchone()
        if not row:
            row = con.execute(
                f"SELECT {base} FROM env_data ORDER BY date DESC LIMIT 1"
            ).fetchone()
        con.close()
        if row:
            data = {
                "uv_index": row[0], "uv_level": row[1],
                "aqi": row[2], "aqi_level": row[3],
                "pm25": row[4], "pm10": row[5],
                "radiation": row[6], "radiation_level": row[7],
                "radiation_points": None,
            }
            if has_pts and len(row) > 8 and row[8]:
                try:
                    data["radiation_points"] = json.loads(row[8])
                except (TypeError, ValueError):
                    pass
            data["settlement"] = (
                load_settings().get("weather", {}) or {}
            ).get("region_name") or "Иваново"
            return data
    except Exception as e:
        pass
    return None

def weather_alerts():
    try:
        settings = load_settings()
        weather = settings.get("weather", {})
        region = weather.get("region_name", "Иваново")

        # Свежесть: тянем только предупреждения, обновлённые за последние 24 ч
        # (писатель — weather-update.py; битый источник не должен вечно висеть).
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")

        con = sqlite3.connect(DB, timeout=5)

        rows = con.execute("""
            SELECT
                fetched_at,
                region,
                alert,
                source_window
            FROM weather_alerts
            WHERE region LIKE ? AND alert IS NOT NULL AND alert != ''
              AND substr(fetched_at, 1, 16) >= ?
            ORDER BY fetched_at DESC
            LIMIT 10
        """, (f"%{region}%", cutoff)).fetchall()

        con.close()

        return rows

    except Exception:
        return []

def mchs_alerts():
    try:
        settings = load_settings()
        weather = settings.get("weather", {})
        region_code = weather.get("region_code", "ivanovo")

        con = sqlite3.connect(DB, timeout=5)

        rows = con.execute("""
            SELECT
                fetched_at,
                published_at,
                title,
                text,
                source_url
            FROM mchs_alerts
            WHERE id = 1
        """).fetchall()

        con.close()

        if region_code != "ivanovo":
            return []

        active_rows = []

        months = {
            "января": 1,
            "февраля": 2,
            "марта": 3,
            "апреля": 4,
            "мая": 5,
            "июня": 6,
            "июля": 7,
            "августа": 8,
            "сентября": 9,
            "октября": 10,
            "ноября": 11,
            "декабря": 12
        }

        for row in rows:
            text = row[3] or ""

            match = re.search(
                r"до\s+(\d{1,2}):(\d{2})\s+(\d{1,2})\s+"
                r"(января|февраля|марта|апреля|мая|июня|июля|августа|"
                r"сентября|октября|ноября|декабря)\s+(\d{4})\s+года",
                text,
                re.IGNORECASE
            )

            if match:
                hour = int(match.group(1))
                minute = int(match.group(2))
                day = int(match.group(3))
                month = months[match.group(4).lower()]
                year = int(match.group(5))

                expires_at = datetime(year, month, day, hour, minute)

                if datetime.now() >= expires_at:
                    continue
            else:
                # Нет разбираемой даты окончания — прячем старые статьи
                # (иначе предупреждение висит вечно).
                try:
                    p = (row[1] or "").replace("T", " ")[:19]
                    if len(p) <= 16:
                        pub_dt = datetime.strptime(p, "%Y-%m-%d %H:%M")
                    else:
                        pub_dt = datetime.strptime(p, "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - pub_dt).days >= 3:
                        continue
                except Exception:
                    pass

            active_rows.append(row)

        return active_rows

    except Exception:
        return []


def weather_alert_status():
    """Свежий прогон информера meteoinfo (для шапки блока предупреждений).

    alert=NULL («оповещения не требуется») — валидное состояние: блок
    показывается со статусом «активных предупреждений нет».
    """
    try:
        settings = load_settings()
        weather = settings.get("weather", {})
        region = weather.get("region_name", "Иваново")

        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")

        con = sqlite3.connect(DB, timeout=5)
        row = con.execute(
            """SELECT fetched_at, region, source_window FROM weather_alerts
               WHERE region LIKE ? AND substr(fetched_at, 1, 16) >= ?
               ORDER BY fetched_at DESC LIMIT 1""",
            (f"%{region}%", cutoff),
        ).fetchone()
        con.close()
        return row

    except Exception:
        return None


def mchs_alert_status():
    """Свежий прогон МЧС-фетчера (для шапки блока экстренных предупреждений)."""
    try:
        settings = load_settings()
        weather = settings.get("weather", {})
        if weather.get("region_code", "ivanovo") != "ivanovo":
            return None

        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")

        con = sqlite3.connect(DB, timeout=5)
        row = con.execute(
            "SELECT fetched_at FROM mchs_alerts "
            "WHERE id = 1 AND substr(fetched_at, 1, 16) >= ?",
            (cutoff,),
        ).fetchone()
        con.close()
        return row

    except Exception:
        return None

def weather_forecast():
    try:
        con = sqlite3.connect(DB, timeout=5)

        # Только сегодня и дальше: просроченные хвосты в БД не должны
        # вытеснять свежие дни из LIMIT 7.
        today = datetime.now().strftime("%Y-%m-%d")

        rows = con.execute("""
            SELECT
                forecast_date,
                weather_code,
                temp_min,
                temp_max,
                precipitation_sum,
                precipitation_probability,
                wind_speed_max,
                wind_direction,
                sunrise,
                sunset,
                fetched_at
            FROM weather_forecast
            WHERE forecast_date >= ?
            ORDER BY forecast_date
            LIMIT 7
        """, (today,)).fetchall()

        con.close()

        return rows

    except Exception:
        return []




def weather_hourly(date_str):
    try:
        con = sqlite3.connect(DB, timeout=5)

        rows = con.execute("""
            SELECT
                hour, temperature, weather_code,
                precipitation, precipitation_probability,
                wind_speed, wind_direction, cloud_cover
            FROM weather_hourly
            WHERE forecast_date = ?
            ORDER BY hour
        """, (date_str,)).fetchall()

        con.close()

        return rows

    except Exception:
        return []


def register_routes(app, ctx):
    login_required = ctx.login_required
    page_data = ctx.page_data

    app.jinja_env.globals["wind_direction_name"] = wind_direction_name
    app.jinja_env.globals["weather_code_name"] = weather_code_name
    app.jinja_env.globals["weather_date_name"] = weather_date_name
    app.jinja_env.globals["weather_is_weekend"] = weather_is_weekend

    @app.route("/weather")
    @login_required
    def weather():
        data = page_data()

        current = weather_current()
        daily = weather_daily()
        alerts = weather_alerts()
        mchs = mchs_alerts()
        forecast = weather_forecast()

        data["weather"] = current
        data["weather_daily"] = daily
        data["weather_alerts"] = alerts
        data["mchs_alerts"] = mchs
        data["weather_alert_status"] = weather_alert_status()
        data["mchs_alert_status"] = mchs_alert_status()
        data["weather_forecast"] = forecast
        data["env_data"] = load_env_data()

        today_str_db = datetime.now().strftime("%Y-%m-%d")
        tomorrow_str_db = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        data["weather_hourly_today"] = weather_hourly(today_str_db)
        data["weather_hourly_tomorrow"] = weather_hourly(tomorrow_str_db)

        if forecast:
            today_fc = forecast[0]
            data["weather_sunrise"] = today_fc[8] if today_fc[8] else None
            data["weather_sunset"] = today_fc[9] if today_fc[9] else None
        else:
            data["weather_sunrise"] = None
            data["weather_sunset"] = None

        try:
            now = datetime.now()
            year = now.year
            month = now.month
            day = now.day
            if month <= 2:
                year -= 1
                month += 12
            A = year // 100
            B = 2 - A + A // 4
            JD = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + B - 1524.5
            days_since_new = (JD - 2451549.5) / 29.530588853
            moon_phase = days_since_new - int(days_since_new)
            data["weather_moon_phase"] = moon_phase
        except Exception:
            data["weather_moon_phase"] = None

        return render_template("weather.html",
            **data
        )

    @app.route("/api/weather-status")
    @login_required
    def api_weather_status():
        try:
            con = sqlite3.connect(DB, timeout=5)
            row = con.execute("""
                SELECT temperature, weather_code, wind_speed, wind_direction, precipitation
                FROM weather_observations
                ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            con.close()

            if row:
                con2 = sqlite3.connect(DB, timeout=5)
                prev = con2.execute("""
                    SELECT temperature FROM weather_observations
                    ORDER BY timestamp DESC LIMIT 1 OFFSET 1
                """).fetchone()
                con2.close()

                trend = 0
                if prev and row[0] is not None and prev[0] is not None:
                    if row[0] > prev[0]:
                        trend = 1
                    elif row[0] < prev[0]:
                        trend = -1

                return jsonify({
                    "temp": row[0],
                    "code": row[1],
                    "wind_speed": row[2],
                    "wind_dir": row[3],
                    "precip": row[4],
                    "trend": trend
                })
            return jsonify({"temp": None})
        except Exception:
            return jsonify({"temp": None})
