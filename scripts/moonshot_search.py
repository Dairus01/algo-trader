import json
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.backtest import compact_result, run_backtest
from algo_trader.config import ExecutionConfig, StrategyConfig
from algo_trader.data import fetch_pinned_research_data, load_ohlc


CANDIDATES = [
    (0.175, 6.0, 12),
    (0.175, 6.0, 20),
    (0.20, 5.0, 12),
    (0.20, 5.0, 20),
    (0.225, 5.0, 12),
    (0.225, 5.0, 20),
    (0.25, 4.0, 12),
    (0.25, 4.0, 20),
    (0.25, 5.0, 12),
    (0.25, 5.0, 20),
    (0.30, 4.0, 12),
    (0.30, 4.0, 20),
]


def main() -> None:
    path = Path("data/XAUUSD_hourly.csv")
    if not path.exists():
        fetch_pinned_research_data(path)
    df = load_ohlc(path)
    base = StrategyConfig()
    execution = ExecutionConfig()
    rows = []

    for risk, reward_r, breakout in CANDIDATES:
        strategy = replace(base, reward_r=reward_r, breakout_lookback=breakout)
        result = compact_result(
            run_backtest(
                df, strategy, execution, risk,
                starting_balance=100.0,
                start="2016-04-10",
                end="2026-04-10 23:59:59",
            )
        )
        rows.append({
            "risk_pct": risk * 100,
            "reward_r": reward_r,
            "breakout_lookback": breakout,
            "final_balance": result["final_balance"],
            "return_pct": result["return_pct"],
            "trades": result["trades"],
            "win_rate_pct": result["win_rate_pct"],
            "profit_factor": result["profit_factor"],
            "max_drawdown_pct": result["max_balance_drawdown_pct"],
            "best_day_pct": result["best_day_pct"],
            "worst_day_pct": result["worst_day_pct"],
        })

    qualified = [
        r for r in rows
        if r["best_day_pct"] >= 100.0 and r["final_balance"] > 100.0
    ]
    payload = {
        "research_window": "2016-04-10 through 2026-04-10",
        "starting_balance": 100.0,
        "rows": rows,
        "best_surviving_100pct_day_candidate": (
            max(qualified, key=lambda r: r["final_balance"]) if qualified else None
        ),
    }
    Path("results").mkdir(exist_ok=True)
    Path("results/moonshot_search.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
