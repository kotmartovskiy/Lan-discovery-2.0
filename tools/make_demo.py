#!/usr/bin/env python3
"""make_demo.py — статичный демо-слепок панели LAN Discovery для публикации.

Собирает HTML-страницы и снимки GET-API через test_client() (без фоновых
потоков — они стартуют только при __main__), выкладывает результат в OUT:

  <страницы>.html, restore.html, api/* (без расширения), static/style.css,
  demo.js, stub.html, 404.html  (заглушки для внешних/битых ссылок)

Секреты вычищаются: api/notes и api/secrets → [], в api/settings вырезаются
ключи token/password/secret и т.п., в wifi-скане — bssid.
MAC-адреса заменяются на случайные (locally administered, детерминированно
от оригинала), MAC-подстроки в именах устройств убираются до нейтральных.
demo.js внедряется в <head> — виджеты шапки (погода, здоровье системы)
получают снимки API сразу при разборе страницы.

Использование (на хосте панели):
  cd /opt/lan-discovery && venv/bin/python tools/make_demo.py /tmp/demo
  Приложение/БД определяются от расположения скрипта (корень репо) —
  работает при любом --prefix, без хардкода /opt.

Режим фикстур для API-адаптера панели (спека §28, core/demo):
  venv/bin/python tools/make_demo.py --fixtures /tmp/demo-fixtures

  Снимает только GET-API (без HTML) с той же санитизацией →
  <dir>/api/<path>.json; панель в demo-режиме (settings.web.demo
  или LAN_DEMO=1, LAN_DEMO_DIR=<dir>) отдаёт их вместо реальных
  данных, страницы рендерятся production-шаблонами.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.parse
from datetime import datetime

# Корень репозитория от скрипта (tools/..) — не хардкод /opt: на стенде
# 2.0 (и при любом --prefix) берём своё приложение и свою devices.db,
# а не чужую установку в /opt/lan-discovery
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --fixtures: только API-снимки (фикстуры core/demo, §28), без HTML
_FIXTURES_MODE = "--fixtures" in sys.argv[1:]
_argv = [a for a in sys.argv[1:] if a != "--fixtures"]
OUT = os.path.abspath(_argv[0] if _argv else "/tmp/demo")
DB = os.path.join(ROOT, "devices.db")
SNAP = datetime.now().strftime("%d.%m.%Y %H:%M")

HTML_PAGES = [
    "/", "/about", "/apps", "/apps/bluetooth", "/apps/disks", "/apps/dlna",
    "/apps/downloads", "/apps/filemanager", "/apps/nettools", "/apps/notes",
    "/apps/passwords", "/apps/terminal", "/apps/upnp", "/apps/wifianalyzer",
    "/currencies", "/help", "/history", "/inventory", "/modules",
    "/monitoring", "/system", "/torrent", "/weather",
]

DROP_KEYS = {
    "token", "password", "password_hash", "secret", "secret_key",
    "api_key", "access_token", "bssid",
}
EMPTY_JSON = {
    "api/notes.json": "[]",
    "api/secrets.json": "[]",
}

# GET-эндпоинты-действия: в демо не вызываем (запуск плеера, сканы, перезагрузка…)
SKIP_API = (
    "/load", "/play", "/stop", "/start", "/scan", "/pair", "/connect",
    "/disconnect", "/remove", "/power", "/mute", "/next", "/prev", "/pause",
    "/random", "/add", "/check", "/prepare", "/transfer", "/verify",
    "/poweroff", "/reboot", "/delete", "/mkdir", "/copy", "/move", "/dismiss",
    "/service/", "/nettools/", "/network/check", "/dns", "/ping", "/ports",
    "/trace", "/file_url", "/discoverable", "/restart", "/read",
)
SKIP_ALLOW = ("/wifi/scan",)  # безопасные исключения

# --- санитайзер MAC-адресов и имён ---
MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}(?::|-)){5}[0-9A-Fa-f]{2}")
BARE12_RE = re.compile(
    r"(?<![0-9A-Za-z_-])[0-9A-Fa-f]{12}(?![0-9A-Za-z_-])")
MAC_MENTION_RE = re.compile(r"\(\s*мак[^)]{0,24}\)", re.I)
NAME_KEYS = {"name", "title"}

# персональные имена в именах устройств → нейтральные (приватность демо)
NAME_OVERRIDES = {
    "Телефон Света new": "Телефон 1",
    "Телефон Света": "Телефон 2",
    "Телефон Тима": "Телефон 3",
    "Леся тел": "Телефон 4",
}
PERSON_RE = re.compile(
    r"\b(?:Света|Светы|Свете|Леся|Леси|Лесе|Тима|Тимы|Тиме|Тиму)\b", re.I)


def clean_name(s):
    """Нейтральное имя: убирает MAC/«мак», персональные имена → оверрайды."""
    s = MAC_RE.sub(" ", s)
    s = BARE12_RE.sub(
        lambda m: " " if re.search(r"[a-fA-F]", m.group()) else m.group(), s)
    s = MAC_MENTION_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"\s+([.,;:!?)])", r"\1", s)
    if s in NAME_OVERRIDES:
        return NAME_OVERRIDES[s]
    s = PERSON_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s if s else "Устройство"


def fake_mac(m):
    """Детерминированно-случайный MAC: local admin, unicast, не повторяется."""
    h = hashlib.md5(("lan-demo:" + m.group()).encode("utf-8")).hexdigest()
    first = format((int(h[0:2], 16) & 0xFC) | 0x02, "02X")
    rest = ":".join(h[i:i + 2] for i in range(2, 12, 2))
    return (first + ":" + rest).upper()


def fake_bare_id(m):
    """Голый 12-hex (job_id и т.п.) → 12 цифр: детерминированно (один и тот же
    токен в events/jobs даёт один фейк), без a-f — demo_lint не считает его
    MAC-фрагментом; буквенная часть исходного токена не восстанавливается."""
    tok = m.group()
    if not re.search(r"[a-fA-F]", tok):
        return tok
    h = hashlib.md5(("lan-demo-id:" + tok).encode("utf-8")).hexdigest()
    return "".join(str(int(ch, 16) % 10) for ch in h[:12])


def build_name_fixes():
    """Старое имя (с MAC) → нейтральное; сканируются колонки name/title БД."""
    fixes = {}
    con = sqlite3.connect(DB)
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        for t in tables:
            try:
                cols = [r[1] for r in con.execute(
                    'pragma table_info("%s")' % t)]
            except Exception:
                continue
            for c in cols:
                if str(c).lower() not in NAME_KEYS:
                    continue
                try:
                    rows = con.execute(
                        'select distinct "%s" from "%s"' % (c, t)).fetchall()
                except Exception:
                    continue
                for (v,) in rows:
                    if isinstance(v, str) and v.strip():
                        n = clean_name(v)
                        if n != v:
                            fixes[v] = n
    finally:
        con.close()
    return fixes


def apply_name_fixes(text, fixes):
    # длинные первыми: «Телефон Света new…» ⊃ «Телефон Света»
    for old, new in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
        if old in text:
            text = text.replace(old, new)
    return text


def scrub_name_keys(obj):
    if isinstance(obj, dict):
        return {k: (clean_name(v)
                    if str(k).lower() in NAME_KEYS and isinstance(v, str)
                    else scrub_name_keys(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_name_keys(x) for x in obj]
    return obj


# --- санитайзер рабочей подсети и паролей (публичный слепок) ---
IP_RE = re.compile(r"\b192\.168\.3\.")
PW_RE = [
    (re.compile(r"(?i)(root\s*/\s*)1234"), r"\1••••"),
    (re.compile(r"(?i)(пароль[^<\n]{0,10}?:?\s*)1234"), r"\1••••"),
    (re.compile(r"(?i)(password['\"]?\s*[:=]\s*['\"])1234"), r"\1••••"),
]


def scrub_net_secrets(s):
    """Рабочая подсеть 192.168.3.x → 192.168.1.x, пароли-литералы → ••••."""
    s = IP_RE.sub("192.168.1.", s)
    for pat, repl in PW_RE:
        s = pat.sub(repl, s)
    return s

BANNER = (
    '<div style="position:fixed;left:0;right:0;bottom:0;background:#12203a;'
    'color:#9fb6e8;font:11px/1.8 system-ui,sans-serif;padding:2px 8px;'
    'z-index:99998;text-align:center">'
    'Демо-режим · слепок от %s · данные не обновляются · '
    '<a href="index.html" style="color:#ffd54f">главная</a></div>' % SNAP
)

STUB_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Заглушка — Демо LAN Discovery</title>
<style>
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
      background:#0e1626;color:#e8eefc;font:16px/1.5 system-ui,sans-serif}
 .card{max-width:520px;padding:32px;background:#17223a;border:1px solid #2b3b5f;
       border-radius:12px;text-align:center}
 h1{font-size:20px;margin:0 0 12px}
 p{color:#9fb6e8;font-size:14px;word-break:break-all}
 code{display:block;margin:10px 0;padding:8px;background:#0e1626;border-radius:6px;
      font-size:12px;color:#ffd54f;max-height:80px;overflow:auto}
 .btn{display:inline-block;margin-top:14px;padding:10px 22px;background:#2f6fd6;
      color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer;
      text-decoration:none}
 .btn:hover{background:#3f7fe6}
</style></head><body><div class="card">
 <h1>🔒 Внешний ресурс недоступен в демо</h1>
 <p>Вы находитесь в статичном демо-режиме: внешние ссылки и действия отключены.</p>
 <code id="u"></code>
 <button class="btn" onclick="back()">← Назад</button>
 <a class="btn" href="index.html" style="background:#374a72">На главную</a>
</div>
<script>
 function q(n){return (location.search.match(new RegExp(n+'=([^&]*)'))||[])[1]||'';}
 var t=q('to'); if(t){document.getElementById('u').textContent=decodeURIComponent(t);}
 else{document.getElementById('u').style.display='none';}
 function back(){ if(history.length>1){history.back();} else {location.href='index.html';} }
</script></body></html>
"""

