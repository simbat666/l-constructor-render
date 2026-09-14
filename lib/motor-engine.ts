import ruleBase from "@/data/motor-v2.json";

/** @deprecated Historical standalone prototype. The web application uses
 * calculateCabinet in cabinet-engine.ts. Do not connect this parallel engine
 * to exports; migrate useful rules with source-backed regression tests. */

export type StartMethod = "direct" | "vfd" | "soft" | "star" | "triac";
export type SensorWire = "none" | "2" | "3" | "4";
export type ControlInput = "unset" | "di0" | "di24";
export type ActuatorOutput = "unset" | "relay" | "transistor";

export type MotorInput = {
  name: string;
  marking: string;
  deviceTag: string;
  loadStatus: "primary" | "backup";
  processType: "other" | "pump" | "fan" | "compressor" | "stirrer" | "conveyor";
  customProcessType: string;
  start: StartMethod;
  voltage: 230 | 400;
  ratedCurrent: number;
  ratedPower: number;
  trip: "thermal" | "magnetic";
  vfdLocation: "inside" | "outside";
  vfdSelection: "automatic" | "manual";
  vfdBrand: "VEDA 1" | "VEDA 2";
  vfdManualModel: string;
  vfdReserve: number;
  vfdInputCurrent: number;
  controlInput: ControlInput;
  actuatorOutput: ActuatorOutput;
  thermalContact: boolean;
  ptc: boolean;
  windingPt100: { wire: SensorWire; quantity: number };
  bearingPt100: { wire: SensorWire; quantity: number };
};

type Rule = Record<string, any>;
type Io = Record<string, number>;
type SelectedDiagram = { row: Rule; multiplier: number; title: string; source: "diagram 1" | "diagram 2" };

const startCode: Record<StartMethod, number> = { direct: 0, vfd: 1, soft: 2, star: 3, triac: 4 };
const voltageCode = (voltage: 230 | 400) => voltage === 230 ? 0 : 1;
const tripCode = (trip: MotorInput["trip"]) => trip === "thermal" ? 0 : 1;
const locationCode = (input: MotorInput) => input.start === "vfd" ? (input.vfdLocation === "inside" ? 1 : 2) : 0;
const wireCode = (wire: SensorWire) => wire === "none" ? null : Number(wire) - 2;

function equalIo(value: number | undefined, required: number) { return Number(value || 0) === required; }
function findSingle(rows: Rule[], subject: string): { row: Rule; error?: never; candidates?: never } | { error: string; candidates?: number[]; row?: never } {
  if (rows.length === 1) return { row: rows[0] };
  if (!rows.length) return { error: `В базе нет строки для «${subject}».` };
  return { error: `В базе ${rows.length} строк для «${subject}» — правило выбора неоднозначно.`, candidates: rows.map((row) => row.sourceRow) };
}
function addIo(target: Io, source: Io, multiplier = 1) { for (const [key, value] of Object.entries(source)) target[key] = (target[key] || 0) + Number(value || 0) * multiplier; }
function rangeMatch(rows: Rule[], current: number, subject: string) { return findSingle(rows.filter((row) => current >= row.currentFrom && current <= row.currentTo), subject); }

function selectLoadIndex(input: MotorInput) {
  if (input.start !== "vfd") return rangeMatch(ruleBase.loadIndex1.filter((row: Rule) => row.voltageCode === voltageCode(input.voltage) && row.startCode === startCode[input.start] && row.tripCode === tripCode(input.trip)), input.ratedCurrent, `индекс нагрузки при ${input.ratedCurrent} А`);
  if (input.vfdLocation === "inside" && input.vfdSelection === "manual") {
    const manual = findSingle(ruleBase.manualVfd.filter((row: Rule) => row.model === input.vfdManualModel), `ручной ЧП «${input.vfdManualModel}»`);
    if ("error" in manual) return manual;
    return { row: { loadIndex: manual.row.loadIndex, sourceRow: manual.row.sourceRow, selectionSource: "sp1" }, manualVfd: manual.row };
  }
  const inputCurrent = input.vfdLocation === "inside" && input.vfdSelection === "automatic" ? input.ratedCurrent * (1 + input.vfdReserve / 100) : input.vfdInputCurrent;
  const index = rangeMatch(ruleBase.loadIndex2.filter((row: Rule) => row.voltageCode === 1 && row.startCode === 1), inputCurrent, `индекс нагрузки ЧП при ${inputCurrent.toFixed(2)} А`);
  return index;
}

