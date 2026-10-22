"""Slack alerting: a summary to the default channel plus routed exception alerts."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import requests

from .audit import AuditResult
from .checks import Finding

ICON = {"critical": "🔴", "warning": "🟠"}
STATUS_ICON = {"PASS": "✅", "WARN": "🟠", "FAIL": "🔴"}


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _finding_line(f: Finding) -> str:
    rate = f" ({f.rate:.1%})" if f.rate is not None else ""
    ids = f"\n      e.g. {', '.join(f'`{_esc(i)}`' for i in f.sample_ids)}" if f.sample_ids else ""
    return f"{ICON.get(f.severity, '•')} *{f.check}*: {_esc(f.message)}, {f.count} row(s){rate}{ids}"


def _schema_text(result: AuditResult) -> str:
    s = result.schema
    parts = []
    if s.renamed:
        parts.append("Renamed: " + ", ".join(f"`{_esc(m.source)}` → `{m.canonical}`" for m in s.renamed))
    if s.missing_required:
        parts.append("*Missing required:* " + ", ".join(f"`{c}`" for c in s.missing_required))
    if s.unknown_columns:
        parts.append("New columns (ignored): " + ", ".join(f"`{_esc(c)}`" for c in s.unknown_columns))
    return "\n".join(parts)


def summary_payload(result: AuditResult, cfg: dict) -> dict:
    limit = int(cfg["report"].get("max_findings_in_slack", 8))
    head = f"{STATUS_ICON[result.status]} Shipment data quality: {result.status} ({result.clean_rate:.1%} clean)"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": head[:150]}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Rows*\n{result.rows:,}"},
            {"type": "mrkdwn", "text": f"*Findings*\n{len(result.critical)} critical, {len(result.warnings)} warning"},
            {"type": "mrkdwn", "text": f"*SLA ({result.sla_min_clean_rate:.0%} clean)*\n{'met' if result.sla_met else 'not met'}"},
            {"type": "mrkdwn", "text": f"*Source*\n{_esc(result.source)}"},
            {"type": "mrkdwn", "text": f"*Run*\n{result.run_at}"},
        ]},
    ]
    schema_text = _schema_text(result)
    if schema_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Schema drift handled*\n{schema_text}"[:2900]}})
    shown = result.findings[:limit]
    if shown:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(_finding_line(f) for f in shown)[:2900]}})
    if len(result.findings) > limit:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"+{len(result.findings) - limit} more finding(s) in the full report"}]})
    return {"text": head, "blocks": blocks}


def exception_payload(rule_name: str, findings: list[Finding], result: AuditResult) -> dict:
    head = f"Data quality exceptions for {rule_name} ({result.status})"
    body = "\n".join(_finding_line(f) for f in findings)[:2900]
    return {"text": head, "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": head[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{_esc(result.source)} | {result.run_at}"}]},
    ]}


@dataclass
class Message:
    route: str          # human label, e.g. "default" or "data-ops"
    webhook_env: str    # env var that holds the webhook URL
    payload: dict


def build_messages(result: AuditResult, cfg: dict) -> list[Message]:
    routing = cfg["routing"]
    messages: list[Message] = []
    if result.findings or cfg["report"].get("post_on_pass", False):
        messages.append(Message("default", routing["default_webhook_env"], summary_payload(result, cfg)))
    for rule in routing.get("rules", []):
        wanted_checks = set(rule.get("checks", []))
        wanted_sev = rule.get("severity")
        matched = [f for f in result.findings
                   if f.check in wanted_checks or (wanted_sev and f.severity == wanted_sev)]
        if matched:
            messages.append(Message(rule["name"], rule["webhook_env"], exception_payload(rule["name"], matched, result)))
    return messages


def post(webhook_url: str, payload: dict, timeout: int = 10) -> None:
    resp = requests.post(webhook_url, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Slack returned {resp.status_code}: {resp.text[:200]}")


def send_alerts(result: AuditResult, cfg: dict, env: Mapping[str, str] | None = None,
                dry_run: bool = False) -> list[dict]:
    """Send (or, with dry_run, just describe) all alerts. Returns a delivery log."""
    env = env if env is not None else os.environ
    log = []
    for msg in build_messages(result, cfg):
        url = env.get(msg.webhook_env, "")
        if dry_run:
            log.append({"route": msg.route, "env": msg.webhook_env, "status": "dry-run", "payload": msg.payload})
            continue
        if not url:
            log.append({"route": msg.route, "env": msg.webhook_env, "status": "skipped (webhook not set)"})
            continue
        try:
            post(url, msg.payload)
            log.append({"route": msg.route, "env": msg.webhook_env, "status": "sent"})
        except Exception as exc:  # keep going so one broken webhook doesn't hide the others
            log.append({"route": msg.route, "env": msg.webhook_env, "status": f"failed: {exc}"})
    return log
