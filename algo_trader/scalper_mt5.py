from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .indicators import floor_to_step
from .scalper_config import ScalperConfig, ScalperExecutionConfig
from .scalper_strategy import prepare_scalper_features, signals_from_row

MAGIC = 260619
LOG_PATH = Path("logs/scalper_decisions.jsonl")


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


def _bars(mt5, symbol: str, count: int = 600) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
    if rates is None or len(rates) < 100:
        raise RuntimeError(f"Not enough M1 data for {symbol}: {mt5.last_error()}")
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


def _close_expired(mt5, symbol: str, max_minutes: int, live: bool) -> None:
    now = datetime.now(timezone.utc)
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return

    for pos in _positions(mt5, symbol):
        opened = datetime.fromtimestamp(pos.time, tz=timezone.utc)
        if (now - opened).total_seconds() < max_minutes * 60:
            continue
        if not live:
            _log({"event": "paper_time_exit", "ticket": pos.ticket})
            continue

        is_buy = int(pos.type) == int(mt5.POSITION_TYPE_BUY)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": 20,
            "magic": MAGIC,
            "comment": "hf-scalper-time-exit",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _fill_mode(mt5, info),
        }
        result = mt5.order_send(request)
        _log({"event": "time_exit", "ticket": pos.ticket, "result": str(result)})


def run_scalper_mt5(
    symbol: str = "XAUUSD",
    live: bool = False,
    poll_seconds: float = 2.0,
) -> None:
    mt5 = _require_mt5()
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    cfg = ScalperConfig()
    exe = ScalperExecutionConfig()
    last_closed_bar = None
    day = None
    day_start_equity = None
    daily_loss_limit = 0.10

    _log({
        "event": "startup",
        "symbol": symbol,
        "live": live,
        "target_trade_rate_per_hour": "13+",
        "config": cfg.__dict__,
    })

    try:
        while True:
            _close_expired(mt5, symbol, cfg.max_hold_bars, live)
            bars = _bars(mt5, symbol)

            # Current M1 candle is still forming. Only the previous candle is used.
            closed = bars.iloc[:-1].copy()
            closed_time = closed.index[-1]
            if last_closed_bar == closed_time:
                time.sleep(poll_seconds)
                continue
            last_closed_bar = closed_time

            account = mt5.account_info()
            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if account is None or info is None or tick is None:
                _log({"event": "blocked_missing_broker_state"})
                time.sleep(poll_seconds)
                continue

            current_day = closed_time.strftime("%Y-%m-%d")
            if current_day != day:
                day = current_day
                day_start_equity = float(account.equity)

            if day_start_equity and account.equity <= day_start_equity * (1 - daily_loss_limit):
                _log({
                    "event": "blocked_daily_loss",
                    "equity": account.equity,
                    "day_start_equity": day_start_equity,
                })
                time.sleep(poll_seconds)
                continue

            spread = float(tick.ask - tick.bid)
            if spread > exe.max_spread_usd_live:
                _log({"event": "blocked_spread", "spread": spread})
                time.sleep(poll_seconds)
                continue

            features = prepare_scalper_features(closed, cfg)
            row = features.iloc[-1]
            atr = float(row["atr"])
            signals = signals_from_row(row)[: cfg.max_new_positions_per_bar]

            if not signals:
                _log({"event": "no_signal", "bar": closed_time.isoformat()})
                time.sleep(poll_seconds)
                continue

            current_positions = _positions(mt5, symbol)
            slots = max(0, cfg.max_positions - len(current_positions))
            if slots <= 0:
                _log({
                    "event": "blocked_position_cap",
                    "positions": len(current_positions),
                    "cap": cfg.max_positions,
                })
                time.sleep(poll_seconds)
                continue

            stop_distance = atr * cfg.stop_atr
            if stop_distance <= 0:
                time.sleep(poll_seconds)
                continue

            contract_size = float(info.trade_contract_size)
            risk_dollars = float(account.equity) * cfg.risk_fraction_per_trade
            raw_lots = risk_dollars / (stop_distance * contract_size)
            lots = floor_to_step(raw_lots, float(info.volume_step))
            lots = min(lots, float(info.volume_max))

            # A normal 0.01-lot gold account often cannot represent the desired
            # risk on a $100 balance. Refuse instead of silently over-risking.
            if lots < float(info.volume_min):
                _log({
                    "event": "blocked_min_volume",
                    "raw_lots": raw_lots,
                    "volume_min": info.volume_min,
                    "equity": account.equity,
                    "suggestion": "Use a cent/micro account with finer XAUUSD volume.",
                })
                time.sleep(poll_seconds)
                continue

            for name, direction in signals[:slots]:
                entry = float(tick.ask if direction > 0 else tick.bid)
                stop = entry - direction * stop_distance
                target = entry + direction * stop_distance * cfg.reward_r

                decision = {
                    "event": "signal",
                    "bar": closed_time.isoformat(),
                    "strategy": name,
                    "direction": "BUY" if direction > 0 else "SELL",
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "lots": lots,
                    "risk_dollars": risk_dollars,
                    "spread": spread,
                    "paper": not live,
                }
                _log(decision)

                if not live:
                    continue

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": lots,
                    "type": mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL,
                    "price": entry,
                    "sl": stop,
                    "tp": target,
                    "deviation": 20,
                    "magic": MAGIC,
                    "comment": f"hf-scalper-{name}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": _fill_mode(mt5, info),
                }
                result = mt5.order_send(request)
                _log({
                    "event": "order_send",
                    "strategy": name,
                    "result": str(result),
                })

            time.sleep(poll_seconds)
    finally:
        mt5.shutdown()
