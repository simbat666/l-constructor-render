#!/usr/bin/env python3
"""Read the supplied motor OL workbook and make a read-only JSON snapshot for L."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook


SOURCE = Path("/Users/dmitrijmitin/Downloads/ОЛ - Электродвигатель асинхронный_В2.xlsx")
TARGET = Path(__file__).resolve().parents[1] / "data" / "motor-v2.json"


def value(cell):
    if cell is None:
        return None
    if isinstance(cell, float) and cell.is_integer():
        return int(cell)
    return cell


def num(cell):
    return float(cell) if cell is not None else None


def rows(ws):
    return list(ws.iter_rows(values_only=True))


def io(row, offset=11):
    labels = ["di0", "di24", "doTransistor", "doRelay", "aiPt1000", "ai420", "ai010", "ao420", "ao010"]
    return {label: value(row[offset + index]) or 0 for index, label in enumerate(labels)}


def main():
    book = load_workbook(SOURCE, read_only=True, data_only=True)

    index1 = []
    for excel_row, row in enumerate(rows(book["Load index 1"])[1:], start=2):
        if row[8] is None:
            continue
        index1.append({
            "sourceRow": excel_row, "voltageCode": value(row[0]), "voltage": value(row[1]),
            "startCode": value(row[2]), "start": value(row[3]), "tripCode": value(row[4]),
            "trip": value(row[5]), "currentFrom": num(row[6]), "currentTo": num(row[7]),
            "loadIndex": value(row[8]), "linesAmount": value(row[9]),
        })

    index2 = []
    for excel_row, row in enumerate(rows(book["Load index 2"])[1:], start=2):
        if row[7] is None:
            continue
        index2.append({
            "sourceRow": excel_row, "selectionCode": value(row[0]), "selection": value(row[1]),
            "voltageCode": value(row[1]), "voltage": value(row[2]), "startCode": value(row[3]),
            "start": value(row[4]), "currentFrom": num(row[5]), "currentTo": num(row[6]),
            "loadIndex": value(row[7]), "linesAmount": value(row[8]),
        })

    diagrams1 = []
    for excel_row, row in enumerate(rows(book["diagram 1"])[1:], start=2):
        if row[9] is None:
            continue
        diagrams1.append({
            "sourceRow": excel_row, "startCode": value(row[0]), "start": value(row[1]),
            "locationCode": value(row[2]), "location": value(row[3]),
            "voltageCode": value(row[4]), "voltage": value(row[5]),
            "tripCode": value(row[6]), "trip": value(row[7]), "controller": value(row[8]),
            "diagram1": value(row[9]), "diagram2": value(row[10]), "io": io(row),
            "linesAmount": value(row[20]), "consumptionCurrent": value(row[21]), "placement": value(row[22]),
        })

    diagrams2 = []
    for excel_row, row in enumerate(rows(book["diagram 2"])[1:], start=2):
        if row[9] is None:
            continue
        diagrams2.append({
            "sourceRow": excel_row, "thermalCode": value(row[0]), "thermal": value(row[1]),
            "ptcCode": value(row[2]), "ptc": value(row[3]),
            "windingWireCode": value(row[4]), "windingWire": value(row[5]),
            "bearingWireCode": value(row[6]), "bearingWire": value(row[7]),
            "controller": value(row[8]), "diagram1": value(row[9]), "diagram2": value(row[10]),
            "io": io(row), "linesAmount": value(row[20]), "consumptionCurrent": value(row[21]),
            "placement": value(row[22]),
        })

    sp1 = []
    for excel_row, row in enumerate(rows(book["sp1"])[1:], start=2):
        if row[2] is None:
            continue
        sp1.append({"sourceRow": excel_row, "selectionCode": value(row[0]), "selection": value(row[1]), "model": value(row[2]), "loadIndex": value(row[3]), "name": value(row[4]), "quantity": value(row[5]), "unit": value(row[6]), "lde": value(row[7])})

    sp2 = []
    for excel_row, row in enumerate(rows(book["sp2"])[1:], start=2):
        if row[0] is None:
            continue
        sp2.append({"sourceRow": excel_row, "diagram1": value(row[0]), "name": value(row[1]), "quantity": value(row[2]), "unit": value(row[3]), "lde": value(row[4])})

    sp3 = []
    for excel_row, row in enumerate(rows(book["sp3"])[1:], start=2):
        if row[0] is None:
            continue
        sp3.append({"sourceRow": excel_row, "diagram1": value(row[0]), "loadIndex": value(row[1]), "name": value(row[2]), "quantity": value(row[3]), "unit": value(row[4]), "lde": value(row[5])})

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps({
        "source": {"file": SOURCE.name, "revision": "В2", "sheets": ["Load index 1", "Load index 2", "diagram 1", "diagram 2", "sp1", "sp2", "sp3"]},
        "loadIndex1": index1, "loadIndex2": index2, "diagrams1": diagrams1, "diagrams2": diagrams2,
        "sp1": sp1, "sp2": sp2, "sp3": sp3,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {TARGET} | {len(index1)} + {len(index2)} load-index rows | {len(diagrams1)} + {len(diagrams2)} diagram rows")


if __name__ == "__main__":
    main()
