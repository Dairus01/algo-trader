import json
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.data import load_ohlc
from algo_trader.scalper_backtest import compact_scalper_result, run_scalper_backtest
from algo_trader.scalper_config import ScalperConfig, ScalperExecutionConfig
from algo_trader.scalper_strategy import prepare_scalper_features, signals_from_row

path = Path("data/XAUUSD_M1_2026_sample.csv")
if not path.exists():
    raise SystemExit("Run python scripts/fetch_m1_sample.py first")

df = load_ohlc(path)
base = ScalperConfig()
micro = ScalperExecutionConfig(min_ounces=0.01, volume_step_ounces=0.01)

configs = {
    "momentum_18R": replace(
        base,
        enable_mean_reversion=False,
        enable_breakout=False,
        stop_atr=1.2,
        reward_r=1.8,
        max_hold_bars=8,
        risk_fraction_per_trade=0.001,
    ),
    "momentum_25R": replace(
        base,
        enable_mean_reversion=False,
        enable_breakout=False,
        stop_atr=1.5,
        reward_r=2.5,
        max_hold_bars=12,
        risk_fraction_per_trade=0.001,
    ),
    "momentum_30R": replace(
        base,
        enable_mean_reversion=False,
        enable_breakout=False,
        stop_atr=2.0,
        reward_r=3.0,
        max_hold_bars=15,
        risk_fraction_per_trade=0.001,
    ),
    "mean_reversion": replace(
        base,
        enable_momentum=False,
        enable_breakout=False,
        mean_reversion_z=0.8,
        mean_reversion_rsi_low=45,
        mean_reversion_rsi_high=55,
        stop_atr=1.2,
        reward_r=1.5,
        max_hold_bars=6,
        risk_fraction_per_trade=0.001,
    ),
    "breakout": replace(
        base,
        enable_momentum=False,
        enable_mean_reversion=False,
        breakout_lookback=2,
        stop_atr=1.5,
        reward_r=2.0,
        max_hold_bars=10,
        risk_fraction_per_trade=0.001,
    ),
    "ensemble": replace(
        base,
        stop_atr=1.5,
        reward_r=2.2,
        max_hold_bars=10,
        risk_fraction_per_trade=0.00075,
        max_positions=40,
    ),
}

rows = {}
for name, cfg in configs.items():
    feat = prepare_scalper_features(df, cfg)
    signal_count = feat.apply(lambda r: len(signals_from_row(r)), axis=1)
    mask = (
        (feat.index.hour >= cfg.session_start_utc)
        & (feat.index.hour < cfg.session_end_utc)
        & (feat.index.dayofweek < 5)
    )
    per_hour = signal_count[mask].groupby(feat.index[mask].floor("h")).sum()
    result = compact_scalper_result(
        run_scalper_backtest(
            df,
            strategy=cfg,
            execution=micro,
            starting_balance=100.0,
        )
    )
    result["raw_avg_signals_per_session_hour"] = float(per_hour.mean())
    result["raw_hours_ge_13_signals_pct"] = float((per_hour >= 13).mean() * 100)
    rows[name] = result

payload = {"starting_balance": 100.0, "execution": micro.__dict__, "variants": rows}
Path("results").mkdir(exist_ok=True)
Path("results/scalper_variant_sweep.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
