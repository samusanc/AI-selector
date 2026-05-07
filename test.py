#!/usr/bin/env python3
"""
test.py — Refactored with Pydantic v2
=======================================
What changed vs the original:
  - Config loading (API_KEY, PROXY_IP, PROXY_PORT) now uses BaseSettings.
    No more manual load_dotenv() + os.getenv() + string checks.
  - The AI response parser now validates the extracted JSON through Pydantic,
    so "hallucinated" fields or missing required ones are caught immediately.
  - All data is accessed via dot notation on typed objects.

Learning goals:
  1. BaseSettings for .env / environment variable loading.
  2. @field_validator for custom validation logic.
  3. model_dump() to convert a Pydantic object back to a plain dict.
  4. Seeing Pydantic errors as useful feedback rather than crashes.
"""

import sys
import json
from pathlib import Path
from typing import Literal

import requests

# ── Pydantic core ─────────────────────────────────────────────────────────────
from pydantic import BaseModel, Field, ValidationError, field_validator, TypeAdapter

# ── Pydantic Settings ─────────────────────────────────────────────────────────
# pydantic-settings is a separate package (pip install pydantic-settings).
# It extends BaseModel with the ability to read values from:
#   - Environment variables
#   - .env files
#   - Secrets files
# Install: pip install pydantic-settings
from pydantic_settings import BaseSettings


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Settings model (replaces load_config + manual .env parsing)
# ══════════════════════════════════════════════════════════════════════════════

class AppSettings(BaseSettings):
    """
    Reads configuration from environment variables or a .env file.

    How it works:
      - BaseSettings looks for environment variables whose names match the
        field names (case-insensitive by default).
      - It also reads a `.env` file automatically if you tell it to (see Config).
      - If a required variable is missing or has the wrong type, it raises a
        clear ValidationError — no more silent `None` values.

    Original code:
        api_key = os.getenv("API_KEY")
        ip      = os.getenv("PROXY_IP")
        port    = os.getenv("PROXY_PORT")
        if not all([api_key, ip, port]):
            sys.exit(1)

    Now: just instantiate AppSettings() and all of that happens automatically.
    """

    api_key: str    # Maps to env var API_KEY  (case-insensitive)
    proxy_ip: str   # Maps to PROXY_IP
    proxy_port: int # Maps to PROXY_PORT — Pydantic converts "8080" → 8080 for you

    # @field_validator runs AFTER Pydantic has parsed and type-checked a field.
    # Use it when you need logic beyond simple type checking.
    @field_validator("proxy_port")
    @classmethod
    def port_must_be_valid(cls, v: int) -> int:
        """Ensure the port number is in the valid TCP range."""
        if not (1 <= v <= 65535):
            # Raising ValueError inside a validator produces a clean Pydantic error.
            raise ValueError(f"proxy_port must be 1–65535, got {v}")
        return v

    @property
    def proxy_addr(self) -> str:
        """
        A computed property that builds the full proxy address string.
        Properties are not stored in the model — they're calculated on the fly.
        Original code: f"{ip}:{port}"
        """
        return f"{self.proxy_ip}:{self.proxy_port}"

    class model_config:
        # Tell BaseSettings to also read a `.env` file in the current directory.
        # Order of precedence: actual env vars > .env file > field defaults.
        env_file = ".env"
        env_file_encoding = "utf-8"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — AI tool-call models (validates what the AI sends back)
# ══════════════════════════════════════════════════════════════════════════════
# These mirror the models in server.py but live here too because test.py needs
# to validate the AI's response before sending it to the server.

