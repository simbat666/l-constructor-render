#!/usr/bin/env python3
"""Offline QElectroTech .elmt collection to one AutoCAD block-library DXF.

The DXF is an interchange artifact. Convert it to DWG with AutoCAD SAVEAS.
Each source element becomes one named BLOCK; the JSON index keeps source paths,
names, QET terminals, and conversion warnings. No electrical connectivity is
claimed for the AutoCAD geometry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import ezdxf

POWER_CONTROL_CATEGORIES = (
    "110_network_supplies", "114_connections", "120_cables_wiring",
    "130_terminals_terminal_strips", "140_connectors_plugs",
    "200_fuses_protective_gears", "310_relays_contactors_contacts",
    "330_transformers_power_supplies", "340_converters_inverters",
    "380_signaling_operating", "390_sensors_instruments",
    "391_consumers_actuators",
)


def selected_files(source: Path, profile: str) -> list[Path]:
    if profile == "all":
        return sorted(source.rglob("*.elmt"))
    if profile == "power-control":
        base = source / "10_electric" / "10_allpole"
        return sorted(file for category in POWER_CONTROL_CATEGORIES
                      for file in (base / category).rglob("*.elmt")
                      if "/60_connectors_electronics/" not in file.as_posix())
    raise ValueError(f"Unknown QET profile: {profile}")


def number(value: str | None, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def point(x: float, y: float) -> tuple[float, float]:
    return x, -y  # Qt screen coordinates have downward-positive Y.


def style(element: ET.Element) -> dict:
    value = element.get("style", "")
    if "line-style:none" in value or "line-style:dashed" in value and element.tag == "arc":
        return {"layer": "QET_HIDDEN"} if "line-style:none" in value else {"layer": "QET_GEOMETRY", "linetype": "DASHED"}
    return {"layer": "QET_GEOMETRY", **({"linetype": "DASHED"} if "line-style:dashed" in value else {})}


def polygon_vertices(element: ET.Element) -> list[tuple[float, float]]:
    coords = []
    for key, value in element.attrib.items():
        match = re.fullmatch(r"x(\d+)", key)
        if match and f"y{match.group(1)}" in element.attrib:
            coords.append((int(match.group(1)), point(number(value), number(element.get(f"y{match.group(1)}")))))
    return [value for _, value in sorted(coords)]


def ellipse_points(element: ET.Element, start: float = 0, sweep: float = 360) -> list[tuple[float, float]]:
    x, y = number(element.get("x")), number(element.get("y"))
    w, h = number(element.get("width")), number(element.get("height"))
    count = max(8, math.ceil(abs(sweep) / 5))
    return [point(x + w / 2 + w / 2 * math.cos(math.radians(start + sweep * i / count)),
                  y + h / 2 - h / 2 * math.sin(math.radians(start + sweep * i / count)))
            for i in range(count + 1)]


def add_shape(block, element: ET.Element, warnings: set[str]) -> None:
    tag = element.tag
    attrs = style(element)
    if attrs["layer"] == "QET_HIDDEN":
        return
    if tag == "line":
        block.add_line(point(number(element.get("x1")), number(element.get("y1"))),
                       point(number(element.get("x2")), number(element.get("y2"))), dxfattribs=attrs)
    elif tag == "polygon":
        vertices = polygon_vertices(element)
        if len(vertices) >= 2:
            block.add_lwpolyline(vertices, close=element.get("closed") == "true", dxfattribs=attrs)
    elif tag == "rect":
        x, y = number(element.get("x")), number(element.get("y"))
        w, h = number(element.get("width")), number(element.get("height"))
        block.add_lwpolyline([point(x, y), point(x + w, y), point(x + w, y + h), point(x, y + h)],
                             close=True, dxfattribs=attrs)
        if number(element.get("rx")) or number(element.get("ry")):
            warnings.add("rounded rectangle corners simplified")
    elif tag == "circle":
        diameter = number(element.get("diameter"))
        block.add_circle(point(number(element.get("x")) + diameter / 2,
                               number(element.get("y")) + diameter / 2), diameter / 2, dxfattribs=attrs)
    elif tag == "ellipse":
        block.add_lwpolyline(ellipse_points(element), close=True, dxfattribs=attrs)
    elif tag == "arc":
        block.add_lwpolyline(ellipse_points(element, number(element.get("start")),
                                           number(element.get("angle"))), dxfattribs=attrs)
    elif tag in {"text", "dynamic_text", "input"}:
        content = element.get("text", "") if tag != "dynamic_text" else element.findtext("text", "")
        if content:
            font = element.get("font", "")
            size = number(font.split(",")[1] if len(font.split(",")) > 1 else element.get("size"), 7)
            block.add_text(content.replace("\n", " "), dxfattribs={"layer": "QET_TEXT",
                           "height": max(1, size), "rotation": -number(element.get("rotation")),
                           "insert": point(number(element.get("x")), number(element.get("y")))})
        if tag != "text":
            warnings.add("dynamic text exported as static sample text")
    elif tag != "terminal":
        warnings.add(f"unsupported element: {tag}")
    if tag in {"rect", "circle", "ellipse", "polygon", "arc"} and "filling:none" not in element.get("style", ""):
        warnings.add("filled shape exported as outline")


def convert(source: Path, destination: Path, profile: str = "all") -> dict:
    files = selected_files(source, profile)
    if not files:
        raise ValueError(f"No .elmt files below {source}")
    destination.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 4  # millimetres; QET display units still require scale verification.
    for layer in ("QET_GEOMETRY", "QET_TEXT", "QET_HIDDEN"):
        doc.layers.new(layer)
    rows = []
    for file in files:
        relative = file.relative_to(source).as_posix()
        root = ET.parse(file).getroot()
        description = root.find("description")
        if description is None:
            continue
        name = "QET_" + hashlib.sha256(relative.encode()).hexdigest()[:16].upper()
        block = doc.blocks.new(name, base_point=(0, 0))
        warnings: set[str] = set()
        terminals = []
        for element in description:
            if element.tag == "terminal":
                terminals.append({"name": element.get("name", ""), "x": number(element.get("x")),
                                  "y": -number(element.get("y")), "orientation": element.get("orientation", "")})
            else:
                add_shape(block, element, warnings)
        if len(block) == 0:
            warnings.add("no visible geometry")
        names = {e.get("lang", ""): e.text or "" for e in root.findall("./names/name")}
        rows.append({"block": name, "source": relative, "sourceSha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                     "name": names.get("ru") or names.get("en") or next(iter(names.values()), file.stem),
                     "terminals": terminals, "warnings": sorted(warnings)})
    # The model-space atlas makes every imported block visibly browsable in AutoCAD.
    model = doc.modelspace()
    for i, row in enumerate(rows):
        x, y = (i % 20) * 180, -(i // 20) * 140
        model.add_blockref(row["block"], (x, y))
        model.add_text(row["source"].split("/")[-1][:28], dxfattribs={"height": 4, "insert": (x, y - 65), "layer": "QET_TEXT"})
    target = destination / "qet-symbol-library.dxf"
    doc.saveas(target)
    reopened = ezdxf.readfile(target)
    audit = reopened.audit()
    if audit.has_errors or len(rows) != sum(1 for b in reopened.blocks if b.name.startswith("QET_")):
        raise RuntimeError(f"DXF audit failed: {len(audit.errors)} errors or block count mismatch")
    index = destination / "qet-symbol-library.json"
    index.write_text(json.dumps({"source": str(source), "profile": profile, "count": len(rows), "symbols": rows}, ensure_ascii=False,
                                indent=2) + "\n", encoding="utf-8")
    catalog = destination / "qet-symbol-catalog.csv"
    with catalog.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["block", "name", "source", "terminals", "warnings"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"block": row["block"], "name": row["name"], "source": row["source"],
                             "terminals": len(row["terminals"]), "warnings": "; ".join(row["warnings"])})
    return {"count": len(rows), "dxf": str(target), "index": str(index), "catalog": str(catalog),
            "auditErrors": len(audit.errors)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="unpacked qelectrotech-elements collection")
    parser.add_argument("destination", type=Path, help="new output directory")
    parser.add_argument("--profile", choices=("all", "power-control"), default="all")
    args = parser.parse_args()
    print(json.dumps(convert(args.source, args.destination, args.profile), ensure_ascii=False))


if __name__ == "__main__":
    main()
