import ruleBase from "@/data/motor-v2.json";

export type StartMethod = "direct" | "vfd" | "soft" | "star" | "triac";
export type ThermalContact = "none" | "di0" | "di24";
export type SensorWire = "none" | "2" | "3" | "4";

export type MotorInput = {
  name: string;
  start: StartMethod;
  voltage: 230 | 400;
  current: number;
  trip: "thermal" | "magnetic";
  vfdLocation: "inside" | "outside";
  controlInput: "di0" | "di24";
  actuatorOutput: "relay" | "transistor";
  thermalContact: ThermalContact;
  ptc: boolean;
  windingPt100: SensorWire;
  bearingPt100: SensorWire;
};

type Rule = Record<string, any>;
type Io = Record<string, number>;

const startCode: Record<StartMethod, number> = { direct: 0, vfd: 1, soft: 2, star: 3, triac: 4 };
const voltageCode = (voltage: 230 | 400) => voltage === 230 ? 0 : 1;
const tripCode = (trip: MotorInput["trip"]) => trip === "thermal" ? 0 : 1;
const locationCode = (input: MotorInput) => input.start === "vfd" ? (input.vfdLocation === "inside" ? 1 : 2) : 0;
const wireCode = (wire: SensorWire) => wire === "none" ? null : Number(wire) - 2;

function equalIo(value: number | undefined, required: number) {
  return Number(value || 0) === required;
}

function findSingle(rows: Rule[], subject: string) {
  if (rows.length === 1) return { row: rows[0] };
  if (!rows.length) return { error: `В базе нет строки для «${subject}».` };
  return { error: `В базе ${rows.length} строк для «${subject}» — правило выбора неоднозначно.`, candidates: rows.map((row) => row.sourceRow) };
}

function selectLoadIndex(input: MotorInput) {
  const rows: Rule[] = input.start === "vfd" ? ruleBase.loadIndex2 : ruleBase.loadIndex1;
  const matches = rows.filter((row) =>
    row.voltageCode === voltageCode(input.voltage)
    && row.startCode === startCode[input.start]
    && (input.start === "vfd" || row.tripCode === tripCode(input.trip))
    && input.current >= row.currentFrom
    && input.current <= row.currentTo,
  );
  return findSingle(matches, `индекс нагрузки при ${input.current} А`);
}

function selectMainDiagram(input: MotorInput) {
  const matches = ruleBase.diagrams1.filter((row: Rule) =>
    row.startCode === startCode[input.start]
    && row.locationCode === locationCode(input)
    && row.voltageCode === voltageCode(input.voltage)
    && row.tripCode === tripCode(input.trip)
    && equalIo(row.io.di0, input.controlInput === "di0" ? 1 : 0)
    && equalIo(row.io.di24, input.controlInput === "di24" ? 1 : 0)
    && equalIo(row.io.doRelay, input.actuatorOutput === "relay" ? 1 : 0)
    && equalIo(row.io.doTransistor, input.actuatorOutput === "transistor" ? 1 : 0),
  );
  return findSingle(matches, "главная электрическая схема");
}

function selectSensorDiagram(rows: Rule[], title: string) {
  return findSingle(rows, title);
}

function addIo(target: Io, source: Io) {
  for (const [key, value] of Object.entries(source)) target[key] = (target[key] || 0) + Number(value || 0);
}

function sumLines(rows: Rule[]) {
  const result: Record<string, number> = {};
  for (const row of rows) {
    try {
      const lines = JSON.parse(row.linesAmount || "{}");
      for (const [key, value] of Object.entries(lines)) result[key] = (result[key] || 0) + Number(value || 0);
    } catch {
      // Invalid JSON must not silently alter a calculated output; it remains visible in traceability.
    }
  }
  return result;
}

function aggregateSpecification(rows: Rule[]) {
  const groups = new Map<string, { name: string; quantity: number; unit: string; lde: string; sources: string[] }>();
  for (const row of rows) {
    const key = [row.name, row.unit, row.lde].join("|");
    const present = groups.get(key) || { name: row.name, quantity: 0, unit: row.unit, lde: row.lde || "—", sources: [] };
    present.quantity += Number(row.quantity || 0);
    present.sources.push(`${row.diagram1 ? "sp3" : "sp2"}:${row.sourceRow}`);
    groups.set(key, present);
  }
  return [...groups.values()].sort((a, b) => a.lde.localeCompare(b.lde) || a.name.localeCompare(b.name, "ru"));
}

