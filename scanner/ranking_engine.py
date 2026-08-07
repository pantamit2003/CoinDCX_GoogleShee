"""
scanner/ranking_engine.py

Layer 8 — Ranking Engine (multi-coin).

Runs the full pipeline (Layers 0-1-2-3-5-7) on every coin in the given
pair list, takes each coin's MOST RECENT bar as its current status, and
returns a ranked leaderboard sorted by Confidence (highest first), with
Risk_Reward as a tiebreaker.

This is what turns the scanner from "analyze one coin" into "scan the
whole market and tell me the best opportunities right now" — which was
the original goal (rank all coins from highest to lowest probability).

Design notes:
- BTC itself is excluded from ranking (it's the reference, RS vs BTC for
  BTC doesn't make sense).
- Each coin's fetch+pipeline is wrapped in try/except so one bad pair
  (delisted, no data, API hiccup) doesn't kill the whole scan.
- Only the LAST row of each coin's dataframe is kept for the leaderboard
  — that's "right now." The full per-coin dataframe is discarded after
  extracting that row to keep memory use sane across many coins.
"""

import time
import pandas as pd

from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores
from strategy.trade_plan import generate_trade_plan


LEADERBOARD_COLUMNS = [
    "Pair", "Close", "High", "Trend_Stage", "Direction",
    "Trend_Score", "Momentum_Score", "Volume_Score",
    "Breakout_Score", "RS_Score", "Confidence", "RVOL_20",
    "Made_New_Low_Recently", "Bars_Since_90d_Low",
    "Entry_Trigger", "Entry_Price", "Stop_Loss_Price",
    "Take_Profit_Price", "Risk_Reward", "Expected_Move_Percent",
    "Reason_Codes",
]


class RankingEngine:

    def __init__(self, resolution="1", days=2, btc_pair="B-BTC_USDT"):
        self.resolution = resolution
        self.days = days
        self.btc_pair = btc_pair
        self.candles = CandleData()
        self.ema = EMAIndicator()

    # -----------------------------------------------------------------
    def _run_pipeline(self, pair: str, btc_df: pd.DataFrame) -> pd.DataFrame:
        df = self.candles.get_candles(pair=pair, resolution=self.resolution, days=self.days)

        df = self.ema.calculate(df)
        df = add_atr(df)
        df = add_all_features(df)
        df = classify_trend_lifecycle(df)
        df = calculate_relative_strength(df, btc_df)
        df = calculate_scores(df)
        df = generate_trade_plan(df)

        return df

    # -----------------------------------------------------------------
    def scan(self, pairs: list) -> pd.DataFrame:
        btc_df = self.candles.get_candles(pair=self.btc_pair, resolution=self.resolution, days=self.days)

        rows = []
        errors = []

        for pair in pairs:
            if pair == self.btc_pair:
                continue

            try:
                df = self._run_pipeline(pair, btc_df)
                last = df.iloc[-1]

                row = {"Pair": pair}
                for col in LEADERBOARD_COLUMNS:
                    if col == "Pair":
                        continue
                    row[col] = last.get(col)
                rows.append(row)

            except Exception as e:
                errors.append((pair, str(e)))
                continue

        if errors:
            print(f"\n{len(errors)} pair(s) failed and were skipped:")
            for pair, err in errors:
                print(f"  - {pair}: {err}")

        if not rows:
            return pd.DataFrame(columns=LEADERBOARD_COLUMNS)

        leaderboard = pd.DataFrame(rows)
        leaderboard = leaderboard.sort_values(
            by=["Confidence", "Risk_Reward"],
            ascending=[False, False]
        ).reset_index(drop=True)

        return leaderboard[LEADERBOARD_COLUMNS]


# ---------- Helper Function (matches your existing convention) ----------
def scan_market(pairs, **kwargs):
    return RankingEngine(**kwargs).scan(pairs)