/**
 * Layout test: the contract's paths exist, the data documents keep their
 * declared schema, every rule points at a real `R-*` in `law/`, and the client
 * half follows the UI rules of this project (slot, tokens, geometry, no colour
 * literals, no `!important`).
 *
 * Artifacts owned by other participants are checked when they exist and skipped
 * with a named reason when they do not, so the file is green while the corpus
 * and the reference layer are still being written.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(HERE);

const PROFILE_IDS = ['chat_answer', 'technical_doc', 'business_letter', 'prose_essay', 'math_paper'];
const LAYER_IDS = ['central', 'language', 'composition', 'author', 'classification', 'corpus', 'math'];
const GROUP_MINIMUMS = {
  syntax: 14,
  morphology: 12,
  'morphology.depth': 8,
  nominal: 4,
  discourse: 10,
  narrative: 8,
  lexis: 6,
  punctuation: 10,
  paragraph: 4,
  style: 14,
  ai: 10,
};

function path(rel) {
  return join(ROOT, ...rel.split('/'));
}

async function json(rel) {
  return JSON.parse(await readFile(path(rel), 'utf8'));
}

async function text(rel) {
  return readFile(path(rel), 'utf8');
}

/** Every `R-*` identifier the law actually carries. */
async function lawRefs() {
  let entries = [];
  try {
    entries = await readdir(path('law'));
  } catch {
    return new Set();
  }
  const refs = new Set();
  for (const entry of entries) {
    if (!entry.endsWith('.md')) continue;
    const body = await text(`law/${entry}`);
    for (const match of body.matchAll(/R-[A-ZА-ЯЁ]+-\d+/gu)) refs.add(match[0]);
  }
  return refs;
}

test('бандл: обязательные файлы и форма манифеста', async () => {
  const files = ['package.json', 'cordis.patch.yml', 'index.js', 'client.js', 'locale/en.json', 'locale/ru.json', 'icon.svg'];
  for (const file of files) {
    assert.ok(existsSync(path(file)), `нет файла ${file}`);
  }

  const manifest = await json('package.json');
  assert.equal(manifest.name, 'dsh-style-fingerprint');
  assert.equal(manifest.private, true);
  assert.equal(manifest.type, 'module');
  assert.equal(manifest.main, 'index.js');
  for (const subpath of ['.', './client', './cordis.patch.yml', './locale/*.json', './icon', './package.json']) {
    assert.ok(manifest.exports && manifest.exports[subpath], `в exports нет ${subpath}`);
  }
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
  assert.equal(manifest.dsh.client.platform, 'web');
  assert.ok(Array.isArray(manifest.dsh.client.inject));
  assert.ok(Array.isArray(manifest.files) && manifest.files.includes('client.js'));
  assert.ok(manifest.files.includes('icon.svg'));
});

test('патч профиля: ровно одна запись, имя совпадает с пакетом', async () => {
  const yaml = await text('cordis.patch.yml');
  const inserts = yaml.split(/\r?\n/u).filter((line) => /^- insert:/u.test(line));
  assert.equal(inserts.length, 1, 'в патче не ровно одна запись insert');
  assert.match(yaml, /id:\s*style-fingerprint/u);
  assert.match(yaml, /name:\s*dsh-style-fingerprint/u);
  const ids = yaml.match(/^\s*-?\s*id:\s*([\w-]+)/gmu) || [];
  assert.equal(ids.length, 1, `в патче несколько id: ${ids.join(', ')}`);
});

test('локаль: название и описание на двух языках, иконка — валидный SVG', async () => {
  const en = await json('locale/en.json');
  const ru = await json('locale/ru.json');
  for (const [name, doc] of [['en', en], ['ru', ru]]) {
    assert.ok(doc.meta && typeof doc.meta.title === 'string' && doc.meta.title.length > 2, `${name}: нет meta.title`);
    assert.ok(doc.meta && typeof doc.meta.description === 'string' && doc.meta.description.length > 20, `${name}: нет meta.description`);
  }
  assert.notEqual(en.meta.title, ru.meta.title);

  const icon = await stat(path('icon.svg'));
  assert.ok(icon.size > 100 && icon.size <= 256 * 1024, `размер иконки ${icon.size} байт`);
  const svg = await text('icon.svg');
  assert.match(svg.trim(), /^<svg/u);
  assert.match(svg, /viewBox="[^"]+"/u);
});

