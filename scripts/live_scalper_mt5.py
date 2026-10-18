import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.scalper_mt5 import run_scalper_mt5

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--paper", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--poll", type=float, default=2.0)
    args = p.parse_args()
    run_scalper_mt5(symbol=args.symbol, live=bool(args.live), poll_seconds=args.poll)