class WriteFileCall(BaseModel):
    """
    The shape of a write_file tool call the AI might produce.

    Notice `model_config = {"extra": "ignore"}`:
    AI models sometimes hallucinate extra fields like "explanation" or "notes".
    "extra": "ignore" tells Pydantic to silently discard any unexpected fields
    rather than raising a ValidationError.  This makes parsing AI output more
    robust without being completely permissive.

    Other options:
      "extra": "forbid"  → raise error on unexpected fields (strict mode)
      "extra": "allow"   → keep unexpected fields in the model (lenient mode)
    """
    action: Literal["write_file"]
    path: str
    content: str

    model_config = {"extra": "ignore"}   # tolerate AI hallucinations gracefully


class ShellCall(BaseModel):
    """The shape of a shell tool call."""
    action: Literal["shell"]
    command: str
    timeout: int = Field(default=30)

    model_config = {"extra": "ignore"}


# The discriminated union — same pattern as server.py.
# TypeAdapter lets us validate against a Union without a wrapper model.
from typing import Annotated, Union
ToolCall = Annotated[
    Union[WriteFileCall, ShellCall],
    Field(discriminator="action"),
]
_tool_adapter = TypeAdapter(ToolCall)   # build once, reuse many times


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Helper functions (now operating on typed objects)
# ══════════════════════════════════════════════════════════════════════════════

def load_settings() -> AppSettings:
    """
    Instantiates AppSettings, which reads the .env file automatically.
    If required variables are missing, Pydantic prints a clear error and we exit.
    """
    try:
        return AppSettings()
    except ValidationError as exc:
        print("❌ Configuration error — check your .env file:")
        for err in exc.errors():
            # err["loc"]  → the field name(s) with the problem
            # err["msg"]  → human-readable description
            loc = " → ".join(str(x) for x in err["loc"])
            print(f"   {loc}: {err['msg']}")
        sys.exit(1)


def parse_agent_file(agent_name: str) -> dict:
    """Unchanged — reads key: value lines from an agent config file."""
    agent_file = Path(f".agents/{agent_name}.txt")
    if not agent_file.exists():
        print(f"Error: Agent file '{agent_file}' not found.")
        sys.exit(1)

    config = {}
    for line in agent_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            config[key.strip()] = val.strip()
    return config


def resolve_file(path_str: str) -> str:
    """Unchanged — resolves a file path and reads its content."""
    path = Path(path_str)
    if not path.exists():
        print(f"Error: Referenced file '{path}' not found.")
        sys.exit(1)
    text = path.read_text().strip()
    if text.startswith("name:"):
        return text.replace("name:", "").strip()
    return text


def load_tools(tools_dir_or_file: str) -> str:
    """Unchanged — loads tool definitions from a file or directory."""
    path = Path(tools_dir_or_file)
    if not path.exists():
        print(f"Error: Tools path '{path}' not found.")
        sys.exit(1)
    if path.is_file():
        return path.read_text().strip()
    tool_files = sorted(path.glob("*.txt"))
    return "\n\n".join(f.read_text().strip() for f in tool_files)


def build_system_prompt(agent_config: dict) -> str:
    """Unchanged — assembles the full system prompt."""
    base_prompt = resolve_file(agent_config["system-prompt"])
    tools_header_path = agent_config.get("tools")
    if not tools_header_path:
        return base_prompt
    tools_header = resolve_file(tools_header_path)
    tool_definitions = load_tools("./.tools")
    return f"{base_prompt}\n\n{tools_header}\n{tool_definitions}"


def extract_tool_call(text: str) -> WriteFileCall | ShellCall | None:
    """
    Extracts and VALIDATES a tool call from the AI's response text.

    This is the key improvement over the original `extract_command`:
    - Original: returns a raw dict — you can't trust its contents.
    - New: returns a typed Pydantic object or None — fully validated.

    Strategy:
      1. Try parsing the entire response as JSON.
      2. If that fails, scan for the first {...} block.
      3. Feed the candidate JSON to Pydantic for validation.
    """

    def try_validate(candidate: str) -> WriteFileCall | ShellCall | None:
        """Attempt to parse and validate a JSON string as a tool call."""
        try:
            # _tool_adapter.validate_json does: parse JSON → discriminate → validate
            return _tool_adapter.validate_json(candidate)
        except (ValidationError, ValueError):
            # ValidationError: parsed fine but doesn't match our models.
            # ValueError: not valid JSON.
            return None

    text = text.strip()

    # First attempt: maybe the entire response IS the JSON.
    result = try_validate(text)
    if result:
        return result

    # Second attempt: scan for the first {...} block using brace counting.
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                result = try_validate(candidate)
                if result:
                    return result
                start = None  # reset and keep scanning

    return None  # no valid tool call found


