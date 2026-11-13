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

Repeated runs are byte-identical: the person page and the PDFs are cached under
.tmp, and the manifest keeps the "built" stamp of the previous run whenever the
computed content is unchanged.  Pass --refresh to refetch the page and the PDFs.

Options:
    --refresh         refetch the person page and every PDF
    --classify-only   parse and classify the bibliography, write .tmp/classify.txt
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
WORD_RE = re.compile(r"[^\W_]+(?:[-'\u2019][^\W_]+)*", re.UNICODE)
CYRILLIC_RE = re.compile(r"[\u0410-\u044f\u0401\u0451]")
LATIN_RE = re.compile(r"[A-Za-z]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# A line that is nothing but a page number, a formula number, a rule or punctuation.
NOISE_LINE_RES = (
    re.compile(r"^[\s\dIVXLCivxlc.,;\-\u2013\u2014/()\[\]]+$"),
    re.compile(r"^/[A-Za-z]+\d*$"),
    re.compile(r"^\u00a9"),
    re.compile(r"^[c\u0441]\s*[\u00a9\u25cb\u24b8]"),
    re.compile(r"^c\s+\d{4}\b"),
    re.compile(r"^(УДК|ББК|ISSN)\b"),
    re.compile(r"^DOI\s*:?\s*(https?://|10\.)"),
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
    r"|Поступил[ао]?\s+в\s+редакцию|Принято\s+к\s+публикации"
    r"|Представлено\s+в\s+редакцию"
    r"|^Московский государственный университет"
    r"|^МГУ им\.|^Andrei Andreevich|^Андрей Андреевич Шкаликов"
)
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
    """Normalise one line: NFKC, no control characters, single spaces."""
    value = unicodedata.normalize("NFKC", value)
    value = CONTROL_RE.sub("", value)
    value = value.replace("\u00a0", " ").replace("\u00ad", "").replace("\u200b", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def normal_key(value: str) -> str:
    """Letters only, lower case: the key used to spot a repeated running head."""
    return re.sub(r"[^а-яёa-z]", "", value.lower())


def count_words(text: str) -> int:
    """Words of a text: letter/digit runs, hyphens and apostrophes kept inside."""
    return len(WORD_RE.findall(text))


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
                time.sleep(2 * attempt)
                continue
            self.requests_made += 1
            if response.status_code == 200:
                return response
            if response.status_code in (403, 404, 410):
                return response
            if attempt == attempts:
                return response
            time.sleep(2 * attempt)
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


def cut_masthead(page: list[str], title: str, running: set[str]) -> list[str]:
    """Drop the journal masthead above the article title on the first page."""
    title_key = normal_key(title)
    if title_key:
        for index, line in enumerate(page):
            if line and title_prefix_matches(line, title_key):
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
    """Join a line break inside a hyphenated word or a compound name."""
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


def build_paragraphs(pages: list[list[str]], title: str, running: set[str]) -> list[str]:
    """Restore paragraphs from the lines of every page."""
    paragraphs: list[str] = []
    buffer: list[str] = []
    previous = ""

    def flush() -> None:
        nonlocal buffer, previous
        if buffer:
            paragraph = tidy_paragraph(" ".join(buffer))
            if paragraph and CYRILLIC_RE.search(paragraph) and count_words(paragraph) >= 2:
                paragraphs.append(paragraph)
        buffer = []
        previous = ""

    first_page = next(
        (index for index, page in enumerate(pages) if any(page)),
        0,
    )
    for page_index, page in enumerate(pages):
        lines = cut_masthead(page, title, running) if page_index == first_page else page
        # The first page with text carries the real title and the author line, and
        # they look exactly like the running head of the other pages; only its
        # footer is cut.
        footer = set()
        if page_index == first_page:
            filled = [index for index, line in enumerate(lines) if line]
            footer = set(filled[-2:])
        for index, line in enumerate(lines):
            if not line:
                flush()
                continue
            if is_noise_line(line):
                continue
            if normal_key(line) in running and (page_index != first_page or index in footer):
                continue
            if buffer and previous and previous.endswith(("-", "\u2013", "\u2014")):
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
    return paragraphs


def pdf_to_text(blob: bytes, title: str, scratch: Path) -> tuple[str, int, int]:
    """Corpus text of one PDF plus the page count and the running lines dropped."""
    pages, page_count = read_pdf_pages(blob, scratch)
    running = running_line_keys(pages)
    dropped = sum(1 for page in pages for line in page if normal_key(line) in running)
    paragraphs = [
        paragraph
        for paragraph in build_paragraphs(pages, title, running)
        if not SERVICE_PARAGRAPH_RE.search(paragraph)
        and not AUTHOR_LINE_RE.fullmatch(paragraph)
    ]
    text = "\n\n".join(paragraphs)
    if text:
        text += "\n"
    return text, page_count, dropped


def language_ok(text: str) -> bool:
    """A Russian paper: Cyrillic letters dominate the extracted text."""
    return cyrillic_share(text) >= 0.3


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
    excluded: list[dict] = []
    short_works: list[str] = []
    english_after_pdf: list[str] = []
    no_text_layer: list[str] = []
    download_failures = 0
    download_seconds = 0.0
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

        text, page_count, dropped = pdf_to_text(blob, entry.title, scratch)
        if not text or count_words(text) < 50:
            no_text_layer.append(entry.mathnet)
            excluded.append({
                "mathnet": entry.mathnet,
                "title": entry.title or entry.raw[:200],
                "reason": "нет текстового слоя в PDF",
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

        words = count_words(text)
        if words < SHORT_WORK_WORDS:
            short_works.append(entry.mathnet)
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        (TEXT_DIR / f"{entry.mathnet}.txt").write_text(text, encoding="utf-8", newline="\n")
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

    keep = {f"{item['mathnet']}.txt" for item in works}
    if TEXT_DIR.exists():
        for stale in TEXT_DIR.glob("*.txt"):
            if stale.name not in keep:
                stale.unlink()

    document = write_manifest(works, excluded, total_words)

    reasons = Counter(item["reason"] for item in excluded)
    attempts = len(works) + download_failures
    failure_share = download_failures / attempts if attempts else 0.0
    short_share = len(short_works) / len(works) if works else 0.0

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
        f"download attempts: {attempts}, failures: {download_failures} ({failure_share:.1%})",
        f"download seconds: {download_seconds:.1f}",
        f"total seconds: {time.time() - started:.1f}",
        f"english texts found after extraction: {len(english_after_pdf)}"
        + (" (" + ", ".join(english_after_pdf) + ")" if english_after_pdf else ""),
        f"pdf without a text layer: {len(no_text_layer)}"
        + (" (" + ", ".join(no_text_layer) + ")" if no_text_layer else ""),
        "",
        "stop conditions:",
        f"  download failures above 20%: {'YES' if failure_share > 0.2 else 'no'}",
        f"  texts below {SHORT_WORK_WORDS} words above 20%: "
        f"{'YES' if short_share > 0.2 else 'no'}",
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

    print(f"entries={len(entries)} works={len(works)} excluded={len(excluded)}")
    print(f"words={total_words} chars={total_chars} short_works={len(short_works)}")
    print(f"download_failures={download_failures}/{attempts} seconds={download_seconds:.1f}")
    print(f"report={BUILD_REPORT}")
    if limit == 0 and (failure_share > 0.2 or short_share > 0.2):
        print("STOP CONDITION TRIGGERED")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
