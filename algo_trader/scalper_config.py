from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScalperConfig:
    ema_fast: int = 5
    ema_slow: int = 13
    rsi_period: int = 7
    atr_period: int = 14
    z_window: int = 20
    breakout_lookback: int = 4

    stop_atr: float = 0.70
    reward_r: float = 1.35
    max_hold_bars: int = 5

    session_start_utc: int = 6
    session_end_utc: int = 21

    max_positions: int = 6
    max_new_positions_per_bar: int = 3

    # Per-position risk. At 0.25% and 6 concurrent positions,
    # instantaneous planned stop-risk is capped near 1.5% before costs.
    risk_fraction_per_trade: float = 0.0025

    # Loose thresholds are intentional: this engine is designed for
    # high trade frequency, then costs/edge decide whether the frequency is useful.
    momentum_return_atr: float = 0.02
    mean_reversion_z: float = 0.70
    min_atr_usd: float = 0.20


@dataclass(frozen=True)
class ScalperExecutionConfig:
    spread_usd: float = 0.35
    slippage_usd_per_side: float = 0.03
    commission_usd_per_ounce_round_trip: float = 0.07
    min_ounces: float = 0.1
    volume_step_ounces: float = 0.1
    max_spread_usd_live: float = 0.80