def query_agent(settings: AppSettings, model: str, system_prompt: str, user_prompt: str) -> str:
    """
    Sends the request to the AI proxy.
    Now accepts an AppSettings object instead of individual string args.
    """
    base_url = f"http://{settings.proxy_addr}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.api_key}",  # dot access, not dict lookup
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    print(f"Proxy : {base_url}")
    print(f"Model : {model}\n")

    try:
        response = requests.post(base_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as exc:
        print(f"Error connecting to proxy: {exc}")
        if hasattr(exc, "response") and exc.response is not None:
            print(f"Details: {exc.response.text}")
        sys.exit(1)


def execute_on_server(tool_call: WriteFileCall | ShellCall) -> dict:
    """
    Sends the validated tool call to the local execution server.

    Key change: we call `tool_call.model_dump()` to convert the Pydantic object
    back into a plain dict that `requests.post(json=...)` can serialize.

    model_dump() is the v2 name for the old .dict() method.
    """
    server_url = "http://localhost:7777"
    try:
        # model_dump() → {"action": "shell", "command": "ls", "timeout": 30}
        response = requests.post(server_url, json=tool_call.model_dump(), timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to command server at {server_url}")
        print("Make sure server.py is running.")
        sys.exit(1)
    except requests.exceptions.RequestException as exc:
        print(f"Error sending command to server: {exc}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: ./test.py <agent_name> <query>")
        print('Example: ./test.py taskmaster "write a story about a cat"')
        sys.exit(1)

    agent_name = sys.argv[1]
    user_query = sys.argv[2]

    # 1. Load and validate settings from .env
    print("Loading configuration...")
    settings = load_settings()
    print(f"✓ Settings loaded  (proxy: {settings.proxy_addr})\n")

    # 2. Parse agent config file
    agent_config = parse_agent_file(agent_name)

    # 3. Build system prompt
    system_prompt = build_system_prompt(agent_config)

    # 4. Resolve model name
    model_name = resolve_file(agent_config["model"])

    # 5. Query the AI
    ai_response = query_agent(settings, model_name, system_prompt, user_query)
    print("AI Response:")
    print("-" * 40)
    print(ai_response)
    print("-" * 40)

    # 6. Parse and validate the AI's tool call (if any)
    tool_call = extract_tool_call(ai_response)

    if tool_call:
        # Because tool_call is a typed object, we can use isinstance() to
        # know exactly which kind of call it is — no string comparison needed.
        if isinstance(tool_call, WriteFileCall):
            print(f"\n✓ Detected write_file call → path: {tool_call.path}")
        elif isinstance(tool_call, ShellCall):
            print(f"\n✓ Detected shell call → command: {tool_call.command}")

        print("Sending to execution server...\n")
        result = execute_on_server(tool_call)

        print("Execution Result:")
        print("-" * 40)
        print(f"Status    : {result.get('status')}")
        if result.get("exit_code") is not None:
            print(f"Exit code : {result.get('exit_code')}")
        if result.get("path"):
            print(f"Path      : {result.get('path')}")
        if result.get("bytes_written") is not None:
            print(f"Written   : {result.get('bytes_written')} bytes")
        if result.get("stdout"):
            print(f"stdout:\n{result['stdout']}")
        if result.get("stderr"):
            print(f"stderr:\n{result['stderr']}")
        print("-" * 40)
    else:
        print("\nNo tool call detected in response.")
