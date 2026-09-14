import fs from "node:fs/promises";
import crypto from "node:crypto";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Downloads/ОЛ - Электродвигатель асинхронный_В2 [9rOgKs].xlsx";
const output = "/tmp/audit-new-motor-ol.json";
const normalize = (value) => String(value ?? "").trim().toLocaleLowerCase("ru");
const splitBinds = (value) => String(value ?? "").split(";").map((item) => item.trim()).filter(Boolean);
const sheetNames = ["Ques 1", "Ques 2", "Ques 3", "Ques 4"];
const requiredHeaders = ["order", "Вопрос", "binds", "Answer", "Ответ", "Field type"];

const sourceBytes = await fs.readFile(source);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 6000 });
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "formula error scan",
});

function extractQuestions(sheetName) {
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  const header = values[0].map((cell) => String(cell ?? "").trim());
  const columns = Object.fromEntries(header.map((name, index) => [name, index]));
  const missingHeaders = requiredHeaders.filter((name) => columns[name] === undefined);
  const result = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index];
    const order = row[columns.order];
    if (order === null || order === undefined || order === "") continue;
    result.push({
      sheet: sheetName,
      row: index + 1,
      order: String(order),
      question: row[columns["Вопрос"]] ?? null,
      binds: row[columns.binds] ?? null,
      answer: row[columns["Ответ"]] ?? null,
      fieldType: row[columns["Field type"]] ?? null,
      required: columns["Required field"] === undefined ? null : Boolean(row[columns["Required field"]]),
    });
  }
  return { header, missingHeaders, result };
}

const questionSets = Object.fromEntries(sheetNames.map((sheetName) => [sheetName, extractQuestions(sheetName)]));
const questions = Object.values(questionSets).flatMap((item) => item.result);
const orders = questions.map((item) => item.order);
const duplicateOrders = [...new Set(orders.filter((order, index) => orders.indexOf(order) !== index))];
const knownOrders = new Set(orders);
const invalidBinds = questions.flatMap((question) => splitBinds(question.binds)
  .filter((target) => !knownOrders.has(target))
  .map((target) => `${question.sheet}!${question.row} (${question.order}) → ${target}`));
const repeatedTargets = questions.flatMap((question) => {
  const targets = splitBinds(question.binds);
  return targets.length === new Set(targets).size ? [] : [`${question.sheet}!${question.row} (${question.order})`];
});
const missingTechnicalRules = questions.filter((question) => question.fieldType === "input" && !/^(float|string)\|/.test(String(question.answer ?? "")))
  .map((question) => `${question.sheet}!${question.row} (${question.order})`);
const requiredWithoutAnswer = questions.filter((question) => question.required && (question.answer === null || question.answer === ""))
  .map((question) => `${question.sheet}!${question.row} (${question.order})`);
const inbound = new Set(questions.flatMap((question) => splitBinds(question.binds)));
const roots = questions.filter((question) => !inbound.has(question.order)).map((question) => `${question.sheet}!${question.row} (${question.order})`);

function tableRows(sheetName) {
  const values = workbook.worksheets.getItem(sheetName).getUsedRange().values;
  return { rows: Math.max(values.length - 1, 0), columns: values[0]?.length || 0, header: values[0] || [] };
}

const diagram1 = workbook.worksheets.getItem("diagram 1").getUsedRange().values;
const diagram2 = workbook.worksheets.getItem("diagram 2").getUsedRange().values;
const missingDiagram1Codes = diagram1.slice(1).flatMap((row, index) => !row[9] ? [`diagram 1!${index + 2}`] : []);
const missingDiagram2Codes = diagram2.slice(1).flatMap((row, index) => !row[9] ? [`diagram 2!${index + 2}`] : []);
const q1 = questionSets["Ques 1"].result;
const domains = {
  start: q1.filter((row) => row.question === "Способ пуска / управления двигателем").map((row) => row.answer),
  location: q1.filter((row) => row.question === "Расположение (ЧП / Симисторный регулятор скорости)").map((row) => row.answer).filter((answer) => normalize(answer) !== "unused"),
  voltage: q1.filter((row) => row.question === "Номинальное напряжение, В").map((row) => row.answer),
  trip: q1.filter((row) => row.question === "Тип защиты автоматического выключателя").map((row) => row.answer),
};
const diagramDomains = {
  start: [...new Set(diagram1.slice(1).map((row) => row[1]).filter(Boolean))],
  location: [...new Set(diagram1.slice(1).map((row) => row[3]).filter(Boolean))],
  voltage: [...new Set(diagram1.slice(1).map((row) => row[5]).filter(Boolean))],
  trip: [...new Set(diagram1.slice(1).map((row) => row[7]).filter(Boolean))],
};
const domainMismatch = Object.fromEntries(Object.entries(domains).map(([dimension, values]) => [dimension, values.filter((value) => normalize(value) && !diagramDomains[dimension].some((diagramValue) => normalize(diagramValue) === normalize(value)))]));

