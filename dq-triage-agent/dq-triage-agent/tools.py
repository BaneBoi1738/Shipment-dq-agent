"""Tools the agent can call. Each one is a plain Python function over the
validated data, plus a JSON schema so the model knows how to call it.

The model never sees the raw dataset. It has to investigate through these
tools, which keeps it grounded in real numbers instead of guessing.
"""
import json
from pathlib import Path

import pandas as pd


class TriageTools:
    def __init__(self, clean: pd.DataFrame, quarantine: pd.DataFrame, out_dir: str):
        self.clean = clean
        self.q = quarantine.explode("failed_rules").rename(columns={"failed_rules": "rule"})
        self.total = len(clean) + len(quarantine)
        self.q_rows = len(quarantine)
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.notes: list[dict] = []

    # ---------- tools ----------
    def get_quarantine_summary(self) -> dict:
        """Overall failure counts by rule and by carrier."""
        by_carrier = (
            self.q.groupby("carrier")["shipment_id"].nunique()
            / (self.clean.groupby("carrier").size() + self.q.groupby("carrier")["shipment_id"].nunique())
        )
        return {
            "total_records": int(self.total),
            "quarantined_records": int(self.q_rows),
            "failures_by_rule": self.q["rule"].value_counts().to_dict(),
            "failure_rate_by_carrier_pct": (by_carrier * 100).round(2).sort_values(ascending=False).to_dict(),
        }

    def get_failed_records(self, rule: str | None = None, carrier: str | None = None, limit: int = 10) -> dict:
        """Sample failed records, optionally filtered by rule and/or carrier."""
        df = self.q
        if rule:
            df = df[df["rule"] == rule]
        if carrier:
            df = df[df["carrier"] == carrier]
        cols = ["shipment_id", "carrier", "rule", "status", "pickup_date",
                "eta_date", "delivered_date", "weight_kg", "origin_city", "destination_city"]
        sample = df[cols].head(min(limit, 25)).astype(str).to_dict(orient="records")
        return {"matching_failures": int(len(df)), "sample": sample}

    def get_carrier_breakdown(self, carrier: str) -> dict:
        """For one carrier: which rules it fails, and its weekly failure trend."""
        cq = self.q[self.q["carrier"] == carrier]
        if cq.empty and carrier not in set(self.clean["carrier"]):
            return {"error": f"Unknown carrier '{carrier}'"}
        allc = pd.concat([
            self.clean[self.clean["carrier"] == carrier].assign(failed=False),
            cq.drop_duplicates("shipment_id").assign(failed=True),
        ])
        weekly = (
            allc.set_index("pickup_date").resample("W-MON")["failed"].mean().mul(100).round(2)
        )
        return {
            "carrier": carrier,
            "failures_by_rule": cq["rule"].value_counts().to_dict(),
            "weekly_failure_rate_pct": {str(k.date()): v for k, v in weekly.dropna().items()},
        }

    def write_fix_note(self, carrier: str, root_cause: str, evidence: str,
                       recommended_fix: str, priority: str) -> dict:
        """Record a fix note for one carrier. Called once per issue the agent decides to escalate."""
        note = dict(carrier=carrier, root_cause=root_cause, evidence=evidence,
                    recommended_fix=recommended_fix, priority=priority)
        self.notes.append(note)
        return {"saved": True, "notes_so_far": len(self.notes)}

    # ---------- plumbing ----------
    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if fn is None or name.startswith("_") or name == "run":
            return json.dumps({"error": f"Unknown tool {name}"})
        try:
            return json.dumps(fn(**args), default=str)
        except Exception as e:  # return errors to the model instead of crashing
            return json.dumps({"error": str(e)})

    def save_report(self, summary: str) -> Path:
        lines = ["# Shipment Data Quality: Triage Report", "", summary.strip(), "", "## Fix notes", ""]
        order = {"high": 0, "medium": 1, "low": 2}
        for n in sorted(self.notes, key=lambda n: order.get(n["priority"].lower(), 3)):
            lines += [
                f"### {n['carrier']} — {n['priority'].upper()}",
                f"**Root cause:** {n['root_cause']}",
                f"**Evidence:** {n['evidence']}",
                f"**Recommended fix:** {n['recommended_fix']}",
                "",
            ]
        path = self.out / "triage_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


TOOL_SCHEMAS = [
    {
        "name": "get_quarantine_summary",
        "description": "Get overall data quality numbers: total and quarantined record counts, failures by rule, and failure rate by carrier. Start here.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_failed_records",
        "description": "Look at sample failed records to understand what a failure actually looks like. Filter by rule and/or carrier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rule": {"type": "string", "description": "Rule name, e.g. delivered_before_pickup"},
                "carrier": {"type": "string"},
                "limit": {"type": "integer", "description": "Max records to return (<=25)"},
            },
        },
    },
    {
        "name": "get_carrier_breakdown",
        "description": "For one carrier, see which rules it fails most and how its weekly failure rate is trending.",
        "input_schema": {
            "type": "object",
            "properties": {"carrier": {"type": "string"}},
            "required": ["carrier"],
        },
    },
    {
        "name": "write_fix_note",
        "description": "Escalate one issue to a carrier with a root cause, the evidence (cite numbers), a concrete fix, and a priority.",
        "input_schema": {
            "type": "object",
            "properties": {
                "carrier": {"type": "string"},
                "root_cause": {"type": "string"},
                "evidence": {"type": "string"},
                "recommended_fix": {"type": "string"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["carrier", "root_cause", "evidence", "recommended_fix", "priority"],
        },
    },
]
