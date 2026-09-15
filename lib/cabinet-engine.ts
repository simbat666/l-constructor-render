import base from '@/data/motor-v2.json';
import bindings from '@/data/motor-field-bindings.json';
import {
  activeAnswers,
  engineSelections,
  selectedDiagramRows,
  selectedOptionalDiagramRows,
  questionnaireFingerprint,
  type QuestionnaireState,
} from '@/lib/questionnaire';
import {
  allocateControllerIo,
  type IoCounts,
  type Channel,
} from '@/lib/io-allocator';

export type SchemeRow = {
  sourceRow: number;
  io: IoCounts;
  diagram1: string | null;
  diagram2: string | null;
  controller: string | null;
  [key: string]: unknown;
};
export type MotorInstance = {
  id: string;
  tag: string;
  state: QuestionnaireState;
};
export type CadInstance = {
  drawingKind: 'electrical' | 'external';
  id: string;
  blockId: string;
  tag: string;
  code: string;
  source: string;
  channels: Channel[];
};
type Demand = {
  id: string;
  blockId: string;
  source: string;
  candidates: SchemeRow[];
};
type LoadCandidate = {
  sourceRow: number;
  loadIndex: string;
  currentFrom: number;
  currentTo: number;
};
const normalized = (value: unknown) =>
  String(value ?? '')
    .trim()
    .toLocaleLowerCase('ru');

/** The graph resolves visibility. Semantic keys resolve engineering meaning. */
function semanticFields(state: QuestionnaireState) {
  const fields = new Map<
    string,
    Array<{ value: string | boolean; source: string }>
  >();
  for (const answer of activeAnswers(state)) {
    const key =
      answer.engineKey ||
      bindings.fields.find(
        (binding) =>
          binding.sheet === answer.sheet &&
          normalized(binding.question) === normalized(answer.question),
      )?.key;
    if (!key) continue;
    fields.set(key, [
      ...(fields.get(key) || []),
      { value: answer.value, source: `${answer.sheet}:${answer.sourceRow}` },
    ]);
  }
  return fields;
}

function fieldValue(
  fields: Map<string, Array<{ value: string | boolean; source: string }>>,
  key: string,
) {
  const values = fields.get(key) || [];
  return values.length === 1 ? String(values[0].value) : null;
}

function singleRange<Row extends { currentFrom: number; currentTo: number }>(
  rows: Row[],
  current: number,
) {
  return rows.filter(
    (row) => current >= row.currentFrom && current <= row.currentTo,
  );
}

