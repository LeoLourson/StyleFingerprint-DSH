# Контракт: форматы, API, границы работы

Все пути — относительно корня `C:\Users\LeoLeon\Documents\dsh-style-fingerprint`. Файл
читается участниками до первой правки. Изменение контракта — только через Lead, одной
правкой этого файла, с записью в журнале в конце.

Окончательные решения пользователя (13.11.2026): пять профилей без художественной прозы
(`chat_answer`, `technical_doc`, `business_letter`, `prose_essay`, `math_paper`); семь
шаблонов; образцы — 120–150 тыс. слов на автора, числа по ним не выдаются за норму, они
служат цитатами; метрики — полный набор с морфологией и синтаксисом (pymorphy3 + natasha);
один входной скилл; все слои в едином формате; невыбранное — в `DEFERRED.md`.

## 1. Владение файлами

| Участник | Пишет | Не трогает |
|---|---|---|
| Lead | `PLAN.md`, `CONTRACT.md`, `README.md`, `DEFERRED.md`, `law/00-central.md`, `data/profiles.json`, `data/settings.json`, установка в профиль | всё остальное без договорённости |
| corpus-scout | `corpus/**`, `tools/fetch_corpus.py`, `tools/build_index.py` | `law/`, `templates/`, `reference/`, `data/`, `index.js`, `client.js` |
| metricist | `tools/analyze.py`, `reference/metrics.md`, `data/metrics.json`, `data/targets.json`, `tests/test_metrics.py`, `tests/fixtures/**` | `law/`, `templates/`, `reference/` кроме `metrics.md`, `index.js`, `client.js` |
| canon-writer | `law/10-*.md` … `law/55-*.md`, `templates/*.md`, `reference/migration.md` | `index.js`, `client.js`, `tools/`, `corpus/`, `data/` |
| pattern-writer | `reference/*.md` кроме `metrics.md`, `data/rules.json` | `index.js`, `client.js`, `tools/`, `corpus/`, `data/metrics.json`, `data/targets.json`, `data/profiles.json` |
| plugin-smith | `package.json`, `cordis.patch.yml`, `index.js`, `client.js`, `locale/**`, `icon.svg`, `tests/*.test.mjs` | `law/`, `reference/`, `templates/`, `corpus/`, `data/` |

Общие файлы не правятся никем, кроме владельца. Нужно изменение чужого файла — сообщение
Lead, Lead решает сам или передаёт владельцу.

## 2. Образцы

`corpus/text/<author>/<work>.txt` — UTF-8 без BOM, `\n`, один абзац на строку, пустая
строка между абзацами. Без заголовков глав, предисловий, комментариев, примечаний,
ссылок, служебных блоков источника. Фрагменты романов — целые главы, разделённые пустой
строкой и строкой `## <роман>, часть <N>, глава <M>`.

Состав: Толстой — «Анна Каренина» (главы), «Хаджи-Мурат», пять рассказов; Достоевский —
«Идиот» (главы), «Преступление и наказание» (главы), пять рассказов. Целевой объём —
120–150 тыс. слов на автора.

`corpus/manifest.json`:

```json
{
  "version": 1,
  "built": "2026-11-13T00:00:00Z",
  "use": "exemplar",
  "coverage": {"tolstoy": {"words": 0, "works": 0, "target": 130000},
               "dostoevsky": {"words": 0, "works": 0, "target": 130000}},
  "works": [
    {"id": "tolstoy/anna-karenina", "author": "tolstoy", "title": "Анна Каренина",
     "genre": "novel", "year": 1877, "source": "wikisource", "url": "https://…",
     "file": "corpus/text/tolstoy/anna-karenina.txt", "chars": 0, "words": 0,
     "sha256": "…", "license": "CC BY-SA 4.0", "excerpt": "", "notes": ""}
  ],
  "excluded": [{"title": "…", "reason": "старая орфография"}]
}
```

`author` ∈ `tolstoy`, `dostoevsky`. `genre` ∈ `novel`, `novella`, `story`. `year` —
год первой публикации или написания; неизвестен — `null`.

`corpus/index.json` — цитатный индекс: `{"version":1,"built":"…","excerpts":[{"id":"tolstoy/anna-karenina#12","author":"tolstoy","work":"Анна Каренина","title":"Анна Каренина","genre":"novel","year":1877,"kind":"paragraph","text":"…","words":54}]}`.
Абзацы длиной 25–120 слов; одна цитата на 500 слов произведения; `kind` ∈ `paragraph`,
`dialogue`, `scene`.

### Математические образцы

Второй корпус обслуживает профиль `math_paper` и живёт отдельно от литературного:
`corpus/math/shkalikov/<id>.txt` и `corpus/math/manifest.json`. Литературный каталог
`corpus/text/` он не дополняет и его манифест не меняет.

