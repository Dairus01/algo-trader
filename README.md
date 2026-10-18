# Autonomous XAUUSD Algo Trader

A causality-safe, cost-aware autonomous XAUUSD trend-breakout trader for MetaTrader 5 and reproducible historical research.

## What this repository is

This system was built around a 10-year XAUUSD H1 research window from 2016-04-10 through 2026-04-10. The historical research dataset contains 59,150 hourly bars inside that window and originates from a public Dukascopy-derived dataset.

The strategy is deliberately simple and auditable:

- LONG only XAUUSD
- H1 timeframe
- EMA50 > EMA200
- EMA100 > EMA200
- EMA200 rising versus 72 hours ago
- close breaks above the previous 12-bar high
- signal uses closed candles only
- entry is on the next bar / live market tick
- stop = 3 x ATR(14)
- target = 3R
- maximum holding period = 72 H1 bars
- trading window = 06:00 to 20:00 UTC
- no martingale
- no grid
- no averaging down
- one position at a time

## Important result

The default CORE profile risks 2% of equity per trade. In the research backtest, with modeled spread, slippage and commission, the committed Python engine reproduced $100 growing to $682.29 over the full 10-year fixed-parameter test (494 trades, PF 1.665, 29.02% max balance drawdown).

The rolling walk-forward test is more important. Parameters were selected only from the previous three years and then frozen for the next unseen year. The 2019-2025 out-of-sample sequence compounded $100 to about $301 across 310 trades.

There is also a MOONSHOT research profile at 35% risk per trade. The committed engine produced a +103.37% best historical day in this profile, but the same 10-year simulation ended at $1.05 from $100, a -98.95% result. It is therefore disabled unless the operator explicitly acknowledges total-loss risk.

A strategy being mathematically capable of a +100% day is not the same as being capable of sustainably earning 100% per day.

## Research assumptions

Base execution model:

- starting balance: $100
- XAUUSD contract convention: 100 oz per 1.00 lot
- minimum simulated size: 0.1 oz, equivalent to 0.001 lot
- spread: $0.35
- slippage: $0.05 per side
- commission: $0.07 per ounce round trip
- conservative same-bar rule: if stop and target are both crossed inside one H1 candle, stop is assumed first
- signal at closed bar, execution at next bar open
- position sizing floors to the configured volume step

The backtester allows all of these assumptions to be changed.

## Risk profiles

| Profile | Risk/trade | Purpose |
| --- | ---: | --- |
| conservative | 1% | Low-risk research |
| core | 2% | Default |
| aggressive | 5% | High drawdown research |
| moonshot | 35% | Can produce 100% days, historically near-total ruin |

The live runner refuses to activate moonshot mode unless:

ALLOW_MOONSHOT=I_ACCEPT_TOTAL_LOSS

is present in the environment.

## Quick start

Install Python 3.11+ and dependencies:

    pip install -r requirements.txt

Fetch the pinned public research dataset:

    python scripts/fetch_research_data.py

Run the exact 10-year backtest:

    python scripts/backtest_10y.py --profile core

Run the rolling walk-forward test:

    python scripts/walkforward_10y.py

Run MT5 in paper mode:

    python scripts/live_mt5.py --profile core --paper

Run MT5 live only after your own demo validation:

    python scripts/live_mt5.py --profile core --live

## Live MT5 safety

The live trader:

- reads broker volume_min, volume_step and contract_size dynamically
- evaluates only the last fully closed H1 candle
- checks spread before entry
- sizes the position from actual equity and stop distance
- refuses duplicate positions
- uses broker-side SL and TP
- logs decisions to JSONL
- supports paper mode by default
- requires an explicit --live flag before sending orders
- requires a second environment acknowledgement for moonshot mode

## Data provenance

Pinned research source:

- Repository: anusha24-bit/Shiftguard
- Commit: e85ee01ecceab6bb3c7e4fba8c7dff1642d6209e
- File: data/raw/price/hourly/XAUUSD_hourly.csv
- Coverage: 2015-01-01 through 2026-04-10
- Exact 10-year research slice: 2016-04-10 through 2026-04-10

The raw dataset is not committed here. The fetch script downloads the pinned file so the research remains reproducible.

## Disclaimer

This is experimental trading software, not a promise of profit. Backtests can differ from live trading because of broker pricing, gaps, latency, swaps, execution quality, leverage and market-regime changes. The 35% moonshot profile is intentionally dangerous and is included to quantify, not hide, the tradeoff required to produce historical +100% days.
