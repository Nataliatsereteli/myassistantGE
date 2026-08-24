#!/usr/bin/env python3
"""
Генерирует картинки сайта: иконку, иконку для iOS, манифест и по одной
карточке для соцсетей на каждый язык.

    python scripts/make_images.py

Тексты для карточек берутся из content/i18n/<язык>.yml и content/site.yml,
цвета — из палитры дизайн-системы (ниже). То есть после смены слогана
достаточно перегенерировать картинки этой же командой.

Нужен Pillow:  pip install pillow
TTF-шрифты скачиваются во временный каталог .cache/ и в репозиторий не кладутся
(в assets/fonts лежат woff2 для сайта, а Pillow умеет только TTF/OTF).
"""
from __future__ import annotations

import sys
import ssl
import urllib.request
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "assets" / "img"
CACHE = ROOT / ".cache" / "fonts-ttf"

# --- Палитра. Должна совпадать с :root в assets/css/site.css -----------------
PAPER = (247, 243, 234)
PAPER_2 = (241, 235, 222)
INK = (27, 24, 21)
INK_2 = (86, 80, 74)
INK_3 = (102, 96, 89)
RULE = (215, 205, 186)
ACCENT = (124, 31, 44)
CREAM = (253, 249, 242)

# Старый User-Agent -> Google Fonts отдаёт ttf вместо woff2.
UA_OLD = "Mozilla/5.0 (Windows NT 6.1; rv:12.0) Gecko/20120403211507 Firefox/12.0"

FONT_URLS = {
    "display": "Cormorant+Garamond:wght@600",
    "body": "Golos+Text:wght@400;600",
    "mono": "JetBrains+Mono:wght@500",
    "geo": "Noto+Sans+Georgian:wght@400;600",
    "geo_display": "Noto+Serif+Georgian:wght@600",
}


def fetch(url: str, ua: str = UA_OLD) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
        return r.read()


def ensure_ttf() -> dict[str, Path]:
    """Скачивает по одному TTF на каждое начертание, которое нужно карточкам."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for key, spec in FONT_URLS.items():
        family = spec.split(":")[0].replace("+", " ")
        for weight in spec.split("@")[1].split(";"):
            name = f"{family.replace(' ', '')}-{weight}.ttf"
            path = CACHE / name
            if not path.exists():
                css = fetch(f"https://fonts.googleapis.com/css2?family={family.replace(' ', '+')}:wght@{weight}").decode()
                url = css.split("src: url(")[1].split(")")[0]
                path.write_bytes(fetch(url))
                print(f"  ↓ {name}")
            out[f"{key}-{weight}"] = path
    return out


def font(paths: dict[str, Path], key: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(paths[key]), size)


# --------------------------------------------------------------------------- #
#  Иконка: гранатовый квадрат с монограммой M — «штамп на папке дела»          #
# --------------------------------------------------------------------------- #

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="MyAssistant">
  <rect width="32" height="32" rx="5" fill="#7c1f2c"/>
  <rect x="3.5" y="3.5" width="25" height="25" rx="3" fill="none" stroke="#fdf9f2" stroke-opacity=".28"/>
  <path d="M10 22.5V9.5l6 7.5 6-7.5v13" fill="none" stroke="#fdf9f2"
        stroke-width="2.4" stroke-linecap="square" stroke-linejoin="miter"/>
</svg>
"""


def draw_mark(size: int, pad_ratio: float = 0.0) -> Image.Image:
    """Та же монограмма растром — для png-иконок."""
    ss = 8  # рисуем крупно и уменьшаем, чтобы получить сглаживание
    s = size * ss
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = int(s * pad_ratio)
    box = (pad, pad, s - pad - 1, s - pad - 1)
    inner = box[2] - box[0]
    d.rounded_rectangle(box, radius=int(inner * 0.16), fill=ACCENT)

    m = pad + inner * 0.11
    d.rounded_rectangle((m, m, s - m, s - m), radius=int(inner * 0.10),
                        outline=CREAM + (72,), width=max(1, int(inner * 0.012)))

    # Монограмма M
    x0 = pad + inner * 0.28
    x1 = pad + inner * 0.72
    xm = pad + inner * 0.50
    y0 = pad + inner * 0.28
    y1 = pad + inner * 0.72
    ym = pad + inner * 0.52
    w = max(2, int(inner * 0.085))
    d.line([(x0, y1), (x0, y0)], fill=CREAM, width=w)
    d.line([(x0, y0), (xm, ym)], fill=CREAM, width=w)
    d.line([(xm, ym), (x1, y0)], fill=CREAM, width=w)
    d.line([(x1, y0), (x1, y1)], fill=CREAM, width=w)
    return im.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- #
