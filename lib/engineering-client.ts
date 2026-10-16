/** UI contracts only. Engineering rules and graph traversal live in Python. */
import { useEffect, useEffectEvent, useState } from 'react';

export type QuestionnaireState = {
  selected: Record<string, string>;
  inputs: Record<string, string>;
  checks: Record<string, boolean>;
  uploads: Record<string, string>;
};
export type QuestionNode = {
  sheet: string; sourceRow: number; order: string; question: string | null;
  answer: string | null; fieldType: string | null; inputRule: string | null; required: boolean;
};
export type VisibleGroup = {
  key: string; sheet: string; question: string; fieldType: string; nodes: QuestionNode[]; required: boolean;
};
export type ProjectMotor = { id: string; tag: string; state: QuestionnaireState; mainSchemeCode?: string };
export type TraceEntry = { id: string; title: string; detail: string; source?: string };
export type DecisionTraceData = { inputs: TraceEntry[]; rules: TraceEntry[]; outputs: TraceEntry[] };
export type SchemeRow = { sourceRow: number; diagram1: string | null; diagram2: string | null };
export type Channel = {
  family: string; address: string; terminals: string[]; group: string;
  deviceRef: string; terminalStatus: 'source' | 'testDerived';
  commonKey: string | null; commonDesignation: string | null; commonRef: string | null; source: string;
};
export type Calculation = {
  schemaVersion: number; ruleFingerprint: string; status: string;
  blocks: Array<ProjectMotor & {
    errors: string[]; warnings: string[]; main?: SchemeRow;
    mainChoices: Array<{code: string; io: Record<string, number>; sourceRow: number}>;
    optional: Array<{row: SchemeRow; sourceOrder: string}>; channels: Channel[];
    loadIndex: string | null; specification: Array<{name: string; quantity: number; unit: string; source: string}>;
    decisionTrace: DecisionTraceData;
  }>;
  instances: Array<{id: string; blockId: string; tag: string; code: string; source: string; drawingKind: string; channels: Channel[]}>;
  errors: string[]; warnings: string[]; controllerFamily: string | null;
  modules: Array<{ref: string; brand: string; model: string; source: string; terminalStatus: string}>;
  plcHardware: Array<{ref: string; brand: string; model: string; quantity: number; source: string}>;
  io: Record<string, number>; canExport: boolean;
};
type ResponseData = {calculation: Calculation; forms: Record<string, VisibleGroup[]>; revision: string};
export const initialQuestionnaireState: QuestionnaireState = {selected: {}, inputs: {}, checks: {}, uploads: {}};
const empty: Calculation = {schemaVersion: 1, ruleFingerprint: '', status: 'draft', blocks: [], instances: [], errors: [], warnings: [], controllerFamily: null, modules: [], plcHardware: [], io: {}, canExport: false};

/** Used only to choose HTML input attributes from a server-returned field. */
export function numericInputRule(rule: string | null) {
  const match = /^float\|(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)$/.exec(rule || '');
  if (!match) return null;
  const [min, max, step] = match.slice(1).map(Number);
  return { min, max, step };
}

export function projectVersion(motors: ProjectMotor[], moduleCount = 0) {
  return JSON.stringify({motors: motors.map(({id, tag, state, mainSchemeCode}) => ({id, tag, state, ...(mainSchemeCode ? {mainSchemeCode} : {})})), moduleCount});
}

/** Preserve unchanged blocks so a calculation response does not rerender the whole field. */
export function applyNormalizedAnswers<T extends ProjectMotor>(motors: T[], normalized: ProjectMotor[]): T[] {
  const byId = new Map(normalized.map(motor => [motor.id, motor.state]));
  let changed = false;
  const result = motors.map(motor => {
    const state = byId.get(motor.id);
    if (!state || JSON.stringify(state) === JSON.stringify(motor.state)) return motor;
    changed = true;
    return { ...motor, state };
  });
  return changed ? result : motors;
}

export function useEngineering(motors: ProjectMotor[], moduleCount: number, accept: (motors: ProjectMotor[]) => void) {
  const version = projectVersion(motors, moduleCount);
  const [response, setResponse] = useState<(ResponseData & {version: string}) | null>(null);
  const [failure, setFailure] = useState<{version: string; attempt: number; message: string} | null>(null);
  const [attempt, retry] = useState(0);
  const receive = useEffectEvent((data: ResponseData, requestedVersion: string) => {
    if (version !== requestedVersion) return;
    const normalized = data.calculation.blocks;
    setFailure(null);
    setResponse({...data, version: projectVersion(normalized, moduleCount)});
    accept(normalized);
  });
  const fail = useEffectEvent((reason: unknown, requestedVersion: string) => {
    if (version !== requestedVersion) return;
    setFailure({version, attempt, message: reason instanceof Error ? reason.message : 'Сервер расчёта недоступен'});
  });
  useEffect(() => {
    if (response?.version === version) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await fetch('/api/cad/calculate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: version, signal: controller.signal,
        });
        const data = await result.json();
        if (!result.ok) throw new Error(data.error || 'Ошибка расчёта');
        if (!controller.signal.aborted) receive(data, version);
      } catch (reason) {
        if (!controller.signal.aborted) fail(reason, version);
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [version, attempt, response?.version]);
  const error = failure?.version === version && failure.attempt === attempt ? failure.message : '';
  const ready = response?.version === version && !error;
  return {
    cabinet: ready ? response.calculation : empty,
    forms: response?.forms || {}, revision: response?.revision || 'загрузка…',
    version, ready, error, retry: () => retry(value => value + 1),
  };
}
