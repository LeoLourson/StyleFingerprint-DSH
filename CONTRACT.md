# Контракт: форматы, API, границы работы

Все пути ниже — относительно корня репозитория `C:\Users\LeoLeon\Documents\dsh-style-fingerprint`.
Файл читается участниками до первой правки. Изменение контракта — через Lead, одной
правкой этого файла, с записью в конце раздела «Журнал».

## 1. Владение файлами

| Участник | Пишет | Не трогает |
|---|---|---|
| Lead | `PLAN.md`, `CONTRACT.md`, `README.md`, `law/00-central.md`, `data/profiles.json`, `data/settings.json`, установка в профиль | всё остальное без договорённости |
| corpus-metrics | `corpus/**`, `tools/fetch_corpus.py`, `tools/metrics.py`, `tools/build_fingerprint.py`, `data/fingerprint.json`, `reference/metrics.md`, `tests/test_metrics.py` | `law/`, `templates/`, `index.js`, `client.js` |
| rulebook | `law/10-*.md` … `law/50-*.md`, `law/45-*.md`, `reference/*.md` кроме `metrics.md`, `templates/*.md`, `data/rules.json`, `reference/migration.md` | `index.js`, `client.js`, `tools/`, `corpus/` |
| plugin | `package.json`, `cordis.patch.yml`, `index.js`, `client.js`, `locale/**`, `icon.svg`, `tests/*.test.mjs` | `law/`, `reference/`, `templates/`, `corpus/`, `data/*.json` |

`data/profiles.json` и `data/settings.json` — только Lead: plugin их читает, rulebook
на них ссылается.

## 2. Корпус

`corpus/text/<author>/<work>.txt` — UTF-8 без BOM, `\n`, один абзац на строку,
пустая строка между абзацами, без заголовков глав, без предисловий, комментариев,
примечаний, ссылок и служебных блоков источника. Кавычки «ёлочки» как в источнике.

`corpus/manifest.json`:

```json
{
  "version": 1,
  "built": "2026-11-13T00:00:00Z",
  "coverage": {"tolstoy": {"words": 0, "works": 0, "target": 1500000}, "dostoevsky": {"words": 0, "works": 0, "target": 1500000}},
  "notes": "чем корпус неполон",
  "works": [
    {"id": "tolstoy/anna-karenina", "author": "tolstoy", "title": "Анна Каренина",
     "genre": "novel", "year": 1877, "source": "wikisource", "url": "https://…",
     "file": "corpus/text/tolstoy/anna-karenina.txt", "chars": 0, "words": 0,
     "sentences": 0, "sha256": "…", "license": "CC BY-SA 4.0", "notes": ""}
  ],
  "excluded": [{"title": "…", "reason": "старая орфография / версия 2 / служебная страница"}]
}
```

`author` ∈ `tolstoy`, `dostoevsky`. `genre` ∈ `novel`, `novella`, `story`, `play`,
`essay`, `letter`, `diary`. Год — год первой публикации или написания, как на странице
источника; неизвестен — `null`.

## 3. Метрики

Спецификация — `reference/metrics.md`: для каждой метрики идентификатор, единица
(`per1000` на 1000 слов, `ratio`, `words`, `share`), формула словами, пример.
Токенизация: последовательности букв, цифр и дефиса внутри слова; апострофы не режут.
Предложение: конец — `.`, `!`, `?`, `…` с учётом сокращений (г., т. е., и т. п.,
в. ., до н. э.) и многоточия. Абзац — блок между пустыми строками.

`data/fingerprint.json`:

```json
{
  "version": 1,
  "built": "2026-11-13T00:00:00Z",
  "corpus": {"works": 0, "words": 0, "authors": ["tolstoy", "dostoevsky"]},
  "metricSpec": "reference/metrics.md",
  "profiles": {
    "tolstoy": {"novel": {…}, "story": {…}, "all": {…}},
    "dostoevsky": {"novel": {…}, "all": {…}},
    "union": {"all": {…}}
  },
  "metrics": {
    "s.len.mean": {"label": "средняя длина предложения", "unit": "words",
      "by": {"tolstoy/all": {"p5": 0, "p50": 0, "p95": 0, "n": 0},
             "tolstoy/novel": {…}, "dostoevsky/all": {…}, "union/all": {…}},
      "source": "corpus"}
  },
  "targets": {
    "prose_narrative": {"s.len.mean": {"min": 0, "max": 0, "source": "corpus:union/all"}},
    "technical_doc": {"…": {"min": 0, "max": 0, "source": "rule:"}},
    "business_letter": {}, "chat_answer": {}, "prose_essay": {}
  }
}
```

`profiles.*` — сводки по профилю (средние метрик), `metrics` — разбросы, `targets` —
то, что проверяет инструмент. Целевой интервал берётся из p5–p95 корпуса с запасом
`margin` (значение по умолчанию 0,15 интервала) для `prose_*`; для `technical_doc`,
`business_letter`, `chat_answer` интервалы назначаются правилами и помечаются
`"source": "rule"`. Профили жанров в `targets`: `prose_narrative`, `prose_essay`,
`technical_doc`, `business_letter`, `chat_answer`.

