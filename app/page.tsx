'use client';

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  CircleCheck,
  Copy,
  Download,
  FileCog,
  FolderKanban,
  GripVertical,
  Layers3,
  LoaderCircle,
  LogOut,
  Plus,
  Trash2,
  TriangleAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  initialQuestionnaireState,
  numericInputRule,
  useEngineering,
  type QuestionnaireState,
  type VisibleGroup,
  type Channel,
} from '@/lib/engineering-client';
import {
  cloneQuestionnaireState,
  createClientId,
  removeBlockById,
} from '@/lib/client-id';
import {
  addWorkspace,
  deleteLocalProject,
  deleteLocalWorkspace,
  loadLocalCatalog,
  loadLocalWorkspace,
  newProject,
  removeProject,
  removeWorkspace,
  saveLocalCatalog,
  saveLocalWorkspace,
  type LocalProject,
  type LocalWorkspace,
  type StoredMotor,
} from '@/lib/project-storage';

const COLUMNS = 8;
const ROWS = 5;
const CELLS = COLUMNS * ROWS;
const START_CELL = 4 + COLUMNS * 2;
const DXF_API_URL = '/api/cad';
const sheetTitles: Record<string, string> = {
  'Ques 1': 'Основной опросник',
  'Ques 2': 'Дополнительные параметры',
  'Ques 3': 'Ручной выбор ЧП',
  'Ques 4': 'Резерв опросника',
};
type DiagramRow = Record<string, any>;
type OptionalRow = { row: DiagramRow; sourceOrder: string };
type DecisionTraceEntry = {
  id: string;
  title: string;
  detail: string;
  source?: string;
};
type DecisionTraceData = {
  inputs: DecisionTraceEntry[];
  rules: DecisionTraceEntry[];
  outputs: DecisionTraceEntry[];
};
type DrawingDownload = { kind: string; title: string; downloadPath: string };
type Generation =
  | { status: 'idle' }
  | { status: 'working' }
  | {
      status: 'success';
      message: string;
      downloadUrl: string;
      drawings: DrawingDownload[];
    }
  | { status: 'error'; message: string };
type MotorBlock = {
  id: string;
  tag: string;
  cell: number;
  state: QuestionnaireState;
  mainSchemeCode?: string;
  requested: boolean;
  generation: Generation;
};

function blockCountLabel(count: number) {
  const ending =
    count % 100 >= 11 && count % 100 <= 14
      ? 'блоков'
      : count % 10 === 1
        ? 'блок'
        : count % 10 >= 2 && count % 10 <= 4
          ? 'блока'
          : 'блоков';
  return `${String(count).padStart(2, '0')} ${ending}`;
}

function nextFreeCell(
  motors: Array<Pick<MotorBlock, 'cell'>>,
  preferredCell: number,
) {
  for (let offset = 0; offset < CELLS; offset++) {
    const cell = (preferredCell + offset) % CELLS;
    if (!motors.some((motor) => motor.cell === cell)) return cell;
  }
  return null;
}

function LoadingScreen() {
  return <main className="l-rainbow grid min-h-screen place-items-center p-6 text-sm text-[#747480]">Проверяем доступ…</main>;
}

function LoginScreen({ onLogin }: { onLogin: (user: string) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setWorking(true);
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const result = await response.json() as { user?: string; error?: string };
      if (!response.ok || !result.user) throw new Error(result.error || 'Не удалось войти.');
      onLogin(result.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось войти.');
    } finally {
      setWorking(false);
    }
  };
  return <main className="l-rainbow grid min-h-screen place-items-center p-5">
    <form onSubmit={submit} className="w-full max-w-sm rounded-[28px] border border-white bg-white/85 p-7 shadow-[0_20px_60px_rgba(28,29,44,.14)] backdrop-blur">
      <div className="l-block-gradient grid size-11 place-items-center rounded-2xl text-lg font-black text-white">L</div>
      <h1 className="mt-5 text-2xl font-semibold tracking-[-.04em]">L Constructor</h1>
      <p className="mt-2 text-sm leading-6 text-[#747480]">Вход в закрытый инженерный стенд.</p>
      <label className="mt-6 block text-xs font-semibold text-[#555563]">Логин
        <Input className="mt-2" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required />
      </label>
      <label className="mt-4 block text-xs font-semibold text-[#555563]">Пароль
        <Input className="mt-2" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
      </label>
      {error && <p className="mt-4 text-sm text-[#c83c58]">{error}</p>}
      <Button className="mt-6 w-full" type="submit" disabled={working}>{working ? 'Входим…' : 'Войти'}</Button>
    </form>
  </main>;
}

type Session = { enabled: boolean; user: string | null };
type ProjectScreen =
  | { kind: 'projects' }
  | { kind: 'workspaces'; projectId: string }
  | { kind: 'cabinet'; projectId: string; workspaceId: string };

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    let active = true;
    void fetch('/api/auth/session', { cache: 'no-store' })
      .then(async (response) => response.ok ? response.json() as Promise<Session> : { enabled: true, user: null })
      .then((value) => active && setSession(value))
      .catch(() => active && setSession({ enabled: true, user: null }));
    return () => { active = false; };
  }, []);
  if (!session) return <LoadingScreen />;
  if (session.enabled && !session.user) return <LoginScreen onLogin={(user) => setSession({ enabled: true, user })} />;
  return <ProjectHome user={session.user} onLogout={() => setSession({ enabled: true, user: null })} />;
}

