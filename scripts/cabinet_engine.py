"""Authoritative cabinet calculation; shared by calculation and CAD endpoints."""
import math
import hashlib
from questionnaire import (BASE, BINDINGS, active_answers, diagram_rows, optional_rows,
                           selections, semantic_key, norm, number, fmt, prune_state, visible_groups)
from io_allocator import CATALOG, allocate_io

RULE_FINGERPRINT = hashlib.sha256('|'.join((
    BASE['source']['sha256'],
    CATALOG['sources']['composition']['sha256'],
    CATALOG['sources']['marking']['sha256'],
)).encode()).hexdigest()

def semantic_fields(state):
    fields = {}
    for answer in active_answers(state):
        key = semantic_key(answer)
        if key:
            fields.setdefault(key, []).append(dict(value=answer['value'], source=f"{answer['sheet']}:{answer['sourceRow']}"))
    return fields

def string(value):
    return str(value).lower() if isinstance(value, bool) else str(value)

def field(fields, key):
    values = fields.get(key, [])
    return string(values[0]['value']) if len(values) == 1 else None

def single_range(rows, current):
    return [r for r in rows if r['currentFrom'] <= current <= r['currentTo']]

def amperes(value):
    return fmt(math.floor(value * 1000 + 0.5) / 1000) if math.isfinite(value) else 'не указан'

def spec(row, source):
    return dict(name=row['name'], quantity=row['quantity'], unit=row['unit'], source=source)

