#!/usr/bin/env python3
"""demo_lint.py — проверка демо-слепка перед публикацией.

Проверяет папку demo/:
  1) все ссылки href/src/action ведут на существующие файлы (или заглушку);
  2) нет абсолютных путей («/...») и внешних URL в атрибутах;
  3) в JS нет fetch("...") с абсолютными путями;
  4) секреты вычищены (notes/secrets пусты, в settings нет token/password);
  5) каркасные файлы на месте (index, restore, stub, 404, demo.js, static);
   6) MAC-адреса заменены на случайные (local admin), в именах нет
      MAC-фрагментов; вкладки index ведут на страницы снимка; главные
      разделы присутствуют файлами; demo.js в <head>.

Запуск: python tools/demo_lint.py <папка-demo>
Код 0 = чисто, 1 = найдены ошибки.
"""
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "demo")

ATTR_RE = re.compile(
    r'(?:href|src|action)\s*=\s*["\']([^"\']*)["\']', re.I)
FETCH_ABS_RE = re.compile(r"""fetch\(\s*["']/""")
XHR_ABS_RE = re.compile(r"""\.open\(\s*["'][A-Z]+["']\s*,\s*["']/""")
EXT_ATTR_RE = re.compile(r'^https?://', re.I)
MAC_RE = re.compile(r'(?:[0-9A-Fa-f]{2}(?::|-)){5}[0-9A-Fa-f]{2}')
BARE12_RE = re.compile(
    r'(?<![0-9A-Za-z_-])[0-9A-Fa-f]{12}(?![0-9A-Za-z_-])')
# персональные имена не должны попадать в публичный слепок
PERSON_RE = re.compile(
    r'(?<![А-Яа-яЁё])(?:Света|Светы|Леся|Леси|Тима|Тимы)(?![А-Яа-яЁё])', re.I)

# вкладки главной (должны вести на страницы снимка, а не на заглушку).
# Навигация шелла 2.0: index/history/apps/system/modules (+ restore ниже)
INDEX_TABS = (
    "index.html", "history.html", "apps.html", "system.html", "modules.html",
)
# главные разделы обязаны присутствовать файлами (в 2.0 к ним ведут
# ссылки из разделов, а не табы index — полнота слепка проверяется явно)
REQUIRED_PAGES = INDEX_TABS + (
    "restore.html", "inventory.html", "monitoring.html", "torrent.html",
    "currencies.html", "weather.html", "about.html", "help.html",
)

errors = []
warns = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def check_files():
    for f in ("stub.html", "404.html", "demo.js",
              os.path.join("static", "style.css")):
        if not os.path.exists(os.path.join(ROOT, f)):
            err("нет каркасного файла: " + f)
    for f in REQUIRED_PAGES:
        if not os.path.exists(os.path.join(ROOT, f)):
            err("нет обязательной страницы: " + f)


def check_secrets():
    for rel, expect_empty in (("api/notes.json", True),
                              ("api/secrets.json", True),
                              ("api/settings.json", False)):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            err("нет " + rel)
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            err("%s: не JSON (%s)" % (rel, e))
            continue
        if expect_empty and data != []:
            err("%s: должен быть пустым []" % rel)
        if isinstance(data, dict):
            blob = json.dumps(data).lower()
            for k in ("token", "password_hash", "secret_key", "api_key",
                      "access_token", "bssid"):
                if '"%s"' % k in blob:
                    err("%s: остался ключ %s" % (rel, k))
    # нигде в api не должно быть хешей паролей
    api_dir = os.path.join(ROOT, "api")
    for dirpath, _, files in os.walk(api_dir):
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                text = open(p, encoding="utf-8").read()
            except Exception:
                continue
            if "password_hash" in text or "$2b$" in text:
                err("утечка hash в " + os.path.relpath(p, ROOT))


def check_stub_target(rel, url):
    """Внутренняя ссылка не должна уходить в заглушку, если есть страница."""
    target = urllib.parse.unquote(url.split("to=", 1)[1])
    if not target or target.startswith(("http://", "https://", "//")):
        return
    full = target if target.startswith("/") else "/" + target
    if full.startswith("/games/"):
        f = full[1:]
    elif full == "/":
        f = "index.html"
    else:
        f = full.strip("/").replace("/", "-") + ".html"
    if os.path.exists(os.path.join(ROOT, f)):
        err("%s: внутренняя ссылка ушла в заглушку: %s → %s"
            % (rel, target, f))


