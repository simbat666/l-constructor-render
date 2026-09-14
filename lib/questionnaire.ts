import ruleBase from '@/data/motor-v2.json';
import bindings from '@/data/motor-field-bindings.json';

export type QuestionNode = {
  sheet: string;
  sourceRow: number;
  order: string;
  question: string | null;
  binds: string | null;
  answer: string | null;
  fieldType: string | null;
  inputRule: string | null;
  engineKey: string | null;
  required: boolean;
};
export type QuestionnaireState = {
  selected: Record<string, string>;
  inputs: Record<string, string>;
  checks: Record<string, boolean>;
  uploads: Record<string, string>;
};
export type VisibleGroup = {
  key: string;
  sheet: string;
  question: string;
  fieldType: string;
  nodes: QuestionNode[];
  required: boolean;
};

const sheets = ['Ques 1', 'Ques 2', 'Ques 3', 'Ques 4'] as const;
const nodes: QuestionNode[] = sheets.flatMap((sheet) =>
  ((ruleBase as any)[`questions${sheet.slice(-1)}`] || []).map((node: any) => ({
    ...node,
    sheet,
  })),
);
const byOrder = new Map(nodes.map((node) => [node.order, node]));
const nodeKey = (node: QuestionNode) => `${node.sheet}:${node.order}`;
const groupKey = (node: QuestionNode) =>
  `${node.sheet}:${node.question}:${node.fieldType}`;
const selectorTypes = new Set(['list', 'radio_group']);
const splitBinds = (binds: string | null) =>
  (binds || '')
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean);
const normalize = (value: unknown) =>
  String(value || '')
    .trim()
    .toLocaleLowerCase('ru');

/** Resolves engineering meaning without making Excel row/order a business key. */
export function semanticKey(node: QuestionNode) {
  return (
    node.engineKey ||
    bindings.fields.find(
      (binding) =>
        binding.sheet === node.sheet &&
        normalize(binding.question) === normalize(node.question),
    )?.key ||
    null
  );
}

export const initialQuestionnaireState: QuestionnaireState = {
  selected: {},
  inputs: {},
  checks: {},
  uploads: {},
};
export const questionnaireRevision =
  (ruleBase as any).source?.revision || 'не указана';
export const questionnaireFingerprint = ruleBase.source.sha256;

export type NumericInputRule = { min: number; max: number; step: number };

export function numericInputRule(rule: string | null): NumericInputRule | null {
  const match =
    /^float\|(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)$/.exec(
      rule || '',
    );
  if (!match) return null;
  const [min, max, step] = match.slice(1).map(Number);
  return Number.isFinite(min) &&
    Number.isFinite(max) &&
    Number.isFinite(step) &&
    min <= max &&
    step > 0
    ? { min, max, step }
    : null;
}

function inputRuleError(node: QuestionNode, raw: string | undefined) {
  const value = raw?.trim();
  if (!value) return null;
  const rule = numericInputRule(node.inputRule || node.answer);
  if (!rule) {
    const length = /^string\|(\d+)\.\.(\d+)\|?$/.exec(
      node.inputRule || node.answer || '',
    );
    return length &&
      (value.length < Number(length[1]) || value.length > Number(length[2]))
      ? `длина ${length[1]}…${length[2]} символов`
      : null;
  }
  const number = Number(value);
  if (!Number.isFinite(number) || number < rule.min || number > rule.max)
    return `допустимо ${rule.min}…${rule.max}`;
  const steps = (number - rule.min) / rule.step;
  return Math.abs(steps - Math.round(steps)) >
    Math.max(1e-8, Math.abs(rule.step) * 1e-7)
    ? `шаг ${rule.step}`
    : null;
}

