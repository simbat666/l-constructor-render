import test from 'node:test';
import assert from 'node:assert/strict';
import { createJiti } from 'jiti';

const auth = await createJiti(import.meta.url).import('../lib/auth.ts');
const names = ['L_AUTH_SESSION_SECRET', 'L_AUTH_USER_1', 'L_AUTH_USER_2', 'L_AUTH_PASSWORD_HASH_1', 'L_AUTH_PASSWORD_HASH_2'];
const original = Object.fromEntries(names.map((key) => [key, process.env[key]]));
function configure() {
  process.env.L_AUTH_SESSION_SECRET = 'test-session-secret-with-at-least-32-characters';
  process.env.L_AUTH_USER_1 = 'engineer-1'; process.env.L_AUTH_USER_2 = 'engineer-2';
  process.env.L_AUTH_PASSWORD_HASH_1 = auth.passwordHash('correct-horse-battery-staple', 'fixture-salt-one');
  process.env.L_AUTH_PASSWORD_HASH_2 = auth.passwordHash('another-correct-horse-battery', 'fixture-salt-two');
}
test('only configured users can create and present a signed session', () => {
  configure(); const request = new Request('http://localhost/api/auth/login');
  assert.equal(auth.credentialsUser('ENGINEER-1', 'correct-horse-battery-staple'), 'engineer-1');
  assert.equal(auth.credentialsUser('engineer-1', 'wrong-password-at-least-12'), null);
  const cookie = auth.sessionCookie('engineer-1', request).split(';')[0];
  assert.equal(auth.sessionUser(new Request('http://localhost/api/cad/calculate', { headers: { cookie } })), 'engineer-1');
  assert.equal(auth.requireSession(new Request('http://localhost/api/cad/calculate', { headers: { cookie } })), null);
  assert.equal(auth.requireSession(new Request('http://localhost/api/cad/calculate'))?.status, 401);
});
test.after(() => { for (const [key, value] of Object.entries(original)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
