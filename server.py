#!/usr/bin/env python3

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime


class CommandHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                command_data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as e:
                self._respond(400, {"status": "error", "message": f"Invalid JSON: {e}"})
                return

            result = self._execute(command_data)
            self._respond(200, result)

        except Exception as e:
            self._respond(500, {"status": "error", "message": f"Server error: {e}"})

    def do_GET(self):
        self._respond(200, {
            "status": "running",
            "server": "JSON Command Server",
            "version": "1.0",
        })

    def _execute(self, command_data):
        if "command" not in command_data:
            return {"status": "error", "message": "Missing 'command' field"}

        cmd = command_data["command"]
        timeout = command_data.get("timeout", 30)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "status": "success",
                "command": cmd,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timestamp": datetime.now().isoformat(),
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"Command timed out after {timeout}s",
                "command": cmd,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "command": cmd,
            }

    def _respond(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {format % args}")


def run_server(host="localhost", port=7777):
    httpd = HTTPServer((host, port), CommandHandler)
    print(f"Command server listening on {host}:{port}")
    print("Press Ctrl+C to stop\n")
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
