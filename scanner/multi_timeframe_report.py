"""
scanner/multi_timeframe_report.py

Runs the FULL pipeline (Features -> Trend Lifecycle -> Relative Strength ->
Scoring -> Trade Plan) SEPARATELY on each of 6 timeframes for one coin:
    1m, 5m, 15m, 1h, 2h, 4h

Unlike strategy/multi_timeframe.py (which only checks trend STAGE for
alignment), this gives you the complete picture per timeframe — Confidence,
Entry, SL, TP, RR, all sub-scores — so you can see exactly what each
timeframe is independently saying, not just a yes/no alignment verdict.

WHY THIS MATTERS FOR YOUR GOAL:
You want to enter mid-trend and ride it out, not react to 1-minute noise.
Seeing the SAME scoring logic applied at 15m/1h/4h tells you whether the
"trend" you're seeing on 1m is backed by real structure on higher
timeframes, or whether it's a temporary blip that only exists on 1m.
"""

import pandas as pd

from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores
from strategy.trade_plan import generate_trade_plan


# (label, resolution_code, days_of_history_to_fetch)
TIMEFRAMES = [
    ("1m", "1", 2),
    ("5m", "5", 5),
    ("15m", "15", 10),
    ("1h", "60", 30),
    ("2h", "120", 45),
    ("4h", "240", 60),
]

REPORT_COLUMNS = [
    "Timeframe", "Close", "Trend_Stage", "Direction", "Trend_Age",
    "Trend_Score", "Momentum_Score", "Volume_Score", "Breakout_Score",
    "RS_Score", "Confidence", "Entry_Trigger", "Entry_Price",
    "Stop_Loss_Price", "Take_Profit_Price", "Risk_Reward",
    "Expected_Move_Percent", "Reason_Codes",
]


class MultiTimeframeReport:

    def __init__(self, btc_pair="B-BTC_USDT"):
        self.btc_pair = btc_pair
        self.candles = CandleData()
        self.ema = EMAIndicator()

    # -----------------------------------------------------------------
    def _run_pipeline(self, pair: str, resolution: str, days: int) -> pd.DataFrame:
        btc_df = self.candles.get_candles(pair=self.btc_pair, resolution=resolution, days=days)
        df = self.candles.get_candles(pair=pair, resolution=resolution, days=days)

        df = self.ema.calculate(df)
        df = add_atr(df)
        df = add_all_features(df)
        df = classify_trend_lifecycle(df)
        df = calculate_relative_strength(df, btc_df)
        df = calculate_scores(df)
        df = generate_trade_plan(df)

        return df

    # -----------------------------------------------------------------
    def generate(self, pair: str) -> pd.DataFrame:
        rows = []
        errors = []

        for label, resolution, days in TIMEFRAMES:
            try:
                df = self._run_pipeline(pair, resolution, days)
                last = df.iloc[-1]

                row = {"Timeframe": label}
                for col in REPORT_COLUMNS:
                    if col == "Timeframe":
                        continue
                    row[col] = last.get(col)
                rows.append(row)

            except Exception as e:
                errors.append((label, str(e)))

        if errors:
            print(f"\n{len(errors)} timeframe(s) failed for {pair}:")
            for label, err in errors:
                print(f"  - {label}: {err}")

        if not rows:
            return pd.DataFrame(columns=REPORT_COLUMNS)

        report = pd.DataFrame(rows)
        return report[REPORT_COLUMNS]


# ---------- Helper Function (matches your existing convention) ----------
def generate_timeframe_report(pair, **kwargs):
    return MultiTimeframeReport(**kwargs).generate(pair)