const loadIndex1 = workbook.worksheets.getItem("Load index 1").getUsedRange().values.slice(1);
const loadIndex2 = workbook.worksheets.getItem("Load index 2").getUsedRange().values.slice(1);
const sp1 = workbook.worksheets.getItem("sp1").getUsedRange().values.slice(1);
const sp2 = workbook.worksheets.getItem("sp2").getUsedRange().values.slice(1);
const sp3 = workbook.worksheets.getItem("sp3").getUsedRange().values.slice(1);
const sp4 = workbook.worksheets.getItem("sp4").getUsedRange().values.slice(1);
const mainDiagramCodes = new Set(diagram1.slice(1).map((row) => normalize(row[9])).filter(Boolean));
const optionalDiagramCodes = new Set(diagram2.slice(1).map((row) => normalize(row[9])).filter(Boolean));
const allDiagramCodes = new Set([...mainDiagramCodes, ...optionalDiagramCodes]);
const index1Codes = new Set(loadIndex1.map((row) => normalize(row[8])).filter(Boolean));
const index2Codes = new Set(loadIndex2.map((row) => normalize(row[8])).filter(Boolean));
const diagram1WithoutLoadIndexBandByCodeDetails = diagram1.slice(1).flatMap((row, index) => {
  const found = normalize(row[1]) === "пуск через чп"
    ? loadIndex2.some((load) => load[2] === row[4] && load[4] === row[0])
    : loadIndex1.some((load) => load[0] === row[4] && load[2] === row[0] && load[4] === row[6]);
  return found ? [] : [{ cell: `diagram 1!${index + 2}`, start: row[1], location: row[3], voltage: row[5], trip: row[7], diagram: row[9] }];
});
const diagram1TextJoinMismatch = diagram1.slice(1).filter((row) => !loadIndex1.some((load) => normalize(load[1]) === normalize(row[5]) && normalize(load[3]) === normalize(row[1]) && normalize(load[5]) === normalize(row[7]))).length;
const sp1UnknownIndex = sp1.flatMap((row, index) => row[5] && !index2Codes.has(normalize(row[5])) ? [`sp1!${index + 2} → ${row[5]}`] : []);
const sp2UnknownIndex = sp2.flatMap((row, index) => row[5] && !index2Codes.has(normalize(row[5])) ? [`sp2!${index + 2} → ${row[5]}`] : []);
const sp3UnknownDiagram = sp3.flatMap((row, index) => row[0] && !optionalDiagramCodes.has(normalize(row[0])) ? [`sp3!${index + 2} → ${row[0]}`] : []);
const sp4UnknownDiagram = sp4.flatMap((row, index) => row[0] && !allDiagramCodes.has(normalize(row[0])) ? [`sp4!${index + 2} → ${row[0]}`] : []);
const sp4UnknownIndex = sp4.flatMap((row, index) => row[1] && !index1Codes.has(normalize(row[1])) && !index2Codes.has(normalize(row[1])) ? [`sp4!${index + 2} → ${row[1]}`] : []);
const sourceSchemaWarnings = [
  "diagram 2!E1 назван «Winding RTD (Pt100)», но E5:E7 содержат тип подключения (2-/3-/4-проводная), а не признак да/нет.",
  "Ques 1!C7 (2.0.) содержит «unused» и не имеет входящей связи; универсальный исполнитель без специального исключения увидит его как стартовый вопрос.",
  "Ques 4!B2 (40.1) не содержит вопроса, ответа или типа поля и также не имеет входящей связи.",
  "Ques 2!B9 (35.1) — единственный обязательный вопрос верхнего уровня среди дополнительных датчиков; подтвердить, что выбор датчика подшипников действительно обязателен после включения доп. параметров.",
];

const report = {
  source: { path: source, sha256: crypto.createHash("sha256").update(sourceBytes).digest("hex") },
  sheets: sheets.ndjson,
  questionSheets: Object.fromEntries(Object.entries(questionSets).map(([name, data]) => [name, { rows: data.result.length, headers: data.header, missingHeaders: data.missingHeaders }])),
  tables: Object.fromEntries(["Load index 1", "Load index 2", "diagram 1", "diagram 2", "sp1", "sp2", "sp3", "sp4"].map((sheetName) => [sheetName, tableRows(sheetName)])),
  samples: {
    loadIndex1: loadIndex1.slice(0, 4),
    loadIndex2: loadIndex2.slice(0, 4),
    diagram1: diagram1.slice(1, 5),
    question2: questionSets["Ques 2"].result,
    diagram2: diagram2.slice(1).map((row, index) => ({
      row: index + 2, thermal: row[1], ptc: row[3], winding: row[5], bearingWire: row[7], diagram1: row[9], diagram2: row[10],
    })),
  },
  checks: { duplicateOrders, invalidBinds, repeatedTargets, missingTechnicalRules, requiredWithoutAnswer, roots, missingDiagram1Codes, missingDiagram2Codes, domainMismatch, diagram1WithoutLoadIndexBandByCode: diagram1WithoutLoadIndexBandByCodeDetails.map((item) => item.cell), diagram1WithoutLoadIndexBandByCodeDetails, diagram1TextJoinMismatch, sp1UnknownIndex, sp2UnknownIndex, sp3UnknownDiagram, sp4UnknownDiagram, sp4UnknownIndexCount: sp4UnknownIndex.length, sp4UnknownIndexValues: [...new Set(sp4UnknownIndex.map((item) => item.split(" → ")[1]))], sp4Coverage: "Проверяется только после получения конкретной ветки ОЛ и Load index; полный декартов перебор некорректен.", sourceSchemaWarnings, formulaErrors: errors.ndjson },
};
await fs.writeFile(output, JSON.stringify(report, null, 2));
for (const [sheetName, range, target] of [["Ques 1", "A1:N44", "/tmp/audit-new-ol-ques1.png"], ["Ques 2", "A1:M13", "/tmp/audit-new-ol-ques2.png"], ["diagram 2", "A1:W10", "/tmp/audit-new-ol-diagram2.png"]]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.1 });
  await fs.writeFile(target, new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify(report, null, 2));
