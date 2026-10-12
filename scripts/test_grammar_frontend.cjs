// Run: node --test scripts/test_grammar_frontend.cjs (after npm ci in miniapp).
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const esbuild = require('../miniapp/node_modules/esbuild');
const bundle = esbuild.buildSync({ entryPoints: [require.resolve('../miniapp/src/grammarRequests.ts')],
  bundle: true, write: false, format: 'cjs', target: 'es2020' }).outputFiles[0].text;

function fixture(respond) {
  const calls = [];
  const module = { exports: {} };
  vm.runInNewContext(bundle, { module, exports: module.exports, AbortController, Error,
    setTimeout: callback => setTimeout(callback, 0),
    fetch: async (url, init) => { calls.push({ url, init }); return respond(calls.length, init); } });
  return { ...module.exports, calls };
}

test('cancelled and previous rounds cannot own a late response', () => {
  const { GrammarRound } = fixture();
  const round = new GrammarRound();
  const first = round.begin();
  const second = round.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(round.owns(first), false);
  assert.equal(round.owns(second), true);
  round.cancel();
  assert.equal(round.owns(second), false);
  assert.equal(round.current(), null);
});

test('network retries reuse the exact first completion payload', async () => {
  const f = fixture(n => { if (n < 3) throw new TypeError('Failed to fetch'); return new Response('{"ok":true}'); });
  const body = { session_id: '17', duration_sec: 90 };
  const pending = f.grammarRequest('/finish', body, { retry: true, keepalive: true });
  body.duration_sec = 999;
  assert.equal((await pending).ok, true);
  assert.equal(f.calls.length, 3);
  assert(f.calls.every(call => call.init.body === '{"session_id":"17","duration_sec":90}' && call.init.keepalive));
});

test('server errors retry; generation uses a single attempt by default', async () => {
  const f = fixture(n => new Response(n < 3 ? '{"detail":"llm_unavailable"}' : '{"session_id":"17"}', { status: n < 3 ? 502 : 200 }));
  assert.equal((await f.grammarRequest('/start', {}, { retry: true })).session_id, '17');
  assert.equal(f.calls.length, 3);
  const generation = fixture(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(generation.grammarRequest('/generate', {}), /Соединение прервалось/);
  assert.equal(generation.calls.length, 1);
});

test('cancellation never retries a request', async () => {
  const controller = new AbortController();
  const f = fixture(() => { controller.abort(); throw Object.assign(new Error('aborted'), { name: 'AbortError' }); });
  await assert.rejects(f.grammarRequest('/start', {}, { signal: controller.signal, retry: true }), /aborted/);
  assert.equal(f.calls.length, 1);
});

test('quota failure is retained and never retried', async () => {
  const f = fixture(() => new Response('{"detail":"limit_reached"}', { status: 402 }));
  await assert.rejects(f.grammarRequest('/start', {}, { retry: true }), error =>
    error instanceof f.GrammarRequestError && error.status === 402 && error.detail === 'limit_reached');
  assert.equal(f.calls.length, 1);
});

test('timeout and expired preparation are shown as actionable messages', async () => {
  for (const [status, detail, message] of [[504, 'generation_timeout', /слишком много времени/],
    [410, 'grammar_preparation_expired', /Подготовь их заново/]]) {
    const f = fixture(() => new Response(JSON.stringify({ detail }), { status }));
    await assert.rejects(f.grammarRequest('/generate', {}), message);
  }
});
