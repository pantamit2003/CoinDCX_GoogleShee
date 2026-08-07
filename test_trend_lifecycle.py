from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle

candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

# Pehle EMA
ema = EMAIndicator()
df = ema.calculate(df)

# Fir ATR
df = add_atr(df)

# Fir Layer 1 features (RVOL, ATR_Expansion, Candle_Streak, etc.)
df = add_all_features(df)

# Ab Trend Lifecycle classify karo
df = classify_trend_lifecycle(df)

print("Last 30 candles - Trend Stage + Age\n")
print(
    df[
        [
            "Time",
            "Close",
            "RVOL_20",
            "ATR_Expansion",
            "Breakout_Up",
            "Trend_Stage",
            "Trend_Age"
        ]
    ].tail(30)
)

print("\nTrend Stage Distribution (kitni baar kaunsa stage aaya)\n")
print(df["Trend_Stage"].value_counts())

print("\nSabse recent EARLY_BREAKOUT bars (agar koi hain)\n")
breakouts = df[df["Trend_Stage"] == "EARLY_BREAKOUT"]
print(breakouts[["Time", "Close", "RVOL_20", "ATR_Expansion"]].tail(10))