DEMO_JS = r"""(function(){
 'use strict';
 var STUB='stub.html?to=';
 function toast(msg){
  var d=document.createElement('div');
  d.textContent=msg;
  d.style.cssText='position:fixed;left:50%;bottom:34px;transform:translateX(-50%);'+
   'background:#333;color:#fff;padding:9px 18px;border-radius:8px;z-index:100000;'+
   'font:13px system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.4)';
  document.body.appendChild(d);
  setTimeout(function(){d.remove();},2600);
 }
 window.__demoToast=toast;
 document.addEventListener('click',function(e){
  var a=e.target.closest?e.target.closest('a'):null;
  if(!a)return;
  var href=a.getAttribute('href');
   if(!href||href.charAt(0)==='#'||/^(javascript|mailto|data):/i.test(href))return;
   if(/^logout([?#]|$)/.test(href)){e.preventDefault();toast('Демо-режим: выход отключён');return;}
   if(/^(https?:)?\/\//i.test(href)){e.preventDefault();location.href=STUB+encodeURIComponent(href);return;}
  if(!/\.html([?#]|$)/.test(href)&&!/^stub\.html/.test(href)){
   e.preventDefault();location.href=STUB+encodeURIComponent(href);
  }
 },true);
 document.addEventListener('submit',function(e){
  e.preventDefault();toast('Демо-режим: действия отключены');
 },true);
 var _f=window.fetch;
 if(_f){
  window.fetch=function(input,init){
   var url=typeof input==='string'?input:(input&&input.url)||'';
   var method=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();
   if(method!=='GET'&&method!=='HEAD'){
    toast('Демо-режим: изменения отключены');
    return Promise.resolve(new Response(JSON.stringify({demo:true,message:'demo'}),{
     status:200,headers:{'Content-Type':'application/json'}}));
   }
   var u=url;
   if(u.charAt(0)==='/'&&u.charAt(1)!=='/')u=u.slice(1);
   var q=''; var qi=u.indexOf('?');
   if(qi>=0){q=u.substr(qi);u=u.substr(0,qi);}
   function fallback(){
    return new Response(JSON.stringify({demo:true,data:null}),{
     status:200,headers:{'Content-Type':'application/json'}});
   }
   return _f.call(window,u+'.json'+q,init).then(function(r){
    if(!r.ok)throw new Error('try plain');
    return r;
   }).catch(function(){
    return _f.call(window,u+q,init).then(function(r){
     if(!r.ok)throw new Error('demo 404');
     return r;
    });
   }).catch(fallback);
  };
 }
})();
"""


