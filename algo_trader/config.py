from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    breakout_lookback: int = 12
    atr_period: int = 14
    stop_atr: float = 3.0
    reward_r: float = 3.0
    ema_fast: int = 50
    ema_mid: int = 100
    ema_slow: int = 200
    slope_lookback: int = 72
    session_start_utc: int = 6
    session_end_utc: int = 20
    max_hold_bars: int = 72


@dataclass(frozen=True)
class ExecutionConfig:
    spread_usd: float = 0.35
    slippage_usd_per_side: float = 0.05
    commission_usd_per_ounce_round_trip: float = 0.07
    min_ounces: float = 0.1
    volume_step_ounces: float = 0.1
    max_spread_usd_live: float = 1.50


@dataclass(frozen=True)
class RiskProfile:
    name: str
    risk_fraction: float
    description: str
    requires_total_loss_ack: bool = False


RISK_PROFILES = {
    "conservative": RiskProfile("conservative", 0.01, "Lower-risk research profile."),
    "core": RiskProfile("core", 0.02, "Default profile validated in the 10-year research."),
    "aggressive": RiskProfile("aggressive", 0.05, "High-drawdown research profile."),
    "moonshot": RiskProfile(
        "moonshot",
        0.35,
        "Historical +100% single-day capability with near-total decade ruin.",
        True,
    ),
}


def get_risk_profile(name: str) -> RiskProfile:
    key = name.lower().strip()
    if key not in RISK_PROFILES:
        raise ValueError(f"Unknown risk profile: {name}. Choose from {sorted(RISK_PROFILES)}")
    return RISK_PROFILES[key]