export function calculateCabinet(motors: MotorInstance[]) {
  const demands: Demand[] = [];
  const blocks = motors.map((motor) => {
    const selection = selectedDiagramRows(motor.state);
    const errors = [...selection.missing];
    const fields = semanticFields(motor.state);
    const warnings: string[] = [];
    if (!selection.rows.length && !errors.length)
      errors.push('В diagram 1 нет подходящей строки.');
    for (const [key, answers] of fields)
      if (new Set(answers.map((answer) => String(answer.value))).size > 1)
        errors.push(
          `Противоречивые значения ${key}: ${answers.map((answer) => answer.source).join(', ')}.`,
        );
    if (selection.rows.length)
      demands.push({
        id: `${motor.id}:main`,
        blockId: motor.id,
        source: 'diagram 1',
        candidates: selection.rows,
      });
    const optional = selectedOptionalDiagramRows(motor.state);
    for (const dimension of engineSelections(motor.state, 'diagram2.').keys()) {
      const candidates = optional
        .filter((item) => item.sourceOrder === dimension)
        .map((item) => item.row as SchemeRow);
      const quantityKey = (
        bindings.optionalQuantities as Record<string, string>
      )[dimension];
      const quantity = quantityKey
        ? Number(fields.get(quantityKey)?.[0]?.value ?? NaN)
        : 1;
      if (!Number.isInteger(quantity) || quantity < 1 || quantity > 30) {
        errors.push(
          `Для ${dimension} укажи целое количество 1…30; для отсутствующего датчика отключи его выбор.`,
        );
        continue;
      }
      if (!candidates.length)
        errors.push(`В diagram 2 нет варианта ${dimension}.`);
      for (let index = 0; index < quantity; index++)
        demands.push({
          id: `${motor.id}:${dimension}:${index + 1}`,
          blockId: motor.id,
          source: 'diagram 2',
          candidates,
        });
    }
    if (
      activeAnswers(motor.state).some(
        (answer) => answer.fieldType === 'uploadButton',
      )
    )
      errors.push(
        'Пользовательское УГО: сохранено только имя файла. Импорт геометрии ещё не реализован.',
      );
    return {
      ...motor,
      errors,
      warnings,
      fields,
      main: undefined as SchemeRow | undefined,
      optional: [] as Array<{ row: SchemeRow; sourceOrder: string }>,
      channels: [] as Channel[],
      loadIndex: null as string | null,
      specification: [] as Array<{
        name: string;
        quantity: number;
        unit: string;
        source: string;
      }>,
    };
  });
  // Incomplete blocks are reported and excluded from this provisional plan.
  // A full-cabinet export remains blocked until EVERY block is valid.
  const ready = demands.filter(
    (demand) =>
      !blocks.find((block) => block.id === demand.blockId)!.errors.length,
  );
  const controllerSets = ready.map(
    (demand) =>
      new Set(
        demand.candidates
          .map((row) => normalized(row.controller))
          .filter(Boolean),
      ),
  );
  const common = [...(controllerSets[0] || [])].filter((controller) =>
    controllerSets.every((set) => set.has(controller)),
  );
  const allocationError =
    ready.length && !common.length
      ? 'Нет общего семейства контроллера у выбранных схем.'
      : null;
  const plans = common.sort().map((controller) => ({
    controller,
    ...allocateControllerIo(
      ready.map((demand) => ({
        ...demand,
        candidates: demand.candidates.filter(
          (row) => normalized(row.controller) === controller,
        ),
      })),
    ),
  }));
  // Compare logical family count, not hardware price or physical module capacity.
  const plan = plans
    .filter((item) => !item.error)
    .sort(
      (a, b) =>
        Object.values(a.totals || {}).filter(Boolean).length -
        Object.values(b.totals || {}).filter(Boolean).length,
    )[0];
  const errors = [
    ...blocks.flatMap((block) =>
      block.errors.map((error) => `${block.tag}: ${error}`),
    ),
    ...(allocationError ? [allocationError] : []),
    ...(!plan && plans.length
      ? plans.map((item) => item.error || 'Ошибка I/O')
      : []),
  ];
  const instances: CadInstance[] = [];
  for (const allocation of plan?.allocations || []) {
    const demand = ready.find((item) => item.id === allocation.id)!;
    const block = blocks.find((item) => item.id === demand.blockId)!;
    if (demand.source === 'diagram 1') block.main = allocation.scheme;
    else
      block.optional.push({ row: allocation.scheme, sourceOrder: demand.id });
    block.channels.push(...allocation.channels);
    [allocation.scheme.diagram1, allocation.scheme.diagram2].forEach(
      (code, index) => {
        if (code?.trim())
          instances.push({
            drawingKind: index === 0 ? 'electrical' : 'external',
            id: `${demand.id}:${index + 1}`,
            blockId: block.id,
            tag: block.tag,
            code: code.trim(),
            source: `${demand.source}:${allocation.scheme.sourceRow}`,
            channels: allocation.channels,
          });
      },
    );
  }
  for (const block of blocks) {
    if (!block.main) continue;
    const main = block.main;
    const rated = Number(block.fields.get('motor.ratedCurrent')?.[0]?.value);
    const start = fieldValue(block.fields, 'diagram1.start');
    const location = fieldValue(block.fields, 'diagram1.location');
    const vfdSelection = fieldValue(block.fields, 'vfd.selection');
    const isVfd = normalized(start) === normalized('Пуск через ЧП');
    let candidates: LoadCandidate[] = [];
    let vfd: (typeof base.manualVfd)[number] | (typeof base.automaticVfd)[number] | null = null;
    let vfdSource: 'sp1' | 'sp2' | null = null;
    let indexSource = 'Load index 1';

    if (!isVfd) {
      // Copy code meanings from the chosen source row: never infer them from order.
      candidates = singleRange(
        base.loadIndex1.filter(
          (row) =>
            row.startCode === main.startCode &&
            row.voltageCode === main.voltageCode &&
            row.tripCode === main.tripCode,
        ),
        rated,
      );
    } else if (
      normalized(location) === normalized('Внутри шкафа') &&
      normalized(vfdSelection) === normalized('Ручной')
    ) {
      indexSource = 'sp1';
      const model = fieldValue(block.fields, 'vfd.model');
      const matches = base.manualVfd.filter(
        (row) =>
          normalized(row.location) === normalized(location) &&
          normalized(row.selection) === normalized(vfdSelection) &&
          normalized(row.model) === normalized(model),
      );
      if (matches.length === 1) {
        vfd = matches[0];
        vfdSource = 'sp1';
        candidates = [{ ...matches[0], currentFrom: rated, currentTo: rated }];
      } else {
        block.warnings.push(
          matches.length
            ? `Выбранная модель ЧП в sp1 неоднозначна: строки ${matches.map((row) => row.sourceRow).join(', ')}.`
            : 'В sp1 нет однозначного ЧП для выбранной ручной модели.',
        );
      }
    } else {
      indexSource = 'Load index 2';
      const inputCurrent =
        normalized(location) === normalized('Внутри шкафа')
          ? rated * (1 + Number(fieldValue(block.fields, 'vfd.reserve') || 0) / 100)
          : Number(fieldValue(block.fields, 'vfd.inputCurrent'));
      const indexRows = base.loadIndex2.filter(
        (row) =>
          row.startCode === main.startCode &&
          row.voltageCode === main.voltageCode &&
          (!vfdSelection || normalized(row.selection) === normalized(vfdSelection)),
      );
      candidates = singleRange(indexRows, inputCurrent);
      if (candidates.length === 1 && normalized(location) === normalized('Внутри шкафа')) {
        const brand = fieldValue(block.fields, 'vfd.brand');
        const matches = base.automaticVfd.filter(
          (row) =>
            normalized(row.location) === normalized(location) &&
            normalized(row.selection) === normalized(vfdSelection) &&
            normalized(row.brand) === normalized(brand) &&
            row.loadIndex === candidates[0].loadIndex,
        );
        if (matches.length === 1) {
          vfd = matches[0];
          vfdSource = 'sp2';
        } else
          block.warnings.push(
            matches.length
              ? `Подбор ЧП из sp2 неоднозначен: строки ${matches.map((row) => row.sourceRow).join(', ')}.`
              : 'В sp2 нет ЧП для выбранного бренда и индекса нагрузки.',
          );
      }
    }
    if (candidates.length === 1) {
      block.loadIndex = candidates[0].loadIndex;
      block.specification = base.mainSpecification
        .filter(
          (row) =>
            row.diagram1 === main.diagram1 && row.loadIndex === block.loadIndex,
        )
        .map((row) => ({
          name: row.name,
          quantity: row.quantity,
          unit: row.unit,
          source: `sp4:${row.sourceRow}`,
        }));
      if (!block.specification.length)
        block.warnings.push(
          `Нет спецификации sp4 для ${main.diagram1} + ${block.loadIndex}.`,
        );
      if (vfd && vfdSource)
        block.specification.push({
          name: vfd.name,
          quantity: vfd.quantity,
          unit: vfd.unit,
          source: `${vfdSource}:${vfd.sourceRow}`,
        });
    } else if (!block.warnings.length)
      block.warnings.push(
        candidates.length
          ? `Граница диапазонов ${indexSource} неоднозначна: строки ${candidates.map((row) => row.sourceRow).join(', ')}. Индекс не угадан.`
          : `Индекс нагрузки не рассчитан: нет однозначного правила ${indexSource}.`,
      );
    for (const optional of block.optional) {
      const rows = base.sensorSpecification.filter(
        (row) => row.diagram1 === optional.row.diagram1,
      );
      block.specification.push(
        ...rows.map((row) => ({
          name: row.name,
          quantity: row.quantity,
          unit: row.unit,
          source: `sp3:${row.sourceRow} / ${optional.sourceOrder}`,
        })),
      );
      if (!rows.length)
        block.warnings.push(
          `Нет спецификации sp3 для ${optional.row.diagram1}.`,
        );
    }
  }
  return {
    schemaVersion: 1,
    ruleFingerprint: questionnaireFingerprint,
    status: 'draft' as const,
    blocks: blocks.map(({ fields: _fields, ...block }) => block),
    instances,
    errors,
    warnings: [
      'DXF — черновая компоновка шаблонов, не выпущенная КД. Маркировки и номиналы внутри исходных шаблонов пока не параметризованы.',
      'I/O — логические номера сигналов, не физические клеммы. Модели контроллеров, ёмкость модулей и их цена ещё не заданы.',
    ],
    controllerFamily: plan?.controller || null,
    io: plan?.totals || {},
    canExport: motors.length > 0 && errors.length === 0 && instances.length > 0,
  };
}
