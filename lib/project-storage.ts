import type { QuestionnaireState } from '@/lib/engineering-client';

const LEGACY_STORAGE_VERSION = 1;
const CATALOG_STORAGE_VERSION = 2;
const MAX_BLOCKS = 40;
const MAX_NAME_LENGTH = 80;

export type StoredMotor = { id: string; tag: string; cell: number; state: QuestionnaireState };

/** The field saved inside one workspace. It contains neither passwords nor CAD files. */
export type StoredProject = { motors: StoredMotor[]; activeMotorId: string | null; sheetOpen: boolean };

export type LocalWorkspace = { id: string; name: string; createdAt: string; updatedAt: string };

export type LocalProject = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  workspaces: LocalWorkspace[];
};

export type LocalProjectCatalog = { projects: LocalProject[] };

const emptyProject = (): StoredProject => ({ motors: [], activeMotorId: null, sheetOpen: false });
const emptyCatalog = (): LocalProjectCatalog => ({ projects: [] });

function accountName(user: string | null) {
  return user && /^[a-z0-9._-]{3,32}$/i.test(user) ? user.toLowerCase() : 'local';
}

function validName(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= MAX_NAME_LENGTH;
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value));
}

function stringRecord(value: unknown): Record<string, string> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  return entries.every(([key, item]) => typeof key === 'string' && typeof item === 'string')
    ? Object.fromEntries(entries) as Record<string, string>
    : null;
}

function booleanRecord(value: unknown): Record<string, boolean> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  return entries.every(([key, item]) => typeof key === 'string' && typeof item === 'boolean')
    ? Object.fromEntries(entries) as Record<string, boolean>
    : null;
}

function questionnaire(value: unknown): QuestionnaireState | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const selected = stringRecord(source.selected);
  const inputs = stringRecord(source.inputs);
  const checks = booleanRecord(source.checks);
  const uploads = stringRecord(source.uploads);
  return selected && inputs && checks && uploads ? { selected, inputs, checks, uploads } : null;
}

function motor(value: unknown): StoredMotor | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const state = questionnaire(source.state);
  const cell = source.cell;
  if (
    typeof source.id !== 'string' || !source.id || source.id.length > 128 ||
    typeof source.tag !== 'string' || source.tag.length > 128 ||
    typeof cell !== 'number' || !Number.isInteger(cell) || cell < 0 || cell >= MAX_BLOCKS || !state
  ) return null;
  return { id: source.id, tag: source.tag, cell, state };
}

function workspace(value: unknown): LocalWorkspace | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  if (
    typeof source.id !== 'string' || !source.id || source.id.length > 128 ||
    !validName(source.name) || !validDate(source.createdAt) || !validDate(source.updatedAt)
  ) return null;
  return { id: source.id, name: source.name.trim(), createdAt: source.createdAt, updatedAt: source.updatedAt };
}

function localProject(value: unknown): LocalProject | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  if (
    typeof source.id !== 'string' || !source.id || source.id.length > 128 ||
    !validName(source.name) || !validDate(source.createdAt) || !validDate(source.updatedAt) || !Array.isArray(source.workspaces)
  ) return null;
  const workspaces = source.workspaces.map(workspace);
  const ids = new Set(workspaces.filter(Boolean).map((item) => item!.id));
  if (workspaces.some((item) => !item) || ids.size !== workspaces.length) return null;
  return { id: source.id, name: source.name.trim(), createdAt: source.createdAt, updatedAt: source.updatedAt, workspaces: workspaces as LocalWorkspace[] };
}

/** Old single-field key, retained solely to migrate an existing local draft. */
export function projectStorageKey(user: string | null) {
  return `l-constructor:project:v${LEGACY_STORAGE_VERSION}:${accountName(user)}`;
}

export function catalogStorageKey(user: string | null) {
  return `l-constructor:projects:v${CATALOG_STORAGE_VERSION}:${accountName(user)}`;
}

export function workspaceStorageKey(user: string | null, projectId: string, workspaceId: string) {
  return `l-constructor:workspace:v${CATALOG_STORAGE_VERSION}:${accountName(user)}:${projectId}:${workspaceId}`;
}