function ProjectHome({ user, onLogout }: { user: string | null; onLogout: () => void }) {
  const [catalog, setCatalog] = useState(() => loadLocalCatalog(user));
  const [screen, setScreen] = useState<ProjectScreen>({ kind: 'projects' });
  const [projectName, setProjectName] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const project = screen.kind === 'projects' ? null : catalog.projects.find((item) => item.id === screen.projectId) || null;
  const createProject = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const created = newProject(projectName);
    const next = { projects: [...catalog.projects, created] };
    saveLocalCatalog(user, next);
    setCatalog(next); setProjectName(''); setScreen({ kind: 'workspaces', projectId: created.id });
  };
  const createWorkspace = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!project) return;
    const next = addWorkspace(catalog, project.id, workspaceName);
    saveLocalCatalog(user, next); setCatalog(next); setWorkspaceName('');
  };
  const deleteProject = (target: LocalProject) => {
    if (!window.confirm(`Удалить проект «${target.name}» и все его рабочие области? Это удалит сохранённые в этом браузере блоки и ответы.`)) return;
    deleteLocalProject(user, target);
    const next = removeProject(catalog, target.id);
    saveLocalCatalog(user, next);
    setCatalog(next);
    setScreen({ kind: 'projects' });
  };
  const deleteWorkspace = (target: LocalWorkspace) => {
    if (!project || !window.confirm(`Удалить рабочую область «${target.name}»? Её блоки и ответы будут удалены только из этого браузера.`)) return;
    deleteLocalWorkspace(user, project.id, target.id);
    const next = removeWorkspace(catalog, project.id, target.id);
    saveLocalCatalog(user, next);
    setCatalog(next);
  };
  const logout = async () => { await fetch('/api/auth/logout', { method: 'POST' }); onLogout(); };
  if (screen.kind === 'cabinet') {
    const workspace = project?.workspaces.find((item) => item.id === screen.workspaceId);
    if (project && workspace) return <CabinetApp user={user} project={project} workspace={workspace} onBack={() => setScreen({ kind: 'workspaces', projectId: project.id })} onLogout={logout} />;
  }
  return <main className="l-rainbow min-h-screen text-[#171720]">
    <header className="mx-auto flex max-w-5xl items-center justify-between px-5 py-5 sm:px-8">
      <div className="flex items-center gap-3"><span className="l-block-gradient grid size-10 place-items-center rounded-2xl text-sm font-black text-white shadow-lg">L</span><div><h1 className="text-sm font-bold tracking-[-.02em]">L Constructor</h1><p className="text-xs text-[#747480]">личный кабинет</p></div></div>
      {user && <button type="button" className="rounded-full border border-white bg-white/70 p-2 text-[#63636f] shadow-sm transition hover:text-[#312e72]" title="Выйти" onClick={logout}><LogOut className="size-3.5" /></button>}
    </header>
    <section className="mx-auto max-w-5xl px-5 pb-12 sm:px-8">
      {screen.kind === 'projects' ? <>
        <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#777786]">Проекты</p><h2 className="mt-1 text-3xl font-semibold tracking-[-.05em]">Инженерные проекты</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-[#767682]">В проекте находятся отдельные рабочие области: каждая хранит своё поле шкафа, ответы блоков и результаты расчёта.</p>
        <form onSubmit={createProject} className="mt-7 flex max-w-xl gap-2 rounded-[24px] border border-white bg-white/75 p-3 shadow-[0_14px_50px_rgba(28,29,44,.07)] backdrop-blur"><Input aria-label="Название проекта" value={projectName} maxLength={80} onChange={(event) => setProjectName(event.target.value)} placeholder="Например, ПС Северная" /><Button type="submit"><Plus className="size-4" />Создать</Button></form>
        {!catalog.projects.length ? <div className="mt-7 rounded-[28px] border border-dashed border-[#d9d9e3] bg-white/55 p-8 text-sm leading-6 text-[#777786]">Пока нет проектов. Создай первый — в нём сразу появится «Рабочая область 1».</div> : <div className="mt-7 grid gap-4 sm:grid-cols-2">{catalog.projects.map((item) => <article key={item.id} className="l-glow relative rounded-[28px] bg-white p-6 transition hover:-translate-y-0.5"><button type="button" onClick={() => setScreen({ kind: 'workspaces', projectId: item.id })} className="block w-full text-left"><FolderKanban className="size-6 text-[#7669dc]" /><h3 className="mt-5 text-lg font-semibold">{item.name}</h3><p className="mt-1 text-sm text-[#747480]">{item.workspaces.length} раб. {item.workspaces.length === 1 ? 'область' : 'области'} · открыть</p></button><button type="button" aria-label={`Удалить проект ${item.name}`} title="Удалить проект" onClick={() => deleteProject(item)} className="absolute right-4 top-4 rounded-full p-2 text-[#8d8d9a] transition hover:bg-[#fff0f3] hover:text-[#c83c58]"><Trash2 className="size-4" /></button></article>)}</div>}
      </> : project ? <>
        <button type="button" onClick={() => setScreen({ kind: 'projects' })} className="mb-5 flex items-center gap-2 text-sm font-medium text-[#6254ca]"><ArrowLeft className="size-4" />Все проекты</button>
        <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#777786]">Проект</p><h2 className="mt-1 text-3xl font-semibold tracking-[-.05em]">{project.name}</h2>
        <p className="mt-3 text-sm leading-6 text-[#767682]">Рабочие области разделяют шкафы и их черновики. Внутри одной области собирается один состав шкафа.</p>
        <form onSubmit={createWorkspace} className="mt-7 flex max-w-xl gap-2 rounded-[24px] border border-white bg-white/75 p-3 shadow-[0_14px_50px_rgba(28,29,44,.07)] backdrop-blur"><Input aria-label="Название рабочей области" value={workspaceName} maxLength={80} onChange={(event) => setWorkspaceName(event.target.value)} placeholder={`Рабочая область ${project.workspaces.length + 1}`} /><Button type="submit"><Plus className="size-4" />Добавить</Button></form>
        {!project.workspaces.length ? <div className="mt-7 rounded-[28px] border border-dashed border-[#d9d9e3] bg-white/55 p-8 text-sm leading-6 text-[#777786]">В проекте пока нет рабочих областей. Добавь новую выше.</div> : <div className="mt-7 grid gap-4 sm:grid-cols-2">{project.workspaces.map((item) => <article key={item.id} className="l-glow relative rounded-[28px] bg-white p-6 transition hover:-translate-y-0.5"><button type="button" onClick={() => setScreen({ kind: 'cabinet', projectId: project.id, workspaceId: item.id })} className="block w-full text-left"><Layers3 className="size-6 text-[#7669dc]" /><h3 className="mt-5 text-lg font-semibold">{item.name}</h3><p className="mt-1 text-sm text-[#747480]">Поле шкафа · открыть</p></button><button type="button" aria-label={`Удалить рабочую область ${item.name}`} title="Удалить рабочую область" onClick={() => deleteWorkspace(item)} className="absolute right-4 top-4 rounded-full p-2 text-[#8d8d9a] transition hover:bg-[#fff0f3] hover:text-[#c83c58]"><Trash2 className="size-4" /></button></article>)}</div>}
      </> : null}
    </section>
  </main>;
}

