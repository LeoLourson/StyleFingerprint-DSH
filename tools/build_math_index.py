#!/usr/bin/env python
"""Build the quotation index of the math corpus.

Run from the repository root, after tools/fetch_math_corpus.py:

    .venv\\Scripts\\python.exe tools/build_math_index.py

Reads corpus/math/manifest.json and the texts it lists, cuts every paragraph into
passages that start and end on a sentence boundary, keeps the passages of 25-120
words without formulas (no = ^ _ \\ $, no mathematical Unicode, no LaTeX; digits,
reference numbers and theorem numbers are ordinary prose and stay), takes about
one quotation per 500 words of a paper and writes corpus/math/index.json.
Repeated runs are byte-identical: the index takes the "built" stamp of the manifest.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fetch_math_corpus import CYRILLIC_RE, LATIN_RE, count_words  # noqa: E402

MANIFEST = ROOT / "corpus" / "math" / "manifest.json"
INDEX = ROOT / "corpus" / "math" / "index.json"
REPORT = ROOT / ".tmp" / "index_report.txt"

MIN_WORDS = 25
MAX_WORDS = 120
PASSAGE_WORDS = 45
WORDS_PER_QUOTE = 500
EXCERPT_ENDS = (".", "!", "?", "\u2026")
SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?\u2026])\s+(?=[\u0410-\u042f\u0401A-Z\u00ab\u201c(\"])")
FORMULA_CHARS = set("=^_\\$")
FORMULA_SIGNS = set("∑∏∫√≤≥≈∞∂∇±×·∈⊂∪∩∅≈≠≡≪≫→←↔⇒⇔⟨⟩‖∥⊥∣⩽⩾")
MATH_RANGES = (
    (0x0370, 0x03FF),  # Greek letters of a formula
    (0x2070, 0x209F),  # superscripts and subscripts
    (0x2100, 0x214F),  # letterlike symbols
    (0x2190, 0x21FF),  # arrows
    (0x2200, 0x22FF),  # mathematical operators
    (0x2300, 0x23FF),  # technical symbols
    (0x27C0, 0x27EF),  # miscellaneous mathematical symbols A
    (0x27F0, 0x27FF),  # supplemental arrows A
    (0x2900, 0x297F),  # supplemental arrows B
    (0x2980, 0x29FF),  # miscellaneous mathematical symbols B
    (0x2A00, 0x2AFF),  # supplemental mathematical operators
    (0x1D400, 0x1D7FF),  # mathematical alphanumeric symbols
)
LATIN_SHARE_MAX = 0.50
# A quoted title followed by a year: a citation or a journal footer, not prose.
CITATION_RE = re.compile(r"\u00ab[^\u00bb]{3,80}\u00bb,?\s*(?:19|20)\d{2}")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
PAGE_RANGE_RE = re.compile(r"\b\d{1,4}\s*[\u2013\u2014-]\s*\d{1,4}\b")
VOLUME_MARKERS = (
    re.compile(r"\b(?:No|\u2116)\s*\d"),
    re.compile(r"\b[\u0442\u0422]\.\s*\d"),
    re.compile(r"\b[\u0441\u0421]\.\s*\d"),
    re.compile(r"\b(?:Vol|No|pp)\."),
)
NUMBERED_ENTRY_RE = re.compile(r"^\d{1,3}\.\s*[\u0410-\u042fA-Z]")
# A footnote or a masthead glued into the text: "1Работа выполнена", "2Механико".
GLUED_FOOTNOTE_RE = re.compile(r"\d[\u0410-\u042f]")


def has_formula(passage: str) -> bool:
    """A passage with a formula: formula signs, LaTeX or mathematical Unicode.

    Digits are not a formula marker: "в работе [24] доказано" is ordinary prose.
    """
    if any(char in FORMULA_CHARS for char in passage):
        return True
    if any(char in FORMULA_SIGNS for char in passage):
        return True
    for char in passage:
        code = ord(char)
        if any(low <= code <= high for low, high in MATH_RANGES):
            return True
    return False


def latin_share(passage: str) -> float:
    cyr = len(CYRILLIC_RE.findall(passage))
    lat = len(LATIN_RE.findall(passage))
    total = cyr + lat
    return lat / total if total else 1.0


def is_bibliography(passage: str) -> bool:
    """A reference list entry, not the prose of the article.

    Prose cites "[24]" and "в 1975 г.", and that stays. A reference list has the
    shape of a list: "//" with a year, several years with several page ranges, a
    numbered entry with years, or three volume/page marks in one passage.
    """
    if CITATION_RE.search(passage):
        return True
    years = len(YEAR_RE.findall(passage))
    ranges = len(PAGE_RANGE_RE.findall(passage))
    markers = sum(1 for pattern in VOLUME_MARKERS if pattern.search(passage))
    if "//" in passage and years:
        return True
    if years >= 3 and ranges >= 2:
        return True
    if NUMBERED_ENTRY_RE.match(passage) and years >= 2:
        return True
    return markers >= 3


def usable(passage: str) -> bool:
    """Whether a passage can be quoted: the citation size, prose, no formulas."""
    words = count_words(passage)
    if not MIN_WORDS <= words <= MAX_WORDS:
        return False
    if not passage.endswith(EXCERPT_ENDS):
        return False
    if len(CYRILLIC_RE.findall(passage)) < 100:
        return False
    if latin_share(passage) > LATIN_SHARE_MAX:
        return False
    if is_bibliography(passage):
        return False
    if GLUED_FOOTNOTE_RE.search(passage):
        return False
    return not has_formula(passage)


def passages(paragraph: str) -> list[str]:
    """Whole sentences of a paragraph packed into passages of 25-120 words."""
    sentences = [part.strip() for part in SENTENCE_BREAK_RE.split(paragraph) if part.strip()]
    packed: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        words = count_words(sentence)
        if words > MAX_WORDS:
            if current:
                packed.append(" ".join(current))
            current, size = [], 0
            continue
        if current and size + words > MAX_WORDS:
            packed.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += words
        if size >= PASSAGE_WORDS:
            packed.append(" ".join(current))
            current, size = [], 0
    if current:
        packed.append(" ".join(current))
    return packed


def pick(candidates: list[str], quota: int) -> list[str]:
    """Evenly spaced passages over the paper, so the quotes cover the text."""
    if len(candidates) <= quota:
        return list(candidates)
    if quota == 1:
        return [candidates[len(candidates) // 2]]
    step = (len(candidates) - 1) / (quota - 1)
    picks: list[str] = []
    for index in range(quota):
        passage = candidates[round(index * step)]
        if passage not in picks:
            picks.append(passage)
    return picks


def spread_quotas(candidates: dict[str, list[str]], words: dict[str, int]) -> dict[str, int]:
    """Quotas of one quotation per 500 words, surplus handed to prose-rich papers.

    A paper of formulas may offer fewer clean passages than its share, while a
    paper of prose offers more; the leftover is spread over the papers that have
    candidates left, so the index keeps the global rate instead of losing it.
    """
    target = max(1, round(sum(words.values()) / WORDS_PER_QUOTE))
    wanted = {mathnet: max(1, round(words[mathnet] / WORDS_PER_QUOTE)) for mathnet in candidates}
    taken = {
        mathnet: min(len(candidates[mathnet]), quota)
        for mathnet, quota in wanted.items()
    }
    total = sum(taken.values())
    order = sorted(
        candidates,
        key=lambda mathnet: (-(len(candidates[mathnet]) - taken[mathnet]), mathnet),
    )
    changed = True
    while total < target and changed:
        changed = False
        for mathnet in order:
            if total >= target:
                break
            if taken[mathnet] < len(candidates[mathnet]):
                taken[mathnet] += 1
                total += 1
                changed = True
    return taken


def build_index() -> tuple[dict, list[str], list[str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    words: dict[str, int] = {}
    works: dict[str, dict] = {}
    for work in manifest["works"]:
        text = (ROOT / work["file"]).read_text(encoding="utf-8")
        paragraphs = [line.strip() for line in text.strip("\n").split("\n\n") if line.strip()]
        candidates = [
            passage
            for paragraph in paragraphs
            for passage in passages(paragraph)
            if usable(passage)
        ]
        found[work["mathnet"]] = candidates
        words[work["mathnet"]] = work["words"]
        works[work["mathnet"]] = work
    quotas = spread_quotas(found, words)

    excerpts: list[dict] = []
    per_work: list[str] = []
    empty: list[str] = []
    for mathnet, candidates in found.items():
        work = works[mathnet]
        quota = quotas[mathnet]
        chosen = pick(candidates, quota)
        if not chosen:
            empty.append(mathnet)
        for number, passage in enumerate(chosen, start=1):
            excerpts.append({
                "id": f"{work['id']}#{number}",
                "author": "shkalikov",
                "work": work["title"],
                "title": work["title"],
                "genre": "article",
                "year": work["year"],
                "kind": "paragraph",
                "text": passage,
                "words": count_words(passage),
            })
        per_work.append(
            f"{mathnet:<9} words={work['words']:>6} candidates={len(candidates):>4} "
            f"quota={quota:>3} taken={len(chosen):>3}"
        )
    document = {"version": 1, "built": manifest["built"], "excerpts": excerpts}
    payload = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(payload, encoding="utf-8", newline="\n")
    return document, per_work, empty


def check_index(document: dict) -> list[str]:
    """Check the written index against the rules and against its own texts."""
    problems: list[str] = []
    seen: set[str] = set()
    texts: dict[str, str] = {}
    for excerpt in document["excerpts"]:
        ident = excerpt["id"]
        if ident in seen:
            problems.append(f"{ident}: duplicate")
        seen.add(ident)
        if not MIN_WORDS <= excerpt["words"] <= MAX_WORDS:
            problems.append(f"{ident}: words {excerpt['words']} outside {MIN_WORDS}-{MAX_WORDS}")
        if count_words(excerpt["text"]) != excerpt["words"]:
            problems.append(f"{ident}: words {excerpt['words']} is not the count of the text")
        if has_formula(excerpt["text"]):
            problems.append(f"{ident}: formula in the quote")
        if not excerpt["text"].endswith(EXCERPT_ENDS):
            problems.append(f"{ident}: the quote does not end a sentence")
        mathnet = ident.split("/")[1].split("#")[0]
        if mathnet not in texts:
            texts[mathnet] = (ROOT / f"corpus/math/shkalikov/{mathnet}.txt").read_text(encoding="utf-8")
        if excerpt["text"] not in texts[mathnet]:
            problems.append(f"{ident}: the quote is not a piece of its work")
    return problems


def main() -> int:
    if not MANIFEST.exists():
        raise SystemExit("manifest is missing: run tools/fetch_math_corpus.py first")
    document, per_work, empty = build_index()
    excerpts = document["excerpts"]
    words = sum(item["words"] for item in excerpts)
    sizes = sorted(item["words"] for item in excerpts)
    problems = check_index(document)
    report = [
        f"built: {document['built']}",
        f"works: {len(per_work)}",
        f"excerpts: {len(excerpts)}",
        f"excerpt words: {words}",
        f"words per excerpt: min={sizes[0] if sizes else 0} "
        f"median={sizes[len(sizes) // 2] if sizes else 0} max={sizes[-1] if sizes else 0}",
        f"works without a single quote: {len(empty)}",
        f"index problems: {len(problems)}",
        "",
        "per work:",
    ]
    report += per_work
    if empty:
        report.append("")
        report.append("no quotes taken:")
        report += empty
    report.append("")
    report += problems
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    print(f"excerpts={len(excerpts)} words={words} works={len(per_work)} empty={len(empty)}")
    print(f"index_problems={len(problems)} report={REPORT}")
    for problem in problems[:10]:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
