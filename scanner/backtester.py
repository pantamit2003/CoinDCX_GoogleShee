"""
scanner/backtester.py

Backtests the scanner: runs the full pipeline on historical data, and
whenever Entry_Trigger == "NOW" fires, opens a SIMULATED trade at that
bar's Entry_Price/Stop_Loss_Price/Take_Profit_Price, then walks FORWARD
through subsequent bars to see whether the Stop Loss or Take Profit gets
hit first.

WHY THIS IS NOT LOOKAHEAD-BIASED:
The scoring/trend-lifecycle/etc. at any bar i only ever use data up to
and including bar i (rolling windows, .shift(1), etc — all backward
looking). We compute the full dataframe once for speed, but each row's
signal is still only a function of the past. The FORWARD walk is legitimate
backtesting: we're simulating what would have happened to a trade opened
at bar i using bars i+1, i+2, ... — exactly like a real trade unfolding
in real time after entry.

ASSUMPTION: if both SL and TP are touched within the same bar (bar's
High/Low range spans both), we conservatively assume SL was hit first.
This is standard, conservative backtesting practice when you don't have
tick-level data to know the true intrabar sequence.

LIMITATION: single coin, single timeframe per run. For a full picture,
run this across multiple pairs and average the results — one coin's
history is a small sample size and shouldn't be trusted alone.
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


class Backtester:

    def __init__(self, resolution="1", days=30, btc_pair="B-BTC_USDT"):
        self.resolution = resolution
        self.days = days
        self.btc_pair = btc_pair
        self.candles = CandleData()
        self.ema = EMAIndicator()

    # -----------------------------------------------------------------
    def _build_dataframe(self, pair: str) -> pd.DataFrame:
        btc_df = self.candles.get_candles(pair=self.btc_pair, resolution=self.resolution, days=self.days)
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
    def _simulate_exit(self, df: pd.DataFrame, entry_idx: int, direction: str,
                        sl_price: float, tp_price: float):
        """
        Walks forward from entry_idx+1 looking for SL or TP hit.
        Returns (exit_idx, exit_price, outcome) where outcome is
        'WIN', 'LOSS', or 'OPEN' (ran out of data before hitting either).
        """
        for j in range(entry_idx + 1, len(df)):
            bar = df.iloc[j]

            if direction == "LONG":
                hit_sl = bar["Low"] <= sl_price
                hit_tp = bar["High"] >= tp_price
                if hit_sl:
                    return j, sl_price, "LOSS"
                if hit_tp:
                    return j, tp_price, "WIN"
            else:  # SHORT
                hit_sl = bar["High"] >= sl_price
                hit_tp = bar["Low"] <= tp_price
                if hit_sl:
                    return j, sl_price, "LOSS"
                if hit_tp:
                    return j, tp_price, "WIN"

        return len(df) - 1, df.iloc[-1]["Close"], "OPEN"

    # -----------------------------------------------------------------
    def run(self, pair: str) -> dict:
        df = self._build_dataframe(pair)

        trades = []
        i = 0
        while i < len(df):
            row = df.iloc[i]

            if row.get("Entry_Trigger") == "NOW":
                direction = row["Direction"]
                entry_price = row["Entry_Price"]
                sl_price = row["Stop_Loss_Price"]
                tp_price = row["Take_Profit_Price"]

                exit_idx, exit_price, outcome = self._simulate_exit(
                    df, i, direction, sl_price, tp_price
                )

                if direction == "LONG":
                    r_multiple = (exit_price - entry_price) / (entry_price - sl_price)
                else:
                    r_multiple = (entry_price - exit_price) / (sl_price - entry_price)

                trades.append({
                    "Pair": pair,
                    "Entry_Time": row["Time"],
                    "Exit_Time": df.iloc[exit_idx]["Time"],
                    "Direction": direction,
                    "Entry_Price": entry_price,
                    "Exit_Price": exit_price,
                    "Stop_Loss": sl_price,
                    "Take_Profit": tp_price,
                    "Outcome": outcome,
                    "R_Multiple": round(r_multiple, 2),
                    "Confidence_At_Entry": row["Confidence"],
                    "Trend_Stage_At_Entry": row["Trend_Stage"],
                })

                # Resume scanning AFTER this trade closes — no overlapping
                # trades on the same coin.
                i = exit_idx + 1
            else:
                i += 1

        return self._summarize(pair, trades)

    # -----------------------------------------------------------------
    def _summarize(self, pair: str, trades: list) -> dict:
        trades_df = pd.DataFrame(trades)

        if trades_df.empty:
            return {
                "Pair": pair,
                "Total_Trades": 0,
                "Trades": trades_df,
                "Summary": "No trades were triggered in this period.",
            }

        closed = trades_df[trades_df["Outcome"] != "OPEN"]
        wins = closed[closed["Outcome"] == "WIN"]
        losses = closed[closed["Outcome"] == "LOSS"]

        win_rate = len(wins) / len(closed) * 100 if len(closed) > 0 else 0
        avg_win_r = wins["R_Multiple"].mean() if len(wins) > 0 else 0
        avg_loss_r = losses["R_Multiple"].mean() if len(losses) > 0 else 0
        total_r = closed["R_Multiple"].sum()
        expectancy = closed["R_Multiple"].mean() if len(closed) > 0 else 0

        return {
            "Pair": pair,
            "Total_Trades": len(trades_df),
            "Closed_Trades": len(closed),
            "Still_Open": len(trades_df) - len(closed),
            "Wins": len(wins),
            "Losses": len(losses),
            "Win_Rate_Percent": round(win_rate, 1),
            "Avg_Win_R": round(avg_win_r, 2),
            "Avg_Loss_R": round(avg_loss_r, 2),
            "Total_R": round(total_r, 2),
            "Expectancy_R_Per_Trade": round(expectancy, 3),
            "Trades": trades_df,
        }


# ---------- Helper Function (matches your existing convention) ----------
def backtest(pair, **kwargs):
    return Backtester(**kwargs).run(pair)