Автор — Андрей Андреевич Шкаликов, источник — Math-Net.Ru, страница персоналии
`personid=12042`. Формат текста тот же: UTF-8 без BOM, `\n`, один абзац на строку.
Текст берётся из текстового слоя PDF статьи, поэтому формулы в нём искажены: они
допустимы как шум и не годятся для цитат. Заголовок статьи, авторы, аннотация, ключевые
слова и библиография остаются: по ним видно, как устроено введение.

Состав — научные статьи, где Шкаликов автор, за все годы. Не входят: некрологи,
юбилейные поздравления, коллективные отчёты кафедры и другие тексты, где он подписан
вместе с десятком авторов и не является автором содержания. Каждое исключение
записывается в `excluded` с причиной. Английские переводы не берутся, если в корпусе
есть русский оригинал: перевод не его язык.

`corpus/math/manifest.json`:

```json
{"version": 1, "built": "…", "author": "shkalikov", "use": "exemplar",
 "coverage": {"shkalikov": {"words": 0, "works": 0, "target": 150000}},
 "source": {"site": "mathnet", "person": 12042,
            "url": "https://www.mathnet.ru/php/person.phtml?personid=12042"},
 "works": [{"id": "shkalikov/mzm14738", "mathnet": "mzm14738", "title": "…",
            "journal": "Матем. заметки", "year": 2025, "pages": "299–319",
            "url": "https://www.mathnet.ru/php/getFT.phtml?jrnid=mzm&paperid=14738&what=fullt&option_lang=rus",
            "file": "corpus/math/shkalikov/mzm14738.txt", "chars": 0, "words": 0,
            "sha256": "…", "notes": ""}],
 "excluded": [{"mathnet": "rm607", "title": "…", "reason": "некролог"}]}
```

PDF после извлечения текста не хранится: источник — MathNet, в манифесте остаются адрес
и sha256 текста. Повторный запуск сборки обязан дать те же файлы; статья, скачать которую
не удалось (закрытый доступ), попадает в `excluded` с причиной `нет открытого полного
текста`, а не в `works` с нулём слов.

`corpus/math/index.json` — тот же цитатный индекс, но отрывки только из прозы статьи:
абзацы введения, где нет формул и разметки. Абзац с символами формул в цитату не идёт.


## 3. Метрики

Спецификация — `reference/metrics.md`: идентификатор, название, единица, формула словами,
минимальный пример, нужен ли разборщик. Единицы: `per1000` (на 1000 слов), `share`
(доля от 1), `ratio`, `mean`, `count`, `words`, `entropy`.

Каталог `data/metrics.json`:

```json
{"version": 1,
 "metrics": [
   {"id": "s.len.mean", "label": "Средняя длина предложения", "unit": "words",
    "needs": "none", "group": "syntax", "spec": "1.1"}
 ],
 "groups": {"syntax": "Длина и ритм", "morphology": "Части речи", "…": "…"}}
```

`needs` ∈ `none`, `morphology`, `syntax`. Группы и минимальное число метрик:
`syntax` 14, `morphology` 12, `morphology.depth` 8, `nominal` 4, `discourse` 10,
`narrative` 8, `lexis` 6, `punctuation` 10, `paragraph` 4, `style` 14, `ai` 10 —
итого не меньше 100.

Целевые интервалы `data/targets.json`:

```json
{"version": 1, "marginDefault": 0.15, "updated": "2026-11-13",
 "profiles": {
   "chat_answer": {
     "s.len.mean": {"min": 10, "max": 26, "source": "rule"},
     "ai.marker.per1000": {"min": 0, "max": 0.5, "source": "rule"}
   },
   "technical_doc": {}, "business_letter": {}, "prose_essay": {}, "math_paper": {}
 }}
```

`source` ∈ `rule`, `corpus`, `author`. Проверяются не все метрики, а ворота: 8–14 на
профиль, остальные выдаются справочно действием `measure`. Правило `kind: "metric"`
ссылается на метрику по идентификатору.

## 4. Правила

`data/rules.json`:

