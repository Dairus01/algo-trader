from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.portfolio_scalper import normalize_m1, ROUND_TRIP_PRICE_COST

DATA = Path("data/multisymbol_m1")
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "EURGBP", "AUDNZD"]
HORIZON = 10
ATR_RISK_MULT = 1.5
SESSION_START = 6
SESSION_END = 21
THRESHOLDS = [0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66]

FEATURES = [
    "ret1_atr", "ret2_atr", "ret5_atr", "ret10_atr", "ret20_atr",
    "ema_gap_atr", "z20", "rsi7", "atr_pct", "range_atr",
    "pos20", "vol10", "hour_sin", "hour_cos",
]


def rsi(close: pd.Series, n: int = 7) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([
        df.high-df.low,
        (df.high-pc).abs(),
        (df.low-pc).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def make_frame(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    x = normalize_m1(df)
    a = atr(x)
    close = x.close
    ema5 = close.ewm(span=5, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std(ddof=0)
    hi20 = x.high.shift(1).rolling(20).max()
    lo20 = x.low.shift(1).rolling(20).min()
    width = (hi20-lo20).replace(0, np.nan)

    for n in [1,2,5,10,20]:
        x[f"ret{n}_atr"] = close.diff(n) / a.replace(0, np.nan)

    x["ema_gap_atr"] = (ema5-ema20) / a.replace(0, np.nan)
    x["z20"] = (close-ma20) / sd20.replace(0, np.nan)
    x["rsi7"] = rsi(close)
    x["atr_pct"] = a / close
    x["range_atr"] = (x.high-x.low) / a.replace(0, np.nan)
    x["pos20"] = (close-lo20) / width
    x["vol10"] = close.pct_change().rolling(10).std()
    x["hour_sin"] = np.sin(2*np.pi*x.index.hour/24)
    x["hour_cos"] = np.cos(2*np.pi*x.index.hour/24)

    x["atr14"] = a
    x["entry_open"] = x.open.shift(-1)
    x["future_close"] = x.close.shift(-HORIZON)
    x["symbol"] = symbol

    cost = ROUND_TRIP_PRICE_COST[symbol]
    move = x.future_close - x.entry_open
    # Learn only directional outcomes large enough to matter after estimated cost.
    x["label"] = (move > 0).astype(int)
    x["abs_edge_to_cost"] = move.abs() / max(cost, 1e-12)

    session = (
        (x.index.hour >= SESSION_START)
        & (x.index.hour < SESSION_END)
        & (x.index.dayofweek < 5)
    )
    x = x.loc[session]
    x = x.dropna(subset=FEATURES + ["entry_open", "future_close", "atr14"])
    return x


def pooled_matrix(frames: dict[str,pd.DataFrame], start, end, strong_only=True):
    parts=[]
    for sid,(sym,df) in enumerate(frames.items()):
        z=df.loc[(df.index>=start)&(df.index<end)].copy()
        if strong_only:
            z=z[z.abs_edge_to_cost>=1.25]
        z["symbol_id"]=sid
        parts.append(z)
    p=pd.concat(parts).sort_index()
    cols=FEATURES+["symbol_id"]
    return p,cols


def simulate(frames, model, cols, start, end, threshold, routes=None):
    trades=[]
    routes = routes or {sym: 1 for sym in frames}
    for sid,(sym,df) in enumerate(frames.items()):
        orientation = int(routes.get(sym, 1))
        if orientation == 0:
            continue
        z=df.loc[(df.index>=start)&(df.index<end)].copy()
        if z.empty: continue
        X=z[FEATURES].copy()
        X["symbol_id"]=sid
        probs=model.predict_proba(X[cols])[:,1]

        idx=z.index
        next_allowed=None
        cost=ROUND_TRIP_PRICE_COST[sym]
        for i,p in enumerate(probs):
            ts=idx[i]
            if next_allowed is not None and ts < next_allowed:
                continue
            if p>=threshold:
                side=1
            elif p<=1-threshold:
                side=-1
            else:
                continue
            side *= orientation

            entry=float(z.entry_open.iloc[i])
            exit_px=float(z.future_close.iloc[i])
            risk_dist=float(z.atr14.iloc[i])*ATR_RISK_MULT
            if not math.isfinite(risk_dist) or risk_dist<=0:
                continue
            net_move=side*(exit_px-entry)-cost
            net_r=net_move/risk_dist
            trades.append({
                "symbol":sym,"entry_time":ts,"side":side,"prob":float(p),
                "net_r":float(net_r),"net_move":float(net_move),
                "cost_r":float(cost/risk_dist),
            })
            next_allowed=ts+pd.Timedelta(minutes=HORIZON)

    if not trades:
        return {"trades":0,"pf":0.0,"avg_trades_per_hour":0.0,"return_pct":0.0}

    t=pd.DataFrame(trades).sort_values("entry_time")
    r=t.net_r.to_numpy(float)
    gp=float(r[r>0].sum()); gl=float(-r[r<0].sum())
    pf=gp/gl if gl>0 else float("inf")

    eq=100.0; peak=100.0; dd=0.0
    risk_fraction=0.001
    for rr in r:
        # Bound single time-exit shock for the account simulator, not the PF calculation.
        rr_eq=max(-4.0,min(4.0,float(rr)))
        eq*=max(0.0,1+risk_fraction*rr_eq)
        peak=max(peak,eq)
        dd=max(dd,(peak-eq)/peak)

    first=pd.Timestamp(start)
    last=pd.Timestamp(end)-pd.Timedelta(hours=1)
    hours=pd.date_range(first.floor("h"),last.floor("h"),freq="h",tz="UTC")
    hours=hours[(hours.hour>=SESSION_START)&(hours.hour<SESSION_END)&(hours.dayofweek<5)]
    counts=t.entry_time.dt.floor("h").value_counts()
    hourly=pd.Series(0,index=hours,dtype=float)
    common=hourly.index.intersection(counts.index)
    hourly.loc[common]=counts.loc[common].astype(float)

    bysym={}
    for sym,g in t.groupby("symbol"):
        sr=g.net_r.to_numpy(float); sgp=float(sr[sr>0].sum()); sgl=float(-sr[sr<0].sum())
        bysym[sym]={
            "trades":int(len(g)),
            "pf":float(sgp/sgl) if sgl>0 else float("inf"),
            "win_rate_pct":float((sr>0).mean()*100),
            "mean_r":float(sr.mean()),
        }

    return {
        "threshold":threshold,
        "trades":int(len(t)),
        "win_rate_pct":float((r>0).mean()*100),
        "pf":float(pf),
        "mean_r":float(r.mean()),
        "avg_trades_per_hour":float(hourly.mean()),
        "median_trades_per_hour":float(hourly.median()),
        "hours_ge_13_pct":float((hourly>=13).mean()*100),
        "max_trades_one_hour":int(hourly.max()),
        "final_balance":float(eq),
        "return_pct":float((eq/100-1)*100),
        "max_drawdown_pct":float(dd*100),
        "by_symbol":bysym,
    }


def main():
    frames={}
    for sym in SYMBOLS:
        p=DATA/f"{sym}_M1_90d_20260526.parquet"
        frames[sym]=make_frame(sym,pd.read_parquet(p))

    all_times=pd.DatetimeIndex(sorted(set().union(*[set(x.index) for x in frames.values()])))
    start=all_times.min().floor("D")
    # Exact chronological split by calendar span.
    train_end=start+pd.Timedelta(days=45)
    valid_end=start+pd.Timedelta(days=60)
    test_end=start+pd.Timedelta(days=90)

    train,cols=pooled_matrix(frames,start,train_end,strong_only=True)
    model=HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_iter=180,
        max_depth=4,
        min_samples_leaf=80,
        l2_regularization=2.0,
        random_state=42,
    )
    model.fit(train[cols],train.label)

    validation=[]
    routed_validation=[]

    for threshold in THRESHOLDS:
        normal = simulate(frames,model,cols,train_end,valid_end,threshold)
        validation.append(normal)

        routes={}
        route_evidence={}
        for sym in SYMBOLS:
            one={sym:frames[sym]}
            normal_sym=simulate(one,model,cols,train_end,valid_end,threshold,{sym:1})
            inverse_sym=simulate(one,model,cols,train_end,valid_end,threshold,{sym:-1})

            candidates=[
                ("normal",1,normal_sym),
                ("inverse",-1,inverse_sym),
            ]
            label,orientation,best=max(
                candidates,
                key=lambda x:(x[2]["pf"],x[2].get("mean_r",-999)),
            )
            # Only route symbols with positive validation expectancy.
            if best["pf"]>1.02 and best.get("mean_r",0)>0 and best["trades"]>=20:
                routes[sym]=orientation
            else:
                routes[sym]=0
                label="disabled"

            route_evidence[sym]={
                "chosen":label,
                "normal":normal_sym,
                "inverse":inverse_sym,
            }

        aggregate=simulate(frames,model,cols,train_end,valid_end,threshold,routes)
        aggregate["routes"]=routes
        aggregate["route_evidence"]=route_evidence
        routed_validation.append(aggregate)

    eligible=[
        x for x in routed_validation
        if x["avg_trades_per_hour"]>=13
        and x["pf"]>1.05
        and x["final_balance"]>100
    ]
    if eligible:
        chosen=max(
            eligible,
            key=lambda x:(x["pf"],x["mean_r"],x["avg_trades_per_hour"]),
        )
    else:
        chosen=max(
            routed_validation,
            key=lambda x:(
                x["pf"]>1,
                x["avg_trades_per_hour"]>=13,
                x["pf"],
                x["avg_trades_per_hour"],
            ),
        )

    holdout=simulate(
        frames,model,cols,valid_end,test_end,
        chosen["threshold"],chosen["routes"],
    )

    payload={
        "data":"six synchronized public M1 mid-quote markets with explicit round-trip costs",
        "symbols":SYMBOLS,
        "horizon_minutes":HORIZON,
        "split":{
            "train":[str(start),str(train_end)],
            "validation":[str(train_end),str(valid_end)],
            "holdout":[str(valid_end),str(test_end)],
        },
        "training_rows":int(len(train)),
        "thresholds":THRESHOLDS,
        "plain_validation":validation,
        "routed_validation":routed_validation,
        "selection_passed_frequency_and_pf_gate":bool(eligible),
        "chosen_validation":chosen,
        "untouched_holdout":holdout,
        "acceptance_gate":{
            "avg_trades_per_hour_min":13.0,
            "profit_factor_min_exclusive":1.05,
            "positive_final_balance":True,
        },
        "holdout_pass":bool(
            holdout["avg_trades_per_hour"]>=13
            and holdout["pf"]>1.05
            and holdout["final_balance"]>100
        ),
    }

    Path("results").mkdir(exist_ok=True)
    Path("results/ml_portfolio_m1.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2))


if __name__=="__main__":
    main()
