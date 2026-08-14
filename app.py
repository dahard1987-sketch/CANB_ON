from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from update_excel import (
    SUPPORTED_LEVELS,
    clear_target_sheet,
    create_sheets_service,
    get_sheet_names,
    inspect_excel_file,
    update_display_timestamp,
    upload_values,
)


MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_FAILURES = 8

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["JSON_AS_ASCII"] = False
app.config["APP_PASSWORD_SHA256"] = os.environ.get(
    "APP_PASSWORD_SHA256", ""
).strip().lower()
app.config["SECRET_KEY"] = os.environ.get("APP_SESSION_SECRET") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
    "SESSION_COOKIE_SECURE",
    "1" if os.environ.get("K_SERVICE") else "0",
) == "1"
# Cloud Run이 전달하는 원래 HTTPS 호스트를 Flask가 올바르게 인식하게 합니다.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_login_attempts: dict[str, deque[float]] = {}
_login_attempts_lock = threading.Lock()


def _configured_password_hash() -> str:
    value = str(app.config.get("APP_PASSWORD_SHA256", "")).strip().lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return ""
    return value


def _auth_marker(password_hash: str) -> str:
    return password_hash[:24]


def _is_authenticated() -> bool:
    password_hash = _configured_password_hash()
    marker = session.get("readi_auth", "")
    return bool(password_hash and hmac.compare_digest(marker, _auth_marker(password_hash)))


def _client_key() -> str:
    return request.remote_addr or "unknown"


def _recent_failures(client_key: str, now: float) -> deque[float]:
    cutoff = now - LOGIN_WINDOW_SECONDS
    failures = _login_attempts.setdefault(client_key, deque())
    while failures and failures[0] <= cutoff:
        failures.popleft()
    return failures


def _login_retry_seconds(client_key: str) -> int:
    now = time.monotonic()
    with _login_attempts_lock:
        failures = _recent_failures(client_key, now)
        if len(failures) < LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(LOGIN_WINDOW_SECONDS - (now - failures[0])))


def _record_login_failure(client_key: str) -> None:
    now = time.monotonic()
    with _login_attempts_lock:
        _recent_failures(client_key, now).append(now)


def _clear_login_failures(client_key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(client_key, None)


def _safe_filename(name: str) -> str:
    """브라우저가 보낸 경로 부분은 버리고 표시용 파일명만 남깁니다."""
    return Path(name.replace("\\", "/")).name or "이름 없는 파일.xlsx"


def _inspect_upload(uploaded, directory: Path, index: int) -> dict[str, Any]:
    filename = _safe_filename(uploaded.filename or "")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(".xlsx 또는 .xlsm 파일만 올릴 수 있습니다.")

    stored_path = directory / f"{uuid.uuid4().hex}{extension}"
    uploaded.save(stored_path)
    level, values = inspect_excel_file(stored_path)
    if not values:
        raise ValueError("'학생 상세' 시트의 A:K에 데이터가 없습니다.")

    return {
        "index": index,
        "level": level,
        "filename": filename,
        "rows": len(values),
        "path": stored_path,
        "values": values,
    }


def _public_item(item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "index": item["index"],
        "level": item["level"],
        "filename": item["filename"],
        "rows": item["rows"],
        **extra,
    }


@app.before_request
def reject_cross_origin_writes():
    """다른 사이트에서 전송되는 변경 요청을 거부합니다."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    origin = request.headers.get("Origin")
    if not origin:
        return None

    parsed = urlsplit(origin)
    if parsed.netloc != request.host or parsed.scheme != request.scheme:
        return jsonify({"error": "허용되지 않은 출처의 요청입니다."}), 403
    return None


@app.before_request
def require_site_password():
    if request.endpoint in {"login", "healthz", "static"}:
        return None
    if _is_authenticated():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 만료되었습니다. 다시 로그인해 주세요."}), 401
    return redirect(url_for("login"))


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/") or request.path in {"/", "/login", "/logout"}:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if _is_authenticated():
        return redirect(url_for("index"))

    password_hash = _configured_password_hash()
    if not password_hash:
        return render_template(
            "login.html",
            error="사이트 비밀번호가 아직 설정되지 않았습니다.",
            unavailable=True,
        ), 503

    error = None
    status_code = 200
    if request.method == "POST":
        client_key = _client_key()
        retry_seconds = _login_retry_seconds(client_key)
        if retry_seconds:
            error = "입력 횟수가 너무 많습니다. 잠시 후 다시 시도해 주세요."
            status_code = 429
        else:
            password = request.form.get("password", "")
            candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
            if hmac.compare_digest(candidate, password_hash):
                _clear_login_failures(client_key)
                session.clear()
                session.permanent = True
                session["readi_auth"] = _auth_marker(password_hash)
                return redirect(url_for("index"))

            _record_login_failure(client_key)
            error = "비밀번호가 맞지 않습니다. 다시 입력해 주세요."
            status_code = 401

    return render_template(
        "login.html",
        error=error,
        unavailable=False,
    ), status_code


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return render_template(
        "index.html",
        levels=SUPPORTED_LEVELS,
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.post("/api/files")
def inspect_files():
    """파일을 임시로 검사한 뒤 즉시 폐기하고 분류 결과만 반환합니다."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "선택된 파일이 없습니다."}), 400

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="excel-updater-inspect-") as temp_dir:
        directory = Path(temp_dir)
        for index, uploaded in enumerate(files):
            filename = _safe_filename(uploaded.filename or "")
            try:
                item = _inspect_upload(uploaded, directory, index)
                results.append(_public_item(item, accepted=True))
            except Exception as error:
                results.append({
                    "index": index,
                    "filename": filename,
                    "accepted": False,
                    "error": str(error),
                })

    return jsonify({"results": results})


