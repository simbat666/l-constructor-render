import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Downloads/ОЛ - Электродвигатель асинхронный_В2 [9rOgKs].xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const summary = await workbook.inspect({ kind: "workbook,sheet", include: "id,name", maxChars: 4000 });
console.log(summary.ndjson);
for (const sheetName of ["Ques 1", "Ques 2", "Load index 1", "diagram 1", "diagram 2", "sp4"]) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const range = sheet.getUsedRange();
  const values = range.values;
  console.log(JSON.stringify({ sheetName, rows: values.length, columns: values[0]?.length || 0, headers: values[0] || [] }));
}
const preview = await workbook.render({ sheetName: "Ques 1", autoCrop: "all", scale: 0.9, format: "png" });
await fs.writeFile("/tmp/new-motor-ol-ques1.png", new Uint8Array(await preview.arrayBuffer()));
