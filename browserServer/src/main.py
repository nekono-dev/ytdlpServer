from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, request
from runtime import ChromiumRuntime
from session import SessionError, SessionManager
from waitress import serve

app = Flask(__name__)
app.json.ensure_ascii = False

PORT = int(os.environ.get("PORT", "8080"))
NOVNC_PORT = int(os.environ.get("NOVNC_PORT", "6080"))
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT", "900"))

manager = SessionManager(ChromiumRuntime(NOVNC_PORT), SESSION_TIMEOUT)
INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


@app.errorhandler(SessionError)
def handle_session_error(e: SessionError) -> tuple[Response, int]:
    return jsonify({"message": e.message}), e.status


@app.route("/", methods=["GET"])
def index() -> Response:
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/session", methods=["GET"])
def get_session() -> tuple[Response, int]:
    return jsonify(manager.info()), 200


@app.route("/session", methods=["POST"])
def start_session() -> tuple[Response, int]:
    form = request.get_json(silent=True)
    if not isinstance(form, dict):
        return jsonify({"message": "Invalid request."}), 400
    return jsonify(manager.start(form.get("profile"), form.get("start_url"))), 200


@app.route("/session/commit", methods=["POST"])
def commit_session() -> tuple[Response, int]:
    return jsonify(manager.commit()), 200


@app.route("/session", methods=["DELETE"])
def cancel_session() -> tuple[Response, int]:
    manager.cancel()
    return jsonify({"message": "Cancelled."}), 200


def _shutdown(*_args: object) -> None:
    # コンテナ停止時に、ブラウザとプロファイルを残さない
    manager.runtime.stop()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown)
    print("INFO: Start browserServer port:", PORT)
    serve(app, host="0.0.0.0", port=PORT)
