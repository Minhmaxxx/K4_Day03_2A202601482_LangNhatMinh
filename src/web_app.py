"""Small local web server for the Shadow Self assistant UI."""

import argparse
import contextlib
import io
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import (
    run_baseline_chatbot,
    run_react_agent,
    safety_response_for,
)
from providers import get_llm_provider


ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "web" / "index.html"
MAX_REQUEST_BYTES = 32 * 1024


class AssistantHandler(BaseHTTPRequestHandler):
    provider = None

    def log_message(self, format_string, *args):
        # Do not put user messages or query strings in local logs.
        if self.path in {"/", "/api/status", "/api/chat", "/api/compare"}:
            return
        super().log_message(format_string, *args)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self):
        try:
            body = INDEX_PATH.read_bytes()
        except OSError:
            self._send_json(500, {"error": "Không tìm thấy web/index.html."})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send_index()
            return
        if self.path == "/api/status":
            model_name = getattr(self.provider, "model_name", "Offline Mock Mode")
            self._send_json(
                200,
                {
                    "provider": self.provider.__class__.__name__,
                    "model": model_name,
                },
            )
            return
        self._send_json(404, {"error": "Không tìm thấy tài nguyên."})

    def do_POST(self):
        if self.path not in {"/api/chat", "/api/compare"}:
            self._send_json(404, {"error": "Không tìm thấy tài nguyên."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Content-Length không hợp lệ."})
            return
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Nội dung quá dài hoặc đang để trống."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Request phải là JSON UTF-8 hợp lệ."})
            return

        message = payload.get("message") if isinstance(payload, dict) else None
        mode = payload.get("mode", "agent") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip():
            self._send_json(400, {"error": "Vui lòng nhập nội dung muốn chia sẻ."})
            return
        if self.path == "/api/chat" and mode not in {"agent", "baseline"}:
            self._send_json(400, {"error": "Chế độ chat không hợp lệ."})
            return

        message = message.strip()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                if self.path == "/api/compare":
                    trace = []
                    _, safety_answer = safety_response_for(message)
                    baseline_answer = safety_answer or run_baseline_chatbot(
                        message,
                        self.provider,
                    )
                    agent_answer = run_react_agent(message, self.provider, trace=trace)
                elif mode == "agent":
                    answer = run_react_agent(message, self.provider)
                else:
                    _, safety_answer = safety_response_for(message)
                    answer = safety_answer or run_baseline_chatbot(message, self.provider)
        except Exception:
            self._send_json(
                500,
                {"error": "Trợ lý đang gặp sự cố tạm thời. Vui lòng thử lại."},
            )
            return

        if self.path == "/api/compare":
            self._send_json(
                200,
                {
                    "baseline": baseline_answer,
                    "agent": agent_answer,
                    "steps": trace,
                },
            )
            return
        self._send_json(200, {"answer": answer, "mode": mode})


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run the local web chat interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai", "anthropic", "openrouter", "mock"),
        help="Override LLM_PROVIDER for this process.",
    )
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main():
    args = parse_arguments()
    AssistantHandler.provider = get_llm_provider(args.provider)
    server = HTTPServer((args.host, args.port), AssistantHandler)
    url = f"http://{args.host}:{args.port}"
    model_name = getattr(AssistantHandler.provider, "model_name", "Offline Mock Mode")

    print(f"Web UI: {url}")
    print(f"Provider: {AssistantHandler.provider.__class__.__name__} ({model_name})")
    print("Nhấn Ctrl+C để dừng server.")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng web server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