def route_file(path):
    if path == "/":
        return "index.html"
    return path.strip("/").replace("/", "-") + ".html"


def build_ips():
    con = sqlite3.connect(DB)
    ips = set()
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        for t in tables:
            try:
                cols = [r[1] for r in con.execute("pragma table_info(%s)" % t)]
            except Exception:
                continue
            if "ip" not in cols:
                continue
            try:
                for (v,) in con.execute(
                        "select distinct ip from \"%s\" where ip is not null" % t):
                    if v and re.match(r"^\d+\.\d+\.\d+\.\d+$", str(v)):
                        ips.add(str(v))
            except Exception:
                pass
    finally:
        con.close()
    return sorted(ips)


def api_paths(app, ips):
    out = []
    for rule in app.url_map.iter_rules():
        r = rule.rule
        if not r.startswith("/api/"):
            continue
        if "GET" not in (rule.methods or set()):
            continue
        if any(s in r for s in SKIP_API) and not any(a in r for a in SKIP_ALLOW):
            continue
        if "<" not in r:
            out.append(r)
        elif "<ip>" in r:
            out.extend(r.replace("<ip>", ip) for ip in ips)
    return sorted(set(out))


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()
                if str(k).lower() not in DROP_KEYS}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def write_file(rel, data, binary=False):
    rel = scrub_net_secrets(rel)
    path = os.path.join(OUT, rel)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if binary else "w"
        with open(path, mode, encoding=None if binary else "utf-8") as f:
            f.write(data)
    except OSError as e:
        print("WRITE SKIP", rel, e)


