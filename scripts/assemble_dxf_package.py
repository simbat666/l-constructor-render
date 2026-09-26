#!/usr/bin/env python3
"""Assemble selected CAD scheme blocks into one self-contained DXF package.

The input codes come from the workbook-derived scheme registry. The script does
not guess missing blocks and only imports audited DXF sources. DWG conversion is
intentionally outside this script: a production converter must prepare the DXF
library first.
"""

from __future__ import annotations

import argparse
import copy
import json
import hashlib
import re
import zipfile
from pathlib import Path

import ezdxf
from ezdxf import bbox, transform, xref
from ezdxf.enums import TextEntityAlignment
from ezdxf.math import Matrix44


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY = ROOT / "data" / "scheme-library.json"
DEFAULT_DXF_SOURCES = ROOT / "data" / "dxf-sources"
PAGE_WIDTH = 420.0
PAGE_HEIGHT = 297.0
PAGE_GAP = 20.0
LEFT = 25.0
RIGHT = 410.0
TOP = 280.0
BOTTOM = 65.0
PROFILES = ROOT / 'data' / 'drawing-profiles.json'
FIELD_CONTRACT = ROOT / 'data' / 'cad-field-contract.json'
TEMPLATE_CONTRACTS = ROOT / 'data' / 'cad-template-contracts'
PLC_CATALOG = ROOT / 'data' / 'plc-catalog.json'
PART_ORDER = ('power', 'control', 'feedback')
PART_TITLES = {'power': 'Силовая цепь', 'control': 'Цепь управления',
               'feedback': 'Обратная связь', 'other': 'Прочие цепи'}

# A marker is deliberately just a marker: the layer contract determines its
# engineering meaning.  For example, ``#1`` on layer QF is a circuit breaker,
# while the identical string on XT1 is a terminal number and must not be
# touched by device numbering.
PLACEHOLDER = re.compile(r'^#(?P<root>[1-9]\d*)(?P<suffix>\.\d+)?$')
FORMAT_PREFIX = re.compile(r'^\\[^;]*;')


def mtext_plain(entity):
    """Read just the visible designation text, retaining source formatting."""
    return FORMAT_PREFIX.sub('', entity.text).strip()


def place_terminal_label(code, field, entity, modelspace, value, module_ref=None):
    """Center a selected terminal on its source-locked conductor and box."""
    box = modelspace.doc.entitydb.get(field.get('boxHandle'))
    line = modelspace.doc.entitydb.get(field.get('lineHandle'))
    if (box is None or box.dxftype() != 'LWPOLYLINE' or box not in modelspace
            or line is None or line.dxftype() != 'LINE' or line not in modelspace):
        raise ValueError(f'{code}: нет проверенной геометрии клеммы {field["role"]}')
    points = list(box.get_points('xy'))
    if len(points) != 4:
        raise ValueError(f'{code}: изменилась рамка клеммы {field["role"]}')
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    low, high = min(p[1] for p in points), max(p[1] for p in points)
    axis = (left + right) / 2
    if (abs(right - left - 5) > .01 or abs(high - low - 4.902) > .01
            or abs(line.dxf.start.x - axis) > .01 or abs(line.dxf.end.x - axis) > .01
            or not (left < entity.dxf.insert.x < right and low < entity.dxf.insert.y < high)):
        raise ValueError(f'{code}: линия клеммы {field["role"]} не совпала с шаблоном')
    if field['role'].startswith('plc.'):
        # A four-character pin such as Q1-1 needs more than the original 5 mm.
        # Adjacent PLC boxes have 11.24 mm or more between their axes.
        half_width = 4.5
        box.set_points([(axis - half_width if x < axis else axis + half_width, y)
                        for x, y in points], format='xy')
    else:
        half_width = 2.5
    entity.text = value
    entity.dxf.insert = (axis, (low + high) / 2, entity.dxf.insert.z)
    entity.dxf.attachment_point = 5  # middle center, directly on conductor axis
    entity.dxf.width = 0  # no automatic wrapping inside the old narrow MTEXT width
    entity.dxf.char_height = 1.8
    # ezdxf may retain the source MTEXT's old glyph extents after replacing
    # its content. Measure a fresh temporary entity with the same font.
    probe = modelspace.add_mtext(value, dxfattribs={
        'style': entity.dxf.style, 'char_height': entity.dxf.char_height,
        'width': 0, 'attachment_point': 5,
    })
    measured = bbox.extents([probe], fast=True)
    modelspace.delete_entity(probe)
    if measured.has_data and measured.size.x > 2 * half_width - .5:
        entity.dxf.char_height *= (2 * half_width - .5) / measured.size.x
    if entity.dxf.char_height < 1.1:
        raise ValueError(f'{code}: надпись {value} не помещается в клемме {field["role"]}')
    if module_ref:
        owner = modelspace.add_text(module_ref, dxfattribs={
            'layer': entity.dxf.layer, 'height': 1.2, 'style': entity.dxf.style,
        })
        owner.set_placement((axis, high + 1.5), align=TextEntityAlignment.MIDDLE_CENTER)
        return owner.dxf.handle
    return None


def load_field_contract(path: Path = FIELD_CONTRACT):
    """Load the explicitly approved DXF placeholder contract.

    Source geometry is never inferred from a nearby visible label.  A new
    dynamic label is enabled only by adding its semantic layer here and a
    regression fixture/test.
    """
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('schemaVersion') != 1 or not isinstance(payload.get('fields'), list):
        raise ValueError('Некорректный контракт полей CAD')
    device_layers = {}
    for field in payload['fields']:
        if field.get('kind') != 'deviceDesignation' or not field.get('enabled'):
            continue
        layer = field.get('layer')
        prefix = field.get('prefix')
        if not isinstance(layer, str) or not isinstance(prefix, str) or not layer or not prefix:
            raise ValueError('В контракте CAD у обозначения аппарата нет слоя или префикса')
        if field.get('numbering') != 'cabinetWide' or layer in device_layers:
            raise ValueError(f'Некорректная сквозная нумерация слоя CAD: {layer}')
        device_layers[layer] = {'prefix': prefix, 'fieldId': field.get('id', layer)}
    return {'schemaVersion': payload['schemaVersion'], 'deviceLayers': device_layers}


