/**
 * Host half of the `dsh-style-fingerprint` bundle.
 *
 * Registers the `style` tool (brief, guide, check, measure, corpus, fingerprint,
 * config), one short system-prompt section built from `law/00-central.md`, the
 * runtime skill `style`, the bridge to the Python metric engine
 * (`tools/analyze.py --serve`) and a same-origin JSON route the settings page
 * reads and writes through.
 *
 * Everything here must survive a missing artifact: `data/`, `law/`, `templates/`,
 * `corpus/` and `tools/` are owned by other participants and may not exist yet.
 * A missing file produces a named reason in the action result, never a throw.
 */
import { spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync, promises as fsp } from 'node:fs';
import { basename, dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'dsh-style-fingerprint';
export const inject = ['tools', 'systemPrompt'];

const ROOT = dirname(fileURLToPath(import.meta.url));
const GATEWAY_PATH = '/plugins/style-fingerprint';
const ENGINE_SCRIPT = join(ROOT, 'tools', 'analyze.py');
const ENGINE_TIMEOUT_MS = 20000;
const ENGINE_MAX_RESTARTS = 2;
const PROFILE_IDS = ['chat_answer', 'technical_doc', 'business_letter', 'prose_essay', 'math_paper'];
const LAYER_IDS = ['central', 'language', 'composition', 'author', 'classification', 'corpus', 'math'];
const AUTHORS = ['tolstoy', 'dostoevsky', 'both'];

/** Layer id to its law files; the first file supplies the layer title. */
const LAYER_FILES = {
  central: ['law/00-central.md'],
  language: ['law/30-language.md', 'law/10-write-for-outsider.md'],
  composition: ['law/20-composition.md'],
  author: ['law/40-author-voice.md'],
  corpus: ['law/45-russian-prose.md'],
  classification: ['law/50-classification.md'],
  math: ['law/55-math-article.md'],
};

const LAYER_ALIASES = {
  central: '00-central',
  language: '30-language',
  composition: '20-composition',
  author: '40-author-voice',
  corpus: '45-russian-prose',
  classification: '50-classification',
  math: '55-math-article',
};

/** Values used when `data/settings.json` and `data/profiles.json` are absent. */
const BUILTIN_SETTINGS = {
  version: 1,
  enabled: true,
  strict: true,
  defaultProfile: 'chat_answer',
  profiles: { chat_answer: true, technical_doc: true, business_letter: true, prose_essay: true, math_paper: true },
  layers: { central: true, language: true, composition: true, author: true, classification: true, corpus: true, math: true },
  margin: 0.15,
  author: 'both',
  pythonPath: '',
  metricTargets: {},
  gate: { minWordsForCheck: 60 },
};

/**
 * Rules are not kept in this file: `data/rules.json` is the single source.
 * A check without that file answers a named reason instead of judging by a
 * second, silently diverging rule table.
 */
const RULES_HINT = 'Собрать data/rules.json (владелец pattern-writer) или проверить текст вручную по чек-листу жанра';

/** Two separate corpora, never merged: fiction and mathematical prose differ in genre. */
const CORPUS_KINDS = {
  literary: { index: 'corpus/index.json', manifest: 'corpus/manifest.json', title: 'литературный' },
  math: { index: 'corpus/math/index.json', manifest: 'corpus/math/manifest.json', title: 'математический' },
};

/** The profile picks its corpus; only `math_paper` reads the mathematical one. */
export function corpusKindFor(profile) {
  return profile === 'math_paper' ? 'math' : 'literary';
}

const FALLBACK_SECTION = [
  '## Стиль: текст для человека вне контекста',
  'Мир не знает ни нашей задачи, ни наших файлов, ни вчерашнего разговора: всё нужное стоит в тексте.',
  'Перед документом вызови `style` с действием `brief` (жанр и читатель), возьми шаблон и чек-лист жанра.',
  'Перед выдачей ответа вызови `style` с действием `check`; всё с пометкой `error` исправляется до отправки.',
  'Первое появление термина объясняется словами; у утверждения есть основание; квантор и связка называют отношение, а не намекают на него.',
].join('\n');

// #region utils

function messageOf(error) {
  if (error instanceof Error) return error.message;
  return String(error ?? 'неизвестная ошибка');
}

function relPath(path) {
  return relative(ROOT, path).split('\\').join('/');
}

function clampText(text, limit) {
  const clean = String(text).replace(/\s+/gu, ' ').trim();
  return clean.length <= limit ? clean : `${clean.slice(0, limit - 1)}…`;
}

/** Word, sentence and paragraph counts of a text; the engine may return its own. */
export function countsOf(text) {
  const source = String(text ?? '');
  const words = (source.match(/[0-9A-Za-zА-Яа-яЁё]+(?:[-'’][0-9A-Za-zА-Яа-яЁё]+)*/gu) || []).length;
  const sentences = (source.match(/[.!?…]+(?=\s|$)/gu) || []).length;
  const paragraphs = source.split(/\n\s*\n/gu).filter((part) => part.trim() !== '').length;
  return { words, sentences, paragraphs };
}

/** Up to 200 characters around one match, with an ellipsis on a cut side. */
export function contextAround(text, start, end, radius = 60) {
  const from = Math.max(0, start - radius);
  const to = Math.min(text.length, end + radius);
  const body = text.slice(from, to).replace(/\s+/gu, ' ').trim();
  const head = from > 0 ? '… ' : '';
  const tail = to < text.length ? ' …' : '';
  const limit = Math.max(0, 200 - head.length - tail.length);
  return `${head}${body.slice(0, limit)}${tail}`;
}

/** Split an optional YAML front matter from the body. */
export function parseFrontMatter(text) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/u.exec(String(text ?? ''));
  if (!match) return { meta: {}, body: String(text ?? '') };
  const meta = {};
  for (const line of match[1].split(/\r?\n/u)) {
    const index = line.indexOf(':');
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    value = value.replace(/^['"]|['"]$/gu, '');
    meta[key] = value;
  }
  return { meta, body: String(text ?? '').slice(match[0].length) };
}

function firstHeading(body) {
  const match = /^#{1,3}\s+(.+)$/mu.exec(body);
  return match ? match[1].trim() : '';
}

/** Body of one `## `-level section, without the heading line. */
function sectionBody(body, matcher) {
  const lines = String(body).split(/\r?\n/u);
  let startIndex = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^##\s+/u.test(line) && matcher.test(line)) {
      startIndex = index + 1;
      break;
    }
  }
  if (startIndex < 0) return '';
  const collected = [];
  for (let index = startIndex; index < lines.length; index += 1) {
    if (/^##\s+/u.test(lines[index])) break;
    collected.push(lines[index]);
  }
  return collected.join('\n').trim();
}

// #endregion
// #region cached reads

/** path → { mtimeMs, size, text }; re-read only when the file changed. */
const textCache = new Map();
const jsonCache = new Map();

/** Read a UTF-8 file, reusing the bytes while mtime and size are unchanged. */
export function readText(path) {
  let stat;
  try {
    stat = statSync(path);
  } catch {
    return { ok: false, path, error: `файл не найден: ${relPath(path)}` };
  }
  const cached = textCache.get(path);
  if (cached && cached.mtimeMs === stat.mtimeMs && cached.size === stat.size) {
    return { ok: true, path, text: cached.text, mtimeMs: stat.mtimeMs, size: stat.size };
  }
  try {
    const text = readFileSync(path, 'utf8').replace(/^\uFEFF/u, '');
    textCache.set(path, { mtimeMs: stat.mtimeMs, size: stat.size, text });
    return { ok: true, path, text, mtimeMs: stat.mtimeMs, size: stat.size };
  } catch (error) {
    return { ok: false, path, error: `файл не читается: ${relPath(path)}: ${messageOf(error)}` };
  }
}

/** Read and parse JSON with the same mtime cache, plus a parse-error reason. */
export function readJson(path) {
  const read = readText(path);
  if (!read.ok) return read;
  const cached = jsonCache.get(path);
  // mtime alone repeats across two writes inside one filesystem tick, and the
  // settings page writes on every edit: the size keeps the cache honest.
  if (cached && cached.mtimeMs === read.mtimeMs && cached.size === read.size) return { ...cached, path };
  try {
    const value = JSON.parse(read.text);
    const entry = { ok: true, path, value, mtimeMs: read.mtimeMs, size: read.size };
    jsonCache.set(path, entry);
    return entry;
  } catch (error) {
    return { ok: false, path, error: `JSON сломан (${relPath(path)}): ${messageOf(error)}` };
  }
}

/** Drop cached bytes after a write so the next read sees the new file. */
export function invalidate(path) {
  textCache.delete(path);
  jsonCache.delete(path);
}

// #endregion
// #region settings and data documents

const SETTINGS_FILE = join(ROOT, 'data', 'settings.json');
const PROFILES_FILE = join(ROOT, 'data', 'profiles.json');
const RULES_FILE = join(ROOT, 'data', 'rules.json');
const TARGETS_FILE = join(ROOT, 'data', 'targets.json');
const METRICS_FILE = join(ROOT, 'data', 'metrics.json');
const CORPUS_INDEX_FILE = join(ROOT, 'corpus', 'index.json');
const CORPUS_MANIFEST_FILE = join(ROOT, 'corpus', 'manifest.json');

function layerMapFrom(value) {
  const map = { ...BUILTIN_SETTINGS.layers };
  if (Array.isArray(value)) {
    for (const layer of LAYER_IDS) map[layer] = value.includes(layer);
    return map;
  }
  if (value && typeof value === 'object') {
    for (const layer of LAYER_IDS) if (typeof value[layer] === 'boolean') map[layer] = value[layer];
  }
  return map;
}

/**
 * Effective settings: `data/settings.json`, then the `defaults` of
 * `data/profiles.json`, then the built-in values. Read on every call through
 * the mtime cache, so a page edit applies without a restart.
 */
export function loadSettings(root = ROOT) {
  const settingsFile = join(root, 'data', 'settings.json');
  const profilesFile = join(root, 'data', 'profiles.json');
  const settingsRead = readJson(settingsFile);
  const profilesRead = readJson(profilesFile);
  const raw = settingsRead.ok && settingsRead.value && typeof settingsRead.value === 'object' ? settingsRead.value : {};
  const profileDefaults = profilesRead.ok && profilesRead.value && profilesRead.value.defaults && typeof profilesRead.value.defaults === 'object'
    ? profilesRead.value.defaults
    : {};
  const merged = { ...BUILTIN_SETTINGS, ...profileDefaults, ...raw };
  merged.profiles = { ...BUILTIN_SETTINGS.profiles };
  for (const profile of PROFILE_IDS) {
    if (profileDefaults.profiles && typeof profileDefaults.profiles[profile] === 'boolean') merged.profiles[profile] = profileDefaults.profiles[profile];
    if (raw.profiles && typeof raw.profiles[profile] === 'boolean') merged.profiles[profile] = raw.profiles[profile];
  }
  merged.layers = layerMapFrom(raw.layers !== undefined ? raw.layers : profileDefaults.layers);
  // Only `minWordsForCheck` reaches the code, so only it survives the merge: a
  // settings file written by an older version may still carry the two switches
  // that promised to turn the gate off and never did.
  const gate = { ...BUILTIN_SETTINGS.gate, ...(profileDefaults.gate || {}), ...(raw.gate || {}) };
  merged.gate = { minWordsForCheck: gate.minWordsForCheck };
  merged.metricTargets = raw.metricTargets && typeof raw.metricTargets === 'object' ? raw.metricTargets : {};
  merged.margin = Number.isFinite(merged.margin) ? Math.min(1, Math.max(0, merged.margin)) : BUILTIN_SETTINGS.margin;
  merged.strict = merged.strict !== false;
  merged.enabled = merged.enabled !== false;
  if (!AUTHORS.includes(merged.author)) merged.author = 'both';
  if (!PROFILE_IDS.includes(merged.defaultProfile)) merged.defaultProfile = BUILTIN_SETTINGS.defaultProfile;
  return {
    value: merged,
    source: settingsRead.ok ? 'data/settings.json' : 'встроенные значения',
    present: settingsRead.ok,
    error: settingsRead.ok ? null : settingsRead.error,
    mtimeMs: settingsRead.ok ? settingsRead.mtimeMs : null,
    profilesDoc: profilesRead.ok ? profilesRead.value : null,
  };
}

export function loadProfiles(root = ROOT) {
  return readJson(join(root, 'data', 'profiles.json'));
}

export function loadCatalog(root = ROOT) {
  const read = readJson(join(root, 'data', 'metrics.json'));
  if (!read.ok) return { ok: false, error: read.error, catalogue: { version: 0, metrics: [], groups: {} } };
  const value = read.value && typeof read.value === 'object' ? read.value : {};
  return {
    ok: true,
    mtimeMs: read.mtimeMs,
    catalogue: {
      version: value.version ?? 0,
      metrics: Array.isArray(value.metrics) ? value.metrics : [],
      groups: value.groups && typeof value.groups === 'object' ? value.groups : {},
    },
  };
}

export function loadRules(root = ROOT) {
  const read = readJson(join(root, 'data', 'rules.json'));
  if (!read.ok) {
    return {
      ok: false,
      error: read.error,
      source: 'нет файла data/rules.json',
      version: 0,
      built: null,
      rules: [],
    };
  }
  const value = read.value && typeof read.value === 'object' ? read.value : {};
  return {
    ok: true,
    error: null,
    version: value.version ?? 0,
    built: value.built ?? null,
    source: 'data/rules.json',
    rules: Array.isArray(value.rules) ? value.rules : [],
  };
}

export function loadTargets(root = ROOT) {
  const read = readJson(join(root, 'data', 'targets.json'));
  if (!read.ok) return { ok: false, error: read.error, marginDefault: BUILTIN_SETTINGS.margin, profiles: {} };
  const value = read.value && typeof read.value === 'object' ? read.value : {};
  return {
    ok: true,
    marginDefault: Number.isFinite(value.marginDefault) ? value.marginDefault : BUILTIN_SETTINGS.margin,
    updated: value.updated ?? null,
    profiles: value.profiles && typeof value.profiles === 'object' ? value.profiles : {},
  };
}

/** One corpus index by kind; kinds never mix. */
export function loadCorpusIndex(kind = 'literary', root = ROOT) {
  const spec = CORPUS_KINDS[kind] || CORPUS_KINDS.literary;
  const read = readJson(join(root, ...spec.index.split('/')));
  if (!read.ok) return { ok: false, kind, error: read.error, built: null, excerpts: [], path: spec.index };
  const value = read.value && typeof read.value === 'object' ? read.value : {};
  return {
    ok: true,
    kind,
    built: value.built ?? null,
    excerpts: Array.isArray(value.excerpts) ? value.excerpts : [],
    path: spec.index,
  };
}

/** One corpus manifest by kind. */
export function loadCorpusManifest(kind = 'literary', root = ROOT) {
  const spec = CORPUS_KINDS[kind] || CORPUS_KINDS.literary;
  const read = readJson(join(root, ...spec.manifest.split('/')));
  if (!read.ok) return { ok: false, kind, error: read.error, path: spec.manifest };
  return { ok: true, kind, value: read.value, path: spec.manifest };
}

/** Profile id from a genre word, a profile id, or the default profile. */
function resolveProfileId(requested, settings) {
  const wanted = String(requested ?? '').trim();
  if (PROFILE_IDS.includes(wanted)) return { id: wanted, warning: null };
  if (wanted !== '') {
    const profiles = loadProfiles();
    const map = profiles.ok && profiles.value && profiles.value.genreMap ? profiles.value.genreMap : {};
    const mapped = map[wanted.toLowerCase()];
    if (PROFILE_IDS.includes(mapped)) return { id: mapped, warning: null };
    return { id: settings.defaultProfile, warning: `Жанр или профиль «${wanted}» неизвестен: взят профиль по умолчанию ${settings.defaultProfile}` };
  }
  return { id: settings.defaultProfile, warning: null };
}

/** Targets of one profile with the margin applied (none in strict mode). */
function targetsFor(profile, settings, targetsDoc) {
  const raw = (targetsDoc.profiles && targetsDoc.profiles[profile]) || {};
  const overrides = settings.metricTargets && typeof settings.metricTargets === 'object' ? settings.metricTargets : {};
  const margin = settings.strict ? 0 : (Number.isFinite(settings.margin) ? settings.margin : targetsDoc.marginDefault ?? 0);
  const metrics = {};
  for (const [id, target] of Object.entries(raw)) {
    if (!target || typeof target !== 'object') continue;
    const override = overrides[id] && typeof overrides[id] === 'object' ? overrides[id] : null;
    const min = Number.isFinite(override?.min) ? override.min : target.min;
    const max = Number.isFinite(override?.max) ? override.max : target.max;
    if (!Number.isFinite(min) || !Number.isFinite(max)) continue;
    const effectiveMin = min === 0 ? 0 : min * (1 - margin);
    const effectiveMax = max === 0 ? 0 : max * (1 + margin);
    metrics[id] = {
      min,
      max,
      effectiveMin: Number(effectiveMin.toFixed(6)),
      effectiveMax: Number(effectiveMax.toFixed(6)),
      source: target.source ?? 'rule',
      overridden: Boolean(override),
    };
  }
  return { profile, margin, strict: settings.strict, metrics };
}

// #endregion
// #region engine bridge

/** One long-lived `tools/analyze.py --serve` child with id-matched JSON lines. */
export class EngineBridge {
  constructor(options = {}) {
    this.python = options.python || 'python';
    this.pythonSource = options.pythonSource || 'PATH';
    this.script = options.script || join(ROOT, 'tools', 'analyze.py');
    this.cwd = options.cwd || ROOT;
    this.timeoutMs = options.timeoutMs ?? ENGINE_TIMEOUT_MS;
    this.maxRestarts = options.maxRestarts ?? ENGINE_MAX_RESTARTS;
    this.starts = 0;
    this.ready = false;
    this.closed = false;
    this.fatal = false;
    this.version = null;
    this.metricCount = 0;
    this.lastError = null;
    this.pending = new Map();
    this.counter = 0;
    this.stdoutBuffer = '';
    this.stderrText = '';
    this.process = null;
    this.readyWaiter = null;
    this.startPromise = null;
    this.restarts = 0;
  }

  get state() {
    if (this.closed) return 'closed';
    if (this.ready) return 'ready';
    if (this.fatal) return 'failed';
    return 'idle';
  }

  /** Spawn the engine and wait for its first `{"ready":true}` line. */
  async start() {
    if (this.closed) throw new Error('движок остановлен вместе с плагином');
    if (this.ready && this.process) return this;
    if (this.startPromise) return this.startPromise;
    if (this.fatal) throw new Error(this.lastError || 'движок недоступен');
    this.startPromise = this.#spawnAndWait().finally(() => { this.startPromise = null; });
    return this.startPromise;
  }

  async #spawnAndWait() {
    if (!existsSync(this.script)) {
      // Not fatal: the engine is written by another participant and may appear
      // later. The next request starts it without reloading the plugin.
      this.lastError = `движок не найден: ${relPath(this.script)}`;
      throw new Error(this.lastError);
    }
    if (this.starts > this.maxRestarts) {
      this.fatal = true;
      this.lastError = `движок не поднялся за ${this.maxRestarts + 1} попытку: ${this.lastError || 'причина неизвестна'}`;
      throw new Error(this.lastError);
    }
    this.starts += 1;
    if (this.starts > 1) this.restarts += 1;
    this.stdoutBuffer = '';
    this.stderrText = '';
    this.ready = false;
    let proc;
    try {
      proc = spawn(this.python, [this.script, '--serve'], {
        cwd: this.cwd,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
    } catch (error) {
      this.lastError = `движок не запустился (${this.python}): ${messageOf(error)}`;
      throw new Error(this.lastError);
    }
    this.process = proc;
    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', (chunk) => this.#onStdout(chunk));
    proc.stderr.on('data', (chunk) => { this.stderrText = `${this.stderrText}${chunk}`.slice(-2000); });
    proc.on('error', (error) => this.#fail(`движок не запустился (${this.python}): ${messageOf(error)}`));
    proc.on('exit', (code) => {
      if (this.process !== proc) return;
      const tail = this.stderrText.trim().split(/\r?\n/u).pop() || '';
      this.#fail(`движок завершился, код ${code}${tail ? `: ${tail}` : ''}`);
    });

    const ready = new Promise((resolveReady, rejectReady) => {
      const timer = setTimeout(() => {
        rejectReady(new Error(`движок не прислал ready за ${Math.round(this.timeoutMs / 1000)} с`));
      }, this.timeoutMs);
      this.readyWaiter = {
        resolve: (value) => { clearTimeout(timer); resolveReady(value); },
        reject: (error) => { clearTimeout(timer); rejectReady(error); },
      };
    });
    try {
      await ready;
    } catch (error) {
      this.lastError = messageOf(error);
      this.fatal = this.starts > this.maxRestarts;
      this.#kill();
      throw new Error(this.lastError);
    }
    this.ready = true;
    this.fatal = false;
    return this;
  }

  #onStdout(chunk) {
    this.stdoutBuffer += chunk;
    let index = this.stdoutBuffer.indexOf('\n');
    while (index >= 0) {
      const line = this.stdoutBuffer.slice(0, index).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (line !== '') this.#onLine(line);
      index = this.stdoutBuffer.indexOf('\n');
    }
  }

  #onLine(line) {
    let payload;
    try {
      payload = JSON.parse(line);
    } catch {
      return; // a banner line from an import warning is not an answer
    }
    if (payload && payload.ready === true && this.readyWaiter) {
      this.version = payload.version ?? null;
      this.metricCount = Number(payload.metrics) || 0;
      const waiter = this.readyWaiter;
      this.readyWaiter = null;
      waiter.resolve(true);
      return;
    }
    const id = payload && payload.id !== undefined ? String(payload.id) : null;
    if (id === null) return;
    const waiter = this.pending.get(id);
    if (!waiter) return;
    this.pending.delete(id);
    clearTimeout(waiter.timer);
    if (payload.ok === false) waiter.reject(new Error(payload.error ? String(payload.error) : 'движок вернул ошибку'));
    else waiter.resolve(payload.result ?? payload);
  }

  #fail(reason) {
    if (this.closing) return;
    this.ready = false;
    this.lastError = reason;
    this.fatal = this.starts > this.maxRestarts;
    if (this.readyWaiter) {
      const waiter = this.readyWaiter;
      this.readyWaiter = null;
      waiter.reject(new Error(reason));
    }
    for (const [id, waiter] of this.pending) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error(reason));
      this.pending.delete(id);
    }
    this.#kill();
  }

  #kill() {
    const proc = this.process;
    this.process = null;
    this.ready = false;
    if (!proc) return;
    try {
      proc.kill();
    } catch {
      // the process may already be gone; nothing else to do
    }
  }

  #send(action, payload) {
    const proc = this.process;
    if (!proc || !this.ready) return Promise.reject(new Error('движок не запущен'));
    this.counter += 1;
    const id = String(this.counter);
    const request = JSON.stringify({ id, action, text: payload.text, options: payload.options });
    return new Promise((resolveAnswer, rejectAnswer) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectAnswer(new Error(`ответа движка нет за ${Math.round(this.timeoutMs / 1000)} с`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve: resolveAnswer, reject: rejectAnswer, timer });
      try {
        proc.stdin.write(`${request}\n`);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        rejectAnswer(new Error(`запрос движку не ушёл: ${messageOf(error)}`));
      }
    });
  }

  /**
   * One engine call with the lifecycle of the contract: start on demand, one
   * retry while the restart budget lasts, and a named failure afterwards.
   */
  async request(action, payload = {}) {
    if (this.closed) throw new Error('движок остановлен вместе с плагином');
    try {
      await this.start();
    } catch (error) {
      throw new Error(`${messageOf(error)}`);
    }
    try {
      return await this.#send(action, payload);
    } catch (error) {
      this.#fail(messageOf(error));
      if (this.starts <= this.maxRestarts) {
        await this.start();
        return this.#send(action, payload);
      }
      throw error;
    }
  }

  /** Status for tool results and the page. */
  status() {
    return {
      available: this.ready === true,
      state: this.state,
      version: this.version,
      metricCount: this.metricCount,
      python: this.python,
      pythonSource: this.pythonSource,
      starts: this.starts,
      restarts: this.restarts,
      reason: this.ready ? null : this.lastError,
    };
  }

  async close() {
    this.closing = true;
    this.closed = true;
    this.#kill();
  }
}

