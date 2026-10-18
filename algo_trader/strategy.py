from __future__ import annotations

import pandas as pd

from .config import StrategyConfig
from .indicators import ema, wilder_atr


def prepare_features(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_mid"] = ema(out["close"], cfg.ema_mid)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["atr"] = wilder_atr(out, cfg.atr_period)

    # Strictly causal. The breakout channel excludes the current candle.
    out["previous_breakout_high"] = (
        out["high"].shift(1).rolling(cfg.breakout_lookback).max()
    )
    out["slow_ema_rising"] = out["ema_slow"] > out["ema_slow"].shift(cfg.slope_lookback)

    hours = out.index.hour
    session_ok = (hours >= cfg.session_start_utc) & (hours < cfg.session_end_utc)

    out["long_signal"] = (
        session_ok
        & (out["ema_fast"] > out["ema_slow"])
        & (out["ema_mid"] > out["ema_slow"])
        & out["slow_ema_rising"]
        & (out["close"] > out["previous_breakout_high"])
        & out["atr"].notna()
    )
    return out