def source_layer_name(layer: str) -> str:
    """Return source layer after ezdxf XREF import adds its private prefix."""
    return layer.rsplit('$', 1)[-1]


def designation_fields(modelspace, contract):
    """Return only approved device markers addressed by their source layers."""
    fields = []
    device_layers = contract['deviceLayers']
    for entity in modelspace:
        if entity.dxftype() != 'MTEXT':
            continue
        layer = source_layer_name(entity.dxf.layer)
        field = device_layers.get(layer)
        if field is None:
            continue
        visible = mtext_plain(entity)
        match = PLACEHOLDER.fullmatch(visible)
        if not match:
            continue
        fields.append({
            'prefix': field['prefix'],
            'fieldId': field['fieldId'],
            'layer': layer,
            'root': int(match['root']),
            'suffix': match['suffix'] or '',
            'placeholder': entity,
            'placeholderHandle': entity.dxf.handle,
        })
    return fields


def renumber_designations(modelspace, counters, contract):
    """Replace approved device fields and return their auditable trace."""
    fields = designation_fields(modelspace, contract)
    roots_by_prefix = {}
    for field in fields:
        roots_by_prefix.setdefault(field['prefix'], set()).add(field['root'])
    numbers = {}
    for prefix, roots in roots_by_prefix.items():
        # Source #1/#1.1 are the same physical device; every root gets one
        # cabinet-wide ordinal while the suffix remains a local contact marker.
        ordered = sorted(roots)
        numbers[prefix] = {
            root: counters.get(prefix, 0) + index
            for index, root in enumerate(ordered, start=1)
        }
        counters[prefix] = counters.get(prefix, 0) + len(ordered)
    trace = []
    for field in fields:
        new_number = numbers[field['prefix']][field['root']]
        before = mtext_plain(field['placeholder'])
        # ``#`` is a source-template marker, not part of the published
        # designation: ``#1.1`` becomes the readable ``1.1``.
        after = f'{new_number}{field["suffix"]}'
        # ``before`` was verified against PLACEHOLDER above, so this changes
        # exactly its visible marker and preserves MTEXT alignment controls.
        field['placeholder'].text = field['placeholder'].text.replace(before, after)
        trace.append({
            'fieldId': field['fieldId'],
            'prefix': field['prefix'],
            'layer': field['layer'],
            'before': before,
            'after': after,
            'placeholderHandle': field['placeholderHandle'],
        })
    return trace


