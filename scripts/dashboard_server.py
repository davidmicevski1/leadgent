#!/usr/bin/env python3
"""LeadGent local dashboard server.

Serves:
- Login page at /login
- Protected dashboard app at /dashboard/
- Protected task API persisted in data/tasks.json
- Protected markdown doc editor API for docs/ and templates/
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT_DIR / "dashboard"
DATA_DIR = ROOT_DIR / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
ALLOWED_DOC_DIRS = [ROOT_DIR / "docs", ROOT_DIR / "templates"]
TASK_STATUSES = {"todo", "in_progress", "done"}
TASK_PRIORITIES = {"high", "medium", "low"}
WRITE_LOCK = threading.Lock()

SESSION_COOKIE_NAME = "leadgent_session"
SESSION_TTL_SECONDS = int(os.getenv("LEADGENT_SESSION_TTL_SECONDS", "604800"))
COOKIE_SECURE = os.getenv("LEADGENT_COOKIE_SECURE", "0") == "1"

DEFAULT_PASSWORDS = {
    "david": "ChangeMe-David",
    "viktorija": "ChangeMe-Viktorija",
}

AUTH_USERS = {
    "david": {
        "username": "David",
        "password": os.getenv("LEADGENT_DAVID_PASSWORD", DEFAULT_PASSWORDS["david"]),
    },
    "viktorija": {
        "username": "Viktorija",
        "password": os.getenv("LEADGENT_VIKTORIJA_PASSWORD", DEFAULT_PASSWORDS["viktorija"]),
    },
}

SESSIONS: dict[str, dict[str, Any]] = {}
SESSIONS_LOCK = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_response(
    handler: BaseHTTPRequestHandler,
    code: int,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if headers:
        for key, value in headers.items():
            handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler: BaseHTTPRequestHandler, code: int, message: str) -> None:
    json_response(handler, code, {"error": message})


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ValueError("Invalid Content-Length") from exc

    raw = handler.rfile.read(length) if length > 0 else b"{}"
    if not raw:
        return {}

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")

    return payload


def ensure_tasks_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TASKS_FILE.exists():
        TASKS_FILE.write_text('{"tasks": []}\n', encoding="utf-8")


def read_tasks() -> list[dict[str, Any]]:
    ensure_tasks_file()
    with TASKS_FILE.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("tasks.json is malformed: tasks must be a list")

    return tasks


def write_tasks(tasks: list[dict[str, Any]]) -> None:
    ensure_tasks_file()
    payload = {"tasks": tasks}
    temp_file = TASKS_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temp_file.replace(TASKS_FILE)


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        tags = [str(tag).strip() for tag in value if str(tag).strip()]
        return tags[:12]
    if isinstance(value, str):
        tags = [part.strip() for part in value.split(",") if part.strip()]
        return tags[:12]
    return []


def normalize_task_input(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    title = str(payload.get("title", existing.get("title") if existing else "")).strip()
    if not title:
        raise ValueError("Task title is required")

    description = str(payload.get("description", existing.get("description") if existing else "")).strip()

    status = str(payload.get("status", existing.get("status") if existing else "todo")).strip().lower()
    if status not in TASK_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(TASK_STATUSES))}")

    priority = str(payload.get("priority", existing.get("priority") if existing else "medium")).strip().lower()
    if priority not in TASK_PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(sorted(TASK_PRIORITIES))}")

    due_date = str(payload.get("dueDate", existing.get("dueDate") if existing else "")).strip()
    tags = normalize_tags(payload.get("tags", existing.get("tags") if existing else []))

    notes = existing.get("notes", []) if existing else []
    if not isinstance(notes, list):
        notes = []

    return {
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "dueDate": due_date,
        "tags": tags,
        "notes": notes,
    }


def next_task_id(tasks: list[dict[str, Any]]) -> str:
    existing = {str(task.get("id", "")) for task in tasks}
    n = 1
    while True:
        candidate = f"t-{n:03d}"
        if candidate not in existing:
            return candidate
        n += 1


def resolve_doc_path(raw_path: str, require_existing: bool) -> tuple[Path, str]:
    if not raw_path:
        raise ValueError("Missing path query parameter")

    cleaned = unquote(raw_path).strip().lstrip("/")
    if not cleaned.endswith(".md"):
        raise ValueError("Only .md files are editable")

    candidate = (ROOT_DIR / cleaned).resolve()

    allowed = any(candidate.is_relative_to(base.resolve()) for base in ALLOWED_DOC_DIRS)
    if not allowed:
        raise PermissionError("Path is outside editable directories")

    if require_existing and not candidate.exists():
        raise FileNotFoundError("Document not found")

    if candidate.exists() and not candidate.is_file():
        raise ValueError("Path is not a file")

    return candidate, cleaned


def list_documents() -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    for base in ALLOWED_DOC_DIRS:
        category = base.name
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(ROOT_DIR).as_posix()
            docs.append(
                {
                    "path": rel,
                    "name": path.name,
                    "category": category,
                }
            )
    return docs


def parse_cookie_value(cookie_header: str, name: str) -> str | None:
    for item in cookie_header.split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == name:
            return value
    return None


def build_session_cookie(token: str, *, max_age: int | None = None) -> str:
    parts = [f"{SESSION_COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if COOKIE_SECURE:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


def authenticate_user(username: str, password: str) -> str | None:
    key = username.strip().lower()
    user = AUTH_USERS.get(key)
    if not user:
        return None

    expected = str(user["password"])
    if hmac.compare_digest(password, expected):
        return str(user["username"])

    return None


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with SESSIONS_LOCK:
        SESSIONS[token] = {
            "username": username,
            "expiresAt": now + SESSION_TTL_SECONDS,
        }
    return token


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with SESSIONS_LOCK:
        SESSIONS.pop(token, None)


def get_authenticated_user(handler: BaseHTTPRequestHandler) -> str | None:
    cookie_header = handler.headers.get("Cookie", "")
    token = parse_cookie_value(cookie_header, SESSION_COOKIE_NAME)
    if not token:
        return None

    now = int(time.time())
    with SESSIONS_LOCK:
        session = SESSIONS.get(token)
        if not session:
            return None
        expires_at = int(session.get("expiresAt", 0))
        if expires_at < now:
            SESSIONS.pop(token, None)
            return None

        # Sliding expiry on activity.
        session["expiresAt"] = now + SESSION_TTL_SECONDS
        return str(session.get("username", "")) or None


def get_session_token(handler: BaseHTTPRequestHandler) -> str | None:
    cookie_header = handler.headers.get("Cookie", "")
    return parse_cookie_value(cookie_header, SESSION_COOKIE_NAME)


class LeadGentHandler(BaseHTTPRequestHandler):
    server_version = "LeadGentDashboard/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        user = get_authenticated_user(self)

        if route == "/api/health":
            json_response(self, HTTPStatus.OK, {"status": "ok", "time": utc_now_iso()})
            return

        if route == "/login":
            if user:
                self.redirect("/dashboard/")
                return
            self.serve_static("login.html")
            return

        if route in {"/", ""}:
            self.redirect("/dashboard/" if user else "/login")
            return

        if route.startswith("/api/"):
            if not user:
                error_response(self, HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return

            if route == "/api/me":
                json_response(self, HTTPStatus.OK, {"authenticated": True, "user": {"username": user}})
                return

            if route == "/api/tasks":
                try:
                    tasks = read_tasks()
                except Exception as exc:  # pragma: no cover
                    error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed reading tasks: {exc}")
                    return

                json_response(self, HTTPStatus.OK, {"tasks": tasks})
                return

            if route == "/api/docs":
                json_response(self, HTTPStatus.OK, {"documents": list_documents()})
                return

            if route == "/api/doc":
                params = parse_qs(parsed.query)
                raw_path = params.get("path", [""])[0]
                try:
                    full_path, rel_path = resolve_doc_path(raw_path, require_existing=True)
                    content = full_path.read_text(encoding="utf-8")
                except FileNotFoundError as exc:
                    error_response(self, HTTPStatus.NOT_FOUND, str(exc))
                    return
                except PermissionError as exc:
                    error_response(self, HTTPStatus.FORBIDDEN, str(exc))
                    return
                except ValueError as exc:
                    error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except Exception as exc:  # pragma: no cover
                    error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed reading document: {exc}")
                    return

                json_response(self, HTTPStatus.OK, {"path": rel_path, "content": content})
                return

            error_response(self, HTTPStatus.NOT_FOUND, "Route not found")
            return

        if route in {"/dashboard", "/dashboard/"}:
            if not user:
                self.redirect("/login")
                return
            self.serve_static("index.html")
            return

        if route.startswith("/dashboard/"):
            if not user:
                self.redirect("/login")
                return

            relative = route[len("/dashboard/") :]
            if not relative:
                relative = "index.html"
            self.serve_static(relative)
            return

        error_response(self, HTTPStatus.NOT_FOUND, "Route not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/login":
            try:
                payload = read_json_body(self)
                username = str(payload.get("username", "")).strip()
                password = str(payload.get("password", ""))
                if not username or not password:
                    raise ValueError("username and password are required")

                authenticated_username = authenticate_user(username, password)
                if not authenticated_username:
                    error_response(self, HTTPStatus.UNAUTHORIZED, "Invalid credentials")
                    return

                token = create_session(authenticated_username)
                cookie = build_session_cookie(token, max_age=SESSION_TTL_SECONDS)
                json_response(
                    self,
                    HTTPStatus.OK,
                    {"authenticated": True, "user": {"username": authenticated_username}},
                    headers={"Set-Cookie": cookie},
                )
            except ValueError as exc:
                error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Login failed: {exc}")
            return

        user = get_authenticated_user(self)
        if not user:
            error_response(self, HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return

        if route == "/api/logout":
            token = get_session_token(self)
            destroy_session(token)
            cookie = build_session_cookie("", max_age=0)
            json_response(self, HTTPStatus.OK, {"loggedOut": True}, headers={"Set-Cookie": cookie})
            return

        if route == "/api/tasks":
            try:
                payload = read_json_body(self)
                with WRITE_LOCK:
                    tasks = read_tasks()
                    task = normalize_task_input(payload)
                    task_id = next_task_id(tasks)
                    now = utc_now_iso()
                    task_record = {
                        "id": task_id,
                        **task,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    tasks.append(task_record)
                    write_tasks(tasks)
                json_response(self, HTTPStatus.CREATED, {"task": task_record})
            except ValueError as exc:
                error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed creating task: {exc}")
            return

        if route.startswith("/api/tasks/") and route.endswith("/notes"):
            task_id = route[len("/api/tasks/") : -len("/notes")].strip("/")
            if not task_id:
                error_response(self, HTTPStatus.BAD_REQUEST, "Missing task id")
                return

            try:
                payload = read_json_body(self)
                note_text = str(payload.get("text", "")).strip()
                if not note_text:
                    raise ValueError("Note text is required")

                with WRITE_LOCK:
                    tasks = read_tasks()
                    task = next((item for item in tasks if item.get("id") == task_id), None)
                    if not task:
                        error_response(self, HTTPStatus.NOT_FOUND, "Task not found")
                        return

                    notes = task.get("notes", [])
                    if not isinstance(notes, list):
                        notes = []
                    note = {
                        "id": f"n-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                        "text": note_text,
                        "createdAt": utc_now_iso(),
                    }
                    notes.append(note)
                    task["notes"] = notes
                    task["updatedAt"] = utc_now_iso()
                    write_tasks(tasks)

                json_response(self, HTTPStatus.CREATED, {"note": note})
            except ValueError as exc:
                error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed adding note: {exc}")
            return

        error_response(self, HTTPStatus.NOT_FOUND, "Route not found")

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        user = get_authenticated_user(self)

        if not user:
            error_response(self, HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return

        if route.startswith("/api/tasks/"):
            task_id = route[len("/api/tasks/") :].strip("/")
            if not task_id:
                error_response(self, HTTPStatus.BAD_REQUEST, "Missing task id")
                return

            try:
                payload = read_json_body(self)
                with WRITE_LOCK:
                    tasks = read_tasks()
                    task = next((item for item in tasks if item.get("id") == task_id), None)
                    if not task:
                        error_response(self, HTTPStatus.NOT_FOUND, "Task not found")
                        return

                    normalized = normalize_task_input(payload, existing=task)
                    task.update(normalized)
                    task["updatedAt"] = utc_now_iso()
                    write_tasks(tasks)

                json_response(self, HTTPStatus.OK, {"task": task})
            except ValueError as exc:
                error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed updating task: {exc}")
            return

        if route == "/api/doc":
            params = parse_qs(parsed.query)
            raw_path = params.get("path", [""])[0]
            try:
                payload = read_json_body(self)
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("content must be a string")

                with WRITE_LOCK:
                    full_path, rel_path = resolve_doc_path(raw_path, require_existing=True)
                    full_path.write_text(content, encoding="utf-8")

                json_response(self, HTTPStatus.OK, {"saved": True, "path": rel_path, "updatedAt": utc_now_iso()})
            except FileNotFoundError as exc:
                error_response(self, HTTPStatus.NOT_FOUND, str(exc))
            except PermissionError as exc:
                error_response(self, HTTPStatus.FORBIDDEN, str(exc))
            except ValueError as exc:
                error_response(self, HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed saving document: {exc}")
            return

        error_response(self, HTTPStatus.NOT_FOUND, "Route not found")

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        user = get_authenticated_user(self)

        if not user:
            error_response(self, HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return

        if route.startswith("/api/tasks/"):
            task_id = route[len("/api/tasks/") :].strip("/")
            if not task_id:
                error_response(self, HTTPStatus.BAD_REQUEST, "Missing task id")
                return

            try:
                with WRITE_LOCK:
                    tasks = read_tasks()
                    original_count = len(tasks)
                    tasks = [task for task in tasks if task.get("id") != task_id]
                    if len(tasks) == original_count:
                        error_response(self, HTTPStatus.NOT_FOUND, "Task not found")
                        return
                    write_tasks(tasks)
                json_response(self, HTTPStatus.OK, {"deleted": True, "taskId": task_id})
            except Exception as exc:  # pragma: no cover
                error_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed deleting task: {exc}")
            return

        error_response(self, HTTPStatus.NOT_FOUND, "Route not found")

    def serve_static(self, relative_path: str) -> None:
        cleaned = Path(relative_path)
        full = (DASHBOARD_DIR / cleaned).resolve()

        if not full.is_relative_to(DASHBOARD_DIR.resolve()) or not full.exists() or not full.is_file():
            error_response(self, HTTPStatus.NOT_FOUND, "Static asset not found")
            return

        mime, _ = mimetypes.guess_type(str(full))
        content_type = mime or "application/octet-stream"
        data = full.read_bytes()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Keep output concise while still visible in terminal.
        print(f"[{self.log_date_time_string()}] {format % args}")


def run(port: int) -> None:
    if not DASHBOARD_DIR.exists():
        raise SystemExit("dashboard/ directory not found")

    ensure_tasks_file()

    insecure_users = [
        AUTH_USERS[key]["username"]
        for key in ("david", "viktorija")
        if AUTH_USERS[key]["password"] == DEFAULT_PASSWORDS[key]
    ]
    if insecure_users:
        joined = ", ".join(insecure_users)
        print(
            "WARNING: Default passwords are active for "
            f"{joined}. Set LEADGENT_DAVID_PASSWORD and LEADGENT_VIKTORIJA_PASSWORD before hosting."
        )

    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, port), LeadGentHandler)
    print(f"LeadGent dashboard running at http://{host}:{port}/dashboard/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run LeadGent dashboard server")
    parser.add_argument("port", nargs="?", type=int, default=8080)
    args = parser.parse_args()
    run(args.port)
