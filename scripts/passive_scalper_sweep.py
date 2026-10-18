import json
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.data import load_ohlc
from algo_trader.passive_scalper_backtest import run_passive_scalper_backtest
from algo_trader.scalper_config import ScalperConfig, ScalperExecutionConfig

path = Path("data/XAUUSD_M1_2026_sample.csv")
if not path.exists():
    raise SystemExit("Run python scripts/fetch_m1_sample.py first")

df = load_ohlc(path)
base = ScalperConfig()
micro = ScalperExecutionConfig(min_ounces=0.01, volume_step_ounces=0.01)

variants = {
    "momentum_pullback_015": (
        replace(
            base,
            enable_mean_reversion=False,
            enable_breakout=False,
            stop_atr=1.5,
            reward_r=2.5,
            max_hold_bars=12,
            max_positions=60,
            risk_fraction_per_trade=0.0005,
        ),
        0.15,
    ),
    "momentum_pullback_030": (
        replace(
            base,
            enable_mean_reversion=False,
            enable_breakout=False,
            stop_atr=1.5,
            reward_r=2.5,
            max_hold_bars=12,
            max_positions=60,
            risk_fraction_per_trade=0.0005,
        ),
        0.30,
    ),
    "mean_reversion_015": (
        replace(
            base,
            enable_momentum=False,
            enable_breakout=False,
            mean_reversion_z=0.7,
            mean_reversion_rsi_low=47,
            mean_reversion_rsi_high=53,
            stop_atr=1.2,
            reward_r=1.6,
            max_hold_bars=7,
            max_positions=60,
            risk_fraction_per_trade=0.0005,
        ),
        0.15,
    ),
    "mean_reversion_030": (
        replace(
            base,
            enable_momentum=False,
            enable_breakout=False,
            mean_reversion_z=0.7,
            mean_reversion_rsi_low=47,
            mean_reversion_rsi_high=53,
            stop_atr=1.2,
            reward_r=1.6,
            max_hold_bars=7,
            max_positions=60,
            risk_fraction_per_trade=0.0005,
        ),
        0.30,
    ),
    "ensemble_015": (
        replace(
            base,
            stop_atr=1.5,
            reward_r=2.2,
            max_hold_bars=10,
            max_positions=80,
            risk_fraction_per_trade=0.0004,
            mean_reversion_z=0.9,
            mean_reversion_rsi_low=44,
            mean_reversion_rsi_high=56,
        ),
        0.15,
    ),
    "ensemble_030": (
        replace(
            base,
            stop_atr=1.5,
            reward_r=2.2,
            max_hold_bars=10,
            max_positions=80,
            risk_fraction_per_trade=0.0004,
            mean_reversion_z=0.9,
            mean_reversion_rsi_low=44,
            mean_reversion_rsi_high=56,
        ),
        0.30,
    ),
}

results = {}
for name, (cfg, offset) in variants.items():
    results[name] = run_passive_scalper_backtest(
        df,
        strategy=cfg,
        execution=micro,
        entry_offset_atr=offset,
        starting_balance=100.0,
    )

qualified = {
    name: result for name, result in results.items()
    if result["avg_entries_per_session_hour"] >= 13.0
    and result["profit_factor"] > 1.0
    and result["final_balance"] > 100.0
}

payload = {
    "starting_balance": 100.0,
    "acceptance_gate": {
        "avg_entries_per_session_hour": 13.0,
        "minimum_profit_factor": 1.0,
        "final_balance_gt": 100.0,
    },
    "results": results,
    "qualified": qualified,
}
Path("results").mkdir(exist_ok=True)
Path("results/passive_scalper_sweep.json").write_text(
    json.dumps(payload, indent=2), encoding="utf-8"
)
print(json.dumps(payload, indent=2))
