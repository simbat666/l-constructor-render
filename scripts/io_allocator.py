"""Allocate typed OL signals to distinct ZENTEC M245 pins from the supplied catalog."""
from __future__ import annotations

import json
from pathlib import Path

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'data/plc-catalog.json').read_text(encoding='utf-8'))
if CATALOG.get('schemaVersion') != 1:
    raise ValueError('Нужен поддерживаемый снимок состава ПЛК')
CODES = tuple(CATALOG['controller']['limits'])
OPTIONS = CATALOG['pinOptions']
BY_CODE = {code: [option for option in OPTIONS if option['code'] == code] for code in CODES}
FLEXIBILITY = {pin: len({option['code'] for option in OPTIONS if option['terminals'][0] == pin})
               for pin in {option['terminals'][0] for option in OPTIONS}}
for options in BY_CODE.values():
    options.sort(key=lambda option: (FLEXIBILITY[option['terminals'][0]], option['sourceRow']))


def pin_match(counts):
    """Return one exclusive physical pin per signal, or None if groups conflict."""
    demands = [code for code, count in zip(CODES, counts) for _ in range(count)]
    if len(demands) > CATALOG['controller']['generalSignalAmount']:
        return None
    # Later equivalent demands take the flexible pins first; earlier cabinet
    # instances can then reclaim the lowest compatible pin in the augmenting path.
    order = sorted(range(len(demands)), key=lambda index: (len(BY_CODE[demands[index]]), CODES.index(demands[index]), -index))
    owners = {}
    chosen = {}

    def visit(index, seen):
        for option in BY_CODE[demands[index]]:
            pin = option['terminals'][0]
            if pin in seen:
                continue
            seen.add(pin)
            previous = owners.get(pin)
            if previous is None or visit(previous, seen):
                owners[pin] = index
                chosen[index] = option
                return True
        return False

    for index in order:
        if not visit(index, set()):
            return None
    return chosen


def allocate_io(blocks):
    if not blocks:
        return dict(allocations=[], totals={code: 0 for code in CODES})
    states = {tuple(0 for _ in CODES): ([], 0, ())}
    for block in blocks:
        if not block['candidates']:
            return dict(allocations=[], error=f"Нет варианта схемы для блока {block['id']}.")
        next_states = {}
        for counts, (selected, penalty, rows) in states.items():
            for candidate in block['candidates']:
                io = candidate['io']
                if any(code not in CODES or isinstance(value, bool) or not isinstance(value, int) or value < 0
                       for code, value in io.items()):
                    return dict(allocations=[], error=f"Некорректный I/O: {block['id']}, строка {candidate['sourceRow']}.")
                updated = tuple(count + io.get(code, 0) for code, count in zip(CODES, counts))
                if any(count > CATALOG['controller']['limits'][code] for code, count in zip(CODES, updated)):
                    continue
                if pin_match(updated) is None:
                    continue
                next_penalty = penalty + io.get('DI24-NPN', 0) * 2 + io.get('DI24-HSC-NPN', 0) * 2 + io.get('DOR-NO', 0)
                path = (selected + [candidate], next_penalty, rows + (candidate['sourceRow'],))
                if updated not in next_states or path[1:] < next_states[updated][1:]:
                    next_states[updated] = path
        states = next_states
        if not states:
            return dict(allocations=[], error=f"Недостаточно совместимых выводов {CATALOG['controller']['brand']} {CATALOG['controller']['model']} для {block['id']}.")
    counts, (selected, _, _) = min(states.items(), key=lambda item: (sum(count > 0 for count in item[0]), item[1][1], item[1][2]))
    matched = pin_match(counts)
    pool = {code: [] for code in CODES}
    index = 0
    for code, count in zip(CODES, counts):
        for _ in range(count):
            pool[code].append(matched[index])
            index += 1
    allocations = []
    for block, scheme in zip(blocks, selected):
        channels = []
        for code in CODES:
            for _ in range(scheme['io'].get(code, 0)):
                option = pool[code].pop(0)
                channels.append(dict(family=code, address=option['terminals'][0],
                                     terminals=option['terminals'], group=option['group'],
                                     commonKey=option['commonKey'], source=f"Ключи зажимов ПЛК:{option['sourceRow']}"))
        allocations.append(dict(id=block['id'], scheme=scheme, channels=channels))
    return dict(allocations=allocations, totals=dict(zip(CODES, counts)))
