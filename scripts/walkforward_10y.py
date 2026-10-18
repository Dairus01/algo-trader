import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.config import ExecutionConfig
from algo_trader.data import fetch_pinned_research_data, load_ohlc
from algo_trader.walkforward import rolling_walk_forward


if __name__ == "__main__":
    path = Path("data/XAUUSD_hourly.csv")
    if not path.exists():
        fetch_pinned_research_data(path)
    df = load_ohlc(path)
    result = rolling_walk_forward(df, ExecutionConfig())
    print(json.dumps(result, indent=2, default=str))
    Path("results").mkdir(exist_ok=True)
    Path("results/latest_walk_forward.json").write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
