#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metric engine of dsh-style-fingerprint.

One process, one implementation of the style metrics: pymorphy3 for morphology,
natasha (razdel + slovnet) for syntax. Modes:

    python tools/analyze.py --serve   JSON lines on stdin, one JSON line per answer
    python tools/analyze.py --stdin   one request on stdin, one answer on stdout

Actions: ping, catalogue, measure. The answer format is section 6 of CONTRACT.md.
A parser failure never kills the process: the metrics that need the failed parser
are reported in "unavailable" with a reason, the rest are computed.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import warnings
from pathlib import Path

# pymorphy3 ищет словарь через pymorphy2, а тот пользуется удалённым pkg_resources.
# Предупреждение не относится к делу и засоряет stderr моста; фильтр узкий, по тексту.
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

VERSION = "0.1"
ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = ROOT / "data" / "metrics.json"

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[-'’][A-Za-zА-Яа-яЁё0-9]+)*")
LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")
SENT_FALLBACK = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"“—])")
QUOTE_RE = re.compile(r"«([^»]{1,1000})»|\"([^\"]{1,1000})\"|“([^”]{1,1000})”")
SLASH_SENTENCE = re.compile(r"[.!?…]+|[;:—–]")


class Unavailable(Exception):
    """A metric cannot be computed on this text; the message is the reason."""


# ---------------------------------------------------------------------------
# Lexicons. Words are matched on lowercased text with word boundaries.
# ---------------------------------------------------------------------------

CONNECTIVES = {
    "cause": ["потому что", "так как", "поскольку", "ведь", "оттого что",
              "из-за того что", "потому"],
    "consequence": ["поэтому", "так что", "отсюда", "значит", "следовательно",
                    "тем самым", "в результате", "вследствие этого"],
    "contrast": ["но", "однако", "зато", "тогда как", "в то время как",
                 "напротив", "наоборот", "а"],
    "concession": ["хотя", "несмотря на то что", "пусть", "правда", "хотя и"],
    "condition": ["если", "когда", "при условии что", "в случае если", "раз"],
    "spec": ["то есть", "а именно", "причём", "иначе говоря", "точнее",
             "в частности"],
    "addition": ["и", "к тому же", "вдобавок", "также", "кроме того",
                 "более того", "помимо этого"],
    "time": ["пока", "после того как", "до того как", "с тех пор как",
             "прежде чем"],
    "purpose": ["чтобы", "для того чтобы", "с тем чтобы", "дабы"],
}

EMPTY_CONNECTIVES = ["таким образом", "при этом", "вместе с тем", "в данной связи",
                     "в этой связи", "соответственно"]

METATEXT = ["в этом разделе", "в этой статье", "в настоящей работе", "в данной работе",
            "ниже будет показано", "как уже отмечалось", "как было сказано выше",
            "как показано выше", "перейдём к", "перейдем к", "давайте разберёмся",
            "давайте разберемся", "давайте рассмотрим", "подведём итоги",
            "подведем итоги", "резюмируя", "возвращаясь к", "забегая вперёд",
            "стоит отметить", "важно отметить"]

INTENSIFIERS = ["очень", "крайне", "весьма", "абсолютно", "совершенно", "чрезвычайно",
                "максимально", "кардинально", "поистине", "буквально", "страшно",
                "ужасно", "невероятно", "исключительно", "необычайно"]

EVALUATIVE = ["важный", "ключевой", "значительный", "значимый", "существенный",
              "эффективный", "уникальный", "идеальный", "впечатляющий", "мощный",
              "яркий", "глубокий", "изящный", "превосходный", "выдающийся",
              "критический", "фундаментальный", "принципиальный", "содержательный",
              "недвусмысленный", "безупречный", "оптимальный"]

HEDGES = ["возможно", "вероятно", "по-видимому", "кажется", "наверное", "пожалуй",
          "отчасти", "видимо", "якобы", "вроде бы", "некоторый", "некий",
          "определённый", "в известной степени", "скорее", "по-моему",
          "насколько можно судить", "как представляется"]

MODAL = ["должен", "должна", "должно", "должны", "нужно", "надо", "следует",
         "необходимо", "можно", "нельзя", "мочь", "может", "могут", "требуется",
         "требуются", "желательно", "надлежит", "предстоит", "разрешается"]

CLICHES = ["в рамках", "в целях", "в связи с", "в соответствии с", "в отношении",
           "применительно к", "в контексте", "на основе", "на базе", "исходя из",
           "является", "являются", "представляет собой", "осуществляется",
           "производится", "реализуется", "обеспечивает", "способствует",
           "направлен на", "нацелен на", "призван", "имеет место",
           "в настоящее время", "на сегодняшний день", "на данный момент",
           "в данной связи", "вышеупомянутый", "указанный", "представляет интерес",
           "играет важную роль", "играет ключевую роль", "занимает центральное место",
           "даёт возможность", "позволяет сделать вывод"]

COLLOQUIAL = ["вообще", "просто", "прямо", "ну", "вот", "типа", "как бы", "мол",
              "короче", "кстати", "между прочим", "честно говоря", "грубо говоря",
              "по сути", "то есть нет"]

BOOKISH = ["данный", "указанный", "вышеупомянутый", "вышеуказанный", "настоящий",
           "соответствующий", "осуществлять", "производить", "являться",
           "представлять собой", "в силу", "ввиду", "согласно", "посредством",
           "в качестве", "в течение", "вследствие"]

SPEECH_VERBS = ["сказать", "говорить", "спросить", "спрашивать", "ответить",
                "отвечать", "произнести", "проговорить", "промолвить", "крикнуть",
                "воскликнуть", "заметить", "добавить", "возразить", "пробормотать",
                "прошептать", "повторить", "шептать", "бормотать", "закричать",
                "перебить", "согласиться", "удивиться", "писать", "написать"]

TIME_MARKERS = ["потом", "затем", "вдруг", "наконец", "сначала", "тотчас",
                "сейчас же", "через минуту", "утром", "вечером", "ночью", "днём",
                "на другой день", "в это время", "между тем", "тем временем",
                "вскоре", "опять", "снова", "уже", "ещё"]

