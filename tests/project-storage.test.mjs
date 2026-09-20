import test from 'node:test';
import assert from 'node:assert/strict';
import { createJiti } from 'jiti';

const storage = await createJiti(import.meta.url).import('../lib/project-storage.ts');

test('removing a workspace only removes the requested workspace from its project', () => {
  const first = storage.newProject('ЩУ-1', '2026-09-20T10:00:00.000Z', 'project-1');
  const second = storage.addWorkspace({ projects: [first] }, first.id, 'Линия 2', '2026-09-20T10:01:00.000Z', 'workspace-2');
  const removed = storage.removeWorkspace(second, first.id, 'workspace-2', '2026-09-20T10:02:00.000Z');

  assert.equal(removed.projects.length, 1);
  assert.equal(removed.projects[0].workspaces.length, 1);
  assert.equal(removed.projects[0].workspaces[0].id, first.workspaces[0].id);
  assert.equal(removed.projects[0].updatedAt, '2026-09-20T10:02:00.000Z');
});

test('removing a project leaves unrelated projects intact', () => {
  const first = storage.newProject('ЩУ-1', '2026-09-20T10:00:00.000Z', 'project-1');
  const second = storage.newProject('ЩУ-2', '2026-09-20T10:01:00.000Z', 'project-2');
  const removed = storage.removeProject({ projects: [first, second] }, first.id);

  assert.deepEqual(removed.projects.map((project) => project.id), [second.id]);
});

test('deleting a project clears drafts for all of its workspaces only', () => {
  const project = storage.newProject('ЩУ-1', '2026-09-20T10:00:00.000Z', 'project-1');
  const catalog = storage.addWorkspace({ projects: [project] }, project.id, 'Линия 2', '2026-09-20T10:01:00.000Z', 'workspace-2');
  const deletedKeys = [];
  const ownWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window');
  const originalWindow = globalThis.window;
  globalThis.window = { localStorage: { removeItem: (key) => deletedKeys.push(key) } };
  try {
    storage.deleteLocalProject('engineer-1', catalog.projects[0]);
  } finally {
    if (ownWindow) globalThis.window = originalWindow;
    else delete globalThis.window;
  }

  assert.deepEqual(deletedKeys, catalog.projects[0].workspaces.map((workspace) =>
    storage.workspaceStorageKey('engineer-1', catalog.projects[0].id, workspace.id),
  ));
});
