import json
from dataclasses import replace
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.backtest import compact_result, run_backtest
from algo_trader.config import ExecutionConfig, StrategyConfig
from algo_trader.data import load_ohlc

HOLDOUT_URL = (
    "https://raw.githubusercontent.com/getdata-finance/"
    "xauusd-1h-ohlcv-metals-historical-data/"
    "0230e718fbeff0cf82f1cdf13991d46d8d88f98e/"
    "XAUUSD_1h.csv"
)


def fetch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(HOLDOUT_URL, timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)


def run_case(df, name, strategy, risk):
    result = compact_result(run_backtest(
        df,
        strategy,
        ExecutionConfig(),
        risk,
        starting_balance=100.0,
        start="2026-04-11",
        end="2026-09-09 23:59:59",
    ))
    return {
        "name": name,
        "risk_pct": risk * 100,
        "strategy": result["strategy"],
        "final_balance": result["final_balance"],
        "return_pct": result["return_pct"],
        "trades": result["trades"],
        "win_rate_pct": result["win_rate_pct"],
        "profit_factor": result["profit_factor"],
        "max_drawdown_pct": result["max_balance_drawdown_pct"],
        "best_day_pct": result["best_day_pct"],
        "worst_day_pct": result["worst_day_pct"],
    }


def main():
    path = Path("data/XAUUSD_holdout_2026.csv")
    if not path.exists():
        fetch(path)
    df = load_ohlc(path)
    base = StrategyConfig()
    cases = [
        run_case(df, "core_2pct_3R", base, 0.02),
        run_case(df, "aggressive_5pct_3R", base, 0.05),
        run_case(df, "fixed_10pct_3R", base, 0.10),
        run_case(
            df,
            "experimental100_12_5pct_9R",
            replace(base, reward_r=9.0, breakout_lookback=12),
            0.125,
        ),
    ]
    payload = {
        "selection_cutoff": "2026-04-10",
        "holdout_window": "2026-04-11 through 2026-09-09",
        "source_commit": "0230e718fbeff0cf82f1cdf13991d46d8d88f98e",
        "source_repo": "getdata-finance/xauusd-1h-ohlcv-metals-historical-data",
        "starting_balance": 100.0,
        "cases": cases,
    }
    Path("results").mkdir(exist_ok=True)
    Path("results/forward_holdout_2026.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