AI_MARKERS = ["ключевой", "фундаментальный", "принципиальный", "содержательный",
              "недвусмысленный", "играет роль", "играет важную роль",
              "играет ключевую роль", "занимает центральное место", "обусловлен",
              "обеспечивает", "способствует", "содействует", "препятствует",
              "гарантирует", "направлен на", "нацелен на", "призван",
              "представляет интерес", "обладает свойством", "характеризуется",
              "позволяет", "даёт возможность", "важно отметить", "стоит отметить",
              "следует отметить", "в современном мире", "в условиях"]

AI_METATEXT = ["в этом ответе", "в этой статье", "в этом разделе", "в данном разделе",
               "ниже будет показано", "как уже отмечалось", "давайте разберёмся",
               "давайте разберемся", "перейдём к", "перейдем к", "подведём итоги",
               "подведем итоги", "забегая вперёд", "в заключение хочется"]

AI_RITUAL = ["отличный вопрос", "хороший вопрос", "конечно", "разумеется",
             "надеюсь, что это поможет", "надеюсь, это поможет",
             "если у вас есть вопросы", "обращайтесь", "удачи",
             "спасибо за внимание", "буду рад помочь", "с удовольствием помогу",
             "дайте знать"]

AI_ADDRESS = ["давайте", "друзья", "коллеги", "уважаемые", "дорогие", "представьте",
              "обратите внимание", "задумайтесь", "согласитесь", "поверьте"]

ABSTRACT_SUFFIXES = ("ость", "есть", "ство", "изм", "ация", "ция", "ение", "ание", "тие")
NOMINALIZATION_SUFFIXES = ("ание", "ение", "тие", "ация", "ование", "кация")

CONTENT_POS = {"NOUN", "VERB", "INFN", "ADJF", "ADJS", "COMP", "PRTF", "PRTS",
               "GRND", "NUMR", "ADVB"}

STOPWORDS = set("""и в во не на я что он с со как а то все она так его но да ты к у же
вы за бы по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего
ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз
тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один
почти мой тем чтобы нее были куда зачем всех никогда можно при наконец два об другой
хоть после над больше тот через эти нас про всего них какая много разве три эту моя
впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между""".split())

CLAUSE_RELS = {"acl", "advcl", "ccomp", "csubj", "xcomp"}


# ---------------------------------------------------------------------------
# Small statistics helpers
# ---------------------------------------------------------------------------

def mean(xs):
    return sum(xs) / len(xs)


def quantile(xs, q):
    if not xs:
        raise Unavailable("нет значений")
    ordered = sorted(xs)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def pearson(xs, ys):
    if len(xs) < 3:
        raise Unavailable("меньше трёх пар значений")
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        raise Unavailable("нулевой разброс длин")
    return num / (dx * dy)


def per1000(count, words):
    if not words:
        raise Unavailable("в тексте нет слов")
    return count * 1000.0 / words


def share(count, total):
    if not total:
        raise Unavailable("нулевой знаменатель")
    return count / total


def round6(value):
    if isinstance(value, bool):
        return value
    value = float(value)
    if not math.isfinite(value):
        raise Unavailable("не конечное значение")
    value = round(value, 6)
    return 0.0 if value == 0 else value


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

def tokenize(text):
    return WORD_RE.findall(text)


def split_sentences(text):
    try:
        from razdel import sentenize
    except Exception:
        parts = SENT_FALLBACK.split(text.replace("\n", " "))
        return [p.strip() for p in parts if p.strip()]
    return [s.text.strip() for s in sentenize(text) if s.text.strip()]


def split_paragraphs(text):
    paragraphs, buffer = [], []
    for line in text.splitlines():
        line = line.strip()
        if line:
            buffer.append(line)
        elif buffer:
            paragraphs.append(" ".join(buffer))
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer))
    return paragraphs


def phrase_occurrences(lower_text, phrases):
    total = 0
    for phrase in phrases:
        body = r"\s+".join(re.escape(part) for part in phrase.split())
        pattern = r"(?<![А-Яа-яЁёA-Za-z0-9])" + body + r"(?![А-Яа-яЁёA-Za-z0-9])"
        total += len(re.findall(pattern, lower_text))
    return total


def letters_len(word):
    return len(LETTER_RE.findall(word))


class MorphInfo:
    __slots__ = ("pos", "lemma", "grammemes")

    def __init__(self, pos, lemma, grammemes):
        self.pos = pos
        self.lemma = lemma
        self.grammemes = grammemes


class NatSent:
    __slots__ = ("tokens", "id2idx")

    def __init__(self, tokens):
        self.tokens = tokens
        self.id2idx = {t.id: i for i, t in enumerate(tokens)}

    def base_rel(self, token):
        return token.rel.split(":")[0]

    def subtype(self, token):
        parts = token.rel.split(":")
        return parts[1] if len(parts) > 1 else ""

    def depth(self, index):
        steps, current, seen = 0, self.tokens[index], set()
        while current.head_id in self.id2idx and steps < 100:
            if current.head_id in seen:
                break
            seen.add(current.head_id)
            current = self.tokens[self.id2idx[current.head_id]]
            steps += 1
        return steps

    def root_index(self):
        for i, token in enumerate(self.tokens):
            if self.base_rel(token) == "root":
                return i
        return 0

    def word_positions(self):
        return [i for i, t in enumerate(self.tokens) if t.pos != "PUNCT"]

    def word_count(self):
        return sum(1 for t in self.tokens if t.pos != "PUNCT")