test('пути контракта на месте', () => {
  for (const rel of ['CONTRACT.md', 'PLAN.md', 'law/00-central.md', 'data/profiles.json', 'data/settings.json']) {
    assert.ok(existsSync(path(rel)), `нет файла контракта ${rel}`);
  }
});

test('rules.json: схема, один список, каждое правило ссылается на существующее R-*', async (t) => {
  if (!existsSync(path('data/rules.json'))) {
    t.skip('data/rules.json ещё не собран (pattern-writer)');
    return;
  }
  const doc = await json('data/rules.json');
  assert.equal(typeof doc.version, 'number');
  assert.ok(Array.isArray(doc.rules) && doc.rules.length > 0, 'нет списка rules');
  assert.equal('forbidden' in doc, false, 'верхний массив forbidden вернулся: правила снова в двух местах');

  const refs = await lawRefs();
  assert.ok(refs.size > 0, 'в law/ нет ни одного идентификатора R-*');

  const ids = new Set();
  for (const rule of doc.rules) {
    assert.match(rule.id, /^[a-z0-9]+([.-][a-z0-9]+)+$/u, `id правила не латиницей: ${rule.id}`);
    assert.ok(!ids.has(rule.id), `правило ${rule.id} повторяется`);
    ids.add(rule.id);
    assert.match(rule.ref, /^R-[A-ZА-ЯЁ]+-\d+$/u, `ref правила ${rule.id} не вида R-СЛОЙ-номер`);
    assert.ok(refs.has(rule.ref), `ref ${rule.ref} правила ${rule.id} не найден в law/`);
    assert.ok(LAYER_IDS.includes(rule.layer), `слой ${rule.layer} правила ${rule.id} неизвестен`);
    assert.ok(Array.isArray(rule.profiles) && rule.profiles.length > 0, `у правила ${rule.id} нет profiles`);
    assert.ok(['error', 'warning'].includes(rule.severity), `severity ${rule.severity} у правила ${rule.id}`);
    assert.ok(['regex', 'forbidden', 'manual', 'metric'].includes(rule.kind), `kind ${rule.kind} у правила ${rule.id}`);
    assert.equal(typeof rule.pattern, 'string', `у правила ${rule.id} нет pattern`);
    assert.doesNotThrow(() => new RegExp(rule.pattern, 'i'), `шаблон правила ${rule.id} не компилируется`);
    if (rule.kind === 'regex') {
      assert.equal(typeof rule.max, 'number', `у нормы ${rule.id} нет числового max`);
      assert.ok(['per1000', 'count', 'share', 'ratio'].includes(rule.units), `единица ${rule.units} у правила ${rule.id} неизвестна`);
    }
  }
  const latinBoundary = doc.rules.filter((rule) => /\\b/u.test(rule.pattern) && /[а-яё]/iu.test(rule.pattern));
  if (latinBoundary.length > 0) {
    t.diagnostic(`\\b рядом с кириллицей в правилах: ${latinBoundary.map((rule) => rule.id).join(', ')} — index.js переписывает границы, но лучше их не писать`);
  }
});

