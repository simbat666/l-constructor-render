import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Documents/ChatGPT/sdf/data/rules-source/ОЛ - Электродвигатель асинхронный_В2 [3yMpy1]-исправлено.xlsx";
const target = source;

function thermalIndex(currentTo) {
  return `M3PT${String(Math.round(Number(currentTo) * 100)).padStart(5, "0")}`;
}

// The QF item identifies the current range in the specification.  This keeps
// the specification aligned with Load index 1 even where the former typo made
// two different ranges share one string key (for example 0.16 A and 1.6 A).
const qfIndex = new Map([
  ["GM2P01 (GV2P01)", "M3PT00016"],
  ["GM2P02 (GV2P02)", "M3PT00025"],
  ["GM2P03 (GV2P03)", "M3PT00040"],
  ["GM2P04 (GV2P04)", "M3PT00063"],
  ["GM2P05 (GV2P05)", "M3PT00100"],
  ["GM2P06 (GV2P06)", "M3PT00160"],
  ["GM2P07 (GV2P07)", "M3PT00250"],
  ["GM2P08 (GV2P08)", "M3PT00400"],
  ["GM2P10 (GV2P10)", "M3PT00630"],
  ["GM2P14 (GV2P14)", "M3PT01000"],
  ["GM2P16 (GV2P16)", "M3PT01400"],
  ["GM2P20 (GV2P20)", "M3PT01800"],
  ["GM2P21 (GV2P21)", "M3PT02300"],
  ["GM2P22 (GV2P22)", "M3PT02500"],
  ["GM2P32 (GV2P32)", "M3PT03200"],
  ["GM3P40 (GV3P40)", "M3PT04000"],
]);

const directCorrections = new Map([
  ["M3PT00010", "M3PT00100"],
  ["M3PT00100", "M3PT01000"],
  ["M3PT00140", "M3PT01400"],
  ["M3PT00180", "M3PT01800"],
  ["M3PT00230", "M3PT02300"],
  ["M3PT00250", "M3PT02500"],
  ["M3PT00320", "M3PT03200"],
  ["M3PT00400", "M3PT04000"],
  ["M3PT00500", "M3PT05000"],
  ["M3PT00650", "M3PT06500"],
]);

const regulatorIndex = new Map([
  ["SIM1", "M3PT00016"], ["SIM2", "M3PT00025"], ["SIM3", "M3PT00040"],
  ["SIM4", "M3PT00063"], ["SIM5", "M3PT00100"], ["SIM6", "M3PT00160"],
  ["SIM7", "M3PT00250"], ["SIM8", "M3PT00400"], ["SIM9", "M3PT00630"],
  ["SIM10", "M3PT01000"], ["SIM11", "M3PT01400"], ["SIM12", "M3PT01800"],
  ["SIM13", "M3PT02300"], ["SIM14", "M3PT02500"], ["SIM15", "M3PT03200"],
  ["SIM16", "M3PT04000"], ["SIM17", "M3PT05000"], ["SIM18", "M3PT06500"],
]);

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const loadIndexSheet = workbook.worksheets.getItem("Load index 1");
const specificationSheet = workbook.worksheets.getItem("sp4");
const loadRows = loadIndexSheet.getRange("A2:J148").values;
const loadChanges = [];

for (let offset = 0; offset < loadRows.length; offset += 1) {
  const rowNumber = offset + 2;
  const row = loadRows[offset];
  const currentTo = row[7];
  const currentIndex = row[8];
  if (typeof currentIndex !== "string" || !currentIndex.startsWith("M3PT")) continue;
  const expected = thermalIndex(currentTo);
  if (currentIndex !== expected) {
    loadIndexSheet.getRange(`I${rowNumber}`).values = [[expected]];
    loadChanges.push({ row: rowNumber, from: currentIndex, to: expected, currentTo });
  }
}

if (loadChanges.length !== 63) {
  throw new Error(`Expected 63 Load index 1 corrections, got ${loadChanges.length}`);
}

const specificationRows = specificationSheet.getRange("A2:F957").values;
const specificationChanges = [];
for (let offset = 0; offset < specificationRows.length; offset += 1) {
  const rowNumber = offset + 2;
  const [diagram, loadIndex, item, , , lde] = specificationRows[offset];
  if (typeof loadIndex !== "string" || !loadIndex.startsWith("M3PT")) continue;

  let expected = qfIndex.get(item) ?? regulatorIndex.get(item);
  if (!expected) expected = directCorrections.get(loadIndex);

  // GM3P50 is intentionally used for both source rows.  Their pre-correction
  // keys differ, so preserve that range information instead of guessing from
  // the item name.
  if (item === "GM3P50 (GV3P50)") expected = directCorrections.get(loadIndex);

  if (!expected) {
    throw new Error(`Cannot resolve sp4!B${rowNumber}: ${diagram}, ${loadIndex}, ${item}, ${lde}`);
  }
  if (loadIndex !== expected) {
    specificationSheet.getRange(`B${rowNumber}`).values = [[expected]];
    specificationChanges.push({ row: rowNumber, diagram, from: loadIndex, to: expected, item, lde });
  }
}

if (specificationChanges.length === 0) throw new Error("sp4 was not corrected");

workbook.recalculate();

const loadCheck = await workbook.inspect({
  kind: "table",
  range: "Load index 1!F2:J80",
  include: "values,formulas",
  tableMaxRows: 80,
  tableMaxCols: 5,
});
const specCheck = await workbook.inspect({
  kind: "table",
  range: "sp4!A1:F80",
  include: "values,formulas",
  tableMaxRows: 80,
  tableMaxCols: 6,
});
console.log(loadCheck.ndjson);
console.log(specCheck.ndjson);

const preview = await workbook.render({ sheetName: "Load index 1", range: "A1:J80", scale: 1.25 });
await fs.writeFile("/tmp/load-index-1-after.png", new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(target);
console.log(JSON.stringify({ target, loadChanges, specificationChanges }, null, 2));