class Analysis:
    """Everything one measure call needs: tokens, sentences, morphology, syntax."""

    def __init__(self, text, engine):
        self.text = text
        self.engine = engine
        self.words = tokenize(text)
        self.lower_words = [w.lower() for w in self.words]
        self.lower_text = text.lower()
        self.sentences = split_sentences(text)
        self.paragraphs = split_paragraphs(text)
        self.sent_word_counts = [len(tokenize(s)) for s in self.sentences]
        self.para_word_counts = [len(tokenize(p)) for p in self.paragraphs]
        self.morphs = None
        self.morph_reason = engine.morph_reason
        if not self.morph_reason:
            try:
                self.morphs = [engine.parse(word) for word in self.words]
            except Exception as exc:  # pragma: no cover - defensive
                self.morphs = None
                self.morph_reason = "pymorphy3: %s: %s" % (type(exc).__name__, exc)
        self.nat_sents = None
        self.syntax_reason = engine.syntax_reason
        if not self.syntax_reason:
            try:
                self.nat_sents = engine.parse_syntax(text)
            except Exception as exc:
                self.nat_sents = None
                self.syntax_reason = "natasha: %s: %s" % (type(exc).__name__, exc)

    # -- guards ------------------------------------------------------------
    def need_words(self):
        if not self.words:
            raise Unavailable("в тексте нет слов")
        return self.words

    def need_morph(self):
        if self.morphs is None:
            raise Unavailable(self.morph_reason or "разбор морфологии недоступен")
        return self.morphs

    def need_syntax(self):
        if self.nat_sents is None:
            raise Unavailable(self.syntax_reason or "разбор синтаксиса недоступен")
        return self.nat_sents

    def need_sentences(self):
        if not self.sentences:
            raise Unavailable("в тексте нет предложений")
        return self.sentences

    def need_paragraphs(self):
        if not self.paragraphs:
            raise Unavailable("в тексте нет абзацев")
        return self.paragraphs

    # -- derived collections -----------------------------------------------
    def verbs(self):
        morphs = self.need_morph()
        return [m for m in morphs if m.pos in ("VERB", "INFN")]

    def finite_verbs(self):
        morphs = self.need_morph()
        return [m for m in morphs if m.pos == "VERB"]

    def nouns(self):
        return [m for m in self.need_morph() if m.pos == "NOUN"]

    def adjectives(self):
        return [m for m in self.need_morph() if m.pos in ("ADJF", "ADJS", "COMP")]

    def content_words(self):
        morphs = self.need_morph()
        return [m for m in morphs if m.pos in CONTENT_POS and m.lemma not in STOPWORDS]

    def quote_pairs(self):
        return min(self.text.count("«"), self.text.count("»")) + self.text.count("“") \
            + self.text.count('"') // 2

    def paren_pairs(self):
        return min(self.text.count("("), self.text.count(")"))


# ---------------------------------------------------------------------------
# Metric implementations
# ---------------------------------------------------------------------------

def m_sent_len_mean(a):
    return mean(a.sent_word_counts)


def m_sent_len_median(a):
    return float(statistics.median(a.sent_word_counts))


def m_sent_len_p90(a):
    return quantile(a.sent_word_counts, 0.9)


def m_sent_len_cv(a):
    if len(a.sent_word_counts) < 2:
        raise Unavailable("меньше двух предложений")
    average = mean(a.sent_word_counts)
    if average == 0:
        raise Unavailable("нулевая средняя длина")
    return statistics.pstdev(a.sent_word_counts) / average


def m_sent_short_share(a):
    lengths = a.sent_word_counts
    return share(sum(1 for n in lengths if n <= 8), len(lengths))


def m_sent_long_share(a):
    lengths = a.sent_word_counts
    return share(sum(1 for n in lengths if n >= 30), len(lengths))


def m_sent_autocorr(a):
    lengths = a.sent_word_counts
    return pearson(lengths[:-1], lengths[1:])


def clause_counts(a):
    per_sentence = []
    for sent in a.need_syntax():
        dependent = sum(1 for t in sent.tokens if sent.base_rel(t) in CLAUSE_RELS)
        per_sentence.append(1 + dependent)
    return per_sentence


def m_clause_len_mean(a):
    clause_counts_list = clause_counts(a)
    words = sum(sent.word_count() for sent in a.need_syntax())
    return share(words, sum(clause_counts_list))


def m_clause_per_sent(a):
    counts = clause_counts(a)
    return share(sum(counts), len(counts))


def m_inversion_share(a):
    flagged = 0
    total = 0
    for sent in a.need_syntax():
        root = sent.root_index()
        subjects = [i for i, t in enumerate(sent.tokens)
                    if sent.base_rel(t) in ("nsubj", "csubj")]
        if not subjects:
            continue
        total += 1
        if min(subjects) > root:
            flagged += 1
    return share(flagged, total)


def m_subject_first_share(a):
    flagged = 0
    total = 0
    for sent in a.need_syntax():
        subjects = [i for i, t in enumerate(sent.tokens)
                    if sent.base_rel(t) in ("nsubj", "csubj")]
        if not subjects:
            continue
        total += 1
        if min(subjects) <= 1:
            flagged += 1
    return share(flagged, total)


def m_subject_absent_share(a):
    sents = a.need_syntax()
    absent = sum(1 for sent in sents
                 if not any(sent.base_rel(t) in ("nsubj", "csubj") for t in sent.tokens))
    return share(absent, len(sents))


def m_simple_share(a):
    sents = a.need_syntax()
    simple = sum(1 for sent in sents
                 if not any(sent.base_rel(t) in CLAUSE_RELS for t in sent.tokens))
    return share(simple, len(sents))


def m_nominal_predicate_share(a):
    sents = a.need_syntax()
    nominal = 0
    for sent in sents:
        root = sent.tokens[sent.root_index()]
        if sent.base_rel(root) == "cop" or root.pos in ("NOUN", "PRON", "ADJ", "NUM"):
            nominal += 1
        elif any(sent.base_rel(t) == "cop" for t in sent.tokens):
            nominal += 1
    return share(nominal, len(sents))


def m_verb_final_share(a):
    flagged = 0
    total = 0
    for sent in a.need_syntax():
        words = sent.word_positions()
        if not words:
            continue
        root = sent.root_index()
        root_token = sent.tokens[root]
        if root_token.pos != "VERB":
            continue
        total += 1
        if words and root >= words[int(len(words) * 0.7)]:
            flagged += 1
    return share(flagged, total)


def m_noun_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos == "NOUN"), len(a.words))


