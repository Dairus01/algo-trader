from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScalperConfig:
    ema_fast: int = 5
    ema_slow: int = 13
    rsi_period: int = 7
    atr_period: int = 14
    z_window: int = 20
    breakout_lookback: int = 3

    stop_atr: float = 1.20
    reward_r: float = 1.80
    max_hold_bars: int = 8

    session_start_utc: int = 6
    session_end_utc: int = 21

    # Designed for pyramided micro-trades rather than one H1 position.
    max_positions: int = 24
    max_new_positions_per_bar: int = 3
    risk_fraction_per_trade: float = 0.0025

    momentum_return_atr: float = 0.0
    momentum_rsi_long: float = 50.0
    momentum_rsi_short: float = 50.0

    mean_reversion_z: float = 1.20
    mean_reversion_rsi_low: float = 35.0
    mean_reversion_rsi_high: float = 65.0

    min_atr_usd: float = 0.20
    enable_momentum: bool = True
    enable_mean_reversion: bool = True
    enable_breakout: bool = True


@dataclass(frozen=True)
class ScalperExecutionConfig:
    spread_usd: float = 0.35
    slippage_usd_per_side: float = 0.03
    commission_usd_per_ounce_round_trip: float = 0.07
    min_ounces: float = 0.1
    volume_step_ounces: float = 0.1
    max_spread_usd_live: float = 0.80
