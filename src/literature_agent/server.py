from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .chat import chat_with_kimi
from .config import load_config
from .config_updates import apply_config_update, preview_config_update
from .search_pipeline import run_search_pipeline
from .storage import list_download_batches, persist_capture


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "ScientificAgentCapture/0.1"

    def do_OPTIONS(self) -> None:
        self._send_json({"status": "ok"})

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_file("apps/web/index.html", content_type="text/html; charset=utf-8")
            return
        if self.path == "/app.js":
            self._send_file("apps/web/app.js", content_type="application/javascript; charset=utf-8")
            return
        if self.path == "/styles.css":
            self._send_file("apps/web/styles.css", content_type="text/css; charset=utf-8")
            return
        if self.path == "/api/download-queue":
            config = load_config()
            self._send_json({"batches": list_download_batches(config)})
            return
        if self.path != "/health":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        config = load_config()
        self._send_json(
            {
                "status": "ok",
                "config_path": str(config.path),
                "library_root": str(config.root_dir),
            }
        )

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            config = load_config()
            if self.path == "/api/capture":
                result = persist_capture(config, payload)
                self._send_json(result, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/chat":
                result = self._handle_chat(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/config/apply":
                result = self._handle_apply_patch(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/search":
                result = self._handle_search(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/agent/run":
                result = self._handle_agent_run(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        except json.JSONDecodeError as exc:
            self._send_json(
                {"error": "invalid json", "detail": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:  # pragma: no cover - smoke path only
            self._send_json(
                {"error": "capture failed", "detail": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _handle_chat(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        user_message = str(payload.get("message", "")).strip()
        if not user_message:
            raise ValueError("Message is required.")
        api_key = str(payload.get("api_key", "")).strip() or None
        result = chat_with_kimi(config, user_message, api_key=api_key)
        preview = preview_config_update(config, result.patch)
        return {
            "reply": result.content,
            "suggested_patch": result.patch,
            "agent_plan": result.plan,
            "config_preview": preview,
            "model": config.planner_model,
        }

    def _handle_apply_patch(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload.get("patch", {})
        if not isinstance(patch, dict) or not patch:
            raise ValueError("Non-empty patch is required.")
        written_path = apply_config_update(config, patch)
        updated = load_config(str(written_path))
        return {
            "status": "ok",
            "config_path": str(written_path),
            "config": updated.raw,
        }

    def _handle_search(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ValueError("Query is required.")
        max_results = int(payload.get("max_results", 20))
        auto_download = bool(payload.get("auto_download", False))
        min_score = payload.get("min_score")
        resolved_min_score = None if min_score in (None, "") else float(min_score)
        return run_search_pipeline(
            config,
            query=query,
            max_results=max_results,
            auto_download=auto_download,
            min_score=resolved_min_score,
        )

    def _handle_agent_run(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload.get("patch", {})
        plan = payload.get("plan", {}) or {}
        apply_patch_first = bool(payload.get("apply_patch_first", True))

        active_config = config
        applied = False
        if apply_patch_first and isinstance(patch, dict) and patch:
            written_path = apply_config_update(config, patch)
            active_config = load_config(str(written_path))
            applied = True

        query = str(plan.get("query", "")).strip()
        if not query:
            raise ValueError("Agent plan query is required.")

        max_results = int(plan.get("max_results", 20))
        min_score = plan.get("min_score")
        resolved_min_score = None if min_score in (None, "") else float(min_score)
        auto_download = bool(plan.get("auto_download", False))
        search_result = run_search_pipeline(
            active_config,
            query=query,
            max_results=max_results,
            auto_download=auto_download,
            min_score=resolved_min_score,
        )
        return {
            "status": "ok",
            "applied_patch": applied,
            "query": query,
            "search": search_result,
            "config": active_config.raw,
        }

    def _send_file(self, relative_path: str, content_type: str) -> None:
        project_root = Path(__file__).resolve().parents[2]
        file_path = project_root / relative_path
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.environ.get("LIT_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("LIT_AGENT_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), CaptureHandler)
    print(f"Scientific Agent capture service listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