def m_verb_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos in ("VERB", "INFN")), len(a.words))


def m_adj_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos in ("ADJF", "ADJS", "COMP")),
                 len(a.words))


def m_adv_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos in ("ADVB", "PRED")), len(a.words))


def m_pron_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos == "NPRO"), len(a.words))


def m_num_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos == "NUMR"), len(a.words))


def m_func_share(a):
    return share(sum(1 for m in a.need_morph()
                     if m.pos in ("PREP", "CONJ", "PRCL", "INTJ")), len(a.words))


def m_aspect_pf_share(a):
    verbs = [m for m in a.need_morph()
             if m.pos in ("VERB", "INFN") and ({"perf", "impf"} & m.grammemes)]
    return share(sum(1 for m in verbs if "perf" in m.grammemes), len(verbs))


def m_mood_impr_share(a):
    verbs = a.finite_verbs()
    return share(sum(1 for m in verbs if "impr" in m.grammemes), len(verbs))


def m_mood_indc_share(a):
    verbs = a.finite_verbs()
    return share(sum(1 for m in verbs if "indc" in m.grammemes), len(verbs))


def m_tense_past_share(a):
    verbs = a.finite_verbs()
    return share(sum(1 for m in verbs if "past" in m.grammemes), len(verbs))


def m_tense_pres_share(a):
    verbs = a.finite_verbs()
    return share(sum(1 for m in verbs if "pres" in m.grammemes), len(verbs))


def m_participle_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos in ("PRTF", "PRTS")), len(a.words))


def m_gerund_share(a):
    return share(sum(1 for m in a.need_morph() if m.pos == "GRND"), len(a.words))


def m_dep_mean(a):
    depths = []
    for sent in a.need_syntax():
        depths.extend(sent.depth(i) for i in sent.word_positions())
    return mean(depths)


def m_dep_max_mean(a):
    maxima = []
    for sent in a.need_syntax():
        positions = sent.word_positions()
        if positions:
            maxima.append(max(sent.depth(i) for i in positions))
    return mean(maxima)


def m_sub_sent_share(a):
    sents = a.need_syntax()
    flagged = sum(1 for sent in sents
                  if any(sent.base_rel(t) in CLAUSE_RELS for t in sent.tokens))
    return share(flagged, len(sents))


def m_sub_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens if sent.base_rel(t) in CLAUSE_RELS)
    return share(total, len(sents))


def m_relcl_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens
                if sent.base_rel(t) == "acl" and sent.subtype(t) == "relcl")
    return share(total, len(sents))


def m_advcl_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens if sent.base_rel(t) == "advcl")
    return share(total, len(sents))


def m_acl_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens
                if sent.base_rel(t) == "acl" and sent.subtype(t) != "relcl")
    return share(total, len(sents))


def m_ccomp_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens
                if sent.base_rel(t) in ("ccomp", "xcomp"))
    return share(total, len(sents))


def m_parataxis_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens if sent.base_rel(t) == "parataxis")
    return share(total, len(sents))


def m_conj_per_sent(a):
    sents = a.need_syntax()
    total = sum(1 for sent in sents for t in sent.tokens if sent.base_rel(t) == "conj")
    return share(total, len(sents))


def m_abstract_share(a):
    nouns = a.nouns()
    abstract = sum(1 for m in nouns if m.lemma.endswith(ABSTRACT_SUFFIXES))
    return share(abstract, len(nouns))


def m_nominalization_per1000(a):
    nouns = a.nouns()
    count = sum(1 for m in nouns if m.lemma.endswith(NOMINALIZATION_SUFFIXES))
    return per1000(count, len(a.words))


def m_noun_verb_ratio(a):
    return share(len(a.nouns()), len(a.verbs()))


def m_adj_noun_ratio(a):
    return share(len(a.adjectives()), len(a.nouns()))


def m_gen_chain_per1000(a):
    chains, run = 0, 0
    for m in a.need_morph():
        if m.pos == "NOUN" and ({"gent", "gen1", "gen2"} & m.grammemes):
            run += 1
        else:
            if run >= 3:
                chains += 1
            run = 0
    if run >= 3:
        chains += 1
    return per1000(chains, len(a.words))


def m_gen_share(a):
    nouns = a.nouns()
    return share(sum(1 for m in nouns if {"gent", "gen1", "gen2"} & m.grammemes), len(nouns))


def m_connective_category_per1000(category):
    def metric(a):
        return per1000(phrase_occurrences(a.lower_text, CONNECTIVES[category]),
                       len(a.need_words()))
    return metric


def m_connective_per1000(a):
    total = sum(phrase_occurrences(a.lower_text, phrases)
                for phrases in CONNECTIVES.values())
    return per1000(total, len(a.need_words()))


def m_connective_distinct(a):
    return float(sum(1 for phrases in CONNECTIVES.values()
                     for phrase in phrases
                     if phrase_occurrences(a.lower_text, [phrase])))


def m_connective_diversity(a):
    used = sum(1 for phrases in CONNECTIVES.values()
               if phrase_occurrences(a.lower_text, phrases))
    return share(used, len(CONNECTIVES))


def m_empty_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, EMPTY_CONNECTIVES), len(a.need_words()))


def m_metatext_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, METATEXT), len(a.need_words()))


def m_anaphora_per_sent(a):
    starts = {"это", "этот", "эта", "эти", "тот", "та", "те", "такой", "такая",
              "такие", "он", "она", "оно", "они", "его", "её", "ее", "их", "там",
              "тогда", "отсюда"}
    flagged = 0
    for sentence in a.need_sentences():
        words = tokenize(sentence)
        if words and words[0].lower() in starts:
            flagged += 1
    return share(flagged, len(a.sentences))


def dialogue_sentences(a):
    result = []
    for sentence in a.need_sentences():
        if QUOTE_RE.search(sentence) or sentence.lstrip().startswith(("—", "–")):
            result.append(sentence)
    return result


