import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.mt5_live import run_mt5


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        default="core",
        choices=["conservative", "core", "aggressive", "experimental100", "moonshot"],
    )
    parser.add_argument("--symbol", default="XAUUSD")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args()
    run_mt5(profile_name=args.profile, symbol=args.symbol, live=bool(args.live))