export function decodeProject(raw: string | null): StoredProject {
  if (!raw) return emptyProject();
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || parsed.version !== LEGACY_STORAGE_VERSION || !Array.isArray(parsed.motors)) return emptyProject();
    const motors = parsed.motors.map(motor);
    if (motors.some((item) => !item) || motors.length > MAX_BLOCKS) return emptyProject();
    const validMotors = motors as StoredMotor[];
    const ids = new Set(validMotors.map((item) => item.id));
    const cells = new Set(validMotors.map((item) => item.cell));
    if (ids.size !== validMotors.length || cells.size !== validMotors.length) return emptyProject();
    const activeMotorId = typeof parsed.activeMotorId === 'string' && ids.has(parsed.activeMotorId) ? parsed.activeMotorId : null;
    return { motors: validMotors, activeMotorId, sheetOpen: Boolean(parsed.sheetOpen) && Boolean(activeMotorId) };
  } catch { return emptyProject(); }
}

export function encodeProject(project: StoredProject) {
  return JSON.stringify({ version: LEGACY_STORAGE_VERSION, ...project });
}

export function decodeCatalog(raw: string | null): LocalProjectCatalog {
  if (!raw) return emptyCatalog();
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || parsed.version !== CATALOG_STORAGE_VERSION || !Array.isArray(parsed.projects)) return emptyCatalog();
    const projects = parsed.projects.map(localProject);
    const ids = new Set(projects.filter(Boolean).map((item) => item!.id));
    if (projects.some((item) => !item) || ids.size !== projects.length) return emptyCatalog();
    return { projects: projects as LocalProject[] };
  } catch { return emptyCatalog(); }
}

export function encodeCatalog(catalog: LocalProjectCatalog) {
  return JSON.stringify({ version: CATALOG_STORAGE_VERSION, ...catalog });
}

function idPart(prefix: string) {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID().replaceAll('-', '')
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random.slice(0, 24)}`;
}

export function newProject(name: string, now = new Date().toISOString(), id = idPart('project')): LocalProject {
  const workspaceId = idPart('workspace');
  return {
    id,
    name: name.trim() || 'Новый проект',
    createdAt: now,
    updatedAt: now,
    workspaces: [{ id: workspaceId, name: 'Рабочая область 1', createdAt: now, updatedAt: now }],
  };
}

export function addWorkspace(catalog: LocalProjectCatalog, projectId: string, name: string, now = new Date().toISOString(), id = idPart('workspace')): LocalProjectCatalog {
  return {
    projects: catalog.projects.map((project) => project.id !== projectId ? project : {
      ...project,
      updatedAt: now,
      workspaces: [...project.workspaces, { id, name: name.trim() || `Рабочая область ${project.workspaces.length + 1}`, createdAt: now, updatedAt: now }],
    }),
  };
}

function legacyCatalog(raw: string | null, now = new Date().toISOString()) {
  if (!raw) return null;
  const draft = decodeProject(raw);
  const projectId = idPart('project');
  const workspaceId = idPart('workspace');
  return {
    catalog: { projects: [{ id: projectId, name: 'Текущий проект', createdAt: now, updatedAt: now, workspaces: [{ id: workspaceId, name: 'Рабочая область 1', createdAt: now, updatedAt: now }] }] },
    projectId,
    workspaceId,
    draft,
  };
}

/** Load the dashboard catalogue and migrate the old one-field draft once, if it exists. */
export function loadLocalCatalog(user: string | null) {
  if (typeof window === 'undefined') return emptyCatalog();
  try {
    const catalogRaw = window.localStorage.getItem(catalogStorageKey(user));
    if (catalogRaw) return decodeCatalog(catalogRaw);
    const migrated = legacyCatalog(window.localStorage.getItem(projectStorageKey(user)));
    if (!migrated) return emptyCatalog();
    window.localStorage.setItem(catalogStorageKey(user), encodeCatalog(migrated.catalog));
    window.localStorage.setItem(workspaceStorageKey(user, migrated.projectId, migrated.workspaceId), encodeProject(migrated.draft));
    return migrated.catalog;
  } catch { return emptyCatalog(); }
}

export function saveLocalCatalog(user: string | null, catalog: LocalProjectCatalog) {
  if (typeof window === 'undefined') return;
  try { window.localStorage.setItem(catalogStorageKey(user), encodeCatalog(catalog)); } catch {
    // A private-mode or full storage quota must not break the engineering UI.
  }
}

export function loadLocalWorkspace(user: string | null, projectId: string, workspaceId: string) {
  if (typeof window === 'undefined') return emptyProject();
  try { return decodeProject(window.localStorage.getItem(workspaceStorageKey(user, projectId, workspaceId))); } catch { return emptyProject(); }
}

export function saveLocalWorkspace(user: string | null, projectId: string, workspaceId: string, project: StoredProject) {
  if (typeof window === 'undefined') return;
  try { window.localStorage.setItem(workspaceStorageKey(user, projectId, workspaceId), encodeProject(project)); } catch {
    // A private-mode or full storage quota must not break the engineering UI.
  }
}