def reply_segments(a):
    segments = []
    for sentence in a.need_sentences():
        if sentence.lstrip().startswith(("—", "–")):
            segments.append(sentence)
        for match in QUOTE_RE.finditer(sentence):
            segments.append(next(g for g in match.groups() if g is not None))
    return segments


def m_dialogue_sent_share(a):
    return share(len(dialogue_sentences(a)), len(a.need_sentences()))


def m_dialogue_word_share(a):
    words = sum(len(tokenize(segment)) for segment in reply_segments(a))
    return share(words, len(a.need_words()))


def m_reply_len_mean(a):
    segments = reply_segments(a)
    if not segments:
        raise Unavailable("в тексте нет реплик")
    return mean([len(tokenize(segment)) for segment in segments])


def m_speech_verb_per1000(a):
    verbs = a.verbs()
    count = sum(1 for m in verbs if m.lemma in SPEECH_VERBS)
    return per1000(count, len(a.words))


def m_dialogue_speech_share(a):
    sents = dialogue_sentences(a)
    if not sents:
        raise Unavailable("в тексте нет реплик")
    speech_lemmas = {m.lemma for m in a.verbs() if m.lemma in SPEECH_VERBS}
    flagged = 0
    for sentence in sents:
        lemmas = {a.engine.parse(word).lemma for word in tokenize(sentence)}
        if lemmas & speech_lemmas:
            flagged += 1
    return share(flagged, len(sents))


def m_scene_share(a):
    dialogue = dialogue_sentences(a)
    scenes = 0
    for paragraph in a.need_paragraphs():
        has_dialogue = any(s in paragraph for s in dialogue)
        infos = [a.engine.parse(word) for word in tokenize(paragraph)]
        perfect_past = sum(1 for m in infos if m.pos == "VERB"
                           and "perf" in m.grammemes and "past" in m.grammemes)
        if has_dialogue or perfect_past >= 2:
            scenes += 1
    return share(scenes, len(a.paragraphs))


def m_time_marker_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, TIME_MARKERS), len(a.need_words()))


def m_perfect_past_share(a):
    verbs = a.need_morph()
    finite = [m for m in verbs if m.pos == "VERB"]
    count = sum(1 for m in finite if "perf" in m.grammemes and "past" in m.grammemes)
    return share(count, len(finite))


def m_first_person_per1000(a):
    morphs = a.need_morph()
    pronouns = ("я", "мы", "мой", "наш", "меня", "мне", "мной", "нас", "нам", "нами")
    count = sum(1 for m in morphs if m.pos == "NPRO" and m.lemma in pronouns)
    count += sum(1 for m in morphs if m.pos == "VERB" and "1per" in m.grammemes)
    return per1000(count, len(a.words))


def m_mattr(a):
    lemmas = [m.lemma for m in a.need_morph()]
    if not lemmas:
        raise Unavailable("в тексте нет слов")
    window = min(50, len(lemmas))
    if window == 1:
        return 1.0
    values = []
    for start in range(0, len(lemmas) - window + 1):
        chunk = lemmas[start:start + window]
        values.append(len(set(chunk)) / window)
    return mean(values)


def m_hapax_share(a):
    content = a.content_words()
    counts = {}
    for m in content:
        counts[m.lemma] = counts.get(m.lemma, 0) + 1
    hapax = sum(1 for n in counts.values() if n == 1)
    return share(hapax, len(content))


def m_yule_k(a):
    words = a.lower_words
    if len(words) < 2:
        raise Unavailable("меньше двух слов")
    frequencies = {}
    for word in words:
        frequencies[word] = frequencies.get(word, 0) + 1
    n = len(words)
    return 10 ** 4 * (sum(i * i for i in frequencies.values()) - n) / (n * n)


def m_word_len_mean(a):
    lengths = [letters_len(word) for word in a.need_words()]
    lengths = [n for n in lengths if n]
    return mean(lengths)


def m_stopword_share(a):
    return share(sum(1 for word in a.lower_words if word in STOPWORDS), len(a.words))


def m_repeat_window_share(a):
    content = a.content_words()
    if not content:
        raise Unavailable("нет содержательных слов")
    seen = []
    repeats = 0
    for m in content:
        if m.lemma in seen[-20:]:
            repeats += 1
        seen.append(m.lemma)
    return share(repeats, len(content))


def m_repeat_window_max(a):
    content = [m.lemma for m in a.content_words()]
    if not content:
        raise Unavailable("нет содержательных слов")
    best = 1
    for start in range(len(content)):
        window = content[start:start + 50]
        best = max(best, max(window.count(lemma) for lemma in set(window)))
    return float(best)


def m_punct_mark_per1000(a):
    text = a.text
    count = sum(text.count(ch) for ch in ",.;:!?…")
    count += text.count("—") + text.count("–")
    count += a.quote_pairs() + a.paren_pairs()
    return per1000(count, len(a.need_words()))


def m_punct_comma_per1000(a):
    return per1000(a.text.count(","), len(a.need_words()))


def m_punct_dash_per1000(a):
    return per1000(a.text.count("—") + a.text.count("–"), len(a.need_words()))


def m_punct_colon_per1000(a):
    return per1000(a.text.count(":"), len(a.need_words()))


def m_punct_semicolon_per1000(a):
    return per1000(a.text.count(";"), len(a.need_words()))


def m_punct_question_per1000(a):
    return per1000(a.text.count("?"), len(a.need_words()))


def m_punct_exclam_per1000(a):
    return per1000(a.text.count("!"), len(a.need_words()))


def m_punct_quote_per1000(a):
    return per1000(a.quote_pairs(), len(a.need_words()))


def m_punct_paren_per1000(a):
    return per1000(a.paren_pairs(), len(a.need_words()))


def m_punct_gap_mean(a):
    segments = [s for s in SLASH_SENTENCE.split(a.text) if tokenize(s)]
    lengths = [len(tokenize(s)) for s in segments]
    return mean(lengths)


def m_dash_colon_ratio(a):
    colons = a.text.count(":")
    if not colons:
        raise Unavailable("в тексте нет двоеточий")
    return (a.text.count("—") + a.text.count("–")) / colons