test('metrics.json: 100+ метрик, группы из каталога не ниже минимумов контракта', async (t) => {
  if (!existsSync(path('data/metrics.json'))) {
    t.skip('data/metrics.json ещё не собран (metricist)');
    return;
  }
  const doc = await json('data/metrics.json');
  assert.equal(typeof doc.version, 'number');
  assert.ok(Array.isArray(doc.metrics), 'нет списка metrics');
  assert.ok(doc.metrics.length >= 100, `метрик ${doc.metrics.length}, нужно не меньше 100`);
  assert.ok(doc.groups && typeof doc.groups === 'object', 'нет групп');

  const ids = new Set();
  const perGroup = new Map();
  for (const metric of doc.metrics) {
    assert.match(metric.id, /^[a-z][a-z0-9]*(\.[a-z0-9]+)+$/u, `id метрики не латиницей: ${metric.id}`);
    assert.ok(!ids.has(metric.id), `метрика ${metric.id} повторяется`);
    ids.add(metric.id);
    assert.ok(typeof metric.label === 'string' && metric.label.length > 2, `у метрики ${metric.id} нет названия`);
    assert.ok(['per1000', 'share', 'ratio', 'mean', 'count', 'words', 'entropy', 'value'].includes(metric.unit), `единица ${metric.unit} метрики ${metric.id} неизвестна`);
    assert.ok(['none', 'morphology', 'syntax'].includes(metric.needs), `needs ${metric.needs} метрики ${metric.id} неизвестен`);
    assert.ok(Object.hasOwn(doc.groups, metric.group), `группа ${metric.group} метрики ${metric.id} не описана`);
    perGroup.set(metric.group, (perGroup.get(metric.group) ?? 0) + 1);
  }
  for (const group of Object.keys(doc.groups)) {
    assert.ok(typeof doc.groups[group] === 'string' && doc.groups[group].length > 2, `группа ${group} без названия`);
  }
  for (const [group, minimum] of Object.entries(GROUP_MINIMUMS)) {
    assert.ok((perGroup.get(group) ?? 0) >= minimum, `в группе ${group} ${perGroup.get(group) ?? 0} метрик, нужно ${minimum}`);
  }
});

test('targets.json: профили, ворота 8–14, метрики из каталога, min ≤ max', async (t) => {
  if (!existsSync(path('data/targets.json'))) {
    t.skip('data/targets.json ещё не собран (metricist)');
    return;
  }
  const targets = await json('data/targets.json');
  assert.ok(Number.isFinite(targets.marginDefault) && targets.marginDefault >= 0 && targets.marginDefault <= 1);
  const catalogue = existsSync(path('data/metrics.json')) ? await json('data/metrics.json') : null;
  const known = catalogue ? new Set(catalogue.metrics.map((metric) => metric.id)) : null;

  for (const profile of Object.keys(targets.profiles)) {
    assert.ok(PROFILE_IDS.includes(profile), `профиль ${profile} неизвестен`);
  }
  for (const profile of PROFILE_IDS) {
    const gates = targets.profiles[profile] ?? {};
    const rows = Object.entries(gates);
    assert.ok(rows.length >= 8 && rows.length <= 14, `у профиля ${profile} ${rows.length} ворот, нужно 8–14`);
    for (const [id, target] of rows) {
      assert.ok(Number.isFinite(target.min) && Number.isFinite(target.max), `у ${profile}/${id} нет числовых min и max`);
      assert.ok(target.min <= target.max, `у ${profile}/${id} min больше max`);
      assert.ok(['rule', 'corpus', 'author'].includes(target.source), `источник ${target.source} у ${profile}/${id} неизвестен`);
      if (known) assert.ok(known.has(id), `метрики ${id} (${profile}) нет в каталоге data/metrics.json`);
    }
  }
});