/** Python for the engine: the `pythonPath` setting, the bundle `.venv`, then PATH. */
export function resolvePython(settings = {}) {
  const configured = typeof settings.pythonPath === 'string' ? settings.pythonPath.trim() : '';
  if (configured !== '') return { command: configured, source: 'data/settings.json: pythonPath' };
  const venv = process.platform === 'win32'
    ? join(ROOT, '.venv', 'Scripts', 'python.exe')
    : join(ROOT, '.venv', 'bin', 'python');
  if (existsSync(venv)) return { command: venv, source: 'каталог плагина, .venv' };
  return { command: process.platform === 'win32' ? 'python' : 'python3', source: 'PATH' };
}

let engine = null;

function getEngine(settings) {
  const python = resolvePython(settings);
  if (engine && (engine.python !== python.command || engine.closed)) {
    engine.close();
    engine = null;
  }
  if (!engine) {
    engine = new EngineBridge({
      python: python.command,
      pythonSource: python.source,
      script: ENGINE_SCRIPT,
      cwd: ROOT,
      timeoutMs: ENGINE_TIMEOUT_MS,
      maxRestarts: ENGINE_MAX_RESTARTS,
    });
  }
  return engine;
}

/** Close the shared engine; used by plugin unload and by tests. */
export async function closeEngine() {
  if (!engine) return;
  const current = engine;
  engine = null;
  await current.close();
}