def transform(html, page_map):
    # 0) внешние CDN-ресурсы (xterm, socket.io…) вырезаем — в демо не грузим
    html = re.sub(
        r'<script[^>]+src="https?://[^"]+"[^>]*>\s*</script>', "", html)
    html = re.sub(r'<link[^>]+href="https?://[^"]+"[^>]*>', "", html)
    # 1) ссылка на Emergency Restore Server (порт 8081) → страница рекавери
    html = re.sub(r'href="https?://[^"]*:8081[^"]*"', 'href="restore.html"', html)
    html = re.sub(r"href='https?://[^']*:8081[^']*'", "href='restore.html'", html)
    # 1b) iframe Transmission (внешний :9091) → заглушка
    html = html.replace(
        '"http://" + window.location.hostname + ":9091/transmission/web/"',
        '"stub.html?to=transmission%3A%2F%2Fweb%3A9091"')
    # 2) внешние ссылки → заглушка
    html = re.sub(
        r'href="(https?://[^"]*)"',
        lambda m: 'href="stub.html?to=' + urllib.parse.quote(m.group(1), safe="") + '"',
        html)
    # 3) внутренние href/action → файлы страниц; неизвестные → заглушка
    def _inner(m):
        attr, path = m.group(1), m.group(2)
        pure = "/" + path.split("?")[0].split("#")[0]
        if pure in page_map:
            return '%s="%s"' % (attr, page_map[pure])
        return '%s="stub.html?to=%s"' % (attr, urllib.parse.quote(path, safe=""))
    html = re.sub(r'(href|action)="/([^"]*)"', _inner, html)
    # 4) остатки в JS-литералах и прочих атрибутах → относительные пути
    html = re.sub(r'(["\'])/([A-Za-z])', r'\1\2', html)
    # 5) demo.js в <head> — шим fetch должен работать ДО инлайн-скриптов
    #    страницы (погода/здоровье в шапке грузятся сразу, а не через 60 с)
    m = re.search(r"<head(?:\s[^>]*)?>", html, flags=re.I)
    if m:
        html = (html[:m.end()] + '\n<script src="demo.js"></script>'
                + html[m.end():])
    else:
        html = re.sub(r"</body>", '<script src="demo.js"></script>\n</body>',
                      html, count=1, flags=re.I)
    # 6) плашка
    html = re.sub(r"</body>", BANNER + "</body>", html, count=1, flags=re.I)
    return html