def apply_template_contract(code, modelspace, source_sha256, instance, counters):
    """Apply only fields identified in a verified, versioned CAD template."""
    path = TEMPLATE_CONTRACTS / f'{code}.json'
    if not path.is_file():
        return []
    contract = json.loads(path.read_text(encoding='utf-8'))
    if contract.get('schemaVersion') != 2 or contract.get('sourceSha256') != source_sha256:
        raise ValueError(f'Изменилась версия DXF {code}; требуется повторная проверка полей')
    fields = contract.get('fields')
    if not isinstance(fields, list) or len({f['handle'] for f in fields}) != len(fields):
        raise ValueError(f'Некорректный контракт полей {code}')
    entities = {}
    for field in fields:
        entity = modelspace.doc.entitydb.get(field['handle'])
        if entity is None or entity.dxftype() != 'MTEXT' or entity.dxf.layer != field['layer'] or mtext_plain(entity) != field['text']:
            raise ValueError(f'Поле {code}:{field["handle"]} не совпало с проверенным шаблоном')
        entities[field['handle']] = entity

    catalog = json.loads(PLC_CATALOG.read_text(encoding='utf-8'))
    bindings = catalog.get('templateFields', {}).get(code, [])
    by_role = {item['role']: item for item in bindings}
    plc_fields = [field for field in fields if field['role'].startswith('plc.')]
    if len(by_role) != len(plc_fields) or {field['role'] for field in plc_fields} != set(by_role):
        raise ValueError(f'{code}: таблица полей CAD не соответствует шаблону')
    for field in plc_fields:
        if by_role[field['role']]['marker'] != field['text']:
            raise ValueError(f'{code}: ключ {field["role"]} в Excel не совпал с DXF')
    signals = contract['signals']
    channels = instance.get('channels', [])
    di = [item for item in channels if item.get('family') == signals['di']]
    do = [item for item in channels if item.get('family') == signals['do']]
    expected_output_terminals = 2 if 'plc.do.2' in by_role else 1
    if len(di) != 1 or len(do) != 1 or len(do[0].get('terminals', [])) != expected_output_terminals:
        raise ValueError(f'{code}: требуется один {signals["di"]} и один {signals["do"]}')
    for channel in (di[0], do[0]):
        module_ref = channel.get('deviceRef', 'PLC') != 'PLC'
        common = channel.get('commonDesignation')
        common_key = channel.get('commonKey') or ('#' + common if common else None)
        if common and common_key != '#' + common:
            raise ValueError(f'{code}: обозначение общего вывода не соответствует выбранной строке выводов')
        matched_rows = [option for option in catalog['modulePinOptions' if module_ref else 'pinOptions']
                        if option['code'] == channel['family']
                        and option['terminals'] == channel.get('terminals')
                        and option['commonDesignation'] == common
                        and option['commonKey'] == common_key]
        if len(matched_rows) != 1 or channel.get('address') != matched_rows[0]['terminals'][0]:
            raise ValueError(f'{code}: пара вывод/GND для {channel["family"]} отсутствует в таблице выводов')
    if not di[0].get('commonDesignation') or not di[0].get('address'):
        raise ValueError(f'{code}: не подтверждены общий вывод и клемма {signals["di"]}')
    if 'plc.do.common' in by_role and not do[0].get('commonDesignation'):
        raise ValueError(f'{code}: не подтверждён общий вывод {signals["do"]}')
    for role, channel in (('plc.di', di[0]), ('plc.common', di[0]), ('plc.do.1', do[0])):
        if by_role[role]['signal'] != channel['family']:
            raise ValueError(f'{code}: строка {role} ссылается на другой тип сигнала')
    for role in ('plc.do.2', 'plc.do.common'):
        if role in by_role and by_role[role]['signal'] != do[0]['family']:
            raise ValueError(f'{code}: строка {role} ссылается на другой тип сигнала')
    head_rules = instance.get('cadHeadRules')
    if not isinstance(head_rules, list) or (contract.get('requireDiagramHead') and len(head_rules) != 3):
        raise ValueError(f'{code}: нет трёх правил diagram head из ОЛ')
    head_by_key = {rule.get('keyDiagram'): rule for rule in head_rules}
    if len(head_by_key) != len(head_rules):
        raise ValueError(f'{code}: повтор ключа diagram head')

    # The source has one QF and one KM. Contacts share their device numbers.
    numbers = {prefix: counters.get(prefix, 0) + 1 for prefix in ('QF', 'KM')}
    for prefix, number in numbers.items():
        counters[prefix] = number
    first_terminal = counters.get('XT1', 0) + 1
    counters['XT1'] = first_terminal + 1
    values = {
        'terminal.xt1.1': str(first_terminal),
        'terminal.xt1.2': str(first_terminal + 1),
        'device.qf.main': f"QF{numbers['QF']}",
        'device.qf.contact': f"QF{numbers['QF']}.1",
        'device.km.main': f"KM{numbers['KM']}",
        'device.km.contact': f"KM{numbers['KM']}.1",
    }
    for role, binding in by_role.items():
        channel = di[0] if role in ('plc.di', 'plc.common') else do[0]
        source_column = binding['valueFrom']
        if source_column == 'CND_COM':
            value = channel.get('commonDesignation')
        else:
            terminal_index = int(source_column.rsplit(' ', 1)[1]) - 1
            value = channel['terminals'][terminal_index] if terminal_index < len(channel['terminals']) else None
        if not value:
            raise ValueError(f'{code}: для роли {role} нет значения {source_column} в выбранной строке ПЛК')
        values[role] = str(value)
    module_fields = {}
    if di[0].get('deviceRef', 'PLC') != 'PLC':
        module_fields.update({'plc.common': di[0]['deviceRef'], 'plc.di': di[0]['deviceRef']})
    if do[0].get('deviceRef', 'PLC') != 'PLC':
        module_fields.update({role: do[0]['deviceRef'] for role in
                              (('plc.do.1', 'plc.do.2') if 'plc.do.2' in by_role
                               else ('plc.do.1', 'plc.do.common'))})
    for field in fields:
        key = field.get('diagramKey')
        if not key:
            continue
        rule = head_by_key.get(key)
        if not rule:
            if contract.get('requireDiagramHead'):
                raise ValueError(f'{code}: ключ {key} отсутствует в diagram head ОЛ')
            if contract.get('headerSource') == 'questionnaire':
                header_name = {'Imd-3': 'processDeviceType', 'Imd-1': 'marking', 'Imd-2': 'deviceTag'}[key]
                value = (instance.get('cadFieldValues') or {}).get(header_name)
                if value is not None:
                    if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
                        raise ValueError(f'{code}: некорректное значение {key} из опросника')
                    values[field['role']] = value.strip()
            continue
        if rule.get('code') != code or rule.get('placeholder') != field.get('sourcePlaceholder', field['text']):
            raise ValueError(f'{code}: ключ {key} в diagram head не соответствует проверенному полю DXF')
        value = rule.get('value')
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
                raise ValueError(f'{code}: некорректное значение {key}')
            values[field['role']] = value.strip()
        else:
            # These OL questionnaire answers are optional. An empty answer is
            # an empty label, not an unresolved CAD template marker.
            values[field['role']] = ''
    trace = []
    for field in fields:
        before = field['text']
        module_ref = module_fields.get(field['role'])
        after = values.get(field['role'], before)
        unresolved = field['role'].startswith('header.') and field['role'] not in values
        if not unresolved and field['role'] not in values:
            raise ValueError(f'{code}: неизвестная роль поля {field["role"]}')
        entity = entities[field['handle']]
        owner_handle = None
        if field['role'].startswith(('plc.', 'terminal.')):
            owner_handle = place_terminal_label(code, field, entity, modelspace, after, module_ref)
        elif after != before:
            entity.text = entity.text.replace(before, after)
        if field.get('offsetY'):
            position = entity.dxf.insert
            entity.dxf.insert = (position.x, position.y + field['offsetY'], position.z)
        trace.append({
            'fieldId': field['role'], 'layer': field['layer'],
            'before': before, 'after': after,
            'placeholderHandle': field['handle'],
            'offsetY': field.get('offsetY', 0),
            'status': 'unresolved' if unresolved else 'empty-optional' if after == '' else 'applied',
            'assignedModule': module_ref,
            'ownerHandle': owner_handle,
            'ruleSource': (f"diagram head:{head_by_key[field['diagramKey']]['sourceRow']}"
                           if field.get('diagramKey') in head_by_key else
                           (instance.get('cadFieldSources') or {}).get(
                               {'Imd-3': 'processDeviceType', 'Imd-1': 'marking', 'Imd-2': 'deviceTag'}[field['diagramKey']])
                           if field.get('diagramKey') and contract.get('headerSource') == 'questionnaire' else None),
        })
    return trace


