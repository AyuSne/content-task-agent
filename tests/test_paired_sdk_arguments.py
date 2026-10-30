import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import cli_json


@pytest.mark.parametrize("wrapped", [False, True])
def test_captured_campaign_arguments_survive_sdk_and_cli_without_double_decoding(cli_env, monkeypatch, wrapped):
    evidence = Path(__file__).resolve().parents[1] / (
        "evidence/redesign/ticket11-paired-raw/paired-new/save-crash-recovery/05-inspect.json"
    )
    facts = json.loads(evidence.read_text(encoding="utf-8"))
    calls = [call for message in facts["messages"] if message["role"] == "assistant"
             for call in message["tool_calls"] if call["function"]["name"] == "submit_campaign"]
    if wrapped:
        arguments = next(call["function"]["arguments"] for call in calls
                         if call["function"]["arguments"].startswith('{"arguments":'))
        assert isinstance(json.loads(arguments)["arguments"], str)
        with pytest.raises(json.JSONDecodeError):
            json.loads(json.loads(arguments)["arguments"])
    else:
        arguments = next(call["function"]["arguments"] for call in calls
                         if not call["function"]["arguments"].startswith('{"arguments":'))
        with pytest.raises(json.JSONDecodeError):
            json.loads(arguments)

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps({
                "id": "replay", "object": "chat.completion", "created": 0, "model": "deepseek-flash",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{
                        "id": "replayed-call", "type": "function",
                        "function": {"name": "submit_campaign", "arguments": arguments},
                    }],
                }}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{server.server_port}")
        task = cli_json("--mode", "real", "create", "--new", "--topic", "回放", "--budget", "10",
                        "--deadline", "2026-10-30", "--audience", "成年人")
        cli_json("--mode", "real", "run", task["task_id"], "--max-rounds", "1")
        facts = cli_json("--mode", "real", "inspect", task["task_id"])
        replayed = next(call for message in facts["messages"] if message["role"] == "assistant"
                        for call in message["tool_calls"] if call["id"] == "replayed-call")
        result = next(json.loads(message["content"]) for message in facts["messages"]
                      if message["role"] == "tool" and message["tool_call_id"] == "replayed-call")
        assert replayed["function"]["arguments"] == arguments
        assert result["error"] == ("invalid_campaign" if wrapped else "invalid_arguments")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