#  Карточка для соцсетей                                                       #
# --------------------------------------------------------------------------- #

def wrap(draw, text, fnt, max_width) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def og_card(paths, lang, brand, headline, kicker, facts) -> Image.Image:
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)

    georgian = lang == "ka"
    f_body = font(paths, "geo-400" if georgian else "body-400", 26)
    f_mono = font(paths, "geo-600" if georgian else "mono-500", 19)
    # Название бренда — латиница, поэтому его всегда набираем основной
    # засечной гарнитурой: грузинский Noto для латинских букв выглядит чужеродно.
    f_brand = font(paths, "display-600", 40)
    display_key = "geo_display-600" if georgian else "display-600"

    pad = 76
    d.rectangle((pad - 24, pad - 24, W - pad + 24, H - pad + 24), outline=RULE, width=1)
    d.rectangle((0, 0, 10, H), fill=ACCENT)

    y = pad
    d.text((pad, y), brand, font=f_brand, fill=INK)
    y += 60
    d.line((pad, y, pad + 56, y), fill=ACCENT, width=2)
    y += 20
    d.text((pad, y), kicker if georgian else kicker.upper(), font=f_mono, fill=INK_3)
    y += 52

    # Заголовок подгоняется по размеру, чтобы всегда влезать в отведённую высоту
    facts_top = H - pad - 74
    available_h = facts_top - y - 16
    size = 92
    while size > 40:
        f_display = font(paths, display_key, size)
        lines = wrap(d, headline, f_display, W - pad * 2)
        line_h = int(size * 1.14)
        if len(lines) <= 3 and len(lines) * line_h <= available_h:
            break
        size -= 4
    for line in lines:
        d.text((pad, y), line, font=f_display, fill=INK)
        y += line_h

    # Нижняя строка фактов: сначала уменьшаем кегль, если не влезает —
    # убираем последний факт. Обрезанного текста на карточке быть не должно.
    d.line((pad, facts_top, W - pad, facts_top), fill=RULE, width=1)
    items = list(facts)
    while items:
        for fsize in range(26, 17, -1):
            f_facts = font(paths, "geo-400" if georgian else "body-400", fsize)
            line = "   ·   ".join(items)
            if d.textlength(line, font=f_facts) <= W - pad * 2:
                d.text((pad, facts_top + 22), line, font=f_facts, fill=INK_2)
                return im
        items.pop()
    return im


def main() -> int:
    IMG.mkdir(parents=True, exist_ok=True)
    site = yaml.safe_load((ROOT / "content" / "site.yml").read_text(encoding="utf-8"))
    brand = site["brand"]["name"]

    print("Шрифты:")
    paths = ensure_ttf()

    (IMG / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    draw_mark(64).save(IMG / "favicon.png")
    # iOS не любит прозрачность: подкладываем гранатовый фон целиком
    apple = Image.new("RGB", (180, 180), ACCENT)
    apple.paste(draw_mark(180), (0, 0), draw_mark(180))
    apple.save(IMG / "apple-touch-icon.png")
    draw_mark(512).save(IMG / "icon-512.png")
    draw_mark(192).save(IMG / "icon-192.png")
    print("Иконки: favicon.svg, favicon.png, apple-touch-icon.png, icon-192/512.png")

    for lang in site["languages"]:
        tr = yaml.safe_load((ROOT / "content" / "i18n" / f"{lang}.yml").read_text(encoding="utf-8"))
        og = tr["meta"]["og"]
        card = og_card(
            paths, lang, brand,
            " ".join(og["headline"].split()),
            " ".join(tr["meta"]["tagline_short"].split()),
            og["facts"],
        )
        card.save(IMG / f"og-{lang}.png", optimize=True)
        print(f"Карточка og-{lang}.png")

    # Манифест здесь больше не делается: он зависит от языка и от base_path,
    # поэтому его собирает build.py из templates/site.webmanifest.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