async function measureWithEngine(rawText) {
  const text = proseForMetrics(rawText);
  const settings = loadSettings().value;
  const python = resolvePython(settings);
  let bridge;
  try {
    bridge = getEngine(settings);
  } catch (error) {
    return {
      metrics: {},
      counts: countsOf(text),
      unavailable: {},
      engine: { available: false, reason: messageOf(error), python: python.command },
    };
  }
  try {
    const result = await bridge.request('measure', { text, options: { language: 'ru' } });
    const metrics = result && typeof result.metrics === 'object' && result.metrics !== null ? result.metrics : {};
    const counts = result && typeof result.counts === 'object' && result.counts !== null ? result.counts : countsOf(text);
    return {
      metrics,
      counts,
      unavailable: result && typeof result.unavailable === 'object' && result.unavailable !== null ? result.unavailable : {},
      engine: { available: true, version: bridge.version, python: python.command, pythonSource: python.source },
    };
  } catch (error) {
    return {
      metrics: {},
      counts: countsOf(text),
      unavailable: {},
      engine: { available: false, reason: messageOf(error), python: python.command, pythonSource: python.source },
    };
  }
}

// #endregion
// #region evaluation

function ruleProfiles(rule) {
  const list = Array.isArray(rule.profiles) ? rule.profiles : ['*'];
  return list.includes('*') ? null : list;
}

function ruleApplies(rule, profile, layers) {
  const profiles = ruleProfiles(rule);
  if (profiles && !profiles.includes(profile)) return false;
  const layer = typeof rule.layer === 'string' ? rule.layer : null;
  if (layer && layers && layers[layer] === false) return false;
  return true;
}

/**
 * Code, formulas and quoted examples are not the author's prose: the law says
 * not to touch them, so the rules must not see them. Masking keeps the string
 * length, so a match index still points at the original text for `places`.
 */