@app.post("/api/publish")
def publish_files():
    """
    브라우저가 보관하던 파일을 한 요청에서 다시 검증하고 업데이트합니다.

    서버 인스턴스 사이에 상태를 보관하지 않으므로 Cloud Run에서 안전하게
    확장할 수 있습니다.
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "업데이트할 파일이 없습니다."}), 400

    with tempfile.TemporaryDirectory(prefix="excel-updater-publish-") as temp_dir:
        directory = Path(temp_dir)
        parsed_items: dict[str, dict[str, Any]] = {}
        validation_errors: list[dict[str, Any]] = []

        for index, uploaded in enumerate(files):
            filename = _safe_filename(uploaded.filename or "")
            try:
                item = _inspect_upload(uploaded, directory, index)
                if item["level"] in parsed_items:
                    previous = parsed_items[item["level"]]["filename"]
                    raise ValueError(
                        f"같은 레벨 파일이 두 개입니다: {previous}, {filename}"
                    )
                parsed_items[item["level"]] = item
            except Exception as error:
                validation_errors.append({
                    "index": index,
                    "filename": filename,
                    "accepted": False,
                    "error": str(error),
                })

        if validation_errors:
            return jsonify({
                "error": "파일 검증에 실패해 Google Sheets는 변경하지 않았습니다.",
                "results": validation_errors,
            }), 400

        try:
            service = create_sheets_service()
            sheet_names = get_sheet_names(service)
        except Exception as error:
            return jsonify({"error": str(error)}), 502

        missing_sheets = [
            level for level in parsed_items
            if level not in sheet_names
        ]
        if missing_sheets:
            return jsonify({
                "error": (
                    "Google Sheets에 필요한 탭이 없어 아무 파일도 변경하지 않았습니다: "
                    + ", ".join(missing_sheets)
                )
            }), 400

        slots: dict[str, dict[str, Any]] = {}
        succeeded = 0
        failed = 0

        for level, item in parsed_items.items():
            try:
                clear_target_sheet(service, level)
                updated_cells = upload_values(service, level, item["values"])
                update_display_timestamp(service, level, sheet_names)
                slots[level] = _public_item(
                    item,
                    status="success",
                    error=None,
                    updated_cells=updated_cells,
                )
                succeeded += 1
            except Exception as error:
                slots[level] = _public_item(
                    item,
                    status="failed",
                    error=str(error),
                    updated_cells=None,
                )
                failed += 1

    return jsonify({
        "slots": slots,
        "summary": {"success": succeeded, "failed": failed},
    })


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
    limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    return jsonify({
        "error": f"한 번에 올릴 수 있는 최대 용량은 {limit_mb}MB입니다."
    }), 413


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
