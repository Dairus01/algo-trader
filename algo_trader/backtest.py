from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from .config import ExecutionConfig, StrategyConfig
from .strategy import prepare_features


@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_index: int
    entry: float
    stop: float
    target: float
    ounces: float


def _floor_size(value: float, step: float) -> float:
    return floor((value + 1e-12) / step) * step


def _utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyConfig,
    execution: ExecutionConfig,
    risk_fraction: float,
    starting_balance: float = 100.0,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    if not 0 < risk_fraction < 1:
        raise ValueError("risk_fraction must be between 0 and 1")
    if starting_balance <= 0:
        raise ValueError("starting_balance must be positive")

    feat = prepare_features(df, strategy)
    start_ts = _utc_timestamp(start) if start is not None else feat.index[0]
    end_ts = _utc_timestamp(end) if end is not None else feat.index[-1]

    balance = float(starting_balance)
    peak_balance = balance
    max_drawdown = 0.0
    position: Position | None = None
    trades: list[dict[str, Any]] = []
    daily_factors: dict[str, float] = {}
    year_factors: dict[str, float] = {}

    half_cost = execution.spread_usd / 2 + execution.slippage_usd_per_side
    indices = feat.index
    warmup = max(
        strategy.ema_slow,
        strategy.slope_lookback,
        strategy.breakout_lookback,
        strategy.atr_period,
    ) + 2

    for i in range(warmup, len(feat) - 1):
        ts = indices[i]
        row = feat.iloc[i]

        if position is not None:
            stop_hit = float(row["low"]) <= position.stop
            target_hit = float(row["high"]) >= position.target
            time_exit = (i - position.entry_index) >= strategy.max_hold_bars

            if stop_hit or target_hit or time_exit:
                # Conservative same-bar assumption: if both levels were crossed,
                # count the stop first because H1 OHLC cannot reveal path order.
                if stop_hit:
                    raw_exit = position.stop
                    reason = "SL"
                elif target_hit:
                    raw_exit = position.target
                    reason = "TP"
                else:
                    raw_exit = float(row["close"])
                    reason = "TIME"

                exit_price = raw_exit - half_cost
                commission = execution.commission_usd_per_ounce_round_trip * position.ounces
                pnl = (exit_price - position.entry) * position.ounces - commission
                before = balance
                balance += pnl

                trade_factor = balance / before if before > 0 else 0.0
                day = position.entry_time.strftime("%Y-%m-%d")
                year = position.entry_time.strftime("%Y")
                daily_factors[day] = daily_factors.get(day, 1.0) * trade_factor
                year_factors[year] = year_factors.get(year, 1.0) * trade_factor

                trades.append(
                    {
                        "entry_time": position.entry_time.isoformat(),
                        "exit_time": ts.isoformat(),
                        "entry": position.entry,
                        "exit": exit_price,
                        "stop": position.stop,
                        "target": position.target,
                        "ounces": position.ounces,
                        "pnl": pnl,
                        "reason": reason,
                        "balance": balance,
                    }
                )
                position = None
                if balance <= 1.0:
                    break

        peak_balance = max(peak_balance, balance)
        if peak_balance > 0:
            max_drawdown = max(max_drawdown, (peak_balance - balance) / peak_balance)

        if position is not None or ts < start_ts or ts > end_ts:
            continue
        if not bool(row["long_signal"]):
            continue

        next_ts = indices[i + 1]
        if next_ts > end_ts:
            continue

        stop_distance = float(row["atr"]) * strategy.stop_atr
        if not np.isfinite(stop_distance) or stop_distance <= 0:
            continue

        entry = float(feat.iloc[i + 1]["open"]) + half_cost
        risk_dollars = balance * risk_fraction
        raw_ounces = risk_dollars / stop_distance
        ounces = _floor_size(raw_ounces, execution.volume_step_ounces)
        if ounces < execution.min_ounces:
            continue

        position = Position(
            entry_time=next_ts,
            entry_index=i + 1,
            entry=entry,
            stop=entry - stop_distance,
            target=entry + stop_distance * strategy.reward_r,
            ounces=ounces,
        )

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    best_day = max((v - 1 for v in daily_factors.values()), default=0.0)
    worst_day = min((v - 1 for v in daily_factors.values()), default=0.0)

    return {
        "starting_balance": starting_balance,
        "final_balance": balance,
        "return_pct": (balance / starting_balance - 1) * 100,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(trades) if trades else 0.0,
        "profit_factor": profit_factor,
        "max_balance_drawdown_pct": max_drawdown * 100,
        "best_day_pct": best_day * 100,
        "worst_day_pct": worst_day * 100,
        "year_returns_pct": {year: (factor - 1) * 100 for year, factor in year_factors.items()},
        "risk_fraction": risk_fraction,
        "strategy": asdict(strategy),
        "execution": asdict(execution),
        "trade_log": trades,
    }


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "trade_log"}
