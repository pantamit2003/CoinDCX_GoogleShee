"""
scanner/coiled_spring_scan.py

Standalone scanner: runs the pipeline (Features -> Trend Lifecycle ->
Coiled Spring -> Relative Strength -> Scoring) on the DAILY ("1D")
timeframe across many coins, and returns only the ones currently
flagged as Coiled_Spring = True.

WHY DAILY:
The "coiled spring before a big move" pattern (like the COOKIE / HEI
charts you looked at) plays out over WEEKS of daily candles, not
minutes. Scanning 15m/1h data for this pattern doesn't make sense —
you need the daily chart to see the multi-week squeeze.

HOW TO USE:
    from scanner.coiled_spring_scan import scan_coiled_spring

    watchlist = scan_coiled_spring(pairs_to_scan)
    print(watchlist)

This does NOT tell you to enter a trade. It tells you which coins to
add to a watchlist. Once a coin is on this watchlist, check its
Breakout_Score (from your normal 15m/1h scan) regularly — THAT is
your entry trigger, once the squeeze actually breaks.
"""

import pandas as pd

from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from indicators.coiled_spring import add_coiled_spring
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores


WATCHLIST_COLUMNS = [
    "Pair", "Close", "Trend_Stage", "Basing_Duration",
    "Range_Contraction", "ATR_Contraction_Ratio", "Coiled_Spring",
    "RS_Score", "Reason_Codes",
]


class CoiledSpringScanner:

    def __init__(self, btc_pair="B-BTC_USDT", resolution="1D", days=180):
        self.btc_pair = btc_pair
        self.resolution = resolution
        self.days = days
        self.candles = CandleData()
        self.ema = EMAIndicator()

    def _run_pipeline(self, pair: str) -> pd.DataFrame:
        btc_df = self.candles.get_candles(
            pair=self.btc_pair, resolution=self.resolution, days=self.days
        )
        df = self.candles.get_candles(
            pair=pair, resolution=self.resolution, days=self.days
        )

        df = self.ema.calculate(df)
        df = add_atr(df)
        df = add_all_features(df)
        df = classify_trend_lifecycle(df)
        df = add_coiled_spring(df)                 # <-- new step
        df = calculate_relative_strength(df, btc_df)
        df = calculate_scores(df)

        return df

    def scan(self, pairs: list) -> pd.DataFrame:
        rows = []
        errors = []

        for pair in pairs:
            try:
                df = self._run_pipeline(pair)
                last = df.iloc[-1]

                if bool(last.get("Coiled_Spring", False)):
                    row = {"Pair": pair}
                    for col in WATCHLIST_COLUMNS:
                        if col == "Pair":
                            continue
                        row[col] = last.get(col)
                    rows.append(row)

            except Exception as e:
                errors.append((pair, str(e)))

        if errors:
            print(f"\n{len(errors)} pair(s) failed during coiled-spring scan:")
            for pair, err in errors:
                print(f"  - {pair}: {err}")

        if not rows:
            return pd.DataFrame(columns=WATCHLIST_COLUMNS)

        watchlist = pd.DataFrame(rows)
        return watchlist[WATCHLIST_COLUMNS]


# ---------- Helper Function (matches your existing project convention) ----------
def scan_coiled_spring(pairs, **kwargs):
    return CoiledSpringScanner(**kwargs).scan(pairs)
