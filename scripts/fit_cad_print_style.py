#!/usr/bin/env python3
"""Fit the reviewed motor reference and IEC symbol blocks to a 1:1 A3 sheet.

The two source DXFs are supplied explicitly. This module writes new files only;
replacing user files is a separate, reviewable step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import ezdxf
from ezdxf import bbox, xref
from ezdxf.math import Matrix44


PART_CENTERS_MM = {'power': 95.0, 'feedback': 215.0, 'control': 335.0}
TERMINAL_TEXT_HANDLES = {'15B6', '15B8', '15B9', '15BD'}
PLC_TEXT_HANDLES = {'15DB', '15FD', '1607', '1609'}
BOX_TEXT_HANDLES = {'15B6', '15B9', '15BD'}
HEADER_Y_MM = {'15BC': 55.0, '1600': 45.0, '1601': 35.0}
TERMINAL_BOX_HANDLES = {'15B5', '15B7', '15BB'}
TERMINAL_LEAD_HANDLES = {'15B3', '15B4', '15BA'}
CATALOG_COLUMNS = 6
CATALOG_CELL = (85.0, 80.0)


def _read(path: Path):
    doc = ezdxf.readfile(path)
    if doc.audit().has_errors or doc.header.get('$INSUNITS') != 4:
        raise ValueError(f'Некорректный DXF или единицы не мм: {path}')
    return doc


def _center(extent, axis: str) -> float:
    return (getattr(extent.extmin, axis) + getattr(extent.extmax, axis)) / 2


def _set_insert_position(insert, x: float, y: float) -> None:
    insert.dxf.insert = (x, y, insert.dxf.insert.z)


def _available_fonts(doc) -> None:
    for style in doc.styles:
        gost = style.dxf.name in {
            'GOST_A', 'Текст 1', 'Текст 1.5',
            'GOST-2.304_Type-B', 'GOST-2.304_Type-B_italic',
            'GOST-2.304_Type-B_shrink',
        }
        style.dxf.font = 'GOST_A.TTF' if gost else 'Arial.ttf'
        if style.has_xdata('ACAD'):
            style.set_xdata('ACAD', [
                (1000, 'GOST type A' if gost else 'Arial'), (1071, 34),
            ])
    # Some converted DWGs carry an unnamed shape STYLE outside the named
    # style table. AutoCAD still loads its missing SHX even though no entity
    # uses the associated decorative linetypes.
    for entity in doc.entitydb.values():
        if (entity is not None and entity.dxftype() == 'STYLE'
                and not entity.dxf.get('name', '')):
            entity.dxf.font = 'ltypeshp.shx'


def symbol_scale_for_major(major_mm: float) -> float:
    if major_mm <= 0:
        raise ValueError('Габарит УГО должен быть положительным')
    return min(40.0 / major_mm, max(15.0 / 13.2292, 5.0 / major_mm))


def normalize_ugo(source: Path, output: Path) -> dict:
    """Normalize block definitions and arrange all catalogue inserts at scale 1."""
    doc = _read(source)
    model = doc.modelspace()
    inserts = {e.dxf.name: e for e in model.query('INSERT')}
    if len(inserts) != 323 or len(list(model.query('INSERT'))) != 323:
        raise ValueError('Ожидались 323 уникальных УГО')
    labels = {}
    descriptions = {}
    for entity in model.query('MTEXT'):
        if entity.has_xdata('L_UGO_LABEL'):
            tags = entity.get_xdata('L_UGO_LABEL')
            name = tags[0].value
            if name in labels or len(tags) != 4:
                raise ValueError(f'Неоднозначная подпись {name}')
            labels[name] = entity
        else:
            match = re.match(r'^(\d{3}) — ', entity.plain_text())
            if not match:
                raise ValueError(f'Неизвестная надпись {entity.dxf.handle}')
            name = 'IEC_' + match.group(1)
            if name in descriptions:
                raise ValueError(f'Повтор описания {name}')
            descriptions[name] = entity
    if len(labels) != 312 or set(descriptions) != set(inserts):
        raise ValueError('Каталог УГО или его подписи неполны')
    if 'L_UGO_PRINT' not in doc.appids:
        doc.appids.new('L_UGO_PRINT')
    doc.layers.get('L_UGO').dxf.lineweight = 30
    doc.layers.get('L_LABELS').dxf.lineweight = 20
    _available_fonts(doc)
    factors = {}
    for index, name in enumerate(sorted(inserts)):
        block = doc.blocks[name]
        if block.block.has_xdata('L_UGO_PRINT'):
            raise ValueError(f'{name} уже нормализован')
        original = bbox.extents(block)
        major = max(original.size.x, original.size.y)
        if major <= 0:
            raise ValueError(f'Пустой блок {name}')
        # A 13.229 mm source grid interval becomes 15 mm. Very small symbols
        # retain a visible 5 mm major dimension; complex symbols cap at 40 mm.
        factor = symbol_scale_for_major(major)
        for entity in block:
            if entity.dxftype() not in {'LINE', 'ARC'} or entity.dxf.layer != 'L_UGO':
                raise ValueError(f'Неожиданная геометрия {name}')
            entity.transform(Matrix44.scale(factor))
        sized = bbox.extents(block)
        if not 4.999 <= max(sized.size.x, sized.size.y) <= 40.001:
            raise ValueError(f'Габарит {name} вне рабочего диапазона')
        block.block.set_xdata('L_UGO_PRINT', [
            (1000, 'A3_1_TO_1_V1'), (1040, factor),
            (1040, sized.size.x), (1040, sized.size.y),
        ])
        col, row = index % CATALOG_COLUMNS, index // CATALOG_COLUMNS
        cell_x, cell_top = col * CATALOG_CELL[0], -row * CATALOG_CELL[1]
        symbol_x, symbol_y = cell_x + 42.5, cell_top - 30.0
        insert = inserts[name]
        if insert.dxf.get('rotation', 0) != 0:
            raise ValueError(f'Неожиданный поворот {name}')
        insert.dxf.xscale = insert.dxf.yscale = insert.dxf.zscale = 1
        _set_insert_position(insert, symbol_x - _center(sized, 'x'),
                             symbol_y - _center(sized, 'y'))
        if name in labels:
            label = labels[name]
            label.dxf.insert = (cell_x + 5.0, cell_top - 53.0, 0)
            label.dxf.char_height = 3.0 if label.plain_text().startswith('#XT') else 4.0
            label.dxf.attachment_point = 1
            label.dxf.style = 'Standard'
            label.set_xdata('L_UGO_LABEL', [
                (1000, name), (1000, insert.dxf.handle),
                (1040, label.dxf.insert.x - insert.dxf.insert.x),
                (1040, label.dxf.insert.y - insert.dxf.insert.y),
            ])
        desc = descriptions[name]
        desc.dxf.insert = (cell_x + 5.0, cell_top - 63.0, 0)
        desc.dxf.char_height = 2.5
        desc.dxf.width = 75.0
        desc.dxf.attachment_point = 1
        desc.dxf.style = 'Standard'
        factors[name] = factor
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    result = _read(output)
    if any(abs(i.dxf.xscale - 1) > 1e-9 or abs(i.dxf.yscale - 1) > 1e-9
           for i in result.modelspace().query('INSERT')):
        raise ValueError('Не все УГО вставлены в каталог в масштабе 1:1')
    return {'symbols': len(inserts), 'separateLabels': len(labels),
            'referenceGridMm': 15.0, 'geometryMajorMm': [5.0, 40.0],
            'insertScale': 1.0, 'deviceLabelHeightMm': 4.0,
            'terminalLabelHeightMm': 3.0,
            'descriptionHeightMm': 2.5, 'factors': factors}


def fit_template(source: Path, output: Path, origin: tuple[float, float]) -> dict:
    """Place the three source parts in equal A3 columns without moving their Y."""
    doc = _read(source)
    model = doc.modelspace()
    boundaries = {}
    for insert in model.query('INSERT'):
        attributes = {a.dxf.tag: a.dxf.text for a in insert.attribs}
        part = attributes.get('PART_ID')
        if part in boundaries or part not in PART_CENTERS_MM:
            raise ValueError('Границы частей схемы неоднозначны')
        boundaries[part] = bbox.extents([insert])
    if set(boundaries) != set(PART_CENTERS_MM):
        raise ValueError('В схеме должны быть три части')
    original_centers = {p: _center(b, 'x') - origin[0] for p, b in boundaries.items()}
    if not (115 < original_centers['power'] < 135
            and 145 < original_centers['feedback'] < 175
            and 175 < original_centers['control'] < 205):
        raise ValueError('Исходная схема уже смещена либо изменилась')
    shifts = {p: origin[0] + PART_CENTERS_MM[p] - _center(b, 'x')
              for p, b in boundaries.items()}
    counts = {p: 0 for p in boundaries}
    for entity in list(model):
        ext = bbox.extents([entity])
        matches = [p for p, b in boundaries.items()
                   if ext.extmin.x >= b.extmin.x - .02
                   and ext.extmax.x <= b.extmax.x + .02]
        if len(matches) != 1:
            raise ValueError(f'Неоднозначная часть для {entity.dxf.handle}')
        entity.transform(Matrix44.translate(shifts[matches[0]], 0, 0))
        counts[matches[0]] += 1
        if entity.dxftype() == 'MTEXT':
            entity.dxf.char_height = (
                2.5 if entity.dxf.handle in BOX_TEXT_HANDLES | {'15FF'}
                else 3.0 if entity.dxf.handle in {'15B8'} | PLC_TEXT_HANDLES
                else 4.0)
            entity.dxf.style = 'Standard'
            if entity.dxf.handle in HEADER_Y_MM:
                entity.dxf.insert = (entity.dxf.insert.x,
                                     origin[1] + HEADER_Y_MM[entity.dxf.handle], 0)
                entity.dxf.width = 95.0
    if counts != {'power': 63, 'feedback': 16, 'control': 15}:
        raise ValueError('Изменился состав частей схемы')
    # The source terminal boxes straddle the frame's four lower rails.
    # Move only the boxes and their text, and extend their existing leads.
    for handle in TERMINAL_BOX_HANDLES | TERMINAL_TEXT_HANDLES:
        doc.entitydb[handle].transform(Matrix44.translate(0, -20.0, 0))
    for handle, delta_x in {'15B8': -2.0, '15DB': -4.0,
                            '15FD': 1.0, '1607': -1.0,
                            '1609': 6.0}.items():
        doc.entitydb[handle].transform(Matrix44.translate(delta_x, 0, 0))
    for handle in TERMINAL_LEAD_HANDLES:
        lead = doc.entitydb[handle]
        start, end = lead.dxf.start, lead.dxf.end
        if not start.y > end.y:
            raise ValueError(f'Неожиданное направление вывода {handle}')
        lead.dxf.end = (end.x, end.y - 20.0, end.z)
    power_insert = next(
        i for i in model.query('INSERT')
        if {a.dxf.tag: a.dxf.text for a in i.attribs}.get('PART_ID') == 'power')
    outline = next(e for e in doc.blocks[power_insert.dxf.name]
                   if e.dxftype() == 'LWPOLYLINE')
    local_bottom = origin[1] + 30.0 - power_insert.dxf.insert.y
    outline.set_points([(x, local_bottom if abs(y) < 1e-6 else y)
                        for x, y in outline.get_points('xy')], format='xy')
    adjusted_bounds = {p: bbox.extents([i]) for i in model.query('INSERT')
                       for p in [next(a.dxf.text for a in i.attribs
                                      if a.dxf.tag == 'PART_ID')]}
    for entity in model:
        if entity.dxftype() == 'INSERT':
            continue
        ext = bbox.extents([entity])
        matches = [p for p, b in adjusted_bounds.items()
                   if ext.extmin.x >= b.extmin.x - .02
                   and ext.extmax.x <= b.extmax.x + .02
                   and ext.extmin.y >= b.extmin.y - .02
                   and ext.extmax.y <= b.extmax.y + .02]
        if len(matches) != 1:
            raise ValueError(f'Объект {entity.dxf.handle} вышел из границы части')
    for name, weight, plot in (
        ('L_DRAWING', 30, 1), ('L_UGO', 30, 1), ('L_LABELS', 20, 1),
        ('L_POINTS', 20, 1), ('L_PART_BOUNDARY', 13, 0),
    ):
        layer = doc.layers.get(name) if name in doc.layers else doc.layers.new(name)
        layer.dxf.color = 7
        layer.dxf.lineweight = weight
        layer.dxf.plot = plot
    _available_fonts(doc)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    _read(output)
    return {'parts': counts, 'centersMm': PART_CENTERS_MM,
            'deviceLabelHeightMm': 4.0, 'terminalAndPlcLabelHeightMm': 3.0,
            'insideTerminalHeightMm': 2.5,
            'headerTopMm': HEADER_Y_MM}


def build_e3_frame(source: Path, output: Path,
                   origin: tuple[float, float]) -> None:
    """Rebuild the supplied A3 border in the visual style of the E3 PDF.

    The original nine long power rails retain their coordinates and handles.
    This is a standalone reference frame, not the runtime frame in data/.
    """
    doc = _read(source)
    model = doc.modelspace()
    if doc.entitydb.get('12B0') is None:
        raise ValueError('Исходная силовая шина рамки не найдена')
    for entity in list(model):
        if entity.dxftype() == 'INSERT' or entity.dxf.handle in {'128E', '129B', '129D'}:
            model.delete_entity(entity)
    for name, color, weight, linetype in (
        ('L_E3_FRAME', 5, 50, 'Continuous'),
        ('L_E3_THIN', 5, 25, 'Continuous'),
        ('L_E3_GRID', 7, 10, 'Continuous'),
        ('L_E3_TEXT', 7, 20, 'Continuous'),
        ('L_NET_PHASE', 7, 30, 'Continuous'),
        ('L_NET_POS', 1, 30, 'Continuous'),
        ('L_NET_NEG', 5, 30, 'Continuous'),
        ('L_NET_PE', 3, 30, 'DASHED'),
    ):
        layer = doc.layers.get(name) if name in doc.layers else doc.layers.new(name)
        layer.dxf.color = color
        layer.dxf.lineweight = weight
        layer.dxf.linetype = linetype
        layer.dxf.plot = 1
    rail_y = {238.761: 'L_NET_PHASE', 233.761: 'L_NET_PHASE',
              228.761: 'L_NET_PHASE', 223.761: 'L_NET_POS',
              218.761: 'L_NET_POS', 84.85: 'L_NET_NEG',
              79.85: 'L_NET_NEG', 74.85: 'L_NET_NEG',
              69.85: 'L_NET_PE'}
    classified = 0
    for entity in model.query('LINE'):
        a, b = entity.dxf.start, entity.dxf.end
        if abs(a.y - b.y) < .01 and abs(a.x - b.x) > 200:
            relative = a.y - origin[1]
            nearest = min(rail_y, key=lambda y: abs(y - relative))
            if abs(nearest - relative) < .06:
                entity.dxf.layer = rail_y[nearest]
                entity.dxf.color = 256
                entity.dxf.lineweight = -1
                classified += 1
    if classified != 9:
        raise ValueError(f'Ожидалось 9 шин рамки, найдено {classified}')
    for entity in model.query('MTEXT'):
        label = entity.plain_text().strip()
        entity.dxf.char_height = 3.0
        entity.dxf.style = 'Standard'
        if label.startswith('+'):
            entity.dxf.layer = 'L_NET_POS'
        elif label.startswith('-') or label == 'N':
            entity.dxf.layer = 'L_NET_NEG'
        elif label == 'PE':
            entity.dxf.layer = 'L_NET_PE'
        else:
            entity.dxf.layer = 'L_NET_PHASE'
        entity.dxf.color = 256

    def pt(x: float, y: float):
        return (origin[0] + x, origin[1] + y)

    def line(x1, y1, x2, y2, layer='L_E3_THIN'):
        model.add_line(pt(x1, y1), pt(x2, y2),
                       dxfattribs={'layer': layer, 'color': 256,
                                   'lineweight': -1})

    def text(value, x, y, height, style='Standard', layer='L_E3_TEXT',
             width=0, rotation=0):
        attrs = {'layer': layer, 'insert': pt(x, y),
                 'char_height': height, 'style': style,
                 'attachment_point': 1, 'rotation': rotation,
                 'color': 256, 'lineweight': -1}
        if width:
            attrs['width'] = width
        model.add_mtext(value, dxfattribs=attrs)

    # E3 reference: 20 mm binding edge, 5 mm on the other sides.
    for a, b in [((20, 5), (415, 5)), ((415, 5), (415, 292)),
                 ((415, 292), (20, 292)), ((20, 292), (20, 5))]:
        line(*a, *b, layer='L_E3_FRAME')
    for index in range(9):
        x = 20 + index * 395 / 8
        line(x, 292, x, 297, 'L_E3_GRID')
        if index < 8:
            text(str(index + 1), x + 24.7, 297, 2.8,
                 layer='L_E3_GRID')
    for index, letter in enumerate('ABCDEF'):
        y = 292 - (index + .5) * 287 / 6
        line(415, 292 - index * 287 / 6,
             420, 292 - index * 287 / 6, 'L_E3_GRID')
        text(letter, 417, y + 1.4, 2.8, layer='L_E3_GRID')

    # Narrow inscription columns and the visible ESKD-style title block.
    line(8, 5, 8, 150, 'L_E3_FRAME')
    for y in (5, 35, 65, 95, 125, 150):
        line(8, y, 20, y)
    for value, y in [('Инв. № подл.', 12), ('Подп. и дата', 43),
                     ('Взам. инв. №', 73), ('Инв. № дубл.', 103),
                     ('Подп. и дата', 132)]:
        text(value, 10.5, y, 2.5, rotation=90)
    for x1, y1, x2, y2 in (
        (230, 5, 415, 5), (230, 60, 415, 60),
        (230, 5, 230, 60), (415, 5, 415, 60),
        (230, 20, 415, 20), (295, 5, 295, 60),
        (295, 42, 415, 42), (382, 20, 382, 42),
    ):
        line(x1, y1, x2, y2)
    for x in (240, 255, 275, 285):
        line(x, 5, x, 20)
    for y in (10, 15):
        line(230, y, 295, y)
    for value, x in [('Изм.', 231), ('Лист', 241),
                     ('№ докум.', 256), ('Подп.', 276), ('Дата', 286)]:
        text(value, x, 19, 2.5, style='GOST_A')
    text('#DOC_NO', 302, 58, 7.0, style='GOST_A')
    text('ЧЕРНОВИК', 384, 58, 3.5, style='GOST_A')
    text('Схема электрическая принципиальная', 300, 36, 4.0,
         style='GOST_A', width=78)
    text('Лист 1', 386, 30, 3.5, style='GOST_A')
    text('#ОРГАНИЗАЦИЯ', 310, 16, 5.0, style='GOST_A')
    _available_fonts(doc)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    _read(output)


def build_a3_proof(template: Path, frame: Path, output: Path,
                   origin: tuple[float, float]) -> None:
    """Build a sheet at exact A3 model units with a 1:1 paper layout."""
    source = _read(template)
    doc = _read(frame)
    xref.load_modelspace(source, doc)
    for entity in doc.modelspace():
        entity.transform(Matrix44.translate(-origin[0], -origin[1], 0))
    for layer in list(doc.layers):
        if '$0$L_' in layer.dxf.name:
            layer.rename(layer.dxf.name.rsplit('$0$', 1)[-1])
    for entity in doc.modelspace().query('MTEXT'):
        if entity.dxf.layer != 'L_LABELS' and entity.dxf.char_height < 2.5:
            entity.dxf.char_height = 2.5
            entity.dxf.style = 'GOST_A'
        entity.text = re.sub(r'\\C\d+;', '', entity.text)
    for entity in doc.modelspace():
        if entity.dxf.layer != 'L_PART_BOUNDARY':
            entity.dxf.color = 256
    _available_fonts(doc)
    doc.header['$INSUNITS'] = 4
    doc.header['$LWDISPLAY'] = 1
    paper = doc.layouts.new('A3-1to1')
    paper.page_setup(size=(420, 297), margins=(0, 0, 0, 0),
                     units='mm', scale=(1, 1), name='ISO A3')
    viewport = paper.add_viewport(center=(210, 148.5), size=(420, 297),
                                  view_center_point=(210, 148.5),
                                  view_height=297)
    viewport.dxf.layer = 'Defpoints'
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    checked = _read(output)
    if checked.layers.get('L_POINTS').dxf.plot != 1:
        raise ValueError('Точки должны печататься')
    if checked.layers.get('L_PART_BOUNDARY').dxf.plot != 0:
        raise ValueError('Границы частей не должны печататься')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--ugo', type=Path, required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8'))['electrical']
    origin = tuple(profile['origin'])
    if hashlib.sha256(args.frame.read_bytes()).hexdigest() != profile['sha256']:
        raise ValueError('Хеш исходной рамки не совпал с профилем')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    template_out = args.output_dir / 'im1-011-etalon.dxf'
    ugo_out = args.output_dir / 'IEC — Чистые УГО.dxf'
    frame_out = args.output_dir / 'sheet-1-1-E3-style.dxf'
    proof_out = args.output_dir / 'im1-011-A3-print-proof.dxf'
    report = {'template': fit_template(args.template, template_out, origin),
              'ugo': normalize_ugo(args.ugo, ugo_out)}
    build_e3_frame(args.frame, frame_out, origin)
    build_a3_proof(template_out, frame_out, proof_out, origin)
    report['frame'] = {'sourceSha256': profile['sha256'],
                       'styledSha256': hashlib.sha256(frame_out.read_bytes()).hexdigest(),
                       'format': 'A3', 'reference': 'user supplied E3 PDF'}
    (args.output_dir / 'print-style-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
