/**
 * Node tests of the host half: the module loads in a fake context, every tool
 * action answers, the check finds violations with places on a spoilt text and
 * passes a clean one, settings apply without a restart, and the client half
 * registers its page through `ctx.slots.inject`.
 *
 * No network, no installed profile, no Python required.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(HERE);
const plugin = await import(pathToFileURL(join(ROOT, 'index.js')).href);

/** Minimal Cordis-shaped context: records what the plugin registers. */
function fakeContext() {
  const tools = new Map();
  const skills = [];
  const sections = [];
  const effects = [];
  const ctx = {
    tools: { register: (definition) => { tools.set(definition.name, definition); return () => tools.delete(definition.name); } },
    systemPrompt: { section: (section) => { sections.push(section); return () => {}; } },
    skills: { register: (skill) => { skills.push(skill); return () => {}; } },
    effect: (fn, label) => {
      const dispose = fn();
      effects.push({ label, dispose: typeof dispose === 'function' ? dispose : null });
      return () => {};
    },
    on: () => () => {},
    // Cordis answers an optional lookup with undefined instead of throwing.
    get: (name) => ctx[name],
    // Every fake service is present at once, so the mount runs immediately.
    inject: (names, callback) => { callback(ctx); },
  };
  return { ctx, tools, skills, sections, effects };
}

/** Rules pinned by the test, so the assertion does not depend on other owners' files. */
const PINNED_RULES = {
  version: 1,
  source: 'test fixture',
  rules: [
    {
      id: 'lang.connective.empty', ref: 'R-LANG-14', layer: 'language',
      title: 'Пустая связка', severity: 'error', profiles: ['*'],
      kind: 'regex', pattern: 'таким образом|в этой связи', units: 'per1000', max: 1,
      message: 'Связка обещает вывод, которого нет', fix: 'Назвать отношение словом',
    },
    {
      id: 'lang.metatext', ref: 'R-LANG-31', layer: 'language',
      title: 'Мета-текст', severity: 'error', profiles: ['chat_answer', 'technical_doc'],
      kind: 'forbidden', pattern: 'в этом разделе',
      message: 'Обещание вместо факта', fix: 'Сказать то, что обещано',
    },
    {
      id: 'lang.intensifier.bare', ref: 'R-LANG-08', layer: 'language',
      title: 'Оценка без меры', severity: 'warning', profiles: ['*'],
      kind: 'regex', pattern: '\\bочень\\b', units: 'per1000', max: 2,
      message: 'Усилитель без меры', fix: 'Назвать число',
    },
  ],
};

/** The engine child must not outlive the test file. */
test.after(async () => {
  await plugin.closeEngine();
});

const TARGETS = { profile: 'chat_answer', margin: 0, strict: false, metrics: {} };
const NO_ENGINE = { available: false, reason: 'движок не запускался в тесте' };

test('фейковый контекст грузит плагин: инструмент, раздел промпта, скилл', () => {
  const { ctx, tools, skills, sections } = fakeContext();
  plugin.apply(ctx);

  assert.deepEqual([...tools.keys()], ['style']);
  assert.equal(tools.get('style').name, 'style');
  assert.equal(tools.get('style').parameters.required[0], 'action');
  assert.equal(tools.get('style').parameters.additionalProperties, false);

  assert.equal(sections.length, 1);
  assert.equal(sections[0].name, 'style-fingerprint');
  assert.equal(sections[0].order, 90);
  const text = sections[0].text({ scope: {} });
  assert.ok(typeof text === 'string' && text.length > 100, 'раздел промпта непустой');

  assert.equal(skills.length, 1);
  assert.equal(skills[0].name, 'style');
  assert.equal(skills[0].source, 'runtime');
  assert.ok(skills[0].content.includes('check'));
});

