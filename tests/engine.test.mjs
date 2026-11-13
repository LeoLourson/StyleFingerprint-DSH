/**
 * Bridge test: the long-lived `tools/analyze.py --serve` child answers, keeps
 * answering within two seconds after warm-up, survives its own crash, reports a
 * missing engine as a reason instead of throwing through the tool, and picks the
 * engine up when the file appears without a plugin reload.
 *
 * A stub Python process stands in for the real engine where the protocol, not
 * the metrics, is under test.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(HERE);
const plugin = await import(pathToFileURL(join(ROOT, 'index.js')).href);

const engineScript = join(ROOT, 'tools', 'analyze.py');
const python = plugin.resolvePython({});
const pythonAvailable = python.source.startsWith('каталог') ? existsSync(python.command) : null;

/** Minimal engine that speaks the documented protocol, ASCII only. */
const STUB_ENGINE = [
  'import json',
  'import sys',
  '',
  'def answer(payload):',
  '    sys.stdout.write(json.dumps(payload) + "\\n")',
  '    sys.stdout.flush()',
  '',
  'answer({"ready": True, "metrics": 0, "version": "stub"})',
  'for line in sys.stdin:',
  '    line = line.strip()',
  '    if not line:',
  '        continue',
  '    request = json.loads(line)',
  '    text = request.get("text", "")',
  '    if request.get("action") == "measure":',
  '        words = len(text.split())',
  '        answer({"id": request.get("id"), "ok": True, "result": {"metrics": {"words": words}, "counts": {"words": words}, "unavailable": {}}})',
  '    else:',
  '        answer({"id": request.get("id"), "ok": True, "result": {"action": request.get("action")}})',
  '',
].join('\n');

const LONG_TEXT = 'Городской сад закрыли на ремонт в начале мая, и жители соседних домов потеряли единственную площадку для прогулок. '.repeat(6);

function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label}: нет ответа за ${ms} мс`)), ms);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

test('мост поднимает движок и после прогрева отвечает быстрее двух секунд', async (t) => {
  if (!existsSync(engineScript)) {
    t.skip('движок tools/analyze.py ещё не создан (metricist): мост проверяется на заглушке в следующем тесте');
    return;
  }
  const bridge = new plugin.EngineBridge({
    python: python.command,
    pythonSource: python.source,
    script: engineScript,
    cwd: ROOT,
    timeoutMs: 30000,
  });
  t.after(async () => { await bridge.close(); });

  const first = await withTimeout(bridge.request('measure', { text: LONG_TEXT, options: { language: 'ru' } }), 40000, 'первый measure');
  assert.ok(first && typeof first.metrics === 'object' && first.metrics !== null, 'нет объекта метрик');
  const metricCount = Object.keys(first.metrics).length;
  assert.ok(metricCount >= 50, `движок вернул ${metricCount} метрик: ожидались десятки`);
  assert.ok(first.counts && first.counts.words > 40, `счётчик слов ${JSON.stringify(first.counts)}`);
  assert.equal(bridge.status().available, true);

  const started = Date.now();
  const second = await withTimeout(bridge.request('measure', { text: LONG_TEXT, options: { language: 'ru' } }), 5000, 'второй measure');
  const elapsed = Date.now() - started;
  assert.ok(second && typeof second.metrics === 'object');
  assert.ok(elapsed < 2000, `ответ после прогрева занял ${elapsed} мс, ожидалось меньше 2000`);

  const again = await bridge.request('measure', { text: LONG_TEXT, options: { language: 'ru' } });
  assert.deepEqual(again.metrics, second.metrics, 'повторный замер дал другие числа');
});

test('мост переживает отсутствие движка и его появление без перезапуска плагина', async (t) => {
  if (pythonAvailable === false) {
    t.skip('нет интерпретатора Python: заглушку движка нечем запустить');
    return;
  }
  const dir = await mkdtemp(join(tmpdir(), 'style-fingerprint-engine-'));
  const script = join(dir, 'engine.py');
  const bridge = new plugin.EngineBridge({ python: python.command, script, cwd: dir, timeoutMs: 20000 });
  t.after(async () => { await bridge.close(); });

  await assert.rejects(
    () => bridge.request('measure', { text: 'короткий текст' }),
    /не найден/u,
    'отсутствие движка не дало понятной причины',
  );
  assert.equal(bridge.status().available, false);

  await writeFile(script, STUB_ENGINE, 'utf8');
  const result = await withTimeout(bridge.request('measure', { text: 'один два три' }), 30000, 'запуск появившегося движка');
  assert.equal(result.metrics.words, 3, 'движок, появившийся позже, не подхвачен');
  assert.equal(bridge.status().available, true);
});

test('мост переживает падение движка и поднимает его снова', async (t) => {
  if (pythonAvailable === false) {
    t.skip('нет интерпретатора Python: заглушку движка нечем запустить');
    return;
  }
  const dir = await mkdtemp(join(tmpdir(), 'style-fingerprint-crash-'));
  const script = join(dir, 'engine.py');
  await writeFile(script, STUB_ENGINE, 'utf8');
  const bridge = new plugin.EngineBridge({ python: python.command, script, cwd: dir, timeoutMs: 20000 });
  t.after(async () => { await bridge.close(); });

  const before = await withTimeout(bridge.request('measure', { text: 'раз два' }), 30000, 'первый запуск');
  assert.equal(before.metrics.words, 2);

  bridge.process.kill();
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(bridge.status().available, false, 'после падения движок считается живым');

  const after = await withTimeout(bridge.request('measure', { text: 'раз два три четыре' }), 30000, 'запрос после падения');
  assert.equal(after.metrics.words, 4, 'движок не поднялся после падения');
  assert.ok(bridge.status().restarts >= 1, `перезапусков ${bridge.status().restarts}`);
});

test('без рабочего Python мост отвечает причиной, а не исключением наружу', async (t) => {
  const bridge = new plugin.EngineBridge({
    python: process.platform === 'win32' ? 'no-such-python-style-fingerprint' : 'no-such-python',
    script: engineScript,
    cwd: ROOT,
    timeoutMs: 5000,
  });
  t.after(async () => { await bridge.close(); });

  await assert.rejects(
    () => withTimeout(bridge.request('measure', { text: 'текст' }), 20000, 'запуск без Python'),
    /движок/u,
  );
  const status = bridge.status();
  assert.equal(status.available, false);
  assert.match(String(status.reason), /движок/u);
});