def calculate_cabinet(motors):
    demands, blocks = [], []
    for motor in motors:
        selection = diagram_rows(motor['state'])
        errors = list(selection['missing'])
        fields = semantic_fields(motor['state'])
        if not selection['rows'] and not errors:
            errors.append('В diagram 1 нет подходящей строки.')
        for key, answers in fields.items():
            if len({string(a['value']) for a in answers}) > 1:
                errors.append(f"Противоречивые значения {key}: {', '.join(a['source'] for a in answers)}.")
        if selection['rows']:
            demands.append(dict(id=f"{motor['id']}:main", blockId=motor['id'], source='diagram 1', candidates=selection['rows']))
        optional = optional_rows(motor['state'])
        for dimension in selections(motor['state'], 'diagram2.'):
            candidates = [o['row'] for o in optional if o['sourceOrder'] == dimension]
            if dimension == 'ptc' and any(row['io'].get('AI-RTD2', 0) for row in candidates):
                errors.append('PTC назначен на AI-RTD2 в diagram 2: совместимость этого входа с PTC не подтверждена.')
                continue
            quantity_key = BINDINGS['optionalQuantities'].get(dimension)
            quantity = number(fields.get(quantity_key, [dict(value=math.nan)])[0]['value']) if quantity_key else 1
            if not math.isfinite(quantity) or quantity != int(quantity) or not 1 <= quantity <= 30:
                errors.append(f'Для {dimension} укажи целое количество 1…30; для отсутствующего датчика отключи его выбор.')
                continue
            if not candidates:
                errors.append(f'В diagram 2 нет варианта {dimension}.')
            for index in range(int(quantity)):
                demands.append(dict(id=f"{motor['id']}:{dimension}:{index + 1}", blockId=motor['id'], source='diagram 2', candidates=candidates))
        if any(a['fieldType'] == 'uploadButton' for a in active_answers(motor['state'])):
            errors.append('Пользовательское УГО: сохранено только имя файла. Импорт геометрии ещё не реализован.')
        blocks.append(dict(motor, errors=errors, warnings=[], fields=fields, optional=[], channels=[], loadIndex=None, loadRule=None, specification=[]))
    by_id = {b['id']: b for b in blocks}
    ready = [d for d in demands if not by_id[d['blockId']]['errors']]
    controller_sets = [{(norm(r.get('controller')), norm(r.get('controllerType'))) for r in d['candidates']
                        if norm(r.get('controller')) and norm(r.get('controllerType'))} for d in ready]
    common = set.intersection(*controller_sets) if controller_sets else set()
    supported = (norm(CATALOG['controller']['brand']), norm(CATALOG['controller']['model']))
    allocation_error = ('Нет общей модели контроллера у выбранных схем.' if ready and not common else
                        f"Для модели {', '.join(f'{brand} {model}' for brand, model in sorted(common))} нет проверенного каталога выводов." if common and supported not in common else None)
    plans = [dict(controller=f'{brand.upper()} {model.upper()}', **allocate_io([
        dict(d, candidates=[r for r in d['candidates'] if (norm(r.get('controller')), norm(r.get('controllerType'))) == (brand, model)])
        for d in ready])) for brand, model in sorted(common) if (brand, model) == supported]
    valid_plans = [p for p in plans if not p.get('error')]
    plan = min(valid_plans, key=lambda p: (len(p.get('modules', [])), sum(bool(v) for v in p.get('totals', {}).values()))) if valid_plans else None
    errors = [f"{b['tag']}: {e}" for b in blocks for e in b['errors']]
    if allocation_error:
        errors.append(allocation_error)
    if not plan:
        errors.extend(p.get('error', 'Ошибка I/O') for p in plans)
    instances = []
    demand_by_id = {d['id']: d for d in ready}
    for allocation in (plan or {}).get('allocations', []):
        demand = demand_by_id[allocation['id']]
        block = by_id[demand['blockId']]
        if demand['source'] == 'diagram 1':
            block['main'] = allocation['scheme']
        else:
            block['optional'].append(dict(row=allocation['scheme'], sourceOrder=demand['id']))
        block['channels'].extend(allocation['channels'])
        head_answers = {answer['diagramKey']: string(answer['value']) for answer in active_answers(block['state'])
                        if answer.get('diagramKey')}
        device_type = head_answers.get('Imd-3')
        cad_fields = {
            'processDeviceType': field(block['fields'], 'process.customType') if norm(device_type) == norm('Другой') else device_type,
            'marking': head_answers.get('Imd-1'),
            'deviceTag': head_answers.get('Imd-2'),
        }
        for index, key in enumerate(('diagram1', 'diagram2')):
            code = allocation['scheme'].get(key)
            if code and code.strip():
                head_rules = [dict(rule, value=cad_fields[{'Imd-3': 'processDeviceType', 'Imd-1': 'marking', 'Imd-2': 'deviceTag'}[rule['keyDiagram']]])
                              for rule in BASE.get('diagramHeads', []) if rule['code'] == code.strip()]
                instances.append(dict(drawingKind='electrical' if index == 0 else 'external', id=f"{demand['id']}:{index + 1}", blockId=block['id'], tag=block['tag'], code=code.strip(), source=f"{demand['source']}:{allocation['scheme']['sourceRow']}", channels=allocation['channels'], cadHeadRules=head_rules))
    for instance in instances:
        if instance['code'] == 'im1-011' and instance['drawingKind'] == 'electrical' and any(
                channel['deviceRef'] != 'PLC' and channel['family'] in ('DI24-NPN', 'DOR-NO')
                for channel in instance['channels']):
            errors.append(f"{instance['tag']}: im1-011: для вывода модуля нет проверенного поля обозначения устройства на схеме.")
    for block in blocks:
        if not block.get('main'):
            continue
        main, fields = block['main'], block['fields']
        rated = number(fields.get('motor.ratedCurrent', [dict(value=math.nan)])[0]['value'])
        start, location, selection = (field(fields, k) for k in ('diagram1.start', 'diagram1.location', 'vfd.selection'))
        candidates, vfd, vfd_source = [], None, None
        index_source, title = 'Load index 1', 'Подбор индекса нагрузки'
        detail = f'Номинальный ток двигателя: {amperes(rated)} А.'
        inside = norm(location) == norm('Внутри шкафа')
        if norm(start) != norm('Пуск через ЧП'):
            candidates = single_range([r for r in BASE['loadIndex1'] if all(r[k] == main[k] for k in ('startCode', 'voltageCode', 'tripCode'))], rated)
        elif inside and norm(selection) == norm('Ручной'):
            index_source, title = 'sp1', 'Ручной подбор ЧП'
            model = field(fields, 'vfd.model')
            detail = f"Выбранная модель ЧП: {model or 'не указана'}. Индекс берётся из той же строки sp1."
            matches = [r for r in BASE['manualVfd'] if norm(r['location']) == norm(location) and norm(r['selection']) == norm(selection) and norm(r['model']) == norm(model)]
            if len(matches) == 1:
                vfd, vfd_source = matches[0], 'sp1'
                candidates = [dict(matches[0], currentFrom=rated, currentTo=rated)]
            else:
                block['warnings'].append(f"Выбранная модель ЧП в sp1 неоднозначна: строки {', '.join(str(r['sourceRow']) for r in matches)}." if matches else 'В sp1 нет однозначного ЧП для выбранной ручной модели.')
        else:
            index_source = 'Load index 2'
            reserve = number(field(fields, 'vfd.reserve') or 0)
            current = rated * (1 + reserve / 100) if inside else number(field(fields, 'vfd.inputCurrent'))
            title = 'Автоматический подбор ЧП' if inside else 'Подбор внешнего ЧП'
            detail = (f'Номинальный ток {amperes(rated)} А + запас {fmt(reserve)}% = {amperes(current)} А. Значение сверяется с диапазоном Load index 2.' if inside else f'Введён входной ток ЧП: {amperes(current)} А. Значение сверяется с диапазоном Load index 2.')
            candidates = single_range([r for r in BASE['loadIndex2'] if r['startCode'] == main['startCode'] and r['voltageCode'] == main['voltageCode'] and (not selection or norm(r['selection']) == norm(selection))], current)
            if len(candidates) == 1 and inside:
                brand = field(fields, 'vfd.brand')
                matches = [r for r in BASE['automaticVfd'] if norm(r['location']) == norm(location) and norm(r['selection']) == norm(selection) and norm(r['brand']) == norm(brand) and r['loadIndex'] == candidates[0]['loadIndex']]
                if len(matches) == 1:
                    vfd, vfd_source = matches[0], 'sp2'
                else:
                    block['warnings'].append(f"Подбор ЧП из sp2 неоднозначен: строки {', '.join(str(r['sourceRow']) for r in matches)}." if matches else 'В sp2 нет ЧП для выбранного бренда и индекса нагрузки.')
        if len(candidates) == 1:
            block['loadIndex'] = candidates[0]['loadIndex']
            block['loadRule'] = dict(title=title, detail=f"{detail} Получен индекс {block['loadIndex']}.", source=f"{index_source}:{candidates[0]['sourceRow']}")
            block['specification'] = [spec(r, f"sp4:{r['sourceRow']}") for r in BASE['mainSpecification'] if r['diagram1'] == main['diagram1'] and r['loadIndex'] == block['loadIndex']]
            if not block['specification']:
                block['warnings'].append(f"Нет спецификации sp4 для {main['diagram1']} + {block['loadIndex']}.")
            if vfd:
                block['specification'].append(spec(vfd, f"{vfd_source}:{vfd['sourceRow']}"))
        elif not block['warnings']:
            block['warnings'].append(f"Граница диапазонов {index_source} неоднозначна: строки {', '.join(str(r['sourceRow']) for r in candidates)}. Индекс не угадан." if candidates else f'Индекс нагрузки не рассчитан: нет однозначного правила {index_source}.')
        for optional in block['optional']:
            rows = [r for r in BASE['sensorSpecification'] if r['diagram1'] == optional['row']['diagram1']]
            block['specification'].extend(spec(r, f"sp3:{r['sourceRow']} / {optional['sourceOrder']}") for r in rows)
            if not rows:
                block['warnings'].append(f"Нет спецификации sp3 для {optional['row']['diagram1']}.")
    for block in blocks:
        block.pop('fields')
        load_rule = block.pop('loadRule')
        inputs = [dict(id=f"{a['sheet']}:{a['order']}", title=a['question'] or a['order'], detail=('Да' if a['value'] else 'Нет') if isinstance(a['value'], bool) else str(a['value']), source=f"{a['sheet']}:{a['sourceRow']}") for a in active_answers(block['state'])]
        rules, outputs = [], []
        main = block.get('main')
        if main:
            rules.append(dict(id=f"diagram1:{main['sourceRow']}", title='Выбор основной схемы', detail=f"Сочетание активных ответов выбрало {main['diagram1'] or 'принципиальную схему не указано'}" + (f" и {main['diagram2']}" if main['diagram2'] else '') + '.', source=f"diagram 1:{main['sourceRow']}"))
        if load_rule:
            rules.append(dict(id=f"load:{load_rule['source']}", **load_rule))
        for optional in block['optional']:
            row = optional['row']
            rules.append(dict(id=f"diagram2:{row['sourceRow']}:{optional['sourceOrder']}", title='Дополнительный сигнал', detail=f"Выбрана схема {row['diagram1'] or row['diagram2'] or 'без кода'}.", source=f"diagram 2:{row['sourceRow']}"))
        if block['loadIndex']:
            outputs.append(dict(id='load-index', title='Индекс нагрузки', detail=block['loadIndex'], source=load_rule['source']))
        if block['channels']:
            outputs.append(dict(id='io', title='Выводы ПЛК', detail=', '.join(f"{c['deviceRef']}:{c['address']} · {c['family']}" for c in block['channels'])))
        for item in block['specification']:
            outputs.append(dict(id=f"spec:{item['source']}:{item['name']}", title=item['name'], detail=f"{fmt(item['quantity'])} {item['unit']}", source=item['source']))
        block['decisionTrace'] = dict(inputs=inputs, rules=rules, outputs=outputs)
    modules = plan['modules'] if plan else []
    return dict(schemaVersion=1, ruleFingerprint=RULE_FINGERPRINT, status='draft', blocks=blocks, instances=instances, errors=errors, warnings=[
        'DXF — черновая компоновка шаблонов, не выпущенная КД. QF/KM/KL нумеруются, но выводы ПЛК, XT, GND/COM, номиналы и соединения ещё не параметризованы.',
        'Выводы ПЛК и тестовых модулей распределены по таблице ключей. Клеммы шкафа XT и подключение GND/COM на CAD ещё не рассчитаны.',
        *(['Номера клемм M245 no display в тестовом листе составлены по образцу M245; нужна сверка с паспортом модуля.'] if modules else []),
    ], controllerFamily=plan['controller'] if plan else None,
       modules=modules,
       plcHardware=([dict(ref='PLC', brand=CATALOG['controller']['brand'], model=CATALOG['controller']['model'],
                          quantity=1, source='base_controller:2')] +
                    [dict(ref=module['ref'], brand=module['brand'], model=module['model'],
                          quantity=1, source=module['source']) for module in modules]) if plan else [],
       io=plan['totals'] if plan else {}, canExport=bool(motors) and not errors and bool(instances))

