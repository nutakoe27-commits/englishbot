// Requires Playwright and Chromium. With Vite running on :5173:
// VITE_API_BASE=http://localhost:8082 npm --prefix miniapp run dev
// node --test scripts/test_grammar_browser.cjs
const assert = require('node:assert/strict');
const { test, before, after } = require('node:test');
const { chromium } = require('playwright');
const URL_BASE = process.env.GRAMMAR_UI_URL || 'http://localhost:5173';
let browser;
before(async () => { browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}), headless: true,
}); });
after(async () => { await browser?.close(); });

const exercises = label => Array.from({ length: 4 }, (_, i) => ({ id: `${label}-${i}`,
  type: 'mcq', category: 'tense', prompt: `${label}: She ___ happy (${i + 1}).`,
  choices: ['am', 'is', 'are', 'be'], correct: 'is', explanation: 'She is.' }));
async function until(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  assert(check(), 'Expected network operation did not occur');
}
async function fixture(t, handlers = {}) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const calls = [];
  const errors = [];
  const json = (route, body, status = 200) => route.fulfill({ status,
    contentType: 'application/json', headers: { 'access-control-allow-origin': URL_BASE,
      'access-control-allow-methods': 'GET, POST, OPTIONS', 'access-control-allow-headers': '*' }, body: JSON.stringify(body) });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname)) return route.abort();
    if (!url.pathname.startsWith('/api/grammar/')) return route.continue();
    if (route.request().method() === 'OPTIONS') return json(route, {});
    const key = url.pathname.slice('/api/grammar'.length);
    const body = route.request().postDataJSON() || {};
    calls.push({ key, body });
    const n = calls.filter(call => call.key === key).length;
    if (handlers[key]) return handlers[key]({ route, body, n, json });
    if (key === '/topics') return json(route, { levels: { A1: ['present', 'articles'].map(key => ({
      key, title_ru: key, category: 'tense', status: 'available', best_score: 0,
    })) } });
    if (key === '/lesson') return json(route, { topic_key: body.topic_key, title_ru: body.topic_key,
      theory: 'She is happy. — Она счастлива.', exercises: [], session_id: '' });
    if (key === '/generate' || key === '/lesson/exercises') return json(route, {
      session_id: `prepared-${body.topic_key || 'weak'}`, exercises: exercises(body.topic_key || 'weak'),
    });
    if (key === '/start') return json(route, { session_id: '101' });
    if (key === '/lesson/finish') return json(route, { passed: true, score: 100, best_score: 100, next_topic_key: null });
    return json(route, { ok: true });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'Uncaught React errors'));
  await page.goto(`${URL_BASE}/tests/grammar.html`);
  await page.getByRole('button', { name: /Проверить себя/ }).waitFor();
  return { page, calls, count: key => calls.filter(call => call.key === key).length };
}
async function lesson(page, topic = 'present') {
  await page.getByRole('button', { name: /Учить правила/ }).click();
  await page.locator('.grm-topic-row').filter({ hasText: topic }).click();
  await page.locator('.grm-theory__title').waitFor();
}
async function complete(page) {
  for (let i = 0; i < 4; i++) {
    await page.getByRole('button', { name: 'is', exact: true }).click();
    await page.getByRole('button', { name: i === 3 ? 'Итоги' : 'Дальше →', exact: true }).click();
  }
  await page.locator('.grm-summary').waitFor();
}

test('failed fetch, manual retry, and completion use exactly one active session', async t => {
  const f = await fixture(t, { '/generate': ({ route, n, json }) => n === 1 ? route.abort('failed')
    : json(route, { session_id: 'prepared-retry', exercises: exercises('retry') }) });
  await f.page.getByRole('button', { name: /Проверить себя/ }).click();
  await f.page.getByText(/Соединение прервалось/).waitFor();
  assert.equal(f.count('/start'), 0);
  await f.page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await f.page.locator('.grm-prompt').waitFor();
  assert.equal(f.count('/start'), 1);
  await complete(f.page);
  await until(() => f.count('/finish') === 1);
  const finish = f.calls.find(call => call.key === '/finish').body;
  assert.equal(finish.session_id, '101');
  assert.equal(finish.results.length, 4);
  assert(finish.results.every(answer => answer.is_correct));
});

