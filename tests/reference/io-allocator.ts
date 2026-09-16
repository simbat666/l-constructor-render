// Frozen pre-Python migration oracle. Never import into app/runtime.
/**
 * Select compatible electrical-scheme variants for every functional block and
 * reserve controller channels.  The OL provides variants (DI 0/24 V and
 * relay/transistor DO); it must not turn those into questions for the user.
 */

export type IoCounts = {
  di0?: number;
  di24?: number;
  doRelay?: number;
  doTransistor?: number;
  aiPt1000?: number;
  ai420?: number;
  ai010?: number;
  ao420?: number;
  ao010?: number;
};

export type SchemeCandidate = { sourceRow: number; io: IoCounts };
export type AllocatorBlock<T extends SchemeCandidate = SchemeCandidate> = {
  id: string;
  candidates: T[];
};
export type Channel = {
  family: string;
  address: string;
};
export type Allocation<T extends SchemeCandidate = SchemeCandidate> = {
  id: string;
  scheme: T;
  channels: Channel[];
};

const channels: Array<[keyof IoCounts, Channel['family'], string]> = [
  ['di0', 'DI 0 В', 'DI0'],
  ['di24', 'DI 24 В', 'DI24'],
  ['doRelay', 'DO реле', 'DOR'],
  ['doTransistor', 'DO транзистор', 'DOT'],
  ['aiPt1000', 'AI Pt1000 (код ОЛ)', 'AIPT'],
  ['ai420', 'AI 4–20 мА', 'AI420'],
  ['ai010', 'AI 0–10 В', 'AI010'],
  ['ao420', 'AO 4–20 мА', 'AO420'],
  ['ao010', 'AO 0–10 В', 'AO010'],
];

function mask(candidate: SchemeCandidate) {
  return channels.reduce(
    (result, [key], index) =>
      result | (Number(candidate.io[key] || 0) > 0 ? 1 << index : 0),
    0,
  );
}

function preference(candidate: SchemeCandidate) {
  // Equal cabinet complexity: the usual 24 V DI + transistor DO variant is
  // preferred.  The allocator still switches when a library lacks it.
  return Number(candidate.io.di0 || 0) * 2 + Number(candidate.io.doRelay || 0);
}

function popcount(value: number) {
  let count = 0;
  for (let bit = value; bit; bit >>>= 1) count += bit & 1;
  return count;
}

export function allocateControllerIo<T extends SchemeCandidate>(
  blocks: AllocatorBlock<T>[],
) {
  type State = { cost: number; selected: T[] };
  let states = new Map<number, State>([[0, { cost: 0, selected: [] }]]);
  for (const block of blocks) {
    for (const candidate of block.candidates) {
      if (
        Object.entries(candidate.io).some(
          ([key, value]) =>
            !channels.some(([known]) => known === key) ||
            !Number.isInteger(value) ||
            Number(value) < 0 ||
            Number(value) > 1000,
        )
      )
        return {
          allocations: [] as Allocation<T>[],
          error: `Некорректный I/O: ${block.id}, строка ${candidate.sourceRow}.`,
        };
    }
    if (!block.candidates.length)
      return {
        allocations: [] as Allocation<T>[],
        error: `Нет варианта схемы для блока ${block.id}.`,
      };
    const next = new Map<number, State>();
    for (const [used, state] of states)
      for (const candidate of block.candidates) {
        const nextMask = used | mask(candidate);
        const nextState = {
          cost: state.cost + preference(candidate),
          selected: [...state.selected, candidate],
        };
        const existing = next.get(nextMask);
        if (
          !existing ||
          nextState.cost < existing.cost ||
          (nextState.cost === existing.cost &&
            candidate.sourceRow < existing.selected.at(-1)!.sourceRow)
        )
          next.set(nextMask, nextState);
      }
    states = next;
  }
  const best = [...states.entries()].sort(
    ([maskA, a], [maskB, b]) =>
      popcount(maskA) - popcount(maskB) || a.cost - b.cost,
  )[0]?.[1];
  if (!best)
    return {
      allocations: [] as Allocation<T>[],
      error: 'Не удалось распределить I/O.',
    };

  const counters: Record<keyof IoCounts, number> = {
    di0: 0,
    di24: 0,
    doRelay: 0,
    doTransistor: 0,
    aiPt1000: 0,
    ai420: 0,
    ai010: 0,
    ao420: 0,
    ao010: 0,
  };
  const allocations = blocks.map((block, index) => {
    const scheme = best.selected[index];
    const assigned: Channel[] = [];
    for (const [key, family, prefix] of channels)
      for (let count = 0; count < Number(scheme.io[key] || 0); count += 1) {
        counters[key] += 1;
        assigned.push({
          family,
          address: `${prefix}-${String(counters[key]).padStart(2, '0')}`,
        });
      }
    return { id: block.id, scheme, channels: assigned };
  });
  return { allocations, totals: counters };
}
