import json
from dataclasses import replace
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.portfolio_scalper import (
    PortfolioVariant,
    normalize_m1,
    simulate_symbol,
    portfolio_metrics,
    ROUND_TRIP_PRICE_COST,
)

DATA = Path("data/multisymbol_m1")
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "EURGBP", "AUDNZD"]

variants = [
    PortfolioVariant(
        name="mom_loose_2R",
        mode="momentum",
        momentum_atr=0.02,
        momentum_rsi_long=51,
        momentum_rsi_short=49,
        stop_atr=1.5,
        reward_r=2.0,
        max_hold_bars=10,
    ),
    PortfolioVariant(
        name="mom_med_25R",
        mode="momentum",
        momentum_atr=0.06,
        momentum_rsi_long=54,
        momentum_rsi_short=46,
        stop_atr=1.6,
        reward_r=2.5,
        max_hold_bars=15,
    ),
    PortfolioVariant(
        name="mom_strict_3R",
        mode="momentum",
        momentum_atr=0.10,
        momentum_rsi_long=57,
        momentum_rsi_short=43,
        stop_atr=1.8,
        reward_r=3.0,
        max_hold_bars=20,
    ),
    PortfolioVariant(
        name="mr_loose",
        mode="mean_reversion",
        z_threshold=0.8,
        mr_rsi_low=45,
        mr_rsi_high=55,
        stop_atr=1.2,
        reward_r=1.6,
        max_hold_bars=8,
    ),
    PortfolioVariant(
        name="mr_medium",
        mode="mean_reversion",
        z_threshold=1.2,
        mr_rsi_low=38,
        mr_rsi_high=62,
        stop_atr=1.4,
        reward_r=2.0,
        max_hold_bars=12,
    ),
    PortfolioVariant(
        name="mr_strict",
        mode="mean_reversion",
        z_threshold=1.7,
        mr_rsi_low=30,
        mr_rsi_high=70,
        stop_atr=1.5,
        reward_r=2.5,
        max_hold_bars=16,
    ),
    PortfolioVariant(
        name="breakout_3",
        mode="breakout",
        breakout_lookback=3,
        stop_atr=1.5,
        reward_r=2.5,
        max_hold_bars=15,
    ),
    PortfolioVariant(
        name="breakout_6",
        mode="breakout",
        breakout_lookback=6,
        stop_atr=1.8,
        reward_r=3.0,
        max_hold_bars=20,
    ),
    PortfolioVariant(
        name="ensemble",
        mode="ensemble",
        momentum_atr=0.06,
        momentum_rsi_long=54,
        momentum_rsi_short=46,
        z_threshold=1.2,
        mr_rsi_low=38,
        mr_rsi_high=62,
        breakout_lookback=4,
        stop_atr=1.6,
        reward_r=2.5,
        max_hold_bars=15,
        max_positions_per_symbol=12,
    ),
]

frames = {}
for symbol in SYMBOLS:
    path = DATA / f"{symbol}_M1_90d_20260526.parquet"
    frames[symbol] = normalize_m1(pd.read_parquet(path))

results = []
for cfg in variants:
    trades = []
    for symbol in SYMBOLS:
        trades.extend(simulate_symbol(symbol, frames[symbol], cfg))
    metrics = portfolio_metrics(
        trades,
        cfg,
        starting_balance=100.0,
        risk_fraction=0.0005,
    )
    results.append(metrics)
    print(
        cfg.name,
        "N=", metrics["trades"],
        "N/hr=", round(metrics["avg_entries_per_session_hour"], 2),
        "PF=", round(metrics["profit_factor_r"], 3),
        "ret=", round(metrics["return_pct"], 2),
        "DD=", round(metrics["max_drawdown_pct"], 2),
    )

qualified = [
    r for r in results
    if r["avg_entries_per_session_hour"] >= 13.0
    and r["profit_factor_r"] > 1.0
    and r["final_balance"] > 100.0
]
qualified.sort(
    key=lambda r: (
        r["profit_factor_r"],
        r["avg_entries_per_session_hour"],
        r["final_balance"],
    ),
    reverse=True,
)

payload = {
    "source": "Sai310421/xauusd-data pinned public 90-day M1 parquet",
    "symbols": SYMBOLS,
    "starting_balance": 100.0,
    "risk_fraction_per_trade": 0.0005,
    "round_trip_price_cost_assumptions": ROUND_TRIP_PRICE_COST,
    "acceptance_gate": {
        "avg_entries_per_session_hour_min": 13.0,
        "profit_factor_r_min_exclusive": 1.0,
        "final_balance_min_exclusive": 100.0,
    },
    "qualified": qualified,
    "results": results,
}

Path("results").mkdir(exist_ok=True)
Path("results/multisymbol_m1_sweep.json").write_text(
    json.dumps(payload, indent=2), encoding="utf-8"
)
print(json.dumps({"qualified_count": len(qualified), "qualified": qualified[:3]}, indent=2))
