#!/usr/bin/env python3
"""Цитатный индекс корпуса: corpus/index.json.

Запуск из корня репозитория (после tools/fetch_corpus.py):

    python tools/build_index.py

Берёт абзацы из corpus/text, отсеивает короткие и слишком длинные (цитата —
25–120 слов), выбирает примерно одну цитату на 500 слов произведения и
раскладывает их равномерно по тексту. Вид цитаты:

    dialogue — абзац целиком реплика (начинается с тире);
    scene    — повествование с прямой речью внутри;
    paragraph — остальное повествование.

Индекс детерминирован: при тех же текстах получается тот же набор цитат.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "manifest.json"
INDEX = ROOT / "corpus" / "index.json"
WORDS_PER_EXCERPT = 500
MIN_WORDS = 25
MAX_WORDS = 120

SPEECH_START = re.compile(r"^—\s")
SPEECH_INSIDE = re.compile(r"[.!?…»]\s+—\s")


def classify(paragraph: str) -> str:
    """Вид абзаца по пунктуации прямой речи."""
    if SPEECH_START.match(paragraph):
        return "dialogue"
    if SPEECH_INSIDE.search(paragraph):
        return "scene"
    return "paragraph"


def paragraph_words(paragraph: str) -> int:
    return len(paragraph.split())


def sentence_chunks(paragraph: str) -> list[str]:
    """Делит длинный абзац на проходы по целым предложениям, 25–120 слов."""
    sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
    chunks: list[str] = []
    buffer: list[str] = []
    for sentence in sentences:
        candidate = " ".join(buffer + [sentence])
        if buffer and paragraph_words(candidate) > MAX_WORDS:
            chunks.append(" ".join(buffer))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        if chunks and paragraph_words(" ".join(buffer)) < MIN_WORDS:
            chunks[-1] = chunks[-1] + " " + " ".join(buffer)
        else:
            chunks.append(" ".join(buffer))
    return [c for c in chunks if MIN_WORDS <= paragraph_words(c) <= MAX_WORDS]


def candidates(paragraphs: list[str]) -> list[str]:
    """Абзацы подходящей длины; длинные абзацы режутся на проходы по фразам."""
    result: list[str] = []
    for paragraph in paragraphs:
        words = paragraph_words(paragraph)
        if MIN_WORDS <= words <= MAX_WORDS:
            result.append(paragraph)
        elif words > MAX_WORDS:
            result.extend(sentence_chunks(paragraph))
    return result


def pick_evenly(paragraphs: list[str], count: int) -> list[str]:
    """Выбирает count абзацев равномерно по списку подходящих."""
    if count >= len(paragraphs):
        return list(paragraphs)
    if count == 1:
        return [paragraphs[len(paragraphs) // 2]]
    step = (len(paragraphs) - 1) / (count - 1)
    return [paragraphs[round(i * step)] for i in range(count)]


def build_work_excerpts(work: dict) -> list[dict]:
    path = ROOT / work["file"]
    text = path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    paragraphs = [b for b in blocks if not b.startswith("## ")]
    eligible = candidates(paragraphs)
    count = max(1, round(work["words"] / WORDS_PER_EXCERPT))
    excerpts = []
    for ordinal, paragraph in enumerate(pick_evenly(eligible, count), start=1):
        excerpts.append({
            "id": f"{work['id']}#{ordinal}",
            "author": work["author"],
            "work": work["title"],
            "title": work["title"],
            "genre": work["genre"],
            "year": work["year"],
            "kind": classify(paragraph),
            "text": paragraph,
            "words": paragraph_words(paragraph),
        })
    return excerpts


def main() -> int:
    if not MANIFEST.exists():
        print("нет corpus/manifest.json: сначала python tools/fetch_corpus.py",
              file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    excerpts: list[dict] = []
    for work in manifest["works"]:
        work_excerpts = build_work_excerpts(work)
        if not work_excerpts:
            print(f"{work['id']}: нет абзацев 25–120 слов", file=sys.stderr)
            return 1
        excerpts.extend(work_excerpts)
    index = {
        "version": 1,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "excerpts": excerpts,
    }
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    kinds: dict[str, int] = {}
    for excerpt in excerpts:
        kinds[excerpt["kind"]] = kinds.get(excerpt["kind"], 0) + 1
    print(f"цитат: {len(excerpts)}; виды: {kinds}")
    print(f"записан {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
