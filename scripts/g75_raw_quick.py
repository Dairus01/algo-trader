from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from g75_raw_duka_bt import G75Raw, Params, fetch_day

START = date.fromisoformat("2026-07-27")
DAYS = 2
CONFIGS = [
    Params(tf_sec=tf, variant=v)
    for tf in (15, 30, 60)
    for v in ("A", "B", "C")
]

def main():
    engines={(p.tf_sec,p.variant):G75Raw(p,starting_balance=100.0) for p in CONFIGS}
    first=last=None
    raw_ticks=0
    for offset in range(DAYS):
        day=START+timedelta(days=offset)
        rows=fetch_day("XAUUSD",day,workers=2)
        raw_ticks+=len(rows)
        if rows:
            if first is None:first=rows[0][0]
            last=rows[-1][0]
        for ts,bid,ask in rows:
            for engine in engines.values():
                engine.on_tick(ts,bid,ask)
        print(f"{day} ticks={len(rows):,} cumulative={raw_ticks:,}",flush=True)
    if first is None or last is None:raise RuntimeError("no ticks")
    results=[]
    for (tf,v),engine in engines.items():
        engine.finish(last)
        r=engine.result(first,last)
        r.update({"tf_sec":tf,"variant":v,"raw_ticks":raw_ticks})
        results.append(r)
    results.sort(key=lambda x:(x["profit_factor"],x["executed_layer_trades_per_trading_hour"]),reverse=True)
    qualified=[r for r in results if r["executed_layer_trades_per_trading_hour"]>=13 and r["profit_factor"]>1 and r["final_balance"]>100]
    payload={"days":DAYS,"raw_ticks":raw_ticks,"qualified":qualified,"results":results}
    Path("results").mkdir(exist_ok=True)
    Path("results/g75_raw_quick.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2))
if __name__=="__main__":main()