test('все действия инструмента отвечают разборчивым JSON', async () => {
  const { ctx, tools } = fakeContext();
  plugin.apply(ctx);
  const tool = tools.get('style');

  const calls = [
    { action: 'brief', genre: 'answer' },
    { action: 'guide', name: 'law/00-central' },
    { action: 'check', text: 'Короткая реплика без нарушений.' },
    { action: 'measure', text: 'Короткий текст для измерения.' },
    { action: 'corpus', query: 'вокзал' },
    { action: 'fingerprint' },
    { action: 'config' },
    { action: 'немало' },
  ];
  const answers = {};
  for (const call of calls) {
    const raw = await tool.execute(call, {});
    assert.equal(typeof raw, 'string', `${call.action}: результат не строка`);
    const parsed = JSON.parse(raw);
    assert.ok(parsed && typeof parsed === 'object', `${call.action}: ответ не объект`);
    answers[call.action] = parsed;
  }

  assert.equal(answers.brief.profile, 'chat_answer');
  assert.ok(Array.isArray(answers.brief.law) && answers.brief.law.length > 0);
  assert.ok('template' in answers.brief && 'checklist' in answers.brief && 'targets' in answers.brief);
  assert.deepEqual(Object.keys(answers.brief.corpora).sort(), ['literary', 'math', 'used']);
  assert.equal(answers['guide'].found, true);
  assert.ok(answers.guide.text.length > 100);
  assert.ok(Array.isArray(answers.measure.metrics) === false);
  assert.ok('engine' in answers.measure && 'catalogue' in answers.measure);
  assert.ok(Array.isArray(answers.corpus.matches));
  assert.ok('literary' in answers.fingerprint.profiles || 'chat_answer' in answers.fingerprint.profiles);
  assert.ok(answers.config.settings && answers.config.files);
  assert.ok(Array.isArray(answers['немало'].actions));
});

test('check на испорченном тексте даёт нарушения с местами, на чистом — pass', () => {
  const dirty = [
    'Таким образом, в этом разделе мы разберём очень важный вопрос.',
    'В этой связи очень полезно посмотреть на пример.',
    'Таким образом, вопрос остаётся очень открытым.',
  ].join(' ');
  const clean = 'В 1877 году Толстой закончил «Анну Каренину». Роман печатался в «Русском вестнике» с 1875 года. Критики спорили о героине, а читатели запомнили сцену на вокзале.';

  const bad = plugin.evaluate({
    text: dirty,
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false },
    rules: PINNED_RULES,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf(dirty),
    engine: NO_ENGINE,
  });

  assert.equal(bad.verdict, 'fail');
  const connective = bad.violations.find((violation) => violation.rule === 'lang.connective.empty');
  assert.ok(connective, 'нет нарушения пустой связки');
  assert.equal(connective.ref, 'R-LANG-14');
  assert.ok(connective.count >= 2);
  assert.ok(connective.places.length >= 1, 'у нарушения нет мест');
  assert.match(connective.places[0].toLowerCase(), /таким образом|в этой связи/);
  assert.ok(connective.places[0].length <= 200, 'место длиннее 200 символов');
  assert.ok(bad.violations.some((violation) => violation.rule === 'lang.metatext'));
  assert.ok(bad.violations.some((violation) => violation.rule === 'lang.intensifier.bare'));
  assert.ok(bad.violations.some((violation) => violation.rule === 'lang.intensifier.bare' && violation.value > 2));
  assert.ok(bad.next.length > 0);

  const good = plugin.evaluate({
    text: clean,
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false },
    rules: PINNED_RULES,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf(clean),
    engine: NO_ENGINE,
  });

  assert.equal(good.verdict, 'pass', `чистый текст не прошёл: ${good.violations.map((v) => v.rule).join(', ')}`);
  assert.deepEqual(good.violations, []);
});

/** A text of `totalWords` words with `markerCount` occurrences of the marker. */
function wordsWith(markerCount, totalWords, marker = 'очень') {
  const parts = [];
  for (let index = 0; index < totalWords; index += 1) parts.push(index < markerCount ? marker : 'слово');
  return `${parts.join(' ')}.`;
}

/** One frequent-word rule with the given unit and limit. */
function frequencyRule(units, max) {
  return {
    rules: [{
      id: 'lang.intensifier.bare', ref: 'R-LANG-08', layer: 'language',
      title: 'Усилитель без меры', severity: 'error', profiles: ['*'],
      kind: 'regex', pattern: 'очень', units, max,
      message: 'Усилитель без меры', fix: 'Назвать меру',
    }],
  };
}

function judgeText(text, rules) {
  return plugin.evaluate({
    text,
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false, gate: { ...plugin.__test.BUILTIN_SETTINGS.gate, minWordsForCheck: 0 } },
    rules,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf(text),
    engine: NO_ENGINE,
  });
}

