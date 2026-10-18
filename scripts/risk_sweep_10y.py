import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.backtest import compact_result, run_backtest
from algo_trader.config import ExecutionConfig, StrategyConfig
from algo_trader.data import fetch_pinned_research_data, load_ohlc


RISK_LEVELS = [0.02, 0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.225, 0.25, 0.275, 0.30, 0.325, 0.35]


def main() -> None:
    path = Path("data/XAUUSD_hourly.csv")
    if not path.exists():
        fetch_pinned_research_data(path)

    df = load_ohlc(path)
    strategy = StrategyConfig()
    execution = ExecutionConfig()

    rows = []
    for risk in RISK_LEVELS:
        result = compact_result(
            run_backtest(
                df,
                strategy,
                execution,
                risk,
                starting_balance=100.0,
                start="2016-04-10",
                end="2026-04-10 23:59:59",
            )
        )
        rows.append(
            {
                "risk_pct": risk * 100,
                "final_balance": result["final_balance"],
                "return_pct": result["return_pct"],
                "trades": result["trades"],
                "win_rate_pct": result["win_rate_pct"],
                "profit_factor": result["profit_factor"],
                "max_drawdown_pct": result["max_balance_drawdown_pct"],
                "best_day_pct": result["best_day_pct"],
                "worst_day_pct": result["worst_day_pct"],
            }
        )

    survivors = [r for r in rows if r["final_balance"] > 100.0]
    hundred_day = [r for r in rows if r["best_day_pct"] >= 100.0]
    payload = {
        "research_window": "2016-04-10 through 2026-04-10",
        "starting_balance": 100.0,
        "rows": rows,
        "best_profitable_profile": max(survivors, key=lambda x: x["final_balance"]) if survivors else None,
        "lowest_risk_with_100pct_day": min(hundred_day, key=lambda x: x["risk_pct"]) if hundred_day else None,
    }

    Path("results").mkdir(exist_ok=True)
    Path("results/intermediate_risk_sweep.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
