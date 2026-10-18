from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algo_trader.data import fetch_pinned_research_data


if __name__ == "__main__":
    path = fetch_pinned_research_data()
    print(f"Downloaded pinned research data to {path}")