function CabinetApp({ user, project, workspace, onBack, onLogout }: { user: string | null; project: LocalProject; workspace: LocalWorkspace; onBack: () => void; onLogout: () => void }) {
  const [savedProject] = useState(() => loadLocalWorkspace(user, project.id, workspace.id));
  const [motors, setMotors] = useState<MotorBlock[]>(() => savedProject.motors.map((motor: StoredMotor) => ({ ...motor, requested: false, generation: { status: 'idle' } })));
  const [activeMotorId, setActiveMotorId] = useState<string | null>(savedProject.activeMotorId);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [cabinetGeneration, setCabinetGeneration] = useState<Generation>({
    status: 'idle',
  });
  const engineering = useEngineering(motors, (normalized) => {
    setMotors(current => current.map(motor => {
      const accepted = normalized.find(item => item.id === motor.id);
      return accepted ? {...motor, state: accepted.state} : motor;
    }));
  });
  const {cabinet, revision: questionnaireRevision} = engineering;
  const inputVersion = JSON.stringify(
    motors.map(({ id, tag, state, mainSchemeCode }) => ({ id, tag, state, ...(mainSchemeCode ? { mainSchemeCode } : {}) })),
  );
  useEffect(() => {
    saveLocalWorkspace(user, project.id, workspace.id, {
      motors: motors.map(({ id, tag, cell, state, mainSchemeCode }) => ({ id, tag, cell, state, ...(mainSchemeCode ? { mainSchemeCode } : {}) })),
      activeMotorId,
      sheetOpen,
    });
  }, [user, project.id, workspace.id, motors, activeMotorId, sheetOpen]);
  const latestVersion = useRef(inputVersion);
  latestVersion.current = inputVersion;
  useEffect(() => {
    setCabinetGeneration({ status: 'idle' });
    setMotors((current) =>
      current.map((motor) => ({ ...motor, generation: { status: 'idle' } })),
    );
  }, [inputVersion]);
  const activeMotor =
    motors.find((motor) => motor.id === activeMotorId) || null;
  const pendingDelete =
    motors.find((motor) => motor.id === pendingDeleteId) || null;
  const state = activeMotor?.state || initialQuestionnaireState;
  const groups = activeMotorId ? engineering.forms[activeMotorId] || [] : [];
  const calculatedMotor = cabinet.blocks.find(
    (item) => item.id === activeMotorId,
  );
  const optionalDiagram = calculatedMotor?.optional || [];
  const activeInstances = cabinet.instances.filter(
    (item) => item.blockId === activeMotorId,
  );
  const schemeCodes = activeInstances.map((item) => item.code);
  const addMotor = (preferredCell = START_CELL, openSheet = false) => {
    const id = createClientId();
    setMotors((current) => {
      if (current.length >= CELLS) return current;
      const cell = nextFreeCell(current, preferredCell);
      if (cell === null) return current;
      return [
        ...current,
        {
          id,
          tag: `М${current.length + 1}`,
          cell,
          state: initialQuestionnaireState,
          requested: false,
          generation: { status: 'idle' },
        },
      ];
    });
    setActiveMotorId(id);
    if (openSheet) setSheetOpen(true);
  };
  const copyMotor = (sourceId: string) => {
    if (motors.length >= CELLS || !motors.some((motor) => motor.id === sourceId))
      return;
    const id = createClientId();
    setMotors((current) => {
      const source = current.find((motor) => motor.id === sourceId);
      const cell = source ? nextFreeCell(current, source.cell + 1) : null;
      if (!source || cell === null) return current;
      return [
        ...current,
        {
          ...source,
          id,
          tag: `М${current.length + 1}`,
          cell,
          state: cloneQuestionnaireState(source.state),
          generation: { status: 'idle' },
        },
      ];
    });
    setActiveMotorId(id);
    setSheetOpen(true);
  };
  const deleteMotor = (id: string) => {
    setMotors((current) => removeBlockById(current, id));
    if (activeMotorId === id) {
      setActiveMotorId(null);
      setSheetOpen(false);
    }
    setCabinetGeneration({ status: 'idle' });
    setPendingDeleteId(null);
  };
  const moveMotor = (id: string, cell: number) =>
    setMotors((current) =>
      current.some((motor) => motor.cell === cell && motor.id !== id)
        ? current
        : current.map((motor) =>
            motor.id === id ? { ...motor, cell } : motor,
          ),
    );
  const update = (
    part: keyof QuestionnaireState,
    key: string,
    value: string | boolean,
  ) => {
    if (!activeMotorId) return;
    setMotors((current) =>
      current.map((motor) =>
        motor.id === activeMotorId
          ? {
              ...motor,
              state: {
                ...motor.state,
                [part]: { ...motor.state[part], [key]: value },
              },
              generation: { status: 'idle' },
            }
          : motor,
      ),
    );
  };
  const generateDxf = async () => {
    if (!engineering.ready || !cabinet.canExport || !schemeCodes.length || !activeMotorId) return;
    const version = inputVersion;
    setMotors((current) =>
      current.map((motor) =>
        motor.id === activeMotorId
          ? { ...motor, generation: { status: 'working' } }
          : motor,
      ),
    );
    try {
      const response = await fetch(`${DXF_API_URL}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          motors: JSON.parse(inputVersion),
          blockId: activeMotorId,
          ruleFingerprint: cabinet.ruleFingerprint,
        }),
      });
      const result = (await response.json()) as {
        error?: string;
        message?: string;
        downloadPath?: string;
        drawings?: DrawingDownload[];
      };
      if (!response.ok || !result.downloadPath)
        throw new Error(result.error || 'Не удалось собрать DXF.');
      if (latestVersion.current !== version) return;
      setMotors((current) =>
        current.map((motor) =>
          motor.id === activeMotorId
            ? {
                ...motor,
                generation: {
                  status: 'success',
                  message: result.message || 'DXF собран и проверен.',
                  downloadUrl: `${DXF_API_URL}${result.downloadPath}`,
                  drawings: result.drawings || [],
                },
              }
            : motor,
        ),
      );
    } catch (error) {
      if (latestVersion.current !== version) return;
      setMotors((current) =>
        current.map((motor) =>
          motor.id === activeMotorId
            ? {
                ...motor,
                generation: {
                  status: 'error',
                  message:
                    error instanceof Error
                      ? error.message
                      : 'Локальный DXF-сервис недоступен.',
                },
              }
            : motor,
        ),
      );
    }
  };

  const generateCabinet = async () => {
    if (!cabinet.canExport) return;
    const version = inputVersion;
    setCabinetGeneration({ status: 'working' });
    try {
      const response = await fetch(`${DXF_API_URL}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          motors: JSON.parse(inputVersion),
          ruleFingerprint: cabinet.ruleFingerprint,
        }),
      });
      const result = await response.json();
      if (!response.ok || !result.downloadPath)
        throw new Error(result.error || 'Не удалось собрать шкаф');
      if (latestVersion.current !== version) return;
      setCabinetGeneration({
        status: 'success',
        message: result.message,
        downloadUrl: `${DXF_API_URL}${result.downloadPath}`,
        drawings: result.drawings || [],
      });
    } catch (error) {
      if (latestVersion.current !== version) return;
      setCabinetGeneration({
        status: 'error',
        message: error instanceof Error ? error.message : 'Ошибка генерации',
      });
    }
  };

  const downloadCalculation = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(cabinet, null, 2)], {
        type: 'application/json',
      }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = 'l-cabinet-calculation.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  useEffect(() => {
    const context = (
      document as Document & {
        modelContext?: {
          registerTool: (
            tool: unknown,
            options: { signal: AbortSignal },
          ) => unknown;
        };
      }
    ).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    void Promise.resolve(
      context.registerTool(
        {
          name: 'open_motor_questionnaire',
          title: 'Добавить двигатель и открыть опросник',
          description:
            'Добавляет блок ЭС двигателя на поле и открывает его опросный лист.',
          inputSchema: {
            type: 'object',
            properties: {},
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute() {
            addMotor(START_CELL, true);
            return { status: 'opened', block: 'ЭС / Двигатель' };
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(() => undefined);
    return () => lifecycle.abort();
  }, []);

  return (
    <main className="l-rainbow min-h-screen text-[#171720]">
      <header className="mx-auto flex max-w-7xl items-center justify-between px-5 py-5 sm:px-8">
        <div className="flex items-center gap-3">
          <span className="l-block-gradient grid size-10 place-items-center rounded-2xl text-sm font-black text-white shadow-lg">
            L
          </span>
          <div>
            <h1 className="text-sm font-bold tracking-[-.02em]">
              L Constructor
            </h1>
            <p className="text-xs text-[#747480]">{project.name} · {workspace.name}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="rounded-full border border-white bg-white/70 px-3 py-1.5 text-xs font-medium text-[#6254ca] shadow-sm transition hover:text-[#312e72]" onClick={onBack}><ArrowLeft className="mr-1 inline size-3.5" />Области</button>
          <div className="flex items-center gap-2 rounded-full border border-white bg-white/70 px-3 py-1.5 text-xs font-medium text-[#63636f] shadow-sm">
            <Layers3 className="size-3.5 text-[#7669dc]" />
            {motors.length ? blockCountLabel(motors.length) : 'поле пустое'}
          </div>
          {user && <button type="button" className="rounded-full border border-white bg-white/70 p-2 text-[#63636f] shadow-sm transition hover:text-[#312e72]" title="Выйти" onClick={onLogout}><LogOut className="size-3.5" /></button>}
        </div>
      </header>
      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-5 px-5 pb-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0 rounded-[28px] border border-white bg-white/75 p-4 shadow-[0_14px_50px_rgba(28,29,44,.07)] backdrop-blur sm:p-6">
          <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#777786]">
            Поле шкафа
          </p>
          <h2 className="mt-1 text-2xl font-semibold tracking-[-.04em]">
            Собери состав
          </h2>
          <p className="mt-2 max-w-lg text-sm leading-6 text-[#767682]">
            Располагай функциональные узлы как на монтажном поле. У каждого
            блока — собственные параметры, схемы и спецификация.
          </p>
          <Board
            motors={motors}
            onAdd={addMotor}
            onMove={moveMotor}
            onOpen={(id) => {
              setActiveMotorId(id);
              setSheetOpen(true);
            }}
          />
        </section>
        <aside className="min-w-0 space-y-5">
          <BlockLibrary onAdd={() => addMotor()} />
          <section className="rounded-[28px] border border-white bg-white/75 p-5 shadow-[0_14px_50px_rgba(28,29,44,.07)] backdrop-blur">
            <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#777786]">
              Состав шкафа
            </p>
            <h2 className="mt-1 text-xl font-semibold tracking-[-.04em]">
              На поле
            </h2>
            {!motors.length ? (
              <div className="mt-5 rounded-2xl border border-dashed border-[#d9d9e3] bg-white/50 p-4 text-sm leading-6 text-[#83838d]">
                Добавь блок из библиотеки — он станет частью этой сборки.
              </div>
            ) : (
              <div className="mt-5 space-y-2">
                {motors.map((motor) => (
                  <div
                    key={motor.id}
                    className="l-glow w-full rounded-2xl bg-white p-4 text-left transition hover:-translate-y-0.5"
                  >
                    <button
                      onClick={() => {
                        setActiveMotorId(motor.id);
                        setSheetOpen(true);
                      }}
                      className="flex w-full items-center gap-3 text-left"
                    >
                      <span className="motor-thumb l-block-gradient grid size-11 overflow-hidden rounded-xl">
                        <img
                          src="/assets/asynchronous-motor-v1.png"
                          alt="Асинхронный электродвигатель"
                        />
                      </span>
                      <span>
                        <span className="block text-sm font-semibold">
                          Асинхронный двигатель
                        </span>
                        <span className="mt-0.5 block text-xs text-[#747480]">
                          {motor.tag} · ОЛ {questionnaireRevision}
                        </span>
                      </span>
                      <ArrowUpRight className="ml-auto size-4 text-[#7669dc]" />
                    </button>
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <button
                        onClick={() => copyMotor(motor.id)}
                        className="flex items-center justify-center gap-2 rounded-xl bg-[#f0eeff] px-3 py-2 text-xs font-semibold text-[#6254ca] transition hover:bg-[#e7e3ff]"
                        title={`Создать независимую копию ${motor.tag}`}
                      >
                        <Copy className="size-3.5" />
                        Копировать
                      </button>
                      <button
                        onClick={() => setPendingDeleteId(motor.id)}
                        className="flex items-center justify-center gap-2 rounded-xl bg-[#fff1ee] px-3 py-2 text-xs font-semibold text-[#b45b45] transition hover:bg-[#ffe5de]"
                        title={`Удалить ${motor.tag} из состава шкафа`}
                      >
                        <Trash2 className="size-3.5" />
                        Удалить
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-5 rounded-2xl bg-[#f4f4f8] p-4 text-xs leading-5 text-[#777786]">
              I/O распределяются по ZENTEC M245 и, при нехватке выводов, до пяти
              тестовых модулей M245 no display для всего шкафа. Клеммы XT и связи
              GND/COM в DXF пока не назначаются.
              {cabinet.plcHardware.length > 0 && (
                <p className="mt-2 font-semibold text-[#403b55]">
                  Подобрано: {cabinet.plcHardware.map((device) => `${device.ref} — ${device.brand} ${device.model}`).join('; ')}.
                </p>
              )}
            </div>
            <div className="mt-5 space-y-3 border-t border-[#e4e4ed] pt-5">
              <h3 className="text-sm font-semibold">Схемы всего шкафа</h3>
              {!engineering.ready && <p role="status" className="text-xs text-[#7669dc]">
                {engineering.error ? <>{engineering.error} <button className="underline" onClick={engineering.retry}>Повторить</button></> : 'Пересчитываю…'}
              </p>}
              <p className="text-xs leading-5 text-[#777786]">
                Два отдельных документа по всем блокам поля: принципиальная
                электрическая схема и схема внешних соединений. У каждого своя
                рамка. Всего {cabinet.instances.length} фрагментов. Черновик, не
                готовая КД.
              </p>
              <Button
                className="l-block-gradient w-full text-white"
                disabled={
                  !cabinet.canExport || cabinetGeneration.status === 'working'
                }
                onClick={generateCabinet}
              >
                {cabinetGeneration.status === 'working' ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <FileCog className="size-4" />
                )}
                {cabinetGeneration.status === 'working'
                  ? 'Собираю…'
                  : 'Собрать схемы шкафа'}
              </Button>
              {cabinetGeneration.status === 'success' && (
                <DrawingDownloads generation={cabinetGeneration} />
              )}
              {cabinetGeneration.status === 'error' && (
                <p role="alert" className="text-xs text-[#9a5635]">
                  {cabinetGeneration.message}
                </p>
              )}
              {cabinet.errors.length > 0 && (
                <p className="text-xs leading-5 text-[#9a5635]">
                  Нужно заполнить или исправить блоки:{' '}
                  {cabinet.blocks
                    .filter((block) => block.errors.length)
                    .map((block) => block.tag)
                    .join(', ') || cabinet.errors[0]}
                  . Открой опросник.
                </p>
              )}
              <Button
                variant="outline"
                className="w-full"
                onClick={downloadCalculation}
                disabled={!motors.length || !engineering.ready}
              >
                <Download className="size-4" />
                Скачать расчёт JSON
              </Button>
              <p className="text-[11px] leading-4 text-[#85858f]">
                Поле и ответы сохраняются в этой рабочей области локально в
                браузере и восстанавливаются после обновления. Перенос на
                другой компьютер и серверное хранение пока не реализованы.
              </p>
            </div>
          </section>
        </aside>
      </div>
      {sheetOpen && activeMotor && (
        <QuestionnaireSheet
          revision={questionnaireRevision}
          calculationPending={!engineering.ready}
          canExport={cabinet.canExport}
          calculationError={engineering.error}
          onRetry={engineering.retry}
          motorTag={activeMotor.tag}
          groups={groups}
          state={state}
          update={update}
          requested={activeMotor.requested}
          setRequested={(requested) =>
            setMotors((current) =>
              current.map((motor) =>
                motor.id === activeMotor.id ? { ...motor, requested } : motor,
              ),
            )
          }
          diagramRows={calculatedMotor?.main ? [calculatedMotor.main] : []}
          selectedMain={calculatedMotor?.main}
          mainChoices={calculatedMotor?.mainChoices || []}
          mainSchemeCode={activeMotor.mainSchemeCode || ''}
          onMainSchemeChange={(code) => setMotors(current => current.map(motor => motor.id === activeMotor.id
            ? { ...motor, mainSchemeCode: code || undefined, generation: { status: 'idle' } } : motor))}
          channels={calculatedMotor?.channels || []}
          optionalRows={optionalDiagram}
          missing={calculatedMotor?.errors || []}
          schemeCodes={schemeCodes}
          generation={activeMotor.generation}
          onGenerate={generateDxf}
          calculationSummary={
            calculatedMotor && (
              <DecisionTrace
                trace={calculatedMotor.decisionTrace}
                errors={calculatedMotor.errors}
                warnings={calculatedMotor.warnings}
              />
            )
          }
          onClose={() => setSheetOpen(false)}
          onReset={() =>
            setMotors((current) =>
              current.map((motor) =>
                motor.id === activeMotor.id
                  ? {
                      ...motor,
                      state: initialQuestionnaireState,
                      mainSchemeCode: undefined,
                      requested: false,
                      generation: { status: 'idle' },
                    }
                  : motor,
              ),
            )
          }
        />
      )}
      {pendingDelete && (
        <div className="fixed inset-0 z-[70] grid place-items-center bg-[#1c1c29]/35 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`Удалить блок ${pendingDelete.tag}`}
            className="w-full max-w-sm rounded-[24px] bg-white p-6 shadow-[0_22px_70px_rgba(28,29,44,.28)]"
          >
            <div className="flex items-start gap-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#fff1ee] text-[#b45b45]">
                <Trash2 className="size-5" />
              </span>
              <div>
                <h3 className="text-lg font-semibold text-[#393744]">
                  Удалить {pendingDelete.tag}?
                </h3>
                <p className="mt-1 text-xs leading-5 text-[#777481]">
                  Удалятся ответы этого блока и его расчёт в текущей вкладке.
                  Остальные блоки не изменятся.
                </p>
              </div>
            </div>
            <div className="mt-6 grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                onClick={() => setPendingDeleteId(null)}
                className="border-[#e3e2ea] bg-white"
              >
                Отмена
              </Button>
              <Button
                onClick={() => deleteMotor(pendingDelete.id)}
                className="bg-[#c96a51] text-white hover:bg-[#b75d46]"
              >
                Удалить блок
              </Button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

function BlockLibrary({ onAdd }: { onAdd: () => void }) {
  return (
    <section className="overflow-hidden rounded-[28px] border border-white bg-white/80 p-3 shadow-[0_14px_50px_rgba(28,29,44,.07)] backdrop-blur">
      <div className="flex items-center justify-between px-2 pb-3 pt-1">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#777786]">
            Библиотека
          </p>
          <h2 className="mt-1 text-lg font-semibold tracking-[-.04em]">
            Функциональные блоки
          </h2>
        </div>
        <span className="rounded-full bg-[#f0eeff] px-2.5 py-1 text-[10px] font-bold uppercase tracking-[.1em] text-[#7563dc]">
          1 доступен
        </span>
      </div>
      <button
        draggable
        onDragStart={(event) =>
          event.dataTransfer.setData('text/plain', 'motor')
        }
        onClick={onAdd}
        className="block-card group w-full rounded-[20px] p-4 text-left transition duration-200 hover:-translate-y-0.5"
      >
        <div className="flex items-start gap-3">
          <span className="motor-thumb l-block-gradient grid size-14 shrink-0 overflow-hidden rounded-2xl shadow-[0_8px_18px_rgba(119,103,255,.25)]">
            <img
              src="/assets/asynchronous-motor-v1.png"
              alt="Асинхронный электродвигатель"
            />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-bold tracking-[-.02em] text-[#23232d]">
                Электродвигатель
              </p>
              <GripVertical className="size-4 text-[#aaa8c4]" />
            </div>
            <p className="mt-1 text-xs font-medium text-[#7766df]">
              ЭС · привод механизма
            </p>
            <p className="mt-1.5 text-[10px] font-bold uppercase tracking-[.1em] text-[#8b8999]">
              асинхронный · 3~
            </p>
          </div>
        </div>
        <p className="mt-3 text-xs leading-5 text-[#626270]">
          Подбирает пуск, питание, защиту и сигналы асинхронного двигателя по
          опросному листу.
        </p>
        <div className="mt-4 flex items-center justify-between">
          <div className="flex gap-1.5">
            <span className="block-chip">ОЛ В2</span>
            <span className="block-chip">схема</span>
          </div>
          <span className="flex items-center gap-1 text-xs font-semibold text-[#5f50cb]">
            Добавить{' '}
            <Plus className="size-3.5 transition group-hover:rotate-90" />
          </span>
        </div>
      </button>
      <div className="mt-2 flex items-center gap-2 rounded-2xl px-3 py-2 text-[11px] leading-4 text-[#858590]">
        <CircleCheck className="size-3.5 shrink-0 text-[#6dceac]" />
        Перетащи карточку на нужную клетку или нажми «Добавить».
      </div>
    </section>
  );
}

function Board({
  motors,
  onAdd,
  onMove,
  onOpen,
}: {
  motors: MotorBlock[];
  onAdd: (cell: number) => void;
  onMove: (id: string, cell: number) => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="mt-7 overflow-x-auto pb-1">
      <div className="grid min-w-[430px] grid-cols-8 gap-1.5 rounded-[22px] bg-[#f4f4f8] p-2.5 sm:min-w-[560px]">
        {Array.from({ length: CELLS }, (_, index) => {
          const motor = motors.find((item) => item.cell === index);
          return (
            <div
              key={index}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
              }}
              onDrop={(event) => {
                event.preventDefault();
                const movingId = event.dataTransfer.getData('text/motor-id');
                if (movingId) onMove(movingId, index);
                else onAdd(index);
              }}
              className="relative aspect-square rounded-lg border border-[#e6e6ee] bg-white/70"
            >
              {motor && (
                <button
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/motor-id', motor.id);
                  }}
                  onClick={() => onOpen(motor.id)}
                  title="Открыть опросник или перенести блок"
                  className="l-block-gradient absolute inset-1 flex cursor-grab flex-col items-center justify-center overflow-hidden rounded-md shadow-[0_5px_14px_rgba(119,103,255,.32)] transition hover:scale-[1.04] active:cursor-grabbing"
                >
                  <img
                    src="/assets/asynchronous-motor-v1.png"
                    alt=""
                    className="motor-board-image"
                  />
                  <span className="relative -mt-1 text-[8px] font-black text-white drop-shadow sm:text-[10px]">
                    ЭС · {motor.tag}
                  </span>
                </button>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-center text-xs text-[#92929d]">
        {COLUMNS} × {ROWS} · {CELLS} посадочных клеток · блоки можно переносить
      </p>
    </div>
  );
}

function QuestionnaireSheet({
  canExport,
  revision,
  calculationPending,
  calculationError,
  onRetry,
  motorTag,
  groups,
  state,
  update,
  requested,
  setRequested,
  diagramRows,
  selectedMain,
  mainChoices,
  mainSchemeCode,
  onMainSchemeChange,
  channels,
  optionalRows,
  missing,
  schemeCodes,
  generation,
  onGenerate,
  onClose,
  onReset,
  calculationSummary,
}: {
  canExport: boolean;
  revision: string;
  calculationPending: boolean;
  calculationError: string;
  onRetry: () => void;
  motorTag: string;
  groups: VisibleGroup[];
  state: QuestionnaireState;
  update: (
    part: keyof QuestionnaireState,
    key: string,
    value: string | boolean,
  ) => void;
  requested: boolean;
  setRequested: (value: boolean) => void;
  diagramRows: DiagramRow[];
  selectedMain?: DiagramRow;
  mainChoices: Array<{code: string; io: Record<string, number>; sourceRow: number}>;
  mainSchemeCode: string;
  onMainSchemeChange: (code: string) => void;
  channels: Channel[];
  optionalRows: OptionalRow[];
  missing: string[];
  schemeCodes: string[];
  generation: Generation;
  onGenerate: () => void;
  onClose: () => void;
  onReset: () => void;
  calculationSummary: ReactNode;
}) {
  let activeSheet = '';
  const [passportOpen, setPassportOpen] = useState(false);
  return (
    <div className="fixed inset-0 z-50 bg-[#1c1c29]/25 p-0 backdrop-blur-sm sm:p-5">
      <div className="ml-auto flex h-full w-full max-w-3xl flex-col bg-[#fbfbfe] shadow-[-20px_0_70px_rgba(28,29,44,.18)]">
        <header className="flex items-center justify-between border-b border-[#ececf2] px-5 py-4 sm:px-8">
          <div className="flex items-center gap-3">
            <span className="l-block-gradient grid size-10 place-items-center rounded-xl text-xs font-black text-white">
              ЭС
            </span>
            <div>
              <p className="text-sm font-semibold">Двигатель {motorTag}</p>
              <p className="text-xs text-[#7d7d88]">
                Опросный лист · {revision}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {calculationSummary && (
              <button
                onClick={() => setPassportOpen(true)}
                className="flex h-10 items-center gap-2 rounded-xl bg-[#eeebff] px-3 text-xs font-semibold text-[#6254ca] hover:bg-[#e5e0ff]"
              >
                <FileCog className="size-4" />
                Паспорт
              </button>
            )}
            <button
              aria-label="Закрыть опросный лист"
              onClick={onClose}
              className="grid size-10 place-items-center rounded-xl bg-[#f0f0f5] text-lg text-[#62626e] hover:bg-[#e8e8ef]"
            >
              ×
            </button>
          </div>
        </header>
        <div role="status" className="px-5 py-2 text-xs text-[#7669dc]">
          {calculationError ? <>{calculationError} <button onClick={onRetry} className="underline">Повторить</button></> : calculationPending ? 'Пересчитываю…' : 'Расчёт обновлён'}
        </div>
        <div className="grid flex-1 overflow-y-auto lg:grid-cols-[1.05fr_.95fr]">
          <section className="p-5 sm:p-8">
            <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#7669dc]">
              Параметры блока
            </p>
            <h2 className="mt-1 text-2xl font-semibold tracking-[-.04em]">
              Опросник двигателя
            </h2>
            <p className="mt-2 text-sm leading-6 text-[#777782]">
              Вопросы открываются через связи{' '}
              <span className="font-mono text-xs">binds</span> в исходной ОЛ.
            </p>
            <div className="mt-7 space-y-5">
              {groups.map((group) => {
                const heading = group.sheet !== activeSheet;
                activeSheet = group.sheet;
                return (
                  <div key={group.key}>
                    {heading && (
                      <p className="mb-3 mt-7 border-t border-[#ececf2] pt-5 text-xs font-bold uppercase tracking-[.14em] text-[#7669dc]">
                        {sheetTitles[group.sheet]}
                      </p>
                    )}
                    <QuestionControl
                      group={group}
                      state={state}
                      update={update}
                    />
                  </div>
                );
              })}
            </div>
            <div className="mt-8 flex gap-3">
              <Button
                onClick={() => setRequested(true)}
                disabled={calculationPending}
                className="l-block-gradient h-11 flex-1 text-white shadow-[0_8px_18px_rgba(119,103,255,.25)] hover:opacity-90"
              >
                Проверить схемы
              </Button>
              <Button
                onClick={onReset}
                variant="outline"
                className="h-11 border-[#e4e4ec] bg-white text-[#686873]"
              >
                Сбросить
              </Button>
            </div>
          </section>
          <section className="border-t border-[#ececf2] bg-[#f6f6fa] p-5 sm:p-8 lg:border-l lg:border-t-0">
            <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#7669dc]">
              Схемы блока
            </p>
            <h2 className="mt-1 text-2xl font-semibold tracking-[-.04em]">
              Результат выбора
            </h2>
            <DiagramResult
              canExport={canExport}
              rows={diagramRows}
              selectedMain={selectedMain}
              mainChoices={mainChoices}
              mainSchemeCode={mainSchemeCode}
              onMainSchemeChange={onMainSchemeChange}
              channels={channels}
              optionalRows={optionalRows}
              missing={missing}
              requested={requested && !calculationPending}
              schemeCodes={schemeCodes}
              generation={generation}
              onGenerate={onGenerate}
            />
          </section>
        </div>
      </div>
      {passportOpen && calculationSummary && (
        <div
          className="fixed inset-0 z-[60] grid place-items-center bg-[#1c1c29]/30 p-4 backdrop-blur-sm"
          role="presentation"
          onMouseDown={() => setPassportOpen(false)}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`Паспорт блока ${motorTag}`}
            onMouseDown={(event) => event.stopPropagation()}
            className="max-h-[calc(100vh-2rem)] w-full max-w-[530px] overflow-y-auto rounded-[24px] border border-white bg-[#fbfbfe] p-5 shadow-[0_22px_70px_rgba(28,29,44,.28)] sm:p-6"
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#7669dc]">
                  Проверка блока
                </p>
                <h3 className="mt-1 text-xl font-semibold tracking-[-.04em] text-[#34323f]">
                  Паспорт {motorTag}
                </h3>
              </div>
              <button
                aria-label="Закрыть паспорт блока"
                onClick={() => setPassportOpen(false)}
                className="grid size-9 place-items-center rounded-xl bg-[#f0f0f5] text-lg text-[#62626e] hover:bg-[#e8e8ef]"
              >
                ×
              </button>
            </div>
            {calculationSummary}
          </section>
        </div>
      )}
    </div>
  );
}

function RequiredMark({ required }: { required: boolean }) {
  return required ? (
    <span className="ml-1 text-[#e67855]" aria-label="обязательное поле">
      *
    </span>
  ) : null;
}

function QuestionControl({
  group,
  state,
  update,
}: {
  group: VisibleGroup;
  state: QuestionnaireState;
  update: (
    part: keyof QuestionnaireState,
    key: string,
    value: string | boolean,
  ) => void;
}) {
  const first = group.nodes[0];
  const key = `${first.sheet}:${first.order}`;
  const label = (
    <>
      {group.question}
      <RequiredMark required={group.required} />
    </>
  );
  if (group.fieldType === 'list' || group.fieldType === 'radio_group')
    return (
      <label className="block text-sm font-medium text-[#40404a]">
        <span>{label}</span>
        <span className="ml-2 font-mono text-[10px] text-[#aaaab5]">
          {group.nodes.map((node) => node.order).join(' / ')}
        </span>
        <select
          value={state.selected[group.key] || ''}
          onChange={(event) =>
            update('selected', group.key, event.target.value)
          }
          className="mt-2 h-11 w-full rounded-xl border border-[#e2e2eb] bg-white px-3 text-sm text-[#33333d] outline-none focus:border-[#9277ff]"
        >
          <option value="" disabled>
            Выбрать
          </option>
          {group.nodes.map((node) => (
            <option key={node.order} value={node.order}>
              {node.order} · {node.answer}
            </option>
          ))}
        </select>
      </label>
    );
  if (group.fieldType === 'input') {
    const rule = first.inputRule || first.answer || '';
    const numeric = numericInputRule(rule);
    return (
      <label className="block text-sm font-medium text-[#40404a]">
        <span>{label}</span>
        <Input
          type={numeric ? 'number' : 'text'}
          inputMode={numeric ? 'decimal' : 'text'}
          min={numeric?.min}
          max={numeric?.max}
          step={numeric?.step}
          value={state.inputs[key] || ''}
          onChange={(event) => update('inputs', key, event.target.value)}
          className="mt-2 h-11 border-[#e2e2eb] bg-white"
          placeholder={rule || 'Введите значение'}
        />
        <span className="mt-1 block text-[10px] text-[#aaaab5]">
          {first.order} · {rule}
        </span>
      </label>
    );
  }
  if (group.fieldType === 'check_box' && group.required) {
    const answered = Object.prototype.hasOwnProperty.call(state.checks, key);
    return (
      <fieldset className="rounded-xl border border-[#e4e4ec] bg-white px-3 py-3 text-sm text-[#40404a]">
        <legend className="px-1 font-medium">{label}</legend>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={() => update('checks', key, true)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${state.checks[key] === true ? 'bg-[#7669dc] text-white' : 'bg-[#f1f0f7] text-[#5f5d6c]'}`}
          >
            Да
          </button>
          <button
            type="button"
            onClick={() => update('checks', key, false)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${answered && state.checks[key] === false ? 'bg-[#7669dc] text-white' : 'bg-[#f1f0f7] text-[#5f5d6c]'}`}
          >
            Нет
          </button>
          <span className="ml-auto self-center font-mono text-[10px] text-[#aaaab5]">
            {first.order}
          </span>
        </div>
      </fieldset>
    );
  }
  if (group.fieldType === 'check_box')
    return (
      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-[#e4e4ec] bg-white px-3 py-3 text-sm text-[#40404a]">
        <input
          type="checkbox"
          checked={Boolean(state.checks[key])}
          onChange={(event) => update('checks', key, event.target.checked)}
          className="size-4 accent-[#7669dc]"
        />
        <span>{label}</span>
        <span className="ml-auto font-mono text-[10px] text-[#aaaab5]">
          {first.order}
        </span>
      </label>
    );
  if (group.fieldType === 'uploadButton')
    return (
      <label className="block rounded-xl border border-dashed border-[#d6d6e3] bg-white p-4 text-sm text-[#40404a]">
        <span>{label}</span>
        <input
          type="file"
          onChange={(event) =>
            update('uploads', key, event.target.files?.[0]?.name || '')
          }
          className="mt-3 block w-full text-xs text-[#777782]"
        />
        {state.uploads[key] && (
          <span className="mt-2 block text-xs text-[#7669dc]">
            {state.uploads[key]}
          </span>
        )}
      </label>
    );
  return null;
}

function DiagramResult({
  canExport,
  rows,
  selectedMain,
  mainChoices,
  mainSchemeCode,
  onMainSchemeChange,
  channels,
  optionalRows,
  missing,
  requested,
  schemeCodes,
  generation,
  onGenerate,
}: {
  canExport: boolean;
  rows: DiagramRow[];
  selectedMain?: DiagramRow;
  mainChoices: Array<{code: string; io: Record<string, number>; sourceRow: number}>;
  mainSchemeCode: string;
  onMainSchemeChange: (code: string) => void;
  channels: Channel[];
  optionalRows: OptionalRow[];
  missing: string[];
  requested: boolean;
  schemeCodes: string[];
  generation: Generation;
  onGenerate: () => void;
}) {
  const canGenerate =
    requested &&
    missing.length === 0 &&
    Boolean(selectedMain) &&
    schemeCodes.length > 0;
  return (
    <div className="mt-6 space-y-6">
      {(mainChoices.length > 1 || mainSchemeCode) && (
        <label className="block text-sm font-medium text-[#40404a]">
          Основная схема
          <select aria-label="Основная схема" className="mt-2 w-full rounded-xl border border-[#dcd8ff] bg-white p-3 text-sm"
            value={mainSchemeCode} onChange={event => onMainSchemeChange(event.target.value)}>
            <option value="">Автоматический подбор</option>
            {mainChoices.map(choice => <option key={`${choice.code}:${choice.sourceRow}`} value={choice.code}>
              {choice.code} · {Object.entries(choice.io).filter(([, count]) => count > 0).map(([code, count]) => `${code} × ${count}`).join(', ')}
            </option>)}
          </select>
        </label>
      )}
      {requested && missing.length > 0 ? (
        <Notice>Не выбрано: {missing.join(', ')}.</Notice>
      ) : requested && !rows.length ? (
        <Notice>Для этой комбинации в diagram 1 нет строки.</Notice>
      ) : requested && selectedMain ? (
        <>
          <p className="text-sm text-[#747480]">
            Выбран вариант схемы и назначены выводы контроллера и, если нужны, модулей.
            Обозначение перед номером вывода показывает устройство.
          </p>
          <DiagramTable
            rows={[{ row: selectedMain, sourceOrder: 'I/O optimizer' }]}
          />
          {channels.length > 0 && (
            <div className="rounded-2xl border border-[#dcd8ff] bg-white p-4 text-xs text-[#615d78]">
              <p className="font-semibold text-[#403b55]">
                Выводы ПЛК
              </p>
              <p className="mt-2 font-mono leading-5">
                {channels
                  .map((channel) => [`${channel.deviceRef}:${channel.terminals.join(' / ')}`, channel.family, channel.commonRef].filter(Boolean).join(' · '))
                  .join('\n')}
              </p>
            </div>
          )}
        </>
      ) : (
        <Notice>
          Подбор I/O ждёт совместимые варианты схем всех заполненных блоков.
        </Notice>
      )}
      {
        <div className="border-t border-[#e5e5ed] pt-6">
          <p className="text-sm font-semibold text-[#40404a]">
            diagram 2 · дополнительные сигналы
          </p>
          {optionalRows.length ? (
            <DiagramTable rows={optionalRows} />
          ) : (
            <p className="mt-3 text-sm text-[#85858f]">
              Выбери параметры двигателя в Ques 2.
            </p>
          )}
        </div>
      }
      {canGenerate && (
        <section className="rounded-2xl border border-[#dcd8ff] bg-white p-4 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="l-block-gradient grid size-10 shrink-0 place-items-center rounded-xl text-white">
              <FileCog className="size-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-[#393744]">
                Черновая компоновка DXF
              </p>
              <p className="mt-2 text-xs leading-5 text-[#9a5635]">
                Шаблоны ещё содержат исходные маркировки и номиналы. Не
                использовать как выпущенную КД.
              </p>
              <p className="mt-1 font-mono text-[11px] leading-5 text-[#756b9e]">
                {schemeCodes.join(' + ')}
              </p>
            </div>
          </div>
          <Button
            disabled={!canExport || generation.status === 'working'}
            onClick={onGenerate}
            className="l-block-gradient mt-4 h-11 w-full text-white shadow-[0_8px_18px_rgba(119,103,255,.25)] hover:opacity-90"
          >
            {generation.status === 'working' ? (
              <>
                <LoaderCircle className="size-4 animate-spin" />
                Собираю DXF…
              </>
            ) : (
              <>
                <FileCog className="size-4" />
                Собрать DXF
              </>
            )}
          </Button>
          {!canExport && <p className="mt-2 text-xs text-[#9a5635]">Для согласованного I/O сначала заполни остальные блоки шкафа.</p>}
          {generation.status === 'success' && (
            <div className="mt-3 rounded-xl bg-[#effbf6] p-3 text-xs leading-5 text-[#39715a]">
              <div className="flex items-center gap-2">
                <CircleCheck className="size-4" />
                Два вида схем собраны отдельно.
              </div>
              <DrawingDownloads generation={generation} />
            </div>
          )}
          {generation.status === 'error' && (
            <div className="mt-3 flex gap-2 rounded-xl bg-[#fff4ee] p-3 text-xs leading-5 text-[#9a5635]">
              <TriangleAlert className="size-4 shrink-0" />
              {generation.message}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function sourceLabel(source?: string) {
  if (!source) return null;
  const [table, row] = source.split(':');
  return row ? `${table} · строка ${row}` : source;
}

function TraceSection({
  title,
  caption,
  entries,
  empty,
  defaultOpen = false,
}: {
  title: string;
  caption: string;
  entries: DecisionTraceEntry[];
  empty: string;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="overflow-hidden rounded-2xl border border-[#e1dff0] bg-white">
      <summary className="cursor-pointer list-none px-4 py-3 marker:hidden">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#403b55]">{title}</p>
            <p className="mt-0.5 text-[11px] leading-4 text-[#85818f]">
              {caption}
            </p>
          </div>
          <span className="rounded-full bg-[#f0eeff] px-2 py-1 text-[10px] font-bold text-[#6d60d3]">
            {entries.length}
          </span>
        </div>
      </summary>
      <div className="border-t border-[#eeeeF4] px-4 py-3">
        {entries.length ? (
          <ul className="space-y-2.5">
            {entries.map((entry) => (
              <li key={entry.id} className="rounded-xl bg-[#f8f8fc] px-3 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-xs font-semibold leading-5 text-[#44424f]">
                    {entry.title}
                  </p>
                  {entry.source && (
                    <span className="shrink-0 rounded-md bg-[#eceafb] px-1.5 py-0.5 font-mono text-[10px] text-[#6d60d3]">
                      {sourceLabel(entry.source)}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs leading-5 text-[#6d6a76]">
                  {entry.detail}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs leading-5 text-[#85818f]">{empty}</p>
        )}
      </div>
    </details>
  );
}

function DecisionTrace({
  trace,
  errors,
  warnings,
}: {
  trace: DecisionTraceData;
  errors: string[];
  warnings: string[];
}) {
  const issues: DecisionTraceEntry[] = [
    ...errors.map((detail, index) => ({
      id: `error:${index}:${detail}`,
      title: 'Требует заполнения или исправления',
      detail,
    })),
    ...warnings.map((detail, index) => ({
      id: `warning:${index}:${detail}`,
      title: 'Проверить перед выпуском',
      detail,
    })),
  ];
  return (
    <section className="space-y-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[.16em] text-[#7669dc]">
          Входы и результаты
        </p>
        <h3 className="mt-1 text-lg font-semibold tracking-[-.03em] text-[#34323f]">
          От ответов к результату
        </h3>
        <p className="mt-1 text-xs leading-5 text-[#777481]">
          Активные ответы ведут к правилам из ОЛ, затем — к схемам, I/O и
          спецификации. Строки показывают источник каждого решения.
        </p>
      </div>
      <TraceSection
        title="Входные данные"
        caption="Только видимые ответы этого экземпляра"
        entries={trace.inputs}
        empty="Заполни опросник: скрытые ответы намеренно не учитываются."
      />
      <TraceSection
        title="Сработавшие правила"
        caption="Выбор схем и инженерных диапазонов"
        entries={trace.rules}
        empty="Правила появятся после выбора совместимой схемы."
        defaultOpen
      />
      <TraceSection
        title="Выходные данные"
        caption="Индекс, выводы ПЛК и состав"
        entries={trace.outputs}
        empty="Итог появится после заполнения обязательных параметров."
      />
      <TraceSection
        title="Ошибки и проверки"
        caption="Что блокирует подбор или требует внимания"
        entries={issues}
        empty="Ошибок и предупреждений для этого блока нет."
      />
    </section>
  );
}

function DrawingDownloads({
  generation,
}: {
  generation: Extract<Generation, { status: 'success' }>;
}) {
  return (
    <div className="space-y-2 py-2">
      {generation.drawings.map((drawing) => (
        <a
          key={drawing.kind}
          href={`${DXF_API_URL}${drawing.downloadPath}`}
          download
          className="flex items-center gap-2 rounded-xl bg-[#eaf8f0] p-3 text-xs font-semibold text-[#39715a]"
        >
          <Download className="size-4 shrink-0" />
          {drawing.title} · DXF
        </a>
      ))}
      <a
        href={generation.downloadUrl}
        download
        className="block text-xs font-semibold underline underline-offset-2"
      >
        Скачать комплект ZIP
      </a>
    </div>
  );
}

function DiagramTable({ rows }: { rows: OptionalRow[] }) {
  return (
    <div className="mt-3 overflow-hidden rounded-2xl border border-[#e2e2eb] bg-white">
      <table className="w-full text-left text-xs">
        <thead className="bg-[#f7f7fb] text-[#8c8c98]">
          <tr>
            <th className="px-3 py-3">Стр.</th>
            <th className="px-3 py-3">Принципиальная</th>
            <th className="px-3 py-3">Внешние соединения</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#eeeeF3]">
          {rows.map(({ row, sourceOrder }, index) => (
            <tr key={`${row.sourceRow}-${sourceOrder}-${index}`}>
              <td className="px-3 py-3 font-mono text-[#9696a0]">
                {row.sourceRow}
              </td>
              <td className="px-3 py-3 font-mono font-bold text-[#6c5edb]">
                {row.diagram1}
              </td>
              <td className="px-3 py-3 font-mono font-bold text-[#6c5edb]">
                {row.diagram2}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-[#ffcfaf] bg-[#fff8f2] p-4 text-sm text-[#9a5f35]">
      {children}
    </div>
  );
}
