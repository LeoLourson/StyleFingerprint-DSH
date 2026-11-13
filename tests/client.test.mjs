/**
 * Activation test of the browser half. A real Cordis context refuses an
 * undeclared service with `cannot get property "<name>" without inject`, and
 * the Settings page showed exactly that for `theme`: the whole entry failed to
 * sync, so the page never appeared.
 *
 * The fake context below rebuilds that rule — a property read works for the
 * built-in members and for injected names only — and runs the module's own
 * `apply`, so the failure the browser reported is reproduced here.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

/** Members every context exposes without an `inject` declaration. */
const BUILTIN = new Set(['effect', 'get', 'has', 'inject', 'on', 'once', 'emit', 'emitAsync', 'waterfall', 'bail', 'serial']);

function fakeDocument() {
  const appended = [];
  return {
    appended,
    createElement: () => ({ setAttribute() {}, remove() {}, textContent: '' }),
    head: { append: (node) => appended.push(node) },
  };
}

/**
 * Cordis-shaped context: `services` are reachable through `get`, and through a
 * property read only when their name is listed in `injected`.
 */
function fakeContext({ services, injected }) {
  const calls = { overrides: [], tabs: [], locales: [] };
  const impl = {
    get: (name) => services[name],
    has: (name) => name in services,
    inject: (names, callback) => {
      callback(impl);
      return () => {};
    },
    on: () => () => {},
    effect: (fn) => {
      const dispose = fn();
      return typeof dispose === 'function' ? dispose : () => {};
    },
  };
  const allowed = new Set([...BUILTIN, ...injected]);
  for (const name of injected) {
    if (name in services) impl[name] = services[name];
  }
  const ctx = new Proxy(impl, {
    get(target, property, receiver) {
      if (typeof property === 'symbol' || allowed.has(property)) return Reflect.get(target, property, receiver);
      throw new Error(`cannot get property "${String(property)}" without inject`);
    },
  });
  return { ctx, calls };
}

async function loadClient() {
  let spec = null;
  globalThis.window = { __ModuleLoader__: { load: (loaded) => { spec = loaded; } } };
  await import(pathToFileURL(join(ROOT, 'client.js')).href);
  assert.ok(spec, 'client.js не позвал window.__ModuleLoader__.load');
  assert.equal(spec.id, 'dsh-style-fingerprint');
  const require = (name) => {
    if (name === 'react') {
      return {
        createElement: () => null,
        useCallback: (fn) => fn,
        useEffect: () => {},
        useMemo: (fn) => fn(),
        useRef: (value) => ({ current: value }),
        useState: (value) => [value, () => {}],
      };
    }
    throw new Error(`no module ${name}`);
  };
  return spec.factory(require);
}

/** Services the page really has; `theme` is present but not injected. */
function pageServices() {
  const calls = { overrides: [], tabs: [], locales: [] };
  const services = {
    locale: {
      register: (ns, lang) => calls.locales.push(`${ns}/${lang}`),
      bind: () => (key) => key,
    },
    slots: {
      inject: (_name, mount) => mount(),
      register: (spec) => calls.tabs.push(spec),
    },
    theme: {
      overrideTokens: (source, tokens) => calls.overrides.push({ source, tokens }),
    },
  };
  return { services, calls };
}

const client = await loadClient();

test('half applies on a context whose inject omits theme', () => {
  const { services, calls } = pageServices();
  const { ctx } = fakeContext({ services, injected: client.inject });
  globalThis.document = fakeDocument();

  assert.doesNotThrow(() => client.apply(ctx), 'apply упал на чтении необъявленной службы');
  assert.equal(calls.overrides.length, 1, 'слой токенов темы не зарегистрирован');
  assert.equal(calls.overrides[0].source, 'dsh-style-fingerprint');
  assert.deepEqual(Object.keys(calls.overrides[0].tokens).sort(), ['--dsw-style-fp-fail', '--dsw-style-fp-pass', '--dsw-style-fp-warn']);
  assert.equal(calls.locales.length, 2, 'словари ru и en не зарегистрированы');
  assert.equal(calls.tabs.length, 1, 'вкладка в settings.plugins.tab не зарегистрирована');
  assert.equal(calls.tabs[0].name, 'settings.plugins.tab');
  assert.equal(calls.tabs[0].id, 'style-fingerprint');
});

test('half applies without a theme service at all', () => {
  const { services, calls } = pageServices();
  delete services.theme;
  const { ctx } = fakeContext({ services, injected: client.inject });
  globalThis.document = fakeDocument();

  assert.doesNotThrow(() => client.apply(ctx));
  assert.equal(calls.overrides.length, 0);
  assert.equal(calls.tabs.length, 1);
});
