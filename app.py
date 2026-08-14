from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request
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

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["JSON_AS_ASCII"] = False
# Cloud Run이 전달하는 원래 HTTPS 호스트를 Flask가 올바르게 인식하게 합니다.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


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
    """IAP에 더해 다른 사이트에서 전송되는 변경 요청도 거부합니다."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    origin = request.headers.get("Origin")
    if not origin:
        return None

    parsed = urlsplit(origin)
    if parsed.netloc != request.host or parsed.scheme != request.scheme:
        return jsonify({"error": "허용되지 않은 출처의 요청입니다."}), 403
    return None


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
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
