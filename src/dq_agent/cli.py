"""Command line interface: `dq-agent generate` and `dq-agent audit`."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import AuditResult, run_audit
from .config import load_config
from .generate_data import generate
from .report import to_markdown
from .schema import anthropic_llm_mapper
from .slack import send_alerts
from .sources import read_csv, read_sheet


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dq-agent", description="Shipment data quality audits.")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="Create synthetic shipment data with planted problems.")
    g.add_argument("--rows", type=int, default=500)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--drift", action="store_true", help="Rename/reorder columns and add one, to simulate schema drift.")
    g.add_argument("--out", default="data/sample_shipments.csv")

    a = sub.add_parser("audit", help="Audit a CSV or Google Sheet.")
    a.add_argument("--source", choices=["csv", "sheets"], default="csv")
    a.add_argument("--path", help="CSV path (for --source csv).")
    a.add_argument("--sheet-id", default=os.environ.get("GOOGLE_SHEET_ID"))
    a.add_argument("--worksheet", default=os.environ.get("GOOGLE_WORKSHEET") or None)
    a.add_argument("--config", help="Path to a config YAML (merged over defaults).")
    a.add_argument("--post-slack", action="store_true", help="Send Slack alerts using webhook env vars.")
    a.add_argument("--dry-run", action="store_true", help="Print the Slack payloads instead of sending.")
    a.add_argument("--llm", action="store_true", help="Use an LLM to map columns the rules can't (needs ANTHROPIC_API_KEY).")
    a.add_argument("--output-md", help="Write the Markdown report here.")
    a.add_argument("--output-json", help="Write the JSON result here.")
    a.add_argument("--output-errors", help="Write a CSV of every bad row (row, shipment_id, check, severity).")
    a.add_argument("--fail-on", choices=["critical", "warning", "never"], default="never",
                   help="Exit with code 1 if findings of this level exist (useful in CI).")
    return p


def _exit_code(result: AuditResult, fail_on: str) -> int:
    if fail_on == "critical" and result.critical:
        return 1
    if fail_on == "warning" and (result.critical or result.warnings):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "generate":
        df = generate(rows=args.rows, seed=args.seed, drift=args.drift)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"Wrote {len(df)} rows to {out}" + (" (with schema drift)" if args.drift else ""))
        return 0

    try:
        cfg = load_config(args.config)
        if args.source == "csv":
            if not args.path:
                print("error: --path is required for --source csv", file=sys.stderr)
                return 2
            df, source = read_csv(args.path), args.path
        else:
            if not args.sheet_id:
                print("error: set --sheet-id or GOOGLE_SHEET_ID", file=sys.stderr)
                return 2
            df, source = read_sheet(args.sheet_id, args.worksheet), f"google-sheet:{args.sheet_id[:8]}..."
        llm_fn = anthropic_llm_mapper() if args.llm else None
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = run_audit(df, cfg, source=source, llm_fn=llm_fn)
    markdown = to_markdown(result)

    # Write files first so they exist even if stdout is closed early (e.g. `| head`).
    if args.output_md:
        Path(args.output_md).write_text(markdown, encoding="utf-8")
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    if args.output_errors:
        Path(args.output_errors).write_text(result.error_details_csv(), encoding="utf-8")
    try:
        print(markdown)
    except BrokenPipeError:
        sys.stdout = None  # silence the interpreter's flush-on-exit error

    if args.post_slack or args.dry_run:
        for entry in send_alerts(result, cfg, dry_run=args.dry_run):
            note = entry["status"]
            print(f"[slack:{entry['route']}] {note}", file=sys.stderr)
            if args.dry_run:
                print(json.dumps(entry["payload"], indent=2))

    return _exit_code(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
