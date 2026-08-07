from data.candles import CandleData
from indicators.ema import EMAIndicator
from indicators.atr import add_atr
from indicators.features import add_all_features
from strategy.relative_strength import calculate_relative_strength
from exchange.coindcx import CoinDCX

candles = CandleData()

# ---- BTC data (reference) ----
btc_df = candles.get_candles(pair="B-BTC_USDT", resolution="1")

# ---- Pick an altcoin to test against ----
exchange = CoinDCX()
pairs = exchange.get_active_pairs()

test_pair = "B-ETH_USDT"
if test_pair not in pairs:
    # fallback: first non-BTC pair in the list
    test_pair = next(p for p in pairs if "BTC" not in p)

print(f"Testing Relative Strength for: {test_pair}\n")

coin_df = candles.get_candles(pair=test_pair, resolution="1")

# ---- Run existing pipeline on the coin ----
ema = EMAIndicator()
coin_df = ema.calculate(coin_df)
coin_df = add_atr(coin_df)
coin_df = add_all_features(coin_df)

# ---- Now Relative Strength vs BTC ----
coin_df = calculate_relative_strength(coin_df, btc_df)

print(
    coin_df[
        [
            "Time",
            "Close",
            "BTC_Close",
            "Coin_Return",
            "BTC_Return",
            "RS_Excess",
            "RS_Ratio_Slope",
            "RS_Label"
        ]
    ].tail(20)
)

print("\nRS Label Distribution\n")
print(coin_df["RS_Label"].value_counts())