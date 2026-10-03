"""Санитайзинг docs/ для публичного репозитория Lan-discovery-docs.

Читает <repo>/docs/*.md, вычищает чувствительные данные (рабочие IP,
пароли, системные пользователи), ссылки на wiki приватного репозитория
меняет на относительные, пишет результат в temp-каталог docs-public
(плюс README.md = Home.md, плюс LICENSE без изменений). Затем:
git init + push в публичный репозиторий.

Проверка: regex по 192.168.3.*, 1234, kot:kot, /wiki/ — должно быть 0.
"""
import pathlib
import re
import shutil

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "docs"
DST = pathlib.Path(r"C:\Users\Lenovo\AppData\Local\Temp\opencode\docs-public")

DST.mkdir(parents=True, exist_ok=True)
for p in DST.iterdir():
    if p.name == ".git":          # remote/ветка сохраняются для push
        continue
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()

# Проприетарная лицензия проекта — копируется дословно
shutil.copy(REPO / "LICENSE", DST / "LICENSE")

REPL = [
    ("192.168.3.0/24", "192.168.1.0/24"),
    ("192.168.3.243", "192.168.1.10"),
    ("192.168.3.234", "192.168.1.10"),
    ("192.168.3.235", "192.168.1.11"),
    ("192.168.3.1", "192.168.1.1"),
    ("`192.168.3.7`", "`192.168.1.20`"),
    ("`192.168.3.236`", "`192.168.1.21`"),
    ("`192.168.3.239`", "`192.168.1.22`"),
    ("`192.168.3.51`", "`192.168.1.30`"),
    ("работать по `.235`", "работать по IP WiFi-точки доступа"),
    ("(пароль: 1234)", "(пароль — свой, заданный при установке)"),
    ("пароль `1234`", "пароль — свой"),
    ("(пароль 1234)", "(пароль — свой)"),
    ("kot:kot", "<пользователь>:<группа>"),
    ("`admin` / `1234`", "`admin` / `<заданный при установке>`"),
    # Дочистка остаточных/шаблонных IP рабочей подсети (в т.ч. 192.168.3.x)
    ("192.168.3.", "192.168.1."),
    ("`/wiki/` оставался", "wiki-раздел оставался"),
]

WIKI = "https://github.com/kotmartovskiy/Lan-discovery-ARM/wiki/"


def _wiki_to_rel(text: str) -> str:
    """Любая wiki-ссылка на существующую страницу docs/ → относительная."""
    def repl(m):
        page = m.group(1)
        return page + ".md" if (SRC / (page + ".md")).exists() else m.group(0)
    return re.sub(
        re.escape(WIKI) + r"([^)\s#\"']+)", repl, text)


def sanitize(text: str) -> str:
    for old, new in REPL:
        text = text.replace(old, new)
    # Дочистка: оставшиеся токены «1234» (исторические описания «root/1234»,
    # «admin/1234» в журналах) — после красивых REPL-вариантов выше
    text = re.sub(r"\b1234\b", "<пароль>", text)
    text = _wiki_to_rel(text)
    text = text.replace(
        "- **Репозиторий:** https://github.com/kotmartovskiy/Lan-discovery-ARM\n",
        "- **Исходный код:** в закрытом репозитории проекта\n",
    )
    return text.replace("wiki: `/wiki`.", "документация: этот репозиторий.")


def emit(src: pathlib.Path, dst: pathlib.Path):
    """Санитизованная копия одного файла (dst может быть в подпапке)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = sanitize(src.read_text(encoding="utf-8"))
    dst.write_text(text, encoding="utf-8", newline="\n")


for f in sorted(SRC.glob("*.md")):
    name = "Оглавление.md" if f.name == "_Sidebar.md" else f.name
    emit(f, DST / name)

# Набор 2.0 (docs/2.0/*.md) — та же санитизация, отдельная подпапка;
# иначе UPGRADE/INSTALL и прочие доки 2.0 не доезжают до публичного репо
for f in sorted((SRC / "2.0").glob("*.md")):
    emit(f, DST / "2.0" / f.name)

(DST / "README.md").write_text(
    (DST / "Home.md").read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
)

leaks = []
for f in DST.rglob("*.md"):
    t = f.read_text(encoding="utf-8")
    for pat in (r"192\.168\.3\.", r"\b1234\b", r"kot:kot", r"/wiki/"):
        for m in re.finditer(pat, t):
            line = t[: m.start()].count("\n") + 1
            rel = f.relative_to(DST)
            leaks.append(f"{rel}:{line}: {t.splitlines()[line - 1].strip()[:100]}")

print(f"files: {len(list(DST.rglob('*.md')))} -> {DST}")
print(f"leaks: {len(leaks)}")
for l in leaks:
    print("  ", l)
raise SystemExit(1 if leaks else 0)
