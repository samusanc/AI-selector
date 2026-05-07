#!/usr/bin/env python3
"""
server.py — Refactored with Pydantic v2
========================================
What changed vs the original:
  - The raw JSON parsing + manual key-checking is replaced by Pydantic models.
  - Pydantic validates incoming payloads at the "front door" and rejects bad ones
    before any dangerous logic (like subprocess.run) ever runs.
  - We use a "Discriminated Union" so Pydantic can automatically tell apart a
    write_file request from a shell request by looking at one field.

Learning goals:
  1. Understand BaseModel and field declarations.
  2. Understand Literal[] for fixed-value fields.
  3. Understand Union / Annotated discriminated unions.
  4. Understand model_validate_json() and ValidationError.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Annotated, Literal, Union

# ── Pydantic imports ──────────────────────────────────────────────────────────
# BaseModel  : The base class every Pydantic model inherits from.
# Field      : Lets you attach extra metadata (default values, descriptions…).
# ValidationError : Raised when incoming data doesn't match our model.
from pydantic import BaseModel, Field, ValidationError


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Define the "Atomic" models (the individual shapes)
# ══════════════════════════════════════════════════════════════════════════════

class WriteFileRequest(BaseModel):
    """
    Represents a request to write a file to disk.

    The key learning here is `Literal["write_file"]`.
    Literal means: "this field can ONLY ever be the exact string 'write_file'".
    If the incoming JSON has `"action": "delete_file"`, Pydantic rejects it.
    This is what makes discrimination possible (see STEP 3).
    """

    # Literal["write_file"] is the "identity badge" of this model.
    # Any payload with `"action": "write_file"` will be routed here.
    action: Literal["write_file"]

    # Path and content are plain required strings.
    # If either is missing, Pydantic raises ValidationError automatically —
    # no need for `if not path: return error` like the original code.
    path: str
    content: str


class ShellCommandRequest(BaseModel):
    """
    Represents a request to run a shell command.

    Notice `timeout` has a DEFAULT VALUE of 30.
    Pydantic fills it in automatically if the caller omits it.
    Original code: `timeout = data.get("timeout", 30)`  — same idea, but automatic.
    """

    # This model's identity badge: the field "action" must be "shell".
    # (Original code used "command" as the key — we keep "command" for the
    # actual command text, but add "action" so discrimination works cleanly.)
    action: Literal["shell"]

    command: str

    # Field(default=30) is equivalent to writing `= 30`, but also lets you
    # attach a human-readable description, min/max bounds, etc.
    timeout: int = Field(default=30, description="Max seconds before the command is killed")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — The "Master Union" (the front door)
# ══════════════════════════════════════════════════════════════════════════════
#
# `Union[WriteFileRequest, ShellCommandRequest]` means:
#   "This value is EITHER a WriteFileRequest OR a ShellCommandRequest."
#
# The `discriminator="action"` part tells Pydantic:
#   "Look at the 'action' field first. If it's 'write_file', use WriteFileRequest.
#    If it's 'shell', use ShellCommandRequest. If it's neither, raise an error."
#
# Without a discriminator, Pydantic would try each model in order (slow).
# With one, it jumps straight to the right model (fast and clear errors).
#
# `Annotated[..., Field(discriminator="action")]` is Pydantic v2's syntax for
# attaching metadata to a type hint. Think of it as: type + extra instructions.

CommandRequest = Annotated[
    Union[WriteFileRequest, ShellCommandRequest],
    Field(discriminator="action"),
]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — The HTTP handler (now lean and clean)
# ══════════════════════════════════════════════════════════════════════════════

class CommandHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        """
        Handles every POST request.

        Old approach: parse JSON manually, check keys manually, validate manually.
        New approach: hand the raw bytes to Pydantic and let it do all of that.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)

            # ── Pydantic magic happens here ───────────────────────────────────
            # `model_validate_json` does THREE things at once:
            #   1. Parses the raw JSON bytes into a Python dict.
            #   2. Looks at the "action" field to pick the right model.
            #   3. Validates every field against the model's type hints.
            #
            # If ANY of those steps fail, it raises `ValidationError`
            # (caught below) — your server never reaches the dangerous logic.
            #
            # The return value is a RICH OBJECT, not a plain dict.
            # Access fields with dots: `request.path` instead of `data["path"]`.
            try:
                request = _parse_request(raw_body)
            except ValidationError as exc:
                # exc.errors() returns a structured list of exactly what went wrong.
                # e.g. [{"loc": ["path"], "msg": "Field required", "type": "missing"}]
                self._respond(400, {
                    "status": "error",
                    "message": "Invalid request payload",
                    # We convert to a plain list so json.dumps can serialize it.
                    "details": exc.errors(),
                })
                return
            except ValueError as exc:
                # Raised if the bytes aren't valid JSON at all.
                self._respond(400, {"status": "error", "message": str(exc)})
                return

            # ── Route to the right handler ────────────────────────────────────
            # Because `action` is a Literal field, we can use a clean match.
            # Python knows `request` is either WriteFileRequest or ShellCommandRequest,
            # so your editor can autocomplete `.path`, `.command`, etc.
            match request.action:
                case "write_file":
                    result = _write_file(request)   # receives a WriteFileRequest
                case "shell":
                    result = _shell(request)         # receives a ShellCommandRequest

            self._respond(200, result)

        except Exception as exc:
            # Catch-all safety net for unexpected server errors.
            self._respond(500, {"status": "error", "message": f"Server error: {exc}"})

    def do_GET(self):
        self._respond(200, {
            "status": "running",
            "server": "JSON Command Server (Pydantic edition)",
            "version": "2.0",
        })

    def _respond(self, code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {format % args}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Pure functions that operate on validated objects
# ══════════════════════════════════════════════════════════════════════════════
# Notice these functions receive TYPED objects, not raw dicts.
# Your editor knows exactly what fields exist — autocomplete works perfectly.

def _parse_request(raw_body: bytes) -> WriteFileRequest | ShellCommandRequest:
    """
    Parses raw JSON bytes into a validated Pydantic model.

    We wrap this in its own function so the handler stays readable.
    Raises:
        json.JSONDecodeError → if bytes aren't valid JSON.
        ValidationError      → if JSON doesn't match either model.
    """
    # model_validate_json is smarter than json.loads + model_validate:
    # it handles the two-step in one call and gives better error locations.
    #
    # We use `WriteFileRequest` as a dummy entry point only to get the type
    # annotation right — but the ACTUAL routing is done by the discriminator
    # defined in `CommandRequest`.  We validate against the Union type.
    #
    # Pydantic v2 trick: to validate against a Union type alias, use
    # TypeAdapter — a lightweight wrapper around any type annotation.
    from pydantic import TypeAdapter
    adapter = TypeAdapter(CommandRequest)   # build once, validate many times
    return adapter.validate_json(raw_body)


def _write_file(req: WriteFileRequest) -> dict:
    """
    Writes a file to disk.
    `req` is already validated — no need to check if path/content exist.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(req.path)), exist_ok=True)
        with open(req.path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {
            "status": "success",
            "action": "write_file",
            "path": req.path,                           # dot access, not ["path"]
            "bytes_written": len(req.content.encode("utf-8")),
            "timestamp": datetime.now().isoformat(),
        }
    except OSError as exc:
        return {"status": "error", "message": str(exc)}


def _shell(req: ShellCommandRequest) -> dict:
    """
    Runs a shell command.
    `req.timeout` is guaranteed to be an int (Pydantic enforced it).
    """
    try:
        result = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=req.timeout,    # already validated as int, default already applied
        )
        return {
            "status": "success",
            "command": req.command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timestamp": datetime.now().isoformat(),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"Command timed out after {req.timeout}s",
            "command": req.command,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "command": req.command}


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_server(host: str = "localhost", port: int = 7777):
    httpd = HTTPServer((host, port), CommandHandler)
    print(f"✓ Command server listening on {host}:{port}")
    print("  Press Ctrl+C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        httpd.server_close()
        sys.exit(0)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
    run_server(host, port)
