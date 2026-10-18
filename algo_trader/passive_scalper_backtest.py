from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from .scalper_config import ScalperConfig, ScalperExecutionConfig
from .scalper_strategy import prepare_scalper_features, signals_from_row


@dataclass
class Position:
    strategy: str
    direction: int
    entry_time: pd.Timestamp
    entry_index: int
    entry: float
    stop: float
    target: float
    ounces: float


@dataclass
class Pending:
    strategy: str
    direction: int
    signal_time: pd.Timestamp
    reference_mid: float
    atr: float


def _floor_size(value: float, step: float) -> float:
    return floor((value + 1e-12) / step) * step


def run_passive_scalper_backtest(
    df: pd.DataFrame,
    strategy: ScalperConfig,
    execution: ScalperExecutionConfig,
    entry_offset_atr: float,
    starting_balance: float = 100.0,
) -> dict[str, Any]:
    feat = prepare_scalper_features(df, strategy)
    balance = float(starting_balance)
    peak = balance
    max_dd = 0.0
    positions: list[Position] = []
    pending: list[Pending] = []
    trades: list[dict[str, Any]] = []
    fills_per_hour: dict[pd.Timestamp, int] = {}

    half_spread = execution.spread_usd / 2
    exit_cost = half_spread + execution.slippage_usd_per_side
    warmup = max(strategy.z_window, strategy.atr_period, strategy.ema_slow) + 2

    for i in range(warmup, len(feat) - 1):
        ts = feat.index[i]
        row = feat.iloc[i]

        # Exit existing positions first. Newly filled limits below cannot exit
        # until a later M1 bar, avoiding impossible same-bar path assumptions.
        survivors: list[Position] = []
        for p in positions:
            if p.direction > 0:
                sl_hit = float(row["low"]) <= p.stop
                tp_hit = float(row["high"]) >= p.target
            else:
                sl_hit = float(row["high"]) >= p.stop
                tp_hit = float(row["low"]) <= p.target
            timed = (i - p.entry_index) >= strategy.max_hold_bars

            if not (sl_hit or tp_hit or timed):
                survivors.append(p)
                continue

            if sl_hit:
                raw = p.stop
                reason = "SL"
            elif tp_hit:
                raw = p.target
                reason = "TP"
            else:
                raw = float(row["close"])
                reason = "TIME"

            exit_price = raw - exit_cost if p.direction > 0 else raw + exit_cost
            pnl = (exit_price - p.entry) * p.ounces * p.direction
            pnl -= execution.commission_usd_per_ounce_round_trip * p.ounces
            balance += pnl
            trades.append({
                "strategy": p.strategy,
                "direction": p.direction,
                "entry_time": p.entry_time.isoformat(),
                "exit_time": ts.isoformat(),
                "pnl": pnl,
                "reason": reason,
                "balance": balance,
            })

        positions = survivors
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak)
        if balance <= 1:
            break

        # One-bar-valid passive limits created on the prior closed candle.
        slots = max(0, strategy.max_positions - len(positions))
        for order in pending[:]:
            if slots <= 0:
                break
            desired_mid = order.reference_mid - order.direction * entry_offset_atr * order.atr

            # Mid OHLC must cross far enough that the broker ask/bid can execute
            # the limit. The fill price therefore still includes half the spread.
            if order.direction > 0:
                filled = float(row["low"]) <= desired_mid
                entry = desired_mid + half_spread
            else:
                filled = float(row["high"]) >= desired_mid
                entry = desired_mid - half_spread
            if not filled:
                continue

            stop_distance = order.atr * strategy.stop_atr
            risk_dollars = balance * strategy.risk_fraction_per_trade
            ounces = _floor_size(risk_dollars / stop_distance, execution.volume_step_ounces)
            if ounces < execution.min_ounces:
                continue

            positions.append(Position(
                strategy=order.strategy,
                direction=order.direction,
                entry_time=ts,
                entry_index=i,
                entry=entry,
                stop=entry - order.direction * stop_distance,
                target=entry + order.direction * stop_distance * strategy.reward_r,
                ounces=ounces,
            ))
            hour = ts.floor("h")
            fills_per_hour[hour] = fills_per_hour.get(hour, 0) + 1
            slots -= 1

        # Pending limits expire after this bar.
        pending = []

        atr = float(row["atr"])
        if not np.isfinite(atr) or atr < strategy.min_atr_usd or not bool(row["session_ok"]):
            continue

        for name, direction in signals_from_row(row)[: strategy.max_new_positions_per_bar]:
            pending.append(Pending(
                strategy=name,
                direction=direction,
                signal_time=ts,
                reference_mid=float(row["close"]),
                atr=atr,
            ))

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))

    mask = (
        (feat.index.hour >= strategy.session_start_utc)
        & (feat.index.hour < strategy.session_end_utc)
        & (feat.index.dayofweek < 5)
    )
    session_hours = feat.index[mask].floor("h").unique()
    hourly = pd.Series(0, index=pd.DatetimeIndex(session_hours), dtype=float)
    for hour, count in fills_per_hour.items():
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
        "hours_ge_13_trades_pct": float((hourly >= 13).mean() * 100) if len(hourly) else 0.0,
        "max_entries_in_one_hour": int(hourly.max()) if len(hourly) else 0,
        "entry_offset_atr": entry_offset_atr,
    }
