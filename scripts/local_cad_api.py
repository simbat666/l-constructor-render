#!/usr/bin/env python3
"""HTTP bridge from L Constructor to the DXF assembler.

The service runs only the audited DXF assembler built on ezdxf. It has no
AutoCAD, no ODA dependency, and no dependency on the original Excel workbook.
Bind address and browser origins are environment-controlled so the same process
runs on localhost for MVP work and behind a production reverse proxy.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from threading import BoundedSemaphore
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER = ROOT / "scripts" / "assemble_dxf_package.py"
LIBRARY = ROOT / "data" / "scheme-library.json"
OUTPUT = ROOT / "output" / "generated"
MAX_BODY = 1_000_000
SAFE_FILE = re.compile(r"^cabinet-[a-f0-9]{32}(?:-(?:electrical|external))?\.(?:dxf|json|zip)$")
GENERATION_SLOTS = BoundedSemaphore(2)
HOST = os.environ.get("L_DXF_BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("L_DXF_PORT", "3001"))
ALLOWED_ORIGINS = {item.strip().rstrip("/") for item in os.environ.get("L_DXF_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3003").split(",") if item.strip()}


def known_codes() -> set[str]:
    library = json.loads(LIBRARY.read_text(encoding="utf-8"))
    return {str(item["code"]).casefold() for item in library["sources"] if item["status"] == "found"}


def drawing_kinds():
    library = json.loads(LIBRARY.read_text(encoding='utf-8'))
    result = {}
    for record in library['sources']:
        kinds = {'electrical' if use['field'] == 'diagram1' else 'external' for use in record['uses'] if use['field'] in ('diagram1', 'diagram2')}
        if len(kinds) != 1:
            raise ValueError(f"Неоднозначное назначение шаблона {record['code']}")
        result[record['code'].casefold()] = kinds.pop()
    return result


def allowed_origin(handler: BaseHTTPRequestHandler) -> str | None:
    origin = handler.headers.get("Origin", "").rstrip("/")
    return origin if origin in ALLOWED_ORIGINS else None


def cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = allowed_origin(handler)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")


def json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, object]) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    cors_headers(handler)
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(payload)


def validate_instances(payload):
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект")
    instances = payload.get("instances")
    if instances is None:
        codes = payload.get("codes")
        if not isinstance(codes, list):
            raise ValueError("Нужен массив instances")
        instances = [{"id": str(index), "tag": "", "code": code} for index, code in enumerate(codes)]
    if not isinstance(instances, list) or not 1 <= len(instances) <= 1000:
        raise ValueError("Допустимо от 1 до 1000 фрагментов DXF")
    known = known_codes()
    kinds = drawing_kinds()
    seen = set()
    result = []
    for item in instances:
        if not isinstance(item, dict):
            raise ValueError("Экземпляр должен быть объектом")
        identity, code, tag = item.get("id"), item.get("code"), item.get("tag", "")
        if not isinstance(identity, str) or not 1 <= len(identity) <= 160 or identity in seen:
            raise ValueError("Каждому экземпляру нужен уникальный id (1…160 символов)")
        if not isinstance(code, str) or code.strip().casefold() not in known:
            raise ValueError("Неизвестный код схемы")
        if not isinstance(tag, str) or len(tag) > 60 or any(ord(char) < 32 for char in tag):
            raise ValueError("Некорректное обозначение блока")
        seen.add(identity)
        code = code.strip().casefold()
        kind = kinds[code]
        if item.get('drawingKind', kind) != kind:
            raise ValueError(f"Неверный вид документа для {code}: нужен {kind}")
        result.append({**item, "code": code, "tag": tag, 'drawingKind': kind})
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print(format % args, flush=True)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        cors_headers(self)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/generate":
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            instances = validate_instances(payload)
            normalized = [item["code"] for item in instances]
            filename = f"cabinet-{uuid.uuid4().hex}.zip"
            OUTPUT.mkdir(parents=True, exist_ok=True)
            target = OUTPUT / filename
            manifest = target.with_suffix(".json")
            manifest.write_text(json.dumps({"schemaVersion": 2, "status": "draft", "instances": instances, "ruleFingerprint": payload.get("ruleFingerprint"), "notice": "Separate electrical and external drawing sets. Template composition, not released electrical documentation."}, ensure_ascii=False, indent=2), encoding="utf-8")
            if not GENERATION_SLOTS.acquire(blocking=False):
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Генератор занят. Повтори запрос через несколько секунд."})
                return
            try:
                result = subprocess.run(
                [sys.executable, str(ASSEMBLER), "--output", str(target), "--manifest", str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=90,
                )
            finally:
                GENERATION_SLOTS.release()
            if result.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
                raise RuntimeError(result.stdout.strip() or "DXF assembly failed")
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "codes": normalized,
                    "file": filename,
                    "downloadPath": f"/files/{filename}",
                    "drawings": [{"kind": kind, "title": title, "downloadPath": f"/files/{target.stem}-{kind}.dxf"} for kind, title in [('electrical', 'Принципиальная электрическая схема'), ('external', 'Схема внешних соединений')] if any(item['drawingKind'] == kind for item in instances)],
                    "manifestPath": f"/files/{manifest.name}",
                    "status": "draft",
                    "message": result.stdout.strip(),
                },
            )
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            json_response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
        except Exception:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Local DXF service failed; see terminal log."})

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/healthz":
            json_response(self, HTTPStatus.OK, {"status": "ok", "service": "l-dxf-api"})
            return
        prefix = "/files/"
        path = urlparse(self.path).path
        if not path.startswith(prefix):
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return
        filename = unquote(path[len(prefix):])
        if not SAFE_FILE.fullmatch(filename):
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid filename"})
            return
        target = OUTPUT / filename
        if not target.is_file():
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "Generated DXF not found"})
            return
        contents = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", {'.dxf': 'application/dxf', '.zip': 'application/zip', '.json': 'application/json; charset=utf-8'}[target.suffix])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(contents)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        cors_headers(self)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(contents)

    def do_HEAD(self) -> None:
        self.do_GET()


if __name__ == "__main__":
    print(f"L Constructor DXF API: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
