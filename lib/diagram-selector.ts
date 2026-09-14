import ruleBase from "@/data/motor-v2.json";

/** Compatibility selector for clients outside the questionnaire UI. */
export type QuestionSelection = Record<string, string>;
type Rule = Record<string, any>;
const normalize = (value: unknown) => String(value || "").trim().toLocaleLowerCase("ru");

function dimensionsFromOrders(selection: QuestionSelection) {
  const result = new Map<string, string>();
  for (const order of Object.values(selection)) {
    const answer = (ruleBase.questions1 as Rule[]).find((item) => item.order === order);
    if (answer?.engineKey?.startsWith("diagram1.") && answer.answer) result.set(answer.engineKey.slice("diagram1.".length), answer.answer);
  }
  return result;
}

export function questionOptions(engineKey: string) {
  return (ruleBase.questions1 as Rule[]).filter((item) => item.engineKey === engineKey);
}

export function selectDiagramPairs(selection: QuestionSelection) {
  const dimensions = dimensionsFromOrders(selection);
  if (!dimensions.size) return [];
  return (ruleBase.diagrams1 as Rule[]).filter((row) => [...dimensions.entries()].every(([key, value]) => normalize(row[key]) === normalize(value)));
}

export function selectionLabel(order: string) {
  return (ruleBase.questions1 as Rule[]).find((item) => item.order === order)?.answer || "—";
}
