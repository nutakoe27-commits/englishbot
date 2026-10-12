// Execute the real TypeScript modules bundled by the repository's esbuild.
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const esbuild = require('../miniapp/node_modules/esbuild');
const API = 'https://api.example.invalid';
const IID = '11111111-2222-3333-4444-555555555555';

function fixture(app = true, href = `https://app.example.invalid/?app=1&mt_iid=${IID}&utm_source=vk`) {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const requests = [];
  const window = {
    location: { href, search: new URL(href).search },
    matchMedia: () => ({ matches: app }),
    history: { state: null, replaceState: (_, __, url) => { window.location.href = new URL(url, window.location.href).href; } },
    fetch: async (input, init) => { requests.push({ input, init }); return new Response('{}', { status: 200 }); },
  };
  const globals = { window, document: { referrer: app ? 'android-app://ru.krichigindocs.englishbot.twa/' : '' },
    navigator: {}, localStorage: storage, sessionStorage: { ...storage, getItem: () => null },
    fetch: (...args) => window.fetch(...args),
    URL, URLSearchParams, Headers, Request, Response, console, setTimeout, clearTimeout };
  function load(name) {
    const entry = path.join(__dirname, '../miniapp/src', name);
    const result = esbuild.buildSync({ entryPoints: [entry], bundle: true, write: false, format: 'cjs', target: 'es2020',
      define: { 'import.meta.env': JSON.stringify({ VITE_API_BASE: API, VITE_MYTRACKER_ID: '', VITE_BOT_USERNAME: 'test_bot' }) } });
    const module = { exports: {} };
    vm.runInNewContext(result.outputFiles[0].text, { ...globals, module, exports: module.exports }, { filename: entry });
    return module.exports;
  }
  return { load, values, requests, window, storage };
}

test('captures the native UUID and removes it before analytics sees the URL', () => {
  const f = fixture();
  const mt = f.load('mtInstall.ts');
  mt.captureInstallId();
  assert.equal(mt.getInstallId(), IID);
  assert.equal(new URL(f.window.location.href).searchParams.has('mt_iid'), false);
  assert.equal(new URL(f.window.location.href).searchParams.get('utm_source'), 'vk');
});

test('scrubs the installation ID even when storage is unavailable', () => {
  const f = fixture();
  f.storage.setItem = () => { throw new Error('private browsing'); };
  f.load('mtInstall.ts').captureInstallId();
  assert.equal(new URL(f.window.location.href).searchParams.has('mt_iid'), false);
});

test('does not attach an old native installation to ordinary browser visits', async () => {
  const f = fixture(false, 'https://app.example.invalid/');
  f.values.set('et_mt_iid', IID);
  const mt = f.load('mtInstall.ts');
  assert.equal(mt.getInstallId(), '');
  await mt.linkInstall(7, API);
  assert.equal(f.requests.length, 0);
});

test('legacy permanent marker cannot suppress relinking; concurrent refreshes coalesce', async () => {
  const f = fixture();
  f.values.set('et_mt_iid', IID);
  f.values.set('et_mt_iid_sent', `7:${IID}`);
  const mt = f.load('mtInstall.ts');
  await Promise.all([mt.linkInstall(7, API), mt.linkInstall(7, API)]);
  assert.equal(f.requests.length, 1);
  await mt.linkInstall(7, API);
  assert.equal(f.requests.length, 2);
  assert.equal(JSON.parse(f.requests[0].init.body).instance_id, IID);
});

test('failed linking can be retried on the next refresh', async () => {
  const f = fixture();
  f.values.set('et_mt_iid', IID);
  let calls = 0;
  f.window.fetch = async () => { calls++; throw new Error('offline'); };
  const mt = f.load('mtInstall.ts');
  await mt.linkInstall(7, API);
  await mt.linkInstall(7, API);
  assert.equal(calls, 2);
});

test('API fetch includes JWT and native ID, preserving Request headers', async () => {
  const f = fixture();
  f.values.set('et_mt_iid', IID);
  f.values.set('englishbot_jwt', 'synthetic-jwt');
  f.load('auth.ts').installFetchAuth();
  await f.window.fetch(new Request(`${API}/api/srs/session/start`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Test': 'preserved' }, body: '{}' }));
  const headers = f.requests[0].init.headers;
  assert.equal(headers.get('Authorization'), 'Bearer synthetic-jwt');
  assert.equal(headers.get('X-MyTracker-Instance-ID'), IID);
  assert.equal(headers.get('Content-Type'), 'application/json');
  assert.equal(headers.get('X-Test'), 'preserved');
});

test('never leaks authentication or install ID to a lookalike host', async () => {
  const f = fixture();
  f.values.set('et_mt_iid', IID);
  f.values.set('englishbot_jwt', 'synthetic-jwt');
  f.load('auth.ts').installFetchAuth();
  await f.window.fetch(`${API}.untrusted.invalid/api/me`);
  assert.equal(f.requests[0].init, undefined);
});

test('browser API calls carry authentication without mobile attribution', async () => {
  const f = fixture(false, 'https://app.example.invalid/');
  f.values.set('et_mt_iid', IID);
  f.values.set('englishbot_jwt', 'synthetic-jwt');
  f.load('auth.ts').installFetchAuth();
  await f.window.fetch(`${API}/api/me`);
  const headers = f.requests[0].init.headers;
  assert.equal(headers.get('Authorization'), 'Bearer synthetic-jwt');
  assert.equal(headers.has('X-MyTracker-Instance-ID'), false);
});
