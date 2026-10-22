"""Data quality checks. All checks run on a DataFrame whose columns are already canonical."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

NULL_TOKENS = {"", "null", "none", "n/a", "na", "nan", "-", "--", "nil", "#n/a"}
TS_COLUMNS = ["picked_up_at", "eta", "delivered_at", "last_update"]


@dataclass
class Finding:
    check: str
    severity: str  # critical | warning
    message: str
    count: int
    rate: float | None = None
    sample_ids: list[str] = field(default_factory=list)
    sample_rows: list[int] = field(default_factory=list)  # 1-based sheet rows (header = row 1)
    details: dict[str, Any] = field(default_factory=dict)
    scope: str = "row"  # "row" = affects specific rows, "dataset" = affects the whole file
    all_rows: list[int] = field(default_factory=list, repr=False)  # every affected row (sheet numbering)
    all_ids: list[str] = field(default_factory=list, repr=False)   # shipment_id for each affected row

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "rate": None if self.rate is None else round(self.rate, 4),
            "sample_ids": self.sample_ids,
            "sample_rows": self.sample_rows,
            "details": self.details,
            "scope": self.scope,
        }


def missing_mask(s: pd.Series) -> pd.Series:
    return s.isna() | s.astype(str).str.strip().str.lower().isin(NULL_TOKENS)


def parse_ts(s: pd.Series) -> pd.Series:
    """Parse mixed timestamp strings to UTC. Naive values are treated as UTC."""
    try:
        return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(s, errors="coerce", utc=True)


def norm_status(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().str.replace(r"[\s\-]+", "_", regex=True)


class _Ctx:
    def __init__(self, df: pd.DataFrame, cfg: dict, now: pd.Timestamp):
        self.df = df.reset_index(drop=True)
        self.cfg = cfg
        self.now = now
        self.n = len(self.df)
        self.missing = {c: missing_mask(self.df[c]) for c in self.df.columns}
        self.ts = {c: parse_ts(self.df[c]) for c in TS_COLUMNS if c in self.df.columns}
        self.status = norm_status(self.df["status"]) if "status" in self.df.columns else None
        self.sample_n = int(cfg["report"].get("sample_ids", 5))
        self.bad_rows: set[int] = set()

    def has(self, *cols: str) -> bool:
        return all(c in self.df.columns for c in cols)

    def check_cfg(self, name: str) -> dict:
        return self.cfg["checks"].get(name, {})

    def finding(self, name: str, mask: pd.Series, message: str, details: dict | None = None) -> Finding | None:
        conf = self.check_cfg(name)
        if conf.get("enabled", True) is False:
            return None
        count = int(mask.sum())
        if count == 0:
            return None
        rate = count / self.n if self.n else None
        severity = conf.get("severity", "warning")
        crit = conf.get("critical_rate")
        if crit is not None and rate is not None and rate >= float(crit):
            severity = "critical"
        bad = [int(i) for i in mask[mask].index]
        self.bad_rows.update(bad)
        all_ids = [str(self.df.loc[i, "shipment_id"]) for i in bad] if self.has("shipment_id") else []
        n = self.sample_n
        return Finding(name, severity, message, count, rate, all_ids[:n], [i + 2 for i in bad[:n]], details or {},
                       scope="row", all_rows=[i + 2 for i in bad], all_ids=all_ids)


def _required_values(ctx: _Ctx) -> Finding | None:
    req = [c for c, s in ctx.cfg["schema"]["columns"].items() if s.get("required", True) and ctx.has(c)]
    if not req:
        return None
    mask = pd.Series(False, index=ctx.df.index)
    per_col = {}
    for c in req:
        m = ctx.missing[c]
        if int(m.sum()):
            per_col[c] = int(m.sum())
        mask |= m
    return ctx.finding("missing_required_values", mask, "Rows with empty required fields", {"by_column": per_col})


def _duplicates(ctx: _Ctx) -> Finding | None:
    if not ctx.has("shipment_id"):
        return None
    ids = ctx.df["shipment_id"].astype(str).str.strip()
    mask = ids.duplicated(keep=False) & ~ctx.missing["shipment_id"]
    return ctx.finding("duplicate_shipment_id", mask, "Shipment IDs that appear more than once",
                       {"distinct_ids": int(ids[mask].nunique())})


def _invalid_status(ctx: _Ctx) -> Finding | None:
    if ctx.status is None:
        return None
    valid = set(ctx.cfg["statuses"]["valid"])
    mask = ~ctx.status.isin(valid) & ~ctx.missing["status"]
    bad = ctx.df.loc[mask, "status"].astype(str).str.strip().value_counts().head(5).to_dict()
    return ctx.finding("invalid_status", mask, "Status values outside the allowed list", {"top_values": bad})


def _unparseable(ctx: _Ctx) -> Finding | None:
    mask = pd.Series(False, index=ctx.df.index)
    per_col = {}
    for c, parsed in ctx.ts.items():
        m = parsed.isna() & ~ctx.missing[c]
        if int(m.sum()):
            per_col[c] = int(m.sum())
        mask |= m
    return ctx.finding("unparseable_timestamps", mask, "Timestamps that could not be parsed", {"by_column": per_col})


def _delivered_before_pickup(ctx: _Ctx) -> Finding | None:
    if not ctx.has("picked_up_at", "delivered_at"):
        return None
    mask = (ctx.ts["delivered_at"] < ctx.ts["picked_up_at"]).fillna(False)
    return ctx.finding("delivered_before_pickup", mask, "Delivered timestamp is earlier than pickup")


def _eta_before_pickup(ctx: _Ctx) -> Finding | None:
    if not ctx.has("picked_up_at", "eta"):
        return None
    mask = (ctx.ts["eta"] < ctx.ts["picked_up_at"]).fillna(False)
    return ctx.finding("eta_before_pickup", mask, "ETA is earlier than pickup")


def _delivered_missing_ts(ctx: _Ctx) -> Finding | None:
    if ctx.status is None or not ctx.has("delivered_at"):
        return None
    mask = (ctx.status == "delivered") & ctx.missing["delivered_at"]
    return ctx.finding("delivered_status_missing_timestamp", mask, "Status is delivered but delivered_at is empty")


def _ts_on_undelivered(ctx: _Ctx) -> Finding | None:
    if ctx.status is None or not ctx.has("delivered_at"):
        return None
    mask = (ctx.status != "delivered") & ~ctx.missing["delivered_at"] & ~ctx.missing["status"]
    return ctx.finding("timestamp_on_undelivered", mask, "delivered_at is filled but status is not delivered")


def _future(ctx: _Ctx) -> Finding | None:
    tol = pd.Timedelta(hours=float(ctx.check_cfg("future_timestamps").get("tolerance_hours", 1)))
    mask = pd.Series(False, index=ctx.df.index)
    for c in ("picked_up_at", "delivered_at", "last_update"):  # an ETA is allowed to be in the future
        if c in ctx.ts:
            mask |= (ctx.ts[c] > ctx.now + tol).fillna(False)
    return ctx.finding("future_timestamps", mask, "Timestamps set in the future")


def _stale_in_transit(ctx: _Ctx) -> Finding | None:
    if ctx.status is None or not ctx.has("last_update"):
        return None
    hours = float(ctx.check_cfg("stale_in_transit").get("max_age_hours", 48))
    live = ctx.status.isin(set(ctx.cfg["statuses"]["in_transit_like"]))
    age = ctx.now - ctx.ts["last_update"]
    mask = (live & (age > pd.Timedelta(hours=hours))).fillna(False)
    return ctx.finding("stale_in_transit", mask, f"In-transit shipments with no update for over {hours:g}h",
                       {"max_age_hours": hours})


def _freshness(ctx: _Ctx) -> Finding | None:
    conf = ctx.check_cfg("data_freshness")
    if conf.get("enabled", True) is False or not ctx.has("last_update"):
        return None
    latest = ctx.ts["last_update"].max()
    hours = float(conf.get("max_age_hours", 24))
    if pd.isna(latest):
        return Finding("data_freshness", conf.get("severity", "critical"),
                       "No parseable last_update values; cannot judge freshness", 1, None, details={},
                       scope="dataset")
    age_h = (ctx.now - latest).total_seconds() / 3600
    if age_h <= hours:
        return None
    return Finding("data_freshness", conf.get("severity", "critical"),
                   f"Newest update is {age_h:.1f}h old (limit {hours:g}h)", 1, None,
                   details={"newest_update": latest.isoformat(), "age_hours": round(age_h, 1)}, scope="dataset")


ROW_CHECKS = [
    _required_values, _duplicates, _invalid_status, _unparseable, _delivered_before_pickup,
    _eta_before_pickup, _delivered_missing_ts, _ts_on_undelivered, _future, _stale_in_transit,
]


def run_checks(df: pd.DataFrame, cfg: dict, missing_required_columns: list[str] | None = None,
               now: pd.Timestamp | None = None) -> tuple[list[Finding], float]:
    """Run every enabled check. Returns (findings, clean_row_rate)."""
    now = now or pd.Timestamp.now(tz="UTC")
    ctx = _Ctx(df, cfg, now)
    findings: list[Finding] = []

    if missing_required_columns:
        conf = cfg["checks"].get("missing_required_columns", {})
        if conf.get("enabled", True) is not False:
            findings.append(Finding(
                "missing_required_columns", conf.get("severity", "critical"),
                "Required columns could not be found in the source", len(missing_required_columns),
                details={"columns": list(missing_required_columns)}, scope="dataset"))

    for fn in ROW_CHECKS:
        f = fn(ctx)
        if f:
            findings.append(f)
    fresh = _freshness(ctx)
    if fresh:
        findings.append(fresh)

    order = {"critical": 0, "warning": 1}
    findings.sort(key=lambda f: (order.get(f.severity, 2), -f.count))
    clean_rate = 1.0 if ctx.n == 0 else 1 - len(ctx.bad_rows) / ctx.n
    return findings, clean_rate