test('per1000: одно вхождение в тексте на 48 слов не нарушение при max 1', () => {
  const text = wordsWith(1, 48);
  assert.equal(plugin.countsOf(text).words, 48, 'тест собрал не 48 слов');
  const result = judgeText(text, frequencyRule('per1000', 1));
  assert.deepEqual(result.violations, [], 'первое вхождение объявлено нарушением в коротком тексте');
  assert.notEqual(result.verdict, 'fail');
});

test('per1000: три вхождения на сто слов при max 2 — нарушение, плотность 30', () => {
  const text = wordsWith(3, 100);
  const result = judgeText(text, frequencyRule('per1000', 2));
  assert.equal(result.violations.length, 1, 'нарушение не найдено');
  const [violation] = result.violations;
  assert.equal(violation.count, 3);
  assert.equal(violation.units, 'per1000');
  assert.ok(Math.abs(violation.value - 30) < 1e-6, `плотность ${violation.value}, ожидалось 30`);
});

test('per1000: норма работает и на длинном тексте — три вхождения при max 2 на 1000 слов', () => {
  const text = wordsWith(3, 1000);
  const result = judgeText(text, frequencyRule('per1000', 2));
  assert.equal(result.violations.length, 1, 'третье вхождение на 1000 слов осталось без замечания');
  assert.ok(Math.abs(result.violations[0].value - 3) < 1e-6);
});

test('count с max 0: одно вхождение — нарушение даже в десяти словах', () => {
  const text = wordsWith(1, 10);
  const result = judgeText(text, frequencyRule('count', 0));
  assert.equal(result.violations.length, 1, 'абсолютная норма размылась длиной текста');
  assert.equal(result.violations[0].units, 'count');
  assert.equal(result.violations[0].severity, 'error');
});

test('цель метрики вне интервала даёт нарушение, строгий режим делает промах ошибкой', () => {
  const text = 'Предложение для проверки метрики.';
  const settings = { ...plugin.__test.BUILTIN_SETTINGS, strict: false, gate: { ...plugin.__test.BUILTIN_SETTINGS.gate, minWordsForCheck: 0 } };
  const targets = {
    profile: 'chat_answer',
    margin: 0,
    strict: false,
    metrics: { 's.len.mean': { min: 8, max: 14, effectiveMin: 8, effectiveMax: 14, source: 'rule' } },
  };
  const relaxed = plugin.evaluate({
    text,
    profile: 'chat_answer',
    settings,
    rules: { rules: [] },
    targets,
    metrics: { 's.len.mean': 31 },
    counts: plugin.countsOf(text),
    engine: { available: true, version: 'test' },
  });
  assert.equal(relaxed.verdict, 'warn');
  assert.equal(relaxed.violations[0].rule, 's.len.mean');
  assert.equal(relaxed.violations[0].severity, 'warning');

  const strict = plugin.evaluate({
    text,
    profile: 'chat_answer',
    settings: { ...settings, strict: true },
    rules: { rules: [] },
    targets: { ...targets, metrics: { ...targets.metrics } },
    metrics: { 's.len.mean': 31 },
    counts: plugin.countsOf(text),
    engine: { available: true, version: 'test' },
  });
  assert.equal(strict.verdict, 'fail');
  assert.equal(strict.violations[0].severity, 'error');
});

test('настройки читаются по mtime и меняют поведение без перезапуска', async () => {
  const root = await mkdtemp(join(tmpdir(), 'style-fingerprint-'));
  await mkdir(join(root, 'data'), { recursive: true });
  await writeFile(join(root, 'data', 'settings.json'), JSON.stringify({ version: 1, strict: false, margin: 0.3 }), 'utf8');

  const first = plugin.loadSettings(root);
  assert.equal(first.value.strict, false);
  assert.equal(first.value.margin, 0.3);

  await writeFile(join(root, 'data', 'settings.json'), JSON.stringify({ version: 1, strict: true, margin: 0.05, author: 'tolstoy' }), 'utf8');
  const second = plugin.loadSettings(root);
  assert.equal(second.value.strict, true);
  assert.equal(second.value.margin, 0.05);
  assert.equal(second.value.author, 'tolstoy');

  const written = await plugin.writeSettings({ margin: 0.2, profiles: { math_paper: false } }, 'test', root);
  assert.equal(written.ok, true);
  assert.equal(written.settings.margin, 0.2);
  assert.equal(written.settings.profiles.math_paper, false);
  const stored = JSON.parse(await readFile(join(root, 'data', 'settings.json'), 'utf8'));
  assert.equal(stored.margin, 0.2);
  assert.equal(stored.changedBy, 'test');
  assert.ok(stored.changedAt);
  assert.equal(stored.strict, true, 'правка затёрла соседние поля');

  const rejected = plugin.__test.sanitizePatch({ margin: 5, defaultProfile: 'нет такого', author: 'пушкин' });
  assert.deepEqual(rejected.clean, {});
  assert.equal(rejected.problems.length, 3);
});

