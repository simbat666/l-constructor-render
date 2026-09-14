import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Downloads/ОЛ - Электродвигатель асинхронный_В2 [3yMpy1].xlsx";
const target = "/Users/dmitrijmitin/Documents/ChatGPT/sdf/data/rules-source/ОЛ - Электродвигатель асинхронный_В2 [3yMpy1]-исправлено.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const sheet = workbook.worksheets.getItem("Ques 1");

if (sheet.getRange("G1").values[0][0] !== "binds") throw new Error("Ques 1!G1 must be the binds column");
if (sheet.getRange("C14:C15").values.flat().join(",") !== "5.1,5.2") throw new Error("Unexpected Ques 1 voltage rows");

sheet.getRange("G14").values = [["6.1"]];
sheet.getRange("G15").values = [["6.2"]];
workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: "Ques 1!A1:K20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 11,
});
console.log(check.ndjson);
const preview = await workbook.render({ sheetName: "Ques 1", range: "A1:K20", scale: 1.5 });
await fs.writeFile("/tmp/motor-ol-ques1-after.png", new Uint8Array(await preview.arrayBuffer()));
await fs.mkdir("/Users/dmitrijmitin/Documents/ChatGPT/sdf/data/rules-source", { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(target);
console.log(JSON.stringify({ target, g14: sheet.getRange("G14").values[0][0], g15: sheet.getRange("G15").values[0][0] }));
