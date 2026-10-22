import pandas as pd
import pytest

from dq_agent.config import load_config

NOW = pd.Timestamp("2026-10-12 12:00:00", tz="UTC")


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def now():
    return NOW


def make_clean(n: int = 20) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "shipment_id": f"SHP-{i:03d}",
            "carrier": "Northwind Freight",
            "origin": "Delhi",
            "destination": "Pune",
            "status": "delivered",
            "picked_up_at": "2026-10-08 08:00:00",
            "eta": "2026-10-11 08:00:00",
            "delivered_at": "2026-10-10 10:00:00",
            "last_update": "2026-10-12 09:00:00",
        })
    return pd.DataFrame(rows)