## 4. Правила

`data/rules.json`:

```json
{
  "version": 1,
  "built": "2026-11-13",
  "rules": [
    {"id": "lang.connective.empty", "layer": "language", "title": "Пустая связка",
     "severity": "error", "profiles": ["*"],
     "kind": "regex", "pattern": "таким образом|в данной связи|в этой связи",
     "units": "per1000", "max": 2,
     "message": "Связка обещает вывод, которого нет",
     "fix": "Назвать отношение словом или снять связку",
     "source": "text-hygiene B4"}
  ],
  "stopwords": ["…"],
  "lexicon": {
    "connectives": {"cause": ["потому что", "так как", "поскольку"],
                    "consequence": ["поэтому", "так что", "отсюда"],
                    "contrast": ["но", "однако", "зато"],
                    "concession": ["хотя", "несмотря на", "пусть"],
                    "condition": ["если", "когда", "при условии"],
                    "spec": ["то есть", "а именно", "причём"],
                    "addition": ["и", "к тому же", "вдобавок"]},
    "intensifiers": ["очень", "крайне", "весьма"],
    "hedges": ["возможно", "вероятно", "по-видимому"],
    "speechVerbs": ["сказал", "спросил", "ответил"],
    "abstractSuffixes": ["ость", "ание", "ение", "ция", "изм", "ство"]
  }
}
```

`kind` ∈ `regex` (число вхождений, норма на `units`), `forbidden` (любое вхождение —
`error`), `metric` (метрика `metric` сравнивается с целью профиля), `manual` (только
сообщение, без автоматического поиска). Регулярные выражения — синтаксис JavaScript,
без inline-флагов; регистр игнорируется всегда. Идентификатор строится как
`слой.тема.случай`, латиницей, точки и дефисы.

## 5. Инструмент `style`

Один инструмент, действия:

| действие | вход | выход |
|---|---|---|
| `brief` | `genre`, `audience?`, `length?` | `{law, template: {id, title, body, slots}, targets, checklist}` |
| `guide` | `name` (`law/00-central`, `reference/…`, `templates/…` или короткое имя) | `{name, text}` |
| `check` | `text`, `profile?`, `strict?` | результат проверки (ниже) |
| `measure` | `text` | `{metrics}` |
| `corpus` | `query`, `author?`, `genre?`, `k?` | `{matches: [{work, author, genre, year, excerpt}]}` |
| `fingerprint` | `author?`, `genre?`, `metric?` | `{metrics, targets}` |
| `config` | — | действующие настройки и их источник |

Результат `check`:

```json
{"profile": "prose_narrative", "verdict": "pass|warn|fail", "score": 0,
 "counts": {"words": 0, "sentences": 0, "paragraphs": 0},
 "metrics": {"…": 0}, "targets": {"…": {"min": 0, "max": 0}},
 "violations": [{"rule": "…", "severity": "error", "title": "…", "count": 0,
                 "places": ["фрагмент с местом"], "message": "…", "fix": "…"}],
 "next": ["что сделать перед отправкой"]}
```

`verdict`: `fail` — есть `error`; `warn` — есть `warning` или метрика вне цели;
`pass` — нет ни того, ни другого. `place` — фрагмент до 200 символов с контекстом.

## 6. Настройки

`data/settings.json` (значения по умолчанию, читается инструментом и страницей):

```json
{"version": 1,
 "enabled": true,
 "strict": true,
 "defaultProfile": "chat_answer",
 "profiles": {"prose_narrative": true, "prose_essay": true, "technical_doc": true,
              "business_letter": true, "chat_answer": true},
 "layers": {"central": true, "language": true, "composition": true, "author": true,
            "classification": true, "corpus": true},
 "margin": 0.15,
 "gate": {"beforeDocument": true, "beforeAnswer": true, "minWordsForCheck": 60},
 "changedAt": "…", "changedBy": "…"}
```

Действующие настройки = `data/settings.json`, отсутствующие поля — из
`data/profiles.json`. Страница настроек пишет файл и сообщает инструменту без
перезапуска (чтение по mtime).

## 7. Проверки, которые обязаны пройти

1. `node tests/tool.test.mjs` — модуль грузится фейковым контекстом, все действия
   инструмента отвечают, `check` на испорченном тексте даёт нарушения разных слоёв.
2. `node tests/layout.test.mjs` — все пути из контракта существуют, `rules.json` и
   `fingerprint.json` проходят схему, у каждого правила есть `source`.
3. `python -m pytest tests/test_metrics.py` — метрики на контрольном тексте совпадают
   с эталоном `tests/fixtures/metrics-sample.json` (допуск 1e-6).
4. `node tests/agreement.test.mjs` — JS и Python дают одинаковые числа на контрольном
   тексте.
5. Установка: `dsh --profile desktop --dump-config | Select-String 'style-fingerprint'`
   даёт ровно одну запись, без `skipping`.

## 8. Журнал

- 2026-11-13: контракт создан.