test('код, формулы и цитируемые примеры не судятся правилами', () => {
  const rules = {
    rules: [{
      id: 'lang.connective.empty', ref: 'R-LANG-14', layer: 'language',
      title: 'Пустая связка', severity: 'error', profiles: ['*'],
      kind: 'regex', pattern: 'таким образом', units: 'count', max: 0,
    }],
  };
  const text = [
    'Проверим пример.',
    '```',
    'Таким образом, пример внутри кода не считается.',
    '```',
    'Инлайн `таким образом` тоже не считается.',
    '$$a = b, таким образом, c = d$$',
    '\\[ e = f, таким образом, g = h \\]',
    '> Таким образом, цитата не считается.',
  ].join('\n');
  const masked = plugin.maskForRules(text);
  assert.equal(masked.length, text.length, 'маска изменила длину текста');
  assert.ok(!/таким образом/u.test(masked), 'маркер остался в замаскированном тексте: код, формула или цитата не закрыты');

  const result = plugin.evaluate({
    text,
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false },
    rules,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf(text),
    engine: NO_ENGINE,
  });
  assert.deepEqual(
    result.violations.map((violation) => violation.rule),
    [],
    `правило сработало внутри кода, формулы или цитаты: ${result.violations.map((violation) => violation.places[0]).join(' | ')}`,
  );
  assert.equal(plugin.maskForRules(text).length, text.length, 'маска изменила длину текста');

  const outside = plugin.evaluate({
    text: 'Таким образом, вывод сделан.',
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false },
    rules,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf('Таким образом, вывод сделан.'),
    engine: NO_ENGINE,
  });
  assert.equal(outside.violations.length, 1, 'правило не сработало на обычном тексте');
});

test('check без data/rules.json отвечает причиной и подсказкой, а не выдуманными правилами', async (t) => {
  if (existsSync(join(ROOT, 'data', 'rules.json'))) {
    t.skip('data/rules.json уже собран: ветка «нет правил» не проверяется');
    return;
  }
  const { ctx, tools } = fakeContext();
  plugin.apply(ctx);
  const result = JSON.parse(await tools.get('style').execute({ action: 'check', text: 'Текст, который нечем проверить.' }, {}));
  assert.equal(typeof result.error, 'string');
  assert.match(result.error, /rules\.json/);
  assert.match(result.hint, /rules\.json/);
  assert.ok(!('violations' in result));
});

test('loadRules без файла сообщает причину и не подставляет второй набор правил', async () => {
  const root = await mkdtemp(join(tmpdir(), 'style-fingerprint-rules-'));
  const rules = plugin.loadRules(root);
  assert.equal(rules.ok, false);
  assert.match(rules.error, /rules\.json/);
  assert.deepEqual(rules.rules, []);
  assert.match(plugin.__test.RULES_HINT, /rules\.json/);
});

test('сообщённое правило не повторяется: верхний массив forbidden больше не второй источник', () => {
  const text = 'В этом разделе мы рассмотрим пример.';
  const withLegacyArray = {
    rules: [
      {
        id: 'lang.metatext', ref: 'R-LANG-31', layer: 'language',
        title: 'Мета-текст', severity: 'error', profiles: ['*'], kind: 'forbidden',
        pattern: 'в этом разделе', message: 'Обещание вместо факта', fix: 'Убрать обещание',
      },
    ],
    forbidden: [
      { id: 'lang.metatext', ref: 'R-LANG-31', layer: 'language', pattern: 'в этом разделе', message: 'дубль' },
    ],
  };
  const result = plugin.evaluate({
    text,
    profile: 'chat_answer',
    settings: { ...plugin.__test.BUILTIN_SETTINGS, strict: false },
    rules: withLegacyArray,
    targets: TARGETS,
    metrics: {},
    counts: plugin.countsOf(text),
    engine: NO_ENGINE,
  });
  assert.equal(result.violations.length, 1, 'нарушение посчитано дважды');
  assert.equal(result.violations[0].rule, 'lang.metatext');
});

