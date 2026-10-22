"""Data sources: local CSV (demo/testing) and Google Sheets (live)."""
from __future__ import annotations

import json
import os

import pandas as pd


def read_csv(path: str) -> pd.DataFrame:
    # Everything is read as text so nothing is silently coerced before the checks run.
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        h = h.strip() or "unnamed"
        if h in seen:
            seen[h] += 1
            out.append(f"{h}.{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def read_sheet(sheet_id: str, worksheet: str | None = None, env: dict | None = None) -> pd.DataFrame:
    """Read a Google Sheet via a service account. The sheet must be shared with its email."""
    env = env if env is not None else os.environ
    try:
        import gspread
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Google Sheets support needs: pip install 'shipment-dq-agent[sheets]'") from exc

    raw = env.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        client = gspread.service_account_from_dict(json.loads(raw))
    else:
        key_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
        client = gspread.service_account(filename=key_path)

    sheet = client.open_by_key(sheet_id)
    ws = sheet.worksheet(worksheet) if worksheet else sheet.sheet1
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _dedupe_headers([str(h) for h in values[0]])
    width = len(headers)
    rows = [(r + [""] * width)[:width] for r in values[1:]]
    return pd.DataFrame(rows, columns=headers, dtype=str)
