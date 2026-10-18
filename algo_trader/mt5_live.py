from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import ExecutionConfig, StrategyConfig, get_risk_profile
from .indicators import floor_to_step
from .strategy import prepare_features

MAGIC = 260618
LOG_PATH = Path("logs/live_decisions.jsonl")


def _log(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"utc": datetime.now(timezone.utc).isoformat(), **event}
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _require_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError(
            "MetaTrader5 is not installed. On Windows run: pip install -r requirements-live.txt"
        ) from exc
    return mt5


def _bars(mt5, symbol: str, count: int = 500) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, count)
    if rates is None or len(rates) < 250:
        raise RuntimeError(f"Not enough H1 data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").sort_index()


def _positions(mt5, symbol: str):
    return [
        p for p in (mt5.positions_get(symbol=symbol) or [])
        if getattr(p, "magic", None) == MAGIC
    ]


def _fill_mode(mt5, info):
    mode = int(getattr(info, "filling_mode", 0))
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _close_expired(mt5, symbol: str, max_hours: int, live: bool) -> None:
    now = datetime.now(timezone.utc)
    info = mt5.symbol_info(symbol)
    for pos in _positions(mt5, symbol):
        opened = datetime.fromtimestamp(pos.time, tz=timezone.utc)
        if (now - opened).total_seconds() < max_hours * 3600:
            continue
        if not live:
            _log({"event": "paper_time_exit", "ticket": pos.ticket})
            continue
        tick = mt5.symbol_info_tick(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL,
            "position": pos.ticket,
            "price": tick.bid,
            "deviation": 30,
            "magic": MAGIC,
            "comment": "algo-trader-time-exit",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _fill_mode(mt5, info),
        }
        result = mt5.order_send(request)
        _log({"event": "time_exit", "ticket": pos.ticket, "result": str(result)})


def run_mt5(
    profile_name: str = "core",
    symbol: str = "XAUUSD",
    live: bool = False,
    poll_seconds: int = 20,
) -> None:
    profile = get_risk_profile(profile_name)
    if profile.requires_total_loss_ack:
        if os.getenv("ALLOW_MOONSHOT") != "I_ACCEPT_TOTAL_LOSS":
            raise RuntimeError(
                "Moonshot is locked. Set ALLOW_MOONSHOT=I_ACCEPT_TOTAL_LOSS only if total loss is acceptable."
            )

    mt5 = _require_mt5()
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    strategy = StrategyConfig()
    execution = ExecutionConfig()
    last_closed_bar = None
    day = None
    day_start_equity = None
    daily_loss_limit = min(0.50, profile.risk_fraction * 3.0)

    _log(
        {
            "event": "startup",
            "symbol": symbol,
            "profile": asdict(profile),
            "strategy": asdict(strategy),
            "live": live,
        }
    )

    try:
        while True:
            _close_expired(mt5, symbol, strategy.max_hold_bars, live)
            bars = _bars(mt5, symbol)

            # MT5 includes the forming candle. Signals use the last fully closed H1 bar.
            closed = bars.iloc[:-1].copy()
            closed_bar_time = closed.index[-1]
            if last_closed_bar == closed_bar_time:
                time.sleep(poll_seconds)
                continue
            last_closed_bar = closed_bar_time

            account = mt5.account_info()
            current_day = closed_bar_time.strftime("%Y-%m-%d")
            if current_day != day:
                day = current_day
                day_start_equity = float(account.equity)

            if day_start_equity and account.equity <= day_start_equity * (1 - daily_loss_limit):
                _log(
                    {
                        "event": "blocked_daily_loss",
                        "equity": account.equity,
                        "day_start_equity": day_start_equity,
                        "limit_fraction": daily_loss_limit,
                    }
                )
                time.sleep(poll_seconds)
                continue

            features = prepare_features(closed, strategy)
            row = features.iloc[-1]
            if not bool(row["long_signal"]):
                _log({"event": "no_signal", "bar": closed_bar_time.isoformat()})
                time.sleep(poll_seconds)
                continue

            if _positions(mt5, symbol):
                _log({"event": "blocked_existing_position", "bar": closed_bar_time.isoformat()})
                time.sleep(poll_seconds)
                continue

            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if info is None or tick is None:
                _log({"event": "blocked_missing_broker_state"})
                time.sleep(poll_seconds)
                continue

            spread = float(tick.ask - tick.bid)
            if spread > execution.max_spread_usd_live:
                _log({"event": "blocked_spread", "spread": spread})
                time.sleep(poll_seconds)
                continue

            stop_distance = float(row["atr"]) * strategy.stop_atr
            risk_dollars = float(account.equity) * profile.risk_fraction
            contract_size = float(info.trade_contract_size)
            raw_lots = risk_dollars / (stop_distance * contract_size)
            lots = floor_to_step(raw_lots, float(info.volume_step))
            lots = min(lots, float(info.volume_max))

            if lots < float(info.volume_min):
                _log(
                    {
                        "event": "blocked_min_volume",
                        "raw_lots": raw_lots,
                        "volume_min": info.volume_min,
                        "equity": account.equity,
                    }
                )
                time.sleep(poll_seconds)
                continue

            entry = float(tick.ask)
            stop = entry - stop_distance
            target = entry + stop_distance * strategy.reward_r
            decision = {
                "event": "signal",
                "bar": closed_bar_time.isoformat(),
                "entry": entry,
                "stop": stop,
                "target": target,
                "lots": lots,
                "risk_dollars": risk_dollars,
                "spread": spread,
                "paper": not live,
            }
            _log(decision)

            if live:
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": lots,
                    "type": mt5.ORDER_TYPE_BUY,
                    "price": entry,
                    "sl": stop,
                    "tp": target,
                    "deviation": 30,
                    "magic": MAGIC,
                    "comment": f"algo-trader-{profile.name}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": _fill_mode(mt5, info),
                }
                result = mt5.order_send(request)
                _log({"event": "order_send", "result": str(result)})

            time.sleep(poll_seconds)
    finally:
        mt5.shutdown()
