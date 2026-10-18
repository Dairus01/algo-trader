import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.data import load_ohlc
from algo_trader.scalper_backtest import compact_scalper_result, run_scalper_backtest

path = Path("data/XAUUSD_M1_2026_sample.csv")
if not path.exists():
    raise SystemExit("Run: python scripts/fetch_m1_sample.py")

df = load_ohlc(path)
result = compact_scalper_result(run_scalper_backtest(df, starting_balance=100.0))
Path("results").mkdir(exist_ok=True)
Path("results/scalper_m1_sample.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps(result, indent=2))
