from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features

candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

# EMA pehle chalao (features isi pe depend karta hai)
ema = EMAIndicator()
df = ema.calculate(df)

# ATR pehle chalao (features isi pe depend karta hai)
df = add_atr(df)

# Ab Layer 1 features add karo
df = add_all_features(df)

print("Columns Added:\n")
print([col for col in df.columns])

print("\nRVOL + VWAP\n")
print(
    df[
        [
            "Time",
            "Close",
            "RVOL_20",
            "VWAP",
            "VWAP_Deviation"
        ]
    ].tail(15)
)

print("\nEMA Slope + ATR Expansion + Velocity\n")
print(
    df[
        [
            "Time",
            "Close",
            "EMA9_Slope",
            "ATR_Expansion",
            "Velocity",
            "Acceleration"
        ]
    ].tail(15)
)

print("\nCandle Quality + Streak + Pullback\n")
print(
    df[
        [
            "Time",
            "Close",
            "Body_Ratio",
            "Upper_Wick_Ratio",
            "Lower_Wick_Ratio",
            "Candle_Streak",
            "Pullback_Depth",
            "Pullback_Orderly"
        ]
    ].tail(15)
)