import argparse
import json
import mimetypes
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Ensure repository root is importable when running `python3 backend/main.py`
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db import (
    close_db,
    get_session,
    get_session_messages,
    get_session_token_summaries,
    init_db,
    list_sessions,
    list_solutions,
    list_token_logs,
    list_users,
    reset_db,
)
from backend.engine import (
    handle_chat_turn,
    record_explicit_session_feedback,
    run_three_session_scenario,
    start_user_session,
)
from backend.env_loader import load_dotenv
from backend.gemini_client import LLMUnavailableError
from backend.rate_limit import RateLimitError, get_rate_limit_status_for_user


def _strip_embedding(sol: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sol.items() if k != "embedding"}


class DejaFixHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy access logs except errors; clean startup log stays visible
        pass

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        raw_len = self.headers.get("Content-Length")
        if not raw_len:
            return {}
        try:
            length = int(raw_len)
        except ValueError:
            return {}
        if length <= 0 or length > 1_048_576:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _serve_static(self, req_path: str) -> bool:
        dist_dir = (ROOT_DIR / "dist").resolve()
        if not dist_dir.is_dir():
            return False

        rel = req_path.lstrip("/") or "index.html"
        candidate = (dist_dir / rel).resolve()
        if not str(candidate).startswith(str(dist_dir)) or not candidate.is_file():
            candidate = dist_dir / "index.html"
        if not candidate.is_file():
            return False

        content = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(str(candidate))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._send_json(200, {"status": "ok", "service": "deja-fix", "runtime": "python"})
            return

        if path == "/api/state":
            user_handle = (query.get("userHandle") or [None])[0]
            include_unverified = (query.get("includeUnverified") or ["false"])[0] == "true"
            users = list_users()
            active_user = (
                next((u for u in users if u["handle"] == user_handle.lower()), None)
                if user_handle
                else None
            )
            sessions = list_sessions(int(active_user["id"]) if active_user else None)
            solutions = [_strip_embedding(s) for s in list_solutions(include_unverified)]
            token_logs = list_token_logs()
            summaries = get_session_token_summaries()
            rate_limit = get_rate_limit_status_for_user(int(active_user["id"])) if active_user else None
            self._send_json(
                200,
                {
                    "users": users,
                    "sessions": sessions,
                    "solutions": solutions,
                    "tokenLogs": token_logs,
                    "sessionTokenSummaries": summaries,
                    "rateLimit": rate_limit,
                },
            )
            return

        if path == "/api/users":
            self._send_json(200, {"users": list_users()})
            return

        if path == "/api/sessions":
            raw_uid = (query.get("userId") or [None])[0]
            uid = int(raw_uid) if raw_uid and raw_uid.isdigit() else None
            self._send_json(200, {"sessions": list_sessions(uid)})
            return

        if path.startswith("/api/sessions/"):
            tail = path[len("/api/sessions/"):].strip("/")
            if not tail.isdigit():
                self._send_json(400, {"error": "Invalid session id."})
                return
            sid = int(tail)
            session = get_session(sid)
            if not session:
                self._send_json(404, {"error": "Session not found."})
                return
            self._send_json(
                200,
                {
                    "session": session,
                    "messages": get_session_messages(sid),
                    "tokenLogs": list_token_logs(sid),
                },
            )
            return

        if path == "/api/solutions":
            include_unverified = (query.get("includeUnverified") or ["false"])[0] == "true"
            solutions = [_strip_embedding(s) for s in list_solutions(include_unverified)]
            self._send_json(200, {"solutions": solutions})
            return

        if path == "/api/tokens":
            raw_sid = (query.get("sessionId") or [None])[0]
            sid = int(raw_sid) if raw_sid and raw_sid.isdigit() else None
            self._send_json(
                200,
                {
                    "logs": list_token_logs(sid),
                    "summaries": get_session_token_summaries(),
                },
            )
            return

        if not path.startswith("/api/") and self._serve_static(path):
            return

        self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        try:
            if path == "/api/sessions":
                user_handle = body.get("userHandle")
                if not isinstance(user_handle, str) or not user_handle.strip():
                    self._send_json(400, {"error": "userHandle is required."})
                    return
                created = start_user_session(
                    user_handle=user_handle,
                    display_name=body.get("displayName") if isinstance(body.get("displayName"), str) else None,
                    title=body.get("title") if isinstance(body.get("title"), str) else None,
                )
                self._send_json(201, created)
                return

            if path == "/api/chat":
                user_handle = body.get("userHandle")
                message = body.get("message")
                if not isinstance(user_handle, str) or not user_handle.strip():
                    self._send_json(400, {"error": "userHandle is required."})
                    return
                if not isinstance(message, str) or not message.strip():
                    self._send_json(400, {"error": "message is required."})
                    return

                sid = body.get("sessionId")
                result = handle_chat_turn(
                    user_handle=user_handle,
                    display_name=body.get("displayName") if isinstance(body.get("displayName"), str) else None,
                    session_id=int(sid) if isinstance(sid, int) else None,
                    message=message,
                )
                rate_limit = get_rate_limit_status_for_user(int(result["user"]["id"]))
                self._send_json(200, {**result, "rateLimit": rate_limit})
                return

            if path.startswith("/api/sessions/") and path.endswith("/feedback"):
                mid = path[len("/api/sessions/"):-len("/feedback")].strip("/")
                outcome = body.get("outcome")
                if not mid.isdigit() or outcome not in ("confirmed", "failed"):
                    self._send_json(
                        400,
                        {"error": 'Valid session id and outcome ("confirmed" | "failed") required.'},
                    )
                    return
                result = record_explicit_session_feedback(int(mid), outcome)
                self._send_json(200, result)
                return

            if path == "/api/demo/run":
                reset_first = body.get("resetFirst") is not False
                if reset_first:
                    reset_db()
                report = run_three_session_scenario()
                self._send_json(200, report)
                return

            if path == "/api/admin/reset":
                reset_db()
                self._send_json(200, {"ok": True, "message": "Database reset to empty state."})
                return

            self._send_json(404, {"error": "Not found."})
        except RateLimitError as err:
            self._send_json(
                429,
                {
                    "error": str(err),
                    "code": "RATE_LIMIT_EXCEEDED",
                    "limitType": err.limit_type,
                    "limit": err.limit,
                    "retryAfterSeconds": err.retry_after_seconds,
                },
                extra_headers={"Retry-After": str(err.retry_after_seconds)},
            )
        except LLMUnavailableError as err:
            self._send_json(
                err.status,
                {
                    "error": err.user_message,
                    "code": "LLM_UNAVAILABLE",
                },
            )
        except ValueError as err:
            self._send_json(400, {"error": str(err)})
        except Exception as err:
            self._send_json(500, {"error": str(err) or "Internal server error."})


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")
    parser = argparse.ArgumentParser(description="Déjà Fix Python Backend Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PYTHON_PORT", "8001")))
    args = parser.parse_args()

    init_db()
    server = ThreadingHTTPServer((args.host, args.port), DejaFixHandler)

    def _shutdown(_signum: int, _frame: Any) -> None:
        close_db()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"Déjà Fix Python backend listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        close_db()


if __name__ == "__main__":
    main()
