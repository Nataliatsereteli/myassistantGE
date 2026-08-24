#!/usr/bin/env python3
"""
Генератор статического сайта.

    python build.py            # собрать в ./_site
    python build.py --serve    # собрать и поднять локальный сервер на :8000
    python build.py --check    # только проверить контент, ничего не собирать

Идея: весь текст и все настройки лежат в ./content/*.yml.
Шаблоны (./templates) и стили (./assets) трогает только разработчик.
Чтобы поменять текст, цену или телефон — правится YAML, всё остальное
пересобирается автоматически.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Консоль Windows по умолчанию не в UTF-8, а в сообщениях сборки есть кириллица
# и типографские символы. Без этого сборка падает на печати, а не на контенте.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
ASSETS = ROOT / "assets"
OUT = ROOT / "_site"

PLACEHOLDER_RE = re.compile(r"(TODO|ЗАПОЛНИТЬ|XXXXXXX|example\.com)", re.I)

warnings: list[str] = []
errors: list[str] = []


# --------------------------------------------------------------------------- #
#  Загрузка контента                                                          #
# --------------------------------------------------------------------------- #

def load_yaml(path: Path) -> Any:
    if not path.exists():
        errors.append(f"Нет файла {path.relative_to(ROOT)}")
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Ошибка YAML в {path.relative_to(ROOT)}:\n    {exc}")
        return {}


def apply_env_overrides(site: dict[str, Any]) -> None:
    """
    Переопределение настроек через переменные окружения. Нужно для превью:
    один и тот же контент можно собрать под рабочий домен и под тестовый,
    не правя content/site.yml.

        SITE_URL=https://demo.example.com  — адрес, который попадёт
            в canonical, hreflang, sitemap и ссылки на картинки соцсетей
        SITE_BASE_PATH=/podpapka       — если превью лежит в подпапке
        SITE_ALLOW_INDEXING=false      — закрыть превью от поисковиков
        SITE_CUSTOM_DOMAIN=            — пустое значение уберёт файл CNAME
    """
    mapping = {
        "SITE_URL": "url",
        "SITE_BASE_PATH": "base_path",
        "SITE_CUSTOM_DOMAIN": "custom_domain",
    }
    for env, key in mapping.items():
        if env in os.environ:
            site[key] = os.environ[env]
    if "SITE_ALLOW_INDEXING" in os.environ:
        site["allow_indexing"] = os.environ["SITE_ALLOW_INDEXING"].strip().lower() in ("1", "true", "yes")


def load_content() -> dict[str, Any]:
    site = load_yaml(CONTENT / "site.yml")
    apply_env_overrides(site)
    data = {
        "site": site,
        "contacts": load_yaml(CONTENT / "contacts.yml"),
        "pricing": load_yaml(CONTENT / "pricing.yml"),
        "services": load_yaml(CONTENT / "services.yml"),
        "i18n": {},
    }
    for lang in site.get("languages", []):
        data["i18n"][lang] = load_yaml(CONTENT / "i18n" / f"{lang}.yml")
    return data


# --------------------------------------------------------------------------- #
#  Помощники: цены, ссылки, контакты                                          #
# --------------------------------------------------------------------------- #

class Money:
    """Единый формат сумм. Цифры живут в pricing.yml, подписи — в i18n."""

    def __init__(self, pricing: dict[str, Any]):
        cur = pricing.get("currency", {})
        self.symbol = cur.get("symbol", "₾")
        self.code = cur.get("code", "GEL")
        self.template = cur.get("format", "{value} {symbol}")
        self.thousands = cur.get("thousands_separator", " ")

    def n(self, value: float | int) -> str:
        value = int(value) if float(value).is_integer() else value
        s = f"{value:,}".replace(",", self.thousands)
        return s

    def one(self, value: float | int) -> str:
        return self.template.format(value=self.n(value), symbol=self.symbol)

    def range(self, lo: float | int, hi: float | int | None = None) -> str:
        if hi is None or hi == lo:
            return self.one(lo)
        return self.template.format(value=f"{self.n(lo)}–{self.n(hi)}", symbol=self.symbol)

    def percent(self, lo: float | int, hi: float | int | None = None) -> str:
        return f"+{self.n(lo)}–{self.n(hi)}%" if hi else f"+{self.n(lo)}%"


def normalise_phone(raw: str) -> str:
    """+995 555 12 34 56 -> 995555123456 (для tel:/wa.me)."""
    return re.sub(r"\D", "", raw or "")


def build_contact_links(contacts: dict[str, Any], ui: dict[str, Any]) -> list[dict[str, str]]:
    """
    Готовый список каналов связи — используется и в подвале, и на /contact/.
    Каналы с незаполненным значением не выводятся: лучше показать три рабочих
    контакта, чем четвёртый со словом TODO. Сборка при этом всё равно
    предупредит, что заглушка осталась.
    """
    def filled(value: str) -> bool:
        return bool(value) and not PLACEHOLDER_RE.search(str(value))

    out: list[dict[str, str]] = []
    phone = contacts.get("phone", "")
    if not filled(phone):
        phone = ""
    if phone:
        out.append({"key": "phone", "label": ui.get("phone", "Телефон"),
                    "value": phone, "href": "tel:+" + normalise_phone(phone), "icon": "phone"})
    wa = contacts.get("whatsapp") or phone
    if wa and contacts.get("show_whatsapp", True):
        digits = normalise_phone(wa)
        text = contacts.get("whatsapp_prefill", {}).get("__use_i18n__") or ""
        href = f"https://wa.me/{digits}"
        if text:
            href += "?text=" + text
        out.append({"key": "whatsapp", "label": "WhatsApp", "value": phone or wa,
                    "href": href, "icon": "whatsapp"})
    tg = contacts.get("telegram")
    if filled(tg):
        handle = tg.lstrip("@")
        out.append({"key": "telegram", "label": "Telegram", "value": "@" + handle,
                    "href": f"https://t.me/{handle}", "icon": "telegram"})
    email = contacts.get("email")
    if filled(email):
        out.append({"key": "email", "label": "Email", "value": email,
                    "href": "mailto:" + email, "icon": "mail"})
    return out


# --------------------------------------------------------------------------- #
#  Маршруты                                                                   #
# --------------------------------------------------------------------------- #

def page_slug(tr: dict[str, Any], page: str) -> str:
    return (tr.get("pages", {}).get(page, {}) or {}).get("slug", page)


def base_path(site: dict[str, Any]) -> str:
    """
    Префикс, если сайт живёт не в корне домена (например, на GitHub Pages
    по адресу username.github.io/repo или в подпапке на своём сервере).
    Пустая строка = сайт в корне, обычный случай.
    """
    raw = (site.get("base_path") or "").strip().strip("/")
    return f"/{raw}" if raw else ""


def build_routes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Возвращает плоский список страниц.
    key — общий для всех языков идентификатор, по нему строятся hreflang-связки
    и переключатель языка.
    """
    langs: list[str] = data["site"].get("languages", [])
    services: list[dict[str, Any]] = data["services"].get("items", [])
    prefix = base_path(data["site"])
    routes: list[dict[str, Any]] = []

    for lang in langs:
        tr = data["i18n"].get(lang, {})
        base = f"{prefix}/{lang}/"
        svc_seg = page_slug(tr, "services")

        routes.append({"key": "home", "lang": lang, "url": base,
                       "template": "home.html", "priority": "1.0", "changefreq": "weekly"})

        routes.append({"key": "services", "lang": lang, "url": f"{base}{svc_seg}/",
                       "template": "services.html", "priority": "0.9", "changefreq": "monthly"})

        for svc in services:
            sid = svc["id"]
            s_tr = tr.get("services", {}).get(sid, {})
            slug = s_tr.get("slug", sid)
            routes.append({
                "key": f"service:{sid}", "lang": lang,
                "url": f"{base}{svc_seg}/{slug}/",
                "template": "service.html", "service": svc,
                "priority": "0.8", "changefreq": "monthly",
            })

        for page, tpl, prio in [
            ("prices", "prices.html", "0.8"),
            ("about", "about.html", "0.7"),
            ("contact", "contact.html", "0.8"),
            ("faq", "faq.html", "0.6"),
            ("privacy", "text.html", "0.2"),
            ("terms", "text.html", "0.2"),
            ("thanks", "thanks.html", "0.0"),
        ]:
            if page not in tr.get("pages", {}):
                continue
            routes.append({"key": page, "lang": lang, "url": f"{base}{page_slug(tr, page)}/",
                           "template": tpl, "page": page, "priority": prio,
                           "changefreq": "yearly" if prio == "0.2" else "monthly",
                           "noindex": page == "thanks"})
    return routes


