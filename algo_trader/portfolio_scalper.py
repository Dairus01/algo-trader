from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import math

import numpy as np
import pandas as pd


# All-in round-trip price-cost assumptions used for the research screen.
# These deliberately include an allowance for spread + commission-equivalent + slippage.
ROUND_TRIP_PRICE_COST = {
    "XAUUSD": 0.50,
    "EURUSD": 0.00012,
    "GBPUSD": 0.00018,
    "USDJPY": 0.015,
    "EURGBP": 0.00014,
    "AUDNZD": 0.00022,
}


@dataclass(frozen=True)
class PortfolioVariant:
    name: str
    mode: str
    ema_fast: int = 5
    ema_slow: int = 13
    rsi_period: int = 7
    atr_period: int = 14
    stop_atr: float = 1.5
    reward_r: float = 2.5
    max_hold_bars: int = 12
    session_start_utc: int = 6
    session_end_utc: int = 21
    max_positions_per_symbol: int = 8

    momentum_atr: float = 0.05
    momentum_rsi_long: float = 53.0
    momentum_rsi_short: float = 47.0

    z_window: int = 20
    z_threshold: float = 1.2
    mr_rsi_low: float = 38.0
    mr_rsi_high: float = 62.0

    breakout_lookback: int = 4


@dataclass
class _Position:
    direction: int
    entry_index: int
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    stop_distance: float
    strategy: str


def normalize_m1(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    if not isinstance(x.index, pd.RangeIndex):
        x = x.reset_index()
    x.columns = [str(c).lower() for c in x.columns]

    if "datetime" not in x.columns:
        for candidate in ("time", "timestamp", "date", "index"):
            if candidate in x.columns:
                x = x.rename(columns={candidate: "datetime"})
                break
    if "datetime" not in x.columns:
        raise ValueError(f"No datetime column: {list(x.columns)}")

    x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
    for c in ("open", "high", "low", "close"):
        x[c] = pd.to_numeric(x[c], errors="coerce")

    x = x.dropna(subset=["datetime", "open", "high", "low", "close"])
    return x.set_index("datetime").sort_index()[["open", "high", "low", "close"]]


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0.0)
    loss = -d.clip(upper=0.0)
    ag = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ag / al.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - pc).abs(),
            (df["low"] - pc).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def features(df: pd.DataFrame, cfg: PortfolioVariant) -> pd.DataFrame:
    x = df.copy()
    x["ef"] = _ema(x["close"], cfg.ema_fast)
    x["es"] = _ema(x["close"], cfg.ema_slow)
    x["rsi"] = _rsi(x["close"], cfg.rsi_period)
    x["atr"] = _atr(x, cfg.atr_period)
    x["ret_atr"] = x["close"].diff() / x["atr"].replace(0.0, np.nan)

    mean = x["close"].rolling(cfg.z_window).mean()
    std = x["close"].rolling(cfg.z_window).std(ddof=0)
    x["z"] = (x["close"] - mean) / std.replace(0.0, np.nan)

    x["prev_high"] = x["high"].shift(1).rolling(cfg.breakout_lookback).max()
    x["prev_low"] = x["low"].shift(1).rolling(cfg.breakout_lookback).min()

    hour = x.index.hour
    x["session"] = (
        (hour >= cfg.session_start_utc)
        & (hour < cfg.session_end_utc)
        & (x.index.dayofweek < 5)
    )
    return x


def _signals(row: pd.Series, cfg: PortfolioVariant) -> list[tuple[str, int]]:
    if not bool(row["session"]):
        return []

    out: list[tuple[str, int]] = []
    modes = {cfg.mode} if cfg.mode != "ensemble" else {"momentum", "mean_reversion", "breakout"}

    if "momentum" in modes:
        if (
            row["ef"] > row["es"]
            and row["ret_atr"] > cfg.momentum_atr
            and row["rsi"] > cfg.momentum_rsi_long
        ):
            out.append(("momentum", 1))
        elif (
            row["ef"] < row["es"]
            and row["ret_atr"] < -cfg.momentum_atr
            and row["rsi"] < cfg.momentum_rsi_short
        ):
            out.append(("momentum", -1))

    if "mean_reversion" in modes:
        if row["z"] < -cfg.z_threshold and row["rsi"] < cfg.mr_rsi_low:
            out.append(("mean_reversion", 1))
        elif row["z"] > cfg.z_threshold and row["rsi"] > cfg.mr_rsi_high:
            out.append(("mean_reversion", -1))

    if "breakout" in modes:
        if row["close"] > row["prev_high"] and row["ef"] >= row["es"]:
            out.append(("breakout", 1))
        elif row["close"] < row["prev_low"] and row["ef"] <= row["es"]:
            out.append(("breakout", -1))

    return out