export function calculateMotor(input: MotorInput) {
  const problems: string[] = [];
  if (!input.name.trim()) problems.push("Укажи наименование шкафа.");
  if (!Number.isFinite(input.current) || input.current <= 0) problems.push("Ток должен быть числом больше нуля.");
  if (input.start === "vfd" && input.voltage !== 400) problems.push("В этой базе для ЧП есть только питание 400 В (3L/PE).");
  if (input.start === "vfd" && input.trip !== "magnetic") problems.push("Для ЧП в `diagram 1` предусмотрена магнитная защита.");
  if (problems.length) return { ok: false as const, problems };

  const loadResult = selectLoadIndex(input);
  const mainResult = selectMainDiagram(input);
  if ("error" in loadResult) problems.push(loadResult.error + (loadResult.candidates ? ` Строки: ${loadResult.candidates.join(", ")}.` : ""));
  if ("error" in mainResult) problems.push(mainResult.error + (mainResult.candidates ? ` Строки: ${mainResult.candidates.join(", ")}.` : ""));
  if (problems.length) return { ok: false as const, problems };

  const optional: Rule[] = [];
  const sensorRequests: Array<{ title: string; rows: Rule[] }> = [];
  if (input.thermalContact !== "none") sensorRequests.push({
    title: "термоконтакт двигателя",
    rows: ruleBase.diagrams2.filter((row: Rule) => row.thermalCode === 1 && equalIo(row.io.di0, input.thermalContact === "di0" ? 1 : 0) && equalIo(row.io.di24, input.thermalContact === "di24" ? 1 : 0)),
  });
  if (input.ptc) sensorRequests.push({ title: "PTC", rows: ruleBase.diagrams2.filter((row: Rule) => row.ptcCode === 1) });
  if (input.windingPt100 !== "none") sensorRequests.push({ title: "Pt100 обмотки", rows: ruleBase.diagrams2.filter((row: Rule) => row.windingWireCode === wireCode(input.windingPt100)) });
  if (input.bearingPt100 !== "none") sensorRequests.push({ title: "Pt100 подшипников", rows: ruleBase.diagrams2.filter((row: Rule) => row.bearingWireCode === wireCode(input.bearingPt100)) });
  for (const request of sensorRequests) {
    const selected = selectSensorDiagram(request.rows, request.title);
    if ("error" in selected) problems.push(selected.error);
    else optional.push(selected.row);
  }
  if (problems.length) return { ok: false as const, problems };

  const load = loadResult.row;
  const main = mainResult.row;
  const diagrams = [main, ...optional];
  const diagramCodes = [...new Set(diagrams.map((row) => row.diagram1))];
  const specificationRows = [
    ...ruleBase.sp3.filter((row: Rule) => row.diagram1 === main.diagram1 && row.loadIndex === load.loadIndex),
    ...ruleBase.sp2.filter((row: Rule) => diagramCodes.includes(row.diagram1)),
  ];
  const vfd = input.start === "vfd" ? ruleBase.sp1.find((row: Rule) => row.selectionCode === 1 && row.loadIndex === load.loadIndex) : undefined;
  if (vfd) specificationRows.push({ ...vfd, diagram1: null });
  const totalIo: Io = {};
  diagrams.forEach((row) => addIo(totalIo, row.io));

  return {
    ok: true as const,
    input,
    loadIndex: load,
    main,
    optional,
    vfd,
    diagrams: diagramCodes,
    io: totalIo,
    lines: sumLines(diagrams),
    specification: aggregateSpecification(specificationRows),
    trace: {
      load: `Load index ${input.start === "vfd" ? 2 : 1}:${load.sourceRow}`,
      diagrams: diagrams.map((row) => `diagram ${row === main ? 1 : 2}:${row.sourceRow}`),
      specificationRows: specificationRows.length,
    },
  };
}