def merge_repeated_commons(code, modelspace, applied_fields, visible_commons, instance_id):
    """Keep one complete, isolated common branch per device/common on a page.

    The source-locked contract names the exact label, box, vertical conductor,
    and lower endpoint.  A duplicate is removed as a whole; other wires in the
    fragment are never shortened or inferred from nearby text.
    """
    path = TEMPLATE_CONTRACTS / f'{code}.json'
    contract = json.loads(path.read_text(encoding='utf-8'))
    branches = contract.get('commonBranches', {})
    removed = 0
    common_fields = [field for field in applied_fields if field['fieldId'] in branches]
    # A native #GND3 branch is clearer than a #GND1 branch renamed to GND3.
    # Keep the native branch when two different signals share this common.
    common_fields.sort(key=lambda field: field['before'] != '#' + field['after'])
    for field in common_fields:
        role = field['fieldId']
        module_ref = field.get('assignedModule')
        key = (module_ref or 'PLC', field['after'])
        if key not in visible_commons:
            visible_commons[key] = {'sourceCode': code, 'role': role, 'instanceId': instance_id}
            continue
        handles = branches[role]
        if len(handles) != 5 or handles[0] != field['placeholderHandle']:
            raise ValueError(f'{code}: неполная карта ветви {role}')
        expected = ('MTEXT', 'LWPOLYLINE', 'LINE', 'HATCH', 'CIRCLE')
        entities = [modelspace.doc.entitydb.get(handle) for handle in handles]
        if any(entity is None or entity.dxftype() != kind or entity not in modelspace
               for entity, kind in zip(entities, expected)):
            raise ValueError(f'{code}: изменилась геометрия ветви {role}')
        for entity in entities:
            modelspace.delete_entity(entity)
        if field.get('ownerHandle'):
            modelspace.delete_entity(modelspace.doc.entitydb[field['ownerHandle']])
        field['status'] = 'shared-common'
        field['removedEntityHandles'] = handles
        field['sharedWith'] = visible_commons[key]
        removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble selected scheme DXFs into one package")
    parser.add_argument("codes", nargs="*", help="Template occurrences in page order (repetition is allowed)")
    parser.add_argument("--manifest", type=Path, help="Validated list of independently identified template instances")
    parser.add_argument("--output", type=Path, required=True, help="Target DXF path")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--sources", type=Path, default=DEFAULT_DXF_SOURCES)
    return parser.parse_args()


def load_selected(library_path: Path, sources_dir: Path, codes: list[str]) -> list[tuple[str, Path]]:
    library = json.loads(library_path.read_text(encoding="utf-8"))
    records = {record["code"].casefold(): record for record in library["sources"]}
    selected = []
    missing = []
    for code in codes:
        record = records.get(code.casefold())
        dxf_path = sources_dir / f"{code}.dxf"
        if record is None or record["status"] != "found" or not dxf_path.is_file():
            missing.append(f"{code} ({dxf_path})")
        else:
            selected.append((code, dxf_path))
    if missing:
        raise FileNotFoundError(
            "Не подготовлены DXF-блоки: " + ", ".join(missing) + ". "
            "Сначала конвертируйте проверенные DWG из data/cad-sources в data/dxf-sources."
        )
    return selected


def template_placement(code, modelspace):
    """Resolve the authored anchor instead of using unrelated source extents."""
    path = TEMPLATE_CONTRACTS / f'{code}.json'
    if not path.is_file():
        return None
    placement = json.loads(path.read_text(encoding='utf-8')).get('placement')
    if not placement:
        return None
    entity = modelspace.doc.entitydb.get(placement['anchorHandle'])
    if (entity is None or entity.dxftype() != 'MTEXT' or entity not in modelspace
            or entity.plain_text().strip() != placement['anchorText']):
        raise ValueError(f'Не совпала опорная надпись CAD-шаблона {code}')
    return {'anchor': entity.dxf.insert, 'x': float(placement['x']), 'y': float(placement['y'])}


def load_and_validate(selected: list[tuple[str, Path]], profile=None):
    prepared = []
    available_width = profile['right'] - profile['left'] if profile else RIGHT - LEFT
    available_height = profile['top'] - profile['bottom'] if profile else TOP - BOTTOM
    for code, path in selected:
        document = ezdxf.readfile(path)
        audit = document.audit()
        if audit.has_errors:
            details = "; ".join(error.message for error in audit.errors)
            raise ValueError(f"DXF-блок {code} не проходит аудит: {details}")
        modelspace = document.modelspace()
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        contract_path = TEMPLATE_CONTRACTS / f'{code}.json'
        if contract_path.is_file():
            contract = json.loads(contract_path.read_text(encoding='utf-8'))
            if contract.get('sourceSha256') != source_sha256:
                raise ValueError(f'Изменилась версия DXF {code}; требуется повторная проверка полей')
            nonhatch = bbox.extents((entity for entity in modelspace if entity.dxftype() != 'HATCH'), fast=True)
            for handle in contract.get('detachedHatches', []):
                entity = document.entitydb.get(handle)
                if entity is None or entity.dxftype() != 'HATCH':
                    raise ValueError(f'{code}: не найден проверенный удалённый штриховой объект {handle}')
                box = bbox.extents([entity], fast=True)
                if not box.has_data or not (box.extmax.x < nonhatch.extmin.x - 5 or box.extmin.x > nonhatch.extmax.x + 5
                                            or box.extmax.y < nonhatch.extmin.y - 5 or box.extmin.y > nonhatch.extmax.y + 5):
                    raise ValueError(f'{code}: штриховой объект {handle} больше не отделён от схемы')
                modelspace.delete_entity(entity)
        extents = bbox.extents(modelspace, fast=True)
        if not extents.has_data:
            raise ValueError(f"DXF-блок {code} не содержит геометрии")
        if extents.size.x > available_width + 0.001 or extents.size.y > available_height + 0.001:
            raise ValueError(f"DXF-блок {code} не помещается в область A3")
        if profile:
            placement = template_placement(code, modelspace)
            if placement:
                dx, dy = placement['x'] - placement['anchor'].x, placement['y'] - placement['anchor'].y
                if (extents.extmin.x + dx < profile['left'] or extents.extmax.x + dx > profile['right']
                        or extents.extmin.y + dy < profile['bottom'] or extents.extmax.y + dy > profile['top']):
                    raise ValueError(f'Привязка {code} выходит за рабочую область рамки')
            elif (extents.extmin.y - profile['sourceOriginY'] + profile['verticalOffset'] < profile['bottom']
                  or extents.extmax.y - profile['sourceOriginY'] + profile['verticalOffset'] > profile['top']):
                raise ValueError(f'Неверная вертикальная привязка {code}: нужен контракт координат шаблона')
        prepared.append((code, document, modelspace, extents, source_sha256))
    return prepared