def login(client):
    panel_pass = os.environ.get("LAN_PANEL_PASS", "")
    if not panel_pass:
        print("нет LAN_PANEL_PASS в окружении (пароль администратора панели)")
        sys.exit(1)
    r = client.get("/login")
    html = r.get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    token = m.group(1) if m else ""
    r = client.post(
        "/login",
        data={"username": "admin", "password": panel_pass, "csrf_token": token},
        follow_redirects=True)
    if r.status_code != 200 or "traceback" in r.get_data(as_text=True).lower():
        print("LOGIN FAILED", r.status_code)
        sys.exit(1)
    print("login: ok")


def _sanitize_api_json(fixes):
    """Санитайзер API-снимков: имена, MAC, подсеть, пароли. Версия файлов."""
    n_json = 0
    for dirpath, _, files in os.walk(os.path.join(OUT, "api")):
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                text = open(p, encoding="utf-8").read()
            except OSError as e:
                print("JSON READ SKIP", p, e)
                continue
            try:
                text = json.dumps(scrub_name_keys(json.loads(text)),
                                  ensure_ascii=False)
            except Exception:
                pass
            for old, new in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
                text = text.replace(old, new)
            text = MAC_RE.sub(fake_mac, text)
            text = BARE12_RE.sub(fake_bare_id, text)
            text = scrub_net_secrets(text)
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                n_json += 1
            except OSError as e:
                print("JSON WRITE SKIP", p, e)
    return n_json


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    import app as panel_app

    ips = build_ips()
    print("ips:", len(ips))

    fixes = build_name_fixes()
    print("name fixes:", len(fixes))
    for old, new in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
        print("  name:", old, "->", new)

    client = panel_app.app.test_client()
    login(client)

    # --- страницы (нет в режиме --fixtures: §28 не копирует HTML) ---
    page_map = {}
    pages = list(HTML_PAGES) if not _FIXTURES_MODE else []
    pages += ["/device/" + ip for ip in ips]
    pages += ["/inventory/device/" + ip for ip in ips]
    for path in pages:
        r = client.get(path)
        if r.status_code != 200:
            print("PAGE SKIP", path, r.status_code)
            continue
        body = r.get_data(as_text=True)
        if "traceback" in body.lower():
            print("PAGE TRACEBACK", path)
            continue
        # ключи/значения — уже scrubbed: HTML скрабится до transform,
        # имена файлов скрабятся в write_file
        page_map[scrub_net_secrets(path)] = scrub_net_secrets(route_file(path))
        write_file(page_map[scrub_net_secrets(path)], body)
    print("pages:", len(page_map))

    # --- снимки API ---
    n_api = 0
    for path in api_paths(panel_app.app, ips):
        try:
            r = client.get(path)
        except Exception as e:
            print("API ERR", path, e)
            continue
        if r.status_code != 200:
            continue
        # <path>.json — чтобы не было конфликта «файл vs каталог»
        # (например, api/transmission и api/transmission/queue)
        write_file(path.lstrip("/") + ".json", r.get_data(as_text=True))
        n_api += 1
    print("api snapshots:", n_api)

    if _FIXTURES_MODE:
        # --- только фикстуры: санитайзеры без HTML-этапов ---
        for rel, empty in EMPTY_JSON.items():
            write_file(rel, empty)
        p = os.path.join(OUT, "api", "settings.json")
        if os.path.exists(p):
            try:
                data = json.load(open(p, encoding="utf-8"))
                json.dump(scrub(data), open(p, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
            except Exception as e:
                print("settings scrub err:", e)
        n_json = _sanitize_api_json(fixes)
        print("json sanitized:", n_json)
        n_files = sum(len(files) for _, _, files in os.walk(OUT))
        print("FIXTURES DONE -> %s (%d files), snapshot %s"
              % (OUT, n_files, SNAP))
        return

    # --- слепок Emergency Restore Server ---
    try:
        import restore_server as rs
        rc = rs.app.test_client()
        with rc.session_transaction() as s:
            s["restore_user"] = "admin"
        rr = rc.get("/")
        if rr.status_code == 200:
            write_file("restore.html", rr.get_data(as_text=True))
            print("restore: ok")
        else:
            print("restore skip:", rr.status_code)
        rb = rc.get("/api/backups")
        if rb.status_code == 200:
            write_file("api/backups.json", rb.get_data(as_text=True))
    except Exception as e:
        print("restore err:", e)

    # --- static ---
    src_css = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "static", "style.css")
    if os.path.exists(src_css):
        write_file("static/style.css", open(src_css, encoding="utf-8").read())
        print("static: ok")

    # --- игры (роут /games/<file> отдаёт каталог games/) ---
    games_dir = os.path.join(
        os.path.dirname(os.path.abspath(panel_app.__file__)), "games")
    if os.path.isdir(games_dir):
        shutil.copytree(games_dir, os.path.join(OUT, "games"),
                        dirs_exist_ok=True)
        for dirpath, _, files in os.walk(games_dir):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, games_dir).replace(os.sep, "/")
                page_map["/games/" + rel] = "games/" + rel
        print("games: ok")

    # --- санитайзер секретов ---
    for rel, empty in EMPTY_JSON.items():
        write_file(rel, empty)
    p = os.path.join(OUT, "api", "settings.json")
    if os.path.exists(p):
        try:
            data = json.load(open(p, encoding="utf-8"))
            json.dump(scrub(data), open(p, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print("settings scrubbed")
        except Exception as e:
            print("settings scrub err:", e)

    # --- санитайзер API-снимков: имена + случайные MAC ---
    print("json sanitized:", _sanitize_api_json(fixes))

    # --- трансформация всех HTML ---
    for fname in list(os.listdir(OUT)):
        if not fname.endswith(".html"):
            continue
        path = os.path.join(OUT, fname)
        html = open(path, encoding="utf-8").read()
        html = apply_name_fixes(html, fixes)
        html = MAC_RE.sub(fake_mac, html)
        html = BARE12_RE.sub(fake_bare_id, html)
        html = scrub_net_secrets(html)
        html = transform(html, page_map)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    # --- заглушки ---
    write_file("stub.html", STUB_HTML)
    write_file("404.html", STUB_HTML)
    write_file("demo.js", DEMO_JS)

    n_files = sum(len(files) for _, _, files in os.walk(OUT))
    print("DONE -> %s (%d files), snapshot %s" % (OUT, n_files, SNAP))


if __name__ == "__main__":
    main()
