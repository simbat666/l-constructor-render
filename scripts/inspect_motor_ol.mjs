import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "/Users/dmitrijmitin/Documents/ChatGPT/sdf/data/rules-source/ОЛ - Электродвигатель асинхронный_В2 [3yMpy1]-исправлено.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const check = await workbook.inspect({
  kind: "workbook,sheet,table",
  range: "Ques 1!A1:N44",
  include: "values,formulas",
  tableMaxRows: 44,
  tableMaxCols: 14,
});
console.log(check.ndjson);
const renderDirectory = "/tmp/motor-ol-audit-renders";
await fs.mkdir(renderDirectory, { recursive: true });
for (const sheetName of ["Ques 1", "Ques 2", "Ques 3", "Ques 4", "Load index 1", "Load index 2", "diagram 1", "diagram 2", "sp1", "sp2", "sp3", "sp4", "спр.ин"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 0.75, format: "png" });
  await fs.writeFile(`${renderDirectory}/${sheetName.replaceAll(" ", "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
}