def validate_circuit_parts(code, modelspace, contract):
    """Check the source-locked entity partition and every crossing conductor."""
    parts = contract.get('circuitParts')
    if not isinstance(parts, dict):
        return None
    groups = {kind: parts.get(kind) for kind in PART_ORDER}
    if any(not isinstance(handles, list) or not handles or len(handles) != len(set(handles))
           for handles in groups.values()):
        raise ValueError(f'{code}: неполная карта частей схемы')
    owner = {}
    for kind, handles in groups.items():
        for handle in handles:
            if handle in owner:
                raise ValueError(f'{code}: объект {handle} назначен двум частям')
            owner[handle] = kind
    actual = {entity.dxf.handle for entity in modelspace}
    if set(owner) != actual:
        raise ValueError(f'{code}: карта частей не покрывает DXF: {sorted(actual ^ set(owner))}')
    expected_roles = {
        'terminal.xt1.1': 'power', 'terminal.xt1.2': 'power',
        'device.qf.main': 'power', 'device.km.contact': 'power',
        'device.qf.contact': 'feedback', 'plc.common': 'feedback', 'plc.di': 'feedback',
        'device.km.main': 'control', 'plc.do.1': 'control', 'plc.do.2': 'control',
        'plc.do.common': 'control',
    }
    for field in contract['fields']:
        expected = expected_roles.get(field['role'], 'power')
        if owner[field['handle']] != expected:
            raise ValueError(f"{code}: поле {field['role']} попало не в {expected}")
    shared_endpoints = {}
    for entity in modelspace.query('LINE'):
        for endpoint in ('start', 'end'):
            point = getattr(entity.dxf, endpoint)
            key = (round(point.x, 5), round(point.y, 5))
            shared_endpoints.setdefault(key, []).append((owner[entity.dxf.handle], entity.dxf.handle, endpoint))
    crossings = {key: items for key, items in shared_endpoints.items()
                 if len({item[0] for item in items}) > 1}
    connections = parts.get('interpartConnections', [])
    if not isinstance(connections, list) or len(connections) != len(crossings):
        raise ValueError(f'{code}: не все межчастные соединения описаны')
    seen = set()
    for connection in connections:
        if set(connection) != {'netId', 'feedback', 'control', 'at'}:
            raise ValueError(f'{code}: неверная карта межчастного соединения')
        at = connection['at']
        if not isinstance(at, list) or len(at) != 2:
            raise ValueError(f'{code}: неверная точка соединения')
        key = tuple(round(float(value), 5) for value in at)
        if key not in crossings or key in seen:
            raise ValueError(f'{code}: точка соединения не совпала с DXF')
        seen.add(key)
        recorded = {(kind, item['handle'], item['endpoint'])
                    for kind in ('feedback', 'control') for item in (connection[kind],)}
        if set(crossings[key]) != recorded or not isinstance(connection['netId'], str) or not connection['netId']:
            raise ValueError(f'{code}: провод в точке {key} не совпал с контрактом')
    return groups, connections


def prepare_grouped_electrical(prepared, instances):
    """Mark complete occurrences once, then copy their verified circuit parts."""
    if len(prepared) != len(instances):
        raise ValueError('Число схем не совпадает с числом блоков расчёта')
    counters = {}
    field_contract = load_field_contract()
    grouped = []
    for source_index, (source, instance) in enumerate(zip(prepared, instances)):
        code, document, modelspace, extents, source_sha = source
        contract_path = TEMPLATE_CONTRACTS / f'{code}.json'
        contract = json.loads(contract_path.read_text(encoding='utf-8')) if contract_path.is_file() else None
        partition = validate_circuit_parts(code, modelspace, contract) if contract else None
        if contract:
            placement = template_placement(code, modelspace)
            fields = apply_template_contract(code, modelspace, source_sha, instance, counters)
        else:
            placement = None
            fields = renumber_designations(modelspace, counters, field_contract)
        if not partition:
            grouped.append((code, document, modelspace, extents, source_sha, {
                'partKind': 'other', 'instance': instance, 'fields': fields,
                'sourceIndex': source_index, 'preparameterized': True,
            }))
            continue
        groups, connections = partition
        for kind in PART_ORDER:
            part_doc = copy.deepcopy(document)
            part_model = part_doc.modelspace()
            handles = set(groups[kind])
            handles.update(field['ownerHandle'] for field in fields
                           if field.get('ownerHandle') and field['placeholderHandle'] in handles)
            for entity in list(part_model):
                if entity.dxf.handle not in handles:
                    part_model.delete_entity(entity)
            part_extents = bbox.extents(part_model, fast=True)
            if not part_extents.has_data:
                raise ValueError(f'{code}: пустая часть {kind}')
            part_fields = [field.copy() for field in fields if field['placeholderHandle'] in handles]
            part_ports = [dict(netId=connection['netId'], at=connection['at'], peerPart=(
                'control' if kind == 'feedback' else 'feedback')) for connection in connections
                if kind in ('feedback', 'control')]
            grouped.append((code, part_doc, part_model, part_extents, source_sha, {
                'partKind': kind, 'instance': instance, 'fields': part_fields,
                'sourceIndex': source_index, 'preparameterized': True,
                'anchorSourceY': placement['anchor'].y,
                'anchorTargetY': placement['y'], 'ports': part_ports,
            }))
    # Pack one circuit kind at a time; keep cabinet block order within each kind.
    return sorted(grouped, key=lambda item: (PART_ORDER.index(item[5]['partKind'])
                                              if item[5]['partKind'] in PART_ORDER else len(PART_ORDER),
                                              item[5]['sourceIndex']))


def source_layout_width(source):
    """Return the width reserved by this source or complete block."""
    meta = source[5] if len(source) > 5 else {}
    if meta.get('groupWidth') is not None:
        return meta['groupWidth']
    width = source[3].size.x + (20 if meta.get('ports') else 0)
    return max(width, 65) if meta.get('partKind') in PART_ORDER else width


def source_anchored(source, profile):
    if not profile:
        return False
    if len(source) > 5 and source[5].get('anchorSourceY') is not None:
        return True
    return bool(template_placement(source[0], source[2]))


