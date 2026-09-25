#!/usr/bin/env python3
"""Assemble selected CAD scheme blocks into one self-contained DXF package.

The input codes come from the workbook-derived scheme registry. The script does
not guess missing blocks and only imports audited DXF sources. DWG conversion is
intentionally outside this script: a production converter must prepare the DXF
library first.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import zipfile
from pathlib import Path

import ezdxf
from ezdxf import bbox, transform, xref
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

# A marker is deliberately just a marker: the layer contract determines its
# engineering meaning.  For example, ``#1`` on layer QF is a circuit breaker,
# while the identical string on XT1 is a terminal number and must not be
# touched by device numbering.
PLACEHOLDER = re.compile(r'^#(?P<root>[1-9]\d*)(?P<suffix>\.\d+)?$')
FORMAT_PREFIX = re.compile(r'^\\[^;]*;')


def mtext_plain(entity):
    """Read just the visible designation text, retaining source formatting."""
    return FORMAT_PREFIX.sub('', entity.text).strip()


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
    if contract.get('schemaVersion') != 1 or contract.get('sourceSha256') != source_sha256:
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

    input_family = contract.get('inputFamily')
    output_family = contract.get('outputFamily')
    if input_family not in ('DI24-NPN', 'DI24-PNP') or output_family not in ('DOR-NO', 'DOT-PNP'):
        raise ValueError(f'{code}: не задан проверенный тип входа и выхода')
    channels = instance.get('channels', [])
    di = [item for item in channels if item.get('family') == input_family]
    do = [item for item in channels if item.get('family') == output_family]
    expected_output_terminals = 2 if output_family == 'DOR-NO' else 1
    if len(di) != 1 or len(do) != 1 or len(do[0].get('terminals', [])) != expected_output_terminals:
        raise ValueError(f'{code}: требуется один {input_family} и один {output_family}')
    if not di[0].get('commonDesignation') or not di[0].get('address'):
        raise ValueError(f'{code}: не подтверждены общий вывод и клемма {input_family}')
    if output_family == 'DOT-PNP' and not do[0].get('commonDesignation'):
        raise ValueError(f'{code}: не подтверждён общий вывод {output_family}')
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
        'plc.inputCommon': di[0]['commonDesignation'],
        'plc.di': str(di[0]['address']),
        'plc.do.1': str(do[0]['terminals'][0]),
    }
    if output_family == 'DOR-NO':
        values['plc.do.2'] = str(do[0]['terminals'][1])
    else:
        values['plc.outputCommon'] = do[0]['commonDesignation']
    module_fields = {}
    if di[0].get('deviceRef', 'PLC') != 'PLC':
        module_fields.update({'plc.inputCommon': di[0]['deviceRef'], 'plc.di': di[0]['deviceRef']})
    if do[0].get('deviceRef', 'PLC') != 'PLC':
        module_fields.update({role: do[0]['deviceRef'] for role in
                              (('plc.do.1', 'plc.do.2') if output_family == 'DOR-NO'
                               else ('plc.do.1', 'plc.outputCommon'))})
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
    trace = []
    for field in fields:
        before = field['text']
        module_ref = module_fields.get(field['role'])
        after = before if module_ref else values.get(field['role'], before)
        unresolved = bool(module_ref) or (field['role'].startswith('header.') and field['role'] not in values)
        if not unresolved and field['role'] not in values:
            raise ValueError(f'{code}: неизвестная роль поля {field["role"]}')
        entity = entities[field['handle']]
        if after != before:
            entity.text = entity.text.replace(before, after)
        if field.get('offsetY'):
            position = entity.dxf.insert
            entity.dxf.insert = (position.x, position.y + field['offsetY'], position.z)
        trace.append({
            'fieldId': field['role'], 'layer': field['layer'],
            'before': before, 'after': after,
            'placeholderHandle': field['handle'],
            'offsetY': field.get('offsetY', 0),
            'status': 'unresolved' if unresolved else 'applied',
            'assignedModule': module_ref,
            'ruleSource': (f"diagram head:{head_by_key[field['diagramKey']]['sourceRow']}"
                           if field.get('diagramKey') in head_by_key else
                           (instance.get('cadFieldSources') or {}).get(
                               {'Imd-3': 'processDeviceType', 'Imd-1': 'marking', 'Imd-2': 'deviceTag'}[field['diagramKey']])
                           if field.get('diagramKey') and contract.get('headerSource') == 'questionnaire' else None),
        })
    return trace


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
        prepared.append((code, document, modelspace, extents, hashlib.sha256(path.read_bytes()).hexdigest()))
    return prepared


def split_pages(prepared, profile=None):
    pages, current, used_width = [], [], 0.0
    available_width = profile['right'] - profile['left'] if profile else RIGHT - LEFT
    for source in prepared:
        anchored = bool(profile and template_placement(source[0], source[2]))
        if current and anchored != bool(profile and template_placement(current[0][0], current[0][2])):
            pages.append(current)
            current, used_width = [], 0.0
        width = source[3].size.x
        gap = profile['gap'] if profile else 0
        if current and used_width + gap + width > available_width + 0.001:
            pages.append(current)
            current, used_width = [], 0.0
        if current:
            used_width += gap
        current.append(source)
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
             module_sheets=None) -> None:
    target = ezdxf.new("R2018", setup=True)
    target.header["$INSUNITS"] = 4  # millimetres; source library uses mm
    target_msp = target.modelspace()
    occurrence = 0
    designation_counters = {}
    seen_commons = set()
    field_contract = load_field_contract()
    module_sheets = module_sheets or []
    if module_sheets and (not profile or drawing_kind != 'electrical'):
        raise ValueError('Листы модулей допустимы только в принципиальном комплекте с рамкой')
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
        if profile and page and all(template_placement(code, source_msp) for code, _, source_msp, _, _ in page):
            packed_width = sum(item[3].size.x for item in page) + profile['gap'] * (len(page) - 1)
            cursor_x += ((profile['right'] - profile['left']) - packed_width) / 2
        for code, source_doc, source_msp, extents, source_sha256 in page:
            occurrence += 1
            instance = instances[occurrence - 1] if instances else {"tag": "", "id": str(occurrence)}
            placement = template_placement(code, source_msp) if profile else None
            if (TEMPLATE_CONTRACTS / f'{code}.json').is_file():
                applied_fields = apply_template_contract(code, source_msp, source_sha256, instance, designation_counters)
                for field in applied_fields:
                    if field['fieldId'] not in ('plc.inputCommon', 'plc.outputCommon'):
                        continue
                    if field['assignedModule']:
                        source_msp.delete_entity(source_doc.entitydb[field['placeholderHandle']])
                        field['after'] = ''
                        field['status'] = 'suppressedModuleCommon'
                        continue
                    if field['status'] != 'applied':
                        continue
                    common = field['after']
                    matching = [channel for channel in instance.get('channels', [])
                                if channel.get('commonDesignation') == common and channel.get('deviceRef', 'PLC') == 'PLC']
                    if not matching:
                        continue
                    key = ('PLC', common)
                    if key in seen_commons:
                        source_msp.delete_entity(source_doc.entitydb[field['placeholderHandle']])
                        field['after'] = ''
                        field['status'] = 'suppressedSharedCommon'
                    else:
                        seen_commons.add(key)
            else:
                applied_fields = renumber_designations(source_msp, designation_counters, field_contract)
            if parameter_trace is not None:
                for field in applied_fields:
                    parameter_trace.append({
                        **field,
                        'drawingKind': drawing_kind,
                        'instanceId': instance['id'],
                        'sourceCode': code,
                    })
            if placement:
                dx = cursor_x - extents.extmin.x
                dy = placement['y'] - placement['anchor'].y
            else:
                dx = cursor_x - extents.extmin.x
                dy = -profile['sourceOriginY'] + profile['verticalOffset'] if profile else TOP - extents.extmax.y
            log = transform.inplace(source_msp, Matrix44.translate(dx, dy, 0))
            if len(log):
                raise ValueError(f"Не все сущности {code} удалось переместить: {list(log)}")
            xref.load_modelspace(source_doc, target, conflict_policy=xref.ConflictPolicy.XREF_PREFIX)
            # An occurrence label is NOT electrical device/terminal renumbering.
            target_msp.add_text(f"DRAFT {occurrence}: {instance['tag']} / {code}", dxfattribs={"insert": (cursor_x, 278 if profile else TOP + 5), "height": 2.0})
            cursor_x += extents.size.x + (profile['gap'] if profile else 0)
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
    for kind, profile in profiles.items():
        selected_instances = [item for item in instances if item['drawingKind'] == kind]
        if not selected_instances:
            continue
        selected = load_selected(library, sources, [item['code'] for item in selected_instances])
        pages = split_pages(load_and_validate(selected, profile), profile)
        path = output.with_name(f'{output.stem}-{kind}.dxf')
        supplements = module_sheets if kind == 'electrical' else []
        assemble(pages, path, selected_instances, profile, parameter_trace, kind, supplements)
        files.append(path)
        print(f"{profile['title']}: {len(selected_instances)} фрагментов, {len(pages) + len(supplements)} листов; рамка {profile['frame']}", flush=True)
    package_manifest = output.with_name(f'{output.stem}-manifest.json')
    manifest_payload['cadModuleSheets'] = module_sheets
    manifest_payload['cadParameterization'] = {
        'contractVersion': load_field_contract()['schemaVersion'],
        'fields': parameter_trace,
    }
    package_manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
        archive.write(package_manifest, 'manifest.json')
    print('Комплект DRAFT: типы схем разделены. Применённые и неразрешённые поля записаны в manifest; связи и штампы ещё требуют инженерного контракта.')


if __name__ == "__main__":
    main()