def m_comma_per_sent(a):
    return share(a.text.count(","), len(a.need_sentences()))


def m_para_len_mean(a):
    return mean(a.need_paragraphs() and a.para_word_counts)


def m_para_len_median(a):
    return float(statistics.median(a.para_word_counts))


def m_para_len_cv(a):
    if len(a.para_word_counts) < 2:
        raise Unavailable("меньше двух абзацев")
    average = mean(a.para_word_counts)
    if average == 0:
        raise Unavailable("нулевая средняя длина абзаца")
    return statistics.pstdev(a.para_word_counts) / average


def m_para_sent_mean(a):
    return share(len(a.need_sentences()), len(a.need_paragraphs()))


def m_para_one_sent_share(a):
    one = sum(1 for p in a.need_paragraphs() if len(split_sentences(p)) == 1)
    return share(one, len(a.paragraphs))


def m_intensifier_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, INTENSIFIERS), len(a.need_words()))


def m_evaluative_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, EVALUATIVE), len(a.need_words()))


def m_hedge_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, HEDGES), len(a.need_words()))


def m_modal_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, MODAL), len(a.need_words()))


def m_passive_share(a):
    morphs = a.need_morph()
    forms = [m for m in morphs if m.pos in ("VERB", "INFN", "PRTF", "PRTS")]
    passive = 0
    for m, word in zip(morphs, a.lower_words):
        if m.pos in ("VERB", "INFN", "PRTF", "PRTS"):
            if m.pos == "PRTS" or "pssv" in m.grammemes or word.endswith(("ся", "сь")):
                passive += 1
    return share(passive, len(forms))


def m_cliche_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, CLICHES), len(a.need_words()))


def m_style_metatext_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, METATEXT), len(a.need_words()))


def m_colloquial_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, COLLOQUIAL), len(a.need_words()))


def m_particle_per1000(a):
    return per1000(sum(1 for m in a.need_morph() if m.pos == "PRCL"), len(a.words))


def m_pron_first_per1000(a):
    pronouns = ("я", "мы", "мой", "наш", "меня", "мне", "мной", "нас", "нам", "нами")
    return per1000(sum(1 for m in a.need_morph()
                       if m.pos == "NPRO" and m.lemma in pronouns), len(a.words))


def m_pron_second_per1000(a):
    pronouns = ("ты", "вы", "ваш", "тебя", "тебе", "вам", "вас", "вами", "тобой")
    return per1000(sum(1 for m in a.need_morph()
                       if m.pos == "NPRO" and m.lemma in pronouns), len(a.words))


def m_bookish_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, BOOKISH), len(a.need_words()))


def m_adj_long_share(a):
    adjectives = a.adjectives()
    return share(sum(1 for m in adjectives if len(m.lemma) > 10), len(adjectives))


def m_noun_long_share(a):
    nouns = a.nouns()
    return share(sum(1 for m in nouns if len(m.lemma) > 10), len(nouns))


def m_sent_start_prep_share(a):
    flagged = 0
    total = 0
    for sent in a.need_syntax():
        positions = sent.word_positions()
        if not positions:
            continue
        total += 1
        if sent.tokens[positions[0]].pos in ("ADP", "CCONJ", "SCONJ"):
            flagged += 1
    return share(flagged, total)


def m_ai_marker_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, AI_MARKERS), len(a.need_words()))


def m_ai_metatext_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, AI_METATEXT), len(a.need_words()))


def m_ai_ritual_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, AI_RITUAL), len(a.need_words()))


def m_ai_address_per1000(a):
    return per1000(phrase_occurrences(a.lower_text, AI_ADDRESS), len(a.need_words()))


def m_ai_notxbuty_per1000(a):
    patterns = [r"не\s+(?:просто|только)\s+[^,.;:!?]{1,60},\s*а\s+",
                r"не\s+столько\s+[^,.;:!?]{1,60},\s*сколько\b"]
    count = sum(len(re.findall(p, a.lower_text)) for p in patterns)
    return per1000(count, len(a.need_words()))


def m_ai_bold_per1000(a):
    return per1000(a.text.count("**") // 2, len(a.need_words()))


def m_ai_heading_per1000(a):
    lines = [line for line in a.text.splitlines() if line.strip()]
    count = sum(1 for line in lines if line.lstrip().startswith("#"))
    return per1000(count, len(a.need_words()))


def m_ai_bullet_per1000(a):
    lines = [line for line in a.text.splitlines() if line.strip()]
    count = sum(1 for line in lines if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line))
    return per1000(count, len(a.need_words()))


def m_ai_emoji_per1000(a):
    emoji = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
    return per1000(len(emoji.findall(a.text)), len(a.need_words()))


def m_ai_list_ratio(a):
    lines = [line for line in a.text.splitlines() if line.strip()]
    if not lines:
        raise Unavailable("нет непустых строк")
    items = sum(1 for line in lines
                if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line))
    return share(items, len(lines))


def m_ai_uniform_share(a):
    lengths = a.need_sentences() and a.sent_word_counts
    median = statistics.median(lengths)
    return share(sum(1 for n in lengths if abs(n - median) <= 2), len(lengths))


def m_ai_triple_share(a):
    triples, rows = 0, 0
    for sent in a.need_syntax():
        groups = {}
        for i, token in enumerate(sent.tokens):
            if sent.base_rel(token) == "conj":
                groups.setdefault(token.head_id, 0)
                groups[token.head_id] += 1
        for head, children in groups.items():
            members = children + 1
            if members >= 2:
                rows += 1
                if members == 3:
                    triples += 1
    return share(triples, rows)


