#!/usr/bin/env python3
"""Create an audited DXF library from the registered DWG source blocks.

The converter intentionally uses AutoCAD Core Console for proprietary DWG
input.  Each source is checked against the SHA-256 stored in the registry;
the generated DXF is written to a separate directory and reopened with ezdxf.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import ezdxf
from ezdxf import bbox


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY = ROOT / "data" / "scheme-library.json"
DEFAULT_SOURCES = ROOT / "data" / "cad-sources"
DEFAULT_OUTPUT = ROOT / "data" / "dxf-sources"
CORE_CONSOLE = Path(
    "/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app/Contents/"
    "Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert registered DWG blocks to audited AutoCAD 2018 DXF")
    parser.add_argument("codes", nargs="*", help="Scheme codes to convert; omit to convert every registered source")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="Replace an existing generated DXF")
    return parser.parse_args()


def make_script(temp: Path, target: Path) -> tuple[Path, Path]:
    done = temp / "convert.done"
    script = temp / "convert.scr"
    script.write_text(
        "\n".join(
            (
                '(setvar "FILEDIA" 0)',
                '(setvar "CMDECHO" 1)',
                '(setvar "LOGFILEMODE" 1)',
                '(setvar "EXPERT" 5)',
                "_.AUDIT",
                "_N",
                "_.DXFOUT",
                str(target),
                "_Version",
                "_2018",
                "16",
                f'(setq f (open "{done}" "w"))',
                '(write-line "CONVERT_OK" f)',
                "(close f)",
                "_.QUIT",
                "_Y",
                "",
                "",
            )
        ),
        encoding="utf-8",
    )
    return script, done


def validate_dxf(path: Path) -> dict[str, object]:
    document = ezdxf.readfile(path)
    audit = document.audit()
    if audit.has_errors:
        messages = "; ".join(error.message for error in audit.errors)
        raise RuntimeError(f"DXF audit failed: {messages}")
    modelspace = document.modelspace()
    extents = bbox.extents(modelspace, fast=True)
    if not extents.has_data:
        raise RuntimeError("DXF contains no model-space geometry")
    return {
        "dxfVersion": document.dxfversion,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "entityCount": len(modelspace),
        "auditErrors": len(audit.errors),
        "extents": [
            round(extents.extmin.x, 3),
            round(extents.extmin.y, 3),
            round(extents.extmax.x, 3),
            round(extents.extmax.y, 3),
        ],
    }


def resolve_records(library: dict[str, object], codes: list[str]) -> list[dict[str, object]]:
    records = {item["code"].casefold(): item for item in library["sources"]}
    requested = codes or [item["code"] for item in library["sources"]]
    selected: list[dict[str, object]] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for code in requested:
        key = code.casefold()
        if key in seen:
            raise ValueError(f"Duplicate conversion request: {code}")
        seen.add(key)
        record = records.get(key)
        if record is None or record["status"] != "found":
            unknown.append(code)
        else:
            selected.append(record)
    if unknown:
        raise ValueError("Unknown or unresolved scheme code: " + ", ".join(unknown))
    return selected


def convert_one(record: dict[str, object], sources: Path, output: Path, force: bool) -> dict[str, object]:
    code = str(record["code"])
    source = sources / Path(str(record["localFile"])).name
    target = output / f"{code}.dxf"
    if not source.is_file():
        raise FileNotFoundError(f"{code}: source DWG is missing: {source}")
    if sha256(source) != record["sha256"]:
        raise RuntimeError(f"{code}: source DWG checksum does not match the scheme registry")
    if target.exists() and not force:
        raise FileExistsError(f"{code}: target exists, use --force only after review: {target}")

    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"l-cad-{code}.") as temporary:
        temp = Path(temporary)
        script, done = make_script(temp, target)
        result = subprocess.run(
            [str(CORE_CONSOLE), "-i", str(source), "-s", str(script)],
            cwd=output,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )
        log = logs / f"{code}.log"
        log.write_text(result.stdout, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"{code}: AutoCAD Core Console returned {result.returncode}; see {log}")
        if "Total errors found 0 fixed 0" not in result.stdout:
            raise RuntimeError(f"{code}: source DWG audit is not clean; see {log}")
        if "Unknown command" in result.stdout or "; error:" in result.stdout:
            raise RuntimeError(f"{code}: AutoCAD script reported an error; see {log}")
        if not done.is_file() or not target.is_file() or target.stat().st_size < 1024:
            raise RuntimeError(f"{code}: AutoCAD did not create a usable DXF; see {log}")

    return {"code": code, "sourceSha256": record["sha256"], "output": validate_dxf(target)}


def main() -> None:
    args = parse_args()
    if not CORE_CONSOLE.is_file():
        raise SystemExit(f"AutoCAD Core Console not found: {CORE_CONSOLE}")
    library = json.loads(args.library.read_text(encoding="utf-8"))
    records = resolve_records(library, args.codes)
    report = {"converter": "AutoCAD Core Console", "targetFormat": "AutoCAD 2018 ASCII DXF", "converted": []}
    for record in records:
        converted = convert_one(record, args.sources, args.output, args.force)
        report["converted"].append(converted)
        print(f"OK {converted['code']}")
    manifest = args.output / "conversion-manifest.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Converted {len(records)} files; manifest: {manifest}")


if __name__ == "__main__":
    main()
