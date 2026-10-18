from __future__ import annotations

from dataclasses import replace
from math import log
from typing import Any

import pandas as pd

from .backtest import run_backtest
from .config import ExecutionConfig, StrategyConfig


def candidate_grid() -> list[StrategyConfig]:
    base = StrategyConfig()
    candidates: list[StrategyConfig] = []
    for breakout in (12, 20, 30):
        for stop_atr in (2.5, 3.0):
            for reward_r in (2.5, 3.0, 4.0):
                for session in ((0, 23), (6, 20)):
                    for slope in (24, 72):
                        candidates.append(
                            replace(
                                base,
                                breakout_lookback=breakout,
                                stop_atr=stop_atr,
                                reward_r=reward_r,
                                session_start_utc=session[0],
                                session_end_utc=session[1],
                                slope_lookback=slope,
                            )
                        )
    return candidates


def _score(result: dict[str, Any]) -> float:
    if (
        result["trades"] < 80
        or result["profit_factor"] <= 1.0
        or result["final_balance"] <= 0
    ):
        return float("-inf")
    return log(result["final_balance"]) / (1 + result["max_balance_drawdown_pct"] / 100)


def rolling_walk_forward(
    df: pd.DataFrame,
    execution: ExecutionConfig,
    risk_fraction: float = 0.02,
    first_test_year: int = 2019,
    last_test_year: int = 2025,
) -> dict[str, Any]:
    folds = []
    compounded = 100.0
    total_test_trades = 0

    for year in range(first_test_year, last_test_year + 1):
        train_start = f"{year - 3}-01-01"
        train_end = f"{year - 1}-12-31 23:59:59"
        test_start = f"{year}-01-01"
        test_end = f"{year}-12-31 23:59:59"

        best = None
        for strategy in candidate_grid():
            result = run_backtest(
                df,
                strategy,
                execution,
                risk_fraction,
                starting_balance=100.0,
                start=train_start,
                end=train_end,
            )
            score = _score(result)
            if best is None or score > best["score"]:
                best = {"strategy": strategy, "result": result, "score": score}

        if best is None or best["score"] == float("-inf"):
            continue

        test = run_backtest(
            df,
            best["strategy"],
            execution,
            risk_fraction,
            starting_balance=100.0,
            start=test_start,
            end=test_end,
        )
        compounded *= 1 + test["return_pct"] / 100
        total_test_trades += test["trades"]
        folds.append(
            {
                "year": year,
                "selected_strategy": best["result"]["strategy"],
                "train": {
                    k: v
                    for k, v in best["result"].items()
                    if k not in ("trade_log", "year_returns_pct")
                },
                "test": {
                    k: v
                    for k, v in test.items()
                    if k not in ("trade_log", "year_returns_pct")
                },
            }
        )

    return {
        "starting_balance": 100.0,
        "compounded_final_balance": compounded,
        "compounded_return_pct": (compounded / 100 - 1) * 100,
        "total_oos_trades": total_test_trades,
        "folds": folds,
    }
