"""Serve the demo dashboard, its health check, and judge incident injections."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Keep ``python scripts/web_server.py`` usable locally as documented. Python
# otherwise places only scripts/ on sys.path, not the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.domain.bin_lookup import ISSUERS_BY_COUNTRY
from pipeline.domain.decline_mapping import CANONICAL_CODES
from pipeline.generator.sampling import COUNTRIES, PROVIDERS, _methods_for_country


MAX_BODY_BYTES = 8_192
DEFAULT_TRIGGER_FILE = Path("data/live/incident_trigger.json")


def health_payload(status_file: str | Path, max_age_seconds: int = 45) -> tuple[bool, dict[str, Any]]:
    try:
        status = json.loads(Path(status_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = {}
    now = datetime.now(timezone.utc).timestamp()
    missing_or_stale = [
        name for name in ("generator_last_write", "detector_last_scan")
        if not isinstance(status.get(name), (int, float)) or now - status[name] > max_age_seconds
    ]
    healthy = status.get("state") == "ready" and not missing_or_stale
    return healthy, {"status": "ok" if healthy else "unhealthy", "state": status.get("state", "unknown"), "missing_or_stale": missing_or_stale}


def validate_injection(body: object) -> dict[str, Any]:
    """Return the generator trigger payload or raise ValueError for bad judge input."""
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    provider = body.get("provider")
    country = body.get("country")
    payment_method = body.get("payment_method")
    issuing_bank = body.get("issuing_bank") or None
    multiplier = body.get("approval_rate_multiplier")
    duration = body.get("duration_minutes")
    decline_code = body.get("dominant_decline_code") or None
    mode = body.get("mode", "controlled")

    if provider not in PROVIDERS:
        raise ValueError("Choose a supported provider.")
    if country not in COUNTRIES:
        raise ValueError("Choose a supported country.")
    if payment_method not in _methods_for_country(country):
        raise ValueError("That payment method is not available in the selected country.")
    if issuing_bank:
        valid_banks = {bank for _, bank in ISSUERS_BY_COUNTRY[country]}
        if provider != "mercadopago" or issuing_bank not in valid_banks:
            raise ValueError("Issuing-bank targeting is available only for a valid Mercado Pago bank route.")
    if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool) or not 0.01 <= multiplier <= 0.95:
        raise ValueError("Approval-rate multiplier must be between 0.01 and 0.95.")
    if not isinstance(duration, int) or isinstance(duration, bool) or not 5 <= duration <= 240:
        raise ValueError("Duration must be a whole number between 5 and 240 minutes.")
    if decline_code is not None and decline_code not in CANONICAL_CODES:
        raise ValueError("Choose a supported decline code.")
    if mode not in {"controlled", "storm"}:
        raise ValueError("Choose either a controlled incident or an alert storm.")

    route = "-".join(filter(None, (provider, country, payment_method, issuing_bank)))
    return {
        "name": f"judge-{route}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        "cell_filter": {key: value for key, value in {
            "provider": provider,
            "country": country,
            "payment_method": payment_method,
            "issuing_bank": issuing_bank,
        }.items() if value is not None},
        "duration_minutes": duration,
        "approval_rate_multiplier": float(multiplier),
        "dominant_decline_code": decline_code,
        "mode": mode,
    }


def write_injection(trigger_file: str | Path, payload: dict[str, Any]) -> None:
    """Atomically queue one trigger; do not silently overwrite a pending judge action."""
    path = Path(trigger_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    reservation = path.with_name(f".{path.name}.lock")
    try:
        descriptor = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise FileExistsError("An injection is already waiting for the live generator.") from error
    os.close(descriptor)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if path.exists():
            raise FileExistsError("An injection is already waiting for the live generator.")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        # The generator only sees a complete payload because replace is atomic.
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        reservation.unlink(missing_ok=True)


class DashboardHandler(SimpleHTTPRequestHandler):
    """Static dashboard handler with two deliberate dynamic endpoints."""

    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/healthz":
            return super().do_GET()
        healthy, payload = health_payload(self.server.status_file, self.server.max_heartbeat_age_seconds)
        # Fly's service check decides whether the public dashboard is routed.
        # The web process is usable as soon as it is listening: it can serve
        # the UI and existing report feed while the data workers warm up. The
        # launcher still exits if either worker dies, so this does not keep a
        # broken runtime alive indefinitely.
        payload["web_status"] = "ok"
        self._send_json(HTTPStatus.OK, payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/injections":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_BODY_BYTES:
                raise ValueError("Request body must be between 1 and 8192 bytes.")
            body = json.loads(self.rfile.read(content_length))
            payload = validate_injection(body)
            write_injection(self.server.trigger_file, payload)
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON."})
        except FileExistsError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        else:
            self._send_json(HTTPStatus.ACCEPTED, {"status": "queued", "injection": payload})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Savvy dashboard and judge controls.")
    parser.add_argument("--directory", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--status-file", default="data/runtime-status.json")
    parser.add_argument("--trigger-file", default=DEFAULT_TRIGGER_FILE)
    parser.add_argument("--max-heartbeat-age-seconds", type=int, default=45)
    args = parser.parse_args()
    reports_db = Path(args.directory) / "data" / "reports.db"
    if reports_db.exists():
        # The database is the source of truth. Re-export it on a web-process
        # restart so the static page is never needlessly left on seed data.
        from agent_workflow.reporting.publish import ReportPublisher

        ReportPublisher(reports_db).export_dashboard_feed()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), lambda *handler_args: DashboardHandler(*handler_args, directory=str(args.directory)))
    server.status_file = args.status_file
    server.trigger_file = args.trigger_file
    server.max_heartbeat_age_seconds = args.max_heartbeat_age_seconds
    print(f"[boot] dashboard server listening on :{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
