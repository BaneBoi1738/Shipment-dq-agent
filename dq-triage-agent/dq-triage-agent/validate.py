"""Rule-based validation: the same 8 checks as the BigQuery / AWS pipeline.

Splits raw shipment records into a clean set and a quarantine set,
where each quarantined record carries the list of rules it failed.
"""
import pandas as pd

VALID_STATUSES = {
    "CREATED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY",
    "DELIVERED", "RTO", "CANCELLED",
}


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"tracking_number": str, "status": str})
    for col in ("pickup_date", "eta_date", "delivered_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df["_rn"] = df.sort_values("pickup_date").groupby("shipment_id").cumcount() + 1

    rules = {
        "duplicate_id": df["_rn"] > 1,
        "missing_tracking": df["tracking_number"].isna(),
        "invalid_status": ~df["status"].isin(VALID_STATUSES),
        "delivered_no_date": (df["status"] == "DELIVERED") & df["delivered_date"].isna(),
        "delivered_before_pickup": (df["delivered_date"] < df["pickup_date"]).fillna(False),
        "eta_before_pickup": (df["eta_date"] < df["pickup_date"]).fillna(False),
        "invalid_weight": df["weight_kg"] <= 0,
        "same_origin_dest": df["origin_city"] == df["destination_city"],
    }
    flags = pd.DataFrame(rules)
    df["failed_rules"] = flags.apply(lambda r: [k for k, v in r.items() if v], axis=1)
    df = df.drop(columns="_rn")

    failed = df["failed_rules"].str.len() > 0
    return df[~failed].drop(columns="failed_rules"), df[failed]