# --------------------------------------------------------------------------- #
#  Проверка контента                                                          #
# --------------------------------------------------------------------------- #

def walk_strings(node: Any, path: str = ""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def check_content(data: dict[str, Any]) -> None:
    site = data["site"]
    langs = site.get("languages", [])

    if not langs:
        errors.append("content/site.yml: не задан список languages")
    if site.get("default_language") not in langs:
        errors.append("content/site.yml: default_language должен быть одним из languages")
    if not site.get("url"):
        errors.append("content/site.yml: не задан url (полный адрес сайта)")

    # Все языки должны описывать один и тот же набор услуг.
    service_ids = {s["id"] for s in data["services"].get("items", [])}
    for lang in langs:
        tr = data["i18n"].get(lang, {})
        if not tr:
            errors.append(f"Пустой или отсутствующий перевод: content/i18n/{lang}.yml")
            continue
        described = set(tr.get("services", {}).keys())
        missing = service_ids - described
        extra = described - service_ids
        if missing:
            errors.append(f"[{lang}] нет описаний услуг: {', '.join(sorted(missing))}")
        if extra:
            warnings.append(f"[{lang}] описаны услуги, которых нет в services.yml: {', '.join(sorted(extra))}")

        # Уникальность слагов внутри языка — иначе страницы перезапишут друг друга.
        slugs: dict[str, str] = {}
        for sid, s in tr.get("services", {}).items():
            slug = s.get("slug", sid)
            if slug in slugs:
                errors.append(f"[{lang}] одинаковый slug '{slug}' у услуг {slugs[slug]} и {sid}")
            slugs[slug] = sid

        for where, text in walk_strings(tr, f"i18n/{lang}"):
            if PLACEHOLDER_RE.search(text):
                warnings.append(f"Не заполнено: {where} → «{text[:70]}»")

    # Все ценовые ключи услуг должны существовать в pricing.yml.
    packages = data["pricing"].get("packages", {})
    for s in data["services"].get("items", []):
        if s.get("price") and s["price"] not in packages:
            errors.append(f"services.yml: услуга {s['id']} ссылается на цену "
                          f"'{s['price']}', которой нет в pricing.yml")

    for where, text in walk_strings(data["contacts"], "contacts"):
        if PLACEHOLDER_RE.search(text):
            warnings.append(f"Не заполнено: {where} → «{text[:70]}»")

    for where, text in walk_strings(site, "site"):
        if PLACEHOLDER_RE.search(text):
            warnings.append(f"Не заполнено: {where} → «{text[:70]}»")

    # Реквизиты нужны публичной оферте: без них документ юридически слаб.
    # Это предупреждение, а не ошибка: сайт должен собираться и публиковаться
    # даже пока выписка из реестра не под рукой.
    legal = site.get("legal", {}) or {}
    for key, what in (("entity_name", "наименование из выписки"),
                      ("tax_id", "идентификационный код")):
        if not str(legal.get(key) or "").strip():
            warnings.append(f"content/site.yml: legal.{key} не заполнен "
                            f"({what}) — нужен для публичной оферты")


# --------------------------------------------------------------------------- #
#  Сборка                                                                     #
# --------------------------------------------------------------------------- #

def asset_hash(*paths: Path) -> str:
    h = hashlib.sha1()
    for p in paths:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


def jinja_env(data: dict[str, Any]) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    money = Money(data["pricing"])
    env.filters["money"] = money.one
    env.globals["money"] = money
    env.globals["price_range"] = money.range
    env.globals["price_percent"] = money.percent
    env.filters["digits"] = normalise_phone

    def tel(raw: str) -> str:
        return "tel:+" + normalise_phone(raw)

    env.filters["tel"] = tel
    env.globals["now_year"] = date.today().year
    env.globals["today"] = date.today().isoformat()
    return env


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def copy_assets() -> None:
    dst = OUT / "assets"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ASSETS, dst)


