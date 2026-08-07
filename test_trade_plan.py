from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.trend_lifecycle import classify_trend_lifecycle
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring_engine import calculate_scores
from strategy.trade_plan import generate_trade_plan

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

# ---- Layer 7: Trade Plan ----
coin_df = generate_trade_plan(coin_df)

print("Last 20 bars - Full Trade Plan View\n")
print(
    coin_df[
        [
            "Time",
            "Close",
            "Trend_Stage",
            "Direction",
            "Confidence",
            "Entry_Trigger",
            "Stop_Loss_Price",
            "Take_Profit_Price",
            "Risk_Reward"
        ]
    ].tail(20)
)

print("\nAll bars where Entry_Trigger == NOW\n")
now_entries = coin_df[coin_df["Entry_Trigger"] == "NOW"]
print(
    now_entries[
        [
            "Time",
            "Close",
            "Direction",
            "Confidence",
            "Entry_Price",
            "Stop_Loss_Price",
            "Stop_Loss_Percent",
            "Take_Profit_Price",
            "Take_Profit_Percent",
            "Risk_Reward"
        ]
    ]
)

print("\nEntry_Trigger Distribution\n")
print(coin_df["Entry_Trigger"].value_counts())