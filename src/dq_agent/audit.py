"""Orchestration: read -> map schema -> run checks -> assemble a result."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .checks import Finding, run_checks
from .schema import LlmFn, SchemaReport, map_schema


@dataclass
class AuditResult:
    source: str
    run_at: str
    rows: int
    clean_rate: float
    schema: SchemaReport
    findings: list[Finding] = field(default_factory=list)
    sla_min_clean_rate: float = 0.95

    @property
    def row_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.scope == "row"]

    @property
    def dataset_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.scope == "dataset"]

    @property
    def invalid_records(self) -> int:
        """Rows with at least one row-level problem (a row with 3 problems counts once)."""
        return round((1 - self.clean_rate) * self.rows)

    @property
    def sla_met(self) -> bool:
        return self.clean_rate >= self.sla_min_clean_rate and not any(
            f.severity == "critical" for f in self.dataset_findings)

    @property
    def metrics(self) -> dict:
        invalid = self.invalid_records
        return {
            "total_records": self.rows,
            "valid_records": self.rows - invalid,
            "invalid_records": invalid,
            "error_rate": round(invalid / self.rows, 4) if self.rows else 0.0,
            "clean_rate": round(self.clean_rate, 4),
            "errors_by_type": {f.check: f.count for f in self.row_findings},
            "dataset_issues": [f.check for f in self.dataset_findings],
            "findings_by_severity": {"critical": len(self.critical), "warning": len(self.warnings)},
            "schema": {
                "columns_renamed": len(self.schema.renamed),
                "columns_ignored": len(self.schema.unknown_columns),
                "required_columns_missing": len(self.schema.missing_required),
            },
            "sla": {"min_clean_rate": self.sla_min_clean_rate, "met": self.sla_met},
        }

    def error_details(self) -> list[dict]:
        """One entry per (row, problem): lets someone fix the source data row by row."""
        out = []
        for f in self.row_findings:
            for i, row in enumerate(f.all_rows):
                out.append({
                    "row": row,
                    "shipment_id": f.all_ids[i] if i < len(f.all_ids) else "",
                    "check": f.check,
                    "severity": f.severity,
                })
        out.sort(key=lambda d: (d["row"], d["check"]))
        return out

    def error_details_csv(self) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["row", "shipment_id", "check", "severity"], lineterminator="\n")
        w.writeheader()
        w.writerows(self.error_details())
        return buf.getvalue()

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def status(self) -> str:
        if self.critical:
            return "FAIL"
        if self.warnings:
            return "WARN"
        return "PASS"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "run_at": self.run_at,
            "status": self.status,
            "rows": self.rows,
            "clean_rate": round(self.clean_rate, 4),
            "metrics": self.metrics,
            "schema": self.schema.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
        }


def run_audit(df: pd.DataFrame, cfg: dict, source: str = "unknown", llm_fn: LlmFn | None = None,
              now: pd.Timestamp | None = None) -> AuditResult:
    samples = {c: df[c].astype(str).head(3).tolist() for c in df.columns}
    schema = map_schema(df.columns, cfg, samples=samples, llm_fn=llm_fn)

    canonical = df[list(schema.rename_map)].rename(columns=schema.rename_map)
    findings, clean_rate = run_checks(canonical, cfg, schema.missing_required, now=now)
    return AuditResult(
        source=source,
        run_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        rows=len(df),
        clean_rate=clean_rate,
        schema=schema,
        findings=findings,
        sla_min_clean_rate=float(cfg.get("sla", {}).get("min_clean_rate", 0.95)),
    )