REGISTRY = {
    "syntax.sent.len.mean": m_sent_len_mean,
    "syntax.sent.len.median": m_sent_len_median,
    "syntax.sent.len.p90": m_sent_len_p90,
    "syntax.sent.len.cv": m_sent_len_cv,
    "syntax.sent.len.short.share": m_sent_short_share,
    "syntax.sent.len.long.share": m_sent_long_share,
    "syntax.sent.len.autocorr": m_sent_autocorr,
    "syntax.clause.len.mean": m_clause_len_mean,
    "syntax.clause.per.sent": m_clause_per_sent,
    "syntax.inversion.share": m_inversion_share,
    "syntax.subject.first.share": m_subject_first_share,
    "syntax.subject.absent.share": m_subject_absent_share,
    "syntax.simple.share": m_simple_share,
    "syntax.predicate.nominal.share": m_nominal_predicate_share,
    "syntax.verb.final.share": m_verb_final_share,
    "morph.noun.share": m_noun_share,
    "morph.verb.share": m_verb_share,
    "morph.adj.share": m_adj_share,
    "morph.adv.share": m_adv_share,
    "morph.pron.share": m_pron_share,
    "morph.num.share": m_num_share,
    "morph.func.share": m_func_share,
    "morph.verb.aspect.pf.share": m_aspect_pf_share,
    "morph.verb.mood.impr.share": m_mood_impr_share,
    "morph.verb.mood.indc.share": m_mood_indc_share,
    "morph.verb.tense.past.share": m_tense_past_share,
    "morph.verb.tense.pres.share": m_tense_pres_share,
    "morph.participle.share": m_participle_share,
    "morph.gerund.share": m_gerund_share,
    "depth.dep.mean": m_dep_mean,
    "depth.dep.max.mean": m_dep_max_mean,
    "depth.sub.sent.share": m_sub_sent_share,
    "depth.sub.per.sent": m_sub_per_sent,
    "depth.relcl.per.sent": m_relcl_per_sent,
    "depth.advcl.per.sent": m_advcl_per_sent,
    "depth.acl.per.sent": m_acl_per_sent,
    "depth.ccomp.per.sent": m_ccomp_per_sent,
    "depth.parataxis.per.sent": m_parataxis_per_sent,
    "depth.coord.per.sent": m_conj_per_sent,
    "nominal.abstract.share": m_abstract_share,
    "nominal.nominalization.per1000": m_nominalization_per1000,
    "nominal.noun.verb.ratio": m_noun_verb_ratio,
    "nominal.adj.noun.ratio": m_adj_noun_ratio,
    "nominal.gen.chain.per1000": m_gen_chain_per1000,
    "nominal.gen.share": m_gen_share,
    "discourse.cause.per1000": m_connective_category_per1000("cause"),
    "discourse.consequence.per1000": m_connective_category_per1000("consequence"),
    "discourse.contrast.per1000": m_connective_category_per1000("contrast"),
    "discourse.concession.per1000": m_connective_category_per1000("concession"),
    "discourse.condition.per1000": m_connective_category_per1000("condition"),
    "discourse.spec.per1000": m_connective_category_per1000("spec"),
    "discourse.addition.per1000": m_connective_category_per1000("addition"),
    "discourse.time.per1000": m_connective_category_per1000("time"),
    "discourse.purpose.per1000": m_connective_category_per1000("purpose"),
    "discourse.connective.per1000": m_connective_per1000,
    "discourse.connective.distinct": m_connective_distinct,
    "discourse.connective.diversity": m_connective_diversity,
    "discourse.empty.per1000": m_empty_per1000,
    "discourse.metatext.per1000": m_metatext_per1000,
    "discourse.anaphora.per.sent": m_anaphora_per_sent,
    "narr.dialogue.sent.share": m_dialogue_sent_share,
    "narr.dialogue.word.share": m_dialogue_word_share,
    "narr.dialogue.reply.len.mean": m_reply_len_mean,
    "narr.speech.verb.per1000": m_speech_verb_per1000,
    "narr.dialogue.speech.share": m_dialogue_speech_share,
    "narr.scene.share": m_scene_share,
    "narr.time.marker.per1000": m_time_marker_per1000,
    "narr.perfect.past.share": m_perfect_past_share,
    "narr.first.person.per1000": m_first_person_per1000,
    "lexis.ttr.mattr": m_mattr,
    "lexis.hapax.share": m_hapax_share,
    "lexis.yule.k": m_yule_k,
    "lexis.word.len.mean": m_word_len_mean,
    "lexis.stopword.share": m_stopword_share,
    "lexis.repeat.window.share": m_repeat_window_share,
    "lexis.repeat.window.max": m_repeat_window_max,
    "punct.mark.per1000": m_punct_mark_per1000,
    "punct.comma.per1000": m_punct_comma_per1000,
    "punct.dash.per1000": m_punct_dash_per1000,
    "punct.colon.per1000": m_punct_colon_per1000,
    "punct.semicolon.per1000": m_punct_semicolon_per1000,
    "punct.question.per1000": m_punct_question_per1000,
    "punct.exclam.per1000": m_punct_exclam_per1000,
    "punct.quote.per1000": m_punct_quote_per1000,
    "punct.parenthesis.per1000": m_punct_paren_per1000,
    "punct.gap.mean": m_punct_gap_mean,
    "punct.dash.colon.ratio": m_dash_colon_ratio,
    "punct.comma.per.sent": m_comma_per_sent,
    "para.len.mean": m_para_len_mean,
    "para.len.median": m_para_len_median,
    "para.len.cv": m_para_len_cv,
    "para.sent.mean": m_para_sent_mean,
    "para.one.sent.share": m_para_one_sent_share,
    "style.intensifier.per1000": m_intensifier_per1000,
    "style.evaluative.per1000": m_evaluative_per1000,
    "style.hedge.per1000": m_hedge_per1000,
    "style.modal.per1000": m_modal_per1000,
    "style.passive.share": m_passive_share,
    "style.cliche.per1000": m_cliche_per1000,
    "style.metatext.per1000": m_style_metatext_per1000,
    "style.colloquial.per1000": m_colloquial_per1000,
    "style.particle.per1000": m_particle_per1000,
    "style.pron.1st.per1000": m_pron_first_per1000,
    "style.pron.2nd.per1000": m_pron_second_per1000,
    "style.bookish.per1000": m_bookish_per1000,
    "style.adj.long.share": m_adj_long_share,
    "style.noun.long.share": m_noun_long_share,
    "style.sent.start.prep.share": m_sent_start_prep_share,
    "ai.marker.per1000": m_ai_marker_per1000,
    "ai.metatext.per1000": m_ai_metatext_per1000,
    "ai.ritual.per1000": m_ai_ritual_per1000,
    "ai.address.per1000": m_ai_address_per1000,
    "ai.notxbuty.per1000": m_ai_notxbuty_per1000,
    "ai.bold.per1000": m_ai_bold_per1000,
    "ai.heading.per1000": m_ai_heading_per1000,
    "ai.bullet.per1000": m_ai_bullet_per1000,
    "ai.emoji.per1000": m_ai_emoji_per1000,
    "ai.list.ratio": m_ai_list_ratio,
    "ai.sent.len.uniform.share": m_ai_uniform_share,
    "ai.triple.share": m_ai_triple_share,
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, enable_syntax=True):
        self.catalogue = self._load_catalogue()
        self.metric_ids = [item["id"] for item in self.catalogue["metrics"]]
        catalogued = set(self.metric_ids)
        if catalogued != set(REGISTRY):
            missing = sorted(catalogued - set(REGISTRY))
            extra = sorted(set(REGISTRY) - catalogued)
            raise RuntimeError("каталог и движок расходятся: нет %s, лишние %s"
                               % (missing, extra))
        self.morph = None
        self.morph_reason = None
        self._segmenter = None
        self._morph_tagger = None
        self._syntax_parser = None
        self._morph_cache = {}
        self.syntax_reason = None
        self._init_morph()
        if enable_syntax:
            self._init_syntax()
        else:
            self.syntax_reason = "natasha: разбор отключён"

    @staticmethod
    def _load_catalogue():
        with open(CATALOGUE_PATH, encoding="utf-8") as handle:
            return json.load(handle)

    def _init_morph(self):
        try:
            import pymorphy3
            self.morph = pymorphy3.MorphAnalyzer()
            self.parse("проверка")
        except Exception as exc:
            self.morph = None
            self.morph_reason = "pymorphy3: %s: %s" % (type(exc).__name__, exc)

    def _init_syntax(self):
        try:
            from natasha import (Segmenter, MorphVocab, NewsEmbedding,
                                 NewsMorphTagger, NewsSyntaxParser)
            segmenter = Segmenter()
            MorphVocab()
            embedding = NewsEmbedding()
            morph_tagger = NewsMorphTagger(embedding)
            syntax_parser = NewsSyntaxParser(embedding)
            self._segmenter = segmenter
            self._morph_tagger = morph_tagger
            self._syntax_parser = syntax_parser
            self.parse_syntax("Это проверка разбора.")
        except Exception as exc:
            self._segmenter = None
            self._morph_tagger = None
            self._syntax_parser = None
            self.syntax_reason = "natasha: %s: %s" % (type(exc).__name__, exc)

    def parse(self, word):
        cached = self._morph_cache.get(word)
        if cached is not None:
            return cached
        parsed = self.morph.parse(word.lower())[0]
        tag = parsed.tag
        info = MorphInfo(tag.POS, parsed.normal_form, frozenset(tag.grammemes))
        self._morph_cache[word] = info
        return info

    def parse_syntax(self, text):
        if self._syntax_parser is None:
            raise RuntimeError(self.syntax_reason or "синтаксический разбор недоступен")
        from natasha import Doc
        document = Doc(text)
        document.segment(self._segmenter)
        document.tag_morph(self._morph_tagger)
        document.parse_syntax(self._syntax_parser)
        return [NatSent(list(sentence.tokens)) for sentence in document.sents]

    def measure(self, text):
        analysis = Analysis(text, self)
        metrics = {}
        unavailable = {}
        for metric_id in self.metric_ids:
            try:
                metrics[metric_id] = round6(REGISTRY[metric_id](analysis))
            except Unavailable as exc:
                unavailable[metric_id] = str(exc)
            except Exception as exc:  # a broken metric must not kill the process
                unavailable[metric_id] = "%s: %s" % (type(exc).__name__, exc)
        counts = {
            "words": len(analysis.words),
            "sentences": len(analysis.sentences),
            "paragraphs": len(analysis.paragraphs),
        }
        return {"metrics": metrics, "counts": counts, "unavailable": unavailable}


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------

