"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const initialOptions = ["ПЧ", "Заслонка", "Фильтр", "Датчики", "ПДУ"];

export default function Home() {
  const [cabinetName, setCabinetName] = useState("Приточная установка П1");
  const [motorCurrent, setMotorCurrent] = useState("12.2");
  const [supply, setSupply] = useState("3Ф · 400 В");
  const [options, setOptions] = useState(initialOptions);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);

  const current = Number(motorCurrent.replace(",", ".")) || 0;
  const protection = current <= 4 ? "QF 10 A" : current <= 10 ? "QF 16 A" : "QF 25 A";
  const moduleCount = 4 + options.length;
  const marking = useMemo(
    () => `L-${supply.startsWith("3") ? "3" : "1"}Ф-${current.toFixed(1).replace(".", ",")}A-${options.length.toString().padStart(2, "0")}`,
    [supply, current, options.length],
  );

  function toggleOption(option: string) {
    setOptions((items) => items.includes(option) ? items.filter((item) => item !== option) : [...items, option]);
  }

  function generate() {
    setGeneratedAt(new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date()));
  }

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    void Promise.resolve(context.registerTool({
      name: "generate_test_cabinet",
      title: "Сформировать тестовый шкаф",
      description: "Заполняет тестовую конфигурацию шкафа и формирует видимый результат в лаборатории L.",
      inputSchema: {
        type: "object",
        properties: {
          cabinetName: { type: "string", description: "Наименование шкафа" },
          motorCurrentA: { type: "number", minimum: 0.1, maximum: 999, description: "Номинальный ток двигателя, А" },
          supply: { type: "string", enum: ["3Ф · 400 В", "1Ф · 230 В"] },
        },
        required: ["cabinetName", "motorCurrentA", "supply"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input) {
        const data = input as { cabinetName?: unknown; motorCurrentA?: unknown; supply?: unknown };
        if (typeof data.cabinetName !== "string" || !data.cabinetName.trim() || typeof data.motorCurrentA !== "number" || data.motorCurrentA <= 0 || (data.supply !== "3Ф · 400 В" && data.supply !== "1Ф · 230 В")) {
          throw new Error("Нужны наименование, ток больше нуля и допустимое питание.");
        }
        setCabinetName(data.cabinetName.trim());
        setMotorCurrent(String(data.motorCurrentA));
        setSupply(data.supply);
        generate();
        return { status: "generated", cabinetName: data.cabinetName.trim(), motorCurrentA: data.motorCurrentA, supply: data.supply };
      },
    }, { signal: lifecycle.signal })).catch(() => undefined);
    return () => lifecycle.abort();
  }, []);

  return (
    <main className="min-h-screen bg-[#080b0d] text-slate-100">
      <header className="border-b border-white/10 bg-[#0c1013]">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8">
          <div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg bg-[#b7ff3c] font-mono text-lg font-black text-black">L</span><div><h1 className="font-mono text-base font-bold tracking-[0.18em]">L / LAB</h1><p className="text-xs text-slate-500">Инженерный тестовый стенд</p></div></div>
          <span className="rounded-full border border-[#b7ff3c]/30 bg-[#b7ff3c]/10 px-3 py-1 text-xs font-medium text-[#c8ff63]">online · test</span>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 px-5 py-6 sm:px-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(440px,1.1fr)]">
        <section className="rounded-xl border border-white/10 bg-[#10161a] p-5 sm:p-7">
          <p className="mb-1 font-mono text-xs uppercase tracking-[0.18em] text-[#b7ff3c]">01 / исходные данные</p>
          <h2 className="text-2xl font-semibold tracking-tight">Собери тестовый шкаф</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">Это стартовый экран L. Дальше сюда подключим реальную базу, правила и выпуск КД.</p>
          <div className="mt-7 space-y-5">
            <label className="block text-sm font-medium text-slate-300">Наименование шкафа<Input value={cabinetName} onChange={(event) => setCabinetName(event.target.value)} className="mt-2 h-11 border-white/10 bg-black/20 text-base" /></label>
            <div className="grid gap-5 sm:grid-cols-2">
              <label className="block text-sm font-medium text-slate-300">Питание<select value={supply} onChange={(event) => setSupply(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-base text-slate-100 outline-none focus:border-[#b7ff3c]"><option>3Ф · 400 В</option><option>1Ф · 230 В</option></select></label>
              <label className="block text-sm font-medium text-slate-300">Номинальный ток, А<Input inputMode="decimal" value={motorCurrent} onChange={(event) => setMotorCurrent(event.target.value)} className="mt-2 h-11 border-white/10 bg-black/20 text-base" /></label>
            </div>
            <fieldset><legend className="text-sm font-medium text-slate-300">Функциональные блоки</legend><div className="mt-3 flex flex-wrap gap-2">{initialOptions.map((option) => <button key={option} type="button" onClick={() => toggleOption(option)} className={`rounded-full border px-3 py-2 text-sm transition ${options.includes(option) ? "border-[#b7ff3c]/50 bg-[#b7ff3c]/15 text-[#d3ff80]" : "border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/30"}`}>{options.includes(option) ? "✓ " : "+ "}{option}</button>)}</div></fieldset>
            <Button onClick={generate} size="lg" className="h-11 w-full bg-[#b7ff3c] font-semibold text-black hover:bg-[#c8ff63]">Сформировать тестовый результат →</Button>
          </div>
        </section>

        <section className="relative overflow-hidden rounded-xl border border-white/10 bg-[#0d1215] p-5 sm:p-7">
          <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(#ffffff08_1px,transparent_1px),linear-gradient(90deg,#ffffff08_1px,transparent_1px)] [background-size:28px_28px]" />
          <div className="relative">
            <div className="flex items-start justify-between gap-4"><div><p className="font-mono text-xs uppercase tracking-[0.18em] text-slate-500">02 / результат</p><h2 className="mt-1 text-2xl font-semibold tracking-tight">{cabinetName || "Новый шкаф"}</h2></div><span className="rounded-md border border-white/10 bg-black/20 px-2 py-1 font-mono text-xs text-slate-300">{marking}</span></div>
            <div className="mt-7 grid gap-3 sm:grid-cols-3"><Metric label="Модулей" value={String(moduleCount)} /><Metric label="Защита" value={protection} /><Metric label="Статус" value={generatedAt ? "готово" : "черновик"} accent={!!generatedAt} /></div>
            <div className="mt-7 rounded-lg border border-white/10 bg-black/20 p-4"><div className="flex items-center justify-between"><h3 className="font-mono text-xs uppercase tracking-[0.14em] text-slate-400">Цепочка выпуска</h3><span className="text-xs text-slate-500">{generatedAt ? `расчёт ${generatedAt}` : "ожидание расчёта"}</span></div><div className="mt-5 grid grid-cols-4 gap-2 text-center text-xs"><Step label="Модель" active /><Step label="BOM" active={!!generatedAt} /><Step label="Э5" active={!!generatedAt} /><Step label="КД" active={!!generatedAt} /></div></div>
            <div className="mt-7"><h3 className="font-mono text-xs uppercase tracking-[0.14em] text-slate-400">Будущий комплект</h3><ul className="mt-3 divide-y divide-white/10 rounded-lg border border-white/10 bg-black/15">{["Спецификация", "Перечень I/O", "Э3 и Э5", "Клеммник и кабельный журнал", "DXF/DWG-комплект"].map((item, index) => <li className="flex items-center justify-between px-4 py-3 text-sm" key={item}><span>{item}</span><span className={generatedAt ? "text-[#b7ff3c]" : "text-slate-600"}>{generatedAt ? "подготовлено" : index === 0 ? "макет" : "далее"}</span></li>)}</ul></div>
          </div>
        </section>
      </div>
      <footer className="mx-auto max-w-7xl px-5 pb-7 text-xs text-slate-600 sm:px-8">L — закрытый веб-стенд для быстрых проверок. Данные в этой версии не сохраняются.</footer>
    </main>
  );
}

function Metric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return <div className="rounded-lg border border-white/10 bg-black/20 p-3"><p className="text-xs text-slate-500">{label}</p><p className={`mt-1 font-mono text-sm font-semibold ${accent ? "text-[#b7ff3c]" : "text-slate-100"}`}>{value}</p></div>;
}

function Step({ label, active = false }: { label: string; active?: boolean }) {
  return <div><span className={`mx-auto grid size-8 place-items-center rounded-full border text-xs ${active ? "border-[#b7ff3c] bg-[#b7ff3c] text-black" : "border-white/15 bg-white/5 text-slate-500"}`}>{active ? "✓" : "·"}</span><p className="mt-2 text-slate-400">{label}</p></div>;
}
