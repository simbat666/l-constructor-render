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
        if profile and (extents.extmin.y - profile['sourceOriginY'] + profile['verticalOffset'] < profile['bottom'] or extents.extmax.y - profile['sourceOriginY'] + profile['verticalOffset'] > profile['top']):
            raise ValueError(f'Неверная вертикальная привязка {code}: нужен контракт координат шаблона')
        prepared.append((code, document, modelspace, extents))
    return prepared


def split_pages(prepared, profile=None):
    pages, current, used_width = [], [], 0.0
    available_width = profile['right'] - profile['left'] if profile else RIGHT - LEFT
    for source in prepared:
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


def assemble(pages, output: Path, instances=None, profile=None) -> None:
    target = ezdxf.new("R2018", setup=True)
    target.header["$INSUNITS"] = 4  # millimetres; source library uses mm
    target_msp = target.modelspace()
    occurrence = 0
    for page_index, page in enumerate(pages):
        page_x = page_index * (PAGE_WIDTH + PAGE_GAP)
        if profile:
            add_frame(target, profile, page_index, len(pages), page_x)
        cursor_x = page_x + (profile['left'] if profile else LEFT)
        for code, source_doc, source_msp, extents in page:
            log = transform.inplace(
                source_msp,
                Matrix44.translate(cursor_x - extents.extmin.x, -profile['sourceOriginY'] + profile['verticalOffset'] if profile else TOP - extents.extmax.y, 0),
            )
            if len(log):
                raise ValueError(f"Не все сущности {code} удалось переместить: {list(log)}")
            xref.load_modelspace(source_doc, target, conflict_policy=xref.ConflictPolicy.XREF_PREFIX)
            # An occurrence label is NOT electrical device/terminal renumbering.
            occurrence += 1
            instance = instances[occurrence - 1] if instances else {"tag": "", "id": str(occurrence)}
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
    if any(item.get('drawingKind') not in profiles for item in instances):
        raise ValueError('Не задан вид документа для экземпляра')
    files = []
    for kind, profile in profiles.items():
        selected_instances = [item for item in instances if item['drawingKind'] == kind]
        if not selected_instances:
            continue
        selected = load_selected(library, sources, [item['code'] for item in selected_instances])
        pages = split_pages(load_and_validate(selected, profile), profile)
        path = output.with_name(f'{output.stem}-{kind}.dxf')
        assemble(pages, path, selected_instances, profile)
        files.append(path)
        print(f"{profile['title']}: {len(selected_instances)} фрагментов, {len(pages)} листов; рамка {profile['frame']}", flush=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
        archive.write(manifest, 'manifest.json')
    print('Комплект DRAFT: типы схем разделены. Аудит ezdxf пройден; соединения и нумерация ещё требуют инженерной проверки.')


if __name__ == "__main__":
    main()