def handle(engine, request):
    request_id = request.get("id") if isinstance(request, dict) else None
    if not isinstance(request, dict):
        return {"id": None, "ok": False, "error": "запрос должен быть JSON-объектом"}
    action = request.get("action")
    try:
        if action == "ping":
            result = {"pong": True, "version": VERSION,
                      "metrics": len(engine.metric_ids)}
        elif action == "catalogue":
            result = engine.catalogue
        elif action == "measure":
            text = request.get("text")
            if not isinstance(text, str):
                raise ValueError("поле text должно быть строкой")
            if not text.strip():
                raise ValueError("пустой текст")
            language = (request.get("options") or {}).get("language", "ru")
            if language != "ru":
                raise ValueError("поддерживается только язык ru")
            result = engine.measure(text)
        else:
            raise ValueError("неизвестное действие: %r" % (action,))
    except Exception as exc:
        return {"id": request_id, "ok": False, "error": str(exc)}
    return {"id": request_id, "ok": True, "result": result}


def dumps(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def serve(engine):
    sys.stdout.write(dumps({"ready": True, "metrics": len(engine.metric_ids),
                            "version": VERSION}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            answer = {"id": None, "ok": False, "error": "некорректный JSON: %s" % exc}
        else:
            answer = handle(engine, request)
        sys.stdout.write(dumps(answer) + "\n")
        sys.stdout.flush()


def main(argv):
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except Exception:
        pass
    mode = "--serve" if "--serve" in argv else "--stdin"
    engine = Engine()
    if mode == "--serve":
        serve(engine)
        return 0
    raw = sys.stdin.read()
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        print(dumps({"id": None, "ok": False, "error": "пустой запрос"}))
        return 1
    try:
        request = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        print(dumps({"id": None, "ok": False, "error": "некорректный JSON: %s" % exc}))
        return 1
    print(dumps(handle(engine, request)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
