"""Human-readable report (Markdown) for terminals, CI artifacts and PR comments."""
from __future__ import annotations

from .audit import AuditResult

ICON = {"critical": "🔴", "warning": "🟠"}
STATUS_ICON = {"PASS": "✅", "WARN": "🟠", "FAIL": "🔴"}


def to_markdown(result: AuditResult) -> str:
    lines = [
        f"# Shipment data quality: {STATUS_ICON[result.status]} {result.status}",
        "",
        f"- **Source:** {result.source}",
        f"- **Run:** {result.run_at}",
        f"- **Rows audited:** {result.rows:,}",
        f"- **Clean rows:** {result.clean_rate:.1%}",
        f"- **Findings:** {len(result.critical)} critical, {len(result.warnings)} warning",
        f"- **SLA (clean rows >= {result.sla_min_clean_rate:.0%}):** {'✅ met' if result.sla_met else '❌ not met'}",
        "",
    ]
    m = result.metrics
    lines += [
        "## Quality metrics", "",
        "| Metric | Value |", "|---|---:|",
        f"| Total records | {m['total_records']:,} |",
        f"| Valid records | {m['valid_records']:,} |",
        f"| Invalid records | {m['invalid_records']:,} |",
        f"| Error rate | {m['error_rate']:.1%} |",
        "",
    ]
    if m["errors_by_type"]:
        lines += ["**Errors by type**", "", "| Check | Rows |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in m["errors_by_type"].items()]
        lines.append("")
    s = result.schema
    if s.has_drift:
        lines += ["## Schema drift", ""]
        for m in s.renamed:
            lines.append(f"- `{m.source}` → `{m.canonical}` ({m.method}, {m.score:.2f})")
        if s.missing_required:
            lines.append(f"- **Missing required:** {', '.join(f'`{c}`' for c in s.missing_required)}")
        if s.missing_optional:
            lines.append(f"- Missing optional: {', '.join(f'`{c}`' for c in s.missing_optional)}")
        if s.unknown_columns:
            lines.append(f"- New/unrecognised columns (ignored): {', '.join(f'`{c}`' for c in s.unknown_columns)}")
        lines.append("")

    if not result.findings:
        lines.append("No issues found.")
        return "\n".join(lines) + "\n"

    lines += ["## Findings", "", "| | Check | Rows | Rate | Example IDs |", "|---|---|---:|---:|---|"]
    for f in result.findings:
        rate = "" if f.rate is None else f"{f.rate:.1%}"
        ids = ", ".join(f.sample_ids) if f.sample_ids else ""
        lines.append(f"| {ICON.get(f.severity, '')} | `{f.check}`: {f.message} | {f.count} | {rate} | {ids} |")
    return "\n".join(lines) + "\n"
