from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import lzma
import math
import struct
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
REC = struct.Struct(">3i2f")
TRANSIENT = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Params:
    tf_sec: int = 60
    variant: str = "A"
    trigger: float = 0.12
    confirm: float = 0.03
    add: float = 0.025
    reversal: float = 0.20
    max_layers: int = 10
    velocity_ticks: int = 8
    layer_ounces: float = 0.10
    commission_usd_per_ounce_rt: float = 0.07
    slippage_usd_per_side: float = 0.01


@dataclass
class Stats:
    realized: float = 0.0
    cycles: int = 0
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    layer_entries: int = 0
    max_layers: int = 0
    trigger_candidates: int = 0
    direction_rejects: int = 0
    max_drawdown_pct: float = 0.0


class G75Raw:
    def __init__(self, params: Params, starting_balance: float = 100.0):
        self.p = params
        self.starting_balance = starting_balance

        self.bucket = None
        self.anchor = None
        self.bar_close_mid = None
        self.started_bucket = False

        self.prev_closes: list[float] = []
        self.tick_mids: list[float] = []

        self.active = False
        self.side = 0
        self.entries: list[float] = []
        self.last_add = None
        self.extreme = None

        self.last_bid = None
        self.last_ask = None

        self.stats = Stats()
        self.equity_peak = starting_balance

        self.layer_entry_times: list[pd.Timestamp] = []
        self.cycle_close_times: list[pd.Timestamp] = []

    def _mark(self, bid: float, ask: float) -> float:
        if not self.active:
            return 0.0
        px = bid if self.side > 0 else ask
        return sum((px - e) * self.side * self.p.layer_ounces for e in self.entries)

    def _equity(self, bid: float, ask: float) -> float:
        return self.starting_balance + self.stats.realized + self._mark(bid, ask)

    def _update_dd(self, bid: float, ask: float) -> None:
        eq = self._equity(bid, ask)
        self.equity_peak = max(self.equity_peak, eq)
        if self.equity_peak > 0:
            dd = (self.equity_peak - eq) / self.equity_peak * 100
            self.stats.max_drawdown_pct = max(self.stats.max_drawdown_pct, dd)

    def _direction_score(self, side: int, mid: float) -> int:
        score = 0
        if side > 0 and mid >= self.anchor + self.p.trigger + self.p.confirm:
            score += 1
        if side < 0 and mid <= self.anchor - self.p.trigger - self.p.confirm:
            score += 1

        if len(self.tick_mids) >= 3:
            d = self.tick_mids[-1] - self.tick_mids[0]
            if d * side > 0:
                score += 1

        if len(self.prev_closes) >= 2:
            d = self.prev_closes[-1] - self.prev_closes[-2]
            if d * side > 0:
                score += 1
        return score

    def _close(self, ts: pd.Timestamp, bid: float, ask: float) -> None:
        if not self.active:
            return

        raw_exit = bid if self.side > 0 else ask
        # Add explicit adverse slippage beyond the observed bid/ask.
        exit_px = raw_exit - self.p.slippage_usd_per_side if self.side > 0 else raw_exit + self.p.slippage_usd_per_side

        pnl = 0.0
        for entry in self.entries:
            leg = (exit_px - entry) * self.side * self.p.layer_ounces
            leg -= self.p.commission_usd_per_ounce_rt * self.p.layer_ounces
            pnl += leg

        self.stats.realized += pnl
        self.stats.cycles += 1
        self.cycle_close_times.append(ts)

        if pnl > 0:
            self.stats.wins += 1
            self.stats.gross_win += pnl
        elif pnl < 0:
            self.stats.losses += 1
            self.stats.gross_loss += abs(pnl)

        self.active = False
        self.side = 0
        self.entries = []
        self.last_add = None
        self.extreme = None

    def _finish_bucket(self, ts: pd.Timestamp) -> None:
        if self.bar_close_mid is None:
            return

        if self.active:
            mark = self.last_bid if self.side > 0 else self.last_ask
            reversal = (
                mark <= self.extreme - self.p.reversal
                if self.side > 0
                else mark >= self.extreme + self.p.reversal
            )
            if reversal:
                self._close(ts, self.last_bid, self.last_ask)

        self.prev_closes.append(self.bar_close_mid)
        if len(self.prev_closes) > 4:
            self.prev_closes.pop(0)
        self.anchor = self.bar_close_mid

    def on_tick(self, ts: pd.Timestamp, bid: float, ask: float) -> None:
        mid = (bid + ask) / 2
        self.last_bid = bid
        self.last_ask = ask

        self.tick_mids.append(mid)
        if len(self.tick_mids) > self.p.velocity_ticks:
            self.tick_mids.pop(0)

        bucket = int(ts.timestamp()) // self.p.tf_sec
        if self.bucket is None:
            self.bucket = bucket
            self.anchor = mid
        elif bucket != self.bucket:
            self._finish_bucket(ts)
            self.bucket = bucket
            self.started_bucket = False

        self.bar_close_mid = mid

        if not self.active and not self.started_bucket and self.anchor is not None:
            side = 0
            if mid >= self.anchor + self.p.trigger:
                side = 1
            elif mid <= self.anchor - self.p.trigger:
                side = -1

            if side:
                self.stats.trigger_candidates += 1
                needed = 1 if self.p.variant == "A" else (2 if self.p.variant == "B" else 3)
                if self.p.variant != "A" and self._direction_score(side, mid) < needed:
                    self.stats.direction_rejects += 1
                else:
                    raw_entry = ask if side > 0 else bid
                    entry = raw_entry + self.p.slippage_usd_per_side if side > 0 else raw_entry - self.p.slippage_usd_per_side
                    self.active = True
                    self.side = side
                    self.entries = [entry]
                    self.last_add = entry
                    self.extreme = bid if side > 0 else ask
                    self.started_bucket = True
                    self.stats.layer_entries += 1
                    self.layer_entry_times.append(ts)
                    self.stats.max_layers = max(self.stats.max_layers, 1)

        if self.active:
            px = bid if self.side > 0 else ask
            self.extreme = max(self.extreme, px) if self.side > 0 else min(self.extreme, px)

            while len(self.entries) < self.p.max_layers:
                target = self.last_add + self.side * self.p.add
                crossed = px >= target if self.side > 0 else px <= target
                if not crossed:
                    break

                if self.p.variant != "A" and len(self.tick_mids) >= 3:
                    if (self.tick_mids[-1] - self.tick_mids[0]) * self.side <= 0:
                        break

                raw_fill = ask if self.side > 0 else bid
                fill = raw_fill + self.p.slippage_usd_per_side if self.side > 0 else raw_fill - self.p.slippage_usd_per_side
                self.entries.append(fill)
                self.last_add = target
                self.stats.layer_entries += 1
                self.layer_entry_times.append(ts)
                self.stats.max_layers = max(self.stats.max_layers, len(self.entries))

        self._update_dd(bid, ask)

    def finish(self, ts: pd.Timestamp) -> None:
        self._finish_bucket(ts)
        if self.active and self.last_bid is not None:
            self._close(ts, self.last_bid, self.last_ask)

    def result(self, start: pd.Timestamp, end: pd.Timestamp) -> dict:
        hours = max((end - start).total_seconds() / 3600, 1e-9)
        gp = self.stats.gross_win
        gl = self.stats.gross_loss
        pf = gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)

        # Active market hours only, based on actual tick span. XAUUSD trades nearly 24h weekdays,
        # so calendar trading hours excluding weekend approximation is more meaningful than 24/7.
        trading_hours = max(hours * 5 / 7, 1e-9)

        return {
            "params": asdict(self.p),
            "starting_balance": self.starting_balance,
            "final_balance": self.starting_balance + self.stats.realized,
            "return_pct": self.stats.realized / self.starting_balance * 100,
            "cycles": self.stats.cycles,
            "layer_entries": self.stats.layer_entries,
            "wins": self.stats.wins,
            "losses": self.stats.losses,
            "cycle_win_rate_pct": 100 * self.stats.wins / max(1, self.stats.cycles),
            "profit_factor": pf,
            "max_drawdown_pct": self.stats.max_drawdown_pct,
            "max_layers": self.stats.max_layers,
            "trigger_candidates": self.stats.trigger_candidates,
            "direction_rejects": self.stats.direction_rejects,
            "cycles_per_trading_hour": self.stats.cycles / trading_hours,
            "executed_layer_trades_per_trading_hour": self.stats.layer_entries / trading_hours,
            "span_hours": hours,
            "estimated_trading_hours": trading_hours,
        }


