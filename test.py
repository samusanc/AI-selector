#!/usr/bin/env python3

import os
import re
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

SERVER_URL = "http://localhost:7777"


def load_config():
    load_dotenv()
    api_key = os.getenv("API_KEY")
    ip = os.getenv("PROXY_IP")
    port = os.getenv("PROXY_PORT")

    if not all([api_key, ip, port]):
        print("Error: Missing API_KEY, PROXY_IP, or PROXY_PORT in .env file.")
        sys.exit(1)

    return api_key, f"{ip}:{port}"


def parse_agent_file(agent_name):
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


def resolve_file(path_str):
    path = Path(path_str)
    if not path.exists():
        print(f"Error: Referenced file '{path}' not found.")
        sys.exit(1)

    text = path.read_text().strip()

    # If it's a name pointer (e.g. model files), return just the value
    if text.startswith("name:"):
        return text.replace("name:", "").strip()

    return text


def load_tools(tools_dir_or_file):
    """
    Loads tool definitions. Accepts either a single .txt file path or a
    directory, in which case all .txt files inside are loaded and appended.
    """
    path = Path(tools_dir_or_file)
    if not path.exists():
        print(f"Error: Tools path '{path}' not found.")
        sys.exit(1)

    if path.is_file():
        return path.read_text().strip()

    # Directory: load all .txt tool files
    tool_files = sorted(path.glob("*.txt"))
    return "\n\n".join(f.read_text().strip() for f in tool_files)


def build_system_prompt(agent_config):
    """
    Builds the final system prompt by combining:
    1. The agent's base system prompt
    2. The tools header prompt
    3. The actual tool definitions
    """
    base_prompt = resolve_file(agent_config["system-prompt"])

    tools_header_path = agent_config.get("tools")
    if not tools_header_path:
        return base_prompt

    tools_header = resolve_file(tools_header_path)

    # Load tool definitions from .tools/
    tool_definitions = load_tools("./.tools")

    return f"{base_prompt}\n\n{tools_header}\n{tool_definitions}"


def extract_command(text):
    """
    Extracts the first valid JSON tool call from the AI response.
    Accepts both shell commands {"command": ...} and native actions {"action": ...}.
    Tries direct parse first, then scans for an embedded JSON object.
    """
    text = text.strip()

    def is_tool_call(data):
        return isinstance(data, dict) and ("command" in data or "action" in data)

    # Try parsing the whole response as JSON
    try:
        data = json.loads(text)
        if is_tool_call(data):
            return data
    except json.JSONDecodeError:
        pass

    # Scan for the outermost JSON object in the response.
    # Walk character by character to correctly handle nested braces.
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
                candidate = text[start:i + 1]
                try:
                    data = json.loads(candidate)
                    if is_tool_call(data):
                        return data
                except json.JSONDecodeError:
                    pass
                start = None

    return None


def execute_on_server(command_block):
    """Sends the command JSON to the local execution server."""
    try:
        response = requests.post(SERVER_URL, json=command_block, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to command server at {SERVER_URL}")
        print("Make sure server.py is running.")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error sending command to server: {e}")
        sys.exit(1)


def query_agent(proxy_addr, api_key, model, system_prompt, user_prompt):
    """Sends the request to the AI proxy and returns the raw text response."""
    base_url = f"http://{proxy_addr}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    print(f"Proxy: {base_url}")
    print(f"Model: {model}\n")

    try:
        response = requests.post(base_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to proxy: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Details: {e.response.text}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: ./test.py <agent_name> <query>")
        print('Example: ./test.py taskmaster "write a story about a cat"')
        sys.exit(1)

    agent_name = sys.argv[1]
    user_query = sys.argv[2]

    # 1. Load network config
    api_key, proxy_addr = load_config()

    # 2. Parse agent config
    agent_config = parse_agent_file(agent_name)

    # 3. Build system prompt (base + tools header + tool definitions)
    system_prompt = build_system_prompt(agent_config)

    # 4. Resolve model name
    model_name = resolve_file(agent_config["model"])

    # 5. Query the AI
    ai_response = query_agent(proxy_addr, api_key, model_name, system_prompt, user_query)

    print("AI Response:")
    print("-" * 40)
    print(ai_response)
    print("-" * 40)

    # 6. Check if the response contains a command to execute
    command_block = extract_command(ai_response)

    if command_block:
        label = command_block.get("action") or command_block.get("command")
        print(f"\nDetected tool call: {label}")
        print("Sending to execution server...\n")

        result = execute_on_server(command_block)

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