export function visibleQuestionGroups(state: QuestionnaireState) {
  // Roots and transitions are an OL graph: changing `order` plus `binds` in
  // Excel changes the visible questionnaire without a TypeScript release.
  const referenced = new Set(nodes.flatMap((node) => splitBinds(node.binds)));
  const distance = new Map(
    nodes
      .filter(
        (node) =>
          node.sheet === 'Ques 1' &&
          !referenced.has(node.order) &&
          normalize(node.answer) !== 'unused',
      )
      .map((node) => [node.order, 0]),
  );
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      const nodeDistance = distance.get(node.order);
      if (nodeDistance === undefined) continue;
      let shouldFollow = false;
      if (selectorTypes.has(node.fieldType || ''))
        shouldFollow = state.selected[groupKey(node)] === node.order;
      if (node.fieldType === 'input')
        shouldFollow =
          Boolean(state.inputs[nodeKey(node)]?.trim()) &&
          !inputRuleError(node, state.inputs[nodeKey(node)]);
      if (node.fieldType === 'check_box')
        shouldFollow = Boolean(state.checks[nodeKey(node)]);
      if (node.fieldType === 'uploadButton')
        shouldFollow = Boolean(state.uploads[nodeKey(node)]);
      if (!shouldFollow) continue;
      for (const order of splitBinds(node.binds)) {
        const nextDistance = nodeDistance + 1;
        if (
          byOrder.has(order) &&
          (distance.get(order) === undefined ||
            nextDistance < distance.get(order)!)
        ) {
          distance.set(order, nextDistance);
          changed = true;
        }
      }
    }
  }
  const activeNodes = nodes.filter(
    (node) => distance.has(node.order) && node.question && node.fieldType,
  );
  // A shared graph node can make a generic downstream input and a more
  // specific branch input visible together. Keep the closest semantic input:
  // the direct branch is the one that owns its range/validation.
  const closestSemanticInput = new Map<string, number>();
  for (const node of activeNodes) {
    const key = node.fieldType === 'input' ? semanticKey(node) : null;
    const nodeDistance = distance.get(node.order)!;
    if (key && nodeDistance < (closestSemanticInput.get(key) ?? Infinity))
      closestSemanticInput.set(key, nodeDistance);
  }
  const groups = new Map<string, VisibleGroup>();
  for (const node of activeNodes) {
    const semanticInput = node.fieldType === 'input' ? semanticKey(node) : null;
    if (
      semanticInput &&
      distance.get(node.order)! > closestSemanticInput.get(semanticInput)!
    )
      continue;
    const key = selectorTypes.has(node.fieldType || '')
      ? groupKey(node)
      : nodeKey(node);
    const present = groups.get(key) || {
      key,
      sheet: node.sheet,
      question: node.question!,
      fieldType: node.fieldType!,
      nodes: [],
      required: false,
    };
    present.nodes.push(node);
    groups.set(key, present);
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      required: group.nodes.some((node) => node.required),
    }))
    .sort(
      (a, b) =>
        sheetIndex(a.sheet) - sheetIndex(b.sheet) ||
        a.nodes[0].order.localeCompare(b.nodes[0].order, 'en', {
          numeric: true,
        }),
    );
}

function groupAnswered(state: QuestionnaireState, group: VisibleGroup) {
  const first = group.nodes[0];
  if (selectorTypes.has(group.fieldType))
    return group.nodes.some((node) => node.order === state.selected[group.key]);
  if (group.fieldType === 'input')
    return (
      Boolean(state.inputs[nodeKey(first)]?.trim()) &&
      !inputRuleError(first, state.inputs[nodeKey(first)])
    );
  if (group.fieldType === 'check_box')
    return Object.prototype.hasOwnProperty.call(state.checks, nodeKey(first));
  if (group.fieldType === 'uploadButton')
    return Boolean(state.uploads[nodeKey(first)]);
  return true;
}

export function missingRequiredAnswers(state: QuestionnaireState) {
  return visibleQuestionGroups(state)
    .filter(
      (group) =>
        (group.required && !groupAnswered(state, group)) ||
        (group.fieldType === 'input' &&
          Boolean(
            inputRuleError(
              group.nodes[0],
              state.inputs[nodeKey(group.nodes[0])],
            ),
          )),
    )
    .map((group) => {
      const error =
        group.fieldType === 'input'
          ? inputRuleError(
              group.nodes[0],
              state.inputs[nodeKey(group.nodes[0])],
            )
          : null;
      return error ? `${group.question} (${error})` : group.question;
    });
}

