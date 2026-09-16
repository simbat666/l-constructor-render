"""Logical I/O assignment. No physical controller terminals are inferred."""
CHANNELS = [
    ('di0', 'DI 0 В', 'DI0'), ('di24', 'DI 24 В', 'DI24'),
    ('doRelay', 'DO реле', 'DOR'), ('doTransistor', 'DO транзистор', 'DOT'),
    ('aiPt1000', 'AI Pt1000 (код ОЛ)', 'AIPT'), ('ai420', 'AI 4–20 мА', 'AI420'),
    ('ai010', 'AI 0–10 В', 'AI010'), ('ao420', 'AO 4–20 мА', 'AO420'), ('ao010', 'AO 0–10 В', 'AO010'),
]

def allocate_io(blocks):
    states = {0: dict(cost=0, selected=[])}
    known = {c[0] for c in CHANNELS}
    for block in blocks:
        for candidate in block['candidates']:
            if any(k not in known or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1000 or int(v) != v for k, v in candidate['io'].items()):
                return dict(allocations=[], error=f"Некорректный I/O: {block['id']}, строка {candidate['sourceRow']}.")
        if not block['candidates']:
            return dict(allocations=[], error=f"Нет варианта схемы для блока {block['id']}.")
        next_states = {}
        for used, state in states.items():
            for candidate in block['candidates']:
                mask = used | sum(1 << i for i, (key, _, _) in enumerate(CHANNELS) if candidate['io'].get(key, 0) > 0)
                cost = state['cost'] + candidate['io'].get('di0', 0) * 2 + candidate['io'].get('doRelay', 0)
                existing = next_states.get(mask)
                if not existing or cost < existing['cost'] or (cost == existing['cost'] and candidate['sourceRow'] < existing['selected'][-1]['sourceRow']):
                    next_states[mask] = dict(cost=cost, selected=state['selected'] + [candidate])
        states = next_states
    if not states:
        return dict(allocations=[], error='Не удалось распределить I/O.')
    best = min(states.items(), key=lambda item: (bin(item[0]).count('1'), item[1]['cost']))[1]
    counters = {key: 0 for key, _, _ in CHANNELS}
    allocations = []
    for block, scheme in zip(blocks, best['selected']):
        assigned = []
        for key, family, prefix in CHANNELS:
            for _ in range(int(scheme['io'].get(key, 0))):
                counters[key] += 1
                assigned.append(dict(family=family, address=f'{prefix}-{counters[key]:02d}'))
        allocations.append(dict(id=block['id'], scheme=scheme, channels=assigned))
    return dict(allocations=allocations, totals=counters)