def page_group_widths(page):
    """Return one width per layout unit, not one width per circuit part."""
    widths = []
    seen = set()
    for index, source in enumerate(page):
        meta = source[5] if len(source) > 5 else {}
        if meta.get('groupWidth') is not None:
            key = ('group', meta.get('sourceIndex'))
        else:
            key = ('item', index)
        if key in seen:
            continue
        seen.add(key)
        widths.append(source_layout_width(source))
    return widths


def split_pages(prepared, profile=None):
    pages, current, used_width = [], [], 0.0
    available_width = profile['right'] - profile['left'] if profile else RIGHT - LEFT
    index = 0
    while index < len(prepared):
        source = prepared[index]
        meta = source[5] if len(source) > 5 else {}
        group_index = meta.get('sourceIndex') if meta.get('groupWidth') is not None else None
        group = [source]
        index += 1
        while index < len(prepared):
            candidate = prepared[index]
            candidate_meta = candidate[5] if len(candidate) > 5 else {}
            if group_index is None or candidate_meta.get('sourceIndex') != group_index:
                break
            group.append(candidate)
            index += 1
        anchored = source_anchored(group[0], profile)
        current_meta = current[0][5] if current and len(current[0]) > 5 else {}
        if current and (anchored != source_anchored(current[0], profile)
                        or meta.get('partKind') != current_meta.get('partKind')):
            pages.append(current)
            current, used_width = [], 0.0
        width = source_layout_width(group[0])
        gap = profile['gap'] if profile else 0
        if current and used_width + gap + width > available_width + 0.001:
            pages.append(current)
            current, used_width = [], 0.0
        if current:
            used_width += gap
        current.extend(group)
        used_width += width
    if current:
        pages.append(current)
    return pages


def add_module_schedule(modelspace, page_x, module, instances):
    """Show calculated module assignments without claiming a verified module schematic."""
    rows = []
    for instance in instances:
        for channel in instance.get('channels', []):
            if channel.get('deviceRef') != module['ref']:
                continue
            terminals = channel.get('terminals')
            if not isinstance(terminals, list) or not terminals or not all(isinstance(t, str) and t for t in terminals):
                raise ValueError(f"{module['ref']}: не заданы назначенные клеммы канала")
            rows.append((instance.get('tag', ''), channel.get('family', ''), ', '.join(terminals),
                         channel.get('commonDesignation') or '—'))
    if not rows:
        raise ValueError(f"{module['ref']}: лист модуля без назначенных сигналов")
    if len(rows) > 24:
        raise ValueError(f"{module['ref']}: назначения не помещаются на лист A3")
    def label(value, x, y, height=2.5):
        modelspace.add_text(str(value), dxfattribs={'insert': (page_x + x, y), 'height': height,
                                                    'layer': 'L-MODULE-SCHEDULE'})
    label(f"ДОП. МОДУЛЬ {module['ref']} / {module['brand']} {module['model']}", 55, 271, 3.5)
    label('НАЗНАЧЕННЫЕ ВХОДЫ И ВЫХОДЫ / ДЛЯ СВЕРКИ', 55, 258, 2.5)
    label('Блок', 55, 244)
    label('Сигнал', 102, 244)
    label(f"Клеммы {module['ref']}", 218, 244)
    label('Общий', 325, 244)
    for index, (tag, family, terminals, common) in enumerate(rows):
        y = 232 - index * 7
        for value, x in ((tag, 55), (family, 102), (terminals, 218), (common, 325)):
            label(value, x, y)
    label('Клеммы модуля: тестовые данные каталога, требуется сверка с паспортом.', 55, 48, 2.0)


def add_frame(target, profile, page_index, page_count, page_x):
    path = ROOT / 'data' / 'dxf-frames' / profile['frame']
    if hashlib.sha256(path.read_bytes()).hexdigest() != profile['sha256']:
        raise ValueError(f"Изменилась версия рамки {profile['frame']}; перепроверь привязки")
    frame = ezdxf.readfile(path)
    if frame.audit().has_errors:
        raise ValueError('Ошибка аудита рамки')
    for key, text in [('numberAnchor', str(page_index + 1)), ('documentAnchor', f"L / {page_index + 1} из {page_count} / DRAFT")]:
        anchor = profile[key]
        entity = frame.entitydb.get(anchor['handle'])
        if entity is None or entity.dxftype() != 'MTEXT' or entity.plain_text().strip() != anchor['text']:
            raise ValueError(f'Не совпала сигнатура поля рамки {key}')
        entity.text = text
    origin_x, origin_y = profile['origin']
    model = frame.modelspace()
    if profile['excludeOutsidePage']:
        # The supplied external frame contains 20 stray HATCH entities wholly
        # outside its A3 INSERT. Exclude only these in the generated copy.
        for entity in list(model):
            if entity.dxftype() != 'HATCH':
                continue
            box = bbox.extents([entity], fast=True)
            if box.has_data and box.extmin.x > origin_x + PAGE_WIDTH:
                model.delete_entity(entity)
    log = transform.inplace(model, Matrix44.translate(page_x - origin_x, -origin_y, 0))
    if len(log):
        raise ValueError('Не удалось переместить рамку')
    xref.load_modelspace(frame, target, conflict_policy=xref.ConflictPolicy.XREF_PREFIX)
    target.modelspace().add_text(profile['title'] + ' / DRAFT', dxfattribs={'insert': (page_x + 40, 290), 'height': 2.0})