def render_site(data: dict[str, Any]) -> int:
    site = data["site"]
    langs = site.get("languages", [])
    base_url = site.get("url", "").rstrip("/")
    env = jinja_env(data)
    routes = build_routes(data)

    # key -> {lang: url} для hreflang и переключателя языков
    alt: dict[str, dict[str, str]] = {}
    for r in routes:
        alt.setdefault(r["key"], {})[r["lang"]] = r["url"]

    prefix = base_path(site)
    version = asset_hash(
        ASSETS / "css" / "site.css",
        ASSETS / "js" / "site.js",
        ASSETS / "fonts" / "fonts.css",
    )

    # Чистим содержимое, а не сам каталог: пока работает `--serve`,
    # каталог _site открыт сервером и удалить его целиком нельзя.
    OUT.mkdir(parents=True, exist_ok=True)
    for item in OUT.iterdir():
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    written = 0
    for r in routes:
        lang = r["lang"]
        tr = data["i18n"][lang]
        ui = tr.get("ui", {})
        ctx = {
            "site": site,
            "contacts": data["contacts"],
            "contact_links": build_contact_links(data["contacts"], ui),
            "pricing": data["pricing"],
            "services": data["services"].get("items", []),
            "t": tr,
            "ui": ui,
            "lang": lang,
            "langs": langs,
            "route": r,
            "url": r["url"],
            "canonical": base_url + r["url"],
            "alternates": {l: base_url + u for l, u in alt.get(r["key"], {}).items()},
            "alt_paths": alt.get(r["key"], {}),
            "nav": build_nav(tr, data["services"].get("items", []), lang, prefix),
            "asset_v": version,
            "base_url": base_url,
            "base": prefix,
            "seo": build_seo(r, tr, site),
            "breadcrumbs": build_breadcrumbs(r, tr, lang, prefix),
            "faq_items": collect_faq(r, tr),
            "preload_font": PRELOAD_FONT.get(lang, PRELOAD_FONT["en"]),
        }
        if "service" in r:
            sid = r["service"]["id"]
            ctx["service"] = r["service"]
            ctx["svc"] = tr["services"][sid]
            ctx["svc_price"] = data["pricing"].get("packages", {}).get(r["service"].get("price"), {})
        if "page" in r:
            ctx["page"] = tr["pages"][r["page"]]
            ctx["page_key"] = r["page"]

        html = env.get_template(r["template"]).render(**ctx)
        out_rel = r["url"][len(prefix):] if prefix else r["url"]
        write(OUT / out_rel.strip("/") / "index.html", html)
        written += 1

    # --- манифест на каждый язык -------------------------------------------
    # Раньше это был статический файл в assets: он не знал ни про base_path,
    # ни про язык, и всегда указывал start_url на русскую главную.
    for lang in langs:
        tr = data["i18n"][lang]
        write(OUT / lang / "site.webmanifest", env.get_template("site.webmanifest").render(
            site=site, t=tr, lang=lang, base=prefix))

    # --- корневая страница: определяет язык и уводит на нужную версию -------
    default_lang = site.get("default_language", langs[0] if langs else "en")
    root_ctx = {
        "site": site, "langs": langs, "default_lang": default_lang,
        "base_url": base_url, "asset_v": version,
        "base": prefix,
        "titles": {l: data["i18n"][l].get("meta", {}) for l in langs},
        "home_urls": alt.get("home", {}),
        "x_default": site.get("x_default", default_lang),
    }
    write(OUT / "index.html", env.get_template("root.html").render(**root_ctx))
    written += 1

    # --- 404: GitHub Pages отдаёт его на любой несуществующий адрес ---------
    l404 = site.get("x_default", default_lang)
    tr404 = data["i18n"][l404]
    route404 = {"key": "notfound", "lang": l404, "url": "/404.html", "noindex": True}
    write(OUT / "404.html", env.get_template("404.html").render(
        site=site, t=tr404, ui=tr404.get("ui", {}), lang=l404, langs=langs,
        contacts=data["contacts"], contact_links=build_contact_links(data["contacts"], tr404.get("ui", {})),
        nav=build_nav(tr404, data["services"].get("items", []), l404, prefix),
        asset_v=version, base_url=base_url, home_urls=alt.get("home", {}),
        alt_paths=alt.get("home", {}), url="/404.html", canonical=base_url + "/404.html",
        alternates={}, services=data["services"].get("items", []), pricing=data["pricing"],
        route=route404, breadcrumbs=[], faq_items=[], base=prefix,
        preload_font=PRELOAD_FONT.get(l404, PRELOAD_FONT["en"]),
        seo={"title": tr404["pages"]["notfound"]["title"] + " — " + site["brand"]["name"],
             "description": tr404["pages"]["notfound"]["summary"], "og_type": "website"},
    ))
    written += 1

    # --- sitemap.xml с hreflang-связками ------------------------------------
    write(OUT / "sitemap.xml", env.get_template("sitemap.xml").render(
        routes=[r for r in routes if not r.get("noindex")],
        alt=alt, base_url=base_url, today=date.today().isoformat(),
        x_default=site.get("x_default", default_lang),
    ))

    # --- robots.txt ---------------------------------------------------------
    write(OUT / "robots.txt", env.get_template("robots.txt").render(
        base_url=base_url, base=prefix, allow_indexing=site.get("allow_indexing", True)))

    # --- служебные файлы ----------------------------------------------------
    (OUT / ".nojekyll").write_text("", encoding="utf-8")  # не запускать Jekyll на GitHub Pages
    if site.get("custom_domain"):
        (OUT / "CNAME").write_text(site["custom_domain"] + "\n", encoding="utf-8")

    copy_assets()
    return written


