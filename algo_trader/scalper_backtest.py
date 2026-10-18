from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from .scalper_config import ScalperConfig, ScalperExecutionConfig
from .scalper_strategy import prepare_scalper_features, signals_from_row


@dataclass
class ScalpPosition:
    strategy: str
    direction: int
    entry_time: pd.Timestamp
    entry_index: int
    entry: float
    stop: float
    target: float
    ounces: float


def _floor_size(value: float, step: float) -> float:
    return floor((value + 1e-12) / step) * step


def run_scalper_backtest(
    df: pd.DataFrame,
    strategy: ScalperConfig | None = None,
    execution: ScalperExecutionConfig | None = None,
    starting_balance: float = 100.0,
) -> dict[str, Any]:
    strategy = strategy or ScalperConfig()
    execution = execution or ScalperExecutionConfig()
    feat = prepare_scalper_features(df, strategy)

    balance = float(starting_balance)
    peak = balance
    max_dd = 0.0
    positions: list[ScalpPosition] = []
    trades: list[dict[str, Any]] = []
    entry_counts: dict[pd.Timestamp, int] = {}

    half_cost = execution.spread_usd / 2 + execution.slippage_usd_per_side
    warmup = max(
        strategy.ema_slow,
        strategy.rsi_period,
        strategy.atr_period,
        strategy.z_window,
        strategy.breakout_lookback,
    ) + 2

    for i in range(warmup, len(feat) - 1):
        ts = feat.index[i]
        row = feat.iloc[i]

        # Manage every open micro-position independently.
        survivors: list[ScalpPosition] = []
        for p in positions:
            if p.direction > 0:
                stop_hit = float(row["low"]) <= p.stop
                target_hit = float(row["high"]) >= p.target
            else:
                stop_hit = float(row["high"]) >= p.stop
                target_hit = float(row["low"]) <= p.target

            time_exit = (i - p.entry_index) >= strategy.max_hold_bars
            if not (stop_hit or target_hit or time_exit):
                survivors.append(p)
                continue

            # Pessimistic OHLC path when both are touched in the same minute.
            if stop_hit:
                raw_exit = p.stop
                reason = "SL"
            elif target_hit:
                raw_exit = p.target
                reason = "TP"
            else:
                raw_exit = float(row["close"])
                reason = "TIME"

            exit_price = raw_exit - half_cost if p.direction > 0 else raw_exit + half_cost
            gross = (exit_price - p.entry) * p.ounces * p.direction
            commission = execution.commission_usd_per_ounce_round_trip * p.ounces
            pnl = gross - commission
            balance += pnl

            trades.append(
                {
                    "strategy": p.strategy,
                    "direction": p.direction,
                    "entry_time": p.entry_time.isoformat(),
                    "exit_time": ts.isoformat(),
                    "entry": p.entry,
                    "exit": exit_price,
                    "stop": p.stop,
                    "target": p.target,
                    "ounces": p.ounces,
                    "pnl": pnl,
                    "reason": reason,
                    "balance": balance,
                }
            )

        positions = survivors
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        if balance <= 1.0:
            break

        atr = float(row["atr"])
        if not np.isfinite(atr) or atr < strategy.min_atr_usd:
            continue
        if not bool(row["session_ok"]):
            continue

        raw_signals = signals_from_row(row)[: strategy.max_new_positions_per_bar]
        slots = max(0, strategy.max_positions - len(positions))
        if slots <= 0:
            continue

        next_ts = feat.index[i + 1]
        next_open = float(feat.iloc[i + 1]["open"])
        stop_distance = atr * strategy.stop_atr
        if stop_distance <= 0:
            continue

        for name, direction in raw_signals[:slots]:
            risk_dollars = balance * strategy.risk_fraction_per_trade
            raw_ounces = risk_dollars / stop_distance
            ounces = _floor_size(raw_ounces, execution.volume_step_ounces)
            if ounces < execution.min_ounces:
                continue

            entry = next_open + half_cost if direction > 0 else next_open - half_cost
            stop = entry - direction * stop_distance
            target = entry + direction * stop_distance * strategy.reward_r

            positions.append(
                ScalpPosition(
                    strategy=name,
                    direction=direction,
                    entry_time=next_ts,
                    entry_index=i + 1,
                    entry=entry,
                    stop=stop,
                    target=target,
                    ounces=ounces,
                )
            )
            hour = next_ts.floor("h")
            entry_counts[hour] = entry_counts.get(hour, 0) + 1

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))

    # Count all tradable session-hours represented in the source data,
    # including hours in which no entry occurred.
    session_mask = (
        (feat.index.hour >= strategy.session_start_utc)
        & (feat.index.hour < strategy.session_end_utc)
        & (feat.index.dayofweek < 5)
    )
    session_hours = feat.index[session_mask].floor("h").unique()
    hourly = pd.Series(0, index=pd.DatetimeIndex(session_hours), dtype=float)
    for hour, count in entry_counts.items():
        if hour in hourly.index:
            hourly.loc[hour] = count

    return {
        "starting_balance": starting_balance,
        "final_balance": balance,
        "return_pct": (balance / starting_balance - 1) * 100,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gp / gl if gl else float("inf"),
        "max_balance_drawdown_pct": max_dd * 100,
        "avg_entries_per_session_hour": float(hourly.mean()) if len(hourly) else 0.0,
        "median_entries_per_session_hour": float(hourly.median()) if len(hourly) else 0.0,
        "hours_ge_13_trades_pct": (
            float((hourly >= 13).mean() * 100) if len(hourly) else 0.0
        ),
        "max_entries_in_one_hour": int(hourly.max()) if len(hourly) else 0,
        "strategy_breakdown": {
            name: sum(1 for t in trades if t["strategy"] == name)
            for name in ("momentum", "mean_reversion", "breakout")
        },
        "strategy": asdict(strategy),
        "execution": asdict(execution),
        "trade_log": trades,
    }


def compact_scalper_result(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "trade_log"}
