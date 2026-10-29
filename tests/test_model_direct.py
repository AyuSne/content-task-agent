"""The real SDK adapter obeys the explicit direct-route contract."""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cta.config import load_config
from cta.model import DeepSeekClient


def test_model_adapter_connects_directly_when_proxy_environment_is_present(cli_env, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CTA_LLM_TIMEOUT", "1")
    received = []

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = json.dumps({
                "id": "local-response", "object": "chat.completion", "created": 0,
                "model": "deepseek-flash",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "本地模型接口响应"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = replace(
            load_config("real"), base_url=f"http://127.0.0.1:{server.server_port}", api_key="local-test-key",
        )
        response = DeepSeekClient(config).complete([{"role": "user", "content": "测试直连"}], [], cursor=0)
        assert response.error is None, response.error
        assert response.content == "本地模型接口响应"
        assert response.usage["total_tokens"] == 8
        assert len(received) == 1
        assert received[0]["stream"] is False
        assert received[0]["thinking"] == {"type": "disabled"}
        assert config.model_config_public()["network_route"] == "direct"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
