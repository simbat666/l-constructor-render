/** Exercise the real SSR bundle after a clean install and production build. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import test from 'node:test';

await test('production home renders with all runtime dependencies installed', { timeout: 45000 }, async t => {
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const child = spawn(resolve('node_modules/.bin/vinext'), ['start', '--hostname', '127.0.0.1', '--port', String(port)], {
    env: { ...process.env, NODE_ENV: 'production' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stdout.on('data', data => { logs = (logs + data).slice(-16000); });
  child.stderr.on('data', data => { logs = (logs + data).slice(-16000); });
  t.after(async () => {
    if (child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill('SIGTERM');
      await exited;
    }
  });
  const url = `http://127.0.0.1:${port}`;
  let response;
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    assert.equal(child.exitCode, null, logs);
    try { response = await fetch(url, { signal: AbortSignal.timeout(3000) }); break; }
    catch { await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  assert.ok(response, logs);
  assert.equal(response.status, 200, logs);
  const html = await response.text();
  assert.ok(!html.includes('id="__next_error__"'), 'SSR returned the error shell');
  assert.ok(html.includes('Проверяем доступ'), 'SSR must render the application loading screen');
  const script = /<script[^>]+src="([^"]+)"/.exec(html)?.[1];
  assert.ok(script, 'Application script was not emitted');
  assert.equal((await fetch(new URL(script, url))).status, 200);
});
