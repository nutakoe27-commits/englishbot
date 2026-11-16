// Authenticated pronunciation must preserve the mobile tap and discard stale audio.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const esbuild = require('../miniapp/node_modules/esbuild');
const code = esbuild.transformSync(fs.readFileSync(require.resolve('../miniapp/src/tts.ts'), 'utf8'),
  { loader: 'ts', format: 'cjs', target: 'es2020' }).code;

function fixture() {
  const calls = [], requests = [], sounds = [];
  class AudioContext {
    destination = {};
    resume() { calls.push('resume'); return Promise.resolve(); }
    async decodeAudioData(data) { calls.push('decode'); return data; }
    createBufferSource() {
      const source = { connect() {}, disconnect() {}, start() { sounds.push(source.buffer); }, stop() { calls.push('stop'); } };
      return source;
    }
  }
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, URLSearchParams, AudioContext,
    require(name) { return name === './auth' ? { API_BASE: 'https://example.invalid' } : { initData: 'synthetic-session' }; },
    fetch(url) { calls.push('fetch'); return new Promise(resolve => requests.push({ url, resolve })); },
  });
  return { ...module.exports, calls, requests, sounds };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = marker => ({ ok: true, arrayBuffer: async () => marker });

test('word audio resumes synchronously before network and carries the lesson context', async () => {
  const f = fixture();
  f.playWord(' hello ', 'first-lesson');
  assert.deepEqual(f.calls, ['resume', 'fetch']);
  const url = new URL(f.requests[0].url);
  assert.equal(url.searchParams.get('text'), 'hello');
  assert.equal(url.searchParams.get('learning_lesson_id'), 'first-lesson');
  assert.equal(url.searchParams.get('init_data'), 'synthetic-session');
  f.requests[0].resolve(response('hello audio'));
  await flush();
  assert.deepEqual(f.sounds, ['hello audio']);
});

test('a late pronunciation cannot interrupt the latest card, and the next tap stops playback', async () => {
  const f = fixture();
  f.playWord('first'); f.playWord('second');
  f.requests[1].resolve(response('second'));
  await flush();
  f.requests[0].resolve(response('first'));
  await flush();
  assert.deepEqual(f.sounds, ['second']);
  f.playWord('third');
  assert(f.calls.includes('stop'));
  f.requests[2].resolve(response('third'));
  await flush();
  assert.deepEqual(f.sounds, ['second', 'third']);
});

test('denied access is never decoded or played', async () => {
  const f = fixture();
  f.playWord('hello');
  f.requests[0].resolve({ ok: false, status: 402 });
  await flush();
  assert.deepEqual(f.sounds, []);
  assert(!f.calls.includes('decode'));
});
