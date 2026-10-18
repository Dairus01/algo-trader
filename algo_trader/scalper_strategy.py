from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import ema, wilder_atr
from .scalper_config import ScalperConfig


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def prepare_scalper_features(df: pd.DataFrame, cfg: ScalperConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["atr"] = wilder_atr(out, cfg.atr_period)
    out["rsi"] = _rsi(out["close"], cfg.rsi_period)

    ret = out["close"].diff()
    out["ret_atr"] = ret / out["atr"].replace(0.0, np.nan)

    mean = out["close"].rolling(cfg.z_window).mean()
    std = out["close"].rolling(cfg.z_window).std(ddof=0)
    out["zscore"] = (out["close"] - mean) / std.replace(0.0, np.nan)

    out["prev_high"] = out["high"].shift(1).rolling(cfg.breakout_lookback).max()
    out["prev_low"] = out["low"].shift(1).rolling(cfg.breakout_lookback).min()

    hours = out.index.hour
    out["session_ok"] = (hours >= cfg.session_start_utc) & (hours < cfg.session_end_utc)

    # Strategy 1: one-minute momentum continuation.
    out["mom_long"] = (
        out["session_ok"]
        & (out["ema_fast"] > out["ema_slow"])
        & (out["ret_atr"] > cfg.momentum_return_atr)
        & (out["rsi"] > 52)
    )
    out["mom_short"] = (
        out["session_ok"]
        & (out["ema_fast"] < out["ema_slow"])
        & (out["ret_atr"] < -cfg.momentum_return_atr)
        & (out["rsi"] < 48)
    )

    # Strategy 2: short-horizon mean reversion.
    out["mr_long"] = (
        out["session_ok"]
        & (out["zscore"] < -cfg.mean_reversion_z)
        & (out["rsi"] < 42)
    )
    out["mr_short"] = (
        out["session_ok"]
        & (out["zscore"] > cfg.mean_reversion_z)
        & (out["rsi"] > 58)
    )

    # Strategy 3: micro-breakout.
    out["bo_long"] = (
        out["session_ok"]
        & (out["close"] > out["prev_high"])
        & (out["ema_fast"] >= out["ema_slow"])
    )
    out["bo_short"] = (
        out["session_ok"]
        & (out["close"] < out["prev_low"])
        & (out["ema_fast"] <= out["ema_slow"])
    )

    return out


def signals_from_row(row: pd.Series) -> list[tuple[str, int]]:
    signals: list[tuple[str, int]] = []
    if bool(row.get("mom_long", False)):
        signals.append(("momentum", 1))
    elif bool(row.get("mom_short", False)):
        signals.append(("momentum", -1))

    if bool(row.get("mr_long", False)):
        signals.append(("mean_reversion", 1))
    elif bool(row.get("mr_short", False)):
        signals.append(("mean_reversion", -1))

    if bool(row.get("bo_long", False)):
        signals.append(("breakout", 1))
    elif bool(row.get("bo_short", False)):
        signals.append(("breakout", -1))

    return signals
