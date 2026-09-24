"""Cabinet-wide PLC selection and exclusive assignment of typed I/O resources."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'data/plc-catalog.json').read_text(encoding='utf-8'))
if CATALOG.get('schemaVersion') != 2:
    raise ValueError('Нужен поддерживаемый снимок состава ПЛК и модулей')
CODES = tuple(CATALOG['controller']['limits'])
MAX_MODULES = int(CATALOG['controller']['maxModules'])
BASE_AMOUNT = int(CATALOG['controller']['generalSignalAmount'])
MODULE_AMOUNT = int(CATALOG['module']['generalSignalAmount'])


def inventory(module_count):
    """Make independent copies of each device's local pin numbers."""
    result = []
    for device_index in range(module_count + 1):
        is_module = device_index > 0
        ref = f'M{device_index}' if is_module else 'PLC'
        sheet = 'Клеммы модуля тест' if is_module else 'Ключи зажимов ПЛК'
        for source in CATALOG['modulePinOptions' if is_module else 'pinOptions']:
            result.append(dict(source, deviceRef=ref, deviceIndex=device_index,
                               sourceSheet=sheet,
                               terminalStatus=CATALOG['module']['terminalStatus'] if is_module else 'source'))
    return result


@lru_cache(maxsize=6)
def indexed_inventory(module_count):
    options = inventory(module_count)
    slot_codes = {}
    for option in options:
        slot_codes.setdefault((option['deviceRef'], option['terminals'][0]), set()).add(option['code'])
    by_code = {code: [] for code in CODES}
    for option in options:
        slot = option['deviceRef'], option['terminals'][0]
        by_code[option['code']].append(option)
    for rows in by_code.values():
        rows.sort(key=lambda option: (len(slot_codes[(option['deviceRef'], option['terminals'][0])]),
                                      option['deviceIndex'], option['sourceRow']))
    return by_code, slot_codes


def demands_for(counts):
    return [code for code, count in zip(CODES, counts) for _ in range(count)]


@lru_cache(maxsize=8192)
def feasible_match(counts, module_count):
    """Exact bipartite matching: one signal per resource on each device."""
    if sum(counts) > BASE_AMOUNT + module_count * MODULE_AMOUNT:
        return False
    if any(count > CATALOG['controller']['limits'][code] + module_count * CATALOG['module']['limits'][code]
           for code, count in zip(CODES, counts)):
        return False
    demands = demands_for(counts)
    by_code, _ = indexed_inventory(module_count)
    order = sorted(range(len(demands)), key=lambda index: (len(by_code[demands[index]]), index))
    owners = {}

    def visit(index, seen):
        for option in by_code[demands[index]]:
            slot = option['deviceRef'], option['terminals'][0]
            if slot in seen:
                continue
            seen.add(slot)
            previous = owners.get(slot)
            if previous is None or visit(previous, seen):
                owners[slot] = index
                return True
        return False

    return all(visit(index, set()) for index in order)


def min_modules(counts):
    return next((amount for amount in range(MAX_MODULES + 1) if feasible_match(counts, amount)), None)


def pin_match(counts, module_count=0):
    """Choose a global minimum-cost feasible assignment for one cabinet."""
    counts = tuple(counts)
    if not feasible_match(counts, module_count):
        return None
    demands = demands_for(counts)
    if not demands:
        return {}
    by_code, slot_codes = indexed_inventory(module_count)
    slots = list(slot_codes)
    slot_index = {slot: index for index, slot in enumerate(slots)}
    inf = 10**9
    costs = [[inf] * len(slots) for _ in demands]
    choices = {}
    for row, code in enumerate(demands):
        for option in by_code[code]:
            slot = option['deviceRef'], option['terminals'][0]
            column = slot_index[slot]
            # Exact matching first; then preserve universal slots for demand
            # families that have fewer alternatives in this same cabinet.
            costs[row][column] = (len(slot_codes[slot]) * 10000
                                  + option['deviceIndex'] * 100 + option['sourceRow'])
            choices[row, column] = option

    # Rectangular Hungarian algorithm, with independent slots per device.
    n, m = len(demands), len(slots)
    u, v = [0] * (n + 1), [0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0, minv, used = 0, [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = costs[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            if delta >= inf:
                return None
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    chosen = {p[j] - 1: choices.get((p[j] - 1, j - 1)) for j in range(1, m + 1) if p[j]}
    return chosen if len(chosen) == n and all(chosen.values()) else None


def allocate_io(blocks):
    """Choose OL variants, then the fewest modules and a global pin placement."""
    if not blocks:
        return dict(allocations=[], totals={code: 0 for code in CODES}, modules=[])
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
                if not feasible_match(updated, MAX_MODULES):
                    continue
                next_penalty = penalty + io.get('DI24-NPN', 0) * 2 + io.get('DI24-HSC-NPN', 0) * 2 + io.get('DOR-NO', 0)
                path = (selected + [candidate], next_penalty, rows + (candidate['sourceRow'],))
                if updated not in next_states or path[1:] < next_states[updated][1:]:
                    next_states[updated] = path
        states = next_states
        if not states:
            return dict(allocations=[], error=f"Недостаточно совместимых выводов {CATALOG['controller']['brand']} {CATALOG['controller']['model']} и {MAX_MODULES} модулей для {block['id']}.")
    counts, (selected, _, _) = min(states.items(), key=lambda item: (
        min_modules(item[0]), sum(item[0]), item[1][1], item[1][2]))
    module_count = min_modules(counts)
    matched = pin_match(counts, module_count)
    if matched is None:
        return dict(allocations=[], error='Не удалось назначить совместимые выводы ПЛК.')
    pool = {code: [] for code in CODES}
    for index, code in enumerate(demands_for(counts)):
        pool[code].append(matched[index])
    allocations = []
    for block, scheme in zip(blocks, selected):
        channels = []
        for code in CODES:
            for _ in range(scheme['io'].get(code, 0)):
                option = pool[code].pop(0)
                channels.append(dict(family=code, address=option['terminals'][0],
                                     terminals=option['terminals'], group=option['group'],
                                     deviceRef=option['deviceRef'], terminalStatus=option['terminalStatus'],
                                     commonKey=option['commonKey'], commonDesignation=option.get('commonDesignation'),
                                     commonRef=(f"{option['deviceRef']}:{option['commonDesignation']}"
                                                if option.get('commonDesignation') else None),
                                     source=f"{option['sourceSheet']}:{option['sourceRow']}"))
        allocations.append(dict(id=block['id'], scheme=scheme, channels=channels))
    modules = [dict(ref=f'M{index}', brand=CATALOG['module']['brand'], model=CATALOG['module']['model'],
                    source='moduls:2', terminalStatus=CATALOG['module']['terminalStatus'])
               for index in range(1, module_count + 1)]
    return dict(allocations=allocations, totals=dict(zip(CODES, counts)), modules=modules)