PRELOAD_FONT = {
    "ru": "golos-text-cyrillic-400-normal.woff2",
    "en": "golos-text-latin-400-normal.woff2",
    "ka": "noto-sans-georgian-georgian-400-normal.woff2",
}


def build_seo(route: dict[str, Any], tr: dict[str, Any], site: dict[str, Any]) -> dict[str, str]:
    """
    Title и description для каждой страницы берутся из контента, а не из шаблонов.
    Если seo_title не задан, собираем «Заголовок — Бренд» автоматически.
    """
    brand = site["brand"]["name"]
    key = route["key"]

    if key.startswith("service:"):
        node = tr["services"][key.split(":", 1)[1]]
        og = "article"
    elif "page" in route:
        node = tr["pages"][route["page"]]
        og = "website"
    elif key == "services":
        node = tr["pages"]["services"]
        og = "website"
    else:
        node = tr["pages"]["home"]
        og = "website"

    title = node.get("seo_title") or f"{node.get('title', brand)} — {brand}"
    desc = node.get("seo_description") or node.get("summary") or tr["meta"]["description"]
    return {"title": title.strip(), "description": " ".join(desc.split()), "og_type": og}


def build_breadcrumbs(route: dict[str, Any], tr: dict[str, Any], lang: str,
                      prefix: str = "") -> list[dict[str, str]]:
    home = {"title": tr["ui"]["home"], "url": f"{prefix}/{lang}/"}
    key = route["key"]
    if key == "home":
        return []
    services_page = tr["pages"]["services"]
    svc_url = f"{prefix}/{lang}/{services_page['slug']}/"
    if key == "services":
        return [home, {"title": services_page["nav_title"], "url": svc_url}]
    if key.startswith("service:"):
        node = tr["services"][key.split(":", 1)[1]]
        return [home,
                {"title": services_page["nav_title"], "url": svc_url},
                {"title": node["name"], "url": f"{svc_url}{node['slug']}/"}]
    page = tr["pages"].get(route.get("page", key), {})
    return [home, {"title": page.get("nav_title") or page.get("title", key),
                   "url": f"{prefix}/{lang}/{page.get('slug', key)}/"}]