test('корпус: манифесты и индексы в своей форме, корпуса не склеены', async (t) => {
  const hasLiterary = existsSync(path('corpus/manifest.json'));
  const hasMath = existsSync(path('corpus/math/manifest.json'));
  if (!hasLiterary && !hasMath) {
    t.skip('ни литературного, ни математического корпуса пока нет (corpus-scout)');
    return;
  }

  if (hasLiterary) {
    const manifest = await json('corpus/manifest.json');
    assert.equal(typeof manifest.version, 'number');
    assert.ok(['exemplar', undefined].includes(manifest.use), `use=${manifest.use}`);
    for (const author of ['tolstoy', 'dostoevsky']) {
      const coverage = manifest.coverage && manifest.coverage[author];
      assert.ok(coverage, `нет coverage для ${author}`);
      assert.ok(Number.isFinite(coverage.words) && coverage.words >= 0, `нет числа слов у ${author}`);
      assert.ok(Number.isFinite(coverage.works) && coverage.works >= 0, `нет числа работ у ${author}`);
    }
    assert.ok(Array.isArray(manifest.works) && manifest.works.length > 0, 'нет works');
    for (const work of manifest.works) {
      assert.ok(typeof work.id === 'string' && work.id.includes('/'), `у работы нет id вида автор/работа: ${JSON.stringify(work.id)}`);
      assert.ok(['tolstoy', 'dostoevsky'].includes(work.author), `автор работы ${work.id} неизвестен: ${work.author}`);
      assert.ok(['novel', 'novella', 'story'].includes(work.genre), `жанр ${work.genre} работы ${work.id} неизвестен`);
      assert.ok(work.year === null || Number.isFinite(work.year), `год работы ${work.id} не число и не null`);
      assert.ok(typeof work.file === 'string' && work.file.endsWith('.txt'), `у работы ${work.id} нет файла`);
      assert.match(String(work.sha256), /^[0-9a-f]{64}$/u, `sha256 работы ${work.id} не хеш`);
      assert.ok(typeof work.license === 'string' && work.license.length > 0, `у работы ${work.id} нет лицензии`);
    }
  }

  if (hasMath) {
    const manifest = await json('corpus/math/manifest.json');
    assert.equal(typeof manifest.version, 'number');
    assert.ok(typeof manifest.author === 'string' && manifest.author.length > 0, 'нет автора математического корпуса');
    assert.ok(Array.isArray(manifest.works) && manifest.works.length > 0, 'нет works в математическом манифесте');
    for (const work of manifest.works) {
      assert.ok(typeof work.id === 'string' && work.id.includes('/'), `у работы нет id вида автор/работа: ${JSON.stringify(work.id)}`);
      const author = work.author ?? manifest.author;
      assert.ok(typeof author === 'string' && author.length > 0, `у работы ${work.id} нет автора`);
      assert.ok(typeof work.file === 'string' && work.file.endsWith('.txt'), `у работы ${work.id} нет файла`);
    }
  }

  if (hasLiterary && hasMath && existsSync(path('corpus/index.json')) && existsSync(path('corpus/math/index.json'))) {
    const literary = await json('corpus/index.json');
    const math = await json('corpus/math/index.json');
    const literaryIds = new Set((literary.excerpts ?? []).map((excerpt) => excerpt.id));
    const leaked = (math.excerpts ?? []).filter((excerpt) => literaryIds.has(excerpt.id));
    assert.equal(leaked.length, 0, `математический индекс содержит литературные цитаты: ${leaked.slice(0, 3).map((excerpt) => excerpt.id).join(', ')}`);
    const literaryAuthors = new Set((literary.excerpts ?? []).map((excerpt) => excerpt.author));
    for (const excerpt of math.excerpts ?? []) {
      assert.ok(!literaryAuthors.has(excerpt.author) || (math.excerpts ?? []).every((row) => row.author === excerpt.author), 'авторы двух корпусов смешаны');
    }
  }
});

test('собранные тексты корпуса совпадают с манифестом: слова, знаки, sha256', async (t) => {
  const corpora = [
    { manifest: 'corpus/manifest.json', root: 'corpus/text' },
    { manifest: 'corpus/math/manifest.json', root: 'corpus/math/shkalikov' },
  ];
  let checked = 0;
  for (const corpus of corpora) {
    if (!existsSync(path(corpus.manifest)) || !existsSync(path(corpus.root))) continue;
    const manifest = await json(corpus.manifest);
    for (const work of manifest.works ?? []) {
      assert.ok(existsSync(path(work.file)), `файла ${work.file} нет, хотя каталог ${corpus.root} собран`);
      const body = await readFile(path(work.file), 'utf8');
      const digest = createHash('sha256').update(body, 'utf8').digest('hex');
      assert.equal(digest, work.sha256, `sha256 работы ${work.id} не совпал с файлом`);
      assert.equal(body.split(/\s+/u).filter(Boolean).length, work.words, `число слов работы ${work.id} не совпало с файлом`);
      assert.equal(body.length, work.chars, `число знаков работы ${work.id} не совпало с файлом`);
      checked += 1;
    }
  }
  if (checked === 0) t.skip('тексты не собраны: их собирают tools/fetch_corpus.py и tools/fetch_math_corpus.py');
});

