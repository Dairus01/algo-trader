from pathlib import Path
import requests

COMMIT = "c28cb25970d7a72f6a1decc1aadbc358219a6c91"
SOURCE = "Sai310421/xauusd-data"
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "EURGBP", "AUDNZD"]

root = Path("data/multisymbol_m1")
root.mkdir(parents=True, exist_ok=True)

for symbol in SYMBOLS:
    name = f"{symbol}_M1_90d_20260526.parquet"
    url = f"https://raw.githubusercontent.com/{SOURCE}/{COMMIT}/parquet/{name}"
    out = root / name
    if out.exists() and out.stat().st_size > 100_000:
        print(f"cached {out}")
        continue
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    out.write_bytes(response.content)
    print(f"{symbol}: {len(response.content):,} bytes -> {out}")