def collect_faq(route: dict[str, Any], tr: dict[str, Any]) -> list[dict[str, str]]:
    """Вопросы для микроразметки FAQPage. Гугл показывает её в выдаче."""
    key = route["key"]
    if key.startswith("service:"):
        return tr["services"][key.split(":", 1)[1]].get("faq", []) or []
    if key == "faq":
        return tr["pages"]["faq"].get("items", []) or []
    if key == "home":
        return (tr["home"].get("faq", {}) or {}).get("items", []) or []
    return []


def build_nav(tr: dict[str, Any], services: list[dict[str, Any]], lang: str,
              prefix: str = "") -> list[dict[str, str]]:
    """Верхнее меню собирается из pages.*.nav_title, порядок задан здесь."""
    order = ["services", "prices", "about", "faq", "contact"]
    out = []
    for key in order:
        page = tr.get("pages", {}).get(key)
        if not page:
            continue
        out.append({
            "key": key,
            "title": page.get("nav_title") or page.get("title", key),
            "url": f"{prefix}/{lang}/{page.get('slug', key)}/",
        })
    return out


# --------------------------------------------------------------------------- #

def report() -> None:
    if warnings:
        print(f"\n  Предупреждения ({len(warnings)}):")
        for w in warnings:
            print(f"    ! {w}")
    if errors:
        print(f"\n  Ошибки ({len(errors)}):")
        for e in errors:
            print(f"    ✗ {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка сайта")
    ap.add_argument("--serve", action="store_true", help="поднять локальный сервер после сборки")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--check", action="store_true", help="только проверить контент")
    ap.add_argument("--strict", action="store_true", help="считать предупреждения ошибками")
    args = ap.parse_args()

    data = load_content()
    check_content(data)

    if errors:
        report()
        print("\nСборка остановлена: сначала исправьте ошибки выше.\n")
        return 1

    if args.check:
        report()
        if args.strict and warnings:
            print("\n--strict: предупреждения считаются ошибками.\n")
            return 1
        print(f"\n  Контент в порядке. Языков: {len(data['site'].get('languages', []))}, "
              f"услуг: {len(data['services'].get('items', []))}.\n")
        return 0

    n = render_site(data)
    report()

    total_kb = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024
    print(f"\n  Собрано {n} страниц в _site/ ({total_kb:.0f} KB)\n")

    if args.strict and warnings:
        return 1

    if args.serve:
        import functools
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            # Без chdir: иначе каталог _site остаётся занят и следующая
            # сборка не может его очистить.
            def log_message(self, *a):
                pass

        handler = functools.partial(Handler, directory=str(OUT))
        socketserver.TCPServer.allow_reuse_address = True

        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print(f"  http://localhost:{args.port}/   (Ctrl+C — остановить)\n")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
