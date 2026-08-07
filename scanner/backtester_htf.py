"""
scanner/backtester_htf.py

Same as scanner/backtester.py, but ONLY counts a 15m trade as valid if
the 1h trend direction (at that point in time) agrees with the trade's
direction. This directly tests the "1h regime + 15m entry" idea —
we've discussed it, but never actually measured whether it improves
results. This module measures it.

HOW ALIGNMENT WORKS:
For each 15m entry, we look up the most recent COMPLETED 1h bar's
direction at that timestamp (using merge_asof, backward-looking only —
no lookahead). If that 1h direction doesn't match the 15m trade's
direction, the trade is skipped entirely (never opened).
"""

import pandas as pd

from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores, ScoringEngine
from strategy.trade_plan import generate_trade_plan


class HTFFilteredBacktester:

    def __init__(self, ltf_resolution="15", ltf_days=60,
                 htf_resolution="60", htf_days=60, btc_pair="B-BTC_USDT"):
        self.ltf_resolution = ltf_resolution
        self.ltf_days = ltf_days
        self.htf_resolution = htf_resolution
        self.htf_days = htf_days
        self.btc_pair = btc_pair
        self.candles = CandleData()
        self.ema = EMAIndicator()
        self.scoring = ScoringEngine()

    # -----------------------------------------------------------------
    def _get_htf_direction_series(self, pair: str) -> pd.DataFrame:
        """Returns a Time/Direction series for the higher timeframe."""
        df = self.candles.get_candles(pair=pair, resolution=self.htf_resolution, days=self.htf_days)
        df = self.ema.calculate(df)
        df = add_atr(df)
        df = add_all_features(df)
        df = classify_trend_lifecycle(df)
        df["Direction"] = df["Trend_Stage"].apply(
            lambda stage: self.scoring._direction(stage)
        )
        return df[["Time", "Direction"]].rename(columns={"Direction": "HTF_Direction"})

    # -----------------------------------------------------------------
    def _build_ltf_dataframe(self, pair: str) -> pd.DataFrame:
        btc_df = self.candles.get_candles(pair=self.btc_pair, resolution=self.ltf_resolution, days=self.ltf_days)
        df = self.candles.get_candles(pair=pair, resolution=self.ltf_resolution, days=self.ltf_days)

        df = self.ema.calculate(df)
        df = add_atr(df)
        df = add_all_features(df)
        df = classify_trend_lifecycle(df)
        df = calculate_relative_strength(df, btc_df)
        df = calculate_scores(df)
        df = generate_trade_plan(df)

        return df

    # -----------------------------------------------------------------
    def _simulate_exit(self, df, entry_idx, direction, sl_price, tp_price):
        for j in range(entry_idx + 1, len(df)):
            bar = df.iloc[j]
            if direction == "LONG":
                if bar["Low"] <= sl_price:
                    return j, sl_price, "LOSS"
                if bar["High"] >= tp_price:
                    return j, tp_price, "WIN"
            else:
                if bar["High"] >= sl_price:
                    return j, sl_price, "LOSS"
                if bar["Low"] <= tp_price:
                    return j, tp_price, "WIN"
        return len(df) - 1, df.iloc[-1]["Close"], "OPEN"

    # -----------------------------------------------------------------
    def run(self, pair: str) -> pd.DataFrame:
        ltf_df = self._build_ltf_dataframe(pair)
        htf_series = self._get_htf_direction_series(pair)

        # Align: for each 15m bar, find the most recent 1h direction
        # (backward = no lookahead, only uses completed 1h bars)
        ltf_df = ltf_df.sort_values("Time")
        htf_series = htf_series.sort_values("Time")
        merged = pd.merge_asof(
            ltf_df, htf_series, on="Time", direction="backward"
        )

        trades = []
        i = 0
        while i < len(merged):
            row = merged.iloc[i]

            if row.get("Entry_Trigger") == "NOW":
                direction = row["Direction"]
                htf_direction = row.get("HTF_Direction")

                # ---- THE FILTER: skip if 1h doesn't agree ----
                if htf_direction != direction:
                    i += 1
                    continue

                entry_price = row["Entry_Price"]
                sl_price = row["Stop_Loss_Price"]
                tp_price = row["Take_Profit_Price"]

                exit_idx, exit_price, outcome = self._simulate_exit(
                    merged, i, direction, sl_price, tp_price
                )

                if direction == "LONG":
                    r_multiple = (exit_price - entry_price) / (entry_price - sl_price)
                else:
                    r_multiple = (entry_price - exit_price) / (sl_price - entry_price)

                trades.append({
                    "Pair": pair,
                    "Entry_Time": row["Time"],
                    "Direction": direction,
                    "Outcome": outcome,
                    "R_Multiple": round(r_multiple, 2),
                    "Trend_Stage_At_Entry": row["Trend_Stage"],
                })

                i = exit_idx + 1
            else:
                i += 1

        return pd.DataFrame(trades)


def run_htf_filtered_batch(pairs: list, **kwargs) -> pd.DataFrame:
    bt = HTFFilteredBacktester(**kwargs)
    all_trades = []

    for i, pair in enumerate(pairs):
        print(f"[{i + 1}/{len(pairs)}] HTF-filtered backtest: {pair}...")
        try:
            trades = bt.run(pair)
            if not trades.empty:
                all_trades.append(trades)
        except Exception as e:
            print(f"  - {pair} failed: {e}")

    if not all_trades:
        return pd.DataFrame()

    return pd.concat(all_trades, ignore_index=True)