test('background lesson preparation creates no practice until the user starts it', async t => {
  const f = await fixture(t);
  await lesson(f.page);
  await f.page.getByRole('button', { name: 'К практике →', exact: true }).waitFor();
  assert.equal(f.count('/start'), 0);
  await f.page.getByRole('button', { name: 'Назад', exact: true }).click();
  await until(() => f.count('/abandon') === 1);
  assert.equal(f.calls.find(call => call.key === '/abandon').body.session_id, 'prepared-present');
  assert.equal(f.count('/start'), 0);
});

test('a late cancelled lesson response cannot replace the next lesson', async t => {
  let release;
  let entered = false;
  const gate = new Promise(resolve => { release = resolve; });
  const f = await fixture(t, { '/lesson/exercises': async ({ route, body, json }) => {
    if (body.topic_key === 'present') { entered = true; await gate; }
    await json(route, { session_id: `prepared-${body.topic_key}`, exercises: exercises(body.topic_key) }).catch(() => {});
  } });
  await lesson(f.page);
  await until(() => entered);
  await f.page.getByRole('button', { name: 'Назад', exact: true }).click();
  await f.page.locator('.grm-topic-row').filter({ hasText: 'articles' }).click();
  await f.page.getByRole('button', { name: 'К практике →', exact: true }).waitFor();
  release();
  await f.page.getByRole('button', { name: 'К практике →', exact: true }).click();
  await f.page.locator('.grm-prompt').waitFor();
  assert.match(await f.page.locator('.grm-prompt').innerText(), /^articles:/);
  assert.deepEqual(f.calls.filter(call => call.key === '/start').map(call => call.body.session_id), ['prepared-articles']);
});

test('lost activation response retries the same preparation without regenerating', async t => {
  const f = await fixture(t, { '/start': ({ route, n, json }) => n === 1 ? route.abort('failed') : json(route, { session_id: '101' }) });
  await f.page.getByRole('button', { name: /Проверить себя/ }).click();
  await f.page.locator('.grm-prompt').waitFor();
  assert.equal(f.count('/generate'), 1);
  assert.deepEqual(f.calls.filter(call => call.key === '/start').map(call => call.body.session_id), ['prepared-weak', 'prepared-weak']);
});

test('failed completion remains visible; manual retry preserves the original payload', async t => {
  const f = await fixture(t, { '/finish': ({ route, n, json }) => json(route,
    n <= 3 ? { detail: 'temporary_failure' } : { ok: true }, n <= 3 ? 503 : 200) });
  await f.page.getByRole('button', { name: /Проверить себя/ }).click();
  await f.page.locator('.grm-prompt').waitFor();
  await complete(f.page);
  await f.page.getByRole('alert').waitFor();
  assert.equal(f.count('/finish'), 3);
  await f.page.getByRole('button', { name: 'Сохранить ещё раз', exact: true }).click();
  await f.page.getByRole('alert').waitFor({ state: 'hidden' });
  await until(() => f.count('/finish') === 4);
  const bodies = f.calls.filter(call => call.key === '/finish').map(call => JSON.stringify(call.body));
  assert.equal(new Set(bodies).size, 1);
});

test('unmounting active exercises abandons the persisted session', async t => {
  const f = await fixture(t);
  await f.page.getByRole('button', { name: /Проверить себя/ }).click();
  await f.page.locator('.grm-prompt').waitFor();
  await f.page.getByRole('button', { name: 'Unmount grammar', exact: true }).click();
  await until(() => f.count('/abandon') === 1);
  assert.equal(f.calls.find(call => call.key === '/abandon').body.session_id, '101');
});

test('cancelling a pending activation abandons its key and never restores the screen', async t => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const f = await fixture(t, { '/start': async ({ route, json }) => {
    await gate; await json(route, { session_id: '101' }).catch(() => {});
  } });
  await f.page.getByRole('button', { name: /Проверить себя/ }).click();
  await until(() => f.count('/start') === 1);
  await f.page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await until(() => f.count('/abandon') === 1);
  release();
  await f.page.getByRole('button', { name: /Проверить себя/ }).waitFor();
  assert.equal(await f.page.locator('.grm-exercise').count(), 0);
  assert.equal(f.calls.find(call => call.key === '/abandon').body.session_id, 'prepared-weak');
});