def assemble(pages, output: Path, instances=None, profile=None, parameter_trace=None, drawing_kind=None,
             module_sheets=None, connection_trace=None) -> None:
    target = ezdxf.new("R2018", setup=True)
    target.header["$INSUNITS"] = 4  # millimetres; source library uses mm
    target_msp = target.modelspace()
    occurrence = 0
    designation_counters = {}
    field_contract = load_field_contract()
    module_sheets = module_sheets or []
    if module_sheets and (not profile or drawing_kind != 'electrical'):
        raise ValueError('Листы модулей допустимы только в принципиальном комплекте с рамкой')
    part_pages = {}
    for index, page in enumerate(pages):
        for source in page:
            if len(source) > 5:
                meta = source[5]
                part_pages[(meta['sourceIndex'], meta['partKind'])] = index + 1
    all_pages = pages + [[] for _ in module_sheets]
    for page_index, page in enumerate(all_pages):
        page_x = page_index * (PAGE_WIDTH + PAGE_GAP)
        if profile:
            add_frame(target, profile, page_index, len(all_pages), page_x)
        if page_index >= len(pages):
            module = module_sheets[page_index - len(pages)]
            add_module_schedule(target_msp, page_x, module, instances or [])
            continue
        cursor_x = page_x + (profile['left'] if profile else LEFT)
        if profile and page and all(source_anchored(item, profile) for item in page):
            widths = page_group_widths(page)
            packed_width = sum(widths) + profile['gap'] * max(len(widths) - 1, 0)
            cursor_x += ((profile['right'] - profile['left']) - packed_width) / 2
        visible_commons = {}
        active_group = None
        group_cursor_x = None
        group_width = None
        for source_index_in_page, source in enumerate(page):
            code, source_doc, source_msp, extents, source_sha256 = source[:5]
            meta = source[5] if len(source) > 5 else {}
            grouped = meta.get('groupWidth') is not None
            group_key = (('group', meta.get('sourceIndex')) if grouped
                         else ('item', source_index_in_page))
            if group_key != active_group:
                active_group = group_key
                group_cursor_x = cursor_x
                group_width = meta.get('groupWidth') if grouped else None
            occurrence += 1
            instance = meta.get('instance') or (instances[occurrence - 1] if instances else {"tag": "", "id": str(occurrence)})
            placement = (template_placement(code, source_msp)
                         if profile and meta.get('anchorSourceY') is None else None)
            if meta.get('preparameterized'):
                applied_fields = meta['fields']
                shared_count = (merge_repeated_commons(code, source_msp, applied_fields, visible_commons, instance['id'])
                                if (TEMPLATE_CONTRACTS / f'{code}.json').is_file() else 0)
            elif (TEMPLATE_CONTRACTS / f'{code}.json').is_file():
                applied_fields = apply_template_contract(code, source_msp, source_sha256, instance, designation_counters)
                shared_count = merge_repeated_commons(code, source_msp, applied_fields, visible_commons, instance['id'])
            else:
                applied_fields = renumber_designations(source_msp, designation_counters, field_contract)
                shared_count = 0
            if parameter_trace is not None:
                for field in applied_fields:
                    parameter_trace.append({
                        **field,
                        'drawingKind': drawing_kind,
                        'instanceId': instance['id'],
                        'sourceCode': code,
                        'sheetNumber': page_index + 1,
                        **({'circuitPart': meta['partKind']} if meta.get('partKind') else {}),
                    })
            current_extents = bbox.extents(source_msp, fast=True)
            if not current_extents.has_data:
                raise ValueError(f'{code}: после объединения общих выводов нет геометрии')
            if grouped:
                dx = group_cursor_x + meta['sourceOffsetX'] - current_extents.extmin.x
                dy = meta['anchorTargetY'] - meta['anchorSourceY']
            elif meta.get('anchorSourceY') is not None:
                dx = cursor_x - current_extents.extmin.x
                dy = meta['anchorTargetY'] - meta['anchorSourceY']
            elif placement:
                dx = cursor_x - current_extents.extmin.x
                dy = placement['y'] - placement['anchor'].y
            else:
                dx = cursor_x - current_extents.extmin.x
                dy = -profile['sourceOriginY'] + profile['verticalOffset'] if profile else TOP - current_extents.extmax.y
            log = transform.inplace(source_msp, Matrix44.translate(dx, dy, 0))
            if len(log):
                raise ValueError(f"Не все сущности {code} удалось переместить: {list(log)}")
            xref.load_modelspace(source_doc, target, conflict_policy=xref.ConflictPolicy.XREF_PREFIX)
            for port in meta.get('ports', []):
                peer_page = part_pages.get((meta['sourceIndex'], port['peerPart']))
                if peer_page is None:
                    raise ValueError(f'{code}: нет листа для соединения {port["netId"]}')
                x, y = port['at'][0] + dx, port['at'][1] + dy
                net_name = f"NET-{meta['sourceIndex'] + 1}"
                # Parts of one motor are kept on one sheet whenever possible.
                # A same-sheet connection is already drawn by the source wire;
                # only a real cross-sheet connection gets a NET reference.
                if peer_page != page_index + 1:
                    target_msp.add_circle((x, y), radius=0.6,
                                          dxfattribs={'layer': 'L-INTERPART-REF'})
                    target_msp.add_text(f'{net_name} / Л.{peer_page}', dxfattribs={
                        'insert': (x + 1.5, y + 1.5), 'height': 1.8,
                        'layer': 'L-INTERPART-REF'})
                if connection_trace is not None:
                    connection_trace.append({
                        'instanceId': instance['id'], 'sourceCode': code,
                        'netId': net_name, 'part': meta['partKind'],
                        'page': page_index + 1, 'peerPart': port['peerPart'],
                        'peerPage': peer_page, 'point': [x, y],
                    })
            # An occurrence label is NOT electrical device/terminal renumbering.
            part_title = PART_TITLES.get(meta.get('partKind'))
            label = f"DRAFT {instance['tag']} / {code}" + (f" / {part_title}" if part_title else '')
            label_x = (group_cursor_x + meta['sourceOffsetX']) if grouped else cursor_x
            target_msp.add_text(label, dxfattribs={"insert": (label_x, 278 if profile else TOP + 5), "height": 2.0})
            next_source = page[source_index_in_page + 1] if source_index_in_page + 1 < len(page) else None
            next_meta = next_source[5] if next_source is not None and len(next_source) > 5 else {}
            next_grouped = next_meta.get('groupWidth') is not None
            next_key = (('group', next_meta.get('sourceIndex')) if next_grouped
                        else ('item', source_index_in_page + 1))
            if next_source is None or next_key != group_key:
                if grouped:
                    cursor_x = group_cursor_x + group_width + (profile['gap'] if profile else 0)
                else:
                    cursor_x += current_extents.size.x + (20 if meta.get('ports') else 0) + (profile['gap'] if profile else 0)
    extents = bbox.extents(target_msp, fast=False)
    if extents.has_data:
        # Keep the header's WCS extents consistent with the assembled model.
        # This is viewport metadata, not proof of AutoCAD compatibility.
        target.header["$EXTMIN"] = (extents.extmin.x, extents.extmin.y, extents.extmin.z)
        target.header["$EXTMAX"] = (extents.extmax.x, extents.extmax.y, extents.extmax.z)
        target.set_modelspace_vport(height=max(extents.size.x, extents.size.y) * 1.1, center=extents.center)
    audit = target.audit()
    if audit.has_errors:
        raise RuntimeError("Итоговый DXF не проходит аудит: " + "; ".join(error.message for error in audit.errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    target.saveas(output)
    # ezdxf intentionally rewrites EXTMIN/EXTMAX to its sentinel values on save.
    # Preserve measured bounds for viewers; validate the serialized result below.
    lines = output.read_text(encoding="utf-8").splitlines()
    for variable, point in (("$EXTMIN", extents.extmin), ("$EXTMAX", extents.extmax)):
        marker = lines.index(variable)
        lines[marker + 2] = f"{point.x:.16g}"
        lines[marker + 4] = f"{point.y:.16g}"
        lines[marker + 6] = f"{point.z:.16g}"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reopened = ezdxf.readfile(output)
    if reopened.audit().has_errors:
        raise RuntimeError("Сохранённый DXF не проходит повторное чтение и аудит")


def main() -> None:
    args = parse_args()
    instances = json.loads(args.manifest.read_text(encoding="utf-8"))["instances"] if args.manifest else None
    if args.output.suffix == '.zip':
        if not instances:
            raise ValueError('Комплект требует manifest с экземплярами и drawingKind')
        build_bundle(instances, args.output, args.manifest, args.library, args.sources)
        return
    codes = [item["code"] for item in instances] if instances else args.codes
    if not codes:
        raise ValueError("Не выбраны экземпляры схем")
    selected = load_selected(args.library, args.sources, codes)
    pages = split_pages(load_and_validate(selected))
    assemble(pages, args.output, instances)
    print(f"Черновик DXF: {len(selected)} фрагментов, {len(pages)} областей A3 в modelspace. Повторное чтение и аудит ezdxf выполнены. Это не проверка в AutoCAD.")


def build_bundle(instances, output, manifest, library=DEFAULT_LIBRARY, sources=DEFAULT_DXF_SOURCES):
    profiles = json.loads(PROFILES.read_text(encoding='utf-8'))
    manifest_payload = json.loads(manifest.read_text(encoding='utf-8'))
    hardware = {item['ref']: item for item in manifest_payload.get('plcHardware', [])
                if isinstance(item, dict) and isinstance(item.get('ref'), str)}
    module_refs = {channel.get('deviceRef') for instance in instances for channel in instance.get('channels', [])
                   if isinstance(channel, dict) and channel.get('deviceRef') not in (None, 'PLC')}
    if any(not isinstance(ref, str) or not re.fullmatch(r'M[1-9]\d{0,2}', ref) for ref in module_refs):
        raise ValueError('Некорректное обозначение модуля для дополнительного листа')
    module_sheets = [dict(ref=ref,
                          brand=str(hardware.get(ref, {}).get('brand') or 'ZENTEC'),
                          model=str(hardware.get(ref, {}).get('model') or 'M245 no display'))
                     for ref in sorted(module_refs, key=lambda item: int(item[1:]))]
    if any(item.get('drawingKind') not in profiles for item in instances):
        raise ValueError('Не задан вид документа для экземпляра')
    files = []
    parameter_trace = []
    connection_trace = []
    circuit_pages = []
    for kind, profile in profiles.items():
        selected_instances = [item for item in instances if item['drawingKind'] == kind]
        if not selected_instances:
            continue
        selected = load_selected(library, sources, [item['code'] for item in selected_instances])
        prepared = load_and_validate(selected, profile)
        if kind == 'electrical' and any((TEMPLATE_CONTRACTS / f"{item['code']}.json").is_file()
                                        for item in selected_instances):
            prepared = prepare_grouped_electrical(prepared, selected_instances)
        pages = split_pages(prepared, profile)
        if kind == 'electrical':
            for index, page in enumerate(pages):
                parts = []
                instance_ids = []
                for item in page:
                    if len(item) <= 5:
                        continue
                    part = item[5].get('partKind')
                    instance_id = item[5].get('instance', {}).get('id')
                    if part and part not in parts:
                        parts.append(part)
                    if instance_id and instance_id not in instance_ids:
                        instance_ids.append(instance_id)
                circuit_pages.append({
                    'sheetNumber': index + 1,
                    'part': parts[0] if len(parts) == 1 else 'grouped' if parts else 'unsplit',
                    'parts': parts,
                    'instanceIds': instance_ids,
                })
        path = output.with_name(f'{output.stem}-{kind}.dxf')
        supplements = module_sheets if kind == 'electrical' else []
        assemble(pages, path, selected_instances, profile, parameter_trace, kind, supplements,
                 connection_trace)
        files.append(path)
        print(f"{profile['title']}: {len(selected_instances)} блоков, {len(prepared)} частей, "
              f"{len(pages) + len(supplements)} листов; рамка {profile['frame']}", flush=True)
    package_manifest = output.with_name(f'{output.stem}-manifest.json')
    manifest_payload['cadModuleSheets'] = module_sheets
    manifest_payload['cadParameterization'] = {
        'contractVersion': load_field_contract()['schemaVersion'],
        'fields': parameter_trace,
    }
    manifest_payload['cadInterpartConnections'] = connection_trace
    manifest_payload['cadCircuitPages'] = circuit_pages
    package_manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
        archive.write(package_manifest, 'manifest.json')
    print('Комплект DRAFT: типы схем разделены. Применённые и неразрешённые поля записаны в manifest; связи и штампы ещё требуют инженерного контракта.')


if __name__ == "__main__":
    main()
