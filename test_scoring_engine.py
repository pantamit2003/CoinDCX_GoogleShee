from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores

candles = CandleData()

# ---- BTC data (reference) ----
btc_df = candles.get_candles(pair="B-BTC_USDT", resolution="1")

# ---- Coin to test ----
test_pair = "B-ETH_USDT"
print(f"Running full pipeline for: {test_pair}\n")

coin_df = candles.get_candles(pair=test_pair, resolution="1")

# ---- Layer 0/1: EMA, ATR, Features ----
ema = EMAIndicator()
coin_df = ema.calculate(coin_df)
coin_df = add_atr(coin_df)
coin_df = add_all_features(coin_df)

# ---- Layer 2: Trend Lifecycle ----
coin_df = classify_trend_lifecycle(coin_df)

# ---- Layer 3: Relative Strength vs BTC ----
coin_df = calculate_relative_strength(coin_df, btc_df)

# ---- Layer 5: Scoring ----
coin_df = calculate_scores(coin_df)

print(
    coin_df[
        [
            "Time",
            "Close",
            "Trend_Stage",
            "Direction",
            "Trend_Score",
            "Momentum_Score",
            "Volume_Score",
            "Breakout_Score",
            "RS_Score",
            "Confidence"
        ]
    ].tail(20)
)

print("\nTop 10 Highest Confidence Bars (in this history)\n")
top = coin_df.sort_values("Confidence", ascending=False).head(10)
print(
    top[
        [
            "Time",
            "Close",
            "Trend_Stage",
            "Direction",
            "Confidence",
            "Reason_Codes"
        ]
    ]
)

print("\nConfidence Distribution (describe)\n")
print(coin_df["Confidence"].describe())