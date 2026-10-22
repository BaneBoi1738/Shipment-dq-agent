"""Synthetic shipment data with deliberately planted quality problems.

100% fabricated: no real company, carrier or customer data is used. The same seed
produces the same rows whether or not --drift is set; drift only changes headers,
column order and adds an extra column, which lets tests prove the schema mapper
recovers the original data.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pandas as pd

CARRIERS = ["Northwind Freight", "Swiftline", "Harbor Express", "Cobalt Logistics", "Meridian Cargo"]
CITIES = ["Delhi", "Mumbai", "Bengaluru", "Chennai", "Hyderabad", "Pune", "Kolkata", "Ahmedabad", "Jaipur", "Gurugram"]
FMT = "%Y-%m-%d %H:%M:%S"

DRIFT_NAMES = {
    "shipment_id": "Tracking Number",
    "carrier": "Carrier Name",
    "origin": "Origin City",
    "destination": "Destination City",
    "status": "Shipment Status",
    "picked_up_at": "Pickup Time",
    "eta": "Estimated Delivery",
    "delivered_at": "Delivery Date",
    "last_update": "Last Updated",
}


def generate(rows: int = 500, seed: int = 42, drift: bool = False, now: datetime | None = None) -> pd.DataFrame:
    rng = random.Random(seed)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0, tzinfo=None)
    data = []
    for i in range(1, rows + 1):
        picked = now - timedelta(hours=rng.uniform(6, 240))
        transit_days = rng.randint(1, 5)
        eta = picked + timedelta(days=transit_days)
        origin, dest = rng.sample(CITIES, 2)
        elapsed_ratio = (now - picked) / (eta - picked)
        if elapsed_ratio > 1.0 or (elapsed_ratio > 0.85 and rng.random() < 0.6):
            status = "delivered"
            delivered = min(picked + (eta - picked) * rng.uniform(0.8, 1.15), now - timedelta(hours=1))
            delivered = max(delivered, picked + timedelta(hours=4))
            last_update = delivered
        else:
            status = rng.choice(["picked_up", "in_transit", "in_transit", "out_for_delivery"])
            delivered = None
            last_update = now - timedelta(hours=rng.uniform(0.5, 12))
        data.append({
            "shipment_id": f"SHP-{i:06d}",
            "carrier": rng.choice(CARRIERS),
            "origin": origin,
            "destination": dest,
            "status": status,
            "picked_up_at": picked.strftime(FMT),
            "eta": eta.strftime(FMT),
            "delivered_at": delivered.strftime(FMT) if delivered else "",
            "last_update": last_update.strftime(FMT),
        })

    df = pd.DataFrame(data)
    rows_idx = list(range(rows))
    rng.shuffle(rows_idx)
    take = lambda k: [rows_idx.pop() for _ in range(max(1, int(rows * k)))]  # noqa: E731

    for i in take(0.012):                                  # missing carrier
        df.loc[i, "carrier"] = ""
    for i in take(0.010):                                  # missing ETA
        df.loc[i, "eta"] = "N/A"
    for i in take(0.015):                                  # invalid status values
        df.loc[i, "status"] = rng.choice(["deliverd", "unknown", "IN-TRANSIT??", "pending"])
    for i in take(0.010):                                  # delivered before pickup
        if df.loc[i, "status"] == "delivered":
            df.loc[i, "delivered_at"] = (datetime.strptime(df.loc[i, "picked_up_at"], FMT) - timedelta(hours=7)).strftime(FMT)
        else:
            df.loc[i, "status"] = "delivered"
            df.loc[i, "delivered_at"] = (datetime.strptime(df.loc[i, "picked_up_at"], FMT) - timedelta(hours=7)).strftime(FMT)
    for i in take(0.012):                                  # delivered but no timestamp
        df.loc[i, "status"] = "delivered"
        df.loc[i, "delivered_at"] = ""
    for i in take(0.008):                                  # delivered_at on an undelivered row
        df.loc[i, "status"] = "in_transit"
        df.loc[i, "delivered_at"] = df.loc[i, "last_update"]
    for i in take(0.015):                                  # stale in-transit (no update for 4 days)
        df.loc[i, "status"] = "in_transit"
        df.loc[i, "delivered_at"] = ""
        df.loc[i, "last_update"] = (now - timedelta(days=4, hours=rng.randint(0, 20))).strftime(FMT)
    for i in take(0.008):                                  # garbage timestamps
        df.loc[i, "last_update"] = rng.choice(["TBD", "not available", "32/13/2026"])
    for i in take(0.006):                                  # last_update in the future
        df.loc[i, "last_update"] = (now + timedelta(days=3)).strftime(FMT)
    for i in take(0.008):                                  # ETA before pickup
        df.loc[i, "eta"] = (datetime.strptime(df.loc[i, "picked_up_at"], FMT) - timedelta(days=1)).strftime(FMT)
    dupes = df.sample(n=max(2, int(rows * 0.01)), random_state=seed)   # duplicate shipment rows
    df = pd.concat([df, dupes], ignore_index=True)

    if drift:
        df = df.rename(columns=DRIFT_NAMES)
        df["Internal Notes"] = ""
        cols = list(df.columns)
        random.Random(seed + 1).shuffle(cols)
        df = df[cols]
    return df