def check_privacy():
    """MAC-адреса обязаны быть фейковыми, без MAC-фрагментов в именах."""
    n_macs = 0
    for dirpath, _, files in os.walk(ROOT):
        for fn in files:
            if not (fn.endswith(".html") or fn.endswith(".json")):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT)
            try:
                text = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for m in MAC_RE.finditer(text):
                n_macs += 1
                first = int(m.group()[0:2], 16)
                if (first & 0x03) != 0x02:
                    err("%s: настоящий MAC не заменён: %s" % (rel, m.group()))
            for m in BARE12_RE.finditer(text):
                tok = m.group()
                if re.search(r"[a-fA-F]", tok):
                    err("%s: MAC-фрагмент в тексте/имени: %s" % (rel, tok))
            for m in PERSON_RE.finditer(text):
                err("%s: персональное имя в слепке: %s" % (rel, m.group()))
    print("macs checked:", n_macs)


def check_net_secrets():
    """Рабочая подсеть и пароли не должны попадать в публичный слепок."""
    net_re = re.compile(r"192\.168\.3\.")
    pw_re = re.compile(
        r"(?i)(?:root\s*/\s*1234|пароль[^<\n]{0,12}?:?\s*1234"
        r"|password['\"]?\s*[:=]\s*['\"]1234)")
    n = 0
    for dirpath, _, files in os.walk(ROOT):
        for fn in files:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT)
            if net_re.search(rel.replace(os.sep, "/")):
                n += 1
                err("имя файла содержит рабочую подсеть: " + rel)
            if not (fn.endswith(".html") or fn.endswith(".json")
                    or fn.endswith(".js") or fn.endswith(".css")):
                continue
            try:
                text = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for m in net_re.finditer(text):
                n += 1
                line = text[:m.start()].count("\n") + 1
                err("%s:%d: рабочая подсеть 192.168.3.x: %s"
                    % (rel, line, text.splitlines()[line - 1].strip()[:100]))
            for m in pw_re.finditer(text):
                n += 1
                line = text[:m.start()].count("\n") + 1
                err("%s:%d: пароль-литерал: %s"
                    % (rel, line, text.splitlines()[line - 1].strip()[:100]))
    print("net/secret leaks checked:", n)


def check_html():
    n_html = 0
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d != "api"]
        for fn in files:
            if not fn.endswith(".html"):
                continue
            n_html += 1
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            html = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            for m in ATTR_RE.finditer(html):
                url = m.group(1).strip()
                if not url or url.startswith(("#", "javascript:", "mailto:",
                                              "data:")):
                    continue
                if EXT_ATTR_RE.match(url):
                    err("%s: внешняя ссылка в атрибуте: %s" % (rel, url[:90]))
                    continue
                if url.startswith("/"):
                    err("%s: абсолютный путь: %s" % (rel, url[:90]))
                    continue
                if url.startswith("//"):
                    err("%s: протокол-относительная: %s" % (rel, url[:90]))
                    continue
                if url.startswith("stub.html?to="):
                    check_stub_target(rel, url)
                pure = url.split("#")[0].split("?")[0]
                if not pure:
                    continue
                if pure.endswith(".html") or "/" not in pure or pure.split(
                        "/")[0] in ("static", "api", "stub.html"):
                    target = os.path.join(ROOT, pure)
                    if pure.endswith(".html") and not os.path.exists(target):
                        err("%s: битая ссылка → %s" % (rel, pure))
            for m in FETCH_ABS_RE.finditer(html):
                err("%s: fetch с абсолютным путём: %s" % (rel, m.group(0)))
            for m in XHR_ABS_RE.finditer(html):
                err("%s: XHR с абсолютным путём: %s" % (rel, m.group(0)))
            if 'src="http' in html or "src='http" in html:
                err("%s: внешний скрипт/картинка" % rel)
            # навигационные ссылки-обязанности
            if fn == "index.html":
                if 'href="restore.html"' not in html:
                    err("index.html: нет ссылки на restore.html")
                if "Демо-режим" not in html:
                    err("index.html: нет плашки «Демо-режим»")
                for t in INDEX_TABS:
                    if 'href="%s"' % t not in html:
                        err("index.html: вкладка ведёт не на страницу: " + t)
                low = html.lower()
                i_js = low.find('src="demo.js"')
                i_head = low.find("</head>")
                if i_js < 0 or (0 <= i_head < i_js):
                    err("index.html: demo.js не в <head> — виджеты шапки "
                        "не получат данные API")
    print("html checked:", n_html)


def main():
    if not os.path.isdir(ROOT):
        print("нет папки", ROOT)
        return 1
    check_files()
    check_secrets()
    check_html()
    check_privacy()
    check_net_secrets()
    n = sum(len(f) for _, _, f in os.walk(ROOT))
    for w in warns:
        print("WARN:", w)
    if errors:
        print("ERRORS: %d" % len(errors))
        for e in errors[:60]:
            print(" -", e)
        if len(errors) > 60:
            print(" ... и ещё", len(errors) - 60)
        return 1
    print("LINT OK: %d files in %s" % (n, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
