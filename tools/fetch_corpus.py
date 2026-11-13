#!/usr/bin/env python3
"""Сборка корпуса-образцов: тексты Толстого и Достоевского из Викитеки.

Запуск из корня репозитория:

    python tools/fetch_corpus.py            # скачать, вычистить, записать corpus/
    python tools/fetch_corpus.py --report   # только посчитать слова по произведениям
    python tools/fetch_corpus.py --refresh  # заново скачать страницы, не брать кэш

Что делает: перечисляет страницы выбранных произведений через API Викитеки,
забирает викитекст пакетами, вычищает служебные блоки (заголовок источника,
сноски, оглавления, шаблоны оформления), склеивает главы романов с метками
«## <роман>, часть N, глава M» и пишет:

    corpus/text/<author>/<work>.txt   — UTF-8 без BOM, абзац на строку
    corpus/manifest.json              — состав, объёмы, sha256, источники

Кэш скачанных страниц лежит вне репозитория: %TEMP%/dsh-style-fingerprint-cache.
Он нужен, чтобы повторная сборка не дёргала сервер и давала тот же результат.

Зависимости: только `requests`; HTML не разбирается, поэтому bs4 не нужен.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
TEXT_DIR = CORPUS / "text"
MANIFEST = CORPUS / "manifest.json"
CACHE_DIR = Path(os.environ.get("TEMP") or ".") / "dsh-style-fingerprint-cache"

API = "https://ru.wikisource.org/w/api.php"
USER_AGENT = "dsh-style-fingerprint/0.1 (local corpus build)"
PAUSE = 0.4          # секунды между запросами к API
BATCH = 20           # страниц викитекста за один запрос

TARGET = 130_000     # целевой объём на автора, слов

ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
    "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
    "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19,
    "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24,
    "XXV": 25, "XXVI": 26, "XXVII": 27, "XXVIII": 28, "XXIX": 29,
    "XXX": 30, "XXXI": 31, "XXXII": 32, "XXXIII": 33, "XXXIV": 34,
    "XXXV": 35,
}

# Произведения. mode:
#   chapters — подстраницы «<prefix>/<часть>/Глава <глава>» (римские номера),
#   sections — подстраницы «<prefix>/<часть>/<глава>» (арабские номера),
#   roman    — подстраницы «<prefix>/<римский номер>»,
#   page     — одна страница целиком.
# parts — какие части брать (для романов берём целые части: одиночные главы
# обрывают сцену). markers — ставить метки «## роман, часть N, глава M».
WORKS = [
    dict(id="tolstoy/anna-karenina", author="tolstoy", title="Анна Каренина",
         genre="novel", year=1877, mode="chapters",
         prefix="Анна Каренина (Толстой)", parts=[1], markers=True,
         url="https://ru.wikisource.org/wiki/Анна_Каренина_(Толстой)",
         license="public domain",
         notes="часть I, все 34 главы; современная орфография"),
    dict(id="tolstoy/hadji-murat", author="tolstoy", title="Хаджи-Мурат",
         genre="novella", year=1912, mode="roman", prefix="Хаджи-Мурат (Толстой)",
         parts=list(range(1, 26)), markers=False,
         url="https://ru.wikisource.org/wiki/Хаджи-Мурат_(Толстой)",
         license="public domain",
         notes="написан в 1896–1904, впервые опубликован посмертно в 1912"),
    dict(id="tolstoy/kreutzer-sonata", author="tolstoy", title="Крейцерова соната",
         genre="novella", year=1891, mode="page",
         page="Крейцерова соната (Толстой)",
         url="https://ru.wikisource.org/wiki/Крейцерова_соната_(Толстой)",
         license="public domain", notes=""),
    dict(id="tolstoy/smert-ivana-ilicha", author="tolstoy", title="Смерть Ивана Ильича",
         genre="novella", year=1886, mode="page",
         page="Смерть Ивана Ильича (Толстой)",
         url="https://ru.wikisource.org/wiki/Смерть_Ивана_Ильича_(Толстой)",
         license="public domain", notes=""),
    dict(id="tolstoy/kavkazskiy-plennik", author="tolstoy", title="Кавказский пленник",
         genre="story", year=1872, mode="page",
         page="Кавказский пленник (Толстой)",
         url="https://ru.wikisource.org/wiki/Кавказский_пленник_(Толстой)",
         license="public domain", notes=""),
    dict(id="tolstoy/sevastopol-v-dekabre", author="tolstoy",
         title="Севастополь в декабре месяце", genre="story", year=1855, mode="page",
         page="Севастополь в декабре месяце (Толстой)",
         url="https://ru.wikisource.org/wiki/Севастополь_в_декабре_месяце_(Толстой)",
         license="public domain", notes="первый из «Севастопольских рассказов»"),
    dict(id="tolstoy/posle-bala", author="tolstoy", title="После бала",
         genre="story", year=1911, mode="page", page="После бала (Толстой)",
         url="https://ru.wikisource.org/wiki/После_бала_(Толстой)",
         license="public domain",
         notes="написан в 1903, впервые опубликован посмертно в 1911"),

    dict(id="dostoevsky/idiot", author="dostoevsky", title="Идиот",
         genre="novel", year=1869, mode="chapters",
         prefix="Идиот (Достоевский)", parts=[1], markers=True,
         url="https://ru.wikisource.org/wiki/Идиот_(Достоевский)",
         license="public domain", notes="часть I, все 16 глав"),
    dict(id="dostoevsky/prestuplenie-i-nakazanie", author="dostoevsky",
         title="Преступление и наказание", genre="novel", year=1866, mode="chapters",
         prefix="Преступление и наказание (Достоевский)", parts=[1], markers=True,
         url="https://ru.wikisource.org/wiki/Преступление_и_наказание_(Достоевский)",
         license="public domain", notes="часть I, все 7 глав"),
    dict(id="dostoevsky/chestnyy-vor", author="dostoevsky", title="Честный вор",
         genre="story", year=1848, mode="page", page="Честный вор (Достоевский)",
         url="https://ru.wikisource.org/wiki/Честный_вор_(Достоевский)",
         license="public domain", notes="из цикла «Рассказы бывалого человека»"),
    dict(id="dostoevsky/belye-nochi", author="dostoevsky", title="Белые ночи",
         genre="novella", year=1848, mode="page", page="Белые ночи (Достоевский)",
         url="https://ru.wikisource.org/wiki/Белые_ночи_(Достоевский)",
         license="public domain", notes=""),
    dict(id="dostoevsky/krotkaya", author="dostoevsky", title="Кроткая",
         genre="story", year=1876, mode="sections",
         prefix="Кроткая (Достоевский)", parts=[1, 2], markers=False,
         url="https://ru.wikisource.org/wiki/Кроткая_(Достоевский)",
         license="public domain", notes="фантастический рассказ; главы без предисловия «От автора»"),
    dict(id="dostoevsky/son-smestnogo-cheloveka", author="dostoevsky",
         title="Сон смешного человека", genre="story", year=1877, mode="page",
         page="Сон смешного человека (Достоевский)",
         url="https://ru.wikisource.org/wiki/Сон_смешного_человека_(Достоевский)",
         license="public domain", notes=""),
    dict(id="dostoevsky/malchik-u-hrista", author="dostoevsky",
         title="Мальчик у Христа на ёлке", genre="story", year=1876, mode="page",
         page="Мальчик у Христа на ёлке (Достоевский)",
         url="https://ru.wikisource.org/wiki/Мальчик_у_Христа_на_ёлке_(Достоевский)",
         license="public domain", notes=""),
]

EXCLUDED = [
    dict(title="Анна Каренина (Толстой)/…/ДО", reason="старая орфография"),
    dict(title="Кроткая (Достоевский)/Версия 2", reason="дубль версии"),
    dict(title="Сон смешного человека (Достоевский)/Версия 2", reason="дубль версии"),
    dict(title="Мальчик у Христа на елке (Достоевский)", reason="дубль версии"),
    dict(title="Белые ночи (Достоевский)/1988 (СО)", reason="дубль версии"),
]

# Шаблоны, которые целиком выбрасываются из текста.
DROP_TEMPLATES = {
    "Отексте", "Версии", "Главы2", "СодержаниеБН", "Современник", "книга",
    "Книга", "reflist", "примечания", "ext", "fsp", "↕", "#titleparts",
    "BASEPAGENAME", "SUBPAGENAME", "PAGENAME", "нет источника",
}
# Шаблоны, у которых берётся первый значимый аргумент.
FIRST_ARG_TEMPLATES = {"nobr", "Razr", "right", "poem1", "poemx1", "Эпиграф2", "эпиграф2"}
# Шаблоны-языковые пометки: {{lang|fr|текст}} или {{lang-fr|текст}}.
LANG_TEMPLATE = re.compile(r"^lang(?:-[a-z]{2,3})?$")

NOTE_HEADING = re.compile(
    r"^[ \t]*={2,}[ \t]*(?:Примечания|Комментарии|Сноски|Источники)[ \t]*={2,}[ \t]*$",
    re.MULTILINE,
)
HEADING_LINE = re.compile(r"^[ \t]*={2,}[ \t]*.*?[ \t]*={2,}[ \t]*$", re.MULTILINE)
JUNK_PARAGRAPH = re.compile(
    r"^(?:[IVXLCDM]+|\d{1,4}|[—–-]+|См\. также.*|Примечания.*|Источник.*)$"
)


# ---------------------------------------------------------------- сеть и кэш

class Client:
    """Запросы к API Викитеки: POST, пауза, повтор на 429."""

    def __init__(self, refresh: bool) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.refresh = refresh
        self.requests = 0

    def call(self, params: dict, tries: int = 8) -> dict:
        data = dict(params)
        data.update({"format": "json", "formatversion": 2, "maxlag": 5})
        for attempt in range(tries):
            self.requests += 1
            response = self.session.post(API, data=data, timeout=120)
            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
                log(f"  429, ждём {wait} с")
                time.sleep(wait)
                continue
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                raise RuntimeError(f"API: {result['error']}")
            time.sleep(PAUSE)
            return result
        raise RuntimeError("API не ответил после повторов")

    def pages_with_prefix(self, prefix: str) -> list[str]:
        titles, cont = [], None
        while True:
            params = {"action": "query", "list": "allpages", "apprefix": prefix,
                      "apnamespace": 0, "aplimit": 500}
            if cont:
                params["apcontinue"] = cont
            data = self.call(params)
            titles += [p["title"] for p in data["query"]["allpages"]]
            cont = data.get("continue", {}).get("apcontinue")
            if not cont:
                return titles

    def wikitext(self, titles: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        pending = []
        for title in titles:
            path = cache_path(title)
            if not self.refresh and path.exists() and path.stat().st_size:
                result[title] = path.read_text(encoding="utf-8")
            else:
                pending.append(title)
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            data = self.call({
                "action": "query", "prop": "revisions", "rvprop": "content",
                "rvslots": "main", "titles": "|".join(chunk), "redirects": 1,
            })
            normalized = {n["from"]: n["to"] for n in data["query"].get("normalized", [])}
            redirected = {r["from"]: r["to"] for r in data["query"].get("redirects", [])}
            fetched = {
                p["title"]: p["revisions"][0]["slots"]["main"]["content"]
                for p in data["query"]["pages"] if "revisions" in p
            }
            for title in chunk:
                real = normalized.get(title, title)
                real = redirected.get(real, real)
                if real not in fetched:
                    raise RuntimeError(f"страница не найдена: {title}")
                result[title] = fetched[real]
                cache_path(title).write_text(fetched[real], encoding="utf-8")
        return result


def cache_path(title: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", title)[:150]
    return CACHE_DIR / f"{safe}.wikitext"


def listing(client: Client) -> None:
    """Диагностика: какие подстраницы есть у произведений с подстраницами."""
    for work in WORKS:
        if "prefix" in work:
            pages = client.pages_with_prefix(work["prefix"] + "/")
            print(f"{work['id']}: {len(pages)} подстраниц")
            for title in pages[:6]:
                print("   ", title)


# ------------------------------------------------------------------ шаблоны

def split_template(body: str) -> tuple[str, list[str]]:
    """Делит содержимое шаблона на имя и аргументы верхнего уровня."""
    parts, depth_braces, depth_links, buf = [], 0, 0, []
    for i, char in enumerate(body):
        if body.startswith("{{", i):
            depth_braces += 1
        elif body.startswith("}}", i):
            depth_braces -= 1
        elif body.startswith("[[", i):
            depth_links += 1
        elif body.startswith("]]", i):
            depth_links -= 1
        if char == "|" and depth_braces == 0 and depth_links == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    name = parts[0].strip()
    return name, [part.strip() for part in parts[1:]]


def template_end(text: str, start: int) -> int:
    """Индекс сразу за закрывающими }} шаблона, открытого в start."""
    depth = 0
    i = start
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            depth += 1
            i += 2
            continue
        if pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        i += 1
    raise ValueError("незакрытый шаблон")


def expand_templates(text: str, unknown: list[str]) -> str:
    """Раскрывает известные шаблоны, остальные выбрасывает."""
    while True:
        start = text.find("{{")
        if start < 0:
            return text
        end = template_end(text, start)
        name, args = split_template(text[start + 2:end - 2])
        plain = name.split(":")[0].strip()
        if LANG_TEMPLATE.match(plain):
            positional = [a for a in args if "=" not in a]
            repl = positional[-1] if positional else ""
        elif plain in FIRST_ARG_TEMPLATES:
            positional = [a for a in args if "=" not in a]
            repl = positional[0] if positional else ""
            if plain in {"poem1", "poemx1"}:
                inner = next((a for a in args if "<poem>" in a), "")
                repl = inner
        elif plain == "акут":
            repl = ""
        elif plain in DROP_TEMPLATES or plain.startswith("#"):
            repl = ""
        else:
            unknown.append(plain)
            repl = ""
        text = text[:start] + repl + text[end:]


# ------------------------------------------------------------------- чистка

def strip_service_blocks(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<noinclude>.*?</noinclude>", "", text, flags=re.S)
    text = re.sub(r"<includeonly>.*?</includeonly>", "", text, flags=re.S)
    text = re.sub(r"<references[^>]*>.*?</references>", "", text, flags=re.S)
    text = re.sub(r"<references[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"__[A-Z]+__", "", text)
    match = NOTE_HEADING.search(text)
    if match:
        text = text[:match.start()]
    return text


def strip_leading_title(text: str) -> str:
    """Снимает центрированный заголовок произведения в начале страницы."""
    match = re.match(r"\s*<center>(.*?)</center>\s*", text, flags=re.S)
    if not match:
        return text
    inner = re.sub(r"<[^>]+>", "", match.group(1))
    letters = [c for c in inner if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return text[match.end():]
    return text


def strip_markup(text: str) -> str:
    text = HEADING_LINE.sub("", text)
    text = re.sub(r"\[\[(?:Файл|File|Изображение|Image|Категория|Category):[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[([^\[\]]+)\]\]",
                  lambda m: "" if re.match(r"^[a-z]{2,3}(-[a-z]+)?:", m.group(1))
                  else m.group(1).split("|")[-1].lstrip(":"), text)
    text = re.sub(r"\[(?:https?|ftp)://[^\s\]]+(?:\s+([^\]]*))?\]",
                  lambda m: (m.group(1) or ""), text)
    text = text.replace("'''''", "").replace("'''", "").replace("''", "")
    text = re.sub(r"<poem>(.*?)</poem>",
                  lambda m: m.group(1).replace("\n", " "), text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:div|blockquote|p|center)[^>]*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\r", "")
    return text


def to_paragraphs(text: str) -> list[str]:
    """Делит текст на абзацы, различая две манеры исходника.

    В одних страницах Викитеки абзац занимает одну длинную строку и абзацы
    разделены пустой строкой, в других весь текст идёт строками по абзацу без
    пустых строк, в третьих строки — жёсткая разбивка по 76 знаков. Поэтому
    сначала смотрим на медианную длину строки: длинные строки — это абзацы,
    короткие — куски одного абзаца.
    """
    lines = [line.strip() for line in text.split("\n")]
    filled = [line for line in lines if line]
    if not filled:
        return []
    lengths = sorted(len(line) for line in filled)
    median = lengths[len(lengths) // 2]
    paragraphs: list[str] = []
    if median > 200:
        paragraphs = list(filled)
    else:
        buffer: list[str] = []
        for line in lines:
            if line:
                buffer.append(line)
            elif buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
        if buffer:
            paragraphs.append(" ".join(buffer))

    cleaned: list[str] = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"[ \t]{2,}", " ", paragraph).strip()
        if len(paragraph) < 2 or JUNK_PARAGRAPH.match(paragraph):
            continue
        if cleaned and cleaned[-1] == paragraph:
            continue
        cleaned.append(paragraph)
    return cleaned


def clean_wikitext(text: str, unknown: list[str]) -> list[str]:
    text = strip_service_blocks(text)
    text = expand_templates(text, unknown)
    text = strip_leading_title(text)
    text = strip_markup(text)
    return to_paragraphs(text)


# --------------------------------------------------------------- сборка работ

def chapter_pages(client: Client, work: dict) -> list[tuple[int, int, str]]:
    """Возвращает (часть, глава, страница) для выбранных частей произведения."""
    prefix = re.escape(work["prefix"])
    found: list[tuple[int, int, str]] = []
    for title in client.pages_with_prefix(work["prefix"] + "/"):
        if "/ДО" in title or "Версия" in title:
            continue
        if work["mode"] == "chapters":
            match = re.fullmatch(prefix + r"/Часть ([IVXLC]+)/Глава ([IVXLC]+)", title)
            if match:
                part = ROMAN[match.group(1)]
                chapter = ROMAN[match.group(2)]
                if part in work["parts"]:
                    found.append((part, chapter, title))
        elif work["mode"] == "sections":
            match = (re.fullmatch(prefix + r"/Часть (\d+)/Глава (\d+)", title)
                     or re.fullmatch(prefix + r"/Глава (\d+)/([IVXLC]+)", title))
            if match:
                second = match.group(2)
                chapter = ROMAN[second] if second in ROMAN else int(second)
                part = int(match.group(1))
                if part in work["parts"]:
                    found.append((part, chapter, title))
        elif work["mode"] == "roman":
            match = re.fullmatch(prefix + r"/([IVXLC]+)", title)
            if match:
                chapter = ROMAN[match.group(1)]
                if chapter in work["parts"]:
                    found.append((1, chapter, title))
    found.sort(key=lambda item: (item[0], item[1]))
    if not found:
        raise RuntimeError(f"не найдены главы: {work['id']}")
    return found


def build_work(client: Client, work: dict) -> tuple[str, list[str]]:
    """Возвращает текст произведения и список неизвестных шаблонов."""
    unknown: list[str] = []
    if work["mode"] == "page":
        pages = [(1, 1, work["page"])]
    else:
        pages = chapter_pages(client, work)
    texts = client.wikitext([title for _, _, title in pages])

    blocks: list[str] = []
    for part, chapter, title in pages:
        paragraphs = clean_wikitext(texts[title], unknown)
        if not paragraphs:
            raise RuntimeError(f"пустой текст после чистки: {title}")
        if work.get("markers"):
            blocks.append(f"## {work['title']}, часть {part}, глава {chapter}")
        blocks.extend(paragraphs)
    return "\n\n".join(blocks) + "\n", unknown


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def excerpt_of(text: str) -> str:
    """Первая содержательная фраза: метки глав и короткие строки пропускаются."""
    candidate = ""
    for paragraph in text.split("\n\n"):
        if paragraph.startswith("## "):
            continue
        if len(paragraph.split()) >= 12:
            candidate = paragraph
            break
        if not candidate:
            candidate = paragraph
    if len(candidate) <= 240:
        return candidate
    cut = candidate[:240].rsplit(" ", 1)[0]
    return cut + "…"


def write_manifest(works: list[dict], started: str) -> None:
    coverage: dict[str, dict] = {}
    for work in works:
        author = work["author"]
        bucket = coverage.setdefault(author, {"words": 0, "works": 0, "target": TARGET})
        bucket["words"] += work["words"]
        bucket["works"] += 1
    manifest = {
        "version": 1,
        "built": started,
        "use": "exemplar",
        "coverage": coverage,
        "works": works,
        "excluded": EXCLUDED,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------- запуск

def log(message: str) -> None:
    print(message, flush=True)


def run(report_only: bool, refresh: bool) -> None:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = Client(refresh)
    works_out: list[dict] = []
    total: dict[str, int] = {}
    unknown_all: dict[str, int] = {}
    for work in WORKS:
        text, unknown = build_work(client, work)
        paragraphs = [p for p in text.split("\n\n") if not p.startswith("## ")]
        words = sum(len(p.split()) for p in paragraphs)
        chars = len(text)
        total[work["author"]] = total.get(work["author"], 0) + words
        for name in unknown:
            unknown_all[name] = unknown_all.get(name, 0) + 1
        log(f"{work['id']}: {words} слов, {chars} символов")
        record = {
            "id": work["id"], "author": work["author"], "title": work["title"],
            "genre": work["genre"], "year": work["year"], "source": "wikisource",
            "url": work["url"], "file": f"corpus/text/{work['author']}/{work['id'].split('/')[1]}.txt",
            "chars": chars, "words": words, "sha256": sha256(text.encode("utf-8")),
            "license": work["license"], "excerpt": excerpt_of(text),
            "notes": work["notes"],
        }
        works_out.append(record)
        if not report_only:
            path = ROOT / record["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    log("")
    for author, words in sorted(total.items()):
        log(f"{author}: {words} слов (цель {TARGET})")
    if unknown_all:
        log("неизвестные шаблоны: " + ", ".join(f"{k}×{v}" for k, v in sorted(unknown_all.items())))
    log(f"запросов к API: {client.requests}")
    if not report_only:
        write_manifest(works_out, started)
        log(f"записан {MANIFEST.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="посчитать слова, ничего не писать")
    parser.add_argument("--refresh", action="store_true", help="не брать кэш страниц")
    parser.add_argument("--list", action="store_true", help="показать подстраницы произведений")
    args = parser.parse_args()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if args.list:
        listing(Client(args.refresh))
        return 0
    try:
        run(args.report, args.refresh)
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"ошибка: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
