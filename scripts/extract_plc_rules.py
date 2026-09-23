#!/usr/bin/env python3
"""Build a versioned PLC pin catalog from the supplied engineering workbooks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'data' / 'rules-source'
COMPOSITION = SOURCES / 'Состав ПЛК.xlsx'
MARKING = SOURCES / 'Правило_обозначения и маркировки элемкнтов на схеме_Э3_В2.xlsx'
TARGET = ROOT / 'data' / 'plc-catalog.json'


def records(sheet):
    rows = list(sheet.iter_rows(values_only=True))
    columns = {str(name).strip(): index for index, name in enumerate(rows[0]) if name is not None}
    return columns, rows[1:]


def main():
    composition = load_workbook(COMPOSITION, read_only=True, data_only=True)
    marking = load_workbook(MARKING, read_only=True, data_only=True)
    base_columns, base_rows = records(composition['base_controller'])
    required = {'controller_Manufacturer (Brand)', 'controller_Type', 'general signal amount', 'max_moduls'}
    if not required <= set(base_columns) or len(base_rows) != 1:
        raise ValueError('Ожидается одна описанная базовая модель ПЛК')
    base = base_rows[0]
    brand = base[base_columns['controller_Manufacturer (Brand)']]
    model = base[base_columns['controller_Type']]
    code_columns = [name for name in base_columns if name not in required | {'consumption_current'}]
    limits = {code: int(base[base_columns[code]]) for code in code_columns}
    key_columns, key_rows = records(marking['Ключи зажимов ПЛК'])
    needed = {'controller_Manufacturer (Brand)', 'controller_Type', 'signal code', 'entrance number 1',
              'entrance number 2', 'entrance number 3', 'entrance number 4', 'group',
              'Ключ на схеме Э3', 'Ключ для общего GND на схеме Э3'}
    if not needed <= set(key_columns):
        raise ValueError('Не хватает колонок в таблице ключей зажимов ПЛК')
    options = []
    for source_row, row in enumerate(key_rows, start=2):
        if row[key_columns['controller_Manufacturer (Brand)']] != brand or row[key_columns['controller_Type']] != model:
            continue
        code = row[key_columns['signal code']]
        if code not in limits:
            raise ValueError(f'Ключи зажимов ПЛК:{source_row}: неизвестный тип сигнала {code}')
        terminals = [str(row[key_columns[f'entrance number {index}']]) for index in range(1, 5)
                     if row[key_columns[f'entrance number {index}']] is not None]
        if not terminals:
            raise ValueError(f'Ключи зажимов ПЛК:{source_row}: нет номера вывода')
        key = row[key_columns['Ключ на схеме Э3']]
        if key != '#' + code:
            raise ValueError(f'Ключи зажимов ПЛК:{source_row}: ключ {key} не соответствует {code}')
        options.append({'sourceRow': source_row, 'code': code, 'terminals': terminals,
                        'group': row[key_columns['group']], 'key': key,
                        'commonKey': row[key_columns['Ключ для общего GND на схеме Э3']]})
    for code, limit in limits.items():
        count = len({item['terminals'][0] for item in options if item['code'] == code})
        if count != limit:
            raise ValueError(f'{code}: в составе ПЛК указано {limit}, в ключах доступно {count}')
    if len({(item['code'], item['terminals'][0]) for item in options}) != len(options):
        raise ValueError('Повторяющаяся пара тип сигнала / вывод ПЛК')
    group_columns, group_rows = records(composition['add_cond'])
    group_required = {'controller_Manufacturer (Brand)', 'controller_Type', 'group', 'signal amount', *code_columns}
    if not group_required <= set(group_columns):
        raise ValueError('Не хватает колонок в add_cond')
    seen_groups = set()
    for source_row, row in enumerate(group_rows, start=2):
        if row[group_columns['controller_Manufacturer (Brand)']] != brand or row[group_columns['controller_Type']] != model:
            continue
        group = row[group_columns['group']]
        if group in seen_groups:
            raise ValueError(f'add_cond:{source_row}: повтор группы {group}')
        seen_groups.add(group)
        listed = [item for item in options if item['group'] == group]
        count = len({item['terminals'][0] for item in listed})
        if count != row[group_columns['signal amount']]:
            raise ValueError(f'add_cond:{source_row}: группа {group} содержит {count} выводов вместо {row[group_columns["signal amount"]]}')
        for code in code_columns:
            actual = len([item for item in listed if item['code'] == code])
            expected = row[group_columns[code]] or 0
            if actual != expected:
                raise ValueError(f'add_cond:{source_row}: {code} в группе {group}: {actual} вместо {expected}')
    if {item['group'] for item in options} != seen_groups:
        raise ValueError('Ключи зажимов ПЛК содержат группу вне add_cond')
    payload = {
        'schemaVersion': 1,
        'sources': {
            'composition': {'file': COMPOSITION.name, 'sha256': hashlib.sha256(COMPOSITION.read_bytes()).hexdigest()},
            'marking': {'file': MARKING.name, 'sha256': hashlib.sha256(MARKING.read_bytes()).hexdigest()},
        },
        'controller': {'brand': brand, 'model': model, 'generalSignalAmount': base[base_columns['general signal amount']],
                       'maxModules': base[base_columns['max_moduls']], 'limits': limits},
        'pinOptions': options,
    }
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {TARGET} | {brand} {model} | {len(options)} pin options')


if __name__ == '__main__':
    main()