function selectMainDiagram(input: MotorInput) {
  if (input.controlInput === "unset" || input.actuatorOutput === "unset") return { error: "В ОЛ нет условия для исполнения цепей управления. Выбери DI 0/24 В и релейный/транзисторный выход в параметрах конструктора." };
  return findSingle(ruleBase.diagrams1.filter((row: Rule) =>
    row.startCode === startCode[input.start] && row.locationCode === locationCode(input) && row.voltageCode === voltageCode(input.voltage) && row.tripCode === tripCode(input.trip)
    && equalIo(row.io.di0, input.controlInput === "di0" ? 1 : 0) && equalIo(row.io.di24, input.controlInput === "di24" ? 1 : 0)
    && equalIo(row.io.doRelay, input.actuatorOutput === "relay" ? 1 : 0) && equalIo(row.io.doTransistor, input.actuatorOutput === "transistor" ? 1 : 0),
  ), "главная электрическая схема");
}

function selectOptionalDiagrams(input: MotorInput) {
  const selected: SelectedDiagram[] = [];
  const problems: string[] = [];
  const add = (title: string, rows: Rule[], multiplier = 1) => {
    const result = findSingle(rows, title);
    if (result.error) problems.push(result.error);
    else if (result.row) selected.push({ row: result.row, multiplier, title, source: "diagram 2" });
  };
  if (input.thermalContact) add("термоконтакт двигателя", ruleBase.diagrams2.filter((row: Rule) => row.thermalCode === 1 && equalIo(row.io.di0, input.controlInput === "di0" ? 1 : 0) && equalIo(row.io.di24, input.controlInput === "di24" ? 1 : 0)));
  if (input.ptc) add("PTC", ruleBase.diagrams2.filter((row: Rule) => row.ptcCode === 1));
  if (input.windingPt100.wire !== "none" && input.windingPt100.quantity > 0) add("Pt100 обмотки", ruleBase.diagrams2.filter((row: Rule) => row.windingWireCode === wireCode(input.windingPt100.wire)), input.windingPt100.quantity);
  if (input.bearingPt100.wire !== "none" && input.bearingPt100.quantity > 0) add("Pt100 подшипников", ruleBase.diagrams2.filter((row: Rule) => row.bearingWireCode === wireCode(input.bearingPt100.wire)), input.bearingPt100.quantity);
  return { selected, problems };
}

function sumLines(rows: SelectedDiagram[]) {
  const result: Record<string, number> = {};
  for (const item of rows) try { for (const [key, value] of Object.entries(JSON.parse(item.row.linesAmount || "{}"))) result[key] = (result[key] || 0) + Number(value || 0) * item.multiplier; } catch { /* invalid source JSON stays traceable, never guessed */ }
  return result;
}

function aggregateSpecification(rows: Array<Rule & { source: string; multiplier?: number }>) {
  const groups = new Map<string, { name: string; quantity: number; unit: string; lde: string; sources: string[] }>();
  for (const row of rows) {
    const key = [row.name, row.unit, row.lde].join("|");
    const existing = groups.get(key) || { name: row.name, quantity: 0, unit: row.unit, lde: row.lde || "—", sources: [] as string[] };
    existing.quantity += Number(row.quantity || 0) * Number(row.multiplier || 1);
    existing.sources.push(`${row.source}:${row.sourceRow}`);
    groups.set(key, existing);
  }
  return [...groups.values()].sort((a, b) => a.lde.localeCompare(b.lde) || a.name.localeCompare(b.name, "ru"));
}

