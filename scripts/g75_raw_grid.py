from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from g75_raw_duka_bt import G75Raw, Params, fetch_day

START = date.fromisoformat("2026-07-27")
DAYS = 7
CONFIGS = [
    Params(tf_sec=tf, variant=v)
    for tf in (15, 30, 60)
    for v in ("A", "B", "C")
]


def main():
    engines = {(p.tf_sec, p.variant): G75Raw(p, starting_balance=100.0) for p in CONFIGS}
    first = None
    last = None
    raw_ticks = 0

    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        rows = fetch_day("XAUUSD", day, workers=4)
        raw_ticks += len(rows)
        if rows:
            if first is None:
                first = rows[0][0]
            last = rows[-1][0]
        for ts, bid, ask in rows:
            for engine in engines.values():
                engine.on_tick(ts, bid, ask)
        print(f"{day} ticks={len(rows):,} cumulative={raw_ticks:,}", flush=True)

    if first is None or last is None:
        raise RuntimeError("No raw ticks")

    results = []
    for (tf, variant), engine in engines.items():
        engine.finish(last)
        r = engine.result(first, last)
        r.update({
            "symbol": "XAUUSD",
            "tf_sec": tf,
            "variant": variant,
            "period_start": str(first),
            "period_end": str(last),
            "raw_ticks": raw_ticks,
            "data_source": "Dukascopy raw BI5 bid/ask",
        })
        results.append(r)

    results.sort(
        key=lambda r: (
            r["profit_factor"],
            r["executed_layer_trades_per_trading_hour"],
        ),
        reverse=True,
    )
    qualified = [
        r for r in results
        if r["executed_layer_trades_per_trading_hour"] >= 13
        and r["profit_factor"] > 1
        and r["final_balance"] > 100
    ]

    payload = {
        "selection_period": [str(first), str(last)],
        "raw_ticks": raw_ticks,
        "acceptance_gate": {
            "executed_layer_trades_per_trading_hour_min": 13,
            "profit_factor_min_exclusive": 1.0,
            "final_balance_min_exclusive": 100.0,
        },
        "qualified": qualified,
        "results": results,
    }
    out = Path("results/g75_raw_grid.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