const MASK_PATTERNS = [
  /```[\s\S]*?```/gu,
  /`[^`\n]*`/gu,
  /\$\$[\s\S]*?\$\$/gu,
  /\$[^$\n]*\$/gu,
  /\\\[[\s\S]*?\\\]/gu,
  /\\\([\s\S]*?\\\)/gu,
  /^>[^\n]*$/gmu,
];

export function maskForRules(text) {
  let masked = String(text ?? '');
  for (const pattern of MASK_PATTERNS) {
    masked = masked.replace(pattern, (chunk) => '\u00a0'.repeat(chunk.length));
  }
  return masked;
}

/**
 * What the metrics measure: the author's prose. Code, formulas, quotations and
 * table rows are not prose, so they are dropped instead of masked: a block of
 * shell commands would otherwise count as one long Russian sentence and skew
 * sentence length, clause depth and the noun-to-verb ratio. Headings stay,
 * because `ai.heading` counts them.
 */
const NON_PROSE = [
  /```[\s\S]*?```/gu,
  /`[^`\n]*`/gu,
  /\$\$[\s\S]*?\$\$/gu,
  /\$[^$\n]*\$/gu,
  /\\\[[\s\S]*?\\\]/gu,
  /\\\([\s\S]*?\\\)/gu,
  /^>[^\n]*$/gmu,
  /^\s*\|.*\|\s*$/gmu,
];

export function proseForMetrics(text) {
  let prose = String(text ?? '');
  for (const pattern of NON_PROSE) prose = prose.replace(pattern, ' ');
  return prose;
}

/**
 * JavaScript `\b` counts only ASCII word characters, so a boundary that touches
 * Cyrillic never matches. Rewrite an opening boundary as a negative lookbehind
 * and a closing one as a negative lookahead over letters, digits and underscore;
 * the pattern keeps its meaning and starts matching Russian words.
 */
export function adaptBoundaries(pattern) {
  const source = String(pattern);
  if (!source.includes('\\b') || !/[\u0400-\u04FF]/u.test(source)) return source;
  let out = '';
  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === '\\' && source[index + 1] === 'b') {
      const opening = out === '' || /[|(]$/u.test(out);
      const next = source[index + 2];
      const closing = next === undefined || next === '|' || next === ')';
      if (opening && closing) out += '\\b';
      else if (opening) out += '(?<![\\p{L}\\p{N}_])';
      else if (closing) out += '(?![\\p{L}\\p{N}_])';
      else out += '\\b';
      index += 1;
      continue;
    }
    out += source[index];
  }
  return out;
}

function collectMatches(text, pattern) {
  const source = adaptBoundaries(pattern);
  let regex;
  try {
    regex = new RegExp(source, 'giu');
  } catch (error) {
    try {
      regex = new RegExp(source, 'gi');
    } catch {
      return { error: `регулярное выражение не компилируется: ${messageOf(error)}`, matches: [] };
    }
  }
  const matches = [];
  let match = regex.exec(text);
  while (match && matches.length < 50) {
    matches.push({ start: match.index, end: match.index + match[0].length, text: match[0] });
    if (match[0] === '') regex.lastIndex += 1;
    match = regex.exec(text);
  }
  return { matches, error: null };
}

/**
 * Judge one text against the rules and the profile targets. Pure: every input
 * is a value, so tests can pin the rules instead of the files on disk.
 */
export function evaluate(input) {
  const text = String(input.text ?? '');
  const profile = input.profile;
  const settings = { ...BUILTIN_SETTINGS, ...(input.settings || {}) };
  settings.layers = { ...BUILTIN_SETTINGS.layers, ...(input.settings?.layers || {}) };
  const rules = input.rules || { rules: [], source: 'правила не переданы' };
  const targets = input.targets || { profile, margin: 0, strict: false, metrics: {} };
  const metrics = input.metrics && typeof input.metrics === 'object' ? input.metrics : {};
  const engine = input.engine || { available: false, reason: 'движок не вызывался' };
  const counts = input.counts || countsOf(text);
  const words = Math.max(1, Number(counts.words) || 0);
  const gateWords = Number(settings.gate?.minWordsForCheck) || 0;
  const belowGate = gateWords > 0 && words < gateWords;
  const strict = Boolean(settings.strict);
  const masked = maskForRules(text);
  const violations = [];
  const notes = [];
  let passed = 0;
  let total = 0;

  const push = (violation) => {
    violations.push(violation);
  };

  const judge = (rule, kind) => {
    if (!ruleApplies(rule, profile, settings.layers)) return;
    if (!rule.pattern) return;
    total += 1;
    const found = collectMatches(masked, rule.pattern);
    if (found.error) {
      notes.push(`${rule.id}: ${found.error}`);
      passed += 1;
      return;
    }
    if (found.matches.length === 0) {
      passed += 1;
      return;
    }
    let value;
    if (kind === 'forbidden') value = found.matches.length;
    else if (rule.units === 'per1000') value = (found.matches.length / words) * 1000;
    else value = found.matches.length;
    if (kind !== 'forbidden' && Number.isFinite(rule.max)) {
      if (rule.units === 'per1000') {
        // Contract, "Частотная норма в коротком тексте": the allowed number of
        // occurrences is max(1, max·words/1000), so one occurrence is never a
        // defect whatever the length. `value` below stays the density: it is
        // what a person reads in the report.
        const allowed = Math.max(1, (rule.max * words) / 1000);
        if (found.matches.length <= allowed) {
          passed += 1;
          return;
        }
      } else if (value <= rule.max) {
        passed += 1;
        return;
      }
    }
    // A regex rule without a limit counts every occurrence as a defect.
    const severity = String(rule.severity || (kind === 'forbidden' ? 'error' : 'warning'));
    push({
      rule: rule.id || 'rule',
      ref: rule.ref || '',
      layer: rule.layer || 'language',
      severity,
      title: rule.title || rule.id || 'Правило',
      count: found.matches.length,
      value: Number(value.toFixed(3)),
      units: kind === 'forbidden' ? 'count' : (rule.units || 'count'),
      places: found.matches.slice(0, 5).map((match) => contextAround(text, match.start, match.end)),
      message: rule.message || '',
      fix: rule.fix || '',
    });
  };

  for (const rule of Array.isArray(rules.rules) ? rules.rules : []) {
    if (!rule || typeof rule !== 'object') continue;
    // `data/rules.json` is one list: `kind` says what the rule is. Manual rules
    // only carry a message and metric gates live in `data/targets.json`.
    if (rule.kind === 'manual' || rule.kind === 'metric') continue;
    judge(rule, rule.kind === 'forbidden' ? 'forbidden' : 'regex');
  }

  const targetEntries = Object.entries(targets.metrics || {});
  const metricSkipped = [];
  for (const [id, target] of targetEntries) {
    total += 1;
    const value = metrics[id];
    if (!Number.isFinite(value)) {
      metricSkipped.push(id);
      total -= 1;
      continue;
    }
    if (!engine.available) {
      metricSkipped.push(id);
      total -= 1;
      continue;
    }
    if (belowGate) {
      metricSkipped.push(id);
      total -= 1;
      continue;
    }
    if (value >= target.effectiveMin && value <= target.effectiveMax) {
      passed += 1;
      continue;
    }
    push({
      rule: id,
      ref: '',
      layer: 'metric',
      severity: strict ? 'error' : 'warning',
      title: `Метрика ${id} вне цели`,
      count: 1,
      value: Number(value.toFixed(4)),
      units: 'value',
      places: [],
      message: `Значение ${Number(value.toFixed(4))} вне интервала [${target.effectiveMin}; ${target.effectiveMax}]`,
      fix: 'Изменить текст под цель профиля или подтвердить цель в настройках',
    });
  }

  if (belowGate) notes.push(`Текст короче порога проверки ${gateWords} слов: метрики считаются, но ворот не закрывают`);
  if (!engine.available) notes.push(`Движок метрик недоступен: ${engine.reason || 'причина неизвестна'}; проверены только правила`);

  const errors = violations.filter((violation) => violation.severity === 'error');
  const warnings = violations.filter((violation) => violation.severity !== 'error');
  const verdict = errors.length > 0 ? 'fail' : warnings.length > 0 ? 'warn' : 'pass';
  const score = total > 0 ? Math.round((passed / total) * 100) : 100;

  const next = [];
  for (const violation of errors.slice(0, 5)) {
    next.push(`${violation.ref || violation.rule}: ${violation.title}${violation.fix ? ` — ${violation.fix}` : ''}`);
  }
  for (const violation of warnings.slice(0, 3)) {
    next.push(`${violation.ref || violation.rule}: ${violation.title}${violation.fix ? ` — ${violation.fix}` : ''}`);
  }
  if (warnings.length > 3) next.push(`Ещё предупреждений: ${warnings.length - 3}`);
  if (metricSkipped.length > 0) next.push(`Метрики без проверки: ${metricSkipped.slice(0, 6).join(', ')}${metricSkipped.length > 6 ? '…' : ''}`);
  if (verdict === 'pass' && next.length === 0) next.push('Правок нет: текст прошёл правила профиля и цели метрик');

  return {
    profile,
    verdict,
    score,
    counts,
    metrics,
    targets: targets.metrics || {},
    margin: targets.margin ?? 0,
    strict,
    rulesSource: rules.source || (rules.builtin ? 'встроенные правила' : 'data/rules.json'),
    engine,
    violations,
    notes,
    next,
  };
}

// #endregion
// #region actions

function layerDocs(layer, settings) {
  const files = LAYER_FILES[layer] || [];
  const docs = [];
  for (const file of files) {
    const read = readText(join(ROOT, file));
    if (!read.ok) {
      docs.push({ id: `law/${basename(file, '.md')}`, layer, title: layer, text: '', path: file, missing: true, error: read.error });
      continue;
    }
    const { meta, body } = parseFrontMatter(read.text);
    docs.push({
      id: meta.id || `law/${basename(file, '.md')}`,
      layer,
      title: meta.title || firstHeading(body) || layer,
      reads: meta.reads || 'on-demand',
      text: body,
      path: file,
    });
  }
  return docs;
}

