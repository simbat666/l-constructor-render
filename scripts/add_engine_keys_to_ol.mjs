import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Downloads/ОЛ - Электродвигатель асинхронный_В2 [9rOgKs].xlsx";
const target = "/Users/dmitrijmitin/Documents/ChatGPT/sdf/data/rules-source/ОЛ - Электродвигатель асинхронный_В2 [9rOgKs]-рабочая.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));

const mappings = {
  "Ques 1": {
    2: "diagram1.start", 3: "diagram1.start", 4: "diagram1.start", 5: "diagram1.start", 6: "diagram1.start",
    7: "diagram1.location", 8: "diagram1.location", 9: "diagram1.location",
    14: "diagram1.voltage", 15: "diagram1.voltage",
    43: "diagram1.trip", 44: "diagram1.trip",
  },
  "Ques 2": {
    2: "diagram2.thermal", 3: "diagram2.ptc",
    5: "diagram2.windingWire", 6: "diagram2.windingWire", 7: "diagram2.windingWire",
    10: "diagram2.bearingWire", 11: "diagram2.bearingWire", 12: "diagram2.bearingWire",
  },
};

for (const [sheetName, rowMappings] of Object.entries(mappings)) {
  const sheet = workbook.worksheets.getItem(sheetName);
  if (sheet.getRange("L1").values[0][0] && sheet.getRange("L1").values[0][0] !== "Engine key") throw new Error(`${sheetName}!L1 is occupied by an unexpected value`);
  sheet.getRange("L1").values = [["Engine key"]];
  if (sheetName === "Ques 2") sheet.getRange("L2:L20").clear({ applyTo: "contents" });
  for (const [row, engineKey] of Object.entries(rowMappings)) sheet.getRange(`L${row}`).values = [[engineKey]];
}

workbook.recalculate();
const check1 = await workbook.inspect({ kind: "table", range: "Ques 1!C1:L44", include: "values,formulas", tableMaxRows: 44, tableMaxCols: 10 });
const check2 = await workbook.inspect({ kind: "table", range: "Ques 2!C1:L20", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 10 });
console.log(check1.ndjson);
console.log(check2.ndjson);
const preview = await workbook.render({ sheetName: "Ques 1", range: "A1:N44", scale: 1.1 });
await fs.writeFile("/tmp/ques1-engine-key.png", new Uint8Array(await preview.arrayBuffer()));
const preview2 = await workbook.render({ sheetName: "Ques 2", range: "A1:M13", scale: 1.4 });
await fs.writeFile("/tmp/ques2-engine-key.png", new Uint8Array(await preview2.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(target);
console.log(JSON.stringify({ target, mappings }, null, 2));
