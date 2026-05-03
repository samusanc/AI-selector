import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

def load_config():
    """Loads environment variables from .env file."""
    load_dotenv()
    api_key = os.getenv("API_KEY")
    ip = os.getenv("PROXY_IP")
    port = os.getenv("PROXY_PORT")
    
    if not all([api_key, ip, port]):
        print("Error: Missing API_KEY, PROXY_IP, or PROXY_PORT in .env file.")
        sys.exit(1)
    
    return api_key, f"{ip}:{port}"

def parse_agent_file(agent_name):
    """Parses the .agents file to get prompt and model paths."""
    agent_file = Path(f".agents/{agent_name}.txt")
    if not agent_file.exists():
        print(f"Error: Agent file '{agent_file}' not found.")
        sys.exit(1)

    config = {}
    content = agent_file.read_text().splitlines()
    for line in content:
        if ":" in line:
            key, val = line.split(":", 1)
            config[key.strip()] = val.strip()
    
    return config

def get_file_content(path_str):
    """Reads content from a file path found inside agent/model files."""
    # Convert potential './' paths to standard Paths
    path = Path(path_str)
    if not path.exists():
        print(f"Error: Referenced file '{path}' not found.")
        sys.exit(1)
    
    # Logic to handle name:gemini... or raw text
    text = path.read_text().strip()
    if text.startswith("name:"):
        return text.replace("name:", "").strip()
    return text

def test_gemini_proxy(ip_port, api_key, model, system_prompt, user_prompt):
    """Sends the request to the CLI proxy."""
    base_url = f"http://{ip_port}/v1/chat/completions"
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

    print(f"Requesting: {base_url}")
    print(f"Agent Model: {model}\n")

    try:
        response = requests.post(base_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        result = response.json()["choices"][0]["message"]["content"].strip()
        
        print("Success! Response:")
        print("-" * 40)
        print(result)
        print("-" * 40)
        
    except requests.exceptions.RequestException as e:
        print("Error connecting to proxy:")
        print(e)
        if hasattr(e, 'response') and e.response is not None:
            print(f"Details: {e.response.text}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: ./test.py <agent_name> <query>")
        print("Example: ./test.py taskmaster \"hello, the watermelons are bad?\"")
        sys.exit(1)

    agent_name = sys.argv[1]
    user_query = sys.argv[2]

    # 1. Load network config
    api_key, proxy_addr = load_config()

    # 2. Resolve agent paths
    agent_paths = parse_agent_file(agent_name)
    
    # 3. Read actual model name and system prompt text
    model_name = get_file_content(agent_paths.get("model"))
    system_prompt = get_file_content(agent_paths.get("system-prompt"))

    # 4. Run request
    test_gemini_proxy(proxy_addr, api_key, model_name, system_prompt, user_query)
