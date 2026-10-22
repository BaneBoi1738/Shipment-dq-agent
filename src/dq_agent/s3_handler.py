"""AWS Lambda handler: audit a CSV uploaded to S3 and write the results back to S3.

Flow:  upload incoming/shipments.csv -> S3 event -> this Lambda ->
       reports/shipments-<time>.md          human-readable report
       reports/shipments-<time>.json        same results + quality metrics, machine-readable
       reports/shipments-<time>-errors.csv  every bad row: row, shipment_id, check, severity

Failure behaviour (see docs/data-contract.md):
  * Bad INPUT (empty file, not a CSV, header only) is not retryable. The handler records it as
    reports/<name>-<time>.error.json, logs it, and returns status ERROR without raising.
  * Unexpected errors (S3 outage, permission problem, bug) are logged and RE-RAISED so that
    Lambda's own retry/alerting applies. Reports are written only after a successful audit.

Logs are one JSON object per line, so CloudWatch Logs Insights can query fields directly, e.g.
    fields @timestamp, event, status, error_rate | filter event = "audit_complete"

Environment variables (all optional):
    REPORT_PREFIX  (default "reports/")   INPUT_PREFIX (default "incoming/")
    REPORT_BUCKET  (default: same bucket) DQ_SEND_SLACK=1 to also post Slack alerts
"""
from __future__ import annotations

import io
import json
import os
import urllib.parse
from datetime import datetime, timezone

import pandas as pd

from .audit import run_audit
from .config import load_config
from .report import to_markdown
from .slack import send_alerts

_REQUEST_ID: str | None = None


class InputError(Exception):
    """The uploaded file cannot be audited. Retrying will not help."""


def log_event(event: str, level: str = "INFO", **fields) -> None:
    """Emit one structured JSON log line (pure JSON so CloudWatch Logs Insights can parse it)."""
    payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, "event": event}
    if _REQUEST_ID:
        payload["request_id"] = _REQUEST_ID
    payload.update(fields)
    print(json.dumps(payload, default=str), flush=True)


def _s3_client():  # separate function so tests can replace it
    import boto3
    return boto3.client("s3")


def _records(event: dict) -> list[tuple[str, str]]:
    """Accept a real S3 notification or a simple manual test event."""
    if "Records" in event:
        out = []
        for rec in event["Records"]:
            s3 = rec.get("s3")
            if s3:
                out.append((s3["bucket"]["name"], urllib.parse.unquote_plus(s3["object"]["key"])))
        return out
    if "bucket" in event and "key" in event:
        return [(event["bucket"], event["key"])]
    raise ValueError("Event has no S3 records. Use an S3 notification or {'bucket': ..., 'key': ...}.")


def read_csv_bytes(body: bytes) -> pd.DataFrame:
    """Parse an uploaded file, turning every 'cannot audit this' case into an InputError."""
    if not body.strip():
        raise InputError("file is empty")
    try:
        df = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise InputError("file is not valid UTF-8 text (is it really a CSV?)") from None
    except pd.errors.EmptyDataError:
        raise InputError("file has no columns") from None
    except pd.errors.ParserError as exc:
        raise InputError(f"could not parse CSV: {exc}") from None
    if df.empty:
        raise InputError("file has a header but no data rows")
    return df


def _plan(key: str) -> tuple[str, str, str, str]:
    report_prefix = os.environ.get("REPORT_PREFIX", "reports/")
    input_prefix = os.environ.get("INPUT_PREFIX", "incoming/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = os.path.splitext(os.path.basename(key))[0]
    return report_prefix, input_prefix, f"{report_prefix}{stem}-{stamp}", stamp


def _put(s3, bucket: str, key: str, text: str, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType=f"{content_type}; charset=utf-8")


def _skip_reason(key: str, report_prefix: str, input_prefix: str) -> str | None:
    if key.startswith(report_prefix):
        return "key is inside the reports prefix"
    if input_prefix and not key.startswith(input_prefix):
        return f"key is outside the input prefix '{input_prefix}'"
    if not key.lower().endswith(".csv"):
        return "not a .csv file"
    return None


def _audit_one(s3, bucket: str, key: str, cfg: dict) -> dict:
    report_prefix, input_prefix, base, _ = _plan(key)
    reason = _skip_reason(key, report_prefix, input_prefix)
    if reason:
        log_event("skipped", bucket=bucket, key=key, reason=reason)
        return {"key": key, "skipped": reason}

    out_bucket = os.environ.get("REPORT_BUCKET", bucket)
    log_event("audit_started", bucket=bucket, key=key)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    try:
        df = read_csv_bytes(body)
    except InputError as exc:
        error = {"status": "ERROR", "source": f"s3://{bucket}/{key}", "error_type": "InputError",
                 "message": str(exc), "retryable": False}
        _put(s3, out_bucket, f"{base}.error.json", json.dumps(error, indent=2), "application/json")
        log_event("input_rejected", level="WARNING", bucket=bucket, key=key, size_bytes=len(body), reason=str(exc))
        return {"key": key, "status": "ERROR", "error": str(exc), "report": f"s3://{out_bucket}/{base}.error.json"}

    result = run_audit(df, cfg, source=f"s3://{bucket}/{key}")
    _put(s3, out_bucket, f"{base}.md", to_markdown(result), "text/markdown")
    _put(s3, out_bucket, f"{base}.json", json.dumps(result.to_dict(), indent=2), "application/json")
    _put(s3, out_bucket, f"{base}-errors.csv", result.error_details_csv(), "text/csv")

    deliveries = []
    if os.environ.get("DQ_SEND_SLACK") == "1":
        deliveries = [{k: v for k, v in d.items() if k != "payload"} for d in send_alerts(result, cfg)]

    m = result.metrics
    summary = {
        "key": key,
        "status": result.status,
        "sla_met": m["sla"]["met"],
        "metrics": {k: m[k] for k in ("total_records", "valid_records", "invalid_records", "error_rate")},
        "errors_by_type": m["errors_by_type"],
        "critical": len(result.critical),
        "warnings": len(result.warnings),
        "schema_renamed": m["schema"]["columns_renamed"],
        "report": f"s3://{out_bucket}/{base}.md",
        "slack": deliveries,
    }
    log_event("audit_complete", bucket=bucket, key=key, status=result.status, sla_met=m["sla"]["met"],
              total_records=m["total_records"], invalid_records=m["invalid_records"], error_rate=m["error_rate"],
              critical=len(result.critical), warnings=len(result.warnings), errors_by_type=m["errors_by_type"],
              schema_renamed=m["schema"]["columns_renamed"], report=summary["report"])
    return summary


def lambda_handler(event: dict, context: object = None) -> dict:
    global _REQUEST_ID
    _REQUEST_ID = getattr(context, "aws_request_id", None)
    cfg = load_config()
    s3 = _s3_client()
    results = []
    for bucket, key in _records(event):
        try:
            results.append(_audit_one(s3, bucket, key, cfg))
        except Exception as exc:
            # Unexpected: log with context, then let Lambda see the failure (retry / alarms).
            log_event("audit_failed", level="ERROR", bucket=bucket, key=key,
                      error_type=type(exc).__name__, error=str(exc))
            raise
    return {"results": results}