test('check на собранных правилах даёт нарушения с местами', async (t) => {
  if (!existsSync(join(ROOT, 'data', 'rules.json'))) {
    t.skip('data/rules.json ещё не собран: сквозная проверка правил пропущена');
    return;
  }
  const { ctx, tools } = fakeContext();
  plugin.apply(ctx);
  const tool = tools.get('style');
  const dirty = 'Таким образом, в этом разделе мы рассмотрим вопрос. В этой связи приведём пример, который ничего не доказывает.';
  const result = JSON.parse(await tool.execute({ action: 'check', text: dirty, profile: 'chat_answer' }, {}));
  assert.ok(!result.error, `check вернул ошибку: ${result.error}`);
  assert.ok(['fail', 'warn'].includes(result.verdict), `вердикт ${result.verdict}`);
  assert.ok(Array.isArray(result.violations) && result.violations.length > 0, 'нет нарушений');
  const withPlaces = result.violations.filter((violation) => Array.isArray(violation.places) && violation.places.length > 0);
  assert.ok(withPlaces.length > 0, 'ни одного нарушения с местом');
  for (const violation of withPlaces) {
    assert.ok(violation.places[0].length <= 200);
  }
});

test('образец с нарушениями: каждое правило в отчёте ровно один раз, места разные', async (t) => {
  const corrupted = join(HERE, 'fixtures', 'sample-corrupted.txt');
  if (!existsSync(corrupted) || !existsSync(join(ROOT, 'data', 'rules.json'))) {
    t.skip('нужны tests/fixtures/sample-corrupted.txt и data/rules.json');
    return;
  }
  const text = await readFile(corrupted, 'utf8');
  const { ctx, tools } = fakeContext();
  plugin.apply(ctx);
  const result = JSON.parse(await tools.get('style').execute({ action: 'check', text, profile: 'prose_essay' }, {}));
  assert.ok(!result.error, `check вернул ошибку: ${result.error}`);
  assert.ok(result.violations.length > 0, 'образец с нарушениями прошёл без замечаний');

  const rules = result.violations.map((violation) => violation.rule);
  assert.equal(rules.length, new Set(rules).size, `правило повторяется в отчёте: ${rules.join(', ')}`);

  const places = result.violations.flatMap((violation) => (violation.places[0] === undefined ? [] : [`${violation.rule}|${violation.places[0]}`]));
  assert.equal(places.length, new Set(places).size, 'одно и то же место приписано дважды');
  assert.ok(result.violations.some((violation) => violation.severity === 'error'), 'нет ни одной ошибки');
  for (const violation of result.violations) {
    for (const place of violation.places) assert.ok(place.length <= 200, 'место длиннее 200 символов');
  }
});

test('чистый образец не получает ни одной ошибки', async (t) => {
  const cleanFile = join(HERE, 'fixtures', 'sample-clean.txt');
  if (!existsSync(cleanFile) || !existsSync(join(ROOT, 'data', 'rules.json'))) {
    t.skip('нужны tests/fixtures/sample-clean.txt и data/rules.json');
    return;
  }
  const text = await readFile(cleanFile, 'utf8');
  const { ctx, tools } = fakeContext();
  plugin.apply(ctx);
  const result = JSON.parse(await tools.get('style').execute({ action: 'check', text, profile: 'prose_essay' }, {}));
  assert.ok(!result.error, `check вернул ошибку: ${result.error}`);
  const errors = result.violations.filter((violation) => violation.severity === 'error');
  assert.deepEqual(
    errors.map((violation) => violation.rule),
    [],
    `чистый образец получил ошибки: ${errors.map((violation) => `${violation.rule}: ${violation.places[0] || ''}`).join(' | ')}`,
  );
  assert.ok(['pass', 'warn'].includes(result.verdict), `вердикт ${result.verdict}`);
  if (result.violations.length > 0) {
    t.diagnostic(`предупреждения на чистом образце: ${result.violations.map((violation) => violation.rule).join(', ')}`);
  }
});

