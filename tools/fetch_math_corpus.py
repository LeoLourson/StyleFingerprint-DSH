#!/usr/bin/env python
"""Build the math corpus of A. A. Shkalikov's papers from Math-Net.Ru.

Run from the repository root:

    .venv\\Scripts\\python.exe tools/fetch_math_corpus.py

The script reads the person page (personid=12042), takes one bibliography entry per
paper, drops obituaries, jubilee greetings, collective reports and English-only
texts, downloads the Russian full-text PDF of every remaining paper into .tmp/pdf
(never into the repository), extracts the text layer with pypdf, removes running
heads and page numbers, glues hyphenated line breaks and restores paragraphs, then
writes corpus/math/shkalikov/<mathnet>.txt and corpus/math/manifest.json.

Repeated runs are byte-identical: the person page is cached under .tmp and the
manifest keeps the "built" stamp of the previous run whenever the computed content
is unchanged.  A successful build deletes the downloaded PDFs, so .tmp keeps only
the reports; the next run downloads them again and gets the same texts.

Needs requests, beautifulsoup4, pypdf (fontTools, the pypdf extra for Type1
fonts, improves the decoding of older PDFs) and pymorphy3 with its Russian
dictionary, which glues the words a line break split.  No PDF is written outside
.tmp.

Options:
    --refresh         refetch the person page and every PDF
    --classify-only   parse and classify the bibliography, write .tmp/classify.txt
    --validate        check the manifest against the text files and exit
    --limit N         process only N articles (development aid, not for the final run)
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
import pymorphy3

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "corpus" / "math"
TEXT_DIR = CORPUS_DIR / "shkalikov"
MANIFEST = CORPUS_DIR / "manifest.json"
TMP_DIR = ROOT / ".tmp"
PDF_DIR = TMP_DIR / "pdf"
PAGE_CACHE = TMP_DIR / "person.html"
URL_CACHE = TMP_DIR / "math_urls.json"
BUILD_REPORT = TMP_DIR / "build_report.txt"
CLASSIFY_REPORT = TMP_DIR / "classify.txt"
VALIDATION_REPORT = TMP_DIR / "validate.txt"

PERSON_ID = 12042
PERSON_URL = (
    "https://www.mathnet.ru/php/person.phtml?personid=12042"
    "&wshow=pubs&showmode=groups&showall=show&option_lang=rus"
)
SITE = "https://www.mathnet.ru"
USER_AGENT = "dsh-style-fingerprint/0.1 (local corpus build)"
PAUSE_SECONDS = 0.4
AUTHOR_MARKERS = ("Шкаликов", "Shkalikov")
TARGET_WORDS = 150000
SHORT_WORK_WORDS = 1000

MATHNET_RE = re.compile(r"mi\.mathnet\.ru/rus/([a-z]+)(\d+)")
GETFT_RE = re.compile(r'href="([^"]*getFT[^"]*)"', re.IGNORECASE)
QUOTE_RE = re.compile(r"[\u201c\u201e\"]([^\u201c\u201d\"]{3,400})[\u201d\"]")
CYRILLIC_RE = re.compile(r"[\u0410-\u044f\u0401\u0451]")
TOKEN_RE = re.compile(r"[^\W_]+(?:[-'\u2019][^\W_]+)*", re.UNICODE)
TOKEN_EDGE_RE = re.compile(r"[\u0410-\u044f\u0401\u0451-]+")
MORPH: pymorphy3.MorphAnalyzer | None = None
KNOWN_WORDS: dict[str, bool] = {}
LATIN_RE = re.compile(r"[A-Za-z]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# A line that is nothing but a page number, a formula number, a rule or punctuation.
NOISE_LINE_RES = (
    re.compile(r"^[\s\dIVXLCivxlc.,;\-\u2013\u2014/()\[\]]+$"),
    re.compile(r"^/[A-Za-z]+\d*$"),
    re.compile(r"^\u00a9"),
    re.compile(r"^[c\u0441]\s*[\u00a9\u25cb\u24b8\u20dd\u20de\u20e0]"),
    re.compile(r"^c\s+\d{4}\b"),
    re.compile(r"^(УДК|ББК|ISSN)\b"),
    re.compile(r"^DOI\s*:?\s*(https?://|10\.)"),
    # A running head in capitals closed by the page number, glued to the text.
    re.compile(r"^[А-ЯЁ][А-ЯЁ\s.,\-\u2013]{14,}\s+\d{1,3}$"),
    # A journal footer inside the text block: "Матем. заметки, т. 30, No 3".
    re.compile(
        r"^\d*\s*(?:Матем\.\s*заметки|УМН|Функц\.\s*анализ|Матем\.\s*сб\."
        r"|Сиб\.\s*матем\.|Вестн\.\s*Моск\.\s*ун-та)\b.*\bт\.\s*\d"
    ),
)
ABBREVIATIONS = {
    "т", "е", "др", "пр", "см", "напр", "рис", "табл", "стр", "г", "гг", "в", "вв",
    "п", "пп", "гл", "изд", "ред", "сокр", "теор", "лем", "след", "док", "гос",
    "акад", "им", "проф", "доц", "т.е", "т.д", "т.п", "т.к", "науч",
}
AUTHOR_LINE_RE = re.compile(
    r"(?:[A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.\s*)?[A-ZА-ЯЁ][\w\-Ёё]+(?:\s*\d{1,2})?)"
    r"(?:\s*(?:,|\band\b)\s*"
    r"[A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.\s*)?[A-ZА-ЯЁ][\w\-Ёё]+(?:\s*\d{1,2})?)*"
)
# The address, e-mail and "received" blocks of a printed article are service text.
SERVICE_PARAGRAPH_RE = re.compile(
    r"E-?mail\s*:|[\w.+-]+@[\w-]+\.[\w.]+"
    r"|Поступил[ао]?\s+в\s+редакцию|поступила\s+в\s+редакцию"
    r"|Принято\s+к\s+публикации|Представлено\s+в\s+редакцию"
    r"|^Статья\s+поступила"
    r"|^Московский\s+государственный|^МГУ им\."
    r"|^Andrei Andreevich|^Андрей Андреевич Шкаликов"
)
# A text layer that draws every line twice: the same word follows itself.
DOUBLE_WORD_RE = re.compile(r"([\w\u0410-\u044f\u0401\u0451]{4,})\1")
DOUBLED_SHARE_MAX = 0.01
SECTION_HEAD_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+[А-ЯЁ]"
    r"|Литература\b|Список литературы\b|Введение\b|Заключение\b"
    r"|Приложение\b|References\b|Abstract\b)"
)
# A note about a person: life dates ("1914–2004", "25 марта 1940 г. — 18 мая 2020 г.")
# or a bare name ("Жиков Василий Васильевич") in the title.
LIFE_DATES_RE = re.compile(
    r"\d{1,2}\.\d{1,2}\.\d{4}\s*[\u2013\u2014-]\s*\d{1,2}\.\d{1,2}\.\d{4}"
    r"|\d{1,2}\s+[а-яё]{3,}\s+\d{4}\s*г\.\s*[\u2013\u2014-]\s*"
    r"\d{1,2}\s+[а-яё]{3,}\s+\d{4}"
    r"|\b(?:1[89]|20)\d{2}\s*[\u2013\u2014-]\s*(?:1[89]|20)\d{2}\b"
)
PERSON_NAME_RE = re.compile(
    r"^[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+){1,2}\.?(?:\s*\([^)]*\))?$"
)


@dataclass
class Entry:
    """One bibliography entry with the fields the manifest needs."""

    mathnet: str
    journal_id: str
    paper_id: str
    raw: str
    citation_ru: str
    authors: list[str]
    title: str
    journal: str
    year: int | None
    pages: str
    reason: str = ""
    url: str = ""


# --------------------------------------------------------------------------- text


def norm_space(value: str) -> str:
    """Normalise one line: NFKC, no control characters, single spaces.

    A soft hyphen stays: at the end of a line it is the only mark of a word split
    by the typesetter, and dropping it here would leave the two halves apart.
    """
    value = unicodedata.normalize("NFKC", value)
    value = CONTROL_RE.sub("", value)
    value = value.replace("\u00a0", " ").replace("\u200b", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def normal_key(value: str) -> str:
    """Letters only, lower case: the key used to spot a repeated running head."""
    return re.sub(r"[^а-яёa-z]", "", value.lower())


def count_words(text: str) -> int:
    """Words of the corpus text: whitespace-separated tokens, len(text.split()).

    The literary corpus counts the same way, so the numbers of both corpora compare
    and the manifest reports what a reader of the file sees.
    """
    return len(text.split())


def paragraph_is_text(paragraph: str) -> bool:
    """Whether a restored paragraph carries at least two tokens of letters.

    The filter keeps the paragraph set the corpus was reviewed with, so a change of
    the word counter cannot move text between runs.
    """
    return len(TOKEN_RE.findall(paragraph)) >= 2


def cyrillic_share(text: str) -> float:
    """Share of Cyrillic letters among all letters of the text."""
    cyr = len(CYRILLIC_RE.findall(text))
    lat = len(LATIN_RE.findall(text))
    total = cyr + lat
    return cyr / total if total else 0.0


# ---------------------------------------------------------------------- http part


class Fetcher:
    """Requests with the build user agent, a fixed pause and simple retries."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.requests_made = 0
        self.urls = self._load_url_cache()

    @staticmethod
    def _load_url_cache() -> dict[str, str]:
        if URL_CACHE.exists():
            try:
                return json.loads(URL_CACHE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def remember_url(self, mathnet: str, url: str) -> None:
        self.urls[mathnet] = url
        URL_CACHE.write_text(
            json.dumps(dict(sorted(self.urls.items())), ensure_ascii=False, indent=1),
            encoding="utf-8",
            newline="\n",
        )

    def get(self, url: str, *, timeout: int = 120, attempts: int = 3) -> requests.Response | None:
        for attempt in range(1, attempts + 1):
            if self.requests_made:
                time.sleep(PAUSE_SECONDS)
            try:
                response = self.session.get(url, timeout=timeout)
            except requests.RequestException:
                if attempt == attempts:
                    return None
                time.sleep(3 * attempt)
                continue
            self.requests_made += 1
            if response.status_code == 200:
                return response
            if response.status_code in (404, 410):
                return response
            if attempt == attempts:
                return response
            # 403 and 429 come back when the site throttles a burst of downloads;
            # a pause of tens of seconds brings the next attempt through.
            time.sleep(20 * attempt)
        return None


def load_person_page(fetcher: Fetcher, refresh: bool) -> str:
    """The bibliography page, cached so that a rerun sees the same input."""
    if PAGE_CACHE.exists() and not refresh:
        return PAGE_CACHE.read_text(encoding="utf-8")
    response = fetcher.get(PERSON_URL, timeout=120)
    if response is None or response.status_code != 200:
        status = "no response" if response is None else response.status_code
        raise SystemExit(f"person page is not reachable: {status}")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    PAGE_CACHE.write_text(response.text, encoding="utf-8", newline="\n")
    return response.text


# ------------------------------------------------------------------ bibliography


def split_authors(text: str) -> list[str]:
    """Author names of a citation prefix, split on commas and the word 'and'."""
    text = re.sub(r"\s+", " ", text).strip(" ,;.")
    if not text:
        return []
    parts = re.split(r"\s*,\s*|\s+\band\b\s+", text)
    return [part.strip() for part in parts if part.strip()]


def parse_year(rest: str, raw: str) -> int | None:
    """Publication year: the last year written as (YYYY) or before the pages."""
    in_brackets = re.findall(r"\((\d{4})\)", rest)
    if in_brackets:
        return int(in_brackets[-1])
    after_comma = re.findall(r",\s*(\d{4})\b", rest)
    if after_comma:
        return int(after_comma[-1])
    loose = re.findall(r"\b(19\d{2}|20\d{2})\b", rest)
    if loose:
        return int(loose[-1])
    every = re.findall(r"\b(19\d{2}|20\d{2})\b", raw)
    return int(max(every)) if every else None


def parse_pages(rest: str) -> str:
    """Page range of the citation, en dash normalised."""
    found = re.findall(r"(\d+)\s*[\u2013\u2014-]\s*(\d+)", rest)
    if not found:
        return ""
    first, last = found[-1]
    return f"{first}\u2013{last}"


def parse_journal(before_year: str) -> str:
    """Journal name: the citation text before its volume number."""
    match = re.search(r",\s*\d", before_year)
    name = before_year[: match.start()] if match else before_year
    return name.strip(" ,;\u2013\u2014-")


def parse_entry(mathnet: str, journal_id: str, paper_id: str, raw: str) -> Entry:
    """Split one bibliography entry into authors, title, journal, year, pages."""
    match = QUOTE_RE.search(raw)
    if match:
        title = match.group(1).strip()
        authors_text = raw[: match.start()]
        rest = raw[match.end() :]
    else:
        title = ""
        authors_text = ""
        rest = raw

    # The Russian half of a bilingual entry ends at the first semicolon
    # after the title; the English translation follows it.
    semicolon = rest.find(";")
    offset = len(raw) - len(rest)
    if semicolon < 0:
        russian_rest = rest
        citation_ru = raw
    else:
        russian_rest = rest[:semicolon]
        citation_ru = raw[: offset + semicolon]

    year = parse_year(russian_rest, raw)
    pages = parse_pages(russian_rest)
    cut = russian_rest
    if year is not None:
        for pattern in (f"({year})", f", {year}", f" {year}"):
            position = cut.find(pattern)
            if position >= 0:
                cut = cut[:position]
                break
        else:
            years = [m.start() for m in re.finditer(r"\b(19\d{2}|20\d{2})\b", cut)]
            if years:
                cut = cut[: years[-1]]
    journal = parse_journal(cut)

    return Entry(
        mathnet=mathnet,
        journal_id=journal_id,
        paper_id=paper_id,
        raw=raw,
        citation_ru=citation_ru,
        authors=split_authors(authors_text),
        title=title,
        journal=journal,
        year=year,
        pages=pages,
    )


def parse_bibliography(html: str) -> list[Entry]:
    """Every bibliography entry that carries a MathNet identifier, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[Entry] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=MATHNET_RE):
        match = MATHNET_RE.search(link["href"])
        if not match:
            continue
        journal_id, paper_id = match.group(1), match.group(2)
        mathnet = f"{journal_id}{paper_id}"
        if mathnet in seen:
            continue
        seen.add(mathnet)
        cell = link.find_parent("td") or link.parent
        raw = norm_space(cell.get_text(" ", strip=True))
        entries.append(parse_entry(mathnet, journal_id, paper_id, raw))
    return entries


def is_shkalikov(name: str) -> bool:
    return any(marker.lower() in name.lower() for marker in AUTHOR_MARKERS)


def classify(entry: Entry) -> str:
    """Exclusion reason of an entry, or an empty string for a real article."""
    title = entry.title.lower()
    citation = entry.citation_ru.lower()
    if (
        title.startswith("памяти")
        or "некролог" in citation
        or "in memoriam" in citation
        or "in memory of" in citation
        or LIFE_DATES_RE.search(title)
    ):
        return "некролог"
    if (
        re.search(r"к\s+\d{2,3}\s*[-\u2010-\u2015]?\s*летию", title)
        or re.search(r"к\s+(?:шести|семи|восьми|девяноста|сто)[летию]{0,6}", title)
        or "поздравляем" in title
        or "юбилей" in title
    ):
        return "юбилейное поздравление"
    if re.search(r"\d{2,3}\s+лет\s+со\s+дня\s+рождения", title):
        return "юбилейная статья о человеке"
    if PERSON_NAME_RE.match(entry.title):
        return "заметка о человеке (юбилей или память)"
    if len(entry.authors) >= 7 and not (entry.authors and is_shkalikov(entry.authors[0])):
        return f"коллективный текст: {len(entry.authors)} авторов, Шкаликов не первый"
    if not CYRILLIC_RE.search(citation):
        return "английский текст без русского оригинала"
    return ""


# ------------------------------------------------------------------------- pdf


def landing_pdf_url(fetcher: Fetcher, entry: Entry) -> str:
    """The Russian full-text link of the paper, taken from its MathNet page."""
    response = fetcher.get(f"{SITE}/rus/{entry.mathnet}", timeout=60)
    if response is None or response.status_code != 200:
        return ""
    for raw_link in GETFT_RE.findall(response.text):
        link = html_module.unescape(raw_link)
        if "what=fullt" in link and "option_lang=rus" in link and "fullteng" not in link:
            return urljoin(SITE, link)
    return ""


def download_pdf(fetcher: Fetcher, entry: Entry, refresh: bool) -> tuple[bytes | None, str]:
    """PDF bytes of the paper and its getFT address; None when the text is closed."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    cached = PDF_DIR / f"{entry.mathnet}.pdf"
    if cached.exists() and not refresh:
        blob = cached.read_bytes()
        if blob[:5] == b"%PDF-":
            return blob, fetcher.urls.get(entry.mathnet, "")

    # A known full-text address saves the landing page and one request: the site
    # throttles a burst, so every request that can be skipped counts.
    known = fetcher.urls.get(entry.mathnet, "")
    if known and not refresh:
        response = fetcher.get(known, timeout=180)
        blob = response.content if response is not None and response.status_code == 200 else b""
        if blob[:5] == b"%PDF-" and len(blob) >= 10000:
            cached.write_bytes(blob)
            return blob, known

    url = landing_pdf_url(fetcher, entry)
    if not url:
        return None, ""
    response = fetcher.get(url, timeout=180)
    if response is None or response.status_code != 200:
        return None, url
    blob = response.content
    if blob[:5] != b"%PDF-" or len(blob) < 10000:
        return None, url
    cached.write_bytes(blob)
    fetcher.remember_url(entry.mathnet, url)
    return blob, url


def read_pdf_pages(blob: bytes, scratch: Path) -> tuple[list[list[str]], int]:
    """Text lines of every PDF page and the page count."""
    scratch.write_bytes(blob)
    reader = PdfReader(str(scratch))
    pages: list[list[str]] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # a broken page must not kill the whole build
            text = ""
        pages.append([norm_space(line) for line in text.splitlines()])
    return pages, len(reader.pages)


# ---------------------------------------------------------------------- cleaning


def is_noise_line(line: str) -> bool:
    return any(pattern.match(line) for pattern in NOISE_LINE_RES)


def title_prefix_matches(line: str, title_key: str) -> bool:
    """Whether a line is the beginning of the article title, split or whole."""
    key = normal_key(line)
    if len(key) < 8:
        return False
    return key.startswith(title_key[:12]) or title_key.startswith(key)


def title_word_hits(line_key: str, title_words: list[str]) -> int:
    """How many long words of the title appear in the line, in order."""
    hits = 0
    position = 0
    for word in title_words:
        found = line_key.find(word, position)
        if found < 0:
            continue
        hits += 1
        position = found + len(word)
    return hits


def cut_masthead(page: list[str], title: str, running: set[str]) -> list[str]:
    """Drop the journal masthead above the article title on the first page."""
    title_key = normal_key(title)
    if title_key:
        for index, line in enumerate(page):
            if line and title_prefix_matches(line, title_key):
                return page[index:]
        # A scanned page may garble the title ("О БАЗЖСНОСТИ ...") while most of
        # its long words stay readable: three of them in one line mark the title.
        title_words = [normal_key(word) for word in title.split()]
        title_words = [word for word in title_words if len(word) >= 5]
        for index, line in enumerate(page[:12]):
            if line and title_word_hits(normal_key(line), title_words) >= 3:
                return page[index:]
        # The printed title may differ from the citation: it can be shorter
        # ("Инвариантные подпространства..." for a paper with a longer name
        # in the bibliography), so look for the longest tail of the title.
        words = [normal_key(word) for word in title.split() if normal_key(word)]
        for start in range(1, max(1, len(words) - 2)):
            window = "".join(words[start:])
            if len(window) < 20:
                continue
            for index, line in enumerate(page):
                if line and window in normal_key(line):
                    return page[index:]
    last_udc = -1
    for index, line in enumerate(page):
        if re.match(r"^(УДК|ББК)\b", line):
            last_udc = index
    if last_udc >= 0:
        return page[last_udc + 1 :]
    start = 0
    while start < len(page) and (not page[start] or normal_key(page[start]) in running):
        start += 1
    return page[start:]


def running_line_keys(pages: list[list[str]]) -> set[str]:
    """Letter-only keys of lines repeated at the top or bottom of many pages."""
    if len(pages) < 2:
        return set()
    counts: Counter[str] = Counter()
    for page in pages:
        filled = [index for index, line in enumerate(page) if line]
        if not filled:
            continue
        keys = {normal_key(page[index]) for index in filled[:2] + filled[-2:]}
        for key in keys:
            if len(key) >= 6:
                counts[key] += 1
    threshold = max(2, math.ceil(len(pages) * 0.4))
    return {key for key, count in counts.items() if count >= threshold}


def ends_sentence(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped or stripped[-1] not in ".!?\u2026":
        return False
    word = re.search(r"([\w.Ёё]{1,6})\.$", stripped)
    if word and word.group(1).lower().strip(".") in ABBREVIATIONS:
        return False
    return True


def starts_new(line: str) -> bool:
    first = line[:1]
    return first.isupper() or first.isdigit() or first in "«“([•-"


def should_break(previous: str, current: str) -> bool:
    """Paragraph boundary: a finished sentence followed by a capital letter."""
    if previous.endswith(":") and starts_new(current):
        return True
    return ends_sentence(previous) and starts_new(current)


def glue(previous: str, current: str) -> str:
    """Join a line break inside a hyphenated word or a compound name.

    A soft hyphen is the break marker of a word split by the typesetter: the word
    goes on without the hyphen, so the marker itself is dropped.
    """
    if previous.endswith("\u00ad"):
        return previous[:-1] + current
    if previous.endswith("\u2013") or previous.endswith("\u2014"):
        return previous + current
    if previous.endswith("-"):
        if current[:1].islower():
            return previous[:-1] + current
        return previous + current
    return previous + " " + current


def tidy_paragraph(text: str) -> str:
    """Spaces, punctuation gaps and stray marks of a restored paragraph."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,;:.!?)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return text.strip()


def clean_soft_hyphens(line: str) -> str:
    """Keep a soft hyphen only at the end, where it marks a word break."""
    if line.endswith("\u00ad"):
        return line[:-1].rstrip() + "\u00ad"
    return line.replace("\u00ad", "")


def morph_analyzer() -> pymorphy3.MorphAnalyzer:
    """The Russian dictionary, built once for the whole build."""
    global MORPH
    if MORPH is None:
        MORPH = pymorphy3.MorphAnalyzer()
    return MORPH


def is_known_word(word: str) -> bool:
    """Whether the dictionary knows the word in at least one reading."""
    key = word.lower()
    if key not in KNOWN_WORDS:
        KNOWN_WORDS[key] = any(parse.is_known for parse in morph_analyzer().parse(key))
    return KNOWN_WORDS[key]


def glue_split_words(lines: list[str]) -> tuple[list[str], int]:
    """Glue a word that a line break split without leaving a hyphen.

    The tail of one line and the head of the next are a broken word when the head
    is not a word by itself while the whole is one.
    """
    result = list(lines)
    glued = 0
    for index in range(len(result) - 1):
        left, right = result[index], result[index + 1]
        if not left or not right:
            continue
        tails = TOKEN_EDGE_RE.findall(left)
        head = TOKEN_EDGE_RE.search(right)
        if not tails or not head:
            continue
        tail, head_text = tails[-1], head.group(0)
        if len(head_text) < 2 or is_known_word(head_text) or not is_known_word(tail + head_text):
            continue
        result[index] = left[: left.rfind(tail)].rstrip()
        result[index + 1] = right[: head.start()] + tail + head_text + right[head.end():]
        glued += 1
    return result, glued


def prepare_lines(
    page: list[str], title: str, running: set[str], first_page: bool
) -> tuple[list[str], int]:
    """Cut the masthead and the running lines, then glue broken words."""
    lines = cut_masthead(page, title, running) if first_page else page
    # The first page with text carries the real title and the author line, and
    # they look exactly like the running head of the other pages; only its footer
    # is cut.
    footer = set()
    if first_page:
        filled = [index for index, line in enumerate(lines) if line]
        footer = set(filled[-2:])
    prepared: list[str] = []
    for index, line in enumerate(lines):
        if is_noise_line(line):
            continue
        if normal_key(line) in running and (not first_page or index in footer):
            continue
        prepared.append(clean_soft_hyphens(line))
    return glue_split_words(prepared)


def build_paragraphs(
    pages: list[list[str]], title: str, running: set[str]
) -> tuple[list[str], int, int]:
    """Restore paragraphs from the lines of every page.

    Returns the paragraphs and two counts: breaks joined by a soft hyphen and
    breaks joined by the dictionary.
    """
    paragraphs: list[str] = []
    buffer: list[str] = []
    previous = ""
    soft_joined = 0

    def flush() -> None:
        nonlocal buffer, previous
        if buffer:
            paragraph = tidy_paragraph(" ".join(buffer))
            if paragraph and CYRILLIC_RE.search(paragraph) and paragraph_is_text(paragraph):
                paragraphs.append(paragraph)
        buffer = []
        previous = ""

    first_page = next(
        (index for index, page in enumerate(pages) if any(page)),
        0,
    )
    dict_joined = 0
    for page_index, page in enumerate(pages):
        lines, glued = prepare_lines(page, title, running, page_index == first_page)
        dict_joined += glued
        for line in lines:
            if not line:
                flush()
                continue
            if buffer and previous and previous.endswith(("\u00ad", "-", "\u2013", "\u2014")):
                if previous.endswith("\u00ad"):
                    soft_joined += 1
                buffer[-1] = glue(previous, line)
                previous = buffer[-1]
                continue
            if buffer and previous and (SECTION_HEAD_RE.match(line) or should_break(previous, line)):
                flush()
            buffer.append(line)
            previous = line
            if AUTHOR_LINE_RE.fullmatch(line):
                flush()
    flush()
    return paragraphs, soft_joined, dict_joined


def pdf_to_text(
    blob: bytes, title: str, scratch: Path
) -> tuple[str, int, int, int, int, int]:
    """Corpus text of one PDF plus its build numbers.

    Page count, running lines dropped, raw characters (a scan has no text layer, a
    text layer in a custom font encoding has raw characters but no readable text),
    breaks joined by a soft hyphen and breaks joined by the dictionary.
    """
    pages, page_count = read_pdf_pages(blob, scratch)
    raw_chars = sum(len(line) for page in pages for line in page)
    running = running_line_keys(pages)
    dropped = sum(1 for page in pages for line in page if normal_key(line) in running)
    built, soft_joined, dict_joined = build_paragraphs(pages, title, running)
    paragraphs = [
        paragraph
        for paragraph in built
        if not SERVICE_PARAGRAPH_RE.search(paragraph)
        and not AUTHOR_LINE_RE.fullmatch(paragraph)
    ]
    text = "\n\n".join(paragraphs)
    if text:
        text += "\n"
    return text, page_count, dropped, raw_chars, soft_joined, dict_joined


def language_ok(text: str) -> bool:
    """A Russian paper: Cyrillic letters dominate the extracted text."""
    return cyrillic_share(text) >= 0.3


def is_doubled_text(text: str) -> bool:
    """A PDF whose text layer draws every line twice, word over the same word."""
    words = re.findall(r"[^\W_]+", text)
    if len(words) < 200:
        return False
    doubled = sum(1 for word in words if DOUBLE_WORD_RE.search(word))
    return doubled / len(words) > DOUBLED_SHARE_MAX


def cited_pages(pages: str) -> int:
    """How many printed pages the bibliography gives the paper."""
    match = re.match(r"(\d+)\s*[\u2013\u2014-]\s*(\d+)", pages or "")
    if not match:
        return 0
    return int(match.group(2)) - int(match.group(1)) + 1


# ---------------------------------------------------------------------- writing


def work_notes(page_count: int, dropped: int) -> str:
    return (
        f"{page_count} с. PDF; колонтитулы и колонцифры сняты ({dropped} строк); "
        "переносы склеены; абзацы восстановлены эвристикой «конец строки точкой или "
        "двоеточием, следующая строка с заглавной», границы приблизительны; формулы "
        "искажены текстовым слоем PDF, часть слов слиплась."
    )


def dump_json(path: Path, document: dict) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")


def content_of(manifest: dict) -> dict:
    return {key: value for key, value in manifest.items() if key != "built"}


def write_manifest(works: list[dict], excluded: list[dict], words: int) -> dict:
    """Manifest with a stable built stamp: it changes only with the content."""
    document = {
        "version": 1,
        "built": "",
        "author": "shkalikov",
        "use": "exemplar",
        "notes": (
            "words — число разделённых пробелами отрезков текста (len(text.split())), "
            "как в литературном корпусе; chars — длина файла в знаках; считаются по "
            "файлу corpus/math/shkalikov/<mathnet>.txt, который лежит в дереве. "
            "Разрывы слов склеены: концевой мягкий перенос и словарная проверка "
            "pymorphy3 (голова разрыва не слово, а склейка — слово)."
        ),
        "coverage": {"shkalikov": {"words": words, "works": len(works), "target": TARGET_WORDS}},
        "source": {
            "site": "mathnet",
            "person": PERSON_ID,
            "url": "https://www.mathnet.ru/php/person.phtml?personid=12042",
        },
        "works": works,
        "excluded": excluded,
    }
    previous: dict = {}
    if MANIFEST.exists():
        try:
            previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    if previous.get("built") and content_of(previous) == content_of(document):
        document["built"] = previous["built"]
    else:
        document["built"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dump_json(MANIFEST, document)
    return document


# ------------------------------------------------------------------------ checks


WORK_FIELDS = (
    "id", "mathnet", "title", "journal", "year", "pages", "url", "file",
    "chars", "words", "sha256", "notes",
)


def validate_manifest() -> list[str]:
    """Check the manifest against the files it describes; no problems means good.

    The words and chars of a work are recomputed from its text file, so a number
    that came from another text cannot pass: this is the check that matters for
    the numbers the plugin shows a reader.
    """
    problems: list[str] = []
    if not MANIFEST.exists():
        return ["manifest is missing"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in ("version", "built", "author", "use", "coverage", "source", "works", "excluded"):
        if key not in manifest:
            problems.append(f"manifest misses {key}")
    seen: set[str] = set()
    total_words = 0
    total_chars = 0
    for work in manifest.get("works", []):
        ident = work.get("mathnet", "?")
        empty = [field for field in WORK_FIELDS if not work.get(field)]
        if empty:
            problems.append(f"{ident}: empty or missing {empty}")
            continue
        if ident in seen:
            problems.append(f"{ident}: duplicate work")
        seen.add(ident)
        path = ROOT / work["file"]
        if not path.exists():
            problems.append(f"{ident}: file is missing")
            continue
        text = path.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != work["sha256"]:
            problems.append(f"{ident}: sha256 differs from the file")
        if len(text) != work["chars"]:
            problems.append(f"{ident}: chars {work['chars']} differs from {len(text)}")
        if count_words(text) != work["words"]:
            problems.append(f"{ident}: words {work['words']} differs from {count_words(text)}")
        total_words += work["words"]
        total_chars += work["chars"]
    coverage = manifest.get("coverage", {}).get("shkalikov", {})
    if coverage.get("words") != total_words:
        problems.append(f"coverage words {coverage.get('words')} differs from {total_words}")
    if coverage.get("works") != len(seen):
        problems.append(f"coverage works {coverage.get('works')} differs from {len(seen)}")
    excluded_ids = {item.get("mathnet") for item in manifest.get("excluded", [])}
    if excluded_ids & seen:
        problems.append(f"works and excluded share {sorted(excluded_ids & seen)}")
    for item in manifest.get("excluded", []):
        if set(item) != {"mathnet", "title", "reason"}:
            problems.append(f"{item.get('mathnet', '?')}: excluded fields {sorted(item)}")
        elif not item["reason"] or not item["title"]:
            problems.append(f"{item['mathnet']}: excluded without title or reason")
    pdfs = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.pdf")
        if ".tmp" not in path.parts and ".venv" not in path.parts
    ]
    if pdfs:
        problems.append(f"pdf inside the repository: {pdfs[:5]}")
    return problems


def write_validation(problems: list[str]) -> None:
    report = [
        "manifest validation: " + ("ok" if not problems else f"{len(problems)} problems"),
        "",
    ]
    report += problems
    VALIDATION_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")


def cleanup_pdf_cache() -> None:
    """Remove the downloaded PDFs: only the build report stays in .tmp."""
    if PDF_DIR.exists():
        for path in PDF_DIR.glob("*.pdf"):
            path.unlink()
        if not any(PDF_DIR.iterdir()):
            PDF_DIR.rmdir()
    scratch = TMP_DIR / "current.pdf"
    if scratch.exists():
        scratch.unlink()


# ------------------------------------------------------------------------ main


def write_classify_report(entries: list[Entry]) -> None:
    reasons: Counter[str] = Counter()
    lines = []
    for entry in entries:
        entry.reason = classify(entry)
        reasons[entry.reason or "article"] += 1
        lines.append(
            f"{entry.mathnet}\t{entry.year}\t{len(entry.authors)}\t{entry.journal}\t"
            f"{entry.pages}\t{entry.reason or 'article'}\t{entry.title}"
        )
    header = [
        f"entries: {len(entries)}",
        "by kind: " + ", ".join(f"{key}={value}" for key, value in sorted(reasons.items())),
        "",
    ]
    CLASSIFY_REPORT.write_text("\n".join(header + lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    refresh = "--refresh" in argv
    classify_only = "--classify-only" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0

    if "--validate" in argv:
        problems = validate_manifest()
        write_validation(problems)
        print(f"manifest_problems={len(problems)} report={VALIDATION_REPORT}")
        for problem in problems[:20]:
            print(f"  {problem}")
        return 1 if problems else 0

    started = time.time()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher()
    page = load_person_page(fetcher, refresh)
    entries = parse_bibliography(page)
    if not entries:
        raise SystemExit("no bibliography entries parsed")
    if classify_only:
        write_classify_report(entries)
        print(f"entries={len(entries)}")
        return 0

    works: list[dict] = []
    texts: dict[str, str] = {}
    excluded: list[dict] = []
    short_works: list[str] = []
    english_after_pdf: list[str] = []
    blank_pdf: list[str] = []
    unreadable: list[str] = []
    odd_pages: list[str] = []
    doubled: list[str] = []
    download_failures = 0
    download_seconds = 0.0
    soft_breaks = 0
    dict_breaks = 0
    scratch = TMP_DIR / "current.pdf"

    for entry in entries:
        entry.reason = classify(entry)
        if entry.reason:
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": entry.reason,
            })
            continue

        download_started = time.time()
        blob, url = download_pdf(fetcher, entry, refresh)
        download_seconds += time.time() - download_started
        if blob is None:
            download_failures += 1
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": "нет открытого полного текста",
            })
            continue

        text, page_count, dropped, raw_chars, soft_joined, dict_joined = pdf_to_text(
            blob, entry.title, scratch
        )
        soft_breaks += soft_joined
        dict_breaks += dict_joined
        printed = cited_pages(entry.pages)
        if printed and page_count > max(3 * printed, printed + 8):
            odd_pages.append(entry.mathnet)
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": "PDF содержит выпуск журнала, а не статью",
            })
            continue
        if not text or count_words(text) < 50:
            if raw_chars < 200:
                blank_pdf.append(entry.mathnet)
                reason = "PDF без текстового слоя (скан)"
            else:
                unreadable.append(entry.mathnet)
                reason = "текстовый слой PDF не декодируется"
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": reason,
            })
            continue
        if not language_ok(text):
            english_after_pdf.append(entry.mathnet)
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": "английский текст без русского оригинала",
            })
            continue
        if is_doubled_text(text):
            doubled.append(entry.mathnet)
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": "текстовый слой PDF дублируется",
            })
            continue

        words = count_words(text)
        if words < SHORT_WORK_WORDS:
            short_works.append(entry.mathnet)
        # Written only after the stop conditions below: a build that loses most
        # downloads must leave the corpus that is already on disk untouched.
        texts[entry.mathnet] = text
        works.append({
            "id": f"shkalikov/{entry.mathnet}",
            "mathnet": entry.mathnet,
            "title": entry.title,
            "journal": entry.journal,
            "year": entry.year,
            "pages": entry.pages,
            "url": url,
            "file": f"corpus/math/shkalikov/{entry.mathnet}.txt",
            "chars": len(text),
            "words": words,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "notes": work_notes(page_count, dropped),
        })
        if limit and len(works) >= limit:
            break

    works.sort(key=lambda item: (item["year"] or 0, item["mathnet"]))
    excluded.sort(key=lambda item: (item["title"], item["mathnet"]))
    total_words = sum(item["words"] for item in works)
    total_chars = sum(item["chars"] for item in works)

    reasons = Counter(item["reason"] for item in excluded)
    downloaded = len(works) + len(blank_pdf) + len(unreadable) + len(odd_pages) + len(doubled)
    attempts = downloaded + download_failures
    failure_share = download_failures / attempts if attempts else 0.0
    thin = len(short_works) + len(blank_pdf) + len(unreadable) + len(doubled)
    thin_share = thin / downloaded if downloaded else 0.0
    stopped = limit == 0 and (failure_share > 0.2 or thin_share > 0.2)

    # The tree is replaced only by a build that passed the stop conditions, so a
    # run that loses most downloads leaves the corpus it found on disk.
    if stopped:
        document = {"built": "not written: the build stopped before writing"}
    else:
        keep = {f"{item['mathnet']}.txt" for item in works}
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        for mathnet, text in texts.items():
            (TEXT_DIR / f"{mathnet}.txt").write_text(text, encoding="utf-8", newline="\n")
        for stale in TEXT_DIR.glob("*.txt"):
            if stale.name not in keep:
                stale.unlink()
        document = write_manifest(works, excluded, total_words)

    report = [
        f"built: {document['built']}",
        f"person: {PERSON_URL}",
        "",
        f"entries in bibliography: {len(entries)}",
        f"works in corpus: {len(works)}",
        f"excluded: {len(excluded)}",
    ]
    report += [f"  {reason}: {count}" for reason, count in sorted(reasons.items())]
    report += [
        "",
        f"total words: {total_words}",
        f"total chars: {total_chars}",
        f"works below {SHORT_WORK_WORDS} words: {len(short_works)}"
        + (" (" + ", ".join(short_works) + ")" if short_works else ""),
        f"word breaks glued: {soft_breaks + dict_breaks} "
        f"(soft hyphen {soft_breaks}, dictionary {dict_breaks})",
        f"pdf with fewer than {SHORT_WORK_WORDS} words: {thin} of {downloaded} "
        f"({thin_share:.1%})",
        f"download attempts: {attempts}, failures: {download_failures} ({failure_share:.1%})",
        f"download seconds: {download_seconds:.1f}",
        f"total seconds: {time.time() - started:.1f}",
        f"english texts found after extraction: {len(english_after_pdf)}"
        + (" (" + ", ".join(english_after_pdf) + ")" if english_after_pdf else ""),
        f"pdf that is a scan: {len(blank_pdf)}"
        + (" (" + ", ".join(blank_pdf) + ")" if blank_pdf else ""),
        f"pdf with an undecodable text layer: {len(unreadable)}"
        + (" (" + ", ".join(unreadable) + ")" if unreadable else ""),
        f"pdf of a whole issue instead of the paper: {len(odd_pages)}"
        + (" (" + ", ".join(odd_pages) + ")" if odd_pages else ""),
        f"pdf with a doubled text layer: {len(doubled)}"
        + (" (" + ", ".join(doubled) + ")" if doubled else ""),
        "",
        "stop conditions:",
        f"  download failures above 20%: {'YES' if failure_share > 0.2 else 'no'}",
        f"  pdf with fewer than {SHORT_WORK_WORDS} words above 20%: "
        f"{'YES' if thin_share > 0.2 else 'no'}",
        "",
        "works:",
    ]
    report += [
        f"  {item['mathnet']:<9} {item['year']}  {item['words']:>6} сл.  "
        f"{item['journal']}  с. {item['pages']}"
        for item in works
    ]
    report.append("")
    report.append("excluded:")
    report += [f"  {item['mathnet']:<9} {item['reason']}: {item['title']}" for item in excluded]
    if limit:
        report.append("")
        report.append(f"DEVELOPMENT RUN: only {limit} articles processed")
    BUILD_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")

    if not stopped:
        cleanup_pdf_cache()
    print(f"entries={len(entries)} works={len(works)} excluded={len(excluded)}")
    print(f"words={total_words} chars={total_chars} short_works={len(short_works)}")
    print(f"download_failures={download_failures}/{attempts} seconds={download_seconds:.1f}")
    print(f"report={BUILD_REPORT}")
    if stopped:
        print("STOP CONDITION TRIGGERED: the corpus on disk is unchanged")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
