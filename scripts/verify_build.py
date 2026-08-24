#!/usr/bin/env python3
"""
Проверяет уже собранный сайт в _site/ — то, что нельзя проверить по YAML.

    python build.py && python scripts/verify_build.py

Что смотрим:
  * все внутренние ссылки ведут на существующие страницы и файлы;
  * микроразметка JSON-LD валидна;
  * на каждой странице есть title, description, canonical и hreflang,
    и hreflang-связки взаимны (страница A ссылается на B, а B — на A);
  * заголовки для поисковиков и соцсетей не пустые и не слишком длинные;
  * на каждой странице ровно один <h1>;
  * у всех картинок есть alt;
  * файлы sitemap.xml, robots.txt и 404.html на месте.

Скрипт возвращает ненулевой код, если что-то сломано, — поэтому его гоняет CI.
"""
from __future__ import annotations

import html as html_mod
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"

# Если сайт собран для подпапки, ссылки в HTML имеют префикс, а файлы лежат
# без него — при проверке префикс нужно снимать.
#
# Источник префикса тот же, что и у build.py: сначала переменная окружения,
# потом site.yml. Иначе сборка с SITE_BASE_PATH проходит, а проверка её же
# результата валится — ровно та ловушка, в которую легко попасть на превью.
_site_cfg = yaml.safe_load((ROOT / "content" / "site.yml").read_text(encoding="utf-8")) or {}
_raw_base = os.environ.get("SITE_BASE_PATH", _site_cfg.get("base_path") or "")
BASE = "/" + _raw_base.strip().strip("/")
BASE = "" if BASE == "/" else BASE

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

errors: list[str] = []
notes: list[str] = []

TITLE_MAX = 65      # дальше Google обрезает заголовок в выдаче
DESC_MIN, DESC_MAX = 70, 165


def rel(p: Path) -> str:
    return str(p.relative_to(OUT)).replace("\\", "/")


def strip_base(path: str) -> str:
    if BASE and (path == BASE or path.startswith(BASE + "/")):
        return path[len(BASE):] or "/"
    return path


def target_exists(href: str) -> bool:
    path = strip_base(unquote(urlparse(href).path))
    if path.endswith("/"):
        return (OUT / path.strip("/") / "index.html").exists()
    candidate = OUT / path.strip("/")
    return candidate.exists() or candidate.with_suffix(candidate.suffix).exists()


def main() -> int:
    if not OUT.exists():
        print("Нет каталога _site — сначала запустите python build.py")
        return 1

    pages = sorted(OUT.rglob("*.html"))
    if not pages:
        errors.append("В _site нет ни одной html-страницы")

    alternates: dict[str, set[str]] = {}

    for page in pages:
        h = page.read_text(encoding="utf-8")
        where = rel(page)
        # Страницы с noindex в выдачу не попадают — придираться к длине
        # их title и description смысла нет.
        robots = re.search(r'<meta name="robots"[^>]*>', h)
        indexable = not (robots and "noindex" in robots.group(0))

        # --- обязательные мета-теги ---------------------------------------
        title = re.search(r"<title>(.*?)</title>", h, re.S)
        desc = re.search(r'<meta name="description" content="(.*?)"', h, re.S)
        canon = re.search(r'<link rel="canonical" href="(.*?)"', h)

        if not title or not title.group(1).strip():
            errors.append(f"{where}: нет <title>")
        elif indexable and len(html_mod.unescape(title.group(1))) > TITLE_MAX:
            notes.append(f"{where}: title длиннее {TITLE_MAX} знаков "
                         f"({len(html_mod.unescape(title.group(1)))}) — поиск его обрежет")

        if not desc or not desc.group(1).strip():
            errors.append(f"{where}: нет meta description")
        elif indexable:
            n = len(html_mod.unescape(desc.group(1)))
            if n > DESC_MAX:
                notes.append(f"{where}: description длиннее {DESC_MAX} знаков ({n})")
            elif n < DESC_MIN:
                notes.append(f"{where}: description короче {DESC_MIN} знаков ({n})")

        if not canon:
            errors.append(f"{where}: нет canonical")

        # --- ровно один h1 --------------------------------------------------
        h1 = re.findall(r"<h1[\s>]", h)
        if len(h1) != 1 and not where.endswith("404.html"):
            errors.append(f"{where}: должен быть ровно один <h1>, найдено {len(h1)}")

        # --- lang -----------------------------------------------------------
        if not re.search(r'<html lang="[a-z]{2}"', h):
            errors.append(f"{where}: у <html> нет корректного атрибута lang")

        # --- alt у картинок ---------------------------------------------------
        for img in re.finditer(r"<img\b[^>]*>", h):
            if "alt=" not in img.group(0):
                errors.append(f"{where}: <img> без alt — {img.group(0)[:70]}")

        # --- JSON-LD ----------------------------------------------------------
        for block in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
            try:
                data = json.loads(block.group(1))
            except json.JSONDecodeError as exc:
                errors.append(f"{where}: невалидный JSON-LD — {exc}")
                continue
            if "@graph" in data and not data["@graph"]:
                errors.append(f"{where}: пустой @graph в микроразметке")

        # --- внутренние ссылки -------------------------------------------------
        for m in re.finditer(r'href="(/[^"#]*)"', h):
            href = m.group(1)
            if href.startswith("//"):
                continue
            if not target_exists(href):
                errors.append(f"{where}: битая ссылка -> {href}")

        # --- hreflang ----------------------------------------------------------
        if canon:
            alts = dict(re.findall(r'<link rel="alternate" hreflang="([a-z-]+)" href="(.*?)"', h))
            alts.pop("x-default", None)
            if alts:
                alternates[canon.group(1)] = set(alts.values())
                if canon.group(1) not in alts.values():
                    errors.append(f"{where}: hreflang не содержит ссылку на саму страницу")

    # --- взаимность hreflang -----------------------------------------------
    for url, group in alternates.items():
        for other in group:
            if other == url:
                continue
            back = alternates.get(other)
            if back is None:
                errors.append(f"hreflang: {url} ссылается на {other}, а обратной ссылки нет")
            elif url not in back:
                errors.append(f"hreflang: связка между {url} и {other} односторонняя")

    # --- обязательные файлы -------------------------------------------------
    for required in ("sitemap.xml", "robots.txt", "404.html", "index.html", ".nojekyll"):
        if not (OUT / required).exists():
            errors.append(f"нет файла {required}")

    sitemap = (OUT / "sitemap.xml").read_text(encoding="utf-8") if (OUT / "sitemap.xml").exists() else ""
    for loc in re.findall(r"<loc>(.*?)</loc>", sitemap):
        path = strip_base(urlparse(loc).path)
        if not (OUT / path.strip("/") / "index.html").exists():
            errors.append(f"sitemap указывает на несуществующую страницу: {loc}")

    # --- отчёт ---------------------------------------------------------------
    print(f"Проверено страниц: {len(pages)}")
    if notes:
        print(f"\nЗамечания ({len(notes)}):")
        for n in notes:
            print("  · " + n)
    if errors:
        print(f"\nОшибки ({len(errors)}):")
        for e in errors:
            print("  ✗ " + e)
        return 1
    print("\nОшибок нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