export function calculateMotor(input: MotorInput) {
  const problems: string[] = [];
  if (!input.name.trim()) problems.push("Укажи наименование нагрузки.");
  if (!Number.isFinite(input.ratedCurrent) || input.ratedCurrent <= 0) problems.push("Укажи номинальный ток двигателя больше нуля.");
  if (input.start !== "vfd" && (!Number.isFinite(input.ratedPower) || input.ratedPower <= 0)) problems.push("Укажи электрическую номинальную мощность больше нуля.");
  if (input.processType === "other" && !input.customProcessType.trim()) problems.push("Для типа «Другой» укажи технологическое устройство.");
  if (input.start === "vfd" && input.voltage !== 400) problems.push("Для ЧП эта ОЛ предусматривает только 400 В (3L/PE).");
  if (input.start === "vfd" && input.trip !== "magnetic") problems.push("Для ЧП `diagram 1` предусматривает магнитную защиту.");
  if (input.start === "vfd" && input.vfdLocation === "inside" && input.vfdSelection === "automatic" && (input.vfdReserve < 0 || input.vfdReserve > 30)) problems.push("Запас ЧП должен быть от 0 до 30 %.");
  if (input.start === "vfd" && input.vfdLocation === "outside" && (!Number.isFinite(input.vfdInputCurrent) || input.vfdInputCurrent <= 0)) problems.push("Укажи входной ток ЧП больше нуля.");
  if (input.windingPt100.wire !== "none" && input.windingPt100.quantity < 1) problems.push("Укажи количество датчиков Pt100 обмотки.");
  if (input.bearingPt100.wire !== "none" && input.bearingPt100.quantity < 1) problems.push("Укажи количество датчиков Pt100 подшипников.");
  if (problems.length) return { ok: false as const, problems };

  const loadResult = selectLoadIndex(input);
  const mainResult = selectMainDiagram(input);
  if ("error" in loadResult) problems.push(loadResult.error + (loadResult.candidates ? ` Строки: ${loadResult.candidates.join(", ")}.` : ""));
  if ("error" in mainResult) problems.push(mainResult.error + (mainResult.candidates ? ` Строки: ${mainResult.candidates.join(", ")}.` : ""));
  if (problems.length || !loadResult.row || !mainResult.row) return { ok: false as const, problems };

  const optionalResult = selectOptionalDiagrams(input);
  if (optionalResult.problems.length) return { ok: false as const, problems: optionalResult.problems };
  const main: SelectedDiagram = { row: mainResult.row, multiplier: 1, title: "главная схема", source: "diagram 1" };
  const diagrams = [main, ...optionalResult.selected];
  const load = loadResult.row;

  let vfd: Rule | undefined;
  if (input.start === "vfd" && input.vfdLocation === "inside") {
    vfd = input.vfdSelection === "manual" ? ("manualVfd" in loadResult ? loadResult.manualVfd : undefined) : ruleBase.automaticVfd.find((row: Rule) => row.brand === input.vfdBrand && row.loadIndex === load.loadIndex);
    if (!vfd) return { ok: false as const, problems: ["В `sp1`/`sp2` нет ЧП для выбранного бренда и индекса нагрузки."] };
  }

  const specificationRows: Array<Rule & { source: string; multiplier?: number }> = [
    ...ruleBase.mainSpecification.filter((row: Rule) => row.diagram1 === main.row.diagram1 && row.loadIndex === load.loadIndex).map((row: Rule) => ({ ...row, source: "sp4" })),
    ...optionalResult.selected.flatMap((item) => ruleBase.sensorSpecification.filter((row: Rule) => row.diagram1 === item.row.diagram1).map((row: Rule) => ({ ...row, source: "sp3", multiplier: item.multiplier }))),
  ];
  if (vfd) specificationRows.push({ ...vfd, source: input.vfdSelection === "manual" ? "sp1" : "sp2" });
  const totalIo: Io = {};
  diagrams.forEach((item) => addIo(totalIo, item.row.io, item.multiplier));

  return {
    ok: true as const, input, loadIndex: load, main: main.row, optional: optionalResult.selected, vfd,
    diagrams: diagrams.map((item) => ({ code: item.row.diagram1, quantity: item.multiplier, title: item.title })), io: totalIo, lines: sumLines(diagrams),
    specification: aggregateSpecification(specificationRows),
    trace: { load: `${input.start === "vfd" ? "Load index 2" : "Load index 1"}:${load.sourceRow}`, mainDiagram: `diagram 1:${main.row.sourceRow}`, sensorDiagrams: optionalResult.selected.map((item) => `diagram 2:${item.row.sourceRow} × ${item.multiplier}`), vfd: vfd ? `${input.vfdSelection === "manual" ? "sp1" : "sp2"}:${vfd.sourceRow}` : null, specificationRows: specificationRows.length },
  };
}
