"""Server-owned questionnaire graph from the versioned OL snapshot."""
import json
import math
import re
from collections import deque
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'data/motor-v2.json').read_text(encoding='utf-8'))
if BASE.get('rulesSchemaVersion') != 3:
    raise ValueError('Нужен снимок ОЛ схемы 3; повтори импорт Excel')
BINDINGS = json.loads((ROOT / 'data/motor-field-bindings.json').read_text(encoding='utf-8'))
NODES = [dict(node, sheet=f'Ques {i}') for i in range(1, 5) for node in BASE.get(f'questions{i}', [])]
BY_ORDER = {node['order']: node for node in NODES}
SELECTORS = {'list', 'radio_group'}

def initial_state():
    return {part: {} for part in ('selected', 'inputs', 'checks', 'uploads')}

def norm(value):
    return str(value or '').strip().lower()

def node_key(node):
    return f"{node['sheet']}:{node['order']}"

def group_key(node):
    return f"{node['sheet']}:{node['question']}:{node['fieldType']}"

def binds(node):
    return [part.strip() for part in (node.get('binds') or '').split(';') if part.strip()]

def semantic_key(node):
    return node.get('engineKey') or FIELD_KEYS.get((node['sheet'], norm(node['question'])))


# Rules are loaded once per process; request state never changes these indexes.
FIELD_KEYS = {}
for binding in BINDINGS['fields']:
    FIELD_KEYS.setdefault((binding['sheet'], norm(binding['question'])), binding['key'])
TARGETS = {node['order']: tuple(binds(node)) for node in NODES}
REFERENCED = {order for targets in TARGETS.values() for order in targets}
ROOT_ORDERS = tuple(node['order'] for node in NODES if node['sheet'] == 'Ques 1'
                    and node['order'] not in REFERENCED and norm(node['answer']) != 'unused')
STATE_PARTS = ('selected', 'inputs', 'checks', 'uploads')

def number(value):
    try:
        return float(value) if value not in (None, '') else 0.0
    except (TypeError, ValueError):
        return math.nan

def numeric_rule(rule):
    match = re.fullmatch(r'float\|(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)\|(-?\d+(?:\.\d+)?)', rule or '')
    if not match:
        return None
    low, high, step = map(float, match.groups())
    return dict(min=low, max=high, step=step) if low <= high and step > 0 else None

def fmt(value):
    if not math.isfinite(value):
        return 'не указан'
    return format(value, '.15g')

def input_error(node, raw):
    value = (raw or '').strip()
    if not value:
        return None
    expression = node.get('inputRule') or node.get('answer') or ''
    rule = numeric_rule(expression)
    if not rule:
        length = re.fullmatch(r'string\|(\d+)\.\.(\d+)\|?', expression)
        if length and not int(length[1]) <= len(value.encode('utf-16-le')) // 2 <= int(length[2]):
            return f'длина {length[1]}…{length[2]} символов'
        return None
    n = number(value)
    if not math.isfinite(n) or not rule['min'] <= n <= rule['max']:
        return f"допустимо {fmt(rule['min'])}…{fmt(rule['max'])}"
    steps = (n - rule['min']) / rule['step']
    if abs(steps - round(steps)) > max(1e-8, abs(rule['step']) * 1e-7):
        return f"шаг {fmt(rule['step'])}"
    return None

def visible_groups(state):
    key = tuple(tuple(sorted(state[part].items())) for part in STATE_PARTS)
    # Return independent dictionaries: callers must not mutate another request's form.
    return [dict(group, nodes=[dict(node) for node in group['nodes']])
            for group in _visible_groups(key)]


