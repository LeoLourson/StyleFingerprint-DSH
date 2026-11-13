#!/usr/bin/env python
"""Build the quotation index of the math corpus.

Run from the repository root, after tools/fetch_math_corpus.py:

    .venv\\Scripts\\python.exe tools/build_math_index.py

Reads corpus/math/manifest.json and the texts it lists, keeps paragraphs of 25-120
words that contain no formulas (no digits, no = ^ _ \\ $, no mathematical Unicode),
takes one quotation per 500 words of a paper and writes corpus/math/index.json.
Repeated runs are byte-identical: the index takes the "built" stamp of the manifest.
"""

from __future__ import annotations

import json
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
WORDS_PER_QUOTE = 500
EXCERPT_ENDS = (".", "!", "?", "\u2026")
FORMULA_CHARS = set("=^_\\$")
FORMULA_SIGNS = set("×÷±∞≈≠≡≤≥∫∑∏√∂∇∈∉⊂⊃∪∩∅∥⊥∣‖⩽⩾≪≫→←↔⇒⇔")
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
LATIN_SHARE_MAX = 0.05


def has_formula(paragraph: str) -> bool:
    """A paragraph with a formula: digits, formula signs or Unicode mathematics."""
    if any(char.isdigit() for char in paragraph):
        return True
    if any(char in FORMULA_CHARS or char in FORMULA_SIGNS for char in paragraph):
        return True
    for char in paragraph:
        code = ord(char)
        if any(low <= code <= high for low, high in MATH_RANGES):
            return True
    return False


def latin_share(paragraph: str) -> float:
    cyr = len(CYRILLIC_RE.findall(paragraph))
    lat = len(LATIN_RE.findall(paragraph))
    total = cyr + lat
    return lat / total if total else 1.0


def usable(paragraph: str) -> bool:
    """Whether a paragraph can be quoted: prose of the citation size, no formulas."""
    words = count_words(paragraph)
    if not MIN_WORDS <= words <= MAX_WORDS:
        return False
    if not paragraph.endswith(EXCERPT_ENDS):
        return False
    if len(CYRILLIC_RE.findall(paragraph)) < 100:
        return False
    if latin_share(paragraph) > LATIN_SHARE_MAX:
        return False
    return not has_formula(paragraph)


def pick(paragraphs: list[str], quota: int) -> list[str]:
    """Evenly spaced paragraphs over the document, so the quotes cover the paper."""
    if len(paragraphs) <= quota:
        return list(paragraphs)
    if quota == 1:
        return [paragraphs[len(paragraphs) // 2]]
    step = (len(paragraphs) - 1) / (quota - 1)
    picks: list[str] = []
    for index in range(quota):
        paragraph = paragraphs[round(index * step)]
        if paragraph not in picks:
            picks.append(paragraph)
    return picks


def build_index() -> tuple[dict, list[str], list[str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    excerpts: list[dict] = []
    per_work: list[str] = []
    empty: list[str] = []
    for work in manifest["works"]:
        path = ROOT / work["file"]
        text = path.read_text(encoding="utf-8")
        paragraphs = [line.strip() for line in text.strip("\n").split("\n\n") if line.strip()]
        candidates = [paragraph for paragraph in paragraphs if usable(paragraph)]
        quota = max(1, work["words"] // WORDS_PER_QUOTE)
        chosen = pick(candidates, quota)
        if not chosen:
            empty.append(work["mathnet"])
        for number, paragraph in enumerate(chosen, start=1):
            excerpts.append({
                "id": f"{work['id']}#{number}",
                "author": "shkalikov",
                "work": work["title"],
                "title": work["title"],
                "genre": "article",
                "year": work["year"],
                "kind": "paragraph",
                "text": paragraph,
                "words": count_words(paragraph),
            })
        per_work.append(
            f"{work['mathnet']:<9} words={work['words']:>6} candidates={len(candidates):>4} "
            f"quota={quota:>3} taken={len(chosen):>3}"
        )
    document = {"version": 1, "built": manifest["built"], "excerpts": excerpts}
    payload = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(payload, encoding="utf-8", newline="\n")
    return document, per_work, empty


def main() -> int:
    if not MANIFEST.exists():
        raise SystemExit("manifest is missing: run tools/fetch_math_corpus.py first")
    document, per_work, empty = build_index()
    excerpts = document["excerpts"]
    words = sum(item["words"] for item in excerpts)
    report = [
        f"built: {document['built']}",
        f"works: {len(per_work)}",
        f"excerpts: {len(excerpts)}",
        f"excerpt words: {words}",
        f"works without a single quote: {len(empty)}",
        "",
        "per work:",
    ]
    report += per_work
    if empty:
        report.append("")
        report.append("no quotes taken:")
        report += empty
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    print(f"excerpts={len(excerpts)} words={words} works={len(per_work)}")
    print(f"report={REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
