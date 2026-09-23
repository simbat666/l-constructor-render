/** UI contracts only. Engineering rules and graph traversal live in Python. */
import { useEffect, useRef, useState } from 'react';

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
export type ProjectMotor = { id: string; tag: string; state: QuestionnaireState };
type TraceEntry = { id: string; title: string; detail: string; source?: string };
type SchemeRow = Record<string, any>;
export type Channel = {
  family: string; address: string; terminals: string[]; group: string;
  commonKey: string | null; source: string;
};
export type Calculation = {
  schemaVersion: number; ruleFingerprint: string; status: string;
  blocks: Array<ProjectMotor & {
    errors: string[]; warnings: string[]; main?: SchemeRow;
    optional: Array<{row: SchemeRow; sourceOrder: string}>; channels: Channel[];
    loadIndex: string | null; specification: Array<{name: string; quantity: number; unit: string; source: string}>;
    decisionTrace: { inputs: TraceEntry[]; rules: TraceEntry[]; outputs: TraceEntry[] };
  }>;
  instances: Array<{id: string; blockId: string; tag: string; code: string; source: string; drawingKind: string; channels: Channel[]}>;
  errors: string[]; warnings: string[]; controllerFamily: string | null; io: Record<string, number>; canExport: boolean;
};
type ResponseData = {calculation: Calculation; forms: Record<string, VisibleGroup[]>; revision: string};
export const initialQuestionnaireState: QuestionnaireState = {selected: {}, inputs: {}, checks: {}, uploads: {}};
const empty: Calculation = {schemaVersion: 1, ruleFingerprint: '', status: 'draft', blocks: [], instances: [], errors: [], warnings: [], controllerFamily: null, io: {}, canExport: false};

/** Used only to choose HTML input attributes from a server-returned field. */
export function numericInputRule(rule: string | null) {
  const match = /^float\|(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)$/.exec(rule || '');
  if (!match) return null;
  const [min, max, step] = match.slice(1).map(Number);
  return { min, max, step };
}

export function projectVersion(motors: ProjectMotor[]) {
  return JSON.stringify(motors.map(({id, tag, state}) => ({id, tag, state})));
}

export function useEngineering(motors: ProjectMotor[], accept: (motors: ProjectMotor[]) => void) {
  const version = projectVersion(motors);
  const latest = useRef(version);
  latest.current = version;
  const acceptRef = useRef(accept);
  acceptRef.current = accept;
  const [response, setResponse] = useState<(ResponseData & {version: string}) | null>(null);
  const [error, setError] = useState('');
  const [attempt, retry] = useState(0);
  useEffect(() => {
    if (response?.version === version) return;
    const controller = new AbortController();
    setError('');
    const timer = setTimeout(async () => {
      try {
        const result = await fetch('/api/cad/calculate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({motors: JSON.parse(version)}), signal: controller.signal,
        });
        const data = await result.json();
        if (!result.ok) throw new Error(data.error || 'Ошибка расчёта');
        if (controller.signal.aborted || latest.current !== version) return;
        const normalized = data.calculation.blocks as ProjectMotor[];
        const acceptedVersion = projectVersion(normalized);
        setResponse({...data, version: acceptedVersion});
        acceptRef.current(normalized);
      } catch (reason) {
        if (!controller.signal.aborted && latest.current === version)
          setError(reason instanceof Error ? reason.message : 'Сервер расчёта недоступен');
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [version, attempt, response?.version]);
  const ready = response?.version === version && !error;
  return {
    cabinet: ready ? response.calculation : empty,
    forms: response?.forms || {}, revision: response?.revision || 'загрузка…',
    ready, error, retry: () => retry(value => value + 1),
  };
}
