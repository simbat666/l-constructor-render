#!/usr/bin/env python3
"""Build a traceable CAD-source registry from the supplied scheme archive.

The workbook selects `diagram1` and `diagram2` codes. This script proves that
each selected code has a source DWG before any CAD conversion or assembly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = Path("/Users/dmitrijmitin/Downloads/Схемы.zip")
RULES = ROOT / "data" / "motor-v2.json"
ALIASES = ROOT / "data" / "scheme-aliases.json"
OUTPUT = ROOT / "data" / "scheme-library.json"
SOURCES_DIRECTORY = ROOT / "data" / "cad-sources"


def main() -> None:
    if not ARCHIVE.is_file():
        raise FileNotFoundError(f"Scheme archive is missing: {ARCHIVE}")

    rules = json.loads(RULES.read_text(encoding="utf-8"))
    aliases = json.loads(ALIASES.read_text(encoding="utf-8"))
    requested_by: dict[str, list[dict[str, object]]] = {}
    for sheet_name, rows in (("diagram 1", rules["diagrams1"]), ("diagram 2", rules["diagrams2"])):
        for row in rows:
            for field in ("diagram1", "diagram2"):
                code = row.get(field)
                if not code:
                    continue
                requested_by.setdefault(code.casefold(), []).append(
                    {"sheet": sheet_name, "sourceRow": row["sourceRow"], "field": field}
                )

    with ZipFile(ARCHIVE) as archive:
        dwg_files = {
            Path(item.filename).stem.casefold(): item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.casefold().endswith(".dwg")
        }
        sources = []
        for code, uses in sorted(requested_by.items()):
            alias = aliases.get(code)
            archive_code = (alias or {}).get("archiveCode", code).casefold()
            item = dwg_files.get(archive_code)
            if item is None:
                sources.append(
                    {"code": code, "status": "missing", "archiveCode": archive_code, "uses": uses}
                )
                continue
            content = archive.read(item)
            local_file = f"{code}.dwg"
            SOURCES_DIRECTORY.mkdir(parents=True, exist_ok=True)
            (SOURCES_DIRECTORY / local_file).write_bytes(content)
            sources.append(
                {
                    "code": code,
                    "status": "found",
                    "archiveFile": item.filename,
                    "localFile": f"cad-sources/{local_file}",
                    "alias": alias,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "uses": uses,
                }
            )

        used_codes = {
            (aliases.get(code) or {}).get("archiveCode", code).casefold()
            for code in requested_by
        }
        unreferenced = sorted(
            {Path(item.filename).stem for item in dwg_files.values()} - used_codes
        )

    missing = [item["code"] for item in sources if item["status"] == "missing"]
    result = {
        "sourceArchive": ARCHIVE.name,
        "sourceCount": len(dwg_files),
        "aliasCount": len(aliases),
        "requiredCodeCount": len(sources),
        "foundCodeCount": len(sources) - len(missing),
        "missingCodes": missing,
        "unreferencedDwgCodes": unreferenced,
        "sources": sources,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['foundCodeCount']}/{result['requiredCodeCount']} required codes found")
    if missing:
        print("Missing: " + ", ".join(missing))


if __name__ == "__main__":
    main()