test('движок метрик: единая реализация на Python, второго расчёта на JS нет', async (t) => {
  if (!existsSync(path('tools/analyze.py'))) {
    t.skip('tools/analyze.py ещё не создан (metricist)');
    return;
  }
  const engine = await text('tools/analyze.py');
  assert.ok(engine.length > 1000, 'файл движка подозрительно мал');
  assert.match(engine, /--serve/u, 'движок не объявляет режим --serve');
  assert.match(engine, /--stdin/u, 'движок не объявляет режим --stdin');
  assert.match(engine, /json/u, 'движок не говорит JSON');
});

test('страница настроек: слот из каталога, токены, геометрия, без литералов цвета', async () => {
  const client = await text('client.js');

  assert.match(client, /ctx\.slots\.inject\(\s*'settings\.plugins\.tab'/u, 'страница не зарегистрирована через ctx.slots.inject в settings.plugins.tab');
  assert.match(client, /ctx\.slots\.register\(/u);
  assert.match(client, /id:\s*'style-fingerprint'/u);
  assert.match(client, /const theme = ctx\.get\('theme'\)/u, 'тема читается не через ctx.get: имя не в inject');
  assert.match(client, /theme\.overrideTokens\('dsh-style-fingerprint'/u, 'тема не идёт через overrideTokens');
  assert.match(client, /commit\(\{\s*defaultProfile:/u, 'профиль по умолчанию не меняется со страницы');
  assert.match(client, /var\(--dsw-radius-md\)/u, 'радиус не из токена --dsw-radius-md');
  assert.match(client, /min-height:36px/u, 'строка навигации не 36px');
  assert.match(client, /padding:7px 8px/u, 'у строки навигации не padding 7px 8px');
  assert.match(client, /gap:8px/u, 'у строки навигации не gap 8px');
  assert.match(client, /width:16px/u, 'иконки не 16px');
  assert.ok(!/!important/u.test(client), 'в стилях есть !important');
  assert.ok(!/\bnode:/u.test(client), 'клиентская половина тянет node:-модули');
  assert.ok(!/process\.(env|platform)/u.test(client), 'клиентская половина читает process');

  const hex = client.match(/#[0-9a-fA-F]{3,8}\b/gu) || [];
  assert.deepEqual(hex, [], `литералы цвета #hex: ${hex.join(', ')}`);
  const functional = client.match(/\b(?:rgb|rgba|hsl|hsla)\(/gu) || [];
  assert.deepEqual(functional, [], `литералы цвета ${functional.join(', ')}`);
  const named = client.match(/:\s*(?:red|blue|green|black|white|gray|grey|orange|yellow)\b/gu) || [];
  assert.deepEqual(named, [], `именованные цвета: ${named.join(', ')}`);

  const ownTokens = new Set(['--dsw-style-fp-pass', '--dsw-style-fp-warn', '--dsw-style-fp-fail']);
  // Geometry and type tokens carry no colour; every colour reference must be an
  // alias token or one of this plugin's own layers.
  const geometryTokens = new Set(['--dsw-radius-md', '--dsw-font-mono']);
  const used = new Set([...client.matchAll(/var\((--dsw-[a-z0-9-]+)/gu)].map((match) => match[1]));
  for (const token of used) {
    assert.ok(token.startsWith('--dsw-alias-') || ownTokens.has(token) || geometryTokens.has(token), `неизвестный токен ${token}`);
  }
  for (const token of ownTokens) {
    assert.ok(used.has(token), `свой токен ${token} объявлен, но не используется`);
    assert.ok(client.includes(`'${token}'`), `свой токен ${token} не объявлен в overrideTokens`);
  }
  const pairs = client.match(/'--dsw-style-fp-[a-z]+':\s*\{[^}]*\}/gu) || [];
  assert.equal(pairs.length, 3, 'у своих токенов нет трёх пар light/dark');
  for (const pair of pairs) {
    assert.match(pair, /light:\s*'var\(--dsw-alias-[a-z0-9-]+\)'/u, 'пара light не ссылается на alias-токен');
    assert.match(pair, /dark:\s*'var\(--dsw-alias-[a-z0-9-]+\)'/u, 'пара dark не ссылается на alias-токен');
  }
});