test('маршрут страницы отдаёт состояние, проверяет текст и отвечает 404 на чужой путь', async () => {
  const { ctx, tools } = fakeContext();
  const routes = [];
  ctx.get = (name) => (name === 'webServer'
    ? { register: (route) => { routes.push(route); return () => {}; } }
    : undefined);
  plugin.apply(ctx);

  assert.equal(routes.length, 1, 'страница не зарегистрировала маршрут');
  assert.equal(routes[0].kind, 'prefix');
  assert.equal(routes[0].path, '/plugins/style-fingerprint');

  const state = await routes[0].resource(new Request('http://localhost/plugins/style-fingerprint/state'));
  assert.equal(state.status, 200);
  const body = await state.json();
  assert.ok(body.settings && typeof body.settings === 'object');
  assert.equal(body.profiles.length, 5);
  assert.equal(body.layers.length, 7);
  assert.ok(body.corpora && 'literary' in body.corpora && 'math' in body.corpora);
  assert.equal(body.page.available, true);

  const response = await routes[0].resource(new Request('http://localhost/plugins/style-fingerprint/check', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: 'Короткий текст без нарушений.', profile: 'chat_answer' }),
  }));
  assert.equal(response.status, 200);
  const checked = await response.json();
  assert.ok(checked.verdict !== undefined || typeof checked.error === 'string', 'проверка через маршрут ничего не вернула');

  const unknown = await routes[0].resource(new Request('http://localhost/plugins/style-fingerprint/nope'));
  assert.equal(unknown.status, 404);

  const config = JSON.parse(await tools.get('style').execute({ action: 'config' }, {}));
  assert.equal(config.page.available, true, 'config не видит поднятый маршрут страницы');
});

test('клиентская половина регистрирует страницу через slots.inject', async () => {
  const injected = [];
  const registered = [];
  const overrides = [];
  const appended = [];

  globalThis.window = { __ModuleLoader__: { load: (definition) => { globalThis.__styleDefinition = definition; } } };
  globalThis.document = {
    createElement: () => ({ setAttribute() {}, remove() {}, textContent: '' }),
    head: { append: (node) => appended.push(node) },
  };

  await import(pathToFileURL(join(ROOT, 'client.js')).href);
  const definition = globalThis.__styleDefinition;
  assert.equal(definition.id, 'dsh-style-fingerprint');

  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...(props || {}), children } }),
    useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useRef: (value) => ({ current: value }),
  };
  const module = definition.factory((id) => {
    if (id === 'react') return react;
    if (id === '@deepseek-ai/dsh-client-ui-primitives') return {};
    throw new Error(`неожиданный импорт клиента: ${id}`);
  });

  const clientCtx = {
    effect: (fn) => { const dispose = fn(); return typeof dispose === 'function' ? dispose : () => {}; },
    slots: {
      inject: (key, callback) => { injected.push(key); return callback(); },
      register: (options, component) => { registered.push({ options, component }); return () => {}; },
    },
    locale: { register: () => {}, bind: () => (key) => key },
    theme: { overrideTokens: (source, tokens) => { overrides.push({ source, tokens }); return () => {}; } },
  };
  module.apply(clientCtx);

  assert.deepEqual(injected, ['settings.plugins.tab'], 'страница зарегистрирована без slots.inject');
  assert.equal(registered.length, 1);
  assert.equal(registered[0].options.name, 'settings.plugins.tab');
  assert.equal(registered[0].options.id, 'style-fingerprint');
  assert.equal(typeof registered[0].options.order, 'number');
  assert.equal(typeof registered[0].component, 'function');

  assert.equal(overrides.length, 1);
  const tokens = overrides[0].tokens;
  assert.ok(Object.keys(tokens).length > 0);
  for (const [name, value] of Object.entries(tokens)) {
    assert.equal(typeof value.light, 'string', `${name}: нет пары light`);
    assert.equal(typeof value.dark, 'string', `${name}: нет пары dark`);
  }
  assert.ok(appended.length >= 1, 'стили не вставлены');

  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.__styleDefinition;
});
