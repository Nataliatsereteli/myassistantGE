#!/usr/bin/env python3
"""
Собирает content/i18n/<язык>.yml из фрагментов content/i18n/_parts/.

Фрагменты нужны только на этапе перевода: большой файл неудобно переводить
целиком, поэтому он разрезан по разделам. После сборки править нужно
итоговый content/i18n/<язык>.yml — он и есть источник правды для сайта.

    python scripts/assemble_i18n.py en ka        # собрать указанные языки
    python scripts/assemble_i18n.py ru en ka     # собрать все три
    python scripts/assemble_i18n.py --check en ka # только сверить с ru.yml

Скрипт проверяет, что в переводе тот же набор ключей и та же длина списков,
что и в русском оригинале, и отказывается собирать файл при расхождениях.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "content" / "i18n"
PARTS = I18N / "_parts"

# Порядок сборки и то, под каким родительским ключом лежит фрагмент.
LAYOUT = [
    ("00-meta", None),
    ("10-home", None),
    ("20-tail", None),
    ("30-pages-a", "pages"),
    ("31-pages-faq", "pages"),
    ("32-pages-leg", "pages"),
    ("40-svc-a", "services"),
    ("41-svc-b", "services"),
    ("42-svc-c", "services"),
]

HEADER = """# =============================================================================
#  {title}
#  Файл собран из фрагментов scripts/assemble_i18n.py, но дальше правится
#  напрямую — именно он используется при сборке сайта.
#
#  Правила те же, что и в ru.yml: меняем только текст справа от двоеточия,
#  отступы делаем пробелами, набор ключей не трогаем.
#  Цены — в content/pricing.yml, контакты — в content/contacts.yml.
# =============================================================================

"""

TITLES = {
    "ru": "РУССКАЯ ВЕРСИЯ САЙТА",
    "en": "АНГЛИЙСКАЯ ВЕРСИЯ САЙТА (English)",
    "ka": "ГРУЗИНСКАЯ ВЕРСИЯ САЙТА (ქართული)",
}


def assemble(lang: str) -> str:
    out: list[str] = [HEADER.format(title=TITLES.get(lang, lang.upper()))]
    current_parent: str | None = None
    for name, parent in LAYOUT:
        path = PARTS / f"{lang}.{name}.yml"
        if not path.exists():
            raise SystemExit(f"нет фрагмента {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8").rstrip("\n")
        # Агент мог обернуть ответ в ``` — снимаем.
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            if text.rstrip().endswith("```"):
                text = text.rstrip()[: -3].rstrip()
        if parent and parent != current_parent:
            out.append(f"\n{parent}:\n")
            current_parent = parent
        elif not parent:
            current_parent = None
        out.append(text + "\n\n")
    return "".join(out)


def shape(node, path=""):
    """Набор «путей» до всех значений — для сравнения структуры переводов."""
    keys = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(f"{path}.{k}")
            keys |= shape(v, f"{path}.{k}")
    elif isinstance(node, list):
        keys.add(f"{path}[len={len(node)}]")
        for i, v in enumerate(node):
            keys |= shape(v, f"{path}[{i}]")
    return keys


def compare(lang: str, data) -> list[str]:
    ref = yaml.safe_load((I18N / "ru.yml").read_text(encoding="utf-8"))
    a, b = shape(data), shape(ref)
    problems = []
    for missing in sorted(b - a):
        problems.append(f"[{lang}] не хватает: {missing}")
    for extra in sorted(a - b):
        problems.append(f"[{lang}] лишнее:     {extra}")
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    check_only = "--check" in sys.argv
    langs = args or ["en", "ka"]

    failed = False
    # Русский собирается первым и без сверки: он сам эталон. Остальные языки
    # сверяются уже с обновлённым ru.yml.
    langs = sorted(langs, key=lambda l: l != "ru")

    for lang in langs:
        try:
            text = assemble(lang)
        except SystemExit as exc:
            print(f"✗ {exc}")
            failed = True
            continue

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            print(f"✗ [{lang}] сломанный YAML после сборки:\n   {exc}")
            failed = True
            continue

        problems = [] if lang == "ru" else compare(lang, data)
        if problems:
            print(f"✗ [{lang}] структура не совпадает с ru.yml ({len(problems)} расхождений):")
            for p in problems[:25]:
                print("   " + p)
            if len(problems) > 25:
                print(f"   … ещё {len(problems) - 25}")
            failed = True
            continue

        if not check_only:
            (I18N / f"{lang}.yml").write_text(text, encoding="utf-8", newline="\n")
        what = "эталон" if lang == "ru" else "структура совпадает с ru.yml"
        print(f"✓ [{lang}] {what}, {len(shape(data))} узлов"
              f"{'' if check_only else f' → content/i18n/{lang}.yml'}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