```json
{"version": 1, "built": "2026-11-13",
 "rules": [
   {"id": "lang.connective.empty", "ref": "R-LANG-14", "layer": "language",
    "title": "Пустая связка", "severity": "error",
    "profiles": ["chat_answer", "technical_doc", "business_letter", "prose_essay"],
    "kind": "regex", "pattern": "таким образом|в данной связи|в этой связи",
    "units": "per1000", "max": 2,
    "message": "Связка обещает вывод, которого нет",
    "fix": "Назвать отношение словом или снять связку",
    "source": "text-hygiene B4"},
   {"id": "lang.ai.metatext", "ref": "R-LANG-31", "layer": "language",
    "title": "Мета-текст", "severity": "error", "profiles": ["*"],
    "kind": "forbidden", "pattern": "в этом разделе|как уже было сказано",
    "message": "Текст говорит о себе, а не о предмете",
    "fix": "Убрать анонс или заменить содержанием",
    "source": "text-hygiene A9"}
 ],
 "stopwords": ["…"],
 "lexicon": {"connectives": {"cause": ["потому что", "так как", "поскольку"],
                             "consequence": ["поэтому", "так что", "отсюда"],
                             "contrast": ["но", "однако", "зато"],
                             "concession": ["хотя", "несмотря на", "пусть"],
                             "condition": ["если", "когда"],
                             "spec": ["то есть", "а именно", "причём"],
                             "addition": ["и", "к тому же", "вдобавок"]},
             "intensifiers": ["очень", "крайне", "весьма"],
             "hedges": ["возможно", "вероятно", "по-видимому"],
             "speechVerbs": ["сказал", "спросил", "ответил"],
             "abstractSuffixes": ["ость", "ание", "ение", "ция", "изм", "ство"]}}
```

`kind` ∈ `regex` (норма на `units`), `forbidden` (любое вхождение), `metric` (ссылка на
метрику и цель профиля), `manual` (только сообщение). `ref` — идентификатор правила в
`law/`, обязателен для каждого правила. Регулярные выражения — синтаксис JavaScript, без
inline-флагов, регистр всегда игнорируется. Идентификатор правила `rules.json` —
`слой.тема.случай`, латиницей.

Список правил один: вид правила называет `kind`, а не отдельный массив. Второго списка
запретов нет: одно правило в двух списках давало бы два отчёта об одном нарушении.
`kind: "manual"` проверки не имеет и в отчёт о нарушениях не попадает — он служит
напоминанием для чтения правил.

## 5. Слои и шаблоны

Единый формат файла слоя — паспорт, правила `R-<СЛОЙ>-<номер>`, примеры, чек-лист:

```
---
id: law/language
title: Язык
order: 30
reads: on-demand
source: text-hygiene, paper-de-ai
rules: [R-LANG-01]
---
## Что решает слой
## Правила
### R-LANG-01. Имя
Свойство: что плохо для читателя.
Действие: что делать.
Проверка: как убедиться, что сделано.
## Примеры до и после
## Чек-лист
```

Слои: `law/10-write-for-outsider.md` (слой `language`, центральные формулировки),
`law/20-composition.md` (`composition`), `law/30-language.md` (`language`),
`law/40-author-voice.md` (`author`), `law/45-russian-prose.md` (`corpus`),
`law/50-classification.md` (`classification`), `law/55-math-article.md` (`math`).
`law/00-central.md` пишет Lead, формат тот же, `reads: always`.

Шаблоны (`templates/`): `answer.md`, `report.md`, `readme.md`, `plan.md`, `essay.md`,
`letter.md`, `math-article.md`. Каждый: паспорт (`id`, `profile`, `audience`, `length`),
скелет с слотами, порядок работы, чек-лист, ссылки на правила.

### Распределение номеров правил

Файл слоя — единственный владелец своего диапазона; номер внутри диапазона не
переиспользуется после снятия правила. Ссылка `ref` в `data/rules.json` указывает на
существующий номер.

| Файл | Слой | Номера |
|---|---|---|
| `law/00-central.md` | `law` | без номеров, проверки называются словами |
| `law/10-write-for-outsider.md` | `language` | `R-LAW-01…19` |
| `law/30-language.md` | `language` | `R-LANG-01…39` |
| `law/20-composition.md` | `composition` | `R-COMP-01…19` |
| `law/40-author-voice.md` | `author` | `R-AUTH-01…09` |
| `law/45-russian-prose.md` | `corpus` | `R-CORP-01…09` |
| `law/50-classification.md` | `classification` | `R-CLASS-01…09` |
| `law/55-math-article.md` | `math` | `R-MATH-01…19` |

### Профили в правиле

`profiles: ["*"]` означает «все профили» и допускается только у правил, у которых нет
нормы по профилю: `kind: "forbidden"` и `kind: "manual"`. У `kind: "regex"` и
`kind: "metric"` профили перечисляются явно: норма `max` у профилей разная, и «*»
скрыл бы эту разницу.

## 6. Движок метрик и мост

`tools/analyze.py` — единственная реализация метрик (pymorphy3 + natasha). Режимы:
`python tools/analyze.py --serve` (JSON-строки на stdin, ответ на stdout, по строке на
запрос) и `python tools/analyze.py --stdin` (один запрос, один ответ).