class RevisionConflict(ValueError):
    pass

def validate_project(payload):
    if not isinstance(payload, dict):
        raise ValueError('Нужен объект проекта')
    fingerprint = payload.get('ruleFingerprint')
    if fingerprint is not None and fingerprint != RULE_FINGERPRINT:
        raise RevisionConflict('Инженерная база обновилась. Повтори расчёт по текущей версии.')
    motors = payload.get('motors')
    if not isinstance(motors, list) or len(motors) > 40:
        raise ValueError('Допустимо до 40 блоков')
    seen, result = set(), []
    for motor in motors:
        if not isinstance(motor, dict):
            raise ValueError('Некорректный блок')
        identity, tag, state = motor.get('id'), motor.get('tag'), motor.get('state')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 100 or identity in seen:
            raise ValueError('Нужен уникальный id блока (1…100 символов)')
        if not isinstance(tag, str) or len(tag) > 60 or any(ord(c) < 32 for c in tag):
            raise ValueError('Некорректная маркировка блока')
        if not isinstance(state, dict):
            raise ValueError('Нужны ответы блока')
        clean = {}
        for part in ('selected', 'inputs', 'checks', 'uploads'):
            values = state.get(part)
            if not isinstance(values, dict) or len(values) > 500:
                raise ValueError('Некорректные ответы блока')
            if any(not isinstance(k, str) or len(k) > 500 or (not isinstance(v, bool) if part == 'checks' else not isinstance(v, str) or len(v) > 1000) for k, v in values.items()):
                raise ValueError('Некорректный тип или длина ответа')
            clean[part] = dict(values)
        seen.add(identity)
        result.append(dict(id=identity, tag=tag, state=prune_state(clean)))
    return result

def evaluate_project(payload):
    motors = validate_project(payload)
    calculation = calculate_cabinet(motors)
    # Only active question descriptions leave the server; never the rule tables/binds.
    forms = {}
    for motor in motors:
        forms[motor['id']] = [dict(g, nodes=[{k: v for k, v in n.items() if k not in ('binds', 'engineKey')} for n in g['nodes']]) for g in visible_groups(motor['state'])]
    return dict(calculation=calculation, forms=forms, revision=BASE['source'].get('revision', 'не указана'))
