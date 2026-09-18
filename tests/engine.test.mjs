import test from 'node:test';
import assert from 'node:assert/strict';
import { createJiti } from 'jiti';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const jiti = createJiti(import.meta.url, { alias: { '@': root } });
const { POST: proxyPost } = await jiti.import('../app/api/cad/[...path]/route.ts');

test('calculation proxy drains large upstream JSON before returning to a slow client', async () => {
  const originalFetch = globalThis.fetch;
  const bytes = new TextEncoder().encode(JSON.stringify({ text: 'Тест'.repeat(150000) }));
  let closed = false;
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.subarray(0, 300000));
      setTimeout(() => { controller.enqueue(bytes.subarray(300000)); closed = true; controller.close(); }, 10);
    },
  }), { headers: { 'Content-Type': 'application/json', 'Content-Length': String(bytes.length) } });
  try {
    const response = await proxyPost(new Request('http://localhost/api/cad/calculate', { method: 'POST', body: '{"motors":[]}' }));
    assert.equal(closed, true);
    assert.equal(response.status, 200);
    assert.equal((await response.arrayBuffer()).byteLength, bytes.length);
  } finally { globalThis.fetch = originalFetch; }
});
const {
  createClientId,
  cloneQuestionnaireState,
  removeBlockById,
} = await jiti.import('../lib/client-id.ts');
const {
  addWorkspace,
  catalogStorageKey,
  decodeCatalog,
  decodeProject,
  encodeCatalog,
  encodeProject,
  newProject,
  projectStorageKey,
  workspaceStorageKey,
} = await jiti.import('../lib/project-storage.ts');

test('copied questionnaire state is independent from the source block', () => {
  const source = {
    selected: { 'Ques 1:Способ пуска / управления двигателем:list': '1.2' },
    inputs: { 'Ques 1:6.3': '3.2' }, checks: {}, uploads: {},
  };
  const copy = cloneQuestionnaireState(source);
  copy.inputs['Ques 1:6.3'] = '6.4';
  assert.equal(source.inputs['Ques 1:6.3'], '3.2');
  assert.equal(copy.inputs['Ques 1:6.3'], '6.4');
});

test('deleting a block removes only the requested instance', () => {
  const source = [
    { id: 'one', tag: 'M1' },
    { id: 'two', tag: 'M2' },
  ];
  const result = removeBlockById(source, 'one');
  assert.deepEqual(result, [{ id: 'two', tag: 'M2' }]);
  assert.deepEqual(source, [
    { id: 'one', tag: 'M1' },
    { id: 'two', tag: 'M2' },
  ]);
});

test('block id generation works on HTTP where crypto.randomUUID is unavailable', () => {
  const first = createClientId(null);
  const second = createClientId(null);
  assert.match(first, /^local-[a-z0-9]+-[a-z0-9]+$/);
  assert.notEqual(first, second);
  assert.equal(createClientId(() => 'native-id'), 'native-id');
});

test('workspace draft restores valid blocks separately for each account', () => {
  const project = {
    motors: [{
      id: 'motor-one', tag: 'М1', cell: 5,
      state: { selected: { start: 'direct' }, inputs: { power: '5.5' }, checks: {}, uploads: {} },
    }],
    activeMotorId: 'motor-one', sheetOpen: true,
  };
  assert.deepEqual(decodeProject(encodeProject(project)), project);
  assert.equal(projectStorageKey('Engineer-1'), 'l-constructor:project:v1:engineer-1');
  assert.equal(projectStorageKey('engineer-2'), 'l-constructor:project:v1:engineer-2');
  assert.deepEqual(decodeProject('{"version":1,"motors":[{"id":"same","tag":"M1","cell":0,"state":{"selected":{},"inputs":{},"checks":{},"uploads":{}}},{"id":"same","tag":"M2","cell":1,"state":{"selected":{},"inputs":{},"checks":{},"uploads":{}}}]}'), { motors: [], activeMotorId: null, sheetOpen: false });
});

test('local account catalog separates projects and their workspaces', () => {
  const first = newProject('ПС Северная', '2026-09-18T00:00:00.000Z', 'project-north');
  const catalog = addWorkspace(
    { projects: [first] },
    first.id,
    'Шкаф приточной установки',
    '2026-09-18T01:00:00.000Z',
    'workspace-supply',
  );
  assert.equal(catalog.projects[0].workspaces.length, 2);
  assert.equal(catalog.projects[0].workspaces[1].name, 'Шкаф приточной установки');
  assert.deepEqual(decodeCatalog(encodeCatalog(catalog)), catalog);
  assert.equal(catalogStorageKey('Engineer-1'), 'l-constructor:projects:v2:engineer-1');
  assert.equal(workspaceStorageKey('engineer-1', 'project-north', 'workspace-supply'), 'l-constructor:workspace:v2:engineer-1:project-north:workspace-supply');
});