function templateDoc(path) {
  const read = readText(join(ROOT, path));
  if (!read.ok) return { id: path.replace(/^templates\//u, '').replace(/\.md$/u, ''), title: '', body: '', slots: [], path, missing: true, error: read.error };
  const { meta, body } = parseFrontMatter(read.text);
  const slots = [];
  for (const match of read.text.matchAll(/\{\{\s*([^}]+?)\s*\}\}/gu)) {
    const slot = match[1].trim();
    if (!slots.includes(slot)) slots.push(slot);
  }
  const section = sectionBody(body, /слот/iu);
  if (slots.length === 0 && section !== '') {
    for (const line of section.split(/\r?\n/u)) {
      const item = /^[-*]\s+(.+)$/u.exec(line.trim());
      if (item) slots.push(item[1].trim());
    }
  }
  return {
    id: meta.id || path.replace(/^templates\//u, '').replace(/\.md$/u, ''),
    title: meta.title || firstHeading(body) || path,
    profile: meta.profile || '',
    audience: meta.audience || '',
    length: meta.length || '',
    body,
    slots,
    path,
  };
}

function checklistOf(doc, fallbackDoc) {
  const section = doc ? sectionBody(doc.body || '', /чек-?лист/iu) : '';
  const items = section.split(/\r?\n/u)
    .map((line) => line.trim())
    .map((line) => /^(?:[-*]|\d+\.)\s+(.+)$/u.exec(line))
    .filter(Boolean)
    .map((match) => match[1].trim())
    .filter((item) => item !== '');
  if (items.length > 0) return items.slice(0, 20);
  if (!fallbackDoc) return [];
  const central = sectionBody(fallbackDoc, /нарушени/iu);
  return central.split(/\r?\n/u)
    .map((line) => line.trim())
    .map((line) => /^[-*]\s+(.+)$/u.exec(line))
    .filter(Boolean)
    .map((match) => match[1].trim())
    .slice(0, 20);
}

/** Quotes for brief, taken from the corpus the profile reads and from that corpus alone. */
function samplesOf(settings, profile, count = 3) {
  const kind = corpusKindFor(profile);
  const index = loadCorpusIndex(kind);
  const label = (CORPUS_KINDS[kind] || CORPUS_KINDS.literary).title;
  if (!index.ok) return { samples: [], corpus: kind, note: `${label} корпус не собран: ${index.error}` };
  const allowed = settings.author === 'both' ? null : [settings.author];
  const picked = index.excerpts
    .filter((excerpt) => excerpt && typeof excerpt.text === 'string')
    .filter((excerpt) => (allowed ? allowed.includes(excerpt.author) : true))
    .slice(0, count)
    .map((excerpt) => ({
      id: excerpt.id,
      author: excerpt.author,
      work: excerpt.work || excerpt.title || '',
      genre: excerpt.genre || '',
      year: excerpt.year ?? null,
      kind: excerpt.kind || 'paragraph',
      text: excerpt.text,
    }));
  return { samples: picked, corpus: kind, note: null };
}

/**
 * Genre word → template file name. `brief` is called with the genre of the document, and
 * the genre decides the skeleton; the profile only says which templates it allows.
 * Genre words that name several documents (doc, spec) point at the report, because the
 * report is the general technical document.
 */
const TEMPLATE_BY_GENRE = {
  answer: 'answer', reply: 'answer',
  report: 'report', doc: 'report', spec: 'report',
  readme: 'readme', plan: 'plan',
  letter: 'letter', email: 'letter', message: 'letter',
  essay: 'essay', article: 'essay', explanation: 'essay',
  math: 'math-article', paper: 'math-article', 'math-article': 'math-article',
};

/** The template of the named genre, else the first template the profile allows. */
function chooseTemplate(paths, genre) {
  const wanted = TEMPLATE_BY_GENRE[String(genre ?? '').trim().toLowerCase()];
  if (wanted) {
    const hit = paths.find((path) => basename(path) === `${wanted}.md`);
    if (hit) {
      const candidate = templateDoc(hit);
      if (!candidate.missing) return candidate;
    }
  }
  let template = { id: '', title: '', body: '', slots: [], path: '', missing: true, error: 'в профиле нет шаблона' };
  for (const path of paths) {
    const candidate = templateDoc(path);
    if (!candidate.missing) return candidate;
    template = candidate;
  }
  return template;
}

async function runBrief(args) {
  const settingsRead = loadSettings();
  const settings = settingsRead.value;
  const profilesRead = loadProfiles();
  const profilesDoc = profilesRead.ok ? profilesRead.value : {};
  const profileMeta = (profilesDoc.profiles && profilesDoc.profiles[settings.defaultProfile]) || {};
  const genre = String(args.genre ?? '').trim();
  const resolved = resolveProfileId(genre, settings);
  const profileId = resolved.id;
  const profile = (profilesDoc.profiles && profilesDoc.profiles[profileId]) || profileMeta || {};
  const warnings = [];
  if (resolved.warning) warnings.push(resolved.warning);
  if (!settings.profiles[profileId]) warnings.push(`Профиль ${profileId} выключен в настройках: проверка по нему не выполняется`);
  if (!settings.enabled) warnings.push('Плагин выключен в настройках: инструмент отвечает, но проверка не судит');
  if (!profilesRead.ok) warnings.push(`profiles.json не прочитан: ${profilesRead.error}`);

  const layers = Array.isArray(profile.layers) ? profile.layers : ['central'];
  const law = [];
  for (const layer of layers) {
    if (settings.layers[layer] === false) continue;
    law.push(...layerDocs(layer, settings));
  }
  if (!law.some((doc) => doc.layer === 'central')) law.unshift(...layerDocs('central', settings));

  const templatePaths = Array.isArray(profile.templates) ? profile.templates : [];
  const template = chooseTemplate(templatePaths, genre);

  const targetsDoc = loadTargets();
  const targets = targetsFor(profileId, settings, targetsDoc);
  if (!targetsDoc.ok) warnings.push(`targets.json не прочитан: ${targetsDoc.error}`);

  const centralDoc = law.find((doc) => doc.layer === 'central' && typeof doc.text === 'string' && doc.text !== '');
  const checklist = checklistOf(template, centralDoc ? centralDoc.text : null);
  const { samples, note } = samplesOf(settings, profileId, 3);
  if (note) warnings.push(note);
  const corpora = {
    literary: corpusStatus('literary'),
    math: corpusStatus('math'),
    used: corpusKindFor(profileId),
  };

  return {
    profile: profileId,
    title: profile.title || profileId,
    width: profile.width || '',
    audience: String(args.audience ?? ''),
    length: String(args.length ?? ''),
    language: String(args.language ?? 'ru'),
    law: law.map((doc) => ({ id: doc.id, layer: doc.layer, title: doc.title, text: doc.text, path: doc.path, ...(doc.missing ? { missing: true, error: doc.error } : {}) })),
    template: {
      id: template.id,
      title: template.title,
      body: template.body,
      slots: template.slots,
      path: template.path,
      ...(template.missing ? { missing: true, error: template.error } : {}),
    },
    targets,
    checklist,
    samples,
    corpora,
    warnings,
  };
}

function resolveGuidePath(requested) {
  const clean = String(requested ?? '').trim().replace(/\\/gu, '/').replace(/^\.\//u, '');
  if (clean === '') return null;
  const candidates = [];
  if (clean.includes('/') || extname(clean) === '.md') {
    candidates.push(join(ROOT, clean));
    if (!clean.endsWith('.md')) candidates.push(join(ROOT, `${clean}.md`));
  }
  const alias = LAYER_ALIASES[clean.toLowerCase()];
  if (alias) {
    candidates.push(join(ROOT, 'law', `${alias}.md`));
  }
  for (const dir of ['law', 'reference', 'templates']) {
    candidates.push(join(ROOT, dir, clean));
    if (!clean.endsWith('.md')) candidates.push(join(ROOT, dir, `${clean}.md`));
    if (!clean.startsWith('0') && !clean.endsWith('.md')) candidates.push(join(ROOT, dir, `${clean}.md`));
  }
  for (const dir of ['law', 'reference', 'templates']) {
    let entries = [];
    try {
      entries = readdirSync(join(ROOT, dir));
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry === clean || entry === `${clean}.md` || entry.includes(clean)) candidates.push(join(ROOT, dir, entry));
    }
  }
  for (const candidate of candidates) {
    if (!candidate.startsWith(ROOT)) continue;
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function runGuide(args) {
  const wanted = String(args.name ?? '').trim();
  const path = resolveGuidePath(wanted);
  if (!path) {
    const known = ['law/00-central'];
    for (const layer of LAYER_IDS) known.push(`law/${LAYER_ALIASES[layer]}`);
    return {
      name: wanted,
      found: false,
      error: `Файл «${wanted}» не найден среди law/, reference/, templates/`,
      known,
    };
  }
  const read = readText(path);
  if (!read.ok) return { name: wanted, found: false, path: relPath(path), error: read.error };
  const { meta, body } = parseFrontMatter(read.text);
  return {
    name: relPath(path),
    found: true,
    path: relPath(path),
    id: meta.id || '',
    title: meta.title || firstHeading(body) || relPath(path),
    layer: meta.layer || '',
    rules: meta.rules || '',
    text: body,
    mtimeMs: read.mtimeMs,
  };
}

async function runCheck(args) {
  const settingsRead = loadSettings();
  const settings = settingsRead.value;
  const text = String(args.text ?? '');
  if (text.trim() === '') return { error: 'Пустой текст: проверять нечего', profile: settings.defaultProfile };
  const resolved = resolveProfileId(args.profile ?? settings.defaultProfile, settings);
  const profile = resolved.id;
  if (!settings.profiles[profile]) {
    return {
      error: `Профиль ${profile} выключен в настройках: проверка не выполняется`,
      profile,
      settingsSource: settingsRead.source,
    };
  }
  if (!settings.enabled) {
    return {
      profile,
      verdict: 'pass',
      score: 100,
      counts: countsOf(text),
      metrics: {},
      targets: {},
      engine: { available: false, reason: 'плагин выключен в настройках' },
      violations: [],
      notes: ['Плагин выключен в data/settings.json: проверка не судит'],
      next: ['Включить плагин на странице настроек'],
      disabled: true,
    };
  }
  const rules = loadRules();
  if (!rules.ok) {
    return {
      profile,
      error: `Правила не загружены: ${rules.error}`,
      hint: RULES_HINT,
      counts: countsOf(text),
      engine: { available: false, reason: 'проверка не выполнялась: нет набора правил' },
    };
  }
  const measured = await measureWithEngine(text);
  const targetsDoc = loadTargets();
  const targets = targetsFor(profile, settings, targetsDoc);
  const result = evaluate({
    text,
    profile,
    settings: { ...settings, strict: args.strict === undefined ? settings.strict : Boolean(args.strict) },
    rules,
    targets,
    metrics: measured.metrics,
    counts: measured.counts,
    engine: measured.engine,
  });
  if (resolved.warning) result.notes.unshift(resolved.warning);
  if (!targetsDoc.ok) result.notes.push(`targets.json не прочитан: ${targetsDoc.error}`);
  result.settingsSource = settingsRead.source;
  result.unchanged = measured.unavailable;
  return result;
}

async function runMeasure(args) {
  const text = String(args.text ?? '');
  const catalogueRead = loadCatalog();
  const measured = await measureWithEngine(text);
  return {
    metrics: measured.metrics,
    counts: measured.counts,
    catalogue: {
      version: catalogueRead.catalogue.version,
      count: catalogueRead.catalogue.metrics.length,
      groups: catalogueRead.catalogue.groups,
      ...(catalogueRead.ok ? {} : { error: catalogueRead.error }),
    },
    unavailable: measured.unavailable,
    engine: measured.engine,
  };
}

function runCorpus(args) {
  const settings = loadSettings().value;
  const resolved = resolveProfileId(args.profile ?? settings.defaultProfile, settings);
  const kind = corpusKindFor(resolved.id);
  const spec = CORPUS_KINDS[kind] || CORPUS_KINDS.literary;
  const index = loadCorpusIndex(kind);
  const author = String(args.author ?? '').trim();
  const genre = String(args.genre ?? '').trim();
  const wanted = Math.min(20, Math.max(1, Number(args.k) || 5));
  const query = String(args.query ?? '').trim();
  const head = { profile: resolved.id, corpus: kind, corpusTitle: spec.title, query, author: author || null, genre: genre || null };
  if (!index.ok) {
    return {
      ...head,
      matches: [],
      total: 0,
      built: null,
      note: `${spec.title} корпус не собран: ${index.error}. Профиль ${resolved.id} читает ${spec.index}; смешивать корпуса нельзя.`,
    };
  }
  const allowedAuthors = author === '' || author === 'both' ? null : [author];
  const tokens = query.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((token) => token.length > 2);
  const scored = [];
  for (const excerpt of index.excerpts) {
    if (!excerpt || typeof excerpt.text !== 'string') continue;
    if (allowedAuthors && !allowedAuthors.includes(excerpt.author)) continue;
    if (genre && String(excerpt.genre ?? '') !== genre) continue;
    let score = 0;
    if (tokens.length === 0) score = 1;
    else {
      const haystack = `${excerpt.text} ${excerpt.work ?? ''} ${excerpt.title ?? ''}`.toLowerCase();
      for (const token of tokens) if (haystack.includes(token)) score += 1;
    }
    if (score > 0) scored.push({ excerpt, score });
  }
  scored.sort((left, right) => right.score - left.score || String(left.excerpt.id).localeCompare(String(right.excerpt.id)));
  const matches = scored.slice(0, wanted).map(({ excerpt }) => ({
    id: excerpt.id,
    author: excerpt.author,
    work: excerpt.work || excerpt.title || '',
    genre: excerpt.genre || '',
    year: excerpt.year ?? null,
    kind: excerpt.kind || 'paragraph',
    text: clampText(excerpt.text, 1200),
  }));
  if (author === '' && kind === 'literary' && settings.author !== 'both') {
    const filtered = matches.filter((match) => match.author === settings.author);
    if (filtered.length > 0) matches.splice(0, matches.length, ...filtered);
  }
  return {
    ...head,
    matches,
    total: scored.length,
    built: index.built,
    ...(matches.length === 0 ? { note: 'Совпадений нет: перефразировать запрос или снять фильтр автора и жанра' } : {}),
  };
}

function runFingerprint(args) {
  const settings = loadSettings().value;
  const profilesRead = loadProfiles();
  const profilesDoc = profilesRead.ok ? profilesRead.value : {};
  const targetsDoc = loadTargets();
  const catalogueRead = loadCatalog();
  const profileFilter = String(args.profile ?? '').trim();
  const metricFilter = String(args.metric ?? '').trim();

  const profiles = {};
  for (const id of PROFILE_IDS) {
    if (profileFilter !== '' && profileFilter !== id) continue;
    const meta = (profilesDoc.profiles && profilesDoc.profiles[id]) || {};
    const targets = targetsFor(id, settings, targetsDoc);
    const rows = Object.entries(targets.metrics)
      .filter(([metricId]) => metricFilter === '' || metricId === metricFilter)
      .map(([metricId, target]) => ({ id: metricId, ...target }));
    profiles[id] = {
      title: meta.title || id,
      enabled: settings.profiles[id] !== false,
      layers: Array.isArray(meta.layers) ? meta.layers : [],
      templates: Array.isArray(meta.templates) ? meta.templates : [],
      margin: targets.margin,
      gates: rows.length,
      targets: rows,
    };
  }
  const catalogue = catalogueRead.catalogue;
  const metrics = catalogue.metrics
    .filter((metric) => metricFilter === '' || metric.id === metricFilter)
    .map((metric) => ({ id: metric.id, label: metric.label, unit: metric.unit, group: metric.group, needs: metric.needs }));
  return {
    profiles,
    targets: {
      available: targetsDoc.ok,
      marginDefault: targetsDoc.marginDefault,
      updated: targetsDoc.updated,
      strict: settings.strict,
      margin: settings.margin,
      ...(targetsDoc.ok ? {} : { error: targetsDoc.error }),
    },
    catalogue: {
      available: catalogueRead.ok,
      version: catalogue.version,
      count: catalogue.metrics.length,
      groups: catalogue.groups,
      metrics,
      ...(catalogueRead.ok ? {} : { error: catalogueRead.error }),
    },
    author: settings.author,
  };
}

/** Status of one corpus, for `brief`, `config` and the page. */
function corpusStatus(kind = 'literary') {
  const spec = CORPUS_KINDS[kind] || CORPUS_KINDS.literary;
  const manifest = loadCorpusManifest(kind);
  const index = loadCorpusIndex(kind);
  const base = {
    kind,
    title: spec.title,
    indexPath: spec.index,
    manifestPath: spec.manifest,
    excerpts: index.ok ? index.excerpts.length : 0,
    built: index.ok ? index.built : null,
  };
  if (!manifest.ok) {
    return { ...base, available: false, error: manifest.error, authors: {}, works: 0 };
  }
  const value = manifest.value || {};
  const coverage = value.coverage && typeof value.coverage === 'object' ? value.coverage : {};
  const works = Array.isArray(value.works) ? value.works : [];
  const authors = {};
  for (const [author, item] of Object.entries(coverage)) {
    const row = item && typeof item === 'object' ? item : {};
    authors[author] = {
      words: Number(row.words) || 0,
      works: Number(row.works) || 0,
      target: Number(row.target) || 0,
      ready: (Number(row.words) || 0) >= 120000,
    };
  }
  for (const work of works) {
    const author = typeof work.author === 'string' ? work.author : '';
    if (author === '' || authors[author]) continue;
    authors[author] = { words: 0, works: 1, target: 0, ready: false };
  }
  return {
    ...base,
    available: true,
    built: value.built ?? base.built,
    use: value.use ?? '',
    works: works.length,
    excluded: Array.isArray(value.excluded) ? value.excluded.length : 0,
    authors,
  };
}

function filePresence() {
  const check = (path) => existsSync(join(ROOT, path));
  return {
    'law/00-central.md': check('law/00-central.md'),
    'data/profiles.json': check('data/profiles.json'),
    'data/settings.json': check('data/settings.json'),
    'data/rules.json': check('data/rules.json'),
    'data/metrics.json': check('data/metrics.json'),
    'data/targets.json': check('data/targets.json'),
    'corpus/index.json': check('corpus/index.json'),
    'corpus/manifest.json': check('corpus/manifest.json'),
    'corpus/math/index.json': check('corpus/math/index.json'),
    'corpus/math/manifest.json': check('corpus/math/manifest.json'),
    'templates': check('templates'),
    'reference': check('reference'),
    'tools/analyze.py': check('tools/analyze.py'),
  };
}

function runConfig(context = {}) {
  const settingsRead = loadSettings();
  const python = resolvePython(settingsRead.value);
  const engineStatus = engine ? engine.status() : { available: false, state: 'idle', reason: 'движок ещё не запускался', python: python.command, pythonSource: python.source };
  const gatewayOpen = typeof context.gatewayAvailable === 'boolean' ? context.gatewayAvailable : runtime.gatewayAvailable;
  return {
    settings: settingsRead.value,
    source: {
      settings: settingsRead.source,
      profiles: existsSync(PROFILES_FILE) ? 'data/profiles.json' : 'встроенные значения',
      rules: existsSync(RULES_FILE) ? 'data/rules.json' : 'встроенные правила',
      targets: existsSync(TARGETS_FILE) ? 'data/targets.json' : 'нет файла',
      metrics: existsSync(METRICS_FILE) ? 'data/metrics.json' : 'нет файла',
    },
    mtimeMs: settingsRead.mtimeMs,
    error: settingsRead.error,
    python: { command: python.command, source: python.source, exists: python.source.startsWith('каталог') ? existsSync(python.command) : null },
    engine: engineStatus,
    files: filePresence(),
    page: {
      path: GATEWAY_PATH,
      available: gatewayOpen === true,
      reason: gatewayOpen === true ? null : 'маршрут страницы не поднят: в этом профиле нет службы webServer',
    },
    defaults: BUILTIN_SETTINGS,
  };
}

/** One dispatch for the tool and the page route. */
async function dispatch(args, context = {}) {
  const action = String(args.action ?? '').trim();
  switch (action) {
    case 'brief':
      return runBrief(args);
    case 'guide':
      return runGuide(args);
    case 'check':
      return runCheck(args);
    case 'measure':
      return runMeasure(args);
    case 'corpus':
      return runCorpus(args);
    case 'fingerprint':
      return runFingerprint(args);
    case 'config':
      return runConfig(context);
    default:
      return {
        error: `Действие «${action}» неизвестно`,
        actions: ['brief', 'guide', 'check', 'measure', 'corpus', 'fingerprint', 'config'],
      };
  }
}

// #endregion
// #region prompt section and runtime skill

/**
 * The prompt section: the first section of `law/00-central.md` plus its
 * "Проверка перед отправкой" list and one line about the tool. Short on
 * purpose — it enters every request.
 */
export function promptSection() {
  const read = readText(join(ROOT, 'law', '00-central.md'));
  if (!read.ok) {
    return { text: FALLBACK_SECTION, source: 'запасной текст', path: 'law/00-central.md', error: read.error };
  }
  const { body } = parseFrontMatter(read.text);
  const intro = body.split(/\r?\n##\s+/u)[0].trim();
  const checks = sectionBody(body, /проверка перед отправкой/iu);
  const parts = ['## Стиль: текст для человека вне контекста'];
  if (intro !== '') parts.push(intro.replace(/^#{1,3}\s+.*$/mu, '').trim());
  if (checks !== '') parts.push(checks);
  parts.push('Перед документом — `style` с действием `brief` (жанр и читатель), затем шаблон и чек-лист жанра. Перед выдачей ответа — `style` с действием `check`; всё с пометкой `error` исправляется до отправки.');
  const text = parts.filter((part) => part !== '').join('\n\n');
  return {
    text: text.length > 2400 ? `${text.slice(0, 2399)}…` : text,
    source: 'law/00-central.md',
    path: 'law/00-central.md',
    mtimeMs: read.mtimeMs,
    error: null,
  };
}

const SKILL_CONTENT = [
  '# style: русский текст для человека вне контекста',
  '',
  'Инструмент `style` держит порядок работы с текстом. Он не заменяет закон, а открывает его по частям.',
  '',
  '## Перед документом',
  '',
  '1. Вызови `style` с действием `brief`: `genre` (answer, report, plan, letter, essay, math), читатель и объём.',
  '2. Возьми из ответа `template` (скелет, слоты) и `checklist`; открой нужные слои через `style` с действием `guide` (`law/00-central`, `law/30-language`, …).',
  '3. Пиши по шаблону: он задаёт скелет и порядок, содержание — под читателя.',
  '',
  '## Перед выдачей ответа',
  '',
  '1. Вызови `style` с действием `check`, передав `text` и `profile`.',
  '2. Всё с `severity: "error"` исправляется до отправки. Ложная пометка — причина называется одной строкой в ответе.',
  '3. Короткая реплика в две строки проверки не требует.',
  '',
  '## Где что лежит',
  '',
  '- `law/` — закон и слои в едином формате: паспорт, правила `R-*`, примеры, чек-лист.',
  '- `templates/` — семь шаблонов жанров.',
  '- `data/targets.json` — целевые интервалы профилей, `data/rules.json` — машинные правила.',
  '- `corpus/` — образцы Толстого и Достоевского; `style` с действием `corpus` ищет цитаты, числа по образцам не выдаются за норму.',
  '- У профиля `math_paper` свой корпус, `corpus/math/`: действие `corpus` читает его, остальные профили читают литературный. Корпуса не смешиваются.',
  '- `style` с действием `measure` — метрики движком на Python, `fingerprint` — цели отпечатка, `config` — действующие настройки.',
  '',
  'Инструмент недоступен — тот же порядок вручную: шаблон, затем чек-лист жанра. Ответ без шаблона и без проверки считается незаконченным.',
].join('\n');

// #endregion
// #region settings write and page route

function sanitizePatch(patch) {
  const clean = {};
  const problems = [];
  if (patch === null || typeof patch !== 'object') return { clean, problems: ['тело настроек не объект'] };
  for (const key of ['enabled', 'strict']) if (typeof patch[key] === 'boolean') clean[key] = patch[key];
  if (typeof patch.defaultProfile === 'string') {
    if (PROFILE_IDS.includes(patch.defaultProfile)) clean.defaultProfile = patch.defaultProfile;
    else problems.push(`defaultProfile «${patch.defaultProfile}» не из списка профилей`);
  }
  if (patch.margin !== undefined) {
    const margin = Number(patch.margin);
    if (Number.isFinite(margin) && margin >= 0 && margin <= 1) clean.margin = margin;
    else problems.push('margin вне интервала 0…1');
  }
  if (typeof patch.author === 'string') {
    if (AUTHORS.includes(patch.author)) clean.author = patch.author;
    else problems.push(`author «${patch.author}» не из списка tolstoy, dostoevsky, both`);
  }
  if (typeof patch.pythonPath === 'string') clean.pythonPath = patch.pythonPath.trim();
  if (patch.profiles && typeof patch.profiles === 'object') {
    clean.profiles = {};
    for (const id of PROFILE_IDS) {
      if (typeof patch.profiles[id] === 'boolean') clean.profiles[id] = patch.profiles[id];
    }
  }
  if (patch.layers && typeof patch.layers === 'object') {
    clean.layers = {};
    for (const layer of LAYER_IDS) {
      if (typeof patch.layers[layer] === 'boolean') clean.layers[layer] = patch.layers[layer];
    }
  }
  if (patch.metricTargets && typeof patch.metricTargets === 'object') {
    clean.metricTargets = {};
    for (const [id, target] of Object.entries(patch.metricTargets)) {
      if (target === null) {
        clean.metricTargets[id] = null;
        continue;
      }
      if (!target || typeof target !== 'object') continue;
      const min = Number(target.min);
      const max = Number(target.max);
      if (!Number.isFinite(min) || !Number.isFinite(max) || min > max) {
        problems.push(`metricTargets.${id}: нужны числа min ≤ max`);
        continue;
      }
      clean.metricTargets[id] = { min, max };
    }
  }
  if (patch.gate && typeof patch.gate === 'object') {
    const minWords = Number(patch.gate.minWordsForCheck);
    // A patch that carries no threshold leaves the stored one alone: the gate
    // holds one setting, and dropping it for an unknown key would lose a value.
    if (Number.isFinite(minWords) && minWords >= 0) clean.gate = { minWordsForCheck: Math.round(minWords) };
  }
  return { clean, problems };
}

/** Merge a whitelisted patch into `data/settings.json` atomically. */
export async function writeSettings(patch, who = 'settings page', root = ROOT) {
  const file = join(root, 'data', 'settings.json');
  const current = readJson(file);
  const base = current.ok && current.value && typeof current.value === 'object' ? current.value : {};
  const { clean, problems } = sanitizePatch(patch);
  const next = { ...base };
  for (const [key, value] of Object.entries(clean)) {
    if (key === 'metricTargets') {
      const merged = { ...(base.metricTargets || {}) };
      for (const [id, target] of Object.entries(value)) {
        if (target === null) delete merged[id];
        else merged[id] = target;
      }
      next.metricTargets = merged;
    } else if (key === 'gate') next.gate = { ...value };
    else if (key === 'profiles' || key === 'layers') next[key] = { ...(base[key] || {}), ...value };
    else next[key] = value;
  }
  next.version = base.version ?? 1;
  next.changedAt = new Date().toISOString();
  next.changedBy = who;
  const temporary = `${file}.${process.pid}.tmp`;
  await fsp.mkdir(dirname(file), { recursive: true });
  await fsp.writeFile(temporary, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  await fsp.rename(temporary, file);
  invalidate(file);
  return { ok: true, settings: loadSettings(root).value, problems, changedAt: next.changedAt, changedBy: next.changedBy };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
}

function pageState(context) {
  const settingsRead = loadSettings();
  const settings = settingsRead.value;
  const profilesRead = loadProfiles();
  const profilesDoc = profilesRead.ok ? profilesRead.value : {};
  const targetsDoc = loadTargets();
  const catalogue = loadCatalog();
  const targets = {};
  for (const id of PROFILE_IDS) targets[id] = targetsFor(id, settings, targetsDoc);
  const profileRows = PROFILE_IDS.map((id) => {
    const meta = (profilesDoc.profiles && profilesDoc.profiles[id]) || {};
    return { id, title: meta.title || id, layers: Array.isArray(meta.layers) ? meta.layers : [], templates: Array.isArray(meta.templates) ? meta.templates : [] };
  });
  return {
    settings,
    source: settingsRead.source,
    mtimeMs: settingsRead.mtimeMs,
    error: settingsRead.error,
    profiles: profileRows,
    layers: LAYER_IDS.map((id) => ({ id, enabled: settings.layers[id] !== false })),
    targets,
    catalogue: {
      available: catalogue.ok,
      count: catalogue.catalogue.metrics.length,
      groups: catalogue.catalogue.groups,
      metrics: catalogue.catalogue.metrics.map((metric) => ({ id: metric.id, label: metric.label, unit: metric.unit, group: metric.group })),
      ...(catalogue.ok ? {} : { error: catalogue.error }),
    },
    corpus: corpusStatus('literary'),
    corpora: {
      literary: corpusStatus('literary'),
      math: corpusStatus('math'),
    },
    engine: engine ? engine.status() : { available: false, state: 'idle', reason: 'движок ещё не запускался' },
    python: resolvePython(settings),
    files: filePresence(),
    page: { path: GATEWAY_PATH, available: context.gatewayAvailable === true },
  };
}

/**
 * Same-origin JSON route of the settings page: GET state, POST check,
 * POST settings. A body over 256 KiB is refused; every failure is a JSON
 * answer, never an unhandled rejection.
 */
export function createGateway() {
  const handler = async (request) => {
    const url = new URL(request.url, 'http://localhost');
    const context = { gatewayAvailable: true };
    try {
      if (url.pathname === `${GATEWAY_PATH}/state` && request.method === 'GET') {
        return jsonResponse(pageState(context));
      }
      if (url.pathname === `${GATEWAY_PATH}/config` && request.method === 'GET') {
        return jsonResponse(runConfig(context));
      }
      if (url.pathname === `${GATEWAY_PATH}/check` && request.method === 'POST') {
        const body = await readBody(request);
        if (body.error) return jsonResponse({ error: body.error }, 413);
        const result = await runCheck(body.value);
        return jsonResponse(result);
      }
      if (url.pathname === `${GATEWAY_PATH}/settings` && request.method === 'POST') {
        const body = await readBody(request);
        if (body.error) return jsonResponse({ error: body.error }, 413);
        const who = typeof body.value?.changedBy === 'string' ? body.value.changedBy.slice(0, 60) : 'settings page';
        const result = await writeSettings(body.value, who);
        return jsonResponse(result);
      }
      return jsonResponse({ error: `Неизвестный маршрут ${request.method} ${url.pathname}` }, 404);
    } catch (error) {
      return jsonResponse({ error: messageOf(error) }, 500);
    }
  };
  return { path: GATEWAY_PATH, handler };
}

async function readBody(request) {
  const text = await request.text();
  if (text.length > 262144) return { error: 'тело запроса больше 256 КиБ', value: null };
  if (text.trim() === '') return { value: {}, error: null };
  try {
    return { value: JSON.parse(text), error: null };
  } catch (error) {
    return { value: null, error: `тело запроса не JSON: ${messageOf(error)}` };
  }
}

// #endregion
// #region tool and plugin

const TOOL = {
  name: 'style',
  description: [
    'Russian text for a reader outside the context: check a finished text, take a genre template before writing, read the law, measure style metrics.',
    'Call `brief` before a document (genre, audience) and `check` before sending an answer; fix every violation with severity "error".',
    'Actions: brief, guide, check, measure, corpus, fingerprint, config.',
  ].join(' '),
  parameters: {
    type: 'object',
    properties: {
      action: {
        type: 'string',
        enum: ['brief', 'guide', 'check', 'measure', 'corpus', 'fingerprint', 'config'],
        description: 'What to do.',
      },
      genre: { type: 'string', description: 'brief: answer, report, readme, plan, letter, essay, math, or a profile id.' },
      audience: { type: 'string', description: 'brief: who reads the text.' },
      length: { type: 'string', description: 'brief: target volume.' },
      language: { type: 'string', description: 'brief: language of the text, ru by default.' },
      profile: { type: 'string', description: 'check, corpus and fingerprint: one of chat_answer, technical_doc, business_letter, prose_essay, math_paper. The corpus action reads the mathematical corpus for math_paper and the literary one otherwise; the two never mix.' },
      strict: { type: 'boolean', description: 'check: exact metric targets without the margin; a metric miss counts as an error.' },
      text: { type: 'string', description: 'check and measure: the text itself.' },
      name: { type: 'string', description: 'guide: law/00-central, reference/…, templates/…, or a short name.' },
      query: { type: 'string', description: 'corpus: words to look for in the excerpts.' },
      author: { type: 'string', enum: ['tolstoy', 'dostoevsky', 'both'], description: 'corpus: which samples to quote.' },
      k: { type: 'number', description: 'corpus: how many excerpts (1…20).' },
      metric: { type: 'string', description: 'fingerprint: only this metric id.' },
    },
    required: ['action'],
    additionalProperties: false,
  },
  output: {
    schema: { type: 'string' },
    render: (_args, value) => [{ type: 'text', text: String(value) }],
  },
  async execute(args, exec) {
    const toolArgs = args && typeof args === 'object' ? args : {};
    try {
      const result = await dispatch(toolArgs, (exec && exec.context) || {});
      return JSON.stringify(result, null, 2);
    } catch (error) {
      return JSON.stringify({ action: toolArgs.action ?? null, error: messageOf(error) }, null, 2);
    }
  },
};

/** Facts the actions read from the running plugin rather than from a file. */
const runtime = { gatewayAvailable: false };

/**
 * Run `mount` once the named service exists, and never before.
 *
 * `skills` and `webServer` start later than this plugin, so `ctx.get` at apply
 * time answers undefined and the registration would be lost for the whole run.
 * A service named in `inject` would instead hold the whole plugin back in a
 * profile that has no such service — a headless run has no `webServer` — so the
 * service is reached through `ctx.inject`, which activates the mount when the
 * service appears and leaves the rest of the plugin working without it.
 */
function whenService(ctx, name, mount) {
  if (typeof ctx.inject === 'function') {
    ctx.inject([name], (scope) => { mount(scope); });
    return;
  }
  mount(ctx);
}

export function apply(ctx) {
  let gatewayAvailable = false;

  ctx.effect(() => {
    const dispose = ctx.tools.register(TOOL);
    return () => dispose();
  }, 'style-fingerprint: tool');

  ctx.effect(() => {
    const dispose = ctx.systemPrompt.section({
      name: 'style-fingerprint',
      order: 90,
      text: () => promptSection().text,
    });
    return () => dispose();
  }, 'style-fingerprint: prompt section');

  whenService(ctx, 'skills', (scope) => {
    scope.effect(() => {
      const skills = typeof scope.get === 'function' ? scope.get('skills', false) : undefined;
      if (!skills || typeof skills.register !== 'function') return () => {};
      const dispose = skills.register({
        name: 'style',
        description: 'Русский текст для человека вне контекста: brief перед документом, check перед ответом, guide по слоям закона, corpus по образцам.',
        content: SKILL_CONTENT,
        source: 'runtime',
      });
      return () => { if (typeof dispose === 'function') dispose(); };
    }, 'style-fingerprint: skills');
  });

  whenService(ctx, 'webServer', (scope) => {
    scope.effect(() => {
      const server = typeof scope.get === 'function' ? scope.get('webServer', false) : undefined;
      if (!server || typeof server.register !== 'function') return () => {};
      const gateway = createGateway();
      const dispose = server.register({ kind: 'prefix', path: gateway.path, resource: (request) => gateway.handler(request) });
      gatewayAvailable = true;
      runtime.gatewayAvailable = true;
      return () => {
        gatewayAvailable = false;
        runtime.gatewayAvailable = false;
        dispose();
      };
    }, 'style-fingerprint: page route');
  });

  ctx.effect(() => () => { void closeEngine(); }, 'style-fingerprint: engine');

  if (typeof ctx.on === 'function') {
    ctx.on('dispose', () => { void closeEngine(); });
  }
}

/** Internals pinned for the Node tests; not part of the plugin contract. */
export const __test = {
  ROOT,
  PROFILE_IDS,
  LAYER_IDS,
  FALLBACK_SECTION,
  BUILTIN_SETTINGS,
  SKILL_CONTENT,
  TOOL,
  RULES_HINT,
  CORPUS_KINDS,
  dispatch,
  evaluate,
  promptSection,
  loadSettings,
  loadRules,
  loadTargets,
  loadCatalog,
  loadCorpusIndex,
  loadCorpusManifest,
  corpusKindFor,
  corpusStatus,
  resolvePython,
  createGateway,
  writeSettings,
  pageState,
  sanitizePatch,
  resolveGuidePath,
  adaptBoundaries,
  maskForRules,
  buildTool: () => TOOL,
  EngineBridge,
  get engine() { return engine; },
};