Запрос: `{"id":"1","action":"measure","text":"…","options":{"language":"ru"}}`.
Ответ: `{"id":"1","ok":true,"result":{"metrics":{…},"counts":{"words":0,"sentences":0,"paragraphs":0},"unavailable":{}}}`,
при ошибке — `{"id":"1","ok":false,"error":"текст ошибки"}`.
Действия: `ping`, `catalogue`, `measure`. Первая строка при `--serve`:
`{"ready":true,"metrics":0,"version":"0.1"}`.

Инструмент держит процесс живым: запуск при первом `check`/`measure`, таймаут 20 с на
ответ, перезапуск после падения (не больше двух раз), завершение при выгрузке плагина.
Python недоступен — проверка идёт по `rules.json`, результат содержит
`"engine": {"available": false, "reason": "…"}`.

Путь к интерпретатору: настройка `pythonPath`, по умолчанию
`<корень плагина>/.venv/Scripts/python.exe`, затем `python` из `PATH`.

## 7. Инструмент `style`

| действие | вход | выход |
|---|---|---|
| `brief` | `genre`, `audience?`, `length?`, `language?` | `{profile, law: [{id,title,text}], template: {id,title,body,slots}, targets, checklist, samples}` |
| `guide` | `name` (`law/00-central`, `reference/…`, `templates/…`, короткое имя) | `{name, text}` |
| `check` | `text`, `profile?`, `strict?` | результат проверки |
| `measure` | `text` | `{metrics, counts, catalogue, engine}` |
| `corpus` | `query`, `author?`, `genre?`, `k?` | `{matches: [{id, author, work, genre, year, kind, text}]}` |
| `fingerprint` | `profile?`, `metric?` | `{profiles: {…}, targets, catalogue}` |
| `config` | — | действующие настройки и их источник |

`check`:

```json
{"profile": "chat_answer", "verdict": "pass|warn|fail", "score": 0,
 "counts": {"words": 0, "sentences": 0, "paragraphs": 0},
 "metrics": {"…": 0}, "targets": {"…": {"min": 0, "max": 0}},
 "engine": {"available": true, "version": "0.1"},
 "violations": [{"rule": "lang.connective.empty", "ref": "R-LANG-14", "severity": "error",
                 "title": "…", "count": 0, "places": ["до 200 символов вокруг места"],
                 "message": "…", "fix": "…"}],
 "next": ["что сделать до отправки"]}
```

`verdict`: `fail` — есть `error`; `warn` — есть `warning` или метрика вне цели;
`pass` — нет ни того, ни другого. `score` — 0–100, доля пройденных ворот и правил.

## 8. Настройки

`data/settings.json` — значения по умолчанию; страница правит этот файл:

```json
{"version": 1, "enabled": true, "strict": true, "defaultProfile": "chat_answer",
 "profiles": {"chat_answer": true, "technical_doc": true, "business_letter": true,
              "prose_essay": true, "math_paper": true},
 "layers": {"central": true, "language": true, "composition": true, "author": true,
            "classification": true, "corpus": true, "math": true},
 "margin": 0.15, "author": "both", "pythonPath": "",
 "metricTargets": {},
 "gate": {"beforeDocument": true, "beforeAnswer": true, "minWordsForCheck": 60},
 "changedAt": "…", "changedBy": "…"}
```

Слоёв семь: к шести выбранным добавлен `math`, иначе математический профиль нечем
включать. `author` ∈ `tolstoy`, `dostoevsky`, `both` — какие образцы цитируются.
Действующие настройки = `settings.json`, отсутствующие поля — из `profiles.json`.
Чтение по mtime, без перезапуска.

## 9. Проверки

1. `node tests/tool.test.mjs` — модуль грузится фейковым контекстом, все действия
   отвечают, `check` на испорченном тексте даёт нарушения слоёв с местами, на чистом —
   `pass`.
2. `node tests/layout.test.mjs` — пути контракта существуют, `rules.json`,
   `metrics.json`, `targets.json`, `manifest.json` проходят схему, у каждого правила
   есть `ref` на существующее `R-*`.
3. `python -m pytest tests/test_metrics.py` — все метрики каталога считаются на
   контрольном тексте, значения из `tests/fixtures/metrics-sample.json` (допуск 1e-6),
   повторный запуск даёт те же числа.
4. `node tests/engine.test.mjs` — мост запускает движок, переживает падение, отвечает
   за 2 с после прогрева.
5. Установка: `dsh --profile desktop --dump-config | Select-String 'style-fingerprint'`
   даёт одну запись без `skipping`.

## 10. Журнал

- 2026-11-13: контракт создан.
- 2026-11-13: окончательные решения (профили, шаблоны, образцы, разборщик, формат слоёв,
  мост к Python, седьмой слой `math`), владение по участникам.
