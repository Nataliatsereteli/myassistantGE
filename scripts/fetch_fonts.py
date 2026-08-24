#!/usr/bin/env python3
"""
Скачивает self-hosted шрифты (OFL) с Google Fonts и складывает в assets/fonts/.

Зачем self-hosting, а не CDN Google:
  * нет обращений к сторонним доменам -> быстрее (нет лишнего DNS+TLS)
    и чище с точки зрения приватности;
  * шрифты кэшируются вместе с сайтом и не ломаются, если Google недоступен.

Запускать нужно один раз (и повторно только если меняется набор шрифтов):
    python scripts/fetch_fonts.py

Результат: assets/fonts/*.woff2 + assets/fonts/fonts.css
"""
from __future__ import annotations

import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "fonts"

# Свежий UA обязателен: только тогда Google отдаёт woff2, а не ttf.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Латиница и кириллица берутся из «характерных» гарнитур, грузинский —
# из Noto Georgian (единственное семейство в Google Fonts с ქართული).
FAMILIES = [
    "Cormorant+Garamond:wght@400;500;600",
    "Golos+Text:wght@400;500;600;700",
    "JetBrains+Mono:wght@400;500",
    "Noto+Serif+Georgian:wght@400;500;600",
    "Noto+Sans+Georgian:wght@400;500;600;700",
]

# Ненужные подмножества выкидываем, чтобы не тащить лишние килобайты.
KEEP_SUBSETS = {"latin", "latin-ext", "cyrillic", "cyrillic-ext", "georgian"}

# У Noto Georgian забираем ТОЛЬКО грузинский диапазон: латиницу и кириллицу
# в этих же строках рисуют Cormorant / Golos / JetBrains Mono.
GEORGIAN_ONLY = {"Noto Serif Georgian", "Noto Sans Georgian"}

# --- Знак лари ---------------------------------------------------------------
# ₾ (U+20BE) отсутствует в Cormorant Garamond, Golos Text и JetBrains Mono:
# Google не включает его ни в одно подмножество этих семейств. Без обходного
# пути цена на всём сайте рисовалась бы системным шрифтом (в лучшем случае)
# или пустым квадратом (в худшем).
# Берём один глиф из Noto Georgian и подмешиваем его в каждое семейство
# через unicode-range — остальные символы по-прежнему рисует основной шрифт.
LARI = "₾"
LARI_SOURCES = {"sans": "Noto+Sans+Georgian", "serif": "Noto+Serif+Georgian"}
# Отдельные семейства, которые подставляются в цепочку фолбэков в site.css
# сразу после основной гарнитуры.
LARI_TARGETS = [
    ("Lari Serif", "serif"),
    ("Lari Sans", "sans"),
]

CSS_URL = (
    "https://fonts.googleapis.com/css2?"
    + "&".join("family=" + f for f in FAMILIES)
    + "&display=swap"
)

CTX = ssl.create_default_context()

HEADER = (
    "/* ============================================================\n"
    "   Шрифты сайта. Файл сгенерирован scripts/fetch_fonts.py —\n"
    "   не редактируйте вручную, перегенерируйте скриптом.\n"
    "   Все гарнитуры распространяются по SIL Open Font License 1.1.\n"
    "   ============================================================ */\n"
)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return r.read()


def lari_faces() -> list[str]:
    """@font-face со знаком лари для каждой основной гарнитуры."""
    files: dict[str, str] = {}
    for kind, family in LARI_SOURCES.items():
        css = fetch(
            f"https://fonts.googleapis.com/css2?family={family}"
            f"&text={urllib.parse.quote(LARI)}"
        ).decode("utf-8")
        url = re.search(r"url\((https://[^)]+)\)", css).group(1)
        name = f"lari-{kind}.woff2"
        (OUT / name).write_bytes(fetch(url))
        files[kind] = name
        size = (OUT / name).stat().st_size / 1024
        print(f"  ↓ {name:<52} {size:6.1f} KB  (знак лари ₾)")

    blocks = []
    for family, kind in LARI_TARGETS:
        blocks.append(
            f"/* {family} — знак лари ₾ (U+20BE), глиф из Noto Georgian */\n"
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            "  font-style: normal;\n"
            "  font-weight: 100 900;\n"
            "  font-display: swap;\n"
            f"  src: url(./{files[kind]}) format('woff2');\n"
            "  unicode-range: U+20BE;\n"
            "}"
        )
    return blocks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    css = fetch(CSS_URL).decode("utf-8")

    # Google отдаёт блоки вида:  /* cyrillic */\n@font-face { ... }
    blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{.*?\})", css, re.S)
    if not blocks:
        print("Не удалось разобрать CSS от Google Fonts", file=sys.stderr)
        return 1

    kept: list[str] = []
    downloaded: set[str] = set()
    skipped: set[str] = set()

    for subset, block in blocks:
        if subset not in KEEP_SUBSETS:
            skipped.add(subset)
            continue

        family = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        if family in GEORGIAN_ONLY and subset != "georgian":
            skipped.add(f"{family}/{subset}")
            continue

        weight = re.search(r"font-weight:\s*([\d\s]+);", block).group(1).strip()
        style = re.search(r"font-style:\s*(\w+);", block).group(1)
        url = re.search(r"url\((https://[^)]+)\)", block).group(1)

        slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
        name = f"{slug}-{subset}-{weight.replace(' ', '_')}-{style}.woff2"

        if name not in downloaded:
            (OUT / name).write_bytes(fetch(url))
            downloaded.add(name)
            size = (OUT / name).stat().st_size / 1024
            print(f"  ↓ {name:<52} {size:6.1f} KB")

        block = block.replace(url, f"./{name}")
        # font-display: swap на всех блоках — текст виден сразу, без FOIT.
        if "font-display" not in block:
            block = block.replace("@font-face {", "@font-face {\n  font-display: swap;")
        kept.append(f"/* {family} — {subset} {weight} */\n{block}")

    kept.extend(lari_faces())
    (OUT / "fonts.css").write_text(HEADER + "\n" + "\n\n".join(kept) + "\n", encoding="utf-8")

    files = list(OUT.glob("*.woff2"))
    total = sum(f.stat().st_size for f in files) / 1024
    print(f"\nГотово: {len(files)} файлов, {total:.0f} KB -> {OUT}")
    if skipped:
        print(f"Пропущены подмножества: {', '.join(sorted(skipped))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
