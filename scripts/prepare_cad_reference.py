#!/usr/bin/env python3
"""Prepare reviewable CAD reference copies; never change the source drawings."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import ezdxf


FIELDS = {
    '15B6': ('#XT1', 'terminal.xt1.1', 'power'),
    '15B9': ('#XT2', 'terminal.xt1.2', 'power'),
    '15BC': ('#Process Device Type', 'header.processDeviceType', 'power'),
    '15D8': ('#KM1.1', 'device.km.contact', 'power'),
    '15D9': ('#QF1', 'device.qf.main', 'power'),
    '15DB': ('#GND1', 'plc.inputCommon', 'feedback'),
    '15F2': ('#KM1', 'device.km.main', 'control'),
    '15FB': ('#QF1.1', 'device.qf.contact', 'feedback'),
    '15FD': ('#DI24-NPN', 'plc.di', 'feedback'),
    '1600': ('#Marking', 'header.marking', 'power'),
    '1601': ('#Device tag', 'header.deviceTag', 'power'),
    '1607': ('#DOR-NO', 'plc.do.1', 'control'),
    '1609': ('#DOR-NO', 'plc.do.2', 'control'),
}
DISPLAY = {
    '15B6': '#1', '15B9': '#2', '15BC': '#TYPE',
    '15D8': '#KM.1', '15D9': '#QF', '15DB': '#GND',
    '15F2': '#KM', '15FB': '#QF.1', '15FD': '#DI',
    '1600': '#MARK', '1601': '#TAG', '1607': '#DO1', '1609': '#DO2',
}
FIXED = {'15B8': 'ХТ1', '15BD': 'РЕ', '15FF': 'В1'}
PARTS = {'power', 'feedback', 'control'}
STYLES = {
    'L_DRAWING': (25, True),
    'L_UGO': (25, True),
    'L_LABELS': (20, True),
    'L_POINTS': (13, True),
    'L_PART_BOUNDARY': (13, False),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def set_layer(doc, name: str, weight: int, plot: bool) -> None:
    layer = doc.layers.get(name) if name in doc.layers else doc.layers.new(name)
    layer.dxf.color = 7
    layer.dxf.lineweight = weight
    layer.dxf.plot = int(plot)
    layer.dxf.linetype = 'Continuous'


def bylayer(doc) -> None:
    for layout in (doc.modelspace(), *doc.blocks):
        for entity in layout:
            entity.dxf.lineweight = -1
            entity.dxf.color = 256
            if entity.dxftype() == 'INSERT':
                for attribute in entity.attribs:
                    attribute.dxf.lineweight = -1
                    attribute.dxf.color = 256


def prepare_template(source: Path, output: Path, dwg_source: Path) -> dict:
    doc = ezdxf.readfile(source)
    if doc.audit().has_errors or doc.header.get('$INSUNITS') != 4:
        raise ValueError('Эталонный DXF не прошёл аудит или задан не в миллиметрах')
    model = doc.modelspace()
    if len(model) != 94 or Counter(e.dxftype() for e in model)['INSERT'] != 3:
        raise ValueError('Состав исходного im1-011 изменился; требуется повторная проверка')
    for name, (weight, plot) in STYLES.items():
        set_layer(doc, name, weight, plot)
    if {e.dxf.layer for e in model} != set(STYLES) - {'L_UGO'}:
        raise ValueError('В исходном im1-011 появились неизвестные слои')
    parts = {}
    for insert in model.query('INSERT'):
        attributes = {a.dxf.tag: a.dxf.text for a in insert.attribs}
        part = attributes.get('PART_ID')
        if part not in PARTS or part in parts or attributes.get('DRAWING_KIND') != 'electrical':
            raise ValueError('Некорректная граница части схемы')
        if attributes.get('SECTION') != part or attributes.get('PART_ORDER') != '10':
            raise ValueError(f'Изменились исходные атрибуты части {part}')
        order = {'power': '10', 'feedback': '20', 'control': '30'}[part]
        next(a for a in insert.attribs if a.dxf.tag == 'PART_ORDER').dxf.text = order
        parts[part] = {'block': insert.dxf.name, 'handle': insert.dxf.handle,
                       'order': order, 'section': attributes.get('SECTION')}
    if set(parts) != PARTS:
        raise ValueError('В эталоне нет всех трёх частей')
    fields = []
    for handle, (expected, role, part) in FIELDS.items():
        entity = doc.entitydb.get(handle)
        if entity is None or entity.dxftype() != 'MTEXT' or entity.plain_text().strip() != expected:
            raise ValueError(f'Изменилось поле {handle} {role}')
        display = DISPLAY[handle]
        entity.text = entity.text.replace(expected, display)
        fields.append({'handle': handle, 'sourceText': expected,
                       'expectedText': display, 'role': role, 'part': part})
    for handle, expected in FIXED.items():
        entity = doc.entitydb.get(handle)
        if entity is None or entity.dxftype() != 'MTEXT' or entity.plain_text().strip() != expected:
            raise ValueError(f'Изменилась постоянная надпись {handle}')
    if len(list(model.query('MTEXT'))) != len(FIELDS) + len(FIXED):
        raise ValueError('Появились неучтённые надписи')
    bylayer(doc)
    doc.header['$LWDISPLAY'] = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    return {'schemaVersion': 1, 'status': 'DRAFT', 'sourceDwgSha256': digest(dwg_source),
            'sourceDxfSha256': digest(source), 'preparedDxfSha256': digest(output),
            'drawingKind': 'electrical', 'units': 'mm', 'parts': parts, 'fields': fields,
            'fixedLabels': FIXED, 'untypedPointCount': len(list(model.query('CIRCLE'))),
            'style': {k: {'lineweightMm': v[0] / 100, 'plot': v[1]} for k, v in STYLES.items()}}


def prepare_ugo(source: Path, output: Path) -> dict:
    doc = ezdxf.readfile(source)
    if doc.audit().has_errors:
        raise ValueError('Исходная библиотека УГО не прошла аудит')
    model = doc.modelspace()
    inserts = list(model.query('INSERT'))
    if len(inserts) != 323 or len(list(model.query('MTEXT'))) != 323:
        raise ValueError('Изменился состав каталога УГО')
    names = {e.dxf.name for e in inserts}
    if len(names) != 323:
        raise ValueError('Повторяется имя блока УГО')
    for name in ('L_UGO', 'L_LABELS'):
        set_layer(doc, name, *STYLES[name])
    if 'GOST_A' not in doc.styles:
        doc.styles.new('GOST_A', dxfattribs={'font': 'GOST_A.TTF'})
    doc.appids.new('L_UGO_LABEL') if 'L_UGO_LABEL' not in doc.appids else None
    definitions = {}
    for name in names:
        block = doc.blocks[name]
        for entity in list(block):
            if entity.dxf.layer == 'UGO_GEOMETRY':
                entity.dxf.layer = 'L_UGO'
            elif entity.dxf.layer == 'UGO_LABELS' and entity.dxftype() == 'ATTDEF':
                if entity.dxf.tag != 'TAG' or not entity.dxf.text:
                    raise ValueError(f'Некорректный исходный TAG блока {name}')
                definitions[name] = (entity.dxf.text, entity.dxf.insert)
                block.delete_entity(entity)
            else:
                raise ValueError(f'Неизвестный объект блока {name}: {entity.dxftype()} / {entity.dxf.layer}')
    labels = []
    for entity in list(model):
        if entity.dxf.layer == 'UGO_GEOMETRY':
            entity.dxf.layer = 'L_UGO'
        elif entity.dxf.layer == 'UGO_LABELS':
            entity.dxf.layer = 'L_LABELS'
        else:
            raise ValueError(f'Неизвестный слой каталога: {entity.dxf.layer}')
        if entity.dxftype() == 'INSERT':
            if len(entity.attribs) > 1:
                raise ValueError(f'Неожиданные атрибуты вставки {entity.dxf.name}')
            if not entity.attribs:
                if entity.dxf.name in definitions:
                    raise ValueError(f'Потеряна подпись УГО {entity.dxf.name}')
                continue
            attribute = entity.attribs[0]
            definition = definitions.get(entity.dxf.name)
            if (attribute.dxf.tag != 'TAG' or definition is None
                    or attribute.dxf.text != definition[0]):
                raise ValueError(f'Подпись вставки {entity.dxf.name} не совпала с блоком')
            text = '#' + attribute.dxf.text
            label = model.add_mtext(text, dxfattribs={
                'layer': 'L_LABELS', 'insert': attribute.dxf.insert,
                'char_height': 1.8, 'style': 'GOST_A',
                'attachment_point': 7, 'rotation': attribute.dxf.get('rotation', 0),
            })
            label.set_xdata('L_UGO_LABEL', [
                (1000, entity.dxf.name), (1000, entity.dxf.handle),
                (1040, definition[1].x), (1040, definition[1].y),
            ])
            labels.append(label)
            entity.delete_attrib('TAG')
    # Source header says metres, but catalogue coordinates and the target template
    # are drafting millimetres. Changing the header avoids 1000x insertion scaling.
    doc.header['$INSUNITS'] = 4
    doc.header['$MEASUREMENT'] = 1
    bylayer(doc)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    return {'sourceSha256': digest(source), 'preparedSha256': digest(output),
            'units': 'mm', 'symbolCount': len(inserts),
            'labelCount': len(labels),
            'layers': {'geometry': 'L_UGO', 'labels': 'L_LABELS'},
            'labelHeightMm': 1.8, 'labelEntityType': 'MTEXT',
            'labelPlaceholderPrefix': '#', 'labelPairXdata': 'L_UGO_LABEL',
            'note': 'Symbol blocks contain geometry only; each existing designation is a separate MTEXT.'}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--template-dwg', type=Path, required=True)
    parser.add_argument('--template-dxf', type=Path, required=True)
    parser.add_argument('--ugo-dxf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    contract = prepare_template(args.template_dxf, args.output / 'im1-011-etalon.dxf', args.template_dwg)
    ugo = prepare_ugo(args.ugo_dxf, args.output / 'IEC-UGO-dlya-etalone.dxf')
    (args.output / 'contract.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'ugo-audit.json').write_text(json.dumps(ugo, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