def url_for(symbol: str, day: date, hour: int) -> str:
    return f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"


def download_hour(symbol: str, day: date, hour: int, retries: int = 5) -> bytes:
    url = url_for(symbol, day, hour)
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "DairusAlgoTrader/1.0", "Accept": "*/*"})
            with urlopen(req, timeout=30) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return b""
            if exc.code not in TRANSIENT or attempt == retries - 1:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == retries - 1:
                raise
        time.sleep(min(10.0, 1.0 * 2 ** attempt))
    return b""


def parse_hour(symbol: str, day: date, hour: int, body: bytes):
    if not body:
        return []
    raw = lzma.decompress(body)
    divider = 1000.0 if symbol == "XAUUSD" or "JPY" in symbol else 100000.0
    base = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
    out = []
    for off in range(0, len(raw) - REC.size + 1, REC.size):
        ms, ask_i, bid_i, ask_v, bid_v = REC.unpack_from(raw, off)
        ask = ask_i / divider
        bid = bid_i / divider
        if bid <= 0 or ask <= 0 or bid > ask:
            continue
        out.append((pd.Timestamp(base + timedelta(milliseconds=ms)), bid, ask))
    return out


def fetch_day(symbol: str, day: date, workers: int = 8):
    rows = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_hour, symbol, day, h): h for h in range(24)}
        for fut in cf.as_completed(futures):
            h = futures[fut]
            body = fut.result()
            rows.extend(parse_hour(symbol, day, h, body))
    rows.sort(key=lambda x: x[0])
    return rows


