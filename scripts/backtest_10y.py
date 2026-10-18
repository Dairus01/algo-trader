import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.backtest import compact_result, run_backtest
from algo_trader.config import ExecutionConfig, get_risk_profile, get_strategy_for_profile
from algo_trader.data import fetch_pinned_research_data, load_ohlc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        default="core",
        choices=["conservative", "core", "aggressive", "experimental100", "moonshot"],
    )
    parser.add_argument("--csv", default="data/XAUUSD_hourly.csv")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        fetch_pinned_research_data(path)

    df = load_ohlc(path)
    profile = get_risk_profile(args.profile)
    result = run_backtest(
        df,
        get_strategy_for_profile(profile.name),
        ExecutionConfig(),
        profile.risk_fraction,
        starting_balance=100.0,
        start="2016-04-10",
        end="2026-04-10 23:59:59",
    )
    summary = compact_result(result)
    print(json.dumps(summary, indent=2, default=str))

    Path("results").mkdir(exist_ok=True)
    Path(f"results/latest_backtest_{profile.name}.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