export function selectedOrder(
  state: QuestionnaireState,
  sheet: string,
  question: string,
) {
  return (
    state.selected[`${sheet}:${question}:list`] ||
    state.selected[`${sheet}:${question}:radio_group`] ||
    null
  );
}

export function engineSelections(state: QuestionnaireState, prefix: string) {
  const result = new Map<string, string>();
  for (const node of visibleQuestionGroups(state).flatMap(
    (group) => group.nodes,
  )) {
    const resolvedKey = semanticKey(node);
    if (!resolvedKey?.startsWith(prefix)) continue;
    const key = resolvedKey.slice(prefix.length);
    if (selectorTypes.has(node.fieldType || '')) {
      const selected = state.selected[groupKey(node)];
      if (selected === node.order && node.answer) result.set(key, node.answer);
    }
    if (node.fieldType === 'check_box' && state.checks[nodeKey(node)] === true)
      result.set(key, 'да');
  }
  return result;
}

function matchesDimensions(
  row: Record<string, unknown>,
  dimensions: Map<string, string>,
) {
  return [...dimensions.entries()].every(
    ([key, selected]) => normalize(row[key]) === normalize(selected),
  );
}

export function selectedDiagramRows(state: QuestionnaireState) {
  const missing = missingRequiredAnswers(state);
  const dimensions = engineSelections(state, 'diagram1.');
  if (missing.length) return { rows: [], missing };
  if (!dimensions.size)
    return { rows: [], missing: ['Нет выбранных параметров для diagram 1'] };
  return {
    rows: (ruleBase.diagrams1 as any[]).filter((row) =>
      matchesDimensions(row, dimensions),
    ),
    missing,
  };
}

export function selectedOptionalDiagramRows(state: QuestionnaireState) {
  const rows: Array<{ row: any; sourceOrder: string }> = [];
  const dimensions = engineSelections(state, 'diagram2.');
  for (const [dimension, selected] of dimensions)
    for (const row of ruleBase.diagrams2 as any[])
      if (normalize(row[dimension]) === normalize(selected))
        rows.push({ row, sourceOrder: dimension });
  return rows;
}

export function question4Placeholder() {
  return nodes.find((node) => node.sheet === 'Ques 4') || null;
}
function sheetIndex(sheet: string) {
  return sheets.indexOf(sheet as (typeof sheets)[number]);
}

/** Hidden answers are discarded, not kept as an invisible source of rules. */
export function pruneQuestionnaireState(
  state: QuestionnaireState,
): QuestionnaireState {
  const result: QuestionnaireState = {
    selected: {},
    inputs: {},
    checks: {},
    uploads: {},
  };
  for (const group of visibleQuestionGroups(state)) {
    const node = group.nodes[0];
    if (selectorTypes.has(group.fieldType)) {
      if (group.nodes.some((item) => item.order === state.selected[group.key]))
        result.selected[group.key] = state.selected[group.key];
    } else {
      const part = (
        {
          input: 'inputs',
          check_box: 'checks',
          uploadButton: 'uploads',
        } as const
      )[group.fieldType as 'input'];
      const key = nodeKey(node);
      if (part && Object.prototype.hasOwnProperty.call(state[part], key))
        Object.assign(result[part], { [key]: state[part][key] });
    }
  }
  return result;
}

export function activeAnswers(state: QuestionnaireState) {
  return visibleQuestionGroups(state).flatMap((group) =>
    group.nodes.flatMap((node) => {
      let value: string | boolean | undefined;
      if (
        selectorTypes.has(group.fieldType) &&
        state.selected[group.key] === node.order
      )
        value = node.answer ?? undefined;
      if (group.fieldType === 'input') value = state.inputs[nodeKey(node)];
      if (group.fieldType === 'check_box') value = state.checks[nodeKey(node)];
      if (group.fieldType === 'uploadButton')
        value = state.uploads[nodeKey(node)];
      return value === undefined || value === '' ? [] : [{ ...node, value }];
    }),
  );
}