def run_period(symbol: str, start: date, days: int, params: Params) -> dict:
    engine = G75Raw(params, starting_balance=100.0)
    first = None
    last = None
    raw_ticks = 0

    for offset in range(days):
        day = start + timedelta(days=offset)
        rows = fetch_day(symbol, day)
        raw_ticks += len(rows)
        if rows:
            first = rows[0][0] if first is None else first
            last = rows[-1][0]
        for ts, bid, ask in rows:
            engine.on_tick(ts, bid, ask)
        print(f"{day} ticks={len(rows):,} cumulative={raw_ticks:,}", flush=True)

    if first is None or last is None:
        raise RuntimeError("No ticks downloaded")

    engine.finish(last)
    result = engine.result(first, last)
    result.update({
        "symbol": symbol,
        "period_start": str(first),
        "period_end": str(last),
        "raw_ticks": raw_ticks,
        "days_requested": days,
        "data_source": "Dukascopy raw BI5 bid/ask",
    })
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-07-27")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--tf-sec", type=int, default=60)
    p.add_argument("--variant", choices=["A", "B", "C"], default="A")
    p.add_argument("--trigger", type=float, default=0.12)
    p.add_argument("--confirm", type=float, default=0.03)
    p.add_argument("--add", type=float, default=0.025)
    p.add_argument("--reversal", type=float, default=0.20)
    p.add_argument("--out", default="")
    args = p.parse_args()

    params = Params(
        tf_sec=args.tf_sec,
        variant=args.variant,
        trigger=args.trigger,
        confirm=args.confirm,
        add=args.add,
        reversal=args.reversal,
    )
    result = run_period(args.symbol, date.fromisoformat(args.start), args.days, params)

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
