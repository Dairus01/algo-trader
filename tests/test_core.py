import numpy as np
import pandas as pd

from algo_trader.config import StrategyConfig
from algo_trader.indicators import floor_to_step
from algo_trader.strategy import prepare_features


def _sample(n=500):
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    base = np.linspace(2000, 2300, n)
    return pd.DataFrame(
        {
            "open": base - 0.2,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base,
            "volume": np.ones(n),
        },
        index=idx,
    )


def test_features_are_causal():
    cfg = StrategyConfig()
    a = _sample()
    before = prepare_features(a, cfg)
    b = a.copy()
    b.iloc[450:, b.columns.get_loc("high")] *= 4
    b.iloc[450:, b.columns.get_loc("close")] *= 4
    after = prepare_features(b, cfg)
    pd.testing.assert_series_equal(
        before.loc[: a.index[449], "long_signal"],
        after.loc[: a.index[449], "long_signal"],
    )


def test_breakout_excludes_current_high():
    cfg = StrategyConfig(breakout_lookback=12)
    df = _sample()
    features = prepare_features(df, cfg)
    i = 300
    expected = df["high"].iloc[i - 12 : i].max()
    assert features["previous_breakout_high"].iloc[i] == expected


def test_floor_to_step():
    assert abs(floor_to_step(0.0199, 0.001) - 0.019) < 1e-9
