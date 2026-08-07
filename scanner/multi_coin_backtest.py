"""
scanner/multi_coin_backtest.py

Runs the backtester across MULTIPLE coins and combines all trades into
one pool. This matters because a single coin's history is too small a
sample — especially for EARLY_BREAKOUT, which is intentionally rare
(strict filters = few signals per coin). Pooling many coins gives a
large enough sample to actually judge whether a given Trend_Stage is
a real edge or just noise.
"""

import pandas as pd

from scanner.backtester import Backtester


def run_batch_backtest(pairs: list, resolution="15", days=60, btc_pair="B-BTC_USDT"):
    bt = Backtester(resolution=resolution, days=days, btc_pair=btc_pair)

    all_trades = []
    errors = []

    for i, pair in enumerate(pairs):
        print(f"[{i + 1}/{len(pairs)}] Backtesting {pair}...")
        try:
            result = bt.run(pair)
            trades = result.get("Trades")
            if trades is not None and not trades.empty:
                all_trades.append(trades)
        except Exception as e:
            errors.append((pair, str(e)))

    if errors:
        print(f"\n{len(errors)} pair(s) failed:")
        for pair, err in errors:
            print(f"  - {pair}: {err}")

    if not all_trades:
        print("\nNo trades found across any pair.")
        return pd.DataFrame()

    combined = pd.concat(all_trades, ignore_index=True)
    return combined


def summarize_by_stage(trades_df: pd.DataFrame) -> pd.DataFrame:
    closed = trades_df[trades_df["Outcome"] != "OPEN"]

    breakdown = closed.groupby("Trend_Stage_At_Entry").agg(
        Trades=("Outcome", "count"),
        Wins=("Outcome", lambda x: (x == "WIN").sum()),
        Avg_R=("R_Multiple", "mean"),
        Total_R=("R_Multiple", "sum"),
    )
    breakdown["Win_Rate_%"] = (breakdown["Wins"] / breakdown["Trades"] * 100).round(1)
    breakdown["Avg_R"] = breakdown["Avg_R"].round(3)
    breakdown["Total_R"] = breakdown["Total_R"].round(2)

    return breakdown.sort_values("Trades", ascending=False)


def summarize_overall(trades_df: pd.DataFrame) -> dict:
    closed = trades_df[trades_df["Outcome"] != "OPEN"]
    wins = closed[closed["Outcome"] == "WIN"]
    losses = closed[closed["Outcome"] == "LOSS"]

    win_rate = len(wins) / len(closed) * 100 if len(closed) > 0 else 0

    return {
        "Total_Trades": len(trades_df),
        "Closed_Trades": len(closed),
        "Wins": len(wins),
        "Losses": len(losses),
        "Win_Rate_%": round(win_rate, 1),
        "Avg_R_Per_Trade": round(closed["R_Multiple"].mean(), 3) if len(closed) > 0 else 0,
        "Total_R": round(closed["R_Multiple"].sum(), 2),
    }