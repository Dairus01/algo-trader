from pathlib import Path
import requests

URL = (
    "https://raw.githubusercontent.com/getdata-finance/"
    "xauusd-1m-ohlcv-metals-historical-data/"
    "adcd99e7e0abee51e9a5eef123935e68a8d8aa30/"
    "XAUUSD_1m.csv"
)

path = Path("data/XAUUSD_M1_2026_sample.csv")
path.parent.mkdir(parents=True, exist_ok=True)
r = requests.get(URL, timeout=180)
r.raise_for_status()
path.write_bytes(r.content)
print(path)
