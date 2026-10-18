from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

PINNED_DATA_URL = (
    "https://raw.githubusercontent.com/anusha24-bit/Shiftguard/"
    "e85ee01ecceab6bb3c7e4fba8c7dff1642d6209e/"
    "data/raw/price/hourly/XAUUSD_hourly.csv"
)


def fetch_pinned_research_data(path: str | Path = "data/XAUUSD_hourly.csv") -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(PINNED_DATA_URL, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


def load_ohlc(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    aliases = {
        "datetime_utc": "time",
        "datetime": "time",
        "Datetime": "time",
        "timestamp": "time",
    }
    for source, target in aliases.items():
        if source in df.columns and "time" not in df.columns:
            df = df.rename(columns={source: target})
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").drop_duplicates("time").set_index("time")