def simulate_symbol(
    symbol: str,
    df: pd.DataFrame,
    cfg: PortfolioVariant,
) -> list[dict[str, Any]]:
    x = features(df, cfg)
    positions: list[_Position] = []
    trades: list[dict[str, Any]] = []
    cost_price = ROUND_TRIP_PRICE_COST[symbol]
    warmup = max(cfg.ema_slow, cfg.atr_period, cfg.rsi_period, cfg.z_window, cfg.breakout_lookback) + 3

    for i in range(warmup, len(x) - 1):
        row = x.iloc[i]
        ts = x.index[i]

        survivors: list[_Position] = []
        for p in positions:
            if p.direction > 0:
                stop_hit = float(row["low"]) <= p.stop
                target_hit = float(row["high"]) >= p.target
            else:
                stop_hit = float(row["high"]) >= p.stop
                target_hit = float(row["low"]) <= p.target

            timed = (i - p.entry_index) >= cfg.max_hold_bars
            if not (stop_hit or target_hit or timed):
                survivors.append(p)
                continue

            # Pessimistic path when both stop and target fall in one M1 bar.
            if stop_hit:
                raw_exit = p.stop
                reason = "SL"
            elif target_hit:
                raw_exit = p.target
                reason = "TP"
            else:
                raw_exit = float(row["close"])
                reason = "TIME"

            gross_r = p.direction * (raw_exit - p.entry) / p.stop_distance
            cost_r = cost_price / p.stop_distance
            net_r = gross_r - cost_r

            trades.append(
                {
                    "symbol": symbol,
                    "strategy": p.strategy,
                    "direction": p.direction,
                    "entry_time": p.entry_time,
                    "exit_time": ts,
                    "gross_r": gross_r,
                    "cost_r": cost_r,
                    "net_r": net_r,
                    "reason": reason,
                }
            )

        positions = survivors

        if len(positions) >= cfg.max_positions_per_symbol:
            continue

        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0:
            continue

        signals = _signals(row, cfg)
        if not signals:
            continue

        next_open = float(x.iloc[i + 1]["open"])
        next_ts = x.index[i + 1]
        stop_distance = atr * cfg.stop_atr
        slots = cfg.max_positions_per_symbol - len(positions)

        for strategy_name, direction in signals[:slots]:
            entry = next_open
            positions.append(
                _Position(
                    direction=direction,
                    entry_index=i + 1,
                    entry_time=next_ts,
                    entry=entry,
                    stop=entry - direction * stop_distance,
                    target=entry + direction * stop_distance * cfg.reward_r,
                    stop_distance=stop_distance,
                    strategy=strategy_name,
                )
            )

    return trades


def portfolio_metrics(
    trades: list[dict[str, Any]],
    cfg: PortfolioVariant,
    starting_balance: float = 100.0,
    risk_fraction: float = 0.0005,
) -> dict[str, Any]:
    if not trades:
        return {
            "final_balance": starting_balance,
            "return_pct": 0.0,
            "trades": 0,
            "profit_factor_r": 0.0,
            "win_rate_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_entries_per_session_hour": 0.0,
            "hours_ge_13_trades_pct": 0.0,
        }

    t = pd.DataFrame(trades).sort_values(["exit_time", "entry_time"]).reset_index(drop=True)
    r = t["net_r"].to_numpy(float)
    gp = float(r[r > 0].sum())
    gl = float(-r[r < 0].sum())
    pf = gp / gl if gl > 0 else float("inf")

    # Fixed-fraction sequential screening equity. This slightly simplifies
    # overlapping-position accounting but does not affect trade PF or frequency.
    equity = float(starting_balance)
    peak = equity
    max_dd = 0.0
    for rr in r:
        equity *= max(0.0, 1.0 + risk_fraction * rr)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    entries = pd.to_datetime(t["entry_time"], utc=True)
    counts = entries.groupby(entries.dt.floor("h")).size()

    first = entries.min().floor("h")
    last = entries.max().floor("h")
    all_hours = pd.date_range(first, last, freq="h", tz="UTC")
    session_hours = all_hours[
        (all_hours.hour >= cfg.session_start_utc)
        & (all_hours.hour < cfg.session_end_utc)
        & (all_hours.dayofweek < 5)
    ]
    hourly = pd.Series(0, index=session_hours, dtype=float)
    common = hourly.index.intersection(counts.index)
    hourly.loc[common] = counts.loc[common].astype(float)

    by_symbol = {}
    for symbol, g in t.groupby("symbol"):
        rr = g["net_r"].to_numpy(float)
        sgp = float(rr[rr > 0].sum())
        sgl = float(-rr[rr < 0].sum())
        by_symbol[symbol] = {
            "trades": int(len(g)),
            "win_rate_pct": float((rr > 0).mean() * 100),
            "profit_factor_r": float(sgp / sgl) if sgl > 0 else float("inf"),
            "mean_net_r": float(rr.mean()),
            "mean_cost_r": float(g["cost_r"].mean()),
        }

    return {
        "variant": asdict(cfg),
        "starting_balance": starting_balance,
        "risk_fraction_per_trade": risk_fraction,
        "final_balance": equity,
        "return_pct": (equity / starting_balance - 1.0) * 100,
        "trades": int(len(t)),
        "wins": int((r > 0).sum()),
        "losses": int((r <= 0).sum()),
        "win_rate_pct": float((r > 0).mean() * 100),
        "profit_factor_r": float(pf),
        "mean_net_r": float(r.mean()),
        "max_drawdown_pct": float(max_dd * 100),
        "avg_entries_per_session_hour": float(hourly.mean()) if len(hourly) else 0.0,
        "median_entries_per_session_hour": float(hourly.median()) if len(hourly) else 0.0,
        "hours_ge_13_trades_pct": float((hourly >= 13).mean() * 100) if len(hourly) else 0.0,
        "max_entries_one_hour": int(hourly.max()) if len(hourly) else 0,
        "by_symbol": by_symbol,
    }
