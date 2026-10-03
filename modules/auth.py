import hashlib
import json
import os
import time

from flask_wtf.csrf import CSRFError

USERS_PATH = "/etc/lan-discovery/users.json"
SECRET_KEY_PATH = "/etc/lan-discovery/secret.key"
SESSION_TTL = 12 * 3600  # TTL сессии, сек (P0-5): старые сессии без login_ts тоже истекают

_login_attempts = {}  # ip -> [count, first_attempt_time]


def _load_or_create_secret_key():
    try:
        with open(SECRET_KEY_PATH, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key.hex()
    except Exception:
        pass
    key = os.urandom(32)
    try:
        os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(key)
    except Exception:
        pass
    return key.hex()


def _hash(pw):
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _verify_hash(pw, stored_hash):
    import bcrypt
    try:
        if stored_hash.startswith("$2"):
            return bcrypt.checkpw(pw.encode(), stored_hash.encode())
    except Exception:
        pass
    return hashlib.sha256(pw.encode()).hexdigest() == stored_hash


def load_users():
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(data):
    # D-07: атомарная запись (tmp + os.replace) — обрыв/параллельный читатель
    # не получит обрезанный users.json (иначе панель остаётся без входа).
    tmp = USERS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, USERS_PATH)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def get_current_user():
    from flask import session
    from types import SimpleNamespace
    u = session.get("user")
    if not u:
        return None
    # P0-5: TTL сессии — нет login_ts (старые сессии) или истёк → выкидываем
    login_ts = session.get("login_ts")
    if not login_ts or (time.time() - login_ts) > SESSION_TTL:
        session.pop("user", None)
        session.pop("login_ts", None)
        return None
    users = load_users()
    data = users.get(u)
    # P0-5: отключённый/удалённый пользователь тоже выкидывается из сессии
    if not data or not data.get("enabled", True):
        session.pop("user", None)
        session.pop("login_ts", None)
        return None
    return SimpleNamespace(
        username=u,
        role=data.get("role", "guest"),
        enabled=data.get("enabled", True),
        display_name=data.get("display_name", u),
    )


def get_current_username():
    from flask import session
    return session.get("user")


def login_required(f):
    from functools import wraps
    from flask import redirect, url_for, request, jsonify
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            # D-08: API без сессии — JSON 401 (302 на HTML-форму ломает
            # fetch-клиенты), страницы — редирект на /login, как раньше
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    from functools import wraps
    from flask import jsonify
    @wraps(f)
    def wrapped(*args, **kwargs):
        u = get_current_user()
        if not u or u.role != "admin":
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped


def can_edit(f):
    from functools import wraps
    from flask import jsonify
    @wraps(f)
    def wrapped(*args, **kwargs):
        u = get_current_user()
        if not u or u.role == "guest":
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped


def _check_rate_limit(ip, max_attempts=5, window=300):
    now = time.time()
    if ip in _login_attempts:
        count, first = _login_attempts[ip]
        if now - first > window:
            _login_attempts[ip] = [1, now]
            return True
        if count >= max_attempts:
            return False
        _login_attempts[ip] = [count + 1, first]
        return True
    _login_attempts[ip] = [1, now]
    return True


def _reset_rate_limit(ip):
    _login_attempts.pop(ip, None)


def register_routes(app, ctx):
    from flask import request, redirect, url_for, render_template, session, jsonify

    def _panel_name():
        try:
            return ctx.panel_name()
        except Exception:
            return ""

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        error = None
        if request.method == "POST":
            ip = request.remote_addr
            if not _check_rate_limit(ip):
                error = "Слишком много попыток. Подождите 5 минут."
                return render_template("login.html", error=error,
                                       panel_name=_panel_name())
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            users = load_users()
            u = users.get(username)
            if u and u.get("enabled") and _verify_hash(password, u.get("password_hash", "")):
                _reset_rate_limit(ip)
                session["user"] = username
                session["login_ts"] = time.time()
                stored = u.get("password_hash", "")
                if stored and not stored.startswith("$2"):
                    # P0-5: legacy SHA-256 → однократный re-hash bcrypt
                    # при первом успешном входе; дальше только bcrypt
                    u["password_hash"] = _hash(password)
                    users[username] = u
                    save_users(users)
                return redirect("/")  # HOME (§22) — dashboard
            error = "Неверное имя пользователя или пароль"
        return render_template("login.html", error=error,
                               panel_name=_panel_name())

    @app.route("/logout")
    def logout():
        session.pop("user", None)
        return redirect(url_for("login_page"))

    @app.errorhandler(CSRFError)
    def _csrf_stale(e):
        """Устаревший/чужой CSRF → человеку понятный ответ.

        Ловушка стенда N6: восстановленная вкладка или кэш формы /login
        без живой куки сессии давал голый 400 «The CSRF session token is
        missing» без подсказки. Теперь /login отдаёт свежую форму (200,
        юзер просто жмёт «Войти»), API — JSON 400, остальное — текст с
        подсказкой F5. Защита не ослабляется: валидация токена та же.
        """
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="csrf",
                           message="Форма устарела — обновите страницу"), 400
        if request.path == "/login":
            return render_template(
                "login.html",
                error="Форма устарела — войдите ещё раз.",
                panel_name=_panel_name(),
            ), 200
        return ("Форма устарела — обновите страницу (F5) и повторите "
                "действие.", 400, {"Content-Type":
                                   "text/plain; charset=utf-8"})