@lru_cache(maxsize=128)
def _visible_groups(key):
    state = dict(zip(STATE_PARTS, map(dict, key)))
    distance = dict.fromkeys(ROOT_ORDERS, 0)
    pending = deque(ROOT_ORDERS)
    while pending:
        order = pending.popleft()
        node = BY_ORDER[order]
        kind, key = node['fieldType'], node_key(node)
        follow = ((kind in SELECTORS and state['selected'].get(group_key(node)) == order) or
                  (kind == 'input' and bool(state['inputs'].get(key, '').strip()) and not input_error(node, state['inputs'].get(key))) or
                  (kind == 'check_box' and state['checks'].get(key) is True) or
                  (kind == 'uploadButton' and bool(state['uploads'].get(key))))
        if follow:
            for target in TARGETS[order]:
                if target in BY_ORDER and target not in distance:
                    distance[target] = distance[order] + 1
                    pending.append(target)
    active = [n for n in NODES if n['order'] in distance and n['question'] and n['fieldType']]
    closest = {}
    for node in active:
        key = semantic_key(node) if node['fieldType'] == 'input' else None
        if key:
            closest[key] = min(closest.get(key, math.inf), distance[node['order']])
    groups = {}
    for node in active:
        semantic = semantic_key(node) if node['fieldType'] == 'input' else None
        if semantic and distance[node['order']] > closest[semantic]:
            continue
        key = group_key(node) if node['fieldType'] in SELECTORS else node_key(node)
        group = groups.setdefault(key, dict(key=key, sheet=node['sheet'], question=node['question'], fieldType=node['fieldType'], nodes=[], required=False))
        group['nodes'].append(node)
        group['required'] |= bool(node['required'])
    def sort_key(group):
        return (group['sheet'], tuple(int(p) if p.isdigit() else p for p in re.split(r'(\d+)', group['nodes'][0]['order'])))
    return sorted(groups.values(), key=sort_key)

def prune_state(state):
    result = initial_state()
    for group in visible_groups(state):
        if group['fieldType'] in SELECTORS:
            value = state['selected'].get(group['key'])
            if any(n['order'] == value for n in group['nodes']):
                result['selected'][group['key']] = value
        else:
            part = {'input': 'inputs', 'check_box': 'checks', 'uploadButton': 'uploads'}.get(group['fieldType'])
            key = node_key(group['nodes'][0])
            if part and key in state[part]:
                result[part][key] = state[part][key]
    return result

def active_answers(state):
    result = []
    for group in visible_groups(state):
        for node in group['nodes']:
            kind, key = node['fieldType'], node_key(node)
            value = None
            if kind in SELECTORS and state['selected'].get(group['key']) == node['order']:
                value = node['answer']
            part = {'input': 'inputs', 'check_box': 'checks', 'uploadButton': 'uploads'}.get(kind)
            if part:
                value = state[part].get(key)
            if value is not None and value != '':
                result.append(dict(node, value=value))
    return result

def missing_answers(state):
    result = []
    for group in visible_groups(state):
        node, kind = group['nodes'][0], group['fieldType']
        key = node_key(node)
        error = input_error(node, state['inputs'].get(key)) if kind == 'input' else None
        answered = True
        if kind in SELECTORS:
            answered = any(n['order'] == state['selected'].get(group['key']) for n in group['nodes'])
        elif kind == 'input':
            answered = bool(state['inputs'].get(key, '').strip()) and not error
        elif kind == 'check_box':
            answered = key in state['checks']
        elif kind == 'uploadButton':
            answered = bool(state['uploads'].get(key))
        if (group['required'] and not answered) or error:
            result.append(group['question'] + (f' ({error})' if error else ''))
    return result

def selections(state, prefix):
    result = {}
    for answer in active_answers(state):
        key = semantic_key(answer)
        if key and key.startswith(prefix):
            if answer['fieldType'] in SELECTORS:
                result[key[len(prefix):]] = answer['value']
            elif answer['fieldType'] == 'check_box' and answer['value'] is True:
                result[key[len(prefix):]] = 'да'
    return result

def diagram_rows(state):
    missing = missing_answers(state)
    dimensions = selections(state, 'diagram1.')
    if missing:
        return dict(rows=[], missing=missing)
    if not dimensions:
        return dict(rows=[], missing=['Нет выбранных параметров для diagram 1'])
    return dict(rows=[r for r in BASE['diagrams1'] if all(norm(r.get(k)) == norm(v) for k, v in dimensions.items())], missing=[])

def optional_rows(state):
    return [dict(row=row, sourceOrder=key) for key, value in selections(state, 'diagram2.').items() for row in BASE['diagrams2'] if norm(row.get(key)) == norm(value)]
