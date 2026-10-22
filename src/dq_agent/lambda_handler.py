"""AWS Lambda entry point (optional deployment target).

Configure via environment variables (see .env.example). Trigger it on a schedule with
Amazon EventBridge. An event may override the source, e.g. {"source": "sheets"}.
Packaging and infrastructure are not included yet; see the README roadmap.
"""
from __future__ import annotations

import os

from .audit import run_audit
from .config import load_config
from .schema import anthropic_llm_mapper
from .slack import send_alerts
from .sources import read_csv, read_sheet


def lambda_handler(event: dict | None, context: object = None) -> dict:
    event = event or {}
    cfg = load_config(event.get("config"))
    source = event.get("source") or os.environ.get("DQ_SOURCE", "sheets")

    if source == "csv":
        path = event.get("path") or os.environ["DQ_CSV_PATH"]
        df, label = read_csv(path), path
    else:
        sheet_id = event.get("sheet_id") or os.environ["GOOGLE_SHEET_ID"]
        df, label = read_sheet(sheet_id, os.environ.get("GOOGLE_WORKSHEET") or None), f"google-sheet:{sheet_id[:8]}..."

    llm_fn = anthropic_llm_mapper() if os.environ.get("ANTHROPIC_API_KEY") and event.get("llm") else None
    result = run_audit(df, cfg, source=label, llm_fn=llm_fn)
    deliveries = send_alerts(result, cfg, dry_run=bool(event.get("dry_run")))
    return {
        "status": result.status,
        "rows": result.rows,
        "clean_rate": round(result.clean_rate, 4),
        "critical": len(result.critical),
        "warnings": len(result.warnings),
        "deliveries": [{k: v for k, v in d.items() if k != "payload"} for d in deliveries],